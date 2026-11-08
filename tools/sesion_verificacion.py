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

Con ``--ajustes`` hace la del P7.6 (``PROTOCOLO_AJUSTES``): el neutral con la
cabeza baja, la cabeza que sigue con las manos en las mejillas, el «yo no fui»
con los codos dentro del cuadro (la prueba que decide el H65) y la luz apagada.

Con ``--entrenar`` graba la del P7.7 (``PROTOCOLO_ENTRENAR``): las caras del
operador, neutral, enojada y triste, mirando al frente, abajo y arriba, para
reentrenar la emoción con ``tools/reentrenar_p77.py``; y «yo no fui» y estirar
los brazos, para ver si el tamaño de la mano los separa (H65).

Con ``--confirmar`` graba la sesión corta que confirma, en caras que nadie ha
mirado, los pesos reentrenados y la regla suave del enojo
(``PROTOCOLO_CONFIRMAR``; la evaluación, ``tools/confirmar_p78.py``).

Uso, desde la raíz del repositorio::

    .venv\\Scripts\\python.exe tools\\sesion_verificacion.py
    .venv\\Scripts\\python.exe tools\\sesion_verificacion.py --gestos
    .venv\\Scripts\\python.exe tools\\sesion_verificacion.py --ajustes
    .venv\\Scripts\\python.exe tools\\sesion_verificacion.py --entrenar
    .venv\\Scripts\\python.exe tools\\sesion_verificacion.py --confirmar
    .venv\\Scripts\\python.exe tools\\sesion_verificacion.py --analizar      # rehace el informe
