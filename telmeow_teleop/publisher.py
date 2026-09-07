"""Publicación de poses por MQTT hacia el visor 3D.

QUÉ REEMPLAZA Y POR QUÉ
-----------------------
Sustituye a ``legacy/MQTT/MQTT_Publisher.py``, incompatible con el visor por
tres motivos independientes:

1. Publicaba en un broker PÚBLICO por TCP (``mqtt.eclipseprojects.io:1883``).
   El navegador no puede abrir una conexión TCP cruda: necesita WebSocket. Aquí
   se apunta a un Mosquitto local con dos listeners (1883 TCP para Python, 9001
   WebSocket para el visor).
2. Publicaba ESCALARES SUELTOS en topics separados (``telmeow/angles/head/1``).
   El visor espera un único JSON con la pose completa.
3. Usaba QoS 1 con ``wait_for_publish()`` BLOQUEANTE por mensaje. Eso serializa
   cada publicación contra un ida y vuelta de red, y no sostiene 20 Hz. Aquí se
   usa QoS 0 sin esperar confirmación: para teleoperación el dato más reciente
   vale más que la garantía de entrega de uno viejo.

EL CONTRATO
-----------
Un solo mensaje JSON en ``telmeow/teleop/pose``::

    {"pan": -12.4, "tilt": 8.1, "armL": -20.0, "armLtw": 15.0,
     "armR": 30.5, "armRtw": 50.2, "emo": "feliz",
     "gesture": "page_next", "page": 3}

Todos los campos son opcionales. El visor comprueba cada uno por separado
(``typeof o.pan === 'number'``) y conserva su último valor si falta, lo que
permite emitir mensajes parciales sin que el robot dé un tirón hacia home.

Consumidores de cada topic: ``pose`` lo lee el visor 3D; ``page`` y ``story``
los lee el servicio de pantalla externa de la Raspberry Pi. Ver ``config.py``.
"""

from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import paho.mqtt.client as mqtt

from .config import MQTTConfig, RateConfig

__all__ = ["PosePublisher", "VALID_GESTURES"]

#: Gestos discretos que el visor reconoce (telmeow_viewer.html:1005).
VALID_GESTURES = frozenset({"page_next", "page_prev"})

#: Ventana para calcular la tasa efectiva, en segundos.
_RATE_WINDOW = 1.0

#: Decimales de los ángulos publicados. Acota el tamaño del payload sin
#: acercarse siquiera a una cuantización perceptible: el problema que el plan
#: maestro señala en los scripts legacy son pasos de 5° y 10°, no de 0.001°.
_ANGLE_DECIMALS = 3

#: Holgura del limitador de tasa, como fracción del periodo.
#:
#: EL LIMITADOR ES UNA RED DE SEGURIDAD, NO UN RELOJ DE PRECISIÓN. Su trabajo es
#: frenar a quien publica SISTEMÁTICAMENTE más rápido de la cuenta (p. ej.
#: main.py llamando una vez por frame a 30 fps con la meta puesta en 20 Hz), no
#: castigar el jitter de un emisor que ya va a la tasa correcta.
#:
#: Con un reloj de alta resolución (ver _DEFAULT_CLOCK) un emisor a 20 Hz da
#: intervalos de ~50.1 ms, así que el 10 % (umbral 45 ms) deja 5 ms de margen
#: para un hipo del sistema. El caso que sí hay que frenar sigue frenándose:
#: 30 fps son 33.3 ms, muy por debajo del umbral.
_RATE_TOLERANCE = 0.10

#: Reloj del limitador de tasa.
#:
#: perf_counter y NO monotonic. En Windows, time.monotonic() tiene una
#: resolución de ~15.625 ms, comparable al periodo de 50 ms que hay que medir:
#: los intervalos salen cuantizados a múltiplos de tick (46.875 / 62.5 ms) y de
#: vez en cuando se leen como 31.25 ms, por debajo de cualquier umbral
#: razonable. Medido con monotonic: 9 de 60 mensajes descartados publicando a
#: 20 Hz nominales, con el bucle emisor funcionando perfectamente. El mismo
#: bucle medido con perf_counter da intervalos de 50.1 ms y cero descartes.
#:
#: perf_counter también es monótono y tiene resolución de sub-microsegundo en
#: las tres plataformas, así que es el reloj correcto para medir intervalos.
#: Vale la pena recordarlo para el HUD del PROMPT 7 y para cualquier medida de
#: latencia que se reporte en la tesis.
_DEFAULT_CLOCK = time.perf_counter


