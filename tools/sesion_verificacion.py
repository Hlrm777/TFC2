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

Con ``--gestos`` hace la sesión corta del P7.4 y el P7.5, sin la cámara de
latencia ni la tabla de emociones: la cabeza que ya no debe temblar y la mano a
la cara, con el protocolo ``PROTOCOLO_GESTOS`` y los criterios de abajo, fijados
antes de grabar. Acaba con 20 s del demo de la cabeza, solo, que es lo que separa
las dos causas posibles del H60.

Uso, desde la raíz del repositorio::

    .venv\\Scripts\\python.exe tools\\sesion_verificacion.py
    .venv\\Scripts\\python.exe tools\\sesion_verificacion.py --gestos
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

#: La sesión de los gestos (P7.4 y P7.5): (etiqueta, qué hacer, segundos, qué debe
#: hacer el robot). Una postura por etiqueta, y bajar las manos es una etiqueta
#: aparte para que las cifras del gesto no se mezclen con la salida.
PROTOCOLO_GESTOS: tuple[tuple[int, str, int, str], ...] = (
    (0, "Cara relajada mirando la pantalla, quieto, manos abajo. NO pulses c", 15,
     "calibra 3 s; la cabeza NO vibra, quieta del todo"),
    (1, "Sin girar la cabeza: habla en voz alta y sonríe a ratos, manos abajo", 15,
     "la cabeza casi no se mueve aunque cambies la expresión"),
    (2, "Gira despacio: a tu izquierda, centro, a tu derecha, centro, arriba, abajo, centro", 20,
     "la cabeza te sigue sin retraso que se note; al pararte puede quedar 1° corta"),
    (3, "Mano DERECHA en tu mejilla derecha, codo abajo; la izquierda abajo", 10,
     "SU brazo derecho a la cara en ~1 s y quieto, sin saltos; el izquierdo en reposo"),
    (4, "Baja la mano", 5, "el brazo vuelve a reposo en ~1 s, sin salto"),
    (5, "Mano IZQUIERDA en tu mejilla izquierda, codo abajo; la derecha abajo", 10,
     "SU brazo izquierdo a la cara y quieto; el derecho en reposo"),
    (6, "Baja la mano", 5, "el brazo vuelve a reposo en ~1 s, sin salto"),
    (7, "«El grito»: las dos manos en las mejillas, codos abajo", 10,
     "los dos brazos a la cara y quietos"),
    (8, "Baja las manos", 5, "los dos vuelven a reposo"),
    (9, "Tápate la cara con las dos manos, quieto", 10,
     "los dos brazos a la cara; la cabeza quieta y a los 2 s al frente"),
    (10, "Baja las manos", 5, "brazos a reposo y la cabeza vuelve a tu pose en ~1 s"),
    (11, "«Yo no fui»: manos abiertas a los lados de la cabeza, POR FUERA de los hombros, "
         "palmas a la cámara", 10,
     "NO es la mano a la cara: los brazos hacia la cruz"),
    (12, "Saluda con la derecha, la izquierda abajo", 10,
     "el saludo del robot con el derecho; NO la mano a la cara"),
    (13, "Narra como contando un cuento, con las manos", 40,
     "sigue tus gestos; A LA CARA solo si de verdad llevas la mano a la barbilla o la cara"),
)

#: Salto mediano de la cabeza con la persona quieta, en grados por frame: el de
#: p7_final fue 1.02, y la reproducción offline con el filtro nuevo da 0.07.
TEMBLOR_QUIETO_MAX = 0.2

#: Lo mismo hablando y sonriendo: 1.56 sonriendo en p7_final, 0.64 offline.
TEMBLOR_HABLANDO_MAX = 0.8

#: % mínimo de la etiqueta con el brazo en la pose. No es 100: la etiqueta incluye
#: subir la mano, reconocerla y la rampa, unos 2 de sus 10 s, más lo que tarde el
#: operador tras pulsar ESPACIO.
GESTO_MIN = 60.0

#: Temblor crudo de la Face Mesh con la persona quieta en la sesión del P4
#: (2026-09-06), con la misma regla que ``temblor_crudo``. En las nueve sesiones
#: del pipeline, 0.9-1.4 (H60).
TEMBLOR_CRUDO_P4 = 0.36

#: Qué brazos del robot van a la cara en cada etiqueta de ``PROTOCOLO_GESTOS``.
GESTO_ESPERADO: dict[int, str] = {3: "R", 5: "L", 7: "LR", 9: "LR"}

