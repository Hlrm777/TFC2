"""Las cifras del P6, sobre el dataset propio.

Por qué
-------
Cada número que el plan maestro cita del clasificador de emoción sale de aquí,
para poder reproducirlo y para pasar por lo mismo cualquier modelo o dataset
nuevo:

1. **La red propia.** Lo que cuesta ``model(x, training=False)`` frente a
   ``model.predict(x)`` (el bug 12), si dan las mismas probabilidades, y lo que
   cuesta la primera llamada, que es la que el clasificador paga al arrancar.
2. **El detector Haar.** En cuántas imágenes encuentra 0, 1 o más rostros; en
   cuántas la primera detección —la que usó el entrenamiento— no es la mayor;
   el tamaño de los rostros; y si ``minSize`` cambia algún recorte, y cuánto
   abarata.
3. **El backend** (``--backend``), con cuatro entradas distintas de la misma
   cara, que es como se ve qué convenio respeta cada uno:

   - ``tal cual``: el recorte como lo da el detector, en BGR;
   - ``volteada``: la foto en espejo, como llegaría si el clasificador no
     deshiciera el del pipeline (H43);
   - ``colores al reves``: el recorte en RGB, como llegaría si se le pasara el
     frame que el P7 convierte para MediaPipe;
   - ``48x48 gris``: la entrada literal del enunciado del P6,
     ``predict(face_gray_48x48)``. Para el modelo propio es casi su propio
     preprocesamiento; a las librerías les quita resolución y color.
4. **El tamaño de la cara en operación** (``--registro-cabeza``). El dataset se
   grabó muy cerca de la cámara; para saber qué caja le llegará al Haar con el
   operador sentado a narrar, se mide en el dataset la relación entre el lado
   de la caja y la distancia entre las comisuras externas de los ojos (Face
   Mesh), y se aplica a la ``escala_cara`` que registró el head tracker, que es
   esa misma distancia. De ahí sale si ``minSize`` deja margen.

Lo tercero NO es la accuracy de nadie. Para el modelo propio, el dataset es
con el que se entrenó (con fuga de datos, bug 13): mide si el pipeline
reproduce el entrenamiento, no si el modelo generaliza. Para las librerías es
una sola persona con expresiones posadas. Las cifras comparables son el P9.

Uso (``deepface`` y ``hsemotion``, desde el entorno de
``requirements-benchmark.txt``)::

    python tools/medir_emocion.py                  # todo el dataset (~20 min)
    python tools/medir_emocion.py --paso 20        # 1 de cada 20 imágenes (~1 min)
    python tools/medir_emocion.py --backend hsemotion --paso 10
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telmeow_teleop.config import EMOTION_LABELS, PROJECT_ROOT  # noqa: E402

#: ``minSize`` que se evalúa por defecto: el que usa la configuración.
MIN_LADO = 60

#: Las cuatro entradas con que se prueba el backend (ver el punto 3 de arriba).
VARIANTES = ("tal cual", "volteada", "colores al reves", "48x48 gris")


def clase_de(ruta: Path) -> str | None:
    """``feliz_123.jpg`` → ``"feliz"``; ``None`` si no es una clase del robot.

    Es la etiqueta que puso el operador al grabar (``Creation_of_dataset.py``
    nombra cada archivo con la emoción que estaba sosteniendo), y por eso sirve
    de referencia externa: no sale de ningún código de este proyecto.
    """
    clase = Path(ruta).name.split("_")[0]
    return clase if clase in EMOTION_LABELS else None


def _percentil(valores: list[float], p: float) -> float:
    ordenados = sorted(valores)
    if not ordenados:
        return float("nan")
    k = (len(ordenados) - 1) * p / 100.0
    i = int(k)
    if i + 1 >= len(ordenados):
        return ordenados[-1]
    return ordenados[i] + (ordenados[i + 1] - ordenados[i]) * (k - i)


def medir_red(modelo, repeticiones: int = 50, reloj=time.perf_counter) -> dict:
    """Coste de la red por las dos vías, sobre una entrada fija de 48×48.

    La primera llamada se mide aparte: construye el grafo y es la que el
    ``KerasBackend`` paga al arrancar.
    """
    import numpy as np

    x = np.random.default_rng(0).random((1, 48, 48, 1)).astype(np.float32)
    t0 = reloj()
    modelo(x, training=False)
    primera = 1000.0 * (reloj() - t0)

    llamadas, predicts = [], []
    salida = None
    for _ in range(repeticiones):
        t0 = reloj()
        salida = np.asarray(modelo(x, training=False))
        llamadas.append(1000.0 * (reloj() - t0))
    for _ in range(max(1, repeticiones // 2)):
        t0 = reloj()
        por_predict = np.asarray(modelo.predict(x, verbose=0))
        predicts.append(1000.0 * (reloj() - t0))
    return {
        "primera_ms": primera,
        "llamada_ms": statistics.median(llamadas),
        "llamada_p95": _percentil(llamadas, 95),
        "predict_ms": statistics.median(predicts),
        "predict_p95": _percentil(predicts, 95),
        "max_dif": float(np.abs(salida - por_predict).max()),
    }


@dataclass
class Registro:
    """Lo que se midió de una imagen."""

    clase: str
    caras: list = field(default_factory=list)       # sin minSize, en el orden del detector
    caras_min: list = field(default_factory=list)   # con minSize
    ms: float = 0.0
    ms_min: float = 0.0
    # variante -> si acertó. Falta la variante si en ella no se detectó cara.
    aciertos: dict = field(default_factory=dict)


def _mayor(caras):
    """La misma regla que el clasificador, con su desempate."""
    from telmeow_teleop.emotion_classifier import elegir_rostro

    return elegir_rostro(caras)


def analizar_imagen(img, clase: str, cascade, backend=None, *,
                    min_lado: int = MIN_LADO, variantes=VARIANTES,
                    reloj=time.perf_counter) -> Registro:
    """Detecta con y sin ``minSize`` y, si hay backend, clasifica la cara en
    cada variante. Con el criterio ``"primera"``, que es el del entrenamiento.
    """
    import cv2

    from telmeow_teleop.emotion_classifier import (HAAR_MIN_NEIGHBORS,
                                                   HAAR_SCALE_FACTOR, detectar_rostro)

    gris = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    registro = Registro(clase)
    t0 = reloj()
    caras = cascade.detectMultiScale(gris, scaleFactor=HAAR_SCALE_FACTOR,
                                     minNeighbors=HAAR_MIN_NEIGHBORS)
    registro.ms = 1000.0 * (reloj() - t0)
    t0 = reloj()
    caras_min = cascade.detectMultiScale(gris, scaleFactor=HAAR_SCALE_FACTOR,
                                         minNeighbors=HAAR_MIN_NEIGHBORS,
                                         minSize=(min_lado, min_lado))
    registro.ms_min = 1000.0 * (reloj() - t0)
    registro.caras = [tuple(int(v) for v in c) for c in caras]
    registro.caras_min = [tuple(int(v) for v in c) for c in caras_min]

    if backend is None:
        return registro
    if registro.caras:
        x, y, w, h = registro.caras[0]
        rostro = img[y:y + h, x:x + w]
        entradas = {
            "tal cual": lambda: rostro,
            "colores al reves": lambda: cv2.cvtColor(rostro, cv2.COLOR_BGR2RGB),
            "48x48 gris": lambda: cv2.cvtColor(cv2.resize(rostro, (48, 48)),
                                               cv2.COLOR_BGR2GRAY),
        }
        for variante, entrada in entradas.items():
            if variante in variantes:
                registro.aciertos[variante] = backend.predict(entrada()).etiqueta == clase
    if "volteada" in variantes:
        volteada = cv2.flip(img, 1)
        rect = detectar_rostro(volteada, cascade, criterio="primera")
        if rect is not None:
            x, y, w, h = rect
            registro.aciertos["volteada"] = (
                backend.predict(volteada[y:y + h, x:x + w]).etiqueta == clase
            )
    return registro


def resumir(registros: list[Registro]) -> dict:
    """Agrega los registros en las cifras que cita el plan."""
    con_cara = [r for r in registros if r.caras]
    detecciones = Counter()
    for r in registros:
        detecciones[(r.clase, min(len(r.caras), 2))] += 1
    # clase -> variante -> (aciertos, caras clasificadas)
    aciertos = {clase: {} for clase in EMOTION_LABELS}
    for clase in EMOTION_LABELS:
        for variante in VARIANTES:
            valores = [r.aciertos[variante] for r in registros
                       if r.clase == clase and variante in r.aciertos]
            if valores:
                aciertos[clase][variante] = (sum(valores), len(valores))
    lados = [_mayor(r.caras)[2] for r in con_cara]
    return {
        "imagenes": len(registros),
        "con_cara": len(con_cara),
        "detecciones": detecciones,
        "varias": sum(1 for r in registros if len(r.caras) > 1),
        "primera_no_mayor": sum(1 for r in registros
                                if len(r.caras) > 1 and r.caras[0] != _mayor(r.caras)),
        "min_distinto_mayor": sum(1 for r in con_cara
                                  if r.caras_min and _mayor(r.caras_min) != _mayor(r.caras)),
        "min_distinto_primera": sum(1 for r in con_cara
                                    if r.caras_min and r.caras_min[0] != r.caras[0]),
        "min_perdidas": sum(1 for r in con_cara if not r.caras_min),
        "ms": statistics.median(r.ms for r in registros) if registros else float("nan"),
        "ms_min": statistics.median(r.ms_min for r in registros) if registros else float("nan"),
        "lado_mediana": statistics.median(lados) if lados else float("nan"),
        "lado_p5": _percentil(lados, 5),
        "lado_p95": _percentil(lados, 95),
        "aciertos": aciertos,
    }


def escala_registrada(ruta_csv: Path) -> list[float]:
    """La columna ``escala_cara`` de un registro del head tracker, sin vacíos.

    Es ``HeadTracker.face_scale``: píxeles entre las comisuras externas de los
    ojos, con el operador en su sitio y la cámara de operación.
    """
    import csv

    with open(ruta_csv, encoding="utf-8") as archivo:
        return [float(fila["escala_cara"]) for fila in csv.DictReader(archivo)
                if fila.get("escala_cara") not in (None, "", "nan")]


def relacion_haar_ojos(imagenes, cascade, malla) -> list[float]:
    """Lado de la caja del Haar entre la distancia de las comisuras externas de
    los ojos, en cada imagen en que se tienen las dos cosas.

    Las comisuras son las de ``HeadTracker.face_scale`` (``LANDMARK_IDS[2]`` y
    ``[3]``), para que la relación se pueda aplicar a lo que registra él.
    """
    import math

    import cv2

    from telmeow_teleop.emotion_classifier import detectar_rostro
    from telmeow_teleop.head_tracker import LANDMARK_IDS

    relaciones = []
    for img in imagenes:
        rect = detectar_rostro(img, cascade)
        resultado = malla.process(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        if rect is None or not resultado.multi_face_landmarks:
            continue
        puntos = resultado.multi_face_landmarks[0].landmark
        alto, ancho = img.shape[:2]
        a, b = puntos[LANDMARK_IDS[2]], puntos[LANDMARK_IDS[3]]
        ojos = math.hypot((a.x - b.x) * ancho, (a.y - b.y) * alto)
        if ojos > 0:
            relaciones.append(rect[2] / ojos)
    return relaciones


def _pct(a: int, n: int) -> str:
    return f"{a:4d}/{n:<4d} {100.0 * a / n:5.1f} %" if n else "--"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT / "emotions_dataset")
    parser.add_argument("--paso", type=int, default=1, help="usar 1 de cada N imágenes")
    parser.add_argument("--min-lado", type=int, default=MIN_LADO)
    parser.add_argument("--backend", default="keras",
                        choices=("keras", "deepface", "hsemotion"))
    parser.add_argument("--variantes", nargs="+", default=list(VARIANTES),
                        choices=VARIANTES, metavar="VARIANTE",
                        help=f"entradas con que probar el backend: {', '.join(VARIANTES)}")
    parser.add_argument("--sin-modelo", action="store_true",
                        help="solo el detector, sin cargar ningún backend")
    parser.add_argument("--registro-cabeza", type=Path, metavar="CSV",
                        help="registro del head tracker en el puesto de operación "
                             "(p. ej. calibracion_20260906_integrada.csv): estima la "
                             "caja que le llegará al Haar con el operador sentado")
    parser.add_argument("--paso-malla", type=int, default=100,
                        help="1 de cada N imágenes para la relación caja/ojos")
    args = parser.parse_args(argv)

    import cv2

    from telmeow_teleop.config import AppConfig
    from telmeow_teleop.emotion_classifier import KerasBackend, cargar_cascade, crear_backend

    archivos = [a for a in sorted(args.dataset.glob("*.jpg")) if clase_de(a)][:: args.paso]
    if not archivos:
        print(f"No hay imagenes etiquetadas en {args.dataset}.")
        return 1

    backend = None
    if not args.sin_modelo:
        cfg = AppConfig.from_env()
        red = None
        if args.backend == "keras":
            # La red se carga aparte y ANTES que el backend: el KerasBackend se
            # calienta al construirse, y medir sobre él daría una «primera
            # llamada» que ya no es la primera (salía 10 ms en vez de ~640).
            import keras

            red = medir_red(keras.saving.load_model(cfg.paths.emotion_model, compile=False))
        backend = crear_backend(args.backend, cfg)
        if isinstance(backend, KerasBackend):
            print(f"Modelo {backend.ruta.name}, clases {backend.clases}")
            print(
                f"  red: model(x, training=False) {red['llamada_ms']:.1f} ms (p95 "
                f"{red['llamada_p95']:.1f}) frente a predict {red['predict_ms']:.1f} ms "
                f"(p95 {red['predict_p95']:.1f}); max |diferencia| {red['max_dif']:.1e}; "
                f"primera llamada, en frio, {red['primera_ms']:.0f} ms\n"
            )
        else:
            print(f"Backend {backend.nombre}, colapso '{backend.colapso}'\n")

    cascade = cargar_cascade()
    print(f"Analizando {len(archivos)} imagenes de {args.dataset}...")
    registros = []
    inicio = time.perf_counter()
    for i, archivo in enumerate(archivos, 1):
        img = cv2.imread(str(archivo))
        registros.append(analizar_imagen(img, clase_de(archivo), cascade, backend,
                                         min_lado=args.min_lado, variantes=args.variantes))
        if i % 500 == 0:
            print(f"  {i}/{len(archivos)}  ({time.perf_counter() - inicio:.0f} s)")

    r = resumir(registros)
    print(f"\nDETECTOR ({r['imagenes']} imagenes, {r['con_cara']} con cara)")
    print("  clase         sin cara   una   varias")
    for clase in EMOTION_LABELS:
        d = r["detecciones"]
        print(f"  {clase:12s} {d[(clase, 0)]:9d} {d[(clase, 1)]:5d} {d[(clase, 2)]:8d}")
    print(f"  con varias detecciones: {r['varias']}; en {r['primera_no_mayor']} la primera no es la mayor")
    print("  (lo que depende de la 'primera' varia entre ejecuciones: con varios hilos,"
          " OpenCV devuelve las detecciones en distinto orden)")
    print(f"  lado del rostro: mediana {r['lado_mediana']:.0f} px (p5 {r['lado_p5']:.0f}, p95 {r['lado_p95']:.0f})")
    print(
        f"  Haar: {r['ms']:.1f} ms sin minSize, {r['ms_min']:.1f} ms con minSize="
        f"{args.min_lado}; con minSize cambia el recorte 'mayor' en "
        f"{r['min_distinto_mayor']}, el 'primera' en {r['min_distinto_primera']} "
        f"y se pierde la cara en {r['min_perdidas']}"
    )
    if backend is not None:
        print(f"\nBACKEND '{backend.nombre}' SOBRE EL DATASET PROPIO (NO es su accuracy: ver arriba)")
        columnas = [v for v in VARIANTES if v in args.variantes]
        print("  " + " " * 12 + "".join(f"{v:>22s}" for v in columnas))
        totales = {v: [0, 0] for v in columnas}
        for clase in EMOTION_LABELS:
            fila = ""
            for v in columnas:
                a, n = r["aciertos"][clase].get(v, (0, 0))
                totales[v][0] += a
                totales[v][1] += n
                fila += f"{_pct(a, n):>22s}"
            print(f"  {clase:12s}{fila}")
        print(f"  {'TOTAL':12s}" + "".join(f"{_pct(*totales[v]):>22s}" for v in columnas))

    if args.registro_cabeza is not None:
        import mediapipe as mp

        escalas = escala_registrada(args.registro_cabeza)
        todas = [a for a in sorted(args.dataset.glob("*.jpg")) if clase_de(a)]
        malla = mp.solutions.face_mesh.FaceMesh(static_image_mode=True, max_num_faces=1,
                                                refine_landmarks=True)
        try:
            relaciones = relacion_haar_ojos(
                (cv2.imread(str(a)) for a in todas[:: args.paso_malla]), cascade, malla)
        finally:
            malla.close()
        if not escalas or not relaciones:
            print("\nNo hay escala de cara en el registro o no se pudo medir la relación.")
            return 1
        relacion = statistics.median(relaciones)
        esc = statistics.median(escalas)
        print(f"\nCARA EN OPERACION ({args.registro_cabeza.name})")
        print(f"  escala_cara: mediana {esc:.1f} px (p5 {_percentil(escalas, 5):.1f}, "
              f"p95 {_percentil(escalas, 95):.1f}), {len(escalas)} frames")
        print(f"  caja Haar / escala de cara en el dataset: mediana {relacion:.2f} "
              f"({len(relaciones)} imagenes)")
        print(f"  caja esperada en operacion: ~{esc * relacion:.0f} px "
              f"(p5 {_percentil(escalas, 5) * relacion:.0f}); minSize = {args.min_lado}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