class PosePublisher:
    """Publica poses en el broker, con limitación de tasa por descarte.

    Se puede usar como gestor de contexto::

        with PosePublisher() as pub:
            pub.publish(pan=10.0, tilt=0.0, armL=0.0, armLtw=0.0,
                        armR=20.0, armRtw=30.0, emo="feliz")

    Args:
        mqtt_config: destino y topics. Por defecto, Mosquitto local.
        rates: de aquí sale ``publish_hz``.
        include_timestamp: si es True añade un campo ``t`` con el tiempo Unix
            en milisegundos. Desactivado por defecto para no alterar el
            contrato. Existe porque el indicador de latencia del visor mide en
            realidad el intervalo entre mensajes (``now - lastMsg``), no el
            retardo extremo a extremo; medirlo de verdad requiere un timestamp
            en el payload y un cambio en el visor.
        client_factory: fábrica alternativa de cliente MQTT. Solo para pruebas.
        clock: fuente de tiempo. Debe ser monótona y de alta resolución;
            ver ``_DEFAULT_CLOCK``. Se inyecta solo en las pruebas.
    """

    def __init__(
        self,
        mqtt_config: MQTTConfig | None = None,
        rates: RateConfig | None = None,
        *,
        include_timestamp: bool = False,
        client_factory: Callable[[], Any] | None = None,
        clock: Callable[[], float] = _DEFAULT_CLOCK,
    ) -> None:
        self.config = mqtt_config or MQTTConfig()
        self.rates = rates or RateConfig()
        self.include_timestamp = include_timestamp
        self._clock = clock

        self._min_period = (1.0 / self.rates.publish_hz) * (1.0 - _RATE_TOLERANCE)
        self._last_publish: float | None = None
        self._sent = 0
        self._dropped = 0
        self._failed = 0
        self._recent: deque[float] = deque()
        self._connected = False

        self._client = (client_factory or self._default_client)()
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        # La política de reconexión es del publisher, no de quien fabrique el
        # cliente: se aplica también a los inyectados. Retroceso exponencial de
        # 1 a 30 s -- una sesión de mediación no puede caerse porque el broker
        # parpadee un momento.
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)

    # -- construcción del cliente ------------------------------------------

    def _default_client(self) -> mqtt.Client:
        return mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.config.client_id,
        )

    # -- callbacks ---------------------------------------------------------

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        self._connected = getattr(reason_code, "is_failure", False) is False

    def _on_disconnect(self, client, userdata, *args):
        self._connected = False

    # -- ciclo de vida -----------------------------------------------------

    def connect(self) -> "PosePublisher":
        """Inicia la conexión en segundo plano.

        Usa ``connect_async`` en vez de ``connect``: si el broker todavía no
        está levantado, el programa arranca igual y paho reintenta solo, en vez
        de morir con una excepción en el arranque.
        """
        self._client.connect_async(
            self.config.host, self.config.port, self.config.keepalive
        )
        self._client.loop_start()
        return self

    def wait_connected(self, timeout: float = 5.0) -> bool:
        """Espera hasta que el enlace esté establecido. Devuelve si lo logró.

        HAY QUE LLAMARLA ANTES DE EMPEZAR A EMITIR. ``connect_async`` devuelve
        de inmediato, pero el enlace tarda en levantarse: medido contra un
        broker local, **~2 s**. Con QoS 0 los mensajes publicados en esa ventana
        no se encolan, se descartan — así que sin esta espera los primeros ~40
        mensajes de una sesión a 20 Hz se pierden sin dejar rastro.
        """
        # Usa el reloj real, no el inyectado: esto es una espera de pared, no
        # una medición de intervalos. Con el reloj falso de las pruebas (que no
        # avanza solo) el bucle no terminaría nunca.
        deadline = time.perf_counter() + timeout
        while not self._connected and time.perf_counter() < deadline:
            time.sleep(0.01)
        return self._connected

    def disconnect(self) -> None:
        self._client.disconnect()
        self._client.loop_stop()
        self._connected = False

    def __enter__(self) -> "PosePublisher":
        return self.connect()

    def __exit__(self, *exc_info) -> None:
        self.disconnect()

    # -- telemetría para el HUD -------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def message_count(self) -> int:
        """Mensajes efectivamente publicados."""
        return self._sent

    @property
    def dropped_count(self) -> int:
        """Llamadas descartadas por exceder la tasa."""
        return self._dropped

    @property
    def failed_count(self) -> int:
        """Mensajes que el cliente MQTT no pudo entregar a la red.

        Casi siempre significa «todavía no hay conexión». Con QoS 0 esos
        mensajes se pierden: no se encolan para reenviar.
        """
        return self._failed

    @property
    def effective_hz(self) -> float:
        """Tasa real del último segundo (misma ventana que el `t-rate` del visor)."""
        self._trim_window(self._clock())
        return float(len(self._recent))

    def _trim_window(self, now: float) -> None:
        while self._recent and now - self._recent[0] > _RATE_WINDOW:
            self._recent.popleft()

    # -- publicación -------------------------------------------------------

    def publish(
        self,
        pan: float | None = None,
        tilt: float | None = None,
        armL: float | None = None,
        armLtw: float | None = None,
        armR: float | None = None,
        armRtw: float | None = None,
        emo: str | None = None,
        gesture: str | None = None,
        page: int | None = None,
    ) -> bool:
        """Emite un único mensaje JSON con la pose.

        LIMITACIÓN DE TASA POR DESCARTE: si se llama más rápido que
        ``publish_hz``, la llamada se descarta en vez de encolarse. En
        teleoperación interesa el dato más reciente, no la cola completa: una
        cola solo añade retardo y muestra al robot un pasado que ya no importa.

        EXCEPCIÓN: los mensajes que llevan ``gesture`` o ``page`` NUNCA se
        descartan. Son eventos discretos y poco frecuentes que tienen que
        llegar sí o sí; perder un ``page_next`` desincroniza el gesto del robot
        respecto a la página que el niño está viendo.

        Los campos en ``None`` se omiten del JSON. El visor conserva su último
        valor para los que falten.

        Returns:
            True si el mensaje se emitió, False si se descartó por tasa.

        Raises:
            ValueError: si ``gesture`` no es 'page_next' ni 'page_prev'.
        """
        if gesture is not None and gesture not in VALID_GESTURES:
            raise ValueError(
                f"gesture no reconocido: {gesture!r}. "
                f"Valores válidos: {sorted(VALID_GESTURES)}"
            )

        now = self._clock()
        discrete = gesture is not None or page is not None

        if not discrete and self._last_publish is not None:
            if now - self._last_publish < self._min_period:
                self._dropped += 1
                return False

        payload: dict[str, Any] = {}
        for key, value in (
            ("pan", pan), ("tilt", tilt),
            ("armL", armL), ("armLtw", armLtw),
            ("armR", armR), ("armRtw", armRtw),
        ):
            if value is not None:
                payload[key] = round(float(value), _ANGLE_DECIMALS)
        if emo is not None:
            payload["emo"] = emo
        if gesture is not None:
            payload["gesture"] = gesture
        if page is not None:
            payload["page"] = int(page)
        if self.include_timestamp:
            payload["t"] = int(time.time() * 1000)

        # QoS 0 y sin wait_for_publish(): no se bloquea ni se espera ack.
        info = self._client.publish(
            self.config.topic_pose,
            json.dumps(payload, separators=(",", ":")),
            qos=self.config.qos,
        )

        # Hay que MIRAR el código de retorno. Si el enlace todavía no está
        # arriba, paho devuelve MQTT_ERR_NO_CONN y con QoS 0 tira el mensaje
        # sin encolarlo. Contarlo como enviado hace que el HUD informe 20 Hz
        # mientras al broker no llega absolutamente nada, que es el peor modo
        # de fallo posible: parece que funciona.
        rc = getattr(info, "rc", mqtt.MQTT_ERR_SUCCESS)
        if rc != mqtt.MQTT_ERR_SUCCESS:
            self._failed += 1
            self._last_publish = now
            return False

        self._last_publish = now
        self._sent += 1
        self._recent.append(now)
        self._trim_window(now)
        return True


