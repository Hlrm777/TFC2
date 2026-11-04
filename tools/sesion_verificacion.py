"""La sesión de verificación tras el P6.5, de principio a fin.

Comprueba lo que se cambió después de las sesiones del 2026-11-03 —los pesos
reentrenados, la regla de las cejas y la cabeza con la cara tapada (H58)— y
mide la latencia de la cámara con la luz de trabajo, que faltaba. Hace, en
orden:

1. **Comprueba el broker**: los dos puertos (1883 para Python, 9001 para el
   visor). Si solo está el 1883, es el servicio de Mosquitto sin WebSocket —la
   trampa del doble broker (§4.1)— y se para con las instrucciones. Si no hay
   ninguno, levanta el del repositorio (``mosquitto.conf``).
2. **Sirve el visor** por HTTP (nunca ``file://``) si no lo está, y lo abre.
3. **Graba la sesión** con el pipeline y, a la vez, el monitor de latencia.
   Antes enseña en la consola la tabla del protocolo: qué hacer en cada
   etiqueta, cuánto, y qué debe hacer el robot.
4. **Mide la latencia de la cámara** con la luz de trabajo y, si no hay
   contraste, ofrece repetir con menos luz.
5. **Analiza todo** y deja el informe en ``<base>_informe.txt``.

Al terminar para lo que arrancó él (broker y servidor del visor), no lo que
ya estaba en marcha.

Uso, desde la raíz del repositorio::

    .venv\\Scripts\\python.exe tools\\sesion_verificacion.py
    .venv\\Scripts\\python.exe tools\\sesion_verificacion.py --analizar      # rehace el informe
"""

from __future__ import annotations

import argparse
import csv
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from telmeow_teleop.config import PROJECT_ROOT  # noqa: E402

MOSQUITTO = Path(r"C:\Program Files\mosquitto\mosquitto.exe")
URL_VISOR = "http://localhost:8000/telmeow_viewer.html"

#: El protocolo: (etiqueta, qué hacer, segundos, qué debe hacer el robot). Las
#: etiquetas 0-10 son las del P6.5, para comparar con aquellas sesiones; la 11
#: y la 12 prueban la cabeza con la cara tapada.
PROTOCOLO: tuple[tuple[int, str, int, str], ...] = (
    (0, "Cara relajada mirando la pantalla, quieto. NO pulses c", 10,
     "calibra 3 s (el panel dice CALIBRANDO); neutral"),
    (1, "Triste con puchero, SIN fruncir el ceño", 10, "TRISTE"),
    (2, "Triste con puchero Y el ceño fruncido", 10, "TRISTE (antes salía enojado)"),
    (3, "Enojado: ceño fruncido, como al regañar", 10, "ENOJADO"),
    (4, "Cara relajada", 10, "NEUTRAL"),
    (5, "Otra vez la 1: triste sin fruncir", 10, "TRISTE"),
    (6, "Otra vez la 2: triste con el ceño fruncido", 10, "TRISTE"),
    (7, "Otra vez la 3: enojado", 10, "ENOJADO"),
    (8, "Sonrisa amplia", 10, "FELIZ"),
    (9, "Sorpresa: cejas arriba y boca abierta", 10, "SORPRENDIDO"),
    (10, "Leer en voz alta sin actuar (un texto sin emoción)", 30,
     "NEUTRAL: la boca abierta ya no cuenta como sorpresa"),
    (11, "Sonreír 5 s y, sin dejar de sonreír, taparse la cara con las dos manos, quieto", 15,
     "feliz y neutral ~2 s después de taparte; la cabeza NO tiembla y a los 2 s va al frente"),
    (12, "Destaparse con la cabeza girada a un lado, y quedarse así", 10,
     "la cabeza va a tu pose en ~1 s, sin salto"),
)


# --------------------------------------------------------------------------
# Antes de grabar
# --------------------------------------------------------------------------


def puerto_abierto(puerto: int, host: str = "localhost") -> bool:
    try:
        with socket.create_connection((host, puerto), timeout=0.5):
            return True
    except OSError:
        return False


