"""Monitor de todo el tráfico MQTT de Telmeow.

Se suscribe a ``telmeow/#`` e imprime cada mensaje con su JSON formateado y la
tasa de recepción por topic. Sirve para ver el dato crudo cuando el visor no se
mueve y hay que averiguar si el problema está en el publisher o en el visor.

Uso::

    python tools/mqtt_monitor.py
    python tools/mqtt_monitor.py --broker 192.168.1.50 --topic telmeow/teleop/pose

El suscriptor original queda intacto en ``legacy/MQTT/MQTT_Subscriber.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict, deque
from pathlib import Path

import paho.mqtt.client as mqtt

# Permite ejecutar el archivo directamente (python tools/mqtt_monitor.py) sin
# instalar el paquete.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telmeow_teleop.config import MQTTConfig  # noqa: E402

_RATE_WINDOW = 1.0

_rates: dict[str, deque[float]] = defaultdict(deque)
_counts: dict[str, int] = defaultdict(int)


def _on_connect(client, userdata, flags, reason_code, properties=None):
    topic = userdata["topic"]
    if getattr(reason_code, "is_failure", False):
        print(f"[!] Conexión rechazada: {reason_code}")
        return
    client.subscribe(topic, qos=0)
    print(f"[ok] Conectado y suscrito a '{topic}'")


def _on_disconnect(client, userdata, *args):
    print("[!] Desconectado. Reintentando en segundo plano…")


def _on_message(client, userdata, message):
    now = time.monotonic()
    topic = message.topic

    window = _rates[topic]
    window.append(now)
    while window and now - window[0] > _RATE_WINDOW:
        window.popleft()
    _counts[topic] += 1

    raw = message.payload.decode("utf-8", errors="replace")
    try:
        body = json.dumps(json.loads(raw), indent=2, ensure_ascii=False, sort_keys=True)
    except json.JSONDecodeError:
        body = raw  # el legacy publicaba escalares sueltos, no JSON

    stamp = time.strftime("%H:%M:%S")
    print(
        f"\n[{stamp}] {topic}  "
        f"#{_counts[topic]}  {len(window)} Hz\n{body}"
    )


def main(argv: list[str] | None = None) -> int:
    defaults = MQTTConfig()
    parser = argparse.ArgumentParser(
        prog="mqtt_monitor",
        description="Imprime el tráfico MQTT de Telmeow con su tasa de recepción.",
    )
    parser.add_argument("--broker", default=defaults.host, help="Host del broker.")
    parser.add_argument("--port", type=int, default=defaults.port, help="Puerto TCP.")
    parser.add_argument(
        "--topic",
        default="telmeow/#",
        help="Filtro de suscripción (por defecto todo el árbol telmeow).",
    )
    args = parser.parse_args(argv)

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2, client_id="telmeow-monitor"
    )
    client.user_data_set({"topic": args.topic})
    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    client.on_message = _on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    print(f"Conectando a {args.broker}:{args.port}…  Ctrl-C para salir.")
    client.connect_async(args.broker, args.port, defaults.keepalive)
    client.loop_start()

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nCerrando.")
    finally:
        client.disconnect()
        client.loop_stop()
        if _counts:
            print("\nResumen por topic:")
            for topic, count in sorted(_counts.items()):
                print(f"  {topic}: {count} mensajes")
        else:
            print(
                "\nNo llegó ningún mensaje. Comprueba que Mosquitto esté corriendo\n"
                "y que algo esté publicando (python -m telmeow_teleop.publisher)."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