#: Etiquetas en las que ningún brazo puede ir a la cara.
SIN_GESTO: tuple[int, ...] = (0, 1, 2, 11, 12)


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


def tabla_protocolo(protocolo=PROTOCOLO) -> str:
    lineas = ["", "PROTOCOLO (ESPACIO al empezar cada etiqueta; la 0 empieza sola; q al acabar)", ""]
    for etiqueta, que, segundos, robot in protocolo:
        lineas.append(f"  {etiqueta:>2}  {segundos:>3} s  {que}")
        lineas.append(f"              -> robot: {robot}")
    total = sum(s for _, _, s, _ in protocolo)
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


def temblor_crudo(filas_cabeza: list[dict], etiqueta: int = 0, sin_arranque_s: float = 3.0) -> float | None:
    """Salto mediano de lo que da la Face Mesh, antes de la ganancia y del filtro,
    sin los primeros segundos de la etiqueta. Con ella en la mano se comparan
    sesiones sin que el filtro de cada una se meta por medio."""
    filas = [f for f in filas_cabeza if int(f["etiqueta"]) == etiqueta]
    if not filas:
        return None
    t0 = float(filas[0]["t"])
    filas = [f for f in filas if float(f["t"]) - t0 > sin_arranque_s]
    s = saltos([(float(f["raw_pan"]), float(f["raw_tilt"])) for f in filas])
    return statistics.median(s) if s else None


def de_donde_sale_el_temblor(pipeline: float | None, demo: float | None) -> str:
    """Lo que dice comparar el demo con el pipeline en la misma sesión (H60). El
    2 es un corte de orden de magnitud: lo que hay que separar es 0.36 de 1.3."""
    if pipeline is None or demo is None:
        return "sin las dos medidas no se puede decir de donde sale"
    if pipeline >= 2.0 * demo:
        return "lo causa el PIPELINE: el demo solo tiembla mucho menos en las mismas condiciones"
    if demo >= 2.0 * TEMBLOR_CRUDO_P4:
        return "NO es el pipeline: el demo solo tiembla igual, asi que cambio algo desde septiembre"
    return "ni el demo ni el pipeline tiemblan como antes: mirar que cambio en esta sesion"


def en_la_pose_por_etiqueta(filas_brazos: list[dict]) -> dict[int, dict[str, float]]:
    """``{etiqueta: {lado: % de sus frames con el brazo del todo en la pose}}``."""
    cuenta: dict[int, dict[str, int]] = {}
    total: dict[int, int] = {}
    for f in filas_brazos:
        e = int(f["etiqueta"])
        total[e] = total.get(e, 0) + 1
        por_lado = cuenta.setdefault(e, {"L": 0, "R": 0})
        for lado in "LR":
            if (_num(f.get(f"mano_cara_{lado}")) or 0.0) >= 0.999:
                por_lado[lado] += 1
    return {e: {lado: 100.0 * n / total[e] for lado, n in v.items()} for e, v in cuenta.items()}


