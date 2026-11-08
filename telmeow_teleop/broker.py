"""El broker de la sesión: comprobar que está y, si no, levantarlo.

Salió de ``tools/sesion_verificacion.py`` en el P10, porque el comando de la
sesión de narración (``python -m telmeow_teleop.control_panel``) hace lo mismo
al empezar. La trampa que vigila es la del §4.1 del plan: el SERVICIO de
Mosquitto de Windows escucha en el 1883 pero no en el 9001 (WebSocket), y si
además se lanza el del repositorio quedan dos brokers que no se hablan —500
mensajes publicados, cero recibidos, todo en verde (H15)—.
"""

from __future__ import annotations

import socket
import subprocess
import time
from pathlib import Path

from .config import PROJECT_ROOT

__all__ = ["MOSQUITTO", "puerto_abierto", "estado_broker", "asegurar_broker"]

MOSQUITTO = Path(r"C:\Program Files\mosquitto\mosquitto.exe")


def puerto_abierto(puerto: int, host: str = "localhost") -> bool:
    try:
        with socket.create_connection((host, puerto), timeout=0.5):
            return True
    except OSError:
        return False


def estado_broker(tcp: bool, websocket: bool) -> str:
    """``"listo"``, ``"arrancar"`` o ``"servicio"`` (la trampa del §4.1: el
    servicio de Mosquitto escucha en el 1883 sin WebSocket, y si además se
    lanza el del repositorio quedan dos brokers que no se hablan)."""
    if tcp and websocket:
        return "listo"
    if tcp:
        return "servicio"
    return "arrancar"


def asegurar_broker(arrancados: list, *, puerto_abierto=puerto_abierto, popen=None,
                    esperar=time.sleep, mosquitto: Path = MOSQUITTO,
                    tcp: int = 1883, websocket: int = 9001) -> bool:
    """Deja el broker listo en los dos puertos, o explica por qué no.

    Si lo levanta, añade el proceso a ``arrancados``: quien llama lo apaga al
    terminar, y solo a ese (un broker que ya estaba se deja como estaba).
    Devuelve si quedó listo.
    """
    estado = estado_broker(puerto_abierto(tcp), puerto_abierto(websocket))
    if estado == "servicio":
        print(f"\nEl {tcp} esta abierto pero el {websocket} no: es el SERVICIO de Mosquitto,\n"
              "que no habla con el visor. En una terminal COMO ADMINISTRADOR:\n\n"
              "    Stop-Service mosquitto; Set-Service mosquitto -StartupType Manual\n\n"
              "y vuelve a lanzar este comando.")
        return False
    if estado == "arrancar":
        if not Path(mosquitto).is_file():
            print(f"No encuentro {mosquitto}. Lanza el broker a mano (§4.2) o "
                  f"tools/dev_broker.py, y vuelve a empezar.")
            return False
        print("Levantando el broker del repositorio (mosquitto.conf)...")
        arrancados.append((popen or subprocess.Popen)(
            [str(mosquitto), "-c", str(PROJECT_ROOT / "mosquitto.conf")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        esperar(1.5)
        if estado_broker(puerto_abierto(tcp), puerto_abierto(websocket)) != "listo":
            print("El broker no abrio los dos puertos. Revisa §4.1 del plan.")
            return False
    print(f"Broker: listo ({tcp} y {websocket}).")
    return True
