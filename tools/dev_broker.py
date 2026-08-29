"""Broker MQTT de desarrollo, en Python puro y sin privilegios de administrador.

POR QUÉ EXISTE
--------------
El broker declarado del proyecto es **Mosquitto**, y es el que debe usarse para
las mediciones que se reporten en la tesis. Pero instalarlo en Windows exige
privilegios de administrador (escribe en Program Files y registra un servicio),
lo que lo vuelve un obstáculo en máquinas donde no se tienen.

Este script levanta un broker equivalente con ``amqtt``, que se instala con pip
sin permisos especiales, y abre **los mismos dos listeners** que
``mosquitto.conf``:

    1883  TCP crudo   -> el pipeline de Python
    9001  WebSocket   -> el visor 3D en el navegador

Sirve para cerrar el punto de control del plan (ver el brazo oscilar en el
visor) sin depender de la instalación de Mosquitto. Es una herramienta de
desarrollo: no se usa en la demostración final y no sustituye a Mosquitto en lo
que el documento declara.

Uso::

    python tools/dev_broker.py
    python tools/dev_broker.py --port 1883 --ws-port 9001 --verbose

Instalación previa: ``pip install -r requirements-dev.txt``
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys
from pathlib import Path

from amqtt.broker import Broker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telmeow_teleop.config import MQTTConfig  # noqa: E402


def build_config(port: int, ws_port: int) -> dict:
    """Equivalente al mosquitto.conf del repositorio."""
    return {
        "listeners": {
            "default": {
                "type": "tcp",
                "bind": f"0.0.0.0:{port}",
                "max_connections": 50,
            },
            "ws": {
                "type": "ws",
                "bind": f"0.0.0.0:{ws_port}",
                "max_connections": 50,
            },
        },
        "sys_interval": 0,
        "auth": {"allow-anonymous": True},
        "topic_check": {"enabled": False},
    }


async def run(port: int, ws_port: int) -> None:
    broker = Broker(build_config(port, ws_port))
    await broker.start()
    print(
        f"Broker de desarrollo escuchando:\n"
        f"  TCP       localhost:{port}   (pipeline de Python)\n"
        f"  WebSocket ws://localhost:{ws_port}  (visor 3D)\n"
        f"Ctrl-C para detener."
    )
    try:
        await asyncio.Event().wait()  # corre hasta que lo interrumpan
    finally:
        with contextlib.suppress(Exception):
            await broker.shutdown()


def main(argv: list[str] | None = None) -> int:
    defaults = MQTTConfig()
    parser = argparse.ArgumentParser(
        prog="dev_broker",
        description=(
            "Broker MQTT local en Python puro, con listeners TCP y WebSocket. "
            "Alternativa a Mosquitto cuando no se tienen privilegios de "
            "administrador."
        ),
    )
    parser.add_argument("--port", type=int, default=defaults.port, help="Puerto TCP.")
    parser.add_argument(
        "--ws-port", type=int, default=defaults.ws_port, help="Puerto WebSocket."
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Traza detallada del broker."
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        asyncio.run(run(args.port, args.ws_port))
    except KeyboardInterrupt:
        print("\nBroker detenido.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
