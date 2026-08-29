"""Pruebas del publisher MQTT, sin broker y sin red.

Se inyecta un cliente falso y un reloj falso, así que toda la lógica de payload
y de limitación de tasa se valida de forma determinista. Lo que NO se puede
comprobar aquí es el enlace real con el visor: eso es el punto de control
manual del plan maestro (levantar Mosquitto y ver si el brazo oscila).
"""

import json
import time

import paho.mqtt.client as mqtt
import pytest

from telmeow_teleop.config import MQTTConfig, RateConfig
from telmeow_teleop.publisher import _DEFAULT_CLOCK, VALID_GESTURES, PosePublisher

#: Claves exactas que el visor lee en telmeow_viewer.html:993-1009.
CLAVES_DEL_CONTRATO = {
    "pan", "tilt", "armL", "armLtw", "armR", "armRtw", "emo", "gesture", "page",
}

POSE = dict(pan=1.0, tilt=2.0, armL=3.0, armLtw=4.0, armR=5.0, armRtw=6.0)


class InfoPublicacion:
    """Equivalente al MQTTMessageInfo de paho: solo interesa el código."""

    def __init__(self, rc):
        self.rc = rc


class ClienteFalso:
    """Registra las publicaciones en vez de mandarlas a la red."""

    def __init__(self, rc=mqtt.MQTT_ERR_SUCCESS):
        self.publicaciones = []
        self.conectado = False
        self.loop_activo = False
        self.on_connect = None
        self.on_disconnect = None
        self.retardo_reconexion = None
        self.rc = rc  # código que devolverá publish()

    def publish(self, topic, payload, qos=0):
        self.publicaciones.append({"topic": topic, "payload": payload, "qos": qos})
        return InfoPublicacion(self.rc)

    def connect_async(self, host, port, keepalive):
        self.conectado = True
        self.destino = (host, port, keepalive)

    def loop_start(self):
        self.loop_activo = True

    def loop_stop(self):
        self.loop_activo = False

    def disconnect(self):
        self.conectado = False

    def reconnect_delay_set(self, min_delay, max_delay):
        self.retardo_reconexion = (min_delay, max_delay)


class RelojFalso:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def avanzar(self, segundos):
        self.t += segundos


@pytest.fixture
def entorno():
    cliente = ClienteFalso()
    reloj = RelojFalso()
    pub = PosePublisher(
        MQTTConfig(),
        RateConfig(publish_hz=20.0),
        client_factory=lambda: cliente,
        clock=reloj,
    )
    return pub, cliente, reloj


def _ultimo_payload(cliente):
    return json.loads(cliente.publicaciones[-1]["payload"])


# --------------------------------------------------------------------------
# Forma del payload
# --------------------------------------------------------------------------


def test_publica_un_solo_mensaje_json(entorno):
    pub, cliente, _ = entorno
    assert pub.publish(**POSE) is True
    assert len(cliente.publicaciones) == 1


def test_las_claves_son_exactamente_las_del_contrato(entorno):
    pub, cliente, _ = entorno
    pub.publish(**POSE, emo="feliz", gesture="page_next", page=3)
    assert set(_ultimo_payload(cliente)) == CLAVES_DEL_CONTRATO


def test_los_valores_llegan_intactos(entorno):
    pub, cliente, _ = entorno
    pub.publish(pan=-12.4, tilt=8.1, armL=-20.0, armLtw=15.0, armR=30.5, armRtw=50.2)
    payload = _ultimo_payload(cliente)
    assert payload == {
        "pan": -12.4, "tilt": 8.1, "armL": -20.0,
        "armLtw": 15.0, "armR": 30.5, "armRtw": 50.2,
    }


def test_los_campos_none_se_omiten(entorno):
    """El visor conserva el último valor de los campos ausentes, así que omitir
    es mejor que mandar ceros."""
    pub, cliente, _ = entorno
    pub.publish(pan=10.0)
    assert _ultimo_payload(cliente) == {"pan": 10.0}


def test_sin_emocion_no_hay_clave_emo(entorno):
    pub, cliente, _ = entorno
    pub.publish(**POSE)
    assert "emo" not in _ultimo_payload(cliente)


