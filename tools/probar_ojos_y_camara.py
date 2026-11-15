"""Prueba de punta a punta de los ojos y la cámara del robot, con sus tiempos.

Como ``tools/probar_pantalla.py``, pero para los otros dos programas de la
Raspberry (``raspberry/eye_display.py`` y ``raspberry/robot_camera.py``):

- **Ojos**: hace de PC del narrador contra un broker de verdad. Publica poses a
  20 por segundo, como el pipeline, cambiando la expresión cada medio segundo,
  y mide cuánto pasa entre publicar una expresión nueva y que la pantalla
  empiece a pintarla. También manda una expresión que no existe y basura.
- **Cámara**: arranca ``robot_camera.py`` como programa aparte (con la cámara de
  prueba, o con ``--fuente``), lee su video como lo leería la página y mide las
  fotos por segundo y el tiempo desde la captura hasta que llega la foto.

    python tools/probar_ojos_y_camara.py --broker localhost:1883 --sin-ventana

Todo corre en una sola máquina: el tiempo de la cámara NO incluye la red wifi
entre el robot y la PC ni la cámara real.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from raspberry import eye_display as ojos  # noqa: E402


def probar_ojos(host: str, puerto: int, sin_ventana: bool, cambios: int) -> dict:
    import paho.mqtt.client as mqtt

    if sin_ventana:
        os.environ["SDL_VIDEODRIVER"] = "dummy"
    expresion = ojos.Expresion()
    pantalla = ojos.PantallaOjos(expresion, ojos.carpeta_de_ojos(), ventana=True)
    enlace = ojos.EnlaceOjos(expresion, host, puerto)
    informe: dict = {"imagenes": len(pantalla.imagenes)}
    cambio_visto: list[tuple[float, str]] = []
    paso_de_verdad = pantalla.paso

    def paso():
        antes = pantalla.actual
        paso_de_verdad()
        if pantalla.actual != antes:
            cambio_visto.append((time.perf_counter(), pantalla.actual))

    pantalla.paso = paso
    narrador = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="probar-ojos")
    narrador.connect(host, puerto, 30)
    narrador.loop_start()

    def guion() -> None:
        try:
            limite = time.perf_counter() + 10
            while not enlace.conectado and time.perf_counter() < limite:
                time.sleep(0.01)
            if not enlace.conectado:
                informe["error"] = f"los ojos no conectan con {host}:{puerto}"
                return
            nombres = [n for n in ojos.ARCHIVOS if n != "neutral"]
            retardos, pedidas = [], []
            for i in range(cambios):
                nombre = nombres[i % len(nombres)]
                pedidas.append(nombre)
                publicado = None
                for _ in range(10):                      # medio segundo a 20 Hz
                    narrador.publish(ojos.TOPIC_POSE, json.dumps(
                        {"pan": 1.0, "tilt": 2.0, "emo": nombre}), qos=0)
                    publicado = publicado or time.perf_counter()
                    time.sleep(0.05)
                vistos = [t for t, n in cambio_visto if n == nombre and t >= publicado]
                if vistos:
                    retardos.append((vistos[0] - publicado) * 1000)
            ultima = pedidas[-1]
            narrador.publish(ojos.TOPIC_POSE, json.dumps({"emo": "no_existe"}), qos=0)
            narrador.publish(ojos.TOPIC_POSE, b"\xff\xfe basura", qos=0)
            time.sleep(0.3)
            informe["retardos_ms"] = retardos
            informe["aguanta_lo_raro"] = pantalla.actual == ultima and pantalla.seguir
        except Exception as exc:  # noqa: BLE001
            informe["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            pantalla.seguir = False

    enlace.conectar()
    threading.Thread(target=guion, daemon=True).start()
    pantalla.bucle()
    narrador.loop_stop()
    enlace.cerrar()
    pantalla.cerrar()
    return informe


def probar_camara(fuente: str, puerto: int, segundos: float) -> dict:
    programa = subprocess.Popen(
        [sys.executable, str(RAIZ / "raspberry" / "robot_camera.py"), "--fuente", fuente,
         "--puerto", str(puerto), "--solo-local"],
        stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{puerto}"
    informe: dict = {}
    try:
        estado = None
        limite = time.perf_counter() + 20
        while time.perf_counter() < limite:
            try:
                with urllib.request.urlopen(base + "/estado", timeout=1) as r:
                    estado = json.loads(r.read())
                if estado["cuadros"] > 0:
                    break
            except OSError:
                pass
            time.sleep(0.1)
        if not estado or not estado["cuadros"]:
            return {"error": "la camara no dio ninguna foto en 20 s"}
        informe["fuente"] = estado["fuente"]
        llegadas, retardos, tamanos = [], [], []
        crudo = b""
        with urllib.request.urlopen(base + "/video.mjpg", timeout=5) as r:
            fin = time.perf_counter() + segundos
            while time.perf_counter() < fin:
                crudo += r.read1(65536)   # lo que haya llegado, sin esperar a llenar
                while True:
                    cabecera = re.search(
                        rb"Content-Length: (\d+)\r\nX-Captura-Ms: (\d+)\r\n\r\n", crudo)
                    if not cabecera or len(crudo) < cabecera.end() + int(cabecera.group(1)):
                        break
                    largo = int(cabecera.group(1))
                    foto = crudo[cabecera.end(): cabecera.end() + largo]
                    crudo = crudo[cabecera.end() + largo:]
                    llegadas.append(time.perf_counter())
                    retardos.append(time.time() * 1000 - int(cabecera.group(2)))
                    tamanos.append(largo)
                    informe["jpeg_valido"] = foto[:2] == b"\xff\xd8" and foto[-2:] == b"\xff\xd9"
        informe["fotos"] = len(llegadas)
        if len(llegadas) > 1:
            informe["fps"] = (len(llegadas) - 1) / (llegadas[-1] - llegadas[0])
            informe["retardo_ms"] = statistics.median(retardos)
            informe["retardo_max_ms"] = max(retardos)
            informe["kb_por_foto"] = statistics.mean(tamanos) / 1024
    finally:
        programa.terminate()
        programa.wait(timeout=5)
    return informe


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--broker", default="localhost:1883")
    p.add_argument("--sin-ventana", action="store_true")
    p.add_argument("--cambios", type=int, default=10, help="Cambios de expresión a medir.")
    p.add_argument("--fuente", default="prueba", help="La fuente de robot_camera.py.")
    p.add_argument("--puerto-camara", type=int, default=8012)
    p.add_argument("--segundos", type=float, default=5.0)
    args = p.parse_args(argv)
    host, puerto = ojos._partir_broker(args.broker)
    bien = True

    o = probar_ojos(host, puerto, args.sin_ventana, args.cambios)
    print("OJOS")
    if "error" in o:
        print("  FALLO:", o["error"])
        bien = False
    else:
        r = o["retardos_ms"]
        print(f"  imagenes cargadas: {o['imagenes']} de {len(ojos.ARCHIVOS)}")
        if r:
            print(f"  expresion nueva (publicar -> empieza a pintarse), {len(r)} de "
                  f"{args.cambios} cambios: mediana {statistics.median(r):.0f} ms, "
                  f"maximo {max(r):.0f} ms (mas {ojos.FUNDIDO_S * 1000:.0f} ms de fundido)")
        print(f"  una expresion que no existe y basura no la mueven: "
              f"{'SI' if o['aguanta_lo_raro'] else 'NO'}")
        bien = bien and len(r) == args.cambios and o["aguanta_lo_raro"] \
            and o["imagenes"] == len(ojos.ARCHIVOS)

    c = probar_camara(args.fuente, args.puerto_camara, args.segundos)
    print("CAMARA")
    if "error" in c or c.get("fotos", 0) < 2:
        print("  FALLO:", c.get("error", "no llegaron fotos"))
        bien = False
    else:
        print(f"  fuente: {c['fuente']}; {c['fotos']} fotos en {args.segundos:g} s = "
              f"{c['fps']:.1f} por segundo, {c['kb_por_foto']:.0f} kB cada una")
        print(f"  de la captura a recibirla (misma maquina): mediana {c['retardo_ms']:.0f} ms, "
              f"maximo {c['retardo_max_ms']:.0f} ms")
        print(f"  las fotos son JPEG validos: {'SI' if c.get('jpeg_valido') else 'NO'}")
        bien = bien and bool(c.get("jpeg_valido"))
    return 0 if bien else 1


if __name__ == "__main__":
    raise SystemExit(main())
