"""Apertura de la cámara, con los ajustes en el orden que sí funciona.

Existe porque abrir una webcam en Windows tiene cuatro trampas que ya se han
pagado en este proyecto, y estaban duplicadas en los dos trackers — o sea que
arreglar una dejaba la otra rota:

1. **El backend.** ``cv2.CAP_MSMF`` se **cuelga indefinidamente** en algunas
   máquinas con las protecciones de Windows activadas. Se usa DirectShow.
2. **El FOURCC antes que la resolución.** Pedirlo después no siempre se aplica,
   y sin MJPG muchas webcams bajan a 10 fps a 1280x720 sin avisar.
3. **La exposición sobrevive al proceso.** Los ajustes de DirectShow viven en
   el driver, así que la exposición que deje una aplicación sigue puesta en la
   siguiente. El 2026-09-13 eso dejó la cámara **negra a 30 fps** con la
   exposición en manual −6, heredada de una ejecución anterior. Ver
   ``config.CameraConfig`` y el H34.
4. **``isOpened()`` se comprueba antes de leer**, y aun así el primer frame
   puede venir mal: hay que descartar unos cuantos mientras la autoexposición
   converge.

Se mantiene fuera de ``config.py`` a propósito: ese módulo es solo biblioteca
estándar, y aquí hace falta ``cv2``.
"""

from __future__ import annotations

import cv2

from .config import CameraConfig

__all__ = ["BACKENDS", "abrir_camara", "AUTO_EXPOSURE_ON", "AUTO_EXPOSURE_OFF"]

#: Nombre de backend -> constante de OpenCV. El nombre viaja como cadena en la
#: configuración para que ``config.py`` no tenga que importar cv2.
BACKENDS: dict[str, int] = {
    "dshow": cv2.CAP_DSHOW,
    "msmf": cv2.CAP_MSMF,
    "any": cv2.CAP_ANY,
}

#: Valores de ``CAP_PROP_AUTO_EXPOSURE`` en DirectShow. No son 1 y 0, y no están
#: documentados en la API de OpenCV: vienen del rango del propio filtro de
#: DirectShow, donde 0.75 significa automática y 0.25 manual. Como constantes
#: con nombre porque dos números mágicos invertidos aquí dejan la cámara ciega
#: sin ningún error.
AUTO_EXPOSURE_ON: float = 0.75
AUTO_EXPOSURE_OFF: float = 0.25


def abrir_camara(cfg: CameraConfig):
    """Abre la cámara descrita por ``cfg``, o devuelve ``None``.

    El orden de las llamadas importa y es el del docstring del módulo. Devuelve
    ``None`` en vez de lanzar para que quien llame pueda imprimir un mensaje
    útil y salir con código distinto de cero, que es lo que hacen los dos modos
    demo.
    """
    if cfg.backend not in BACKENDS:
        raise ValueError(
            f"backend de cámara desconocido: {cfg.backend!r}. "
            f"Válidos: {sorted(BACKENDS)}"
        )

    cap = cv2.VideoCapture(cfg.index, BACKENDS[cfg.backend])
    if not cap.isOpened():
        cap.release()
        return None

    # El formato ANTES que la resolución: pedirlo después no siempre se aplica.
    if cfg.fourcc:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*cfg.fourcc))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.height)
    cap.set(cv2.CAP_PROP_FPS, cfg.fps)

    # La exposición, EXPLICITA siempre. Es lo único que hace el arranque
    # reproducible en vez de depender de lo que dejó el programa anterior.
    if cfg.auto_exposure:
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, AUTO_EXPOSURE_ON)
    else:
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, AUTO_EXPOSURE_OFF)
        cap.set(cv2.CAP_PROP_EXPOSURE, cfg.exposure)

    return cap


def describir(cap, cfg: CameraConfig) -> str:
    """Una línea con lo que la cámara concedió de verdad, no lo que se pidió.

    Se lee de vuelta a propósito: la cámara puede conceder otra resolución u
    otra tasa sin avisar, y darlo por bueno ya costó una conclusión falsa en
    este proyecto (H19).
    """
    ancho = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    alto = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    exposicion = (
        "auto" if cfg.auto_exposure else f"manual {cap.get(cv2.CAP_PROP_EXPOSURE):+g}"
    )
    return (
        f"Camara {cfg.index} ({cfg.backend}) a {ancho}x{alto}, "
        f"{cap.get(cv2.CAP_PROP_FPS):.0f} fps nominales, exposicion {exposicion}."
    )


def brillo_medio(frame) -> float:
    """Brillo medio de un frame, de 0 a 255.

    Está aquí para que los modos demo puedan **avisar de una cámara negra** en
    vez de dejar al operador mirando una ventana oscura sin saber si el fallo
    es del detector. Por debajo de ~8 no hay nada que detectar.
    """
    return float(frame.mean())
