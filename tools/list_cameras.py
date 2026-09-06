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
import time
from pathlib import Path

# Silencia la cascada de avisos de los backends de vídeo de Windows al probar
# índices que no existen. Debe hacerse ANTES de importar cv2.
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Esto tiene que ocurrir ANTES del import de cv2, o MSMF puede colgarse al abrir
# la cámara. El orden de estos imports es deliberado: no reordenar.
from telmeow_teleop import configure_video_backend  # noqa: E402

configure_video_backend()

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from telmeow_teleop.config import AppConfig  # noqa: E402

BACKENDS = {"dshow": cv2.CAP_DSHOW, "msmf": cv2.CAP_MSMF, "any": cv2.CAP_ANY}

#: Resoluciones a tantear, de mayor a menor. OpenCV no informa de las que
#: soporta una cámara: hay que pedirlas y comprobar qué devuelve de verdad.
RESOLUCIONES = ((1920, 1080), (1280, 720), (960, 540), (640, 480), (320, 240))


def fps_reales(cap, segundos: float = 3.0) -> tuple[float, float]:
    """Frames NUEVOS por segundo, y brillo medio de la imagen.

    Contar lo que devuelve ``read()`` NO sirve: puede devolver el mismo buffer
    repetido y dar cifras altísimas y falsas. Medido en esta máquina, una
    combinación que parecía dar 156 fps entregaba 21 de cada 39 frames
    duplicados; su tasa real era de 3.7 fps. Aquí se comparan los frames
    consecutivos y solo se cuentan los distintos.

    El brillo importa porque con poca luz la cámara alarga la exposición y baja
    los fps por hardware, sin que ningún ajuste de software lo pueda evitar.
    """
    previo, nuevos, brillos = None, 0, []
    inicio = time.perf_counter()
    while time.perf_counter() - inicio < segundos:
        ok, frame = cap.read()
        if not ok:
            break
        if previo is None or not np.array_equal(frame, previo):
            nuevos += 1
            if len(brillos) < 5:
                brillos.append(float(frame.mean()))
        previo = frame
    transcurrido = time.perf_counter() - inicio
    return (
        nuevos / transcurrido if transcurrido > 0 else 0.0,
        sum(brillos) / len(brillos) if brillos else 0.0,
    )


def probar(indice: int, backend: int, medir: bool = False) -> dict | None:
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

    medidas: dict = {}
    if medir:
        for ancho, alto in ((1280, 720), (640, 480)):
            if (ancho, alto) not in admitidas:
                continue
            # Un backend puede reventar al cambiar de resolución -- MSMF lo hace
            # en algunas cámaras con un fallo interno de OpenCV. La herramienta
            # existe justamente para descubrir esas incompatibilidades, así que
            # las anota en vez de morirse.
            try:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, ancho)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, alto)
                cap.set(cv2.CAP_PROP_FPS, 30)
                for _ in range(8):
                    cap.read()
                medidas[(ancho, alto)] = fps_reales(cap)
            except cv2.error as e:
                medidas[(ancho, alto)] = ("error", str(e).strip().splitlines()[-1])

    cap.release()
    return {"nativa": nativa, "fps": fps, "admitidas": admitidas, "medidas": medidas}


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
    parser.add_argument(
        "--medir", action="store_true",
        help="Mide los fps REALES de cada combinación de cámara, backend y "
             "resolución. Tarda unos segundos por combinación, pero es la única "
             "forma de saber cuál conviene usar.",
    )
    args = parser.parse_args(argv)

    cfg = AppConfig.from_env()
    backends = (
        [(args.backend, BACKENDS[args.backend])]
        if args.backend
        else ([(n, b) for n, b in BACKENDS.items() if n != "any"] if args.medir
              else [(cfg.camera.backend, BACKENDS[cfg.camera.backend])])
    )

    encontradas = 0
    for nombre_backend, backend in backends:
        print(f"Buscando cámaras con el backend '{nombre_backend}'…\n")
        for indice in range(args.max_index + 1):
            info = probar(indice, backend, medir=args.medir)
            if info is None:
                continue
            encontradas += 1
            marca = "  <- la configurada" if indice == cfg.camera.index else ""
            print(f"[{indice}] resolución por defecto {info['nativa'][0]}x{info['nativa'][1]}"
                  f"   {info['fps']:.0f} fps nominales{marca}")
            if info["admitidas"]:
                lista = ", ".join(f"{w}x{h}" for w, h in info["admitidas"])
                print(f"     admite: {lista}")
            for (ancho, alto), medida in info["medidas"].items():
                if medida[0] == "error":
                    print(f"     {ancho}x{alto}: FALLA con este backend — {medida[1]}")
                    continue
                fps, brillo = medida
                aviso = "   (poca luz: la exposición larga limita los fps)" if brillo < 60 else ""
                print(f"     {ancho}x{alto}: {fps:5.1f} fps REALES, "
                      f"brillo {brillo:.0f}/255{aviso}")
            print()

    if args.medir and encontradas:
        print("Los 'fps REALES' cuentan solo frames DISTINTOS del anterior.")
        print("read() puede devolver el mismo buffer repetido y dar cifras falsas.\n")

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