# --------------------------------------------------------------------------
# Prueba del enlace, sin cámara ni modelo
# --------------------------------------------------------------------------


def _demo(duration: float = 10.0) -> None:
    """Publica una pose oscilante para verificar el enlace con el visor.

    Es el punto de control del plan: si el brazo se mueve en el visor, el
    transporte está resuelto y cualquier fallo posterior es de visión.

    Los valores del seno se eligen dentro del espacio admisible comprobándolos
    con ``limits.clamp_arm``, no a ojo: a flexion ~50° la abducción admite hasta
    55°, así que armR = 30 +- 12 y armRtw = 50 +- 8 quedan holgados.
    """
    import math

    from .config import AppConfig
    from .limits import Side, clamp_arm

    # Desde el entorno, para poder apuntar a otro broker sin tocar el código:
    #     TELMEOW_MQTT_HOST=192.168.1.50 python -m telmeow_teleop.publisher
    app = AppConfig.from_env()
    config, rates = app.mqtt, app.rates
    print(
        f"Publicando en {config.host}:{config.port} "
        f"topic '{config.topic_pose}' a {rates.publish_hz:g} Hz "
        f"durante {duration:g} s.  Ctrl-C para cortar."
    )

    with PosePublisher(config, rates) as pub:
        # Sin esta espera los primeros ~2 s de mensajes se pierden en silencio
        # (ver wait_connected), y quien mire el visor no vería nada moverse
        # durante la quinta parte de la demostración.
        if not pub.wait_connected(timeout=5.0):
            print("  aviso: sin conexión tras 5 s; se emite igualmente")

        start = time.perf_counter()
        # Cadencia por fecha límite, no por sleep(periodo): dormir el periodo
        # completo DESPUÉS de hacer el trabajo arrastra el tiempo de ese
        # trabajo y la tasa real queda por debajo de la nominal. Es el mismo
        # patrón que necesitará el bucle de main.py.
        next_tick = start
        try:
            while (elapsed := time.perf_counter() - start) < duration:
                phase = 2.0 * math.pi * 0.25 * elapsed  # 0.25 Hz
                arm_r = 30.0 + 12.0 * math.sin(phase)
                arm_r_tw = 50.0 + 8.0 * math.sin(phase)

                # Red de seguridad: nunca publicar fuera del espacio admisible.
                right = clamp_arm(arm_r, arm_r_tw, Side.R)
                if right.clamped:
                    print(f"  aviso: pose recortada -> {right.as_tuple}")

                pub.publish(
                    pan=0.0,
                    tilt=0.0,
                    armL=0.0,
                    armLtw=0.0,
                    armR=right.abduccion.value,
                    armRtw=right.flexion.value,
                    emo="feliz",
                )
                # Si una pausa del sistema nos dejó atrás, se SALTAN las ranuras
                # perdidas en vez de dispararlas todas seguidas para recuperar.
                # Sin este max(), tras un tirón el bucle emite una ráfaga que el
                # limitador descarta, y la tasa efectiva baja sin motivo real.
                next_tick = max(next_tick + rates.publish_period, time.perf_counter())
                time.sleep(max(0.0, next_tick - time.perf_counter()))
        except KeyboardInterrupt:
            print("\nInterrumpido.")
        finally:
            print(
                f"Enviados {pub.message_count} mensajes, "
                f"{pub.dropped_count} descartados por tasa, "
                f"{pub.failed_count} sin entregar, "
                f"tasa final {pub.effective_hz:g} Hz, "
                f"conectado={pub.connected}"
            )
            if not pub.connected:
                print(
                    "\nNo hubo conexión con el broker. Comprueba que Mosquitto\n"
                    "esté corriendo con los DOS listeners:\n"
                    "    mosquitto -c mosquitto.conf -v"
                )


if __name__ == "__main__":
    _demo()