def test_los_angulos_son_float_no_cadenas(entorno):
    """El legacy publicaba f'{valor}' como texto; el visor exige números."""
    pub, cliente, _ = entorno
    pub.publish(**POSE)
    payload = _ultimo_payload(cliente)
    for clave in ("pan", "tilt", "armL", "armLtw", "armR", "armRtw"):
        assert isinstance(payload[clave], float)


def test_la_pagina_es_entero(entorno):
    pub, cliente, _ = entorno
    pub.publish(page=7)
    assert _ultimo_payload(cliente)["page"] == 7
    assert isinstance(_ultimo_payload(cliente)["page"], int)


def test_los_angulos_se_redondean_sin_cuantizar(entorno):
    """3 decimales acotan el payload; la cuantización que critica el plan
    maestro son pasos de 5° y 10°, no de 0.001°."""
    pub, cliente, _ = entorno
    pub.publish(pan=12.3456789)
    assert _ultimo_payload(cliente)["pan"] == 12.346


# --------------------------------------------------------------------------
# Transporte
# --------------------------------------------------------------------------


def test_publica_en_el_topic_de_pose(entorno):
    pub, cliente, _ = entorno
    pub.publish(**POSE)
    assert cliente.publicaciones[-1]["topic"] == "telmeow/teleop/pose"


def test_usa_qos_0(entorno):
    """QoS 1 con wait_for_publish() bloqueante era el bug de diseño 5."""
    pub, cliente, _ = entorno
    pub.publish(**POSE)
    assert cliente.publicaciones[-1]["qos"] == 0


def test_connect_configura_reconexion_y_arranca_el_loop(entorno):
    pub, cliente, _ = entorno
    pub.connect()
    assert cliente.retardo_reconexion == (1, 30)
    assert cliente.loop_activo is True
    assert cliente.destino == ("localhost", 1883, 60)


def test_gestor_de_contexto_cierra_el_loop(entorno):
    pub, cliente, _ = entorno
    with pub:
        assert cliente.loop_activo is True
    assert cliente.loop_activo is False
    assert cliente.conectado is False


def test_topic_configurable():
    cliente = ClienteFalso()
    pub = PosePublisher(
        MQTTConfig(topic_pose="otro/topic"),
        client_factory=lambda: cliente,
        clock=RelojFalso(),
    )
    pub.publish(pan=0.0)
    assert cliente.publicaciones[-1]["topic"] == "otro/topic"


# --------------------------------------------------------------------------
# Limitación de tasa
# --------------------------------------------------------------------------


def test_descarta_si_se_llama_mas_rapido_que_la_tasa(entorno):
    pub, cliente, reloj = entorno
    assert pub.publish(**POSE) is True
    reloj.avanzar(0.01)  # 100 Hz, muy por encima de 20
    assert pub.publish(**POSE) is False
    assert len(cliente.publicaciones) == 1
    assert pub.dropped_count == 1
    assert pub.message_count == 1


def test_publica_al_cumplirse_el_periodo(entorno):
    pub, _, reloj = entorno
    pub.publish(**POSE)
    reloj.avanzar(0.05)  # exactamente 20 Hz
    assert pub.publish(**POSE) is True
    assert pub.dropped_count == 0


def test_la_primera_llamada_nunca_se_descarta(entorno):
    pub, _, _ = entorno
    assert pub.publish(**POSE) is True


def test_descartar_no_actualiza_la_marca_de_tiempo(entorno):
    """Si un descarte reiniciara el reloj, una ráfaga rápida podría bloquear
    indefinidamente la siguiente publicación legítima."""
    pub, _, reloj = entorno
    pub.publish(**POSE)
    for _ in range(5):
        reloj.avanzar(0.005)
        pub.publish(**POSE)
    reloj.avanzar(0.03)  # 0.055 s desde la última publicación real
    assert pub.publish(**POSE) is True


def test_publicar_a_la_tasa_nominal_no_pierde_mensajes(entorno):
    """Regresión: un emisor que corre a la tasa nominal no debe perder nada.

    Aquí se acumula el periodo a propósito, como hará el bucle real, para que
    el error de punto flotante de 0.05 (no representable en binario) participe
    de la prueba.
    """
    pub, cliente, reloj = entorno
    for _ in range(20):
        pub.publish(**POSE)
        reloj.avanzar(0.05)  # acumulación deliberada, como en el bucle real
    assert pub.dropped_count == 0
    assert len(cliente.publicaciones) == 20


