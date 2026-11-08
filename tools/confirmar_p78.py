"""Confirma en una sesión nueva (p78) los pesos reentrenados y la regla suave del
enojo (P7.7).

En p77 el reentrenado mejoró el neutral sin empeorar nada, pero no llegó al
criterio (+4 puntos de media, se pedían 5); y la regla del enojo con el cabeceo,
a 0.03 por grado, apagaba el enojo mirando abajo (12 %), mientras que a 0.015,
reproducida sobre p77, lo dejaba en 74 % sin enojo falso con el neutral mirando
abajo (100 %). Las dos cosas se vieron en p77, así que se confirman en p78,
grabada con ``sesion_verificacion.py --confirmar`` y que nadie ha mirado.

Cuatro variantes sobre p78, con la histéresis y la regla del cabeceo
reproducidas: pesos de operación o los finales del reentrenamiento
(``blendshapes_p77_final.json``, entrenados con todo, p77 incluida), y la regla a
0.03 (la de operación) o a 0.015.

**Criterios, fijados el 2026-11-08 antes de grabar p78:**

- Los pesos (con la regla de operación): el mismo criterio de p77
  (``reentrenar_p77.criterio``): el neutral sube al menos 5 puntos de media o
  todos sus tramos quedan al 90 % o más, y ni el enojo ni la tristeza caen más de
  5 puntos.
- La regla suave (con los pesos que ganen): el enojo mirando abajo sube al menos
  ``SUBIDA_ENOJO_ABAJO`` puntos, el neutral mirando abajo se queda en
  ``NEUTRAL_ABAJO_MIN`` % o más, y ningún otro tramo cae más de
  ``reentrenar_p77.CAIDA_MAXIMA`` puntos.

Uso::

    python tools/confirmar_p78.py
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from telmeow_teleop.config import PROJECT_ROOT, AppConfig  # noqa: E402

#: La expresión que pide cada etiqueta de ``sesion_verificacion.PROTOCOLO_CONFIRMAR``.
ESPERADAS_P78: dict[int, str] = {
    0: "neutral", 1: "neutral", 2: "neutral", 3: "neutral", 4: "neutral",
    5: "enojado", 6: "enojado", 7: "neutral", 8: "triste", 9: "triste", 10: "neutral",
}

TRAMOS_P78: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("neutral al frente", (1, 10)),
    ("neutral abajo", (2,)),
    ("neutral arriba", (3,)),
    ("neutral hablando", (4,)),
    ("enojado al frente", (5,)),
    ("enojado abajo", (6,)),
    ("triste al frente", (8,)),
    ("triste abajo", (9,)),
)
NEUTRALES = ("neutral al frente", "neutral abajo", "neutral arriba", "neutral hablando")
ENOJO = ("enojado al frente", "enojado abajo")
TRISTEZA = ("triste al frente", "triste abajo")

#: Criterio de la regla suave, fijado el 2026-11-08 antes de grabar p78. En p77,
#: reproducida: el enojo mirando abajo de 12 a 74 % y el neutral mirando abajo,
#: 100 % con las dos.
SUBIDA_ENOJO_ABAJO = 30.0
NEUTRAL_ABAJO_MIN = 90.0

#: La regla con que se grabó p78 (la de operación hasta el 2026-11-08) y la suave,
#: que se adoptó ese día al cumplir el criterio.
REGLA_OPERACION = 0.03
REGLA_SUAVE = 0.015

PESOS_FINALES = PROJECT_ROOT / "modelos_externos" / "blendshapes_p77_final.json"


def resumir(filas: list[dict]) -> dict[str, float | None]:
    """Por tramo de p78: % de sus inferencias (sin las que calibran) en que el
    robot puso la expresión pedida."""
    salida: dict[str, float | None] = {}
    for nombre, etiquetas in TRAMOS_P78:
        tramo = [f for f in filas if f["etiqueta"] in etiquetas and not f["calibrando"]]
        esperada = ESPERADAS_P78[etiquetas[0]]
        salida[nombre] = (100.0 * sum(f["emocion"] == esperada for f in tramo) / len(tramo)
                          if tramo else None)
    return salida


@dataclass(frozen=True)
class VeredictoRegla:
    subida_enojo_abajo: float | None
    vale: bool
    motivos: tuple[str, ...]


def criterio_regla(fuerte: dict[str, float | None], suave: dict[str, float | None]) -> VeredictoRegla:
    import reentrenar_p77 as rp

    motivos = []
    a, b = fuerte.get("enojado abajo"), suave.get("enojado abajo")
    subida = None if None in (a, b) else b - a
    if subida is None:
        motivos.append("falta el tramo del enojo mirando abajo")
    elif subida < SUBIDA_ENOJO_ABAJO:
        motivos.append(f"el enojo mirando abajo sube {subida:+.0f} puntos (< {SUBIDA_ENOJO_ABAJO:.0f})")
    neutral = suave.get("neutral abajo")
    if neutral is None or neutral < NEUTRAL_ABAJO_MIN:
        motivos.append(f"el neutral mirando abajo queda en {neutral if neutral is not None else 0:.0f} % "
                       f"(< {NEUTRAL_ABAJO_MIN:.0f})")
    for nombre, _ in TRAMOS_P78:
        if nombre in ("enojado abajo",):
            continue
        x, y = fuerte.get(nombre), suave.get(nombre)
        if x is not None and y is not None and x - y > rp.CAIDA_MAXIMA:
            motivos.append(f"{nombre}: cae {x - y:.0f} puntos (> {rp.CAIDA_MAXIMA:.0f})")
    return VeredictoRegla(subida, not motivos, tuple(motivos))


def con_regla(filas: list[dict], por_grado: float) -> list[dict]:
    """Las filas con la histéresis rehecha y la regla del enojo a ``por_grado``."""
    import analizar_registro_emocion as are

    copia = [dict(f, enojo_por_grado=por_grado) for f in filas]
    emos, _ = are.reproducir(copia, 5, 0.5)
    for f, e in zip(copia, emos):
        f["emocion"] = f["manual"] or e
    return copia


def tramos_en_pausa(sesion_csv: Path) -> list[tuple[float, float]]:
    """Los intervalos ``(desde, hasta)`` en que la publicación estuvo en pausa
    (columna ``pausa`` de ``sesion.csv``)."""
    import csv

    with open(sesion_csv, encoding="utf-8", newline="") as f:
        filas = list(csv.DictReader(f))
    tramos, desde = [], None
    for fila in filas:
        en_pausa = fila.get("pausa") == "1"
        if en_pausa and desde is None:
            desde = float(fila["t"])
        elif not en_pausa and desde is not None:
            tramos.append((desde, float(fila["t"])))
            desde = None
    if desde is not None:
        tramos.append((desde, float(filas[-1]["t"])))
    return tramos


def fuera_de_pausa(filas: list[dict], tramos: list[tuple[float, float]]) -> list[dict]:
    """Las filas que no caen en una pausa. En p78 el operador pausó con la k
    para rascarse la cara, fuera de la postura pedida: con ``--sin-pausas`` se
    comprueba que el veredicto no depende de esos segundos."""
    return [f for f in filas if not any(a <= f["t"] <= b for a, b in tramos)]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--sesion", default="p78")
    p.add_argument("--pesos", type=Path, default=PESOS_FINALES)
    p.add_argument("--sin-pausas", action="store_true",
                   help="ademas, las cifras sin los segundos en que la publicacion estuvo en pausa")
    a = p.parse_args(argv)

    import analizar_p65 as p65
    import evaluar_backends as eb

    cfg = AppConfig.from_env()
    filas, carpeta = p65._cargar(PROJECT_ROOT / f"{a.sesion}_emocion.csv")
    episodios = p65.episodios_de(filas, carpeta, cfg)
    tablas, tablas_sin, pausas = {}, {}, []
    for nombre, pesos in (("operacion", Path(cfg.paths.emotion_blendshapes)), ("reentrenado", a.pesos)):
        motor = eb.crear(f"combinado:onnx:{cfg.paths.emotion_onnx}+blendshapes:{pesos}"
                         f"+cejas:{cfg.emotion.umbral_cejas}", cfg)
        ev = eb.evaluar(filas, carpeta, motor, episodios=episodios)
        if nombre == "operacion" and ev.coinciden is not None:
            exacta = ev.coinciden == ev.comparables
            print(f"ancla: los pesos de operacion reproducen lo grabado en {ev.coinciden} de "
                  f"{ev.comparables} caras" + ("" if exacta else "   <-- NO CUADRA: estas tablas no valen"))
        for regla in (REGLA_OPERACION, REGLA_SUAVE):
            rehechas = con_regla(ev.filas, regla)
            tablas[(nombre, regla)] = resumir(rehechas)
            if a.sin_pausas:
                pausas = tramos_en_pausa(PROJECT_ROOT / f"{a.sesion}.csv")
                tablas_sin[(nombre, regla)] = resumir(fuera_de_pausa(rehechas, pausas))

    _informe(tablas)
    if a.sin_pausas:
        print(f"\n===== SIN LOS SEGUNDOS EN PAUSA: {pausas} =====")
        _informe(tablas_sin)
    return 0


def _informe(tablas: dict) -> None:
    import reentrenar_p77 as rp

    columnas = list(tablas)
    print(f"\n{'tramo':20s} | " + " | ".join(f"{n} {r:g}".rjust(17) for n, r in columnas))
    for tramo, _ in TRAMOS_P78:
        print(f"{tramo:20s} | " + " | ".join(
            ("--" if tablas[c][tramo] is None else f"{tablas[c][tramo]:.0f} %").rjust(17) for c in columnas))

    v = rp.criterio(tablas[("operacion", REGLA_OPERACION)], tablas[("reentrenado", REGLA_OPERACION)],
                    NEUTRALES, ENOJO, TRISTEZA)
    print("\nLos pesos reentrenados (con la regla de operacion):")
    for motivo in v.motivos:
        print(f"  - {motivo}")
    print("  => VALEN: se adoptan" if v.vale else "  => no valen: se quedan los de operacion")
    pesos = "reentrenado" if v.vale else "operacion"
    r = criterio_regla(tablas[(pesos, REGLA_OPERACION)], tablas[(pesos, REGLA_SUAVE)])
    print(f"\nLa regla suave ({REGLA_SUAVE:g} por grado, con los pesos de {pesos}):")
    for motivo in r.motivos:
        print(f"  - {motivo}")
    print("  => VALE: se adopta" if r.vale else "  => no vale: se queda la de 0.03")


if __name__ == "__main__":
    raise SystemExit(main())
