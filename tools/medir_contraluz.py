"""Mide qué ajuste de la cámara la deja a 15 fotos por segundo (H71).

En la sesión p10 la cámara integrada pasó sola de 30 a 15 fotos por segundo y no
volvió. Esta herramienta lo reproduce sin nadie delante, con la cámara abierta
como la abre el pipeline (``TELMEOW_CAMERA_*``):

- Por defecto, un A/B de la **compensación de contraluz** del driver
  (``CAP_PROP_BACKLIGHT``): varias vueltas alternando 1 (el valor de fábrica) y
  0, con la exposición automática. Por vuelta da los fps reales, el intervalo
  entre fotos (p95), el brillo medio y el ruido entre fotos seguidas (mediana de
  la diferencia absoluta, en niveles de gris).
- Con ``--exposicion``, además, un barrido de la exposición manual de −8 a 0,
  para saber si fijarla a mano sirve.

Medido el 2026-11-10 con la HP Wide Vision HD, de día y sin luz añadida: con la
compensación en 1, 15.0 fps y brillo 144-152; en 0, 30.0 fps y brillo 111-116,
las tres vueltas. La exposición manual solo da 30 fps de −4 hacia abajo, con la
imagen oscura (brillo de 18 a 52 sobre 255 a −4, según la tanda), así que
fijarla a mano no sirve.

Los ajustes de DirectShow viven en el driver (H34): al salir se restauran la
compensación que había y la exposición automática.

Uso::

    python tools/medir_contraluz.py
    python tools/medir_contraluz.py --vueltas 5 --exposicion
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from telmeow_teleop.camera import AUTO_EXPOSURE_ON, abrir_camara  # noqa: E402
from telmeow_teleop.config import AppConfig  # noqa: E402

#: Segundos que se dejan pasar tras abrir (la exposición automática tarda en
#: asentarse) y segundos que se miden.
CALENTAR_S = 3.0
MEDIR_S = 5.0


@dataclasses.dataclass(frozen=True)
class Medida:
    fps: float
    dt_p95_ms: float
    brillo: float
    ruido: float


def resumir(tiempos: list[float], brillos: list[float], ruidos: list[float]) -> Medida:
    """Las cifras de una tanda: fps reales entre la primera y la última foto,
    p95 del intervalo, brillo medio y ruido medio entre fotos seguidas."""
    if len(tiempos) < 2:
        raise ValueError("hacen falta al menos dos fotos para medir")
    dt = np.diff(tiempos) * 1000.0
    return Medida(
        fps=(len(tiempos) - 1) / (tiempos[-1] - tiempos[0]),
        dt_p95_ms=float(np.percentile(dt, 95)),
        brillo=float(np.mean(brillos)),
        ruido=float(np.mean(ruidos)) if ruidos else 0.0,
    )


def medir(cap, calentar: float = CALENTAR_S, segundos: float = MEDIR_S) -> Medida:
    fin = time.perf_counter() + calentar
    while time.perf_counter() < fin:
        cap.read()
    tiempos, brillos, ruidos, previo = [], [], [], None
    fin = time.perf_counter() + segundos
    while time.perf_counter() < fin:
        ok, frame = cap.read()
        if not ok:
            continue
        tiempos.append(time.perf_counter())
        gris = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
        brillos.append(float(gris.mean()))
        if previo is not None:
            ruidos.append(float(np.median(np.abs(gris - previo))))
        previo = gris
    return resumir(tiempos, brillos, ruidos)


def _linea(nombre: str, m: Medida, extra: str = "") -> str:
    return (f"{nombre:22s} {m.fps:5.1f} fps   p95 {m.dt_p95_ms:6.1f} ms   brillo {m.brillo:6.1f}   "
            f"ruido {m.ruido:4.2f}{extra}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--vueltas", type=int, default=3)
    p.add_argument("--exposicion", action="store_true",
                   help="ademas, el barrido de la exposicion manual de -8 a 0")
    a = p.parse_args(argv)

    cfg = AppConfig.from_env().camera
    cap = abrir_camara(cfg)
    if cap is None:
        print("No se pudo abrir la camara: ¿la tiene otro programa?")
        return 1
    original = cap.get(cv2.CAP_PROP_BACKLIGHT)
    cap.release()
    print(f"Compensacion de contraluz de partida: {original:g}")
    try:
        for vuelta in range(a.vueltas):
            for valor in (1, 0):
                time.sleep(0.4)
                cap = abrir_camara(cfg)
                cap.set(cv2.CAP_PROP_BACKLIGHT, valor)
                m = medir(cap)
                print(_linea(f"vuelta {vuelta + 1}, contraluz {valor}", m,
                             f"   (leido {cap.get(cv2.CAP_PROP_BACKLIGHT):g})"), flush=True)
                cap.release()
        if a.exposicion:
            print("\nExposicion manual, con la compensacion de partida:")
            for e in range(-8, 1):
                time.sleep(0.4)
                cap = abrir_camara(dataclasses.replace(cfg, auto_exposure=False, exposure=float(e)))
                cap.set(cv2.CAP_PROP_BACKLIGHT, original)
                m = medir(cap, calentar=2.0, segundos=4.0)
                print(_linea(f"manual {e:+d}", m, f"   (leido {cap.get(cv2.CAP_PROP_EXPOSURE):g})"),
                      flush=True)
                cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, AUTO_EXPOSURE_ON)
                cap.release()
    finally:
        time.sleep(0.4)
        cap = abrir_camara(cfg)
        if cap is not None:
            cap.set(cv2.CAP_PROP_BACKLIGHT, original)
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, AUTO_EXPOSURE_ON)
            print(f"\nRestaurado: contraluz {cap.get(cv2.CAP_PROP_BACKLIGHT):g}, exposicion automatica.")
            cap.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