def test_el_reloj_por_defecto_es_de_alta_resolucion():
    """Regresión de la causa real de los descartes espurios.

    El publisher usaba time.monotonic(), que en Windows tiene una resolución de
    ~15.625 ms — comparable al periodo de 50 ms que hay que medir. Los
    intervalos salían cuantizados a múltiplos de tick y algunos se leían como
    31.25 ms, por debajo del umbral. Medido: 9 de 60 mensajes descartados
    publicando a 20 Hz nominales, con el bucle emisor funcionando bien.

    perf_counter es igualmente monótono y tiene resolución de sub-microsegundo.
    """
    assert _DEFAULT_CLOCK is time.perf_counter
    assert time.get_clock_info("perf_counter").monotonic is True
    # Un reloj cuya resolución se acerque al periodo medido vuelve a romperlo.
    assert time.get_clock_info("perf_counter").resolution < 1e-4


def test_la_tolerancia_no_deja_pasar_a_un_emisor_desbocado(entorno):
    """La holgura es del 5 % del periodo, no una barra libre."""
    pub, _, reloj = entorno
    pub.publish(**POSE)
    reloj.avanzar(0.04)  # 25 Hz: un 20 % por encima, fuera de tolerancia
    assert pub.publish(**POSE) is False


def test_tasa_configurable():
    cliente, reloj = ClienteFalso(), RelojFalso()
    pub = PosePublisher(
        MQTTConfig(),
        RateConfig(publish_hz=5.0),
        client_factory=lambda: cliente,
        clock=reloj,
    )
    pub.publish(pan=0.0)
    reloj.avanzar(0.1)
    assert pub.publish(pan=0.0) is False  # 10 Hz > 5 Hz
    reloj.avanzar(0.1)
    assert pub.publish(pan=0.0) is True


# --------------------------------------------------------------------------
# Eventos discretos: la excepción a la limitación
# --------------------------------------------------------------------------


def test_un_gesto_nunca_se_descarta(entorno):
    """Perder un page_next desincroniza el gesto del robot respecto a la página
    que el niño está viendo."""
    pub, cliente, reloj = entorno
    pub.publish(**POSE)
    reloj.avanzar(0.001)
    assert pub.publish(**POSE, gesture="page_next") is True
    assert len(cliente.publicaciones) == 2
    assert pub.dropped_count == 0


def test_una_pagina_nunca_se_descarta(entorno):
    pub, _, reloj = entorno
    pub.publish(**POSE)
    reloj.avanzar(0.001)
    assert pub.publish(page=4) is True


def test_rafaga_de_gestos_pasa_entera(entorno):
    pub, cliente, _ = entorno
    for _ in range(5):
        assert pub.publish(gesture="page_next") is True
    assert len(cliente.publicaciones) == 5


@pytest.mark.parametrize("gesto", sorted(VALID_GESTURES))
def test_gestos_validos(entorno, gesto):
    pub, cliente, _ = entorno
    pub.publish(gesture=gesto)
    assert _ultimo_payload(cliente)["gesture"] == gesto


@pytest.mark.parametrize("gesto", ["page_up", "next", "PAGE_NEXT", ""])
def test_gesto_invalido_falla_de_inmediato(entorno, gesto):
    """Mejor romper en la llamada que publicar algo que el visor ignora en
    silencio."""
    pub, cliente, _ = entorno
    with pytest.raises(ValueError):
        pub.publish(gesture=gesto)
    assert cliente.publicaciones == []


# --------------------------------------------------------------------------
# Telemetría para el HUD
# --------------------------------------------------------------------------


def test_contadores_arrancan_en_cero(entorno):
    pub, _, _ = entorno
    assert (pub.message_count, pub.dropped_count) == (0, 0)
    assert pub.effective_hz == 0.0


def test_tasa_efectiva_en_la_ventana_de_un_segundo(entorno):
    pub, _, reloj = entorno
    # Instantes calculados, no acumulados: sumar 0.05 veinte veces arrastra
    # error de punto flotante y el propio test se vuelve el sujeto de prueba.
    for i in range(20):
        reloj.t = i * 0.05
        assert pub.publish(**POSE) is True
    assert pub.effective_hz == pytest.approx(20.0)


