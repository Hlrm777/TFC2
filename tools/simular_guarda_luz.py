"""Reproduce sobre una sesión grabada la guarda de la cabeza y la vigilancia de la
luz del P7.6, con las clases de verdad del pipeline.

Del CSV de cabeza salen la pose medida y los ángulos crudos de cada frame; de
``sesion.csv``, si las manos estaban sobre la cara. En las sesiones grabadas antes
del P7.6 eso no se guardaba aparte: se toma ``cabeza_tapada``, que era la guarda
de entonces y equivale a «manos encima, con su retención» (por eso aquí la
retención va a 0). Da, por etiqueta:

- el % de frames con la guarda actuando, antes y ahora, y el salto de la cabeza
  publicada (mediana, p95 y máximo), para ver que en las mejillas la cabeza sigue
  y que tapada no salta;
- la σ de la malla, la zona muerta que se usaría y si el robot se pausaría por
  poca luz o, desde el P7.7, por haber perdido la cara sin manos encima.

Uso::

    python tools/simular_guarda_luz.py p75 p7_final
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from telmeow_teleop.config import AppConfig  # noqa: E402
from telmeow_teleop.head_tracker import RuidoMalla  # noqa: E402
from telmeow_teleop.main import (  # noqa: E402
    SIN_MANOS_RUIDO_S,
    GuardaCabeza,
    MallaInestable,
    VigiaCara,
    VigiaRuido,
    cuenta_para_ruido,
)


def _leer(ruta: Path) -> list[dict]:
    with open(ruta, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def simular(base: Path, cfg: AppConfig | None = None) -> list[dict]:
    """Una fila por frame de ``sesion.csv`` con lo que haría el pipeline."""
    cfg = cfg or AppConfig()
    sesion = _leer(base.with_suffix(".csv"))
    cabeza = {round(float(f["t"]), 4): f for f in _leer(base.with_name(base.name + "_cabeza.csv"))}
    antes = GuardaCabeza(retencion_s=0.0)
    ahora_g = GuardaCabeza(retencion_s=0.0)
    malla = MallaInestable()
    ruido = RuidoMalla()
    vigia = VigiaRuido.desde_config(cfg)
    vigia_cara = VigiaCara.desde_config(cfg)
    ultima_mano = None
    filas = []
    for f in sesion:
        t = float(f["t"])
        c = cabeza.get(round(t, 4))
        pose = (float(c["pan"]), float(c["tilt"])) if c else None
        crudo = (float(c["raw_pan"]), float(c["raw_tilt"])) if c else None
        manos = bool(int(f.get("cabeza_tapada") or 0))
        if manos:
            ultima_mano = t
        falla = malla.paso(t, crudo)
        if cuenta_para_ruido(crudo, t, ultima_mano):
            ruido.actualizar(t, crudo)
        else:
            ruido.cortar()
        zona, pausa = vigia.paso(t, ruido.sigma)
        recientes = ultima_mano is not None and t - ultima_mano < SIN_MANOS_RUIDO_S
        pausa_cara = vigia_cara.paso(t, pose is not None, recientes)
        filas.append({
            "t": t, "etiqueta": int(f["etiqueta"]), "pose": pose,
            "antes": antes.paso(t, pose, manos), "tapada_antes": antes.tapada,
            "ahora": ahora_g.paso(t, pose, manos, falla, malla.sin_cara), "tapada_ahora": ahora_g.tapada,
            "sigma": ruido.sigma, "zona": zona, "pausa": pausa, "pausa_cara": pausa_cara,
        })
    return filas


def _saltos(poses) -> list[float]:
    p = [x for x in poses if x is not None]
    return [max(abs(a[0] - b[0]), abs(a[1] - b[1])) for a, b in zip(p, p[1:])]


def _cifras(s: list[float]) -> str:
    if not s:
        return "--"
    v = sorted(s)
    return f"{statistics.median(v):.2f}/{v[int(0.95 * (len(v) - 1))]:.1f}/{v[-1]:.1f}"


def informe(filas: list[dict], nombre: str) -> str:
    lineas = [f"== {nombre}", "etiq | guarda antes / ahora | cabeza publicada antes / ahora (mediana/p95/max) "
              "| sigma p50 | zona max | pausa luz % | pausa sin cara %"]
    for e in sorted({f["etiqueta"] for f in filas}):
        g = [f for f in filas if f["etiqueta"] == e]
        sig = [f["sigma"] for f in g if f["sigma"] is not None]
        lineas.append(
            f"{e:4d} | {100 * sum(f['tapada_antes'] for f in g) / len(g):4.0f} % / "
            f"{100 * sum(f['tapada_ahora'] for f in g) / len(g):4.0f} % | "
            f"{_cifras(_saltos(f['antes'] for f in g)):>15s} / {_cifras(_saltos(f['ahora'] for f in g)):>15s} | "
            f"{statistics.median(sig) if sig else float('nan'):5.2f} | {max(f['zona'] for f in g):4.1f} | "
            f"{100 * sum(f['pausa'] for f in g) / len(g):3.0f} | "
            f"{100 * sum(f['pausa_cara'] for f in g) / len(g):3.0f}")
    return "\n".join(lineas)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("bases", nargs="+", help="nombres de sesión sin .csv (p75, p7_final)")
    a = p.parse_args(argv)
    for b in a.bases:
        print(informe(simular(PROJECT_ROOT / b), b))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
