"""Coste de BlazePose por frame con cada ``model_complexity`` (0, 1 y 2).

Por qué
-------
En el P7, BlazePose comparte el bucle con Face Mesh (5-7 ms por frame, medido en
el P4) y con el clasificador de emociones: el presupuesto de tiempo es uno
solo. ``model_complexity`` es la palanca de precisión de BlazePose —0 ligero, 1
el de por defecto, 2 pesado— y se paga en milisegundos, así que hay que medirlo
antes de elegir.

Cómo
----
Captura unos segundos de cámara, con el operador en su postura de narración, y
pasa **los mismos frames** por los tres modelos, en modo vídeo y sin el
suavizado de MediaPipe, igual que el arm tracker. Así la comparación es sobre el
mismo contenido y no depende de la cámara ni de la luz del momento. Mide el
tiempo de ``pose.process()`` con ``perf_counter``, y de paso en cuántos frames
hubo persona y con qué visibilidad se vieron hombros y muñecas: un modelo que
mide rápido sobre una silla vacía no dice nada.

Uso, con la cámara de ``TELMEOW_CAMERA_*`` como el arm tracker::

    python tools/medir_blazepose.py
    python tools/medir_blazepose.py --segundos 10 --complejidades 0 1 2
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: Lo que costaba Face Mesh por frame en el P4, para ponerlo al lado.
FACE_MESH_MS = "5-7"

#: Índices de BlazePose: hombros y muñecas, para saber si había alguien.
_HOMBROS = (11, 12)
_MUNECAS = (15, 16)


def _percentil(valores: list[float], p: float) -> float:
    ordenados = sorted(valores)
    k = (len(ordenados) - 1) * p / 100.0
    i = int(k)
    if i + 1 >= len(ordenados):
        return ordenados[-1]
    return ordenados[i] + (ordenados[i + 1] - ordenados[i]) * (k - i)


def capturar(cfg, segundos: float, descartar: float = 1.5):
    """Frames RGB de la cámara durante ``segundos``, tras descartar los primeros
    ``descartar`` segundos mientras la autoexposición converge. Devuelve
    ``(frames, fps)`` o ``None`` si la cámara no abre."""
    import cv2

    from telmeow_teleop.camera import abrir_camara

    cap = abrir_camara(cfg.camera)
    if cap is None:
        return None
    frames = []
    try:
        inicio = time.perf_counter()
        while time.perf_counter() - inicio < descartar:
            cap.read()
        inicio = time.perf_counter()
        while time.perf_counter() - inicio < segundos:
            ok, frame = cap.read()
            if not ok:
                break
            if cfg.camera.flip:
                frame = cv2.flip(frame, 1)
            frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        duracion = time.perf_counter() - inicio
    finally:
        cap.release()
    return frames, len(frames) / duracion if duracion > 0 else 0.0


def crear_pose(complejidad: int):
    """BlazePose con los mismos ajustes que el arm tracker."""
    import mediapipe as mp

    return mp.solutions.pose.Pose(
        static_image_mode=False,
        model_complexity=complejidad,
        smooth_landmarks=False,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )


def medir(frames, complejidad: int, repeticiones: int = 2, calentamiento: int = 10,
          crear=crear_pose, reloj=time.perf_counter) -> dict:
    """Pasa ``frames`` ``repeticiones`` veces por un BlazePose de esa
    complejidad. Los ``calentamiento`` primeros no se cuentan: incluyen cargar
    el modelo y la primera detección."""
    pose = crear(complejidad)
    tiempos, con_persona, vis_hombros, vis_munecas = [], 0, [], []
    try:
        for frame in frames[:calentamiento]:
            pose.process(frame)
        for _ in range(repeticiones):
            for frame in frames:
                t0 = reloj()
                resultado = pose.process(frame)
                tiempos.append(1000.0 * (reloj() - t0))
                puntos = getattr(getattr(resultado, "pose_landmarks", None), "landmark", None)
                if puntos:
                    con_persona += 1
                    vis_hombros.append(min(puntos[i].visibility for i in _HOMBROS))
                    vis_munecas.append(max(puntos[i].visibility for i in _MUNECAS))
    finally:
        pose.close()
    total = max(1, len(tiempos))
    return {
        "complejidad": complejidad,
        "frames": len(tiempos),
        "ms_mediana": statistics.median(tiempos) if tiempos else float("nan"),
        "ms_p95": _percentil(tiempos, 95) if tiempos else float("nan"),
        "con_persona": 100.0 * con_persona / total,
        "vis_hombros": statistics.median(vis_hombros) if vis_hombros else float("nan"),
        "vis_munecas": statistics.median(vis_munecas) if vis_munecas else float("nan"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--segundos", type=float, default=8.0, help="cuánto capturar")
    parser.add_argument("--complejidades", type=int, nargs="+", default=[0, 1, 2])
    parser.add_argument("--repeticiones", type=int, default=2)
    args = parser.parse_args(argv)

    from telmeow_teleop import configure_video_backend
    from telmeow_teleop.config import AppConfig

    configure_video_backend()
    cfg = AppConfig.from_env()
    print(
        f"Capturando {args.segundos:g} s de la camara {cfg.camera.index}"
        f" ({cfg.camera.width}x{cfg.camera.height}). Sientate como para narrar,"
        " con los brazos a la vista, y muevelos un poco."
    )
    capturado = capturar(cfg, args.segundos)
    if capturado is None:
        print(f"No se pudo abrir la camara {cfg.camera.index} (tools/list_cameras.py).")
        return 1
    frames, fps = capturado
    if not frames:
        print("La camara no dio frames.")
        return 1
    print(f"{len(frames)} frames a {fps:.1f} fps. Midiendo (puede tardar un minuto)...\n")

    resultados = [medir(frames, c, args.repeticiones) for c in args.complejidades]
    print("  complejidad | ms por frame: mediana   p95 | frames con persona | vis. hombros | vis. munecas")
    for r in resultados:
        print(
            f"  {r['complejidad']:11d} | {r['ms_mediana']:21.1f} {r['ms_p95']:5.1f}"
            f" | {r['con_persona']:17.0f}% | {r['vis_hombros']:12.2f} | {r['vis_munecas']:12.2f}"
        )
    print(
        f"\n  Para comparar: Face Mesh costaba {FACE_MESH_MS} ms (P4). A 30 fps hay 33 ms"
        " por frame para todo, y a 20 Hz de publicacion, 50 ms."
    )
    if any(r["con_persona"] < 90 for r in resultados):
        print("  OJO: en algun modelo hubo menos de un 90 % de frames con persona.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
