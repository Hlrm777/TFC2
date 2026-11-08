"""Reentrena la emoción con la cara del operador a varias alturas de cabeza (P7.7).

Lo pidió el operador tras p76: con la cabeza baja o alta, su cara neutra se lee
como enojo o como tristeza. La regresión de los blendshapes se entrenó con caras
mirando a la pantalla; aquí aprende además con sus caras neutras, enojadas y
tristes mirando al frente, abajo y arriba.

Es el método del P6.5 (``analizar_p65.py reentrenar``), con dos cambios: cada
sesión dice qué expresión pide cada etiqueta de SU protocolo
(``ESPERADAS_POR_SESION``), y la evaluación es sobre la sesión ``p77``, grabada
con ``sesion_verificacion.py --entrenar`` para esto y que el modelo no ve al
entrenar. **Nunca se parte una grabación al azar** (bug 13).

El criterio está fijado antes de grabar (``criterio``): el reentrenado vale si
la media de los cuatro tramos neutrales sube al menos ``MEJORA_NEUTRAL`` puntos
o todos quedan en ``NEUTRAL_MIN`` % o más, y ni el enojo ni la tristeza caen más
de ``CAIDA_MAXIMA`` puntos. Las cifras son del robot (tras la histéresis y la
regla del cabeceo, que se reproducen) por tramo.

Uso::

    python tools/reentrenar_p77.py                     # entrena con todo lo de antes, evalúa en p77
    python tools/reentrenar_p77.py --final --salida emotion_blendshapes_facs_relativo_p77.json

``--final`` entrena con todo, p77 incluida: es como se hacen los pesos que se
adoptan, después de que la evaluación diga que valen.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from telmeow_teleop.config import EMOTION_LABELS, PROJECT_ROOT, AppConfig  # noqa: E402

_P65 = {0: "neutral", 1: "triste", 2: "triste", 3: "enojado", 4: "neutral",
        5: "triste", 6: "triste", 7: "enojado", 8: "feliz", 9: "sorprendido", 10: "neutral"}

#: La expresión que pide cada etiqueta, por sesión. Solo las etiquetas en las
#: que la cara está clara: fuera las de manos en la cara, gestos de brazos o
#: hablar sonriendo, que mezclan expresiones.
ESPERADAS_POR_SESION: dict[str, dict[int, str]] = {
    "p65_g1": _P65, "p65_g2": _P65, "p65_s1": _P65, "p65_s2": _P65,
    "p7_final": _P65,
    "p75": {0: "neutral", 2: "neutral"},
    "p76": {0: "neutral", 1: "neutral", 2: "enojado", 3: "neutral"},
}

#: El protocolo de ``sesion_verificacion.py --entrenar`` (``PROTOCOLO_ENTRENAR``).
ESPERADAS_P77: dict[int, str] = {
    0: "neutral", 1: "neutral", 2: "neutral", 3: "neutral", 4: "neutral",
    5: "enojado", 6: "enojado", 7: "neutral", 8: "triste", 9: "triste", 10: "triste",
    11: "neutral",
}

#: Los tramos que se comparan en p77.
TRAMOS_P77: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("neutral al frente", (1, 11)),
    ("neutral abajo", (2,)),
    ("neutral arriba", (3,)),
    ("neutral hablando", (4,)),
    ("enojado al frente", (5,)),
    ("enojado abajo", (6,)),
    ("triste sin ceño", (8,)),
    ("triste con ceño", (9,)),
    ("triste abajo", (10,)),
)
NEUTRALES = ("neutral al frente", "neutral abajo", "neutral arriba", "neutral hablando")
ENOJO = ("enojado al frente", "enojado abajo")
TRISTEZA = ("triste sin ceño", "triste con ceño", "triste abajo")

#: Criterio, fijado el 2026-11-07 antes de grabar p77.
MEJORA_NEUTRAL = 5.0
NEUTRAL_MIN = 90.0
CAIDA_MAXIMA = 5.0

#: Las sesiones con que se entrena por defecto: todas las del pipeline con
#: recortes y etiquetas de expresión.
SESIONES = tuple(ESPERADAS_POR_SESION)


def muestras(filas: list[dict], vectores: dict[int, np.ndarray], episodios,
             esperadas: dict[int, str]) -> tuple[np.ndarray, np.ndarray]:
    """``(X, y)`` de una sesión: los rasgos de cada cara que vota, menos la
    neutra vigente, con la clase que pide su etiqueta en ``esperadas``."""
    from analizar_p65 import _vota, neutras_por_fila

    neutras = neutras_por_fila(filas, vectores, episodios)
    clases = list(EMOTION_LABELS)
    X, y = [], []
    for i, f in enumerate(filas):
        esperada = esperadas.get(f["etiqueta"])
        if esperada is None or not _vota(f) or i not in vectores or i not in neutras:
            continue
        X.append(vectores[i] - neutras[i])
        y.append(clases.index(esperada))
    return np.array(X).reshape(len(X), -1), np.array(y, dtype=int)


def resumir(filas: list[dict]) -> dict[str, float | None]:
    """Por tramo de p77: % de sus inferencias (sin las que calibran) en que el
    robot puso la expresión pedida."""
    resumen: dict[str, float | None] = {}
    for nombre, etiquetas in TRAMOS_P77:
        tramo = [f for f in filas if f["etiqueta"] in etiquetas and not f["calibrando"]]
        esperada = ESPERADAS_P77[etiquetas[0]]
        resumen[nombre] = (100.0 * sum(f["emocion"] == esperada for f in tramo) / len(tramo)
                           if tramo else None)
    return resumen


def _media(resumen, nombres) -> float | None:
    v = [resumen[n] for n in nombres if resumen.get(n) is not None]
    return statistics.mean(v) if v else None


@dataclass(frozen=True)
class Veredicto:
    mejora_neutral: float | None
    caida_enojo: float | None
    caida_tristeza: float | None
    vale: bool
    motivos: tuple[str, ...]


def criterio(base: dict[str, float | None], candidato: dict[str, float | None],
             neutrales=NEUTRALES, enojo=ENOJO, tristeza=TRISTEZA) -> Veredicto:
    """Los tramos se pueden cambiar para otra sesión con otros nombres (p78)."""
    motivos = []
    nb, nc = _media(base, neutrales), _media(candidato, neutrales)
    mejora = None if None in (nb, nc) else nc - nb
    todos = all(candidato.get(n) is not None and candidato[n] >= NEUTRAL_MIN for n in neutrales)
    if mejora is None:
        motivos.append("faltan los tramos neutrales")
    elif mejora < MEJORA_NEUTRAL and not todos:
        motivos.append(f"el neutral sube {mejora:+.0f} puntos (< {MEJORA_NEUTRAL:.0f}) y no todos "
                       f"los tramos llegan al {NEUTRAL_MIN:.0f} %")
    caidas = {}
    for texto, nombres in (("enojo", enojo), ("tristeza", tristeza)):
        b, c = _media(base, nombres), _media(candidato, nombres)
        caidas[texto] = None if None in (b, c) else b - c
        if caidas[texto] is None:
            motivos.append(f"faltan los tramos de {texto}")
        elif caidas[texto] > CAIDA_MAXIMA:
            motivos.append(f"{texto}: cae {caidas[texto]:.0f} puntos (> {CAIDA_MAXIMA:.0f})")
    return Veredicto(mejora, caidas["enojo"], caidas["tristeza"], not motivos, tuple(motivos))


def _ruta(nombre: str) -> Path:
    return PROJECT_ROOT / f"{nombre}_emocion.csv"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--entrenar", nargs="*", default=list(SESIONES),
                   help="sesiones (sin _emocion.csv) con que se entrena")
    p.add_argument("--evaluar", default="p77", help="la sesion que NO entrena")
    p.add_argument("--final", action="store_true", help="entrena tambien con la de evaluar, sin evaluar")
    p.add_argument("--salida", type=Path)
    a = p.parse_args(argv)

    import analizar_p65 as p65
    from entrenar_blendshapes import CACHE, sha256
    from telmeow_teleop.emotion_backends import BlendshapesBackend

    cfg = AppConfig.from_env()
    operacion = Path(cfg.paths.emotion_blendshapes)
    base_bs = BlendshapesBackend(operacion, cfg.paths.face_landmarker)
    cache = json.loads(Path(CACHE).read_text(encoding="utf-8"))
    if cache["task_sha256"] != sha256(cfg.paths.face_landmarker):
        p.error(f"{CACHE} es de otro face_landmarker.task: rehazlo con entrenar_blendshapes.py")
    X, y = p65.muestras_del_dataset(cache, base_bs.rasgos)
    partes = [f"dataset propio: {len(y)} fotos"]
    sesiones = list(a.entrenar) + ([a.evaluar] if a.final else [])
    for nombre in sesiones:
        esperadas = ESPERADAS_P77 if nombre == a.evaluar else ESPERADAS_POR_SESION[nombre]
        filas, carpeta = p65._cargar(_ruta(nombre))
        Xs, ys = muestras(filas, p65._vectores(filas, carpeta, base_bs),
                          p65.episodios_de(filas, carpeta, cfg), esperadas)
        if len(ys):
            X, y = np.vstack([X, Xs]), np.concatenate([y, ys])
        partes.append(f"{nombre}: {len(ys)} caras")
    print("Entrenando con " + "; ".join(partes))
    pesos = p65.entrenar(X, y, base_bs.rasgos, {"sesiones": sesiones, "task_sha256": cache["task_sha256"]})
    pesos["origen"] = "tools/reentrenar_p77.py (P7.7)"
    salida = a.salida or (PROJECT_ROOT / "modelos_externos" / "blendshapes_p77_candidato.json")
    salida.write_bytes(json.dumps(pesos, indent=1).encode("utf-8"))
    print(f"pesos en {salida}")
    if a.final:
        return 0

    filas, carpeta = p65._cargar(_ruta(a.evaluar))
    episodios = p65.episodios_de(filas, carpeta, cfg)
    import evaluar_backends as eb

    def evaluar(pesos_json: Path):
        motor = eb.crear(f"combinado:onnx:{cfg.paths.emotion_onnx}+blendshapes:{pesos_json}"
                         f"+cejas:{cfg.emotion.umbral_cejas}", cfg)
        return eb.evaluar(filas, carpeta, motor, episodios=episodios)

    ev_base, ev_cand = evaluar(operacion), evaluar(salida)
    print(f"\nEVALUACION sobre {a.evaluar}")
    if ev_base.coinciden is not None:
        exacta = ev_base.coinciden == ev_base.comparables
        print(f"  ancla: los pesos de operacion reproducen lo grabado en {ev_base.coinciden} de "
              f"{ev_base.comparables} caras" + ("" if exacta else "   <-- NO CUADRA: estas tablas no valen"))
    base, cand = resumir(ev_base.filas), resumir(ev_cand.filas)
    print(f"  {'tramo':20s} | operacion | reentrenado")
    for nombre, _ in TRAMOS_P77:
        fmt = lambda v: "   --" if v is None else f"{v:5.0f} %"  # noqa: E731
        print(f"  {nombre:20s} | {fmt(base[nombre]):>9s} | {fmt(cand[nombre]):>11s}")
    v = criterio(base, cand)
    for motivo in v.motivos:
        print(f"  - {motivo}")
    print("  => el reentrenado VALE: hacer los pesos finales con --final" if v.vale
          else "  => el reentrenado no vale")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
