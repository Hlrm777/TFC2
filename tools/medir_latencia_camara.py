"""Latencia de la cámara: cuánto tarda un cambio de la pantalla en llegar a un frame (P7.3).

El campo ``t`` del pipeline mide desde que ``cap.read()`` entrega el frame hasta
que el mensaje llega (``tools/mqtt_monitor.py --latencia``). Lo que pasa ANTES
—exposición, transferencia por USB, el búfer del driver— no lo ve. Esto lo
mide aparte, con la cámara integrada y sin nada más que la pantalla:

1. La pantalla se pone entera en negro o en blanco a horas conocidas (con algo
   de azar en el periodo, para no sincronizarse con la cámara).
2. La luz de la pantalla sobre la cara del operador y la pared cambia el
   brillo medio de lo que ve la cámara.
3. Por cada cambio, el primer frame cuyo brillo cruza la mitad entre el nivel
   de antes y el de después marca cuándo llegó. La latencia es la hora de
   lectura de ese frame menos la hora del cambio.

Mide **pantalla + cámara juntas**: incluye lo que tarda el monitor en pintar
(hasta un refresco, ~17 ms a 60 Hz, más la respuesta del panel). Es una cota
superior de la latencia de la cámara, y así se declara. Funciona mejor con la
sala en penumbra: el destello se nota más.

Uso::

    python tools/medir_latencia_camara.py --csv latencia_camara.csv
    python tools/medir_latencia_camara.py --segundos 60 --ventana

``q`` sale antes de tiempo. La cámara se abre con la configuración de siempre
(``TELMEOW_CAMERA_*``), la misma con la que corre el pipeline.
"""

from __future__ import annotations

import argparse
import csv
import random
import statistics
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telmeow_teleop.camera import abrir_camara, brillo_medio, describir  # noqa: E402
from telmeow_teleop.config import AppConfig  # noqa: E402

#: Ventana antes del cambio con la que se toma el nivel de partida, en s.
ANTES_S = 0.3

#: Ventana después del cambio con la que se toma el nivel de llegada, en s. Deja
#: medio segundo para que la transición haya acabado.
DESPUES_S = (0.5, 0.9)

#: Contraste mínimo entre los dos niveles, en niveles de gris (0-255). Por
#: debajo, el destello no se distingue del ruido y ese cambio no se cuenta.
CONTRASTE_MINIMO = 3.0

VENTANA = "Telmeow - latencia de la camara"


def latencias_de_destellos(frames: list[tuple[float, float]], cambios: list[tuple[float, int]],
                           contraste_minimo: float = CONTRASTE_MINIMO) -> list[float]:
    """Latencia de cada cambio de pantalla, en ms.

    ``frames``: ``(hora de lectura, brillo)``, en orden. ``cambios``: ``(hora,
    nivel)`` con nivel 0 = negro y 1 = blanco. Las horas, del mismo reloj
    (``perf_counter``). Los cambios sin contraste suficiente o sin frames
    alrededor se descartan.
    """
    tiempos = np.array([t for t, _ in frames], dtype=float)
    brillos = np.array([b for _, b in frames], dtype=float)
    resultado = []
    for hora, _nivel in cambios:
        antes = brillos[(tiempos >= hora - ANTES_S) & (tiempos < hora)]
        despues = brillos[(tiempos >= hora + DESPUES_S[0]) & (tiempos <= hora + DESPUES_S[1])]
        if not len(antes) or not len(despues):
            continue
        a, d = float(np.median(antes)), float(np.median(despues))
        if abs(d - a) < contraste_minimo:
            continue
        umbral = (a + d) / 2.0
        posteriores = np.nonzero(tiempos > hora)[0]
        for i in posteriores:
            if tiempos[i] > hora + DESPUES_S[1]:
                break
            if (brillos[i] >= umbral) if d > a else (brillos[i] <= umbral):
                resultado.append(1000.0 * (tiempos[i] - hora))
                break
    return resultado


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="medir_latencia_camara",
        description="Latencia pantalla + camara, con destellos de la pantalla (P7.3).",
    )
    parser.add_argument("--segundos", type=float, default=30.0, help="Duracion de la medida.")
    parser.add_argument("--periodo", type=float, default=1.0,
                        help="Segundos entre cambios, de media (con +-20 %% al azar).")
    parser.add_argument("--csv", type=Path, help="Graba los frames y los cambios.")
    parser.add_argument("--ventana", action="store_true",
                        help="Ventana normal en vez de pantalla completa (para probar).")
    args = parser.parse_args(argv)

    import cv2

    cfg = AppConfig.from_env()
    cap = abrir_camara(cfg.camera)
    if cap is None:
        print(f"No se pudo abrir la camara {cfg.camera.index}. Prueba 'python tools/list_cameras.py'.")
        return 1
    print(describir(cap, cfg.camera))
    print("Mira la pantalla sin moverte. q sale antes de tiempo.\n")

    cv2.namedWindow(VENTANA, cv2.WINDOW_NORMAL)
    if not args.ventana:
        cv2.setWindowProperty(VENTANA, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    negro = np.zeros((360, 640, 3), dtype=np.uint8)
    blanco = np.full((360, 640, 3), 255, dtype=np.uint8)

    frames: list[tuple[float, float]] = []
    cambios: list[tuple[float, int]] = []
    azar = random.Random(7)
    nivel = 0
    cv2.imshow(VENTANA, negro)
    cv2.waitKey(1)
    inicio = time.perf_counter()
    siguiente = inicio + 1.5          # para que la cámara se asiente en negro
    try:
        while time.perf_counter() - inicio < args.segundos:
            ahora = time.perf_counter()
            if ahora >= siguiente:
                nivel = 1 - nivel
                cv2.imshow(VENTANA, blanco if nivel else negro)
                cv2.waitKey(1)
                cambios.append((time.perf_counter(), nivel))
                siguiente = time.perf_counter() + args.periodo * azar.uniform(0.8, 1.2)
            ok, frame = cap.read()
            if not ok:
                break
            frames.append((time.perf_counter(), brillo_medio(frame[::4, ::4])))
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()

    if args.csv is not None:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            escritor = csv.writer(f)
            escritor.writerow(["tipo", "t", "valor"])
            escritor.writerows(("frame", f"{t:.5f}", f"{b:.2f}") for t, b in frames)
            escritor.writerows(("cambio", f"{t:.5f}", n) for t, n in cambios)
        print(f"Grabado en {args.csv}")

    latencias = latencias_de_destellos(frames, cambios)
    print(f"{len(frames)} frames, {len(cambios)} cambios, {len(latencias)} medidos.")
    if not latencias:
        print("Ningun cambio con contraste suficiente: prueba con la sala en penumbra.")
        return 1
    ordenadas = sorted(latencias)
    p95 = ordenadas[min(len(ordenadas) - 1, int(round(0.95 * (len(ordenadas) - 1))))]
    print(f"Latencia pantalla + camara: mediana {statistics.median(ordenadas):.0f} ms, "
          f"p95 {p95:.0f}, min {ordenadas[0]:.0f}, max {ordenadas[-1]:.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