def estado_broker(tcp: bool, websocket: bool) -> str:
    """``"listo"``, ``"arrancar"`` o ``"servicio"`` (la trampa del §4.1: el
    servicio de Mosquitto escucha en el 1883 sin WebSocket, y si además se
    lanza el del repositorio quedan dos brokers que no se hablan)."""
    if tcp and websocket:
        return "listo"
    if tcp:
        return "servicio"
    return "arrancar"


def tabla_protocolo() -> str:
    lineas = ["", "PROTOCOLO (ESPACIO al empezar cada etiqueta; la 0 empieza sola; q al acabar)", ""]
    for etiqueta, que, segundos, robot in PROTOCOLO:
        lineas.append(f"  {etiqueta:>2}  {segundos:>3} s  {que}")
        lineas.append(f"              -> robot: {robot}")
    total = sum(s for _, _, s, _ in PROTOCOLO)
    lineas += ["", f"  Unos {total // 60} min {total % 60} s en total.", ""]
    return "\n".join(lineas)


def _esperar(mensaje: str) -> None:
    input(f"\n{mensaje}\n  [Enter para seguir] ")


# --------------------------------------------------------------------------
# Después: el informe
# --------------------------------------------------------------------------


def saltos(poses: list[tuple[float, float] | None]) -> list[float]:
    """El mayor cambio entre dos poses seguidas (pan o tilt), en grados."""
    validas = [p for p in poses if p is not None]
    return [max(abs(a[0] - b[0]), abs(a[1] - b[1])) for a, b in zip(validas, validas[1:])]


def _percentil(valores, q):
    v = sorted(valores)
    return v[min(len(v) - 1, int(q * (len(v) - 1)))] if v else None


def _num(valor):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _pose(fila: dict) -> tuple[float, float] | None:
    """La cabeza publicada en una fila de ``sesion.csv`` (vacía sin cara)."""
    pan, tilt = _num(fila.get("pan")), _num(fila.get("tilt"))
    return None if pan is None or tilt is None else (pan, tilt)


def retorno_tras_taparse(filas_emocion: list[dict], etiqueta: int = 11) -> float | None:
    """Segundos desde que la cara deja de servir (sin cara o con una mano
    encima) hasta que la expresión vuelve a neutral, en la etiqueta dada."""
    filas = [f for f in filas_emocion if f["etiqueta"] == etiqueta]
    inicio = next((f["t"] for f in filas if not f["rostro"] or f["manos"]), None)
    if inicio is None:
        return None
    fin = next((f["t"] for f in filas if f["t"] >= inicio and f["retorno"]), None)
    return None if fin is None else fin - inicio


