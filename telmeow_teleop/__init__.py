"""Paquete de teleoperación del robot social Telmeow.

Estima la pose del operador desde una sola cámara RGB y la publica por MQTT
hacia el visor 3D del navegador.

Módulos:
    config  -- parámetros de ejecución (MQTT, tasas, rutas, cámara)
    limits  -- límites articulares del robot y recorte de poses

El paquete reemplaza a los scripts sueltos que quedaron en ``legacy/``, que se
conservan sin modificar como referencia histórica citada en el documento de
tesis.
"""

__version__ = "0.1.0"

__all__ = ["config", "limits"]
