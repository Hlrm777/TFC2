"""Monitor de todo el tráfico MQTT de Telmeow.

Se suscribe a ``telmeow/#`` e imprime cada mensaje con su JSON formateado y la
tasa de recepción por topic. Sirve para ver el dato crudo cuando el visor no se
mueve y hay que averiguar si el problema está en el publisher o en el visor.

Uso::

    python tools/mqtt_monitor.py
    python tools/mqtt_monitor.py --broker 192.168.1.50 --topic telmeow/teleop/pose
    python tools/mqtt_monitor.py --latencia latencia.csv

Con ``--latencia`` mide la latencia desde la cámara (P7.3): el pipeline pone en
el campo ``t`` la hora de captura del frame del que sale cada pose, y aquí se
le resta la hora de llegada. Es un camino independiente del visor, y el que da
la cifra citable: procesamiento + espera del hilo de publicación + broker +
suscriptor. Solo vale con los dos procesos en la MISMA máquina, que es lo único
que comparte el reloj de pared. En vez de cada JSON imprime una línea por
segundo, y al salir la mediana y el p95.

El suscriptor original queda intacto en ``legacy/MQTT/MQTT_Subscriber.py``.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
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


def latencia_ms(cuerpo, ahora_ms: float) -> float | None:
    """Latencia de un mensaje de pose: hora de llegada menos su campo ``t``.

    ``None`` si el mensaje no trae ``t`` numérico (el pipeline anterior al P7.3,
    o el demo del publisher).
    """
    if not isinstance(cuerpo, dict):
        return None
    t = cuerpo.get("t")
    if isinstance(t, bool) or not isinstance(t, (int, float)):
        return None
    return float(ahora_ms) - float(t)


def resumen_latencias(valores: list[float]) -> dict[str, float] | None:
    """Mediana, p95 y extremos, en ms. ``None`` sin valores."""
    if not valores:
        return None
    ordenados = sorted(valores)
    return {
        "n": float(len(ordenados)),
        "mediana": statistics.median(ordenados),
        "p95": ordenados[min(len(ordenados) - 1, int(round(0.95 * (len(ordenados) - 1))))],
        "min": ordenados[0],
        "max": ordenados[-1],
    }


def _on_message(client, userdata, message):
    # perf_counter y no monotonic (§0.7): en Windows monotonic va a saltos de
    # 15.6 ms, comparables al periodo de 50 ms que se quiere medir.
    now = time.perf_counter()
    topic = message.topic

    window = _rates[topic]
    window.append(now)
    while window and now - window[0] > _RATE_WINDOW:
        window.popleft()
    _counts[topic] += 1

    raw = message.payload.decode("utf-8", errors="replace")
    try:
        cuerpo = json.loads(raw)
        body = json.dumps(cuerpo, indent=2, ensure_ascii=False, sort_keys=True)
    except json.JSONDecodeError:
        cuerpo = None
        body = raw  # el legacy publicaba escalares sueltos, no JSON

    latencias = userdata.get("latencias")
    if latencias is not None:
        ahora_ms = time.time() * 1000.0
        valor = latencia_ms(cuerpo, ahora_ms)
        if valor is not None:
            latencias.append(valor)
            userdata["escritor"].writerow([f"{ahora_ms:.1f}", f"{cuerpo['t']}", f"{valor:.1f}"])
            # Al disco en cada fila: tools/sesion_verificacion.py cierra el
            # monitor de golpe al acabar la grabación, y lo que quedara en el
            # búfer se perdería.
            userdata["registro"].flush()
        if now - userdata["ultimo_aviso"] >= 1.0:
            userdata["ultimo_aviso"] = now
            recientes = latencias[-20:]
            mediana = f"{statistics.median(recientes):.0f} ms" if recientes else "sin campo t"
            print(f"{topic}  {len(window)} Hz  latencia {mediana}  ({len(latencias)} medidas)")
        return

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
    parser.add_argument(
        "--latencia",
        type=Path,
        metavar="CSV",
        help="Mide la latencia desde la captura (campo t del pipeline, P7.3) y "
             "la graba: recibido_ms, t_captura_ms, latencia_ms.",
    )
    args = parser.parse_args(argv)

    datos = {"topic": args.topic}
    registro = None
    if args.latencia is not None:
        # Aquí y no arriba: head_tracker importa MediaPipe, y el monitor solo lo
        # necesita para no pisar una medición anterior.
        from telmeow_teleop.head_tracker import apartar_si_existe

        apartado = apartar_si_existe(args.latencia)
        if apartado is not None:
            print(f"AVISO: {args.latencia.name} ya existia. Se conserva como {apartado.name}.")
        registro = open(args.latencia, "w", newline="", encoding="utf-8")
        escritor = csv.writer(registro)
        escritor.writerow(["recibido_ms", "t_captura_ms", "latencia_ms"])
        datos.update(latencias=[], escritor=escritor, registro=registro, ultimo_aviso=0.0)

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2, client_id="telmeow-monitor"
    )
    client.user_data_set(datos)
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
        if registro is not None:
            registro.close()
            resumen = resumen_latencias(datos["latencias"])
            if resumen is None:
                print("\nNingún mensaje traía el campo t: ¿es el pipeline del P7?")
            else:
                print(f"\nLatencia desde la captura ({resumen['n']:.0f} mensajes): "
                      f"mediana {resumen['mediana']:.1f} ms, p95 {resumen['p95']:.1f}, "
                      f"min {resumen['min']:.1f}, max {resumen['max']:.1f}. "
                      f"Grabada en {args.latencia}")
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
