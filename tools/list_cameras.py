"""Enumera las cámaras disponibles y lo que cada una es capaz de dar.

Sirve para averiguar qué índice corresponde a cada cámara —la integrada del
portátil suele ser la 0 y una USB externa la 1— y qué resoluciones acepta cada
una, antes de fijarlas en la configuración.

Uso::

    python tools/list_cameras.py
    python tools/list_cameras.py --max-index 8 --backend msmf

Después, para usar la que interese::

    $env:TELMEOW_CAMERA_INDEX="1"
    $env:TELMEOW_CAMERA_WIDTH="1280"; $env:TELMEOW_CAMERA_HEIGHT="720"
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Silencia la cascada de avisos de los backends de vídeo de Windows al probar
# índices que no existen. Debe hacerse ANTES de importar cv2.
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

import cv2  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telmeow_teleop.config import AppConfig  # noqa: E402

BACKENDS = {"dshow": cv2.CAP_DSHOW, "msmf": cv2.CAP_MSMF, "any": cv2.CAP_ANY}

#: Resoluciones a tantear, de mayor a menor. OpenCV no informa de las que
#: soporta una cámara: hay que pedirlas y comprobar qué devuelve de verdad.
RESOLUCIONES = ((1920, 1080), (1280, 720), (960, 540), (640, 480), (320, 240))


def probar(indice: int, backend: int) -> dict | None:
    """Abre una cámara y averigua qué resoluciones acepta realmente."""
    cap = cv2.VideoCapture(indice, backend)
    if not cap.isOpened():
        cap.release()
        return None

    ok, frame = cap.read()
    if not ok or frame is None:
        cap.release()
        return None

    nativa = (
        int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
    )
    fps = cap.get(cv2.CAP_PROP_FPS)

    admitidas = []
    for ancho, alto in RESOLUCIONES:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, ancho)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, alto)
        real = (
            int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        )
        # Se apunta lo que la cámara DEVUELVE, no lo que se le pidió: pedir
        # 1920x1080 a una cámara que no llega devuelve otra cosa sin avisar.
        if real == (ancho, alto) and real not in admitidas:
            admitidas.append(real)

    cap.release()
    return {"nativa": nativa, "fps": fps, "admitidas": admitidas}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="list_cameras",
        description="Enumera las cámaras disponibles y sus resoluciones.",
    )
    parser.add_argument("--max-index", type=int, default=5, help="Último índice a probar.")
    parser.add_argument(
        "--backend", choices=tuple(BACKENDS), default=None,
        help="Backend de captura. Por defecto, el de la configuración.",
    )
    args = parser.parse_args(argv)

    cfg = AppConfig.from_env()
    nombre_backend = args.backend or cfg.camera.backend
    backend = BACKENDS[nombre_backend]

    print(f"Buscando cámaras con el backend '{nombre_backend}'…\n")
    encontradas = 0
    for indice in range(args.max_index + 1):
        info = probar(indice, backend)
        if info is None:
            continue
        encontradas += 1
        marca = "  <- la configurada" if indice == cfg.camera.index else ""
        print(f"[{indice}] resolución por defecto {info['nativa'][0]}x{info['nativa'][1]}"
              f"   {info['fps']:.0f} fps{marca}")
        if info["admitidas"]:
            lista = ", ".join(f"{w}x{h}" for w, h in info["admitidas"])
            print(f"     admite: {lista}")
        print()

    if not encontradas:
        print("No se encontró ninguna cámara. Si acabas de conectarla, prueba a\n"
              "cerrar las aplicaciones que puedan tenerla ocupada (Teams, Zoom,\n"
              "la app Cámara de Windows) y vuelve a ejecutar esto.")
        return 1

    print("Para usar una en concreto, con mayor resolución:\n")
    print('    $env:TELMEOW_CAMERA_INDEX="1"')
    print('    $env:TELMEOW_CAMERA_WIDTH="1280"; $env:TELMEOW_CAMERA_HEIGHT="720"')
    print("    .venv\\Scripts\\python.exe -m telmeow_teleop.head_tracker")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
