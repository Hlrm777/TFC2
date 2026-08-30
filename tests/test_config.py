"""Pruebas de la resolución de configuración.

Lo que más importa verificar aquí es la PRECEDENCIA (defaults < entorno < CLI).
Es fácil escribirla mal de una forma que parece funcionar: si los argumentos de
línea de comandos se registran con valores por defecto normales en vez de
argparse.SUPPRESS, el nivel del entorno queda pisado siempre y nadie lo nota
hasta que una variable de entorno "no hace nada".
"""

import argparse
from pathlib import Path

import pytest

from telmeow_teleop.config import (
    EMOTION_LABELS,
    PROJECT_ROOT,
    AppConfig,
    CameraConfig,
    ConfigError,
    MQTTConfig,
    PathConfig,
    RateConfig,
    add_cli_args,
    load,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="test")
    add_cli_args(parser)
    return parser


# --------------------------------------------------------------------------
# Valores por defecto
# --------------------------------------------------------------------------


def test_defaults_mqtt():
    mqtt = MQTTConfig()
    assert mqtt.host == "localhost"
    assert mqtt.port == 1883
    assert mqtt.ws_port == 9001
    assert mqtt.qos == 0


def test_los_tres_topics_estan_declarados():
    """Cada uno tiene un consumidor distinto: pose -> visor,
    page y story -> servicio de pantalla en la Raspberry."""
    mqtt = MQTTConfig()
    assert mqtt.topic_pose == "telmeow/teleop/pose"
    assert mqtt.topic_page == "telmeow/teleop/page"
    assert mqtt.topic_story == "telmeow/teleop/story"
    assert len({mqtt.topic_pose, mqtt.topic_page, mqtt.topic_story}) == 3


def test_defaults_tasas():
    rates = RateConfig()
    assert (rates.capture_fps, rates.publish_hz, rates.emotion_hz) == (30.0, 20.0, 5.0)
    assert rates.publish_period == pytest.approx(0.05)
    assert rates.emotion_period == pytest.approx(0.2)


def test_defaults_camara():
    camera = CameraConfig()
    assert camera.index == 0
    assert (camera.width, camera.height) == (640, 480)
    assert camera.flip is True


def test_orden_de_emociones_es_el_del_entrenamiento():
    """No es alfabético: es el orden con el que se entrenó el modelo. Alterarlo
    degrada las predicciones en silencio."""
    assert EMOTION_LABELS == ("feliz", "triste", "sorprendido", "neutral", "enojado")


# --------------------------------------------------------------------------
# Entorno
# --------------------------------------------------------------------------


def test_entorno_sobrescribe_defaults():
    cfg = AppConfig.from_env(
        {"TELMEOW_MQTT_HOST": "192.168.1.50", "TELMEOW_PUBLISH_HZ": "25"}
    )
    assert cfg.mqtt.host == "192.168.1.50"
    assert cfg.rates.publish_hz == 25.0
    assert cfg.mqtt.port == 1883  # sin tocar


def test_entorno_vacio_da_los_defaults():
    assert AppConfig.from_env({}) == AppConfig()


@pytest.mark.parametrize("valor, esperado", [("true", True), ("0", False), ("sí", True), ("no", False)])
def test_entorno_booleano(valor, esperado):
    cfg = AppConfig.from_env({"TELMEOW_CAMERA_FLIP": valor})
    assert cfg.camera.flip is esperado


@pytest.mark.parametrize(
    "variable, valor",
    [
        ("TELMEOW_MQTT_PORT", "no-es-un-numero"),
        ("TELMEOW_PUBLISH_HZ", "rapido"),
        ("TELMEOW_CAMERA_FLIP", "quizas"),
    ],
)
def test_entorno_invalido_falla_en_vez_de_caer_al_default(variable, valor):
    """Una variable mal escrita que se ignora en silencio es muy difícil de
    diagnosticar después."""
    with pytest.raises(ConfigError) as exc:
        AppConfig.from_env({variable: valor})
    assert variable in str(exc.value)


# --------------------------------------------------------------------------
# Precedencia
# --------------------------------------------------------------------------


def test_cli_gana_al_entorno():
    cfg = load(
        argv=["--mqtt-host", "10.0.0.5"],
        env={"TELMEOW_MQTT_HOST": "192.168.1.50"},
        parser=_parser(),
    )
    assert cfg.mqtt.host == "10.0.0.5"


def test_el_entorno_sobrevive_cuando_el_cli_no_lo_toca():
    """La trampa del SUPPRESS: sin él, los defaults del parser pisarían al
    entorno y este test fallaría."""
    cfg = load(
        argv=["--camera", "2"],
        env={"TELMEOW_MQTT_HOST": "192.168.1.50", "TELMEOW_PUBLISH_HZ": "25"},
        parser=_parser(),
    )
    assert cfg.camera.index == 2          # del CLI
    assert cfg.mqtt.host == "192.168.1.50"  # del entorno, intacto
    assert cfg.rates.publish_hz == 25.0     # del entorno, intacto
    assert cfg.rates.capture_fps == 30.0    # default, intacto


