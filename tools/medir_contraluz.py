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
- Con ``--malla``, con el operador sentado y quieto delante: además, el ruido
  de la Face Mesh en cada tanda (el índice de ``head_tracker.RuidoMalla``, el
  mismo que hace crecer la zona muerta de la cabeza). Es lo que dice si apagar
  la compensación —imagen más oscura y con más grano— mete temblor en la
  cabeza del robot. La sesión del P10 lo lanza así, con ``--csv``.

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
    python tools/medir_contraluz.py --malla --csv p10_contraluz.csv
"""

from __future__ import annotations

import argparse
import csv
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
#: Con ``--malla`` se mide más rato: el ruido de la malla es una mediana.
MEDIR_MALLA_S = 8.0

#: Columnas del CSV de ``--csv``, una fila por tanda.
COLUMNAS_CSV = ("vuelta", "contraluz", "fps", "dt_p95_ms", "brillo", "ruido_imagen",
                "ruido_malla", "con_cara")


@dataclasses.dataclass(frozen=True)
class Medida:
    fps: float
    dt_p95_ms: float
    brillo: float
    ruido: float
    #: Con ``--malla``: el índice de ruido de la Face Mesh (``None`` si no vio
    #: bastantes caras) y la fracción de fotos con cara.
    ruido_malla: float | None = None
    con_cara: float | None = None


def ruido_de_malla(tiempos: list[float], crudos: list[tuple[float, float] | None]) -> float | None:
    """El índice de ``RuidoMalla`` sobre toda la tanda: los ángulos crudos de cada
    foto, ``None`` sin cara (la segunda diferencia no se lo salta)."""
    from telmeow_teleop.head_tracker import RuidoMalla

    ruido = RuidoMalla(ventana_s=float("inf"))
    for t, crudo in zip(tiempos, crudos):
        if crudo is None:
            ruido.cortar()
        else:
            ruido.actualizar(t, crudo)
    return ruido.sigma


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


def medir(cap, calentar: float = CALENTAR_S, segundos: float = MEDIR_S, cabeza=None) -> Medida:
    """Una tanda. Con ``cabeza`` (un ``HeadTracker``), además el ruido de la malla."""
    fin = time.perf_counter() + calentar
    while time.perf_counter() < fin:
        ok, frame = cap.read()
        if ok and cabeza is not None:
            # Que la malla ya esté siguiendo la cara cuando se empiece a medir.
            cabeza.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    tiempos, brillos, ruidos, previo = [], [], [], None
    crudos: list[tuple[float, float] | None] = []
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
        if cabeza is not None:
            pose = cabeza.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            crudos.append(cabeza.raw_angles if pose is not None else None)
    medida = resumir(tiempos, brillos, ruidos)
    if cabeza is None:
        return medida
    return dataclasses.replace(
        medida, ruido_malla=ruido_de_malla(tiempos, crudos),
        con_cara=sum(c is not None for c in crudos) / len(crudos) if crudos else 0.0)


def _linea(nombre: str, m: Medida, extra: str = "") -> str:
    malla = ""
    if m.con_cara is not None:
        malla = ("   malla " + ("sin cara" if m.ruido_malla is None else f"{m.ruido_malla:4.2f}")
                 + f" (cara en el {100 * m.con_cara:.0f} %)")
    return (f"{nombre:22s} {m.fps:5.1f} fps   p95 {m.dt_p95_ms:6.1f} ms   brillo {m.brillo:6.1f}   "
            f"ruido {m.ruido:4.2f}{malla}{extra}")


def fila_csv(vuelta: int, contraluz: int, m: Medida) -> list:
    return [vuelta, contraluz, f"{m.fps:.2f}", f"{m.dt_p95_ms:.1f}", f"{m.brillo:.1f}", f"{m.ruido:.3f}",
            "" if m.ruido_malla is None else f"{m.ruido_malla:.3f}",
            "" if m.con_cara is None else f"{m.con_cara:.3f}"]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--vueltas", type=int, default=3)
    p.add_argument("--exposicion", action="store_true",
                   help="ademas, el barrido de la exposicion manual de -8 a 0")
    p.add_argument("--malla", action="store_true",
                   help="con el operador sentado y quieto: ademas, el ruido de la Face Mesh")
    p.add_argument("--csv", type=Path, help="guarda una fila por tanda")
    a = p.parse_args(argv)

    # backlight -1: que abrir la cámara no toque lo que se va a medir.
    cfg = dataclasses.replace(AppConfig.from_env().camera, backlight=-1)
    cap = abrir_camara(cfg)
    if cap is None:
        print("No se pudo abrir la camara: ¿la tiene otro programa?")
        return 1
    original = cap.get(cv2.CAP_PROP_BACKLIGHT)
    cap.release()
    print(f"Compensacion de contraluz de partida: {original:g}")
    cabeza = None
    if a.malla:
        from telmeow_teleop.head_tracker import HeadTracker

        cabeza = HeadTracker.desde_config(AppConfig.from_env())
        print("Quieto y mirando al centro de la pantalla: "
              f"{2 * a.vueltas} tandas de {CALENTAR_S + MEDIR_MALLA_S:.0f} s.", flush=True)
    filas = []
    try:
        for vuelta in range(a.vueltas):
            for valor in (1, 0):
                time.sleep(0.4)
                cap = abrir_camara(cfg)
                cap.set(cv2.CAP_PROP_BACKLIGHT, valor)
                m = medir(cap, segundos=MEDIR_MALLA_S if a.malla else MEDIR_S, cabeza=cabeza)
                filas.append(fila_csv(vuelta + 1, valor, m))
                print(_linea(f"vuelta {vuelta + 1}, contraluz {valor}", m,
                             f"   (leido {cap.get(cv2.CAP_PROP_BACKLIGHT):g})"), flush=True)
                cap.release()
        if a.csv:
            with open(a.csv, "w", newline="", encoding="utf-8") as f:
                escritor = csv.writer(f, lineterminator="\n")
                escritor.writerow(COLUMNAS_CSV)
                escritor.writerows(filas)
            print(f"Guardado en {a.csv}")
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
        if cabeza is not None:
            cabeza.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
