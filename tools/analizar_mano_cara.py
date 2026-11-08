"""Reproduce el detector de la mano en la cara sobre grabaciones de brazos (P7.5).

No hace falta BlazePose: el detector solo mira los píxeles de la mano y los
hombros, y el CSV de brazos los graba en cada frame (``img_*``). Da, por etiqueta
y por brazo, el % del tiempo con el gesto reconocido y cuántas veces empezó; así
se ve si salta donde no debe (saludo, «yo no fui», narración) y si aguanta donde
debe (mano en la cara, «el grito», cara tapada).

Con ``--serie`` imprime además cada entrada y salida del gesto con su hora, para
mirar una grabación concreta contra lo que el operador recuerda haber hecho.

Uso::

    python tools/analizar_mano_cara.py verificacion_2026-10-11.csv
    python tools/analizar_mano_cara.py p7_final_brazos.csv --serie
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from telmeow_teleop.mano_cara import (  # noqa: E402
    T_ENTRADA,
    T_SALIDA,
    DetectorManoCara,
    senales_mano_cara,
)

#: Visibilidad mínima de la mano para fiarse de ella (``ArmConfig.min_visibility``).
VIS_MIN = 0.5


def _num(fila: dict, campo: str) -> float | None:
    try:
        return float(fila[campo])
    except (KeyError, TypeError, ValueError):
        return None


def muestra_de_fila(fila: dict, lado: str) -> tuple[tuple[float, float] | None, bool]:
    """``(muestra, de_fiar)`` de un brazo en una fila del CSV de brazos."""
    otro = "R" if lado == "L" else "L"
    valores = [_num(fila, c) for c in (
        f"img_mano_x_{lado}", f"img_mano_y_{lado}",
        f"img_hombro_x_{lado}", f"img_hombro_y_{lado}",
        f"img_hombro_x_{otro}", f"img_hombro_y_{otro}",
    )]
    if None in valores:
        return None, False
    muestra = senales_mano_cara(tuple(valores[0:2]), tuple(valores[2:4]), tuple(valores[4:6]))
    confianza = _num(fila, f"confianza_mano_{lado}")
    visibilidad = _num(fila, f"vis_destino_{lado}")
    de_fiar = confianza is not None and confianza >= 0.999 and (visibilidad or 0.0) >= VIS_MIN
    return muestra, de_fiar


def reproducir_ambos(filas: list[dict], **kwargs) -> dict[str, list[bool]]:
    """Si el gesto está reconocido en cada fila, para los dos brazos a la vez:
    desde el P7.7 cada detector mira si el otro sigue en la cara (una mano
    perdida aguanta más si la otra sigue), como en ``ArmTracker``, que mira
    primero el izquierdo y luego el derecho."""
    detectores = {lado: DetectorManoCara(**kwargs) for lado in "LR"}
    salida: dict[str, list[bool]] = {"L": [], "R": []}
    for fila in filas:
        for lado, otro in (("L", "R"), ("R", "L")):
            muestra, de_fiar = muestra_de_fila(fila, lado)
            salida[lado].append(detectores[lado].actualizar(
                float(fila["t"]), muestra, de_fiar, otra_en_la_cara=detectores[otro].activo))
    return salida


def reproducir(filas: list[dict], lado: str, **kwargs) -> list[bool]:
    """Si el gesto está reconocido en cada fila, para un brazo."""
    return reproducir_ambos(filas, **kwargs)[lado]


def por_etiqueta(filas: list[dict], activo: list[bool]) -> dict[int, tuple[float, int]]:
    """``{etiqueta: (% del tiempo activo, veces que empezó)}``."""
    grupos: dict[int, list[bool]] = {}
    empezo: dict[int, int] = {}
    anterior = False
    for fila, a in zip(filas, activo):
        e = int(fila["etiqueta"])
        grupos.setdefault(e, []).append(a)
        if a and not anterior:
            empezo[e] = empezo.get(e, 0) + 1
        anterior = a
    return {e: (100.0 * sum(v) / len(v), empezo.get(e, 0)) for e, v in grupos.items()}


def informe(filas: list[dict], serie: bool = False, **kwargs) -> str:
    lineas = []
    activos = {lado: reproducir(filas, lado, **kwargs) for lado in "LR"}
    resumen = {lado: por_etiqueta(filas, activos[lado]) for lado in "LR"}
    lineas.append("etiqueta |  L: %, veces  |  R: %, veces")
    for e in sorted(resumen["L"]):
        pl, nl = resumen["L"][e]
        pr, nr = resumen["R"][e]
        lineas.append(f"  {e:6d} | {pl:5.1f} {nl:3d}     | {pr:5.1f} {nr:3d}")
    if serie:
        for lado in "LR":
            lineas.append(f"\nBrazo {lado}: entradas y salidas")
            anterior = False
            for fila, a in zip(filas, activos[lado]):
                if a != anterior:
                    lineas.append(f"  {float(fila['t']):7.1f} s  etiqueta {fila['etiqueta']:>2}  "
                                  f"{'entra' if a else 'sale'}")
                anterior = a
    return "\n".join(lineas)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("csv", type=Path, nargs="+")
    p.add_argument("--serie", action="store_true")
    p.add_argument("--t-entrada", type=float, default=T_ENTRADA)
    p.add_argument("--t-salida", type=float, default=T_SALIDA)
    a = p.parse_args(argv)
    for ruta in a.csv:
        with open(ruta, encoding="utf-8", newline="") as f:
            filas = list(csv.DictReader(f))
        print(f"\n== {ruta.name} ({len(filas)} filas, {float(filas[-1]['t']) - float(filas[0]['t']):.0f} s)")
        print(informe(filas, a.serie, t_entrada=a.t_entrada, t_salida=a.t_salida))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
