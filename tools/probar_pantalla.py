"""Prueba de punta a punta de la pantalla del cuento (P11) y sus tiempos.

Hace de PC del narrador contra un broker de verdad: publica el cuento y luego
pasa de página, con los mismos mensajes retenidos que ``telmeow_teleop/main.py``
(``{"story", "page"}`` y ``{"page", "total"}``), y mide en la pantalla
(``raspberry/story_display.py``, con su bucle real a 30 cuadros):

- cuánto tarda en salir la primera página desde que se anuncia el cuento,
- cuánto tarda en estar dibujado el cuento entero,
- cuánto pasa entre publicar un cambio de página y que esté en pantalla,
- y, a mitad de la lectura, corta la conexión de la pantalla y comprueba que al
  volver se pone sola en la página vigente.

Sirve igual en la PC y en la Raspberry (allí da las cifras que valen)::

    python tools/probar_pantalla.py --broker localhost:1883 --cuento "trucas.pdf"
    python tools/probar_pantalla.py --sin-ventana --captura pagina.png

``--sin-ventana`` usa una pantalla que no existe (1920x1080): no abre nada.
OJO: deja retenidos en el broker el cuento y la página de la prueba.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import statistics
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from raspberry import story_display as sd  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--broker", default="localhost:1883")
    p.add_argument("--stories-dir", type=Path,
                   default=Path(os.environ.get("TELMEOW_STORIES_DIR", Path.home() / "cuentos")))
    p.add_argument("--cuento", default=None, help="Nombre del PDF; por defecto el primero.")
    p.add_argument("--cambios", type=int, default=20, help="Cambios de página a medir.")
    p.add_argument("--sin-ventana", action="store_true")
    p.add_argument("--captura", type=Path, default=None,
                   help="Guarda en este PNG lo que queda en pantalla al final.")
    args = p.parse_args(argv)

    import paho.mqtt.client as mqtt

    carpeta = args.stories_dir.expanduser()
    nombre = args.cuento or next((r.name for r in sorted(carpeta.glob("*.pdf"))), None)
    if nombre is None or sd.buscar_cuento(carpeta, nombre) is None:
        print(f"No encuentro el cuento ({nombre}) en {carpeta}.", file=sys.stderr)
        return 2
    host, puerto = sd._partir_broker(args.broker)

    if args.sin_ventana:
        os.environ["SDL_VIDEODRIVER"] = "dummy"
    guion = sd.Guion()
    pantalla = sd.Pantalla(guion, carpeta, ventana=args.sin_ventana)
    if args.sin_ventana:   # la pantalla falsa mide como la de verdad
        pantalla.lienzo = pantalla.pg.display.set_mode((1920, 1080))
        pantalla.tamano = pantalla.pre.tamano = (1920, 1080)
    enlace = sd.Enlace(guion, host, puerto)

    pintadas: list[tuple[float, tuple | None]] = []
    pintar_de_verdad = pantalla._pintar

    def pintar(imagen):
        pintar_de_verdad(imagen)
        pintadas.append((time.perf_counter(), guion.leer()))

    pantalla._pintar = pintar

    narrador = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="probar-pantalla")
    narrador.connect(host, puerto, 30)
    narrador.loop_start()
    informe: dict = {}

    def publicar(topic: str, datos: dict) -> float:
        narrador.publish(topic, json.dumps(datos), qos=0, retain=True).wait_for_publish(2.0)
        return time.perf_counter()

    def esperar(condicion, segundos: float) -> bool:
        limite = time.perf_counter() + segundos
        while time.perf_counter() < limite and not condicion():
            time.sleep(0.002)
        return condicion()

    def guion_de_la_prueba() -> None:
        try:
            if not esperar(lambda: enlace.conectado, 10):
                informe["error"] = f"la pantalla no conecta con {host}:{puerto}"
                return
            t0 = publicar(sd.TOPIC_CUENTO, {"story": nombre, "page": 1})
            publicar(sd.TOPIC_PAGINA, {"page": 1, "total": 0})
            if not esperar(lambda: pantalla.mostrada == (nombre, 1), 60):
                informe["error"] = "la primera pagina no salio en 60 s"
                return
            informe["primera_s"] = pintadas[-1][0] - t0
            esperar(lambda: 0 < pantalla.pre.total == pantalla.pre.progreso()[0], 300)
            total = pantalla.pre.total
            informe["paginas"] = total
            informe["todo_s"] = time.perf_counter() - t0

            retardos = []
            pagina = 1
            for i in range(args.cambios):
                pagina = pagina % total + 1 if total > 1 else 1
                antes = len(pintadas)
                t = publicar(sd.TOPIC_PAGINA, {"page": pagina, "total": total})
                if esperar(lambda: len(pintadas) > antes, 5):
                    retardos.append((pintadas[-1][0] - t) * 1000)
                time.sleep(0.15)
                if i == args.cambios // 2:      # el broker "se cae" para la pantalla
                    # Se le cierra el socket por debajo, como si se fuera la
                    # red: un disconnect() pedido no cuenta, paho no reintenta.
                    enlace._cliente.socket().shutdown(socket.SHUT_RDWR)
                    esperar(lambda: not enlace.conectado, 5)
                    informe["noto_el_corte"] = not enlace.conectado
                    pagina = pagina % total + 1 if total > 1 else 1
                    publicar(sd.TOPIC_PAGINA, {"page": pagina, "total": total})
                    informe["sin_enlace_se_queda"] = pantalla.mostrada[1] != pagina
                    t_corte = time.perf_counter()
                    informe["al_volver_se_pone_al_dia"] = esperar(
                        lambda: pantalla.mostrada == (nombre, pagina), 40)
                    informe["vuelta_s"] = time.perf_counter() - t_corte
            informe["retardos_ms"] = retardos
            informe["ultima"] = pagina
        except Exception as exc:  # noqa: BLE001
            informe["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            pantalla.seguir = False

    enlace.conectar()
    threading.Thread(target=guion_de_la_prueba, daemon=True).start()
    pantalla.bucle()
    if args.captura is not None:
        pantalla.pg.image.save(pantalla.lienzo, str(args.captura))
    narrador.loop_stop()
    enlace.cerrar()
    pantalla.cerrar()

    if "error" in informe:
        print("FALLO:", informe["error"])
        return 1
    r = informe["retardos_ms"]
    print(f"cuento: {nombre} ({informe['paginas']} paginas), pantalla {pantalla.tamano}")
    print(f"primera pagina en pantalla: {informe['primera_s']:.2f} s")
    print(f"cuento entero dibujado:     {informe['todo_s']:.2f} s")
    if r:
        print(f"cambio de pagina (publicar -> pantalla), {len(r)} cambios: "
              f"mediana {statistics.median(r):.0f} ms, maximo {max(r):.0f} ms")
    print(f"sin enlace se queda la pagina que habia: "
          f"{'SI' if informe.get('sin_enlace_se_queda') else 'NO'}")
    print(f"al volver el enlace se pone en la vigente: "
          f"{'SI' if informe.get('al_volver_se_pone_al_dia') else 'NO'}"
          f" (a los {informe.get('vuelta_s', 0):.1f} s del corte)")
    bien = (len(r) >= args.cambios and informe.get("noto_el_corte")
            and informe.get("sin_enlace_se_queda")
            and informe.get("al_volver_se_pone_al_dia"))
    return 0 if bien else 1


if __name__ == "__main__":
    raise SystemExit(main())