"""

from __future__ import annotations

import argparse
import csv
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Desde el P10 viven en el paquete (telmeow_teleop/broker.py).
from telmeow_teleop.broker import asegurar_broker, puerto_abierto  # noqa: E402
from telmeow_teleop.config import PROJECT_ROOT  # noqa: E402

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

#: La sesión del P7.6 (2026-11-07): (etiqueta, qué hacer, segundos, qué debe
#: hacer el robot). Una postura por etiqueta.
PROTOCOLO_AJUSTES: tuple[tuple[int, str, int, str], ...] = (
    (0, "Cara relajada mirando la pantalla, quieto, manos abajo. NO pulses c", 15,
     "calibra 3 s; neutral; la cabeza quieta"),
    (1, "Cara relajada: baja la cabeza a mirar el teclado 5 s, vuelve, súbela a mirar "
        "el techo 5 s, vuelve. Sin gesticular", 20,
     "NEUTRAL todo el tiempo (antes, enojado al bajar la cabeza)"),
    (2, "Enojado de verdad: ceño fruncido, como al regañar, mirando la pantalla", 10, "ENOJADO"),
    (3, "Cara relajada", 5, "NEUTRAL"),
    (4, "«El grito»: las dos manos en las mejillas, y gira la cabeza despacio a un "
        "lado y al otro", 15,
     "los brazos a la cara y la cabeza TE SIGUE (antes se quedaba fija)"),
    (5, "Tápate la cara con las dos manos, quieto", 10,
     "la cabeza se queda quieta y a los 2 s va al frente"),
    (6, "Baja las manos", 5, "la cabeza vuelve a tu pose en ~1 s"),
    (7, "«Yo no fui» desde tu sitio de siempre: manos abiertas a los lados de la cabeza, "
        "por fuera de los hombros, palmas a la cámara", 15,
     "brazos hacia la cruz (aquí oscilaba el izquierdo)"),
    (8, "Baja las manos y siéntate unos 20 cm más atrás, hasta ver tus codos en la ventana", 10,
     "nada"),
    (9, "«Yo no fui» desde ahí, con los codos a la vista", 15,
     "brazos hacia la cruz, SIN oscilar"),
    (10, "Desde ahí: estira los brazos hacia la cámara", 10, "brazos al frente"),
    (11, "Vuelve a tu sitio, manos abajo, quieto. APAGA LA LUZ", 20,
     "la cabeza no vibra (la zona sube); si no basta, el panel dice POCA LUZ y el robot se para"),
    (12, "Prende la luz, quieto", 15, "si se paró, sigue solo a los ~3 s"),
)

#: La sesión del P7.7 (2026-11-07), para reentrenar la emoción con la cara del
#: operador a varias alturas de cabeza. Las etiquetas 0-11 son las de
#: ``reentrenar_p77.ESPERADAS_P77``; 12-15, los brazos.
PROTOCOLO_ENTRENAR: tuple[tuple[int, str, int, str], ...] = (
    (0, "Cara relajada mirando la pantalla, quieto, manos abajo. NO pulses c", 15,
     "calibra 3 s, cabeza y cara"),
    (1, "Cara neutral mirando la pantalla", 15, "NEUTRAL"),
    (2, "Cara neutral mirando el teclado (la cabeza baja)", 15, "NEUTRAL"),
    (3, "Cara neutral mirando arriba (encima de la pantalla)", 10, "NEUTRAL"),
    (4, "Cara neutral leyendo en voz alta un texto, mirando la pantalla", 20, "NEUTRAL"),
    (5, "Enojado: ceño fruncido, como al regañar, mirando la pantalla", 15, "ENOJADO"),
    (6, "Enojado mirando el teclado (la cabeza baja)", 15, "ENOJADO"),
    (7, "Cara neutral", 5, "NEUTRAL"),
    (8, "Triste con puchero, SIN fruncir el ceño, mirando la pantalla", 15, "TRISTE"),
    (9, "Triste con puchero Y el ceño fruncido, mirando la pantalla", 15, "TRISTE"),
    (10, "Triste mirando el teclado (la cabeza baja)", 15, "TRISTE"),
    (11, "Cara neutral mirando la pantalla", 10, "NEUTRAL"),
    (12, "«Yo no fui» desde tu sitio: manos abiertas a los lados de la cabeza, por fuera "
         "de los hombros, palmas a la cámara", 15, "brazos hacia la cruz"),
    (13, "Estira los brazos hacia la cámara", 15, "brazos al frente"),
    (14, "«Yo no fui» otra vez", 10, "brazos hacia la cruz"),
    (15, "Estira los brazos otra vez", 10, "brazos al frente"),
)

#: La sesión de confirmación del P7.7 (2026-11-08): caras que nadie ha mirado
#: para confirmar el reentrenado y la regla suave del enojo. Las etiquetas son
#: las de ``confirmar_p78.ESPERADAS_P78``.
PROTOCOLO_CONFIRMAR: tuple[tuple[int, str, int, str], ...] = (
    (0, "Cara relajada mirando la pantalla, quieto, manos abajo. NO pulses c", 15,
     "calibra 3 s, cabeza y cara"),
    (1, "Cara neutral mirando la pantalla", 15, "NEUTRAL"),
    (2, "Cara neutral mirando el teclado (la cabeza baja)", 15, "NEUTRAL"),
    (3, "Cara neutral mirando arriba (encima de la pantalla)", 10, "NEUTRAL"),
    (4, "Cara neutral leyendo en voz alta, mirando la pantalla", 20, "NEUTRAL"),
    (5, "Enojado mirando la pantalla", 10, "ENOJADO"),
    (6, "Enojado mirando el teclado (la cabeza baja)", 15, "ENOJADO"),
    (7, "Cara neutral", 5, "NEUTRAL"),
    (8, "Triste con puchero, mirando la pantalla", 10, "TRISTE"),
    (9, "Triste mirando el teclado (la cabeza baja)", 10, "TRISTE"),
    (10, "Cara neutral mirando la pantalla", 10, "NEUTRAL"),
)

#: Criterios del P7.6, fijados antes de grabar. Enojo del robot con la cabeza
#: subiendo y bajando (etiqueta 1): en el neutral de p75 era el 23 %.
ENOJO_NEUTRAL_MAX = 10.0
#: Enojo de verdad (etiqueta 2): con la regla, 84 % reproducido en p7_final.
ENOJO_REAL_MIN = 60.0
#: Guarda con las manos en las mejillas (4) y tapada (5): en p75, 91 y 81 %;
#: reproducido con la regla nueva, 6 y 71 %.
GUARDA_MEJILLAS_MAX = 10.0
GUARDA_TAPADA_MIN = 60.0
#: Recorrido del pan publicado con las manos en las mejillas: que la cabeza siga.
PAN_MEJILLAS_MIN = 20.0
#: «Yo no fui» con los codos a la vista (9): saltos de más de 5° por minuto en
#: cada brazo (en p75, 216 el izquierdo y 93 el derecho), y codos dentro del
#: cuadro en al menos este % de los frames, para que la prueba valga.
SALTOS_YO_NO_FUI_MAX = 40.0
CODOS_DENTRO_MIN = 80.0


# --------------------------------------------------------------------------
# Antes de grabar
# --------------------------------------------------------------------------


# puerto_abierto, estado_broker y el arranque del broker: telmeow_teleop/broker.py.


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


def angulo_a_la_cruz(abduccion: float, flexion: float) -> float:
    """Grados entre el brazo del robot y la cruz (su lateral)."""
    import math

    from telmeow_teleop.retarget import direccion

    return math.degrees(math.acos(max(-1.0, min(1.0, direccion(abduccion, flexion)[0]))))


def saltos_por_minuto(filas_brazos: list[dict], lado: str, umbral: float = 5.0) -> float | None:
    """Saltos del comando de un brazo mayores que ``umbral`` grados, por minuto."""
    abd, flex = ("armL", "armLtw") if lado == "L" else ("armR", "armRtw")
    comandos = [(_num(f.get(abd)), _num(f.get(flex))) for f in filas_brazos]
    comandos = [c for c in comandos if None not in c]
    if len(filas_brazos) < 2 or len(comandos) < 2:
        return None
    minutos = (float(filas_brazos[-1]["t"]) - float(filas_brazos[0]["t"])) / 60.0
    if minutos <= 0:
        return None
    return sum(1 for s in saltos(comandos) if s > umbral) / minutos


def analizar_ajustes(base: Path) -> str:
    """El informe de la sesión del P7.6, con los criterios fijados antes."""
    import filtros_cabeza as fc
    from telmeow_teleop.main import rutas_de_grabacion

    rutas = rutas_de_grabacion(base.with_suffix(".csv"))
    sesion = fc.leer(rutas["sesion"])
    brazos = fc.leer(rutas["brazos"])
    emocion = fc.leer(rutas["emocion"]) if rutas["emocion"].is_file() else []

    def de(filas, etiqueta):
        return [f for f in filas if int(f["etiqueta"]) == etiqueta]

    def pct(filas, condicion):
        return 100.0 * sum(1 for f in filas if condicion(f)) / len(filas) if filas else None

    def ok(cumple):
        return "OK" if cumple else "NO CUMPLE"

    lineas = [f"INFORME DEL P7.6 DE {base.name}", "=" * 60, ""]
    lineas.append("Emocion (robot, % de la etiqueta):")
    for etiqueta, emo, umbral, mayor in ((1, "enojado", ENOJO_NEUTRAL_MAX, False),
                                         (2, "enojado", ENOJO_REAL_MIN, True)):
        filas = [f for f in de(emocion, etiqueta) if f.get("rostro") == "1"]
        p = pct(filas, lambda f: f["emocion"] == emo)
        if p is None:
            lineas.append(f"  {etiqueta:>2}: sin datos")
            continue
        cumple = p >= umbral if mayor else p <= umbral
        lineas.append(f"  {etiqueta:>2} {emo}: {p:.0f} % (criterio {'>=' if mayor else '<='} {umbral:.0f} %) "
                      f"{ok(cumple)}")

    lineas += ["", "Cabeza con las manos en la cara:"]
    for etiqueta, umbral, mayor in ((4, GUARDA_MEJILLAS_MAX, False), (5, GUARDA_TAPADA_MIN, True)):
        filas = de(sesion, etiqueta)
        p = pct(filas, lambda f: f.get("cabeza_tapada") == "1")
        if p is None:
            continue
        cumple = p >= umbral if mayor else p <= umbral
        texto = "mejillas" if etiqueta == 4 else "tapada"
        linea = (f"  {etiqueta:>2} {texto}: guarda {p:.0f} % (criterio {'>=' if mayor else '<='} "
                 f"{umbral:.0f} %) {ok(cumple)}")
        if etiqueta == 4:
            pans = [_num(f.get("pan")) for f in filas if _num(f.get("pan")) is not None]
            recorrido = max(pans) - min(pans) if pans else 0.0
            linea += f"; el pan recorre {recorrido:.0f} grados (criterio >= {PAN_MEJILLAS_MIN:.0f}) " \
                     f"{ok(recorrido >= PAN_MEJILLAS_MIN)}"
        lineas.append(linea)

    lineas += ["", "«Yo no fui» (H65): saltos > 5 grados por minuto L / R, angulo mediano a la cruz L / R, "
                   "codos dentro del cuadro:"]
    for etiqueta, texto in ((7, "en tu sitio"), (9, "codos a la vista"), (10, "estirar")):
        filas = de(brazos, etiqueta)
        if len(filas) < 2:
            continue
        sl, sr = saltos_por_minuto(filas, "L"), saltos_por_minuto(filas, "R")
        cruz = {}
        for lado, abd, flex in (("L", "armL", "armLtw"), ("R", "armR", "armRtw")):
            v = sorted(angulo_a_la_cruz(_num(f[abd]), _num(f[flex])) for f in filas
                       if _num(f.get(abd)) is not None and _num(f.get(flex)) is not None)
            cruz[lado] = v[len(v) // 2] if v else None
        alto = _num(filas[0].get("alto")) or 480.0
        dentro = pct(filas, lambda f: all(
            _num(f.get(f"img_codo_y_{lado}")) is not None and _num(f.get(f"img_codo_y_{lado}")) <= alto
            for lado in "LR"))
        linea = (f"  {etiqueta:>2} {texto}: {sl or 0:.0f} / {sr or 0:.0f}, "
                 f"{cruz['L'] or 0:.0f} / {cruz['R'] or 0:.0f} grados, {dentro:.0f} %")
        if etiqueta == 9:
            vale = dentro >= CODOS_DENTRO_MIN
            cumple = vale and max(sl or 0, sr or 0) <= SALTOS_YO_NO_FUI_MAX
            linea += (f"  (criterio <= {SALTOS_YO_NO_FUI_MAX:.0f} con codos >= {CODOS_DENTRO_MIN:.0f} %) "
                      + (ok(cumple) if vale else "NO VALE: los codos no se vieron"))
        lineas.append(linea)

    lineas += ["", "Luz (indice de ruido de la malla, zona muerta, pausa):"]
    for etiqueta, texto in ((0, "con luz"), (11, "luz apagada"), (12, "luz prendida otra vez")):
        filas = de(sesion, etiqueta)
        if not filas:
            continue
        ruido = sorted(v for v in (_num(f.get("ruido_malla")) for f in filas) if v is not None)
        zona = sorted(v for v in (_num(f.get("zona_muerta")) for f in filas) if v is not None)
        pausa = pct(filas, lambda f: f.get("pausa_luz") == "1")
        poses = [_pose(f) for f in filas if f.get("pausa_luz") != "1"]
        s = saltos(poses)
        lineas.append(f"  {etiqueta:>2} {texto}: ruido {ruido[len(ruido) // 2] if ruido else float('nan'):.2f}, "
                      f"zona {zona[len(zona) // 2] if zona else float('nan'):.1f} (max {max(zona) if zona else 0:.1f}), "
                      f"pausa {pausa:.0f} %, cabeza publicada sin pausa: salto mediano "
                      f"{statistics.median(s) if s else float('nan'):.2f}")
    luz = de(sesion, 12)
    if luz:
        t0 = float(luz[0]["t"])
        tarde = [f for f in luz if float(f["t"]) - t0 > 6.0]
        if tarde:
            sigue = pct(tarde, lambda f: f.get("pausa_luz") == "1")
            lineas.append(f"  con la luz prendida, pasados 6 s: pausa {sigue:.0f} % (criterio 0 %) {ok(sigue == 0.0)}")
    return "\n".join(lineas)


def tamano_de_mano(filas_brazos: list[dict], etiquetas: set[int]) -> dict[str, float | None]:
    """Tamaño aparente mediano de cada mano, en píxeles, en esas etiquetas."""
    salida = {}
    for lado in "LR":
        v = sorted(x for x in (_num(f.get(f"tam_mano_{lado}")) for f in filas_brazos
                               if int(f["etiqueta"]) in etiquetas) if x is not None)
        salida[lado] = v[len(v) // 2] if v else None
    return salida


def analizar_entrenamiento(base: Path) -> str:
    """El informe de la sesión del P7.7: lo que hizo el robot con los pesos de
    ahora, tramo a tramo, y el tamaño de la mano en «yo no fui» y al estirar.
    El reentrenamiento va aparte (``tools/reentrenar_p77.py``), porque tarda."""
    import filtros_cabeza as fc
    import reentrenar_p77 as rp
    from telmeow_teleop.main import rutas_de_grabacion

    rutas = rutas_de_grabacion(base.with_suffix(".csv"))
    emocion = fc.leer(rutas["emocion"]) if rutas["emocion"].is_file() else []
    for f in emocion:
        f["etiqueta"] = int(f["etiqueta"])
        f["calibrando"] = f.get("calibrando") == "1"
    lineas = [f"INFORME DEL P7.7 DE {base.name}", "=" * 60, "",
              "Emocion con los pesos de ahora (robot, % del tramo):"]
    for nombre, valor in rp.resumir(emocion).items():
        lineas.append(f"  {nombre:20s} {'--' if valor is None else f'{valor:.0f} %'}")
    brazos = fc.leer(rutas["brazos"])
    yo, estirar = tamano_de_mano(brazos, {12, 14}), tamano_de_mano(brazos, {13, 15})
    lineas += ["", "Tamano aparente de la mano (px, mediana), para el H65:"]
    for lado in "LR":
        a, b = yo[lado], estirar[lado]
        razon = f", estirar / yo no fui = {b / a:.2f}" if a and b else ""
        lineas.append(f"  {lado}: yo no fui {a or 0:.0f}, estirar {b or 0:.0f}{razon}")
    lineas += ["", "Siguiente paso: python tools/reentrenar_p77.py (entrena con lo de antes y "
                   "evalua en esta sesion)"]
    return "\n".join(lineas)


def analizar_confirmacion(base: Path) -> str:
    """El informe de la sesión de confirmación: lo que hizo el robot en vivo,
    tramo a tramo. Las cuatro variantes y sus criterios, aparte
    (``tools/confirmar_p78.py``), porque tarda."""
    import confirmar_p78 as cp
    import filtros_cabeza as fc
    from telmeow_teleop.main import rutas_de_grabacion

    rutas = rutas_de_grabacion(base.with_suffix(".csv"))
    emocion = fc.leer(rutas["emocion"]) if rutas["emocion"].is_file() else []
    for f in emocion:
        f["etiqueta"] = int(f["etiqueta"])
        f["calibrando"] = f.get("calibrando") == "1"
    lineas = [f"INFORME DE CONFIRMACION DE {base.name}", "=" * 60, "",
              "Emocion en vivo (pesos y regla de operacion; robot, % del tramo):"]
    for nombre, valor in cp.resumir(emocion).items():
        lineas.append(f"  {nombre:20s} {'--' if valor is None else f'{valor:.0f} %'}")
    lineas += ["", "Siguiente paso: python tools/confirmar_p78.py (pesos reentrenados y regla suave)"]
    return "\n".join(lineas)


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
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument("--gestos", action="store_true", help="la sesion corta de la cabeza y la mano a la cara")
    modo.add_argument("--ajustes", action="store_true", help="la del P7.6: enojo, mejillas, codos y luz")
    modo.add_argument("--entrenar", action="store_true", help="la del P7.7: caras para reentrenar la emocion")
    modo.add_argument("--confirmar", action="store_true", help="la que confirma el reentrenado y la regla suave")
    parser.add_argument("--analizar", action="store_true", help="solo el informe, sin grabar")
    args = parser.parse_args(argv)
    modo_ = ("gestos" if args.gestos else "ajustes" if args.ajustes else "entrenar" if args.entrenar
             else "confirmar" if args.confirmar else "")
    base = PROJECT_ROOT / (args.base or {"gestos": "p75", "ajustes": "p76", "entrenar": "p77",
                                         "confirmar": "p78"}.get(modo_, "p7_final"))
    py = sys.executable
    analisis = {"gestos": analizar_gestos, "ajustes": analizar_ajustes,
                "entrenar": analizar_entrenamiento,
                "confirmar": analizar_confirmacion}.get(modo_, analizar)
    protocolo = {"gestos": PROTOCOLO_GESTOS, "ajustes": PROTOCOLO_AJUSTES,
                 "entrenar": PROTOCOLO_ENTRENAR, "confirmar": PROTOCOLO_CONFIRMAR}.get(modo_, PROTOCOLO)

    if args.analizar:
        informe = analisis(base)
        print(informe)
        base.with_name(base.name + "_informe.txt").write_bytes(informe.encode("utf-8"))
        return 0

    arrancados: list[subprocess.Popen] = []
    try:
        # puerto_abierto se pasa desde aquí para que las pruebas lo sustituyan.
        if not asegurar_broker(arrancados, puerto_abierto=puerto_abierto):
            return 1

        if not puerto_abierto(8000):
            arrancados.append(subprocess.Popen(
                [py, "-m", "http.server", "8000", "--directory", str(PROJECT_ROOT / "telmeow_viewer")],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            time.sleep(1.0)
        import webbrowser

        webbrowser.open(URL_VISOR)
        _esperar(f"Visor: {URL_VISOR}\n  Conectalo a ws://localhost:9001 con el topic telmeow/teleop/pose.")

        print(tabla_protocolo(protocolo))
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

        if args.ajustes or args.entrenar or args.confirmar:
            pass
        elif args.gestos:
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
