"""Pasa una sesión grabada por cualquier backend de emoción, sin el operador.

Para qué
--------
El demo del clasificador, con ``--record`` y ``--guardar-recortes``, deja una
sesión: el CSV con una fila por inferencia y, por cada fila con cara, un PNG
con la cara y su contexto. Esta herramienta vuelve a clasificar esas MISMAS
caras con otro backend —el modelo de FER-2013 del P8, HSEmotion, los puntos del
rostro…— y da la misma tabla por etiqueta del protocolo que
``tools/analizar_registro_emocion.py``. Así se comparan modelos sobre lo mismo
y sin volver a sentar al operador, que es la condición para no probar a ciegas
(plan maestro, P6, 2026-10-24).

Lo que NO cambia entre backends, a propósito: el detector (el Haar de la
grabación), las manos sobre la cara y el retorno a neutral sin cara. Son del
sistema, no del modelo, y se toman de las columnas ``rostro``, ``manos`` y
``retorno`` del CSV. Lo que cambia es la predicción de cada cara y, con ella, lo
que la histéresis le habría mandado al robot.

El ancla
--------
Con el mismo backend y el mismo modelo con que se grabó, la predicción tiene
que salir IGUAL que la columna ``prediccion`` en todas las filas, y la confianza
a menos de 5e-5 (el CSV la guarda con 4 decimales). Es lo que demuestra que el
recorte guardado es exactamente lo que vio el backend en vivo: si no
coincidiera, ninguna comparación de esta herramienta valdría. Se comprueba sola
cuando el backend pedido es el de la grabación.

Uso::

    python tools/evaluar_backends.py emocion.csv --recortes emocion_recortes
    python tools/evaluar_backends.py emocion.csv --recortes emocion_recortes \\
        --backend keras --backend keras:emotion_model_fer2013.keras --backend hsemotion
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import analizar_registro_emocion as are  # noqa: E402

from telmeow_teleop.config import EMOTION_LABELS, PROJECT_ROOT  # noqa: E402

#: Tolerancia de la confianza en el ancla: el CSV la guarda con 4 decimales.
TOLERANCIA = 5e-5

#: Etiquetas del protocolo en que la cara del operador tiene una expresión que
#: se puede juzgar contra la predicción cruda (0-4, y la 5 es neutral).
CLASES_PROTOCOLO = {e: are.ESPERADAS[e] for e in (0, 1, 2, 3, 4, 5)}


def crear(spec: str, cfg=None):
    """``keras`` (el modelo de la configuración), ``keras:RUTA`` (otro modelo
    propio, p. ej. el del P8), ``deepface`` o ``hsemotion``; y los candidatos
    del P6.4 (``telmeow_teleop.emotion_backends``): ``blendshapes:PESOS.json``,
    ``onnx:MODELO.onnx`` y ``combinado:SPEC+SPEC`` (la media de varios)."""
    from telmeow_teleop.config import AppConfig
    from telmeow_teleop.emotion_classifier import KerasBackend, crear_backend

    cfg = cfg or AppConfig.from_env()
    tipo, _, resto = spec.partition(":")
    if tipo == "combinado" and resto:
        from telmeow_teleop.emotion_backends import CombinadoBackend
        return CombinadoBackend([crear(s, cfg) for s in resto.split("+")])
    ruta = Path(resto) if resto else None
    if ruta is not None and not ruta.is_absolute():
        ruta = PROJECT_ROOT / ruta
    if tipo == "keras" and ruta is not None:
        return KerasBackend(ruta)
    if tipo == "blendshapes":
        from telmeow_teleop.emotion_backends import BlendshapesBackend
        return BlendshapesBackend(ruta, cfg.paths.face_landmarker)
    if tipo == "onnx":
        from telmeow_teleop.emotion_backends import OnnxHSEmotionBackend
        return OnnxHSEmotionBackend(ruta, colapso=cfg.emotion.colapso)
    return crear_backend(spec, cfg)


#: Segundos del principio de la etiqueta 0 con que se calibra un backend que
#: mide la cara respecto a la neutra del operador, si la sesión se grabó con
#: otro: los mismos que usa el clasificador al ver la primera cara. Hasta el
#: 2026-10-25 eran 10; con 3 la sesión del protocolo v3 da lo mismo (P6.4).
CALIBRACION_S = 3.0


def episodios_de_calibracion(filas: list[dict], segundos: float = CALIBRACION_S
                              ) -> list[tuple[list[int], int]]:
    """Las calibraciones de la grabación: ``(filas que aportaron cara, fila en
    que terminó)``, con la regla de ``EmotionClassifier._recoger_neutra``.

    Cada una empieza en la primera fila ``calibrando`` con cara y sin manos, y
    termina en la primera de esas con ``segundos`` o más desde la de inicio.
    Hace falta separarlas porque el operador puede volver a calibrar (tecla
    ``c``): el 2026-10-25 lo hizo a los 12 s, y juntar las dos daría otra cara
    neutra que la que usó el robot.
    """
    episodios: list[tuple[list[int], int]] = []
    actual: list[int] = []
    inicio = None
    for i, f in enumerate(filas):
        if not (f["calibrando"] and f["rostro"] and not f["manos"]):
            continue
        if inicio is None:
            inicio = f["t"]
        actual.append(i)
        # El CSV guarda el tiempo con 4 decimales: holgura de 1e-4.
        if f["t"] - inicio >= segundos - 1e-4:
            episodios.append((actual, i))
            actual, inicio = [], None
    return episodios


def calibrar(backend, filas: list[dict], carpeta: Path,
             segundos: float = CALIBRACION_S) -> int | None:
    """Fija la cara neutra de un backend relativo ANTES de evaluar, cuando la
    sesión no trae sus propias calibraciones: con los primeros ``segundos`` de
    la etiqueta 0, con cara y sin manos, que es lo que habría hecho el
    clasificador al arrancar. Devuelve cuántas caras se usaron, o ``None`` si
    el backend no se calibra o la sesión trae filas ``calibrando`` (entonces
    calibra ``evaluar``, en el mismo punto que en vivo).
    """
    if not getattr(backend, "relativo", False):
        return None
    if any(f["calibrando"] for f in filas):
        return None
    neutras = [f for f in filas if f["etiqueta"] == 0 and f["rostro"]]
    if not neutras:
        raise ValueError("la sesión no tiene etiqueta 0 con cara para calibrar")
    t0 = neutras[0]["t"]
    caras = [cara_de(f, carpeta) for f in neutras
             if f["t"] < t0 + segundos and not f["manos"]]
    return backend.calibrar(caras)


def nombre_de(backend) -> str:
    from telmeow_teleop.emotion_classifier import _modelo_de

    modelo = _modelo_de(backend)
    return f"{backend.nombre}:{modelo}" if modelo else backend.nombre


def cargar_sesion(csv: Path, carpeta: Path) -> list[dict]:
    """Las filas de la sesión, comprobando que cada fila con cara tiene su
    recorte en ``carpeta``."""
    filas = are.cargar(csv)
    faltan = [
        i for i, f in enumerate(filas)
        if f["rostro"] and not (f.get("recorte") and (Path(carpeta) / f["recorte"]).is_file())
    ]
    if faltan:
        raise ValueError(
            f"{len(faltan)} filas con cara no tienen recorte en {carpeta} (la "
            f"primera, la {faltan[0]}). ¿Se grabó con --guardar-recortes?"
        )
    return filas


def cara_de(fila: dict, carpeta: Path):
    """La caja del Haar dentro del recorte: lo que recibió el backend en vivo."""
    import cv2

    imagen = cv2.imread(str(Path(carpeta) / fila["recorte"]))
    if imagen is None:
        raise OSError(f"no se pudo leer {fila['recorte']}")
    rx, ry, w, h = int(fila["rx"]), int(fila["ry"]), int(fila["w"]), int(fila["h"])
    return imagen[ry:ry + h, rx:rx + w]


@dataclass
class Evaluacion:
    backend: str
    filas: list[dict]
    resumenes: list
    ms: list[float]
    #: (clase pedida por el protocolo, predicción cruda) -> veces
    confusion: Counter
    #: filas cuya predicción coincide con la grabada (solo con el backend grabado)
    coinciden: int | None
    comparables: int
    #: caras con que se calibró la neutra, o None si el backend no se calibra
    calibracion: int | None = None
    #: de dónde salieron: las filas 'calibrando' de la grabación, o los
    #: primeros segundos de la etiqueta 0
    origen_calibracion: str = ""


def evaluar(filas: list[dict], carpeta: Path, backend, reloj=time.perf_counter,
            calibracion_s: float = CALIBRACION_S,
            histeresis: tuple[int, float] | None = None,
            sin_apoyo: int | None = None) -> Evaluacion:
    """Reclasifica cada cara con ``backend`` y rehace lo que habría hecho el robot.

    Sin cara, con la mano sobre la cara o con retorno a neutral, la fila se
    trata igual que en la grabación (son decisiones del sistema, no del
    modelo). En las filas en manual el robot mostraba la tecla, y así se queda.

    ``histeresis`` = ``(n_frames, min_confidence)`` rehace lo del robot con
    otros parámetros; por defecto, los de la grabación. ``sin_apoyo``, igual
    con los votos sin apoyo tras los que se pasa a neutral (0 lo desactiva).
    """
    nombre = nombre_de(backend)
    grabado = filas[0].get("backend") + (
        f":{filas[0]['modelo']}" if filas[0].get("modelo") else "")
    comparar = nombre == grabado
    calibradas = calibrar(backend, filas, carpeta, calibracion_s)
    origen = f"los primeros {calibracion_s:g} s de la etiqueta 0"
    # Las calibraciones de la grabación se rehacen donde ocurrieron: las filas
    # posteriores se clasifican con la neutra que tenía el robot.
    fin_de_episodio: dict[int, list[int]] = {}
    if getattr(backend, "relativo", False):
        episodios = episodios_de_calibracion(filas, calibracion_s)
        fin_de_episodio = {fin: caras for caras, fin in episodios}
        if episodios:
            tamanos = [len(caras) for caras, _ in episodios]
            origen = (f"la grabacion: {len(episodios)} calibracion(es) en vivo, de "
                      f"{', '.join(map(str, tamanos))} caras; la ultima")
            calibradas = tamanos[-1]
    nuevas: list[dict] = []
    ms: list[float] = []
    coinciden = 0
    comparables = 0
    confusion: Counter = Counter()
    for i, fila in enumerate(filas):
        nueva = dict(fila)
        if i in fin_de_episodio:
            backend.calibrar([cara_de(filas[j], carpeta) for j in fin_de_episodio[i]])
        if fila["rostro"] and not fila["calibrando"]:
            cara = cara_de(fila, carpeta)
            inicio = reloj()
            d = backend.distribucion(cara)
            ms.append(1000.0 * (reloj() - inicio))
            nueva["prediccion"] = d.etiqueta or ""
            nueva["confianza_pred"] = round(float(d.confianza), 4)
            if comparar:
                comparables += 1
                coinciden += (
                    nueva["prediccion"] == (fila["prediccion"] or "")
                    and fila["confianza_pred"] is not None
                    and abs(nueva["confianza_pred"] - fila["confianza_pred"]) <= TOLERANCIA
                )
            esperada = CLASES_PROTOCOLO.get(fila["etiqueta"])
            if esperada is not None and not fila["manual"] and not fila["manos"]:
                confusion[(esperada, nueva["prediccion"] or are.SIN_EQUIVALENTE)] += 1
        nuevas.append(nueva)
    n_frames, min_confidence = histeresis or are.parametros_grabados(filas)
    emociones, _ = are.reproducir(nuevas, n_frames, min_confidence, sin_apoyo)
    for nueva, emocion in zip(nuevas, emociones):
        nueva["emocion"] = nueva["manual"] or emocion
    grupos = are.indices_por_etiqueta(nuevas)
    resumenes = [are.resumir_etiqueta(e, [nuevas[i] for i in grupos[e]]) for e in sorted(grupos)]
    return Evaluacion(nombre, nuevas, resumenes, ms, confusion,
                      coinciden if comparar else None, comparables, calibradas, origen)


def aciertos_crudos(ev: Evaluacion) -> dict[str, tuple[int, int]]:
    """Por clase del protocolo: predicciones crudas correctas y total."""
    totales: Counter = Counter()
    buenos: Counter = Counter()
    for (esperada, prediccion), veces in ev.confusion.items():
        totales[esperada] += veces
        if prediccion == esperada:
            buenos[esperada] += veces
    return {c: (buenos[c], totales[c]) for c in EMOTION_LABELS if totales[c]}


def imprimir(ev: Evaluacion) -> None:
    print(f"\n{'=' * 74}\nBACKEND {ev.backend}\n{'=' * 74}")
    if ev.coinciden is not None:
        estado = "EXACTA" if ev.coinciden == ev.comparables else "NO COINCIDE"
        print(f"  ancla: la prediccion grabada se reproduce en {ev.coinciden} de "
              f"{ev.comparables} caras: {estado}")
    if ev.calibracion is not None:
        print(f"  calibrado con {ev.calibracion} caras de {ev.origen_calibracion}")
    if ev.ms:
        print(f"  coste por cara: mediana {statistics.median(ev.ms):.1f} ms, "
              f"p95 {sorted(ev.ms)[int(0.95 * (len(ev.ms) - 1))]:.1f} ms")
    confianzas = [f["confianza_pred"] for f in ev.filas
                  if f["rostro"] and f["prediccion"] and f["confianza_pred"] is not None]
    if confianzas:
        # Un modelo que reparte casi por igual (todas ~0.3) acierta la clase
        # cruda a veces, pero no pasa nunca de min_confidence: el robot no se
        # mueve. Sin esta cifra la tabla de aciertos crudos engaña.
        print(f"  confianza de la prediccion: mediana {statistics.median(confianzas):.2f}")
    print("\n  prediccion CRUDA frente a la cara pedida (etiquetas 0-5, sin manual ni manos):")
    for clase, (buenos, total) in aciertos_crudos(ev).items():
        otras = Counter({p: v for (e, p), v in ev.confusion.items() if e == clase and p != clase})
        detalle = ", ".join(f"{p} {v}" for p, v in otras.most_common(3))
        print(f"    {clase:12s} {buenos:4d}/{total:<4d} {100 * buenos / total:5.1f} %"
              + (f"   (se confunde con: {detalle})" if detalle else ""))
    print("\n  lo que habria hecho el robot, por etiqueta:")
    for r in ev.resumenes:
        estado = {True: "OK", False: "NO", None: "--"}[r.ok]
        extra = ""
        if r.etiqueta == are.LECTURA:
            extra = f"   {r.cambios} cambios ({are._num(r.por_minuto, 1, 1)}/min)"
        if r.etiqueta == are.MANOS:
            extra = f"   {r.cambios} cambios, manos en el {100 * r.manos:.0f} %"
        if r.retorno_s is not None:
            extra += f"   neutral a los {r.retorno_s:.1f} s sin cara"
        print(f"    {r.etiqueta}  {estado:2s}  robot {are._texto_reparto(r.enviadas)}{extra}")


def fraccion(r, valor: str | None) -> float | None:
    """Fracción de la etiqueta en que el robot mostró ``valor``."""
    if r is None or valor is None:
        return None
    return dict(r.enviadas).get(valor, 0.0)


def criterios(ev: Evaluacion) -> dict:
    """Los criterios del P6.4, fijados antes de ver los números, de un backend.

    Todo es lo que habría hecho el ROBOT (tras la histéresis), no la
    predicción cruda: es lo que ve quien habla con él.
    """
    por = {r.etiqueta: r for r in ev.resumenes}
    expresiones = [fraccion(por.get(e), are.ESPERADAS[e]) for e in (0, 1, 2, 3, 4)]
    expresiones = [x for x in expresiones if x is not None]
    lectura = por.get(are.LECTURA)
    manos = por.get(are.MANOS)
    return {
        "triste": fraccion(por.get(2), "triste"),
        "media_0_4": sum(expresiones) / len(expresiones) if expresiones else None,
        "lectura_neutral": fraccion(lectura, are.NEUTRAL),
        "lectura_por_min": lectura.por_minuto if lectura else None,
        "manos_neutral": fraccion(manos, are.NEUTRAL),
        "manos_cambios": manos.cambios if manos else None,
        "ok": sum(1 for r in ev.resumenes if r.ok),
        "juzgables": sum(1 for r in ev.resumenes if r.ok is not None),
        "ms": statistics.median(ev.ms) if ev.ms else None,
    }


def imprimir_criterios(evaluaciones: list[Evaluacion]) -> None:
    def pct(x):
        return "   --" if x is None else f"{100 * x:4.0f}%"

    print(f"\n{'=' * 74}\nCRITERIOS DEL P6.4  (lo que habria hecho el robot)\n{'=' * 74}")
    print("  triste  = % de la etiqueta 2 en triste      media = % de 0-4 en la esperada")
    print("  leer    = etiqueta 5: % en neutral y cambios por minuto")
    print("  manos   = etiqueta 7: % en neutral y cambios     ms = mediana por cara\n")
    print(f"  {'backend':44s} triste media |  leer    /min | manos  camb |  ms  | OK")
    for ev in evaluaciones:
        c = criterios(ev)
        por_min = "  --" if c["lectura_por_min"] is None else f"{c['lectura_por_min']:5.1f}"
        camb = "  --" if c["manos_cambios"] is None else f"{c['manos_cambios']:4d}"
        ms = "  --" if c["ms"] is None else f"{c['ms']:5.1f}"
        print(f"  {ev.backend[:44]:44s} {pct(c['triste'])} {pct(c['media_0_4'])} | "
              f"{pct(c['lectura_neutral'])} {por_min} | {pct(c['manos_neutral'])} {camb} | "
              f"{ms} | {c['ok']}/{c['juzgables']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("csv", type=Path)
    parser.add_argument("--recortes", type=Path,
                        help="carpeta de --guardar-recortes (por defecto, <csv>_recortes)")
    parser.add_argument("--rejilla", action="store_true",
                        help="con cada backend, la rejilla n_frames x min_confidence del "
                             "analizador sobre SUS predicciones: para recalibrar la "
                             "histeresis del backend elegido")
    parser.add_argument("--calibracion-s", type=float, default=CALIBRACION_S,
                        help="segundos de la etiqueta 0 con que se calibra un backend "
                             "relativo si la sesion no tiene filas 'calibrando'")
    parser.add_argument("--histeresis", nargs=2, metavar=("N_FRAMES", "MIN_CONFIDENCE"),
                        help="lo que habria hecho el robot con otros parametros de la "
                             "histeresis, en vez de los de la grabacion")
    parser.add_argument("--neutral-sin-apoyo-s", type=float,
                        help="segundos de votos sin apoyo tras los que el robot pasa a "
                             "neutral (0 lo desactiva); por defecto, los de la grabacion")
    parser.add_argument("--backend", action="append",
                        help="keras, keras:RUTA, deepface, hsemotion, blendshapes:PESOS, "
                             "onnx:MODELO o combinado:SPEC+SPEC; se puede repetir")
    args = parser.parse_args(argv)
    carpeta = args.recortes or args.csv.with_name(args.csv.stem + "_recortes")
    try:
        filas = cargar_sesion(args.csv, carpeta)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"{args.csv.name}: {len(filas)} inferencias, "
          f"{sum(1 for f in filas if f['rostro'])} con cara, recortes en {carpeta}")
    evaluaciones = []
    for spec in args.backend or ["keras"]:
        histeresis = (int(args.histeresis[0]), float(args.histeresis[1])) if args.histeresis else None
        from telmeow_teleop.config import AppConfig
        sin_apoyo = (None if args.neutral_sin_apoyo_s is None
                     else round(args.neutral_sin_apoyo_s * AppConfig.from_env().rates.emotion_hz))
        evaluaciones.append(evaluar(filas, carpeta, crear(spec), calibracion_s=args.calibracion_s,
                                    histeresis=histeresis, sin_apoyo=sin_apoyo))
        if histeresis:
            print(f"\n  (robot con n_frames={histeresis[0]}, min_confidence={histeresis[1]:g})")
        imprimir(evaluaciones[-1])
        if args.rejilla:
            # Las predicciones nuevas por la misma ClassHysteresis con otros
            # parámetros: es la rejilla del analizador, pero del backend
            # evaluado y no del grabado.
            grabado = are.parametros_grabados(filas)
            print()
            are.imprimir_rejilla(are.barrido(evaluaciones[-1].filas, grabado=grabado), grabado)
    imprimir_criterios(evaluaciones)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
