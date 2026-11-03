"""P6.5 — Tristeza con ceño, enojo y gafas (plan maestro, P6, punto 5).

Dos preguntas sobre el combinado, con las grabaciones del protocolo del P6.5
(cuatro sesiones del pipeline con ``--record --guardar-recortes``: dos con
gafas, G1 y G2, y dos sin ellas, S1 y S2):

``gafas``
    ¿Pesan las gafas? Compara, por tramo del protocolo, el acierto del
    combinado en las sesiones con gafas frente a las sin gafas. Usa la
    predicción GRABADA en vivo, sin volver a inferir: es lo que hizo el robot.
    Criterio, fijado antes de grabar: si sin gafas el acierto medio en la
    tristeza (con y sin ceño) y el enojo sube más de ``UMBRAL_GAFAS`` puntos,
    las gafas pesan.

``reentrenar``
    ¿Mejora la tristeza con ceño si la regresión de los blendshapes aprende con
    las caras del operador? Entrena con el dataset propio MÁS las caras de las
    sesiones de entrenamiento, y evalúa el combinado con esos pesos sobre OTRA
    sesión, frente al combinado de operación. **Nunca se parte una grabación al
    azar**: sus frames seguidos son casi idénticos y sería la fuga del bug 13.
    Criterio: el candidato vale si la tristeza con ceño sube, el enojo no cae
    más de ``CAIDA_MAXIMA`` puntos y leyendo no pasa de ``CAMBIOS_MAXIMOS``
    cambios por minuto.

En las dos, el acierto es el del ROBOT (la expresión tras la histéresis) por
tramo, y al lado el de la predicción cruda.

Uso::

    python tools/analizar_p65.py gafas --con p65_g1_emocion.csv p65_g2_emocion.csv \\
                                       --sin p65_s1_emocion.csv p65_s2_emocion.csv
    python tools/analizar_p65.py reentrenar --entrenar p65_g1_emocion.csv \\
                                            --evaluar p65_g2_emocion.csv [--salida candidato.json]

Los recortes de ``X_emocion.csv`` se buscan en ``X_recortes/`` (lo que deja
``main --record X.csv --guardar-recortes X_recortes``); ``--recortes`` lo cambia
si hay una sola sesión.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import analizar_registro_emocion as are  # noqa: E402
from telmeow_teleop.config import EMOTION_LABELS, PROJECT_ROOT, AppConfig  # noqa: E402

#: La expresión que pide cada etiqueta del protocolo del P6.5.
ESPERADAS: dict[int, str] = {
    0: "neutral", 1: "triste", 2: "triste", 3: "enojado", 4: "neutral",
    5: "triste", 6: "triste", 7: "enojado", 8: "feliz", 9: "sorprendido", 10: "neutral",
}

#: Los tramos que se comparan, con sus etiquetas (cada postura se hace dos veces).
TRAMOS: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("neutral", (0, 4)),
    ("triste sin ceño", (1, 5)),
    ("triste con ceño", (2, 6)),
    ("enojado", (3, 7)),
    ("feliz", (8,)),
    ("sorprendido", (9,)),
    ("lectura", (10,)),
)

#: Los tramos de la pregunta de las gafas.
TRAMOS_GAFAS = ("triste sin ceño", "triste con ceño", "enojado")

#: Puntos de acierto (robot, media de ``TRAMOS_GAFAS``) a partir de los que
#: las gafas pesan. Fijado el 2026-11-02, antes de grabar.
UMBRAL_GAFAS = 10.0

#: Lo que puede caer el enojo con los pesos reentrenados, en puntos.
CAIDA_MAXIMA = 5.0

#: Cambios de expresión por minuto que se admiten leyendo en voz alta.
CAMBIOS_MAXIMOS = 1.0

PESOS_OPERACION = PROJECT_ROOT / "emotion_blendshapes_facs_relativo.json"


# --------------------------------------------------------------------------
# Resumen de una sesión
# --------------------------------------------------------------------------


def recortes_de(csv: Path) -> Path:
    """``p65_g1_emocion.csv`` -> ``p65_g1_recortes``."""
    csv = Path(csv)
    base = csv.stem[:-len("_emocion")] if csv.stem.endswith("_emocion") else csv.stem
    return csv.with_name(f"{base}_recortes")


def _vota(fila: dict) -> bool:
    return bool(fila["rostro"]) and not fila["calibrando"] and not fila["manos"] and not fila["manual"]


@dataclass(frozen=True)
class Tramo:
    nombre: str
    robot: float | None        # % de inferencias en que el robot puso la esperada
    crudo: float | None        # % de predicciones crudas correctas (las que votan)
    inferencias: int
    cambios_por_minuto: float | None


def resumir(filas: list[dict]) -> dict[str, Tramo]:
    """Por tramo del protocolo: acierto del robot y de la predicción cruda.

    Las filas que calibran no cuentan (la expresión se mantiene a propósito).
    """
    resumen = {}
    for nombre, etiquetas in TRAMOS:
        tramo = [f for f in filas if f["etiqueta"] in etiquetas and not f["calibrando"]]
        if not tramo:
            resumen[nombre] = Tramo(nombre, None, None, 0, None)
            continue
        esperada = ESPERADAS[etiquetas[0]]
        robot = 100.0 * sum(f["emocion"] == esperada for f in tramo) / len(tramo)
        votan = [f for f in tramo if _vota(f)]
        crudo = (100.0 * sum(f["prediccion"] == esperada for f in votan) / len(votan)
                 if votan else None)
        cambios = None
        if nombre == "lectura":
            cambios = are.por_minuto(are.contar_cambios([f["emocion"] for f in tramo]),
                                     [f["t"] for f in tramo])
        resumen[nombre] = Tramo(nombre, robot, crudo, len(tramo), cambios)
    return resumen


def _media(valores):
    valores = [v for v in valores if v is not None]
    return statistics.mean(valores) if valores else None


def juntar(resumenes: list[dict[str, Tramo]]) -> dict[str, Tramo]:
    """Varias sesiones de una condición: la media de sus porcentajes."""
    juntos = {}
    for nombre, _ in TRAMOS:
        tramos = [r[nombre] for r in resumenes]
        juntos[nombre] = Tramo(
            nombre,
            _media(t.robot for t in tramos),
            _media(t.crudo for t in tramos),
            sum(t.inferencias for t in tramos),
            _media(t.cambios_por_minuto for t in tramos),
        )
    return juntos


@dataclass(frozen=True)
class Gafas:
    con: dict[str, Tramo]
    sin: dict[str, Tramo]
    diferencia: float | None   # sin - con, robot, media de TRAMOS_GAFAS
    pesan: bool


def comparar_gafas(con: list[dict[str, Tramo]], sin: list[dict[str, Tramo]]) -> Gafas:
    a, b = juntar(con), juntar(sin)
    medias = [_media(r[n].robot for n in TRAMOS_GAFAS) for r in (a, b)]
    diferencia = None if None in medias else medias[1] - medias[0]
    return Gafas(a, b, diferencia, diferencia is not None and diferencia > UMBRAL_GAFAS)


# --------------------------------------------------------------------------
# Reentrenar la regresión de los blendshapes
# --------------------------------------------------------------------------


def neutras_por_fila(filas: list[dict], vectores: dict[int, np.ndarray],
                     episodios: list[tuple[list[int], int]]) -> dict[int, np.ndarray]:
    """La cara neutra vigente en cada fila: la de la última calibración que
    terminó antes, como en vivo. Las filas anteriores a la primera no tienen."""
    neutras: dict[int, np.ndarray] = {}
    vigente = None
    fin_de = {fin: caras for caras, fin in episodios}
    for i in range(len(filas)):
        if i in fin_de:
            usables = [vectores[j] for j in fin_de[i] if j in vectores]
            if usables:
                vigente = np.mean(usables, axis=0)
        if vigente is not None:
            neutras[i] = vigente
    return neutras


def muestras_de_sesion(filas: list[dict], vectores: dict[int, np.ndarray],
                       episodios) -> tuple[np.ndarray, np.ndarray]:
    """``(X, y)`` de una sesión: los rasgos de cada cara que vota, menos la
    neutra vigente, con la clase que pide su etiqueta."""
    neutras = neutras_por_fila(filas, vectores, episodios)
    clases = list(EMOTION_LABELS)
    X, y = [], []
    for i, f in enumerate(filas):
        esperada = ESPERADAS.get(f["etiqueta"])
        if esperada is None or not _vota(f) or i not in vectores or i not in neutras:
            continue
        X.append(vectores[i] - neutras[i])
        y.append(clases.index(esperada))
    return np.array(X), np.array(y, dtype=int)


def muestras_del_dataset(cache: dict, rasgos: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """El dataset propio, relativo a su cara neutra: lo mismo que entrenó los
    pesos de operación (``tools/entrenar_blendshapes.py --relativo``)."""
    con_cara = [f for f in cache["filas"] if f["rasgos"]]
    X = np.array([[f["rasgos"][k] for k in rasgos] for f in con_cara])
    clases = list(EMOTION_LABELS)
    y = np.array([clases.index(f["clase"]) for f in con_cara])
    return X - X[y == clases.index("neutral")].mean(0), y


def entrenar(X: np.ndarray, y: np.ndarray, rasgos: list[str], origen: dict) -> dict:
    """Los pesos en el formato de ``BlendshapesBackend``."""
    from entrenar_blendshapes import ajustar

    modelo = ajustar(X, y, len(EMOTION_LABELS))
    return {
        "rasgos": list(rasgos),
        "clases": list(EMOTION_LABELS),
        "relativo": True,
        **{k: v.tolist() for k, v in modelo.items()},
        "origen": "tools/analizar_p65.py reentrenar (P6.5)",
        "fecha": datetime.now().isoformat(timespec="seconds"),
        **origen,
    }


@dataclass(frozen=True)
class Veredicto:
    mejora_triste_con_ceno: float | None
    caida_enojo: float | None
    cambios_lectura: float | None
    vale: bool
    motivos: tuple[str, ...]


def criterio_reentreno(base: dict[str, Tramo], candidato: dict[str, Tramo]) -> Veredicto:
    motivos = []
    mejora = caida = None
    if base["triste con ceño"].robot is not None and candidato["triste con ceño"].robot is not None:
        mejora = candidato["triste con ceño"].robot - base["triste con ceño"].robot
        if mejora <= 0:
            motivos.append(f"la tristeza con ceño no sube ({mejora:+.0f} puntos)")
    else:
        motivos.append("la sesión no tiene el tramo de la tristeza con ceño (etiquetas 2 y 6)")
    if base["enojado"].robot is not None and candidato["enojado"].robot is not None:
        caida = base["enojado"].robot - candidato["enojado"].robot
        if caida > CAIDA_MAXIMA:
            motivos.append(f"el enojo cae {caida:.0f} puntos (> {CAIDA_MAXIMA:.0f})")
    else:
        motivos.append("la sesión no tiene el tramo del enojo (etiquetas 3 y 7)")
    cambios = candidato["lectura"].cambios_por_minuto
    if cambios is not None and cambios > CAMBIOS_MAXIMOS:
        motivos.append(f"leyendo, {cambios:.1f} cambios por minuto (> {CAMBIOS_MAXIMOS:g})")
    vale = mejora is not None and caida is not None and not motivos
    return Veredicto(mejora, caida, cambios, vale, tuple(motivos))


def _vectores(filas, carpeta, backend_bs) -> dict[int, np.ndarray]:
    """Los rasgos de cada cara de la sesión (las que calibran incluidas)."""
    from evaluar_backends import cara_de

    vectores = {}
    for i, f in enumerate(filas):
        if f["rostro"] and not f["manos"] and f.get("recorte"):
            v = backend_bs.vector(cara_de(f, carpeta))
            if v is not None:
                vectores[i] = v
    return vectores


def _evaluar(filas, carpeta, pesos: Path, cfg) -> tuple[dict[str, Tramo], object]:
    """El combinado con ``pesos`` sobre una sesión, rehaciendo sus calibraciones
    donde ocurrieron (``evaluar_backends.evaluar``)."""
    import evaluar_backends as eb

    spec = f"combinado:onnx:{cfg.paths.emotion_onnx}+blendshapes:{pesos}"
    ev = eb.evaluar(filas, carpeta, eb.crear(spec, cfg))
    return resumir(ev.filas), ev


# --------------------------------------------------------------------------
# Salida
# --------------------------------------------------------------------------


def _pct(valor) -> str:
    return "   --" if valor is None else f"{valor:5.0f}"


def imprimir_tabla(columnas: dict[str, dict[str, Tramo]]) -> None:
    nombres = list(columnas)
    print(f"  {'tramo':18s}" + "".join(f" | {n:>15s}" for n in nombres))
    print(f"  {'':18s}" + "".join(f" | {'robot  crudo':>15s}" for _ in nombres))
    for tramo, _ in TRAMOS:
        fila = f"  {tramo:18s}"
        for n in nombres:
            t = columnas[n][tramo]
            fila += f" | {_pct(t.robot)} % {_pct(t.crudo)} %"
        print(fila)
    fila = f"  {'leyendo: camb/min':18s}"
    for n in nombres:
        c = columnas[n]["lectura"].cambios_por_minuto
        fila += f" | {'--' if c is None else f'{c:.1f}':>15s}"
    print(fila)


def _cargar(csv: Path, recortes: Path | None = None):
    import evaluar_backends as eb

    carpeta = recortes or recortes_de(csv)
    return eb.cargar_sesion(csv, carpeta), carpeta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="analizar_p65", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="orden", required=True)
    g = sub.add_parser("gafas", help="con gafas frente a sin gafas (prediccion grabada)")
    g.add_argument("--con", type=Path, nargs="+", required=True)
    g.add_argument("--sin", type=Path, nargs="+", required=True)
    r = sub.add_parser("reentrenar", help="pesos nuevos, evaluados sobre otra sesion")
    r.add_argument("--entrenar", type=Path, nargs="+", required=True)
    r.add_argument("--evaluar", type=Path, nargs="+", required=True)
    r.add_argument("--salida", type=Path, help="donde guardar los pesos candidatos")
    r.add_argument("--recortes", type=Path, help="carpeta de recortes, si hay una sola sesion")
    args = parser.parse_args(argv)

    if args.orden == "gafas":
        con = [resumir(are.cargar(c)) for c in args.con]
        sin = [resumir(are.cargar(s)) for s in args.sin]
        res = comparar_gafas(con, sin)
        print("\nGAFAS (prediccion grabada en vivo; media de las sesiones de cada condicion)\n")
        imprimir_tabla({"con gafas": res.con, "sin gafas": res.sin})
        if res.diferencia is not None:
            print(f"\n  sin gafas - con gafas, robot, media de {', '.join(TRAMOS_GAFAS)}: "
                  f"{res.diferencia:+.1f} puntos (criterio: > {UMBRAL_GAFAS:g})")
            print("  => las gafas PESAN: se declara" if res.pesan
                  else "  => las gafas no pesan segun el criterio")
        return 0

    import evaluar_backends as eb
    from entrenar_blendshapes import CACHE, sha256
    from telmeow_teleop.emotion_backends import BlendshapesBackend

    cfg = AppConfig.from_env()
    if args.recortes is not None and len(args.entrenar) + len(args.evaluar) > 2:
        parser.error("--recortes solo vale con una sesion de entrenamiento y una de evaluacion")
    base_bs = BlendshapesBackend(PESOS_OPERACION, cfg.paths.face_landmarker)
    rasgos = base_bs.rasgos
    cache = json.loads(Path(CACHE).read_text(encoding="utf-8"))
    if cache["task_sha256"] != sha256(cfg.paths.face_landmarker):
        parser.error(f"{CACHE} es de otro face_landmarker.task: rehazlo con entrenar_blendshapes.py")
    X, y = muestras_del_dataset(cache, rasgos)
    partes = [f"dataset propio: {len(y)} fotos"]
    for csv in args.entrenar:
        filas, carpeta = _cargar(csv, args.recortes)
        Xs, ys = muestras_de_sesion(filas, _vectores(filas, carpeta, base_bs),
                                    eb.episodios_de_calibracion(filas))
        X, y = np.vstack([X, Xs]), np.concatenate([y, ys])
        partes.append(f"{csv.name}: {len(ys)} caras")
    print("Entrenando con " + "; ".join(partes))
    pesos = entrenar(X, y, rasgos, {"sesiones": [c.name for c in args.entrenar],
                                    "task_sha256": cache["task_sha256"]})
    salida = args.salida or (PROJECT_ROOT / "modelos_externos" / "blendshapes_p65_candidato.json")
    salida.write_text(json.dumps(pesos, indent=1), encoding="utf-8")
    print(f"pesos candidatos en {salida}")

    for csv in args.evaluar:
        if csv in args.entrenar:
            parser.error(f"{csv.name} esta en las dos listas: seria evaluar con lo entrenado")
        filas, carpeta = _cargar(csv, args.recortes)
        base, ev_base = _evaluar(filas, carpeta, PESOS_OPERACION, cfg)
        cand, _ = _evaluar(filas, carpeta, salida, cfg)
        print(f"\nEVALUACION sobre {csv.name}")
        if ev_base.coinciden is not None:
            print(f"  ancla: el combinado de operacion reproduce lo grabado en "
                  f"{ev_base.coinciden} de {ev_base.comparables} caras")
        imprimir_tabla({"operacion": base, "reentrenado": cand})
        v = criterio_reentreno(base, cand)
        for motivo in v.motivos:
            print(f"  - {motivo}")
        print("  => el reentrenado VALE" if v.vale else "  => el reentrenado no vale")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