def test_la_tasa_efectiva_decae_al_dejar_de_publicar(entorno):
    pub, _, reloj = entorno
    for _ in range(10):
        pub.publish(**POSE)
        reloj.avanzar(0.05)
    reloj.avanzar(2.0)
    assert pub.effective_hz == 0.0


def test_connected_es_falso_antes_de_conectar(entorno):
    pub, _, _ = entorno
    assert pub.connected is False


# --------------------------------------------------------------------------
# Mensajes que el cliente no logra entregar
# --------------------------------------------------------------------------


def _publisher_que_falla(rc=mqtt.MQTT_ERR_NO_CONN):
    cliente, reloj = ClienteFalso(rc=rc), RelojFalso()
    pub = PosePublisher(MQTTConfig(), client_factory=lambda: cliente, clock=reloj)
    return pub, cliente, reloj


def test_sin_conexion_publish_devuelve_false():
    """Regresión: publish() ignoraba el código de retorno de paho.

    Con QoS 0, un mensaje publicado sin enlace establecido NO se encola: se
    descarta. Medido contra un broker real, los primeros ~40 mensajes de una
    sesión a 20 Hz se perdían mientras el publisher informaba haberlos enviado
    todos. Es el peor modo de fallo posible, porque parece que funciona.
    """
    pub, _, _ = _publisher_que_falla()
    assert pub.publish(**POSE) is False


def test_sin_conexion_no_se_cuentan_como_enviados():
    pub, _, reloj = _publisher_que_falla()
    for _ in range(5):
        pub.publish(**POSE)
        reloj.avanzar(0.05)
    assert pub.message_count == 0
    assert pub.failed_count == 5


def test_los_no_entregados_no_inflan_la_tasa_del_hud():
    """El HUD debe mostrar 0 Hz cuando no llega nada al broker, no 20 Hz."""
    pub, _, _ = _publisher_que_falla()
    pub.publish(**POSE)
    assert pub.effective_hz == 0.0


def test_fallo_y_descarte_por_tasa_son_contadores_distintos():
    pub, cliente, _ = _publisher_que_falla()
    pub.publish(**POSE)  # llega a la red y falla ahí
    pub.publish(**POSE)  # descartado por tasa, ni siquiera lo intenta
    assert pub.failed_count == 1
    assert pub.dropped_count == 1
    assert len(cliente.publicaciones) == 1


def test_un_cliente_sin_codigo_de_retorno_se_considera_exito():
    """Tolerancia con dobles de prueba que devuelven None."""

    class ClienteMudo(ClienteFalso):
        def publish(self, topic, payload, qos=0):
            self.publicaciones.append({"topic": topic, "payload": payload, "qos": qos})
            return None

    cliente = ClienteMudo()
    pub = PosePublisher(
        MQTTConfig(), client_factory=lambda: cliente, clock=RelojFalso()
    )
    assert pub.publish(**POSE) is True
    assert pub.message_count == 1


# --------------------------------------------------------------------------
# Espera de conexión
# --------------------------------------------------------------------------


def test_wait_connected_agota_el_plazo_si_no_hay_enlace(entorno):
    pub, _, _ = entorno
    assert pub.wait_connected(timeout=0.05) is False


def test_wait_connected_devuelve_true_si_ya_hay_enlace(entorno):
    pub, _, _ = entorno
    pub._connected = True
    assert pub.wait_connected(timeout=0.05) is True


# --------------------------------------------------------------------------
# Timestamp opcional
# --------------------------------------------------------------------------


def test_sin_timestamp_por_defecto(entorno):
    """El contrato no lleva 't'; activarlo es una decisión explícita."""
    pub, cliente, _ = entorno
    pub.publish(**POSE)
    assert "t" not in _ultimo_payload(cliente)


def test_timestamp_opcional():
    cliente = ClienteFalso()
    pub = PosePublisher(
        MQTTConfig(),
        client_factory=lambda: cliente,
        clock=RelojFalso(),
        include_timestamp=True,
    )
    pub.publish(**POSE)
    payload = _ultimo_payload(cliente)
    assert isinstance(payload["t"], int)
    assert payload["t"] > 1_700_000_000_000  # milisegundos, no segundos