def analizar_gestos(base: Path) -> str:
    """El informe de la sesión de los gestos, con los criterios fijados antes."""
    import filtros_cabeza as fc
    from telmeow_teleop.main import rutas_de_grabacion

    rutas = rutas_de_grabacion(base.with_suffix(".csv"))
    cabeza = fc.leer(rutas["cabeza"])
    brazos = fc.leer(rutas["brazos"])
    lineas = [f"INFORME DE GESTOS DE {base.name}", "=" * 60, "", "Cabeza publicada, salto mediano entre frames:"]
    poses = [(float(f["pan"]), float(f["tilt"])) for f in cabeza]

    def veredicto(cumple: bool) -> str:
        return "OK" if cumple else "NO CUMPLE"

    for etiqueta, texto, maximo, antes in (
        (0, "quieto", TEMBLOR_QUIETO_MAX, 1.02),
        (1, "hablando y sonriendo", TEMBLOR_HABLANDO_MAX, 1.56),
    ):
        try:
            mediana, p95 = fc.temblor(cabeza, poses, {etiqueta})
        except statistics.StatisticsError:
            lineas.append(f"  {etiqueta:>2} {texto}: sin datos")
            continue
        lineas.append(f"  {etiqueta:>2} {texto}: {mediana:.3f} grados/frame, p95 {p95:.2f} "
                      f"(criterio <= {maximo}; en p7_final {antes}) {veredicto(mediana <= maximo)}")
    giros = [p for f, p in zip(cabeza, poses) if int(f["etiqueta"]) == 2]
    if giros:
        lineas.append(f"   2 girando: pan {min(p[0] for p in giros):+.0f}..{max(p[0] for p in giros):+.0f}, "
                      f"tilt {min(p[1] for p in giros):+.0f}..{max(p[1] for p in giros):+.0f} grados")

    crudo = temblor_crudo(cabeza)
    ruta_demo = base.with_name(base.name + "_demo_cabeza.csv")
    demo = temblor_crudo(fc.leer(ruta_demo)) if ruta_demo.is_file() else None
    formato = lambda v: "--" if v is None else f"{v:.2f}"  # noqa: E731
    lineas += ["", f"Temblor crudo de la Face Mesh, quieto (H60): pipeline {formato(crudo)}, "
                   f"demo solo {formato(demo)}, sesion del P4 {TEMBLOR_CRUDO_P4}",
               f"  {de_donde_sale_el_temblor(crudo, demo)}"]

    pose = en_la_pose_por_etiqueta(brazos)
    lineas += ["", "Mano a la cara, % de la etiqueta con el brazo en la pose (L / R):"]
    for etiqueta, que, _, _ in PROTOCOLO_GESTOS:
        if etiqueta not in pose:
            continue
        p = pose[etiqueta]
        nota = ""
        if etiqueta in GESTO_ESPERADO:
            esperado = GESTO_ESPERADO[etiqueta]
            bien = all(p[lado] >= GESTO_MIN for lado in esperado) and all(
                p[lado] == 0.0 for lado in "LR" if lado not in esperado)
            nota = f"  (esperado {esperado}, >= {GESTO_MIN:.0f} %) {veredicto(bien)}"
        elif etiqueta in SIN_GESTO:
            nota = f"  (esperado ninguno) {veredicto(p['L'] == 0.0 and p['R'] == 0.0)}"
        lineas.append(f"  {etiqueta:>2} {p['L']:5.1f} / {p['R']:5.1f}{nota}  {que[:44]}")

    comandos = {lado: [] for lado in "LR"}
    for f in brazos:
        for lado, abd, flex in (("L", "armL", "armLtw"), ("R", "armR", "armRtw")):
            if (_num(f.get(f"mano_cara_{lado}")) or 0.0) >= 0.999 and _num(f.get(abd)) is not None:
                comandos[lado].append((float(f[abd]), float(f[flex])))
    for lado, valores in comandos.items():
        s = saltos(valores)
        if s:
            lineas.append(f"  brazo {lado} en la pose: salto mediano {statistics.median(s):.2f} "
                          f"grados/frame, maximo {max(s):.1f} (antes 2.6-5.1)")
    saludos = [f for f in brazos if int(f["etiqueta"]) == 12]
    if saludos:
        reconocido = sum((_num(f.get("saludo_R")) or 0.0) > 0.0 for f in saludos)
        lineas += ["", f"Saludo (etiqueta 12): el robot saludo en {100 * reconocido / len(saludos):.0f} % "
                       "de la etiqueta"]
    return "\n".join(lineas)


# --------------------------------------------------------------------------
# La sesión
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sesion_verificacion", description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", help="nombre de los archivos, sin .csv (p7_final; p75 con --gestos)")
    parser.add_argument("--gestos", action="store_true", help="la sesion corta de la cabeza y la mano a la cara")
    parser.add_argument("--analizar", action="store_true", help="solo el informe, sin grabar")
    args = parser.parse_args(argv)
    base = PROJECT_ROOT / (args.base or ("p75" if args.gestos else "p7_final"))
    py = sys.executable
    analisis = analizar_gestos if args.gestos else analizar

    if args.analizar:
        informe = analisis(base)
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

        print(tabla_protocolo(PROTOCOLO_GESTOS if args.gestos else PROTOCOLO))
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

        if args.gestos:
            _esperar("Por ultimo, 20 s con el demo de la cabeza solo, para saber de donde sale\n"
                     "  el temblor: cara relajada, quieto, manos abajo. q lo corta.")
            subprocess.run([py, "-m", "telmeow_teleop.head_tracker",
                            "--record", str(base.with_name(base.name + "_demo_cabeza.csv"))],
                           cwd=PROJECT_ROOT)
        else:
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
        informe = analisis(base)
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