def test_sin_argumentos_ni_entorno_todo_queda_por_defecto():
    assert load(argv=[], env={}, parser=_parser()) == AppConfig()


def test_cli_cubre_las_tres_secciones_a_la_vez():
    cfg = load(
        argv=["--mqtt-port", "8883", "--emotion-hz", "10", "--camera-width", "1280"],
        env={},
        parser=_parser(),
    )
    assert cfg.mqtt.port == 8883
    assert cfg.rates.emotion_hz == 10.0
    assert cfg.camera.width == 1280
    assert cfg.camera.height == 480


def test_no_flip():
    assert load(argv=[], env={}, parser=_parser()).camera.flip is True
    assert load(argv=["--no-flip"], env={}, parser=_parser()).camera.flip is False


def test_argumentos_ajenos_no_rompen_la_resolucion():
    """main.py añadirá flags propios (--record, --no-emotion). from_args debe
    ignorar lo que no sea configuración."""
    parser = _parser()
    parser.add_argument("--record", default=argparse.SUPPRESS)
    cfg = load(argv=["--record", "salida.csv", "--mqtt-port", "1884"], env={}, parser=parser)
    assert cfg.mqtt.port == 1884


# --------------------------------------------------------------------------
# Rutas
# --------------------------------------------------------------------------


def test_ruta_del_modelo_por_defecto_es_absoluta():
    paths = PathConfig()
    assert paths.emotion_model.is_absolute()
    assert paths.emotion_model == PROJECT_ROOT / "emotion_model.keras"


def test_el_cascade_por_defecto_es_el_de_opencv():
    """None significa 'usa cv2.data.haarcascades', que es el que usaron los tres
    scripts legacy.

    La copia que había en la raíz del repositorio NO es el mismo archivo
    (963.441 bytes frente a 930.127) y no la usaba ningún script. Si se tomara
    como predeterminada cambiaría el recorte de rostros respecto al
    entrenamiento, y con él las métricas que el PROMPT 9 debe reproducir. Está
    en legacy/ por eso.
    """
    assert PathConfig().face_cascade is None


def test_el_cascade_legacy_se_puede_forzar():
    cfg = AppConfig.from_env(
        {"TELMEOW_CASCADE_PATH": "legacy/haarcascade_frontalface_default.xml"}
    )
    esperado = PROJECT_ROOT / "legacy" / "haarcascade_frontalface_default.xml"
    assert cfg.paths.face_cascade == esperado


def test_el_cascade_huerfano_esta_en_legacy():
    """Fija dónde vive, para que nadie lo devuelva a la raíz por descuido."""
    assert (PROJECT_ROOT / "legacy" / "haarcascade_frontalface_default.xml").is_file()
    assert not (PROJECT_ROOT / "haarcascade_frontalface_default.xml").exists()


def test_ruta_relativa_se_resuelve_contra_la_raiz_no_contra_el_cwd():
    """Los scripts legacy usaban 'emotion_model.keras' a secas, que se rompe en
    cuanto el programa se lanza desde otra carpeta."""
    paths = PathConfig(emotion_model=Path("modelos/otro.keras"))
    assert paths.emotion_model == PROJECT_ROOT / "modelos" / "otro.keras"


def test_ruta_absoluta_se_respeta():
    absoluta = Path(PROJECT_ROOT.anchor) / "tmp" / "modelo.keras"
    assert PathConfig(emotion_model=absoluta).emotion_model == absoluta


def test_ruta_por_entorno():
    cfg = AppConfig.from_env({"TELMEOW_MODEL_PATH": "emotion_model_fer2013.keras"})
    assert cfg.paths.emotion_model == PROJECT_ROOT / "emotion_model_fer2013.keras"


def test_ruta_por_cli():
    cfg = load(argv=["--model", "otro.keras"], env={}, parser=_parser())
    assert cfg.paths.emotion_model == PROJECT_ROOT / "otro.keras"


def test_project_root_apunta_a_la_carpeta_del_codigo():
    assert (PROJECT_ROOT / "telmeow_teleop" / "config.py").is_file()
    assert (PROJECT_ROOT / "legacy").is_dir()


# --------------------------------------------------------------------------
# Inmutabilidad
# --------------------------------------------------------------------------


def test_la_configuracion_es_inmutable():
    cfg = AppConfig()
    with pytest.raises(Exception):
        cfg.mqtt.host = "otro"  # type: ignore[misc]
