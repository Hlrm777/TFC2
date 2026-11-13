"""Reproduce sobre una sesión grabada la compresión del cabeceo hacia abajo
(P10.2, H72).

Con la ganancia de 1.80 el robot toca su tope de cabeceo (−39°) cuando el
operador baja 21°. Al narrar, el operador baja hasta 37° para leer el pie de la
página, y en la sesión del 2026-11-12 el robot estuvo 14.9 s parado en el tope.
La compresión deja la ganancia igual hasta ``tilt_down_recto`` grados y la baja
de forma continua para que el tope llegue a ``tilt_down_hasta``
(``head_tracker.comprimir_tilt_abajo``).

Usa el cabeceo crudo de cada fila del CSV de cabeza (``raw_tilt``, ya respecto
al neutro) y lo pasa por el mapeo lineal y por el comprimido, sin el filtro ni
la zona muerta, que van después y son los mismos en los dos casos. Da el
tiempo en el tope con cada uno y la tabla de la curva.

**Criterios, fijados el 2026-11-13** (después de ver la sesión, que es la que
destapó el problema; por eso se ve además en vivo):

- el tiempo en el tope baja al menos un ``BAJADA_TOPE`` %;
- las filas con el operador a menos de ``tilt_down_recto`` grados abajo, o
  mirando arriba, salen idénticas.

Uso::

    python tools/simular_compresion_tilt.py                    # p10_narracion
    python tools/simular_compresion_tilt.py p10a
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telmeow_teleop.config import PROJECT_ROOT, AppConfig  # noqa: E402
from telmeow_teleop.head_tracker import comprimir_tilt_abajo  # noqa: E402
from telmeow_teleop.limits import HEAD_TILT_RANGE  # noqa: E402

#: Criterio, fijado el 2026-11-13.
BAJADA_TOPE = 90.0

#: Una fila cuenta como «en el tope» a menos de esto del límite, en grados.
MARGEN_TOPE = 0.01

#: Si entre dos filas pasa más que esto, la cara se perdió y el hueco no cuenta.
HUECO_S = 0.25


def leer(ruta: Path) -> list[tuple[float, float]]:
    """``(t, raw_tilt)`` de cada fila con cabeceo."""
    with open(ruta, encoding="utf-8", newline="") as f:
        filas = [(float(x["t"]), float(x["raw_tilt"])) for x in csv.DictReader(f) if x["raw_tilt"]]
    return [(t, v) for t, v in filas if v == v]


def mapear(crudo: float, ganancia_arriba: float, ganancia_abajo: float,
           recto: float, hasta: float) -> float:
    """El cabeceo del robot para ``crudo``, recortado a su rango; sin filtro."""
    minimo, maximo = HEAD_TILT_RANGE
    if crudo >= 0.0:
        return min(maximo, crudo * ganancia_arriba)
    return max(minimo, -comprimir_tilt_abajo(-crudo, ganancia_abajo, -minimo, recto, hasta))


def segundos_en_tope(tiempos: list[float], cabeceos: list[float]) -> float:
    """Segundos con el cabeceo en el tope de abajo: cada fila en el tope aporta
    el tiempo hasta la siguiente (los huecos sin cara no cuentan)."""
    tope = HEAD_TILT_RANGE[0] + MARGEN_TOPE
    total = 0.0
    for i in range(len(tiempos) - 1):
        dt = tiempos[i + 1] - tiempos[i]
        if cabeceos[i] <= tope and dt <= HUECO_S:
            total += dt
    return total


@dataclass(frozen=True)
class Resultado:
    tope_lineal_s: float
    tope_comprimido_s: float
    max_diferencia_recta: float

    @property
    def bajada(self) -> float | None:
        if self.tope_lineal_s <= 0.0:
            return None
        return 100.0 * (1.0 - self.tope_comprimido_s / self.tope_lineal_s)

    @property
    def cumple(self) -> bool:
        return (self.bajada is not None and self.bajada >= BAJADA_TOPE
                and self.max_diferencia_recta < 1e-9)


def simular(filas: list[tuple[float, float]], ganancia_arriba: float, ganancia_abajo: float,
            recto: float, hasta: float) -> Resultado:
    tiempos = [t for t, _ in filas]
    lineal = [mapear(v, ganancia_arriba, ganancia_abajo, 0.0, 0.0) for _, v in filas]
    comprimido = [mapear(v, ganancia_arriba, ganancia_abajo, recto, hasta) for _, v in filas]
    rectas = [abs(a - b) for (_, v), a, b in zip(filas, lineal, comprimido) if v >= -recto]
    return Resultado(segundos_en_tope(tiempos, lineal), segundos_en_tope(tiempos, comprimido),
                     max(rectas, default=0.0))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("sesion", nargs="?", default="p10_narracion")
    a = p.parse_args(argv)

    h = AppConfig.from_env().head
    ruta = PROJECT_ROOT / f"{a.sesion}_cabeza.csv"
    if not ruta.exists():
        print(f"falta {ruta.name}")
        return 1
    filas = leer(ruta)
    r = simular(filas, h.gain_tilt_up, h.gain_tilt_down, h.tilt_down_recto, h.tilt_down_hasta)

    print(f"{a.sesion}: ganancia abajo {h.gain_tilt_down:g}, recta hasta {h.tilt_down_recto:g} grados, "
          f"tope a los {h.tilt_down_hasta:g}")
    print("\n  el operador baja   el robot, lineal   el robot, comprimido   pendiente")
    for grados in (2, 5, 8, 10, 15, 20, 25, 30, 35, 37, 40):
        antes = -mapear(-grados, h.gain_tilt_up, h.gain_tilt_down, 0.0, 0.0)
        ahora = -mapear(-grados, h.gain_tilt_up, h.gain_tilt_down, h.tilt_down_recto, h.tilt_down_hasta)
        delta = (comprimir_tilt_abajo(grados + 0.01, h.gain_tilt_down, -HEAD_TILT_RANGE[0],
                                      h.tilt_down_recto, h.tilt_down_hasta)
                 - comprimir_tilt_abajo(grados - 0.01, h.gain_tilt_down, -HEAD_TILT_RANGE[0],
                                        h.tilt_down_recto, h.tilt_down_hasta)) / 0.02
        print(f"  {grados:14d}   {antes:14.1f}   {ahora:18.1f}   {delta:9.2f}")

    print(f"\n  segundos con el robot parado en el tope: {r.tope_lineal_s:.1f} lineal, "
          f"{r.tope_comprimido_s:.1f} comprimido")
    print("\nCriterios (2026-11-13):")
    if r.bajada is None:
        print("  la sesion no llega al tope con el mapeo lineal: no se puede decidir")
    else:
        print(f"  el tiempo en el tope baja un {r.bajada:.0f} % (se pide {BAJADA_TOPE:.0f}): "
              + ("OK" if r.bajada >= BAJADA_TOPE else "NO CUMPLE"))
    print(f"  cerca del frente y hacia arriba, la mayor diferencia es {r.max_diferencia_recta:.3g} grados: "
          + ("OK" if r.max_diferencia_recta < 1e-9 else "NO CUMPLE"))
    print("  " + ("LA COMPRESION CUMPLE" if r.cumple else "LA COMPRESION NO CUMPLE"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
