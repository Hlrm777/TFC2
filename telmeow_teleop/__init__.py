"""Paquete de teleoperación del robot social Telmeow.

Estima la pose del operador desde una sola cámara RGB y la publica por MQTT
hacia el visor 3D del navegador.

Módulos:
    config        -- parámetros de ejecución (MQTT, tasas, rutas, cámara)
    limits        -- límites articulares del robot y recorte de poses
    filters       -- suavizado temporal (One Euro, EMA, histéresis de clase)
    publisher     -- publicación de poses por MQTT hacia el visor
    head_tracker  -- pose de la cabeza por Face Mesh + solvePnP

Los tres primeros usan solo biblioteca estándar, así que toda su lógica se puede
validar sin instalar TensorFlow ni MediaPipe.

El paquete reemplaza a los scripts sueltos que quedaron en ``legacy/``, que se
conservan sin modificar como referencia histórica citada en el documento de
tesis.
"""

__version__ = "0.1.0"

__all__ = ["config", "limits", "filters", "publisher", "head_tracker"]
