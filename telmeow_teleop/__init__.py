"""Paquete de teleoperación del robot social Telmeow.

Estima la pose del operador desde una sola cámara RGB y la publica por MQTT
hacia el visor 3D del navegador.

Módulos:
    config        -- parámetros de ejecución (MQTT, tasas, rutas, cámara)
    limits        -- límites articulares del robot y recorte de poses
    filters       -- suavizado temporal (One Euro, EMA, histéresis de clase)
    publisher     -- publicación de poses por MQTT hacia el visor
    head_tracker  -- pose de la cabeza por Face Mesh + solvePnP
    arm_tracker   -- pose de los brazos por BlazePose (abducción y flexión)

Los tres primeros usan solo biblioteca estándar, así que toda su lógica se puede
validar sin instalar TensorFlow ni MediaPipe.

El paquete reemplaza a los scripts sueltos que quedaron en ``legacy/``, que se
conservan sin modificar como referencia histórica citada en el documento de
tesis.
"""

import os as _os


def configure_video_backend() -> None:
    """Prepara las variables de entorno de OpenCV. **Antes de importar cv2.**

    Desactiva las transformaciones por hardware de Media Foundation. Con ellas
    activadas, ``cv2.VideoCapture(indice, cv2.CAP_MSMF)`` se **cuelga
    indefinidamente** al abrir algunas webcams: medido en esta máquina, colgaba
    tanto en un banco de pruebas como en la aplicación real, sin imprimir nada.
    Con la variable a 0, ocho aperturas seguidas sin un solo bloqueo.

    OpenCV lee estas variables al cargar su módulo de vídeo, así que fijarlas
    después de ``import cv2`` no sirve de nada.

    No sobrescribe lo que ya venga definido, para poder investigarlo si hiciera
    falta.

    Se llama sola al importar el paquete. Es pública para los scripts de
    ``tools/`` que importan cv2 por su cuenta: ahí conviene invocarla de forma
    explícita, para que se vea que el orden de los imports es deliberado y nadie
    los reordene.
    """
    _os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")


configure_video_backend()

__version__ = "0.1.0"

__all__ = [
    "config",
    "limits",
    "filters",
    "publisher",
    "head_tracker",
    "arm_tracker",
]