def latencia_mqtt(ruta: Path, sin_arranque_s: float = 5.0) -> tuple[float, float, int] | None:
    """``(mediana, p95, n)`` de la latencia captura -> suscriptor, sin los
    primeros segundos (la carga de los modelos)."""
    if not ruta.is_file():
        return None
    with open(ruta, newline="", encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    if not filas:
        return None
    t0 = float(filas[0]["recibido_ms"])
    valores = [float(f["latencia_ms"]) for f in filas
               if float(f["recibido_ms"]) - t0 > 1000.0 * sin_arranque_s]
    if not valores:
        return None
    return statistics.median(valores), _percentil(valores, 0.95), len(valores)


def analizar(base: Path) -> str:
    """El informe de la sesión, como texto."""
    import analizar_p65 as p
    import analizar_registro_emocion as are
    import analizar_sesion as asn
    import medir_latencia_camara as mlc
    from telmeow_teleop.main import rutas_de_grabacion

    rutas = rutas_de_grabacion(base.with_suffix(".csv"))
    lineas = [f"INFORME DE {base.name}", "=" * 60]

    sesion = asn.cargar(rutas["sesion"])
    r = asn.resumir(sesion)

    def cifra(valor, formato=".0f"):
        return "--" if valor is None else format(valor, formato)

    lineas.append(f"\nTasas: camara {cifra(r.fps_medios, '.1f')} fps medios, frames perdidos "
                  f"{cifra(None if r.perdidos is None else 100 * r.perdidos, '.1f')} %, bucle "
                  f"{cifra(r.ms_bucle_sin[0])} ms (con inferencia {cifra(r.ms_bucle_con[0])} ms), "
                  f"MQTT {cifra(r.hz, '.1f')} Hz, perdidos {r.fallidos}")

    emocion = are.cargar(rutas["emocion"])
    modelo = emocion[0].get("modelo", "") if emocion else ""
    lineas.append(f"\nEmocion ({modelo}), robot y prediccion cruda por tramo:")
    columnas = {"esta sesion": p.resumir(emocion)}
    referencia = sorted(PROJECT_ROOT.glob("p65_[gs][12]_emocion.csv"))
    if referencia:
        columnas["P6.5 (antes)"] = p.juntar([p.resumir(are.cargar(c)) for c in referencia])
    import contextlib
    import io

    texto = io.StringIO()
    with contextlib.redirect_stdout(texto):
        p.imprimir_tabla(columnas)
    lineas.append(texto.getvalue().rstrip())

    retorno = retorno_tras_taparse(emocion)
    lineas.append("\nCara tapada (etiqueta 11):")
    lineas.append(f"  expresion a neutral {retorno:.1f} s despues de taparse" if retorno is not None
                  else "  no se vio volver a neutral (¿se tapo la cara en la 11?)")
    tramo = [f for f in sesion if f["etiqueta"] == 11]
    if tramo:
        tapadas = sum(int(f.get("cabeza_tapada") or 0) for f in tramo)
        publicadas = [_pose(f) for f in tramo]
        s = saltos(publicadas)
        lineas.append(f"  la guarda actuo en {100 * tapadas / len(tramo):.0f} % de los frames")
        if s:
            lineas.append(f"  cabeza publicada: salto medio {statistics.mean(s):.2f} grados/frame, "
                          f"p95 {_percentil(s, 0.95):.2f}")
        a, b = tramo[0]["t"], tramo[-1]["t"]
        with open(rutas["cabeza"], newline="", encoding="utf-8") as f:
            medidas = [(float(x["pan"]), float(x["tilt"])) for x in csv.DictReader(f)
                       if a <= float(x["t"]) <= b]
        sm = saltos(medidas)
        if sm:
            lineas.append(f"  cabeza medida (Face Mesh): salto medio {statistics.mean(sm):.2f} "
                          f"grados/frame, p95 {_percentil(sm, 0.95):.2f}")
    tramo = [f for f in sesion if f["etiqueta"] == 12]
    if tramo:
        s = saltos([_pose(f) for f in tramo])
        if s:
            lineas.append(f"\nAl destaparse (etiqueta 12): salto maximo de la cabeza publicada "
                          f"{max(s):.1f} grados/frame")

    lineas.append("\nLatencia:")
    mqtt = latencia_mqtt(base.with_name(base.name + "_latencia.csv"))
    if mqtt:
        lineas.append(f"  captura -> mensaje: mediana {mqtt[0]:.0f} ms, p95 {mqtt[1]:.0f} ({mqtt[2]} mensajes)")
    camara = None
    for nombre in ("_latencia_camara.csv", "_latencia_camara_penumbra.csv"):
        ruta = base.with_name(base.name + nombre)
        if ruta.is_file():
            medidas = mlc.latencias_de_destellos(*mlc.leer_csv(ruta))
            if medidas:
                camara = statistics.median(medidas)
                lineas.append(f"  pantalla + camara ({ruta.name}): mediana {camara:.0f} ms, "
                              f"{len(medidas)} destellos")
                break
            lineas.append(f"  {ruta.name}: sin destellos validos")
    if mqtt and camara is not None:
        lineas.append(f"  TOTAL: ~{mqtt[0] + camara:.0f} ms (sin el render del visor)")
    return "\n".join(lineas)


# --------------------------------------------------------------------------
# La sesión
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sesion_verificacion", description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", default="p7_final", help="nombre de los archivos (sin .csv)")
    parser.add_argument("--analizar", action="store_true", help="solo el informe, sin grabar")
    args = parser.parse_args(argv)
    base = PROJECT_ROOT / args.base
    py = sys.executable

    if args.analizar:
        informe = analizar(base)
        print(informe)
        base.with_name(base.name + "_informe.txt").write_bytes(informe.encode("utf-8"))
        return 0

    arrancados: list[subprocess.Popen] = []
    try:
        estado = estado_broker(puerto_abierto(1883), puerto_abierto(9001))
        if estado == "servicio":
            print("\nEl 1883 esta abierto pero el 9001 no: es el SERVICIO de Mosquitto, que no\n"
                  "habla con el visor. En una terminal COMO ADMINISTRADOR:\n\n"
                  "    Stop-Service mosquitto; Set-Service mosquitto -StartupType Manual\n\n"
                  "y vuelve a lanzar este script.")
            return 1
        if estado == "arrancar":
            if not MOSQUITTO.is_file():
                print(f"No encuentro {MOSQUITTO}. Lanza el broker a mano (§4.2) o "
                      f"tools/dev_broker.py, y vuelve a empezar.")
                return 1
            print("Levantando el broker del repositorio (mosquitto.conf)...")
            arrancados.append(subprocess.Popen(
                [str(MOSQUITTO), "-c", str(PROJECT_ROOT / "mosquitto.conf")],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            time.sleep(1.5)
            if estado_broker(puerto_abierto(1883), puerto_abierto(9001)) != "listo":
                print("El broker no abrio los dos puertos. Revisa §4.1 del plan.")
                return 1
        print("Broker: listo (1883 y 9001).")

        if not puerto_abierto(8000):
            arrancados.append(subprocess.Popen(
                [py, "-m", "http.server", "8000", "--directory", str(PROJECT_ROOT / "telmeow_viewer")],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            time.sleep(1.0)
        import webbrowser

        webbrowser.open(URL_VISOR)
        _esperar(f"Visor: {URL_VISOR}\n  Conectalo a ws://localhost:9001 con el topic telmeow/teleop/pose.")

        print(tabla_protocolo())
        _esperar("Lee la tabla. Al seguir se abre la ventana del pipeline: la etiqueta 0\n"
                 "  empieza sola, y cada ESPACIO pasa a la siguiente.")
        latencia = base.with_name(base.name + "_latencia.csv")
        monitor = subprocess.Popen(
            [py, str(PROJECT_ROOT / "tools" / "mqtt_monitor.py"), "--latencia", str(latencia)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            subprocess.run([py, "-m", "telmeow_teleop.main", "--record", str(base.with_suffix(".csv")),
                            "--guardar-recortes", str(base.with_name(base.name + "_recortes"))],
                           cwd=PROJECT_ROOT)
        finally:
            monitor.terminate()
            monitor.wait(timeout=5)

        _esperar("Ahora la latencia de la camara, con la LUZ DE TRABAJO de siempre:\n"
                 "  30 s mirando la pantalla, quieto. q la corta.")
        r = subprocess.run([py, str(PROJECT_ROOT / "tools" / "medir_latencia_camara.py"),
                            "--csv", str(base.with_name(base.name + "_latencia_camara.csv"))])
        if r.returncode != 0:
            otra = input("\nNo hubo contraste. ¿Repetir con menos luz (solo una lampara)? [s/N] ")
            if otra.strip().lower().startswith("s"):
                subprocess.run([py, str(PROJECT_ROOT / "tools" / "medir_latencia_camara.py"),
                                "--csv", str(base.with_name(base.name + "_latencia_camara_penumbra.csv"))])

        print("\nAnalizando...\n")
        informe = analizar(base)
        print(informe)
        destino = base.with_name(base.name + "_informe.txt")
        destino.write_bytes(informe.encode("utf-8"))
        print(f"\nInforme guardado en {destino}")
        return 0
    finally:
        for proceso in arrancados:
            proceso.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
