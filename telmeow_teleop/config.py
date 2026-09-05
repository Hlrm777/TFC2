"""Parámetros de ejecución del pipeline de teleoperación.

Centraliza lo que en los scripts legacy estaba disperso como literales dentro
de cada archivo: dirección del broker, topics, tasas de muestreo, índice de
cámara y rutas de los modelos.

PRECEDENCIA
-----------
Los valores se resuelven en tres niveles, de menor a mayor prioridad::

    valores por defecto  <  variables de entorno  <  argumentos de línea de comandos

El prefijo de las variables de entorno es ``TELMEOW_``.

DEPENDENCIAS
------------
Solo biblioteca estándar. Ni ``cv2`` ni ``tensorflow`` se importan aquí, para
que el módulo (y sus pruebas) puedan cargarse sin el stack de visión completo.
El nombre del backend de cámara viaja como cadena y el módulo de captura lo
traduce a la constante ``cv2.CAP_*`` correspondiente.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

__all__ = [
    "ConfigError",
    "PROJECT_ROOT",
    "EMOTION_LABELS",
    "MQTTConfig",
    "RateConfig",
    "CameraConfig",
    "PathConfig",
    "HeadConfig",
    "AppConfig",
    "add_cli_args",
    "load",
]

#: Raíz del proyecto (la carpeta que contiene al paquete). Las rutas relativas
#: se resuelven contra ella y no contra el directorio de trabajo: los scripts
#: legacy usaban 'emotion_model.keras' a secas, que se rompe en cuanto se lanza
#: el programa desde otra carpeta.
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

#: Orden de clases del clasificador de emociones. NO es alfabético: es el orden
#: con el que se entrenó el modelo, y alterarlo degrada las predicciones en
#: silencio. Verificado idéntico en los tres scripts legacy
#: (Creation_of_dataset.py, Training Emotion Recognition.py, Emotion Recognition.py).
EMOTION_LABELS: tuple[str, ...] = (
    "feliz",
    "triste",
    "sorprendido",
    "neutral",
    "enojado",
)

ENV_PREFIX = "TELMEOW_"


class ConfigError(ValueError):
    """Un valor de configuración no se pudo interpretar.

    Se lanza en vez de caer al valor por defecto: una variable de entorno mal
    escrita que se ignora en silencio es muy difícil de diagnosticar después.
    """


# --------------------------------------------------------------------------
# Lectura del entorno
# --------------------------------------------------------------------------

_TRUE = {"1", "true", "yes", "y", "on", "si", "sí"}
_FALSE = {"0", "false", "no", "n", "off"}


def _env_str(env: Mapping[str, str], name: str, default: str) -> str:
    return env.get(ENV_PREFIX + name, default)


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(ENV_PREFIX + name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(
            f"{ENV_PREFIX + name} debe ser un entero, se recibió {raw!r}"
        ) from exc


def _env_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(ENV_PREFIX + name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(
            f"{ENV_PREFIX + name} debe ser un número, se recibió {raw!r}"
        ) from exc


def _env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(ENV_PREFIX + name)
    if raw is None:
        return default
    key = raw.strip().lower()
    if key in _TRUE:
        return True
    if key in _FALSE:
        return False
    raise ConfigError(
        f"{ENV_PREFIX + name} debe ser booleano (true/false), se recibió {raw!r}"
    )


def _env_path(env: Mapping[str, str], name: str, default: Path) -> Path:
    raw = env.get(ENV_PREFIX + name)
    return default if raw is None else Path(raw)


def _resolve(path: Path) -> Path:
    """Resuelve una ruta relativa contra la raíz del proyecto."""
    return path if path.is_absolute() else (PROJECT_ROOT / path)


# --------------------------------------------------------------------------
# Secciones de configuración
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MQTTConfig:
    """Enlace con el broker.

    Mosquitto necesita DOS listeners: TCP en 1883 para los clientes Python y
    WebSocket en 9001 para el visor, porque el navegador no puede hablar TCP
    crudo. ``ws_port`` se guarda aquí solo para poder documentarlo y mostrarlo
    en la interfaz; el publisher de Python usa ``port``.

    LOS TRES TOPICS Y SUS CONSUMIDORES
    ----------------------------------
    Están separados a propósito, porque los consume hardware distinto y a
    ritmos muy distintos:

    ``topic_pose``  -- 'telmeow/teleop/pose'
        Lo consume EL VISOR 3D (telmeow_viewer.html), y es el único al que se
        suscribe. Lleva los 6 ángulos, ``emo``, ``gesture``
        (page_next/page_prev) y opcionalmente un ``page`` numérico que el visor
        usa solo para mantener sincronizada la maqueta de la pantalla externa
        que dibuja en 3D. Telemetría continua a 20 Hz.

    ``topic_page``  -- 'telmeow/teleop/page'
        Lo consume EL SERVICIO DE PANTALLA EXTERNA en la Raspberry Pi
        (PROMPT 11), que es quien realmente renderiza el PDF en el monitor de
        15.6". Evento discreto y poco frecuente.

    ``topic_story`` -- 'telmeow/teleop/story'
        También lo consume el servicio de la Raspberry (PROMPT 11): anuncia qué
        cuento cargar y pre-renderizar.

    El motivo de no mezclarlos: el gesto de cabeza es telemetría de pose a
    20 Hz, mientras que el cambio de página es un evento dirigido a otro
    dispositivo. Publicarlo todo junto obligaría a la Raspberry a parsear 20
    mensajes por segundo para atender uno cada varios segundos.

    El PANEL DEL OPERADOR (PROMPT 10) publica en ``topic_pose`` y ``topic_page``
    desde el MISMO evento, para que el gesto del robot y el cambio de página
    queden sincronizados.

    Aquí solo se declaran las constantes; page y story se implementan en los
    PROMPTS 10 y 11.
    """

    host: str = "localhost"
    port: int = 1883
    ws_port: int = 9001
    topic_pose: str = "telmeow/teleop/pose"
    topic_page: str = "telmeow/teleop/page"
    topic_story: str = "telmeow/teleop/story"
    qos: int = 0
    keepalive: int = 60
    client_id: str = "telmeow-teleop"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "MQTTConfig":
        return cls(
            host=_env_str(env, "MQTT_HOST", cls.host),
            port=_env_int(env, "MQTT_PORT", cls.port),
            ws_port=_env_int(env, "MQTT_WS_PORT", cls.ws_port),
            topic_pose=_env_str(env, "MQTT_TOPIC_POSE", cls.topic_pose),
            topic_page=_env_str(env, "MQTT_TOPIC_PAGE", cls.topic_page),
            topic_story=_env_str(env, "MQTT_TOPIC_STORY", cls.topic_story),
            qos=_env_int(env, "MQTT_QOS", cls.qos),
            keepalive=_env_int(env, "MQTT_KEEPALIVE", cls.keepalive),
            client_id=_env_str(env, "MQTT_CLIENT_ID", cls.client_id),
        )


@dataclass(frozen=True)
class RateConfig:
    """Tasas de los tres bloques, desacopladas entre sí.

    La emoción no cambia a 30 Hz y el crossfade del visor dura 350 ms, así que
    inferirla por frame solo gasta CPU.
    """

    capture_fps: float = 30.0
    publish_hz: float = 20.0
    emotion_hz: float = 5.0

    @property
    def capture_period(self) -> float:
        return 1.0 / self.capture_fps

    @property
    def publish_period(self) -> float:
        return 1.0 / self.publish_hz

    @property
    def emotion_period(self) -> float:
        return 1.0 / self.emotion_hz

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "RateConfig":
        return cls(
            capture_fps=_env_float(env, "CAPTURE_FPS", cls.capture_fps),
            publish_hz=_env_float(env, "PUBLISH_HZ", cls.publish_hz),
            emotion_hz=_env_float(env, "EMOTION_HZ", cls.emotion_hz),
        )


@dataclass(frozen=True)
class CameraConfig:
    """Captura de vídeo.

    ``backend`` se guarda como cadena ('dshow', 'msmf', 'any') para no importar
    cv2 desde este módulo. El valor por defecto es DirectShow porque es lo que
    usan los scripts legacy en Windows.

    ``flip`` voltea la imagen horizontalmente para que el operador se vea como
    en un espejo.


    ``fourcc`` es el formato en que la cámara entrega los frames, y **no es un
    detalle**: por USB, muchas webcams solo dan 30 fps a 1280x720 si comprimen
    en MJPG. Sin comprimir (YUY2, el formato por defecto de DirectShow) esa
    resolución ocupa demasiado ancho de banda y la cámara baja a 10 fps sin
    avisar de nada. Medido en este proyecto: exactamente 10.0 fps a 1280x720
    antes de pedir MJPG.

    Cadena vacía deja el formato que elija el backend.
    """

    index: int = 0
    width: int = 640
    height: int = 480
    fps: int = 30
    flip: bool = True
    backend: str = "dshow"
    fourcc: str = "MJPG"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "CameraConfig":
        return cls(
            index=_env_int(env, "CAMERA_INDEX", cls.index),
            width=_env_int(env, "CAMERA_WIDTH", cls.width),
            height=_env_int(env, "CAMERA_HEIGHT", cls.height),
            fps=_env_int(env, "CAMERA_FPS", cls.fps),
            flip=_env_bool(env, "CAMERA_FLIP", cls.flip),
            backend=_env_str(env, "CAMERA_BACKEND", cls.backend),
            fourcc=_env_str(env, "CAMERA_FOURCC", cls.fourcc),
        )


@dataclass(frozen=True)
class HeadConfig:
    """Mapeo de la cabeza del operador a la del robot.

    Las **ganancias** son el multiplicador entre cuánto gira el operador y
    cuánto gira el robot: con 1.0 lo copia, con 2.0 lo exagera, con 0.5 lo
    atenúa. Van separadas por eje porque los rangos no se parecen y porque la
    estimación no tiene la misma sensibilidad en los dos.

    El tilt lleva **dos ganancias, una por sentido**, porque hay dos asimetrías
    que se suman: el robot llega menos hacia abajo (−39° frente a +42°) y la
    estimación también se queda más corta hacia abajo. Con un único valor habría
    que elegir entre pasarse por arriba o no llegar nunca por abajo — bajar la
    barbilla resultaba casi imposible de alcanzar en la prueba con el operador.

    Ambas suelen necesitar ser mayores que 1: el cabeceo se estima a partir de
    la profundidad de los landmarks, que MediaPipe entrega comprimida. El demo
    del head tracker calcula en vivo las ganancias que harían falta — ver
    ``python -m telmeow_teleop.head_tracker``.

    Amplificar tiene un precio: multiplica también el ruido de la estimación y
    hace llegar antes al tope mecánico.
    """

    method: str = "mesh"
    gain_pan: float = 1.15
    gain_tilt_up: float = 1.0
    gain_tilt_down: float = 1.0
    invert_pan: bool = False
    invert_tilt: bool = False
    min_cutoff: float = 0.5
    beta: float = 0.5

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "HeadConfig":
        return cls(
            method=_env_str(env, "HEAD_METHOD", cls.method),
            gain_pan=_env_float(env, "HEAD_GAIN_PAN", cls.gain_pan),
            gain_tilt_up=_env_float(env, "HEAD_GAIN_TILT_UP", cls.gain_tilt_up),
            gain_tilt_down=_env_float(env, "HEAD_GAIN_TILT_DOWN", cls.gain_tilt_down),
            invert_pan=_env_bool(env, "HEAD_INVERT_PAN", cls.invert_pan),
            invert_tilt=_env_bool(env, "HEAD_INVERT_TILT", cls.invert_tilt),
            min_cutoff=_env_float(env, "HEAD_MIN_CUTOFF", cls.min_cutoff),
            beta=_env_float(env, "HEAD_BETA", cls.beta),
        )


@dataclass(frozen=True)
class PathConfig:
    """Rutas de modelos y recursos. Las relativas se resuelven contra
    ``PROJECT_ROOT``.

    ``face_cascade`` vale ``None`` por defecto, y eso significa **usar el
    cascade que trae OpenCV** (``cv2.data.haarcascades``). No es un descuido:

    Los tres scripts legacy -- el de captura del dataset, el de entrenamiento y
    el de inferencia -- usan ``cv2.data.haarcascades``. Había además una copia
    de ``haarcascade_frontalface_default.xml`` en la raíz del repositorio que no
    usaba ninguno de ellos, y que **no es el mismo archivo**: 963.441 bytes
    frente a los 930.127 del que distribuye OpenCV 4.10. Son dos versiones
    distintas del cascade. Esa copia se movió a ``legacy/`` para que no se tome
    por buena.

    Como el modelo de emociones se entrenó con los rostros que recortaba el
    cascade de OpenCV, usar el otro cambiaría el preprocesamiento y con él las
    métricas -- justo lo que el PROMPT 9 necesita poder reproducir.

    Se deja como ``None`` en vez de resolverlo aquí para que este módulo siga
    sin importar cv2. Quien lo consuma hace::

        ruta = cfg.paths.face_cascade or (
            Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")

    ``TELMEOW_CASCADE_PATH`` permite apuntar a otro archivo -- por ejemplo el de
    ``legacy/`` -- si alguna vez hace falta comparar.
    """

    emotion_model: Path = field(
        default_factory=lambda: PROJECT_ROOT / "emotion_model.keras"
    )
    face_cascade: Path | None = None

    def __post_init__(self) -> None:
        # Frozen: hay que saltarse el __setattr__ bloqueado para normalizar.
        object.__setattr__(self, "emotion_model", _resolve(Path(self.emotion_model)))
        if self.face_cascade is not None:
            object.__setattr__(self, "face_cascade", _resolve(Path(self.face_cascade)))

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "PathConfig":
        default = cls()
        cascade = env.get(ENV_PREFIX + "CASCADE_PATH")
        return cls(
            emotion_model=_env_path(env, "MODEL_PATH", default.emotion_model),
            face_cascade=Path(cascade) if cascade else None,
        )


@dataclass(frozen=True)
class AppConfig:
    """Configuración completa del pipeline."""

    mqtt: MQTTConfig = field(default_factory=MQTTConfig)
    rates: RateConfig = field(default_factory=RateConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    paths: PathConfig = field(default_factory=PathConfig)
    head: HeadConfig = field(default_factory=HeadConfig)
    log_level: str = "INFO"
    balanceo_policy: str = "table"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AppConfig":
        """Construye la configuración desde un mapping de entorno.

        El mapping se recibe como argumento (en vez de leer ``os.environ``
        directamente) para que las pruebas no necesiten monkeypatch.
        """
        env = os.environ if env is None else env
        return cls(
            mqtt=MQTTConfig.from_env(env),
            rates=RateConfig.from_env(env),
            camera=CameraConfig.from_env(env),
            paths=PathConfig.from_env(env),
            head=HeadConfig.from_env(env),
            log_level=_env_str(env, "LOG_LEVEL", cls.log_level),
            balanceo_policy=_env_str(env, "BALANCEO_POLICY", cls.balanceo_policy),
        )


# --------------------------------------------------------------------------
# Línea de comandos
# --------------------------------------------------------------------------

#: destino argparse -> (sección de AppConfig o None, campo)
_CLI_MAP: dict[str, tuple[str | None, str]] = {
    "mqtt_host": ("mqtt", "host"),
    "mqtt_port": ("mqtt", "port"),
    "mqtt_ws_port": ("mqtt", "ws_port"),
    "mqtt_topic_pose": ("mqtt", "topic_pose"),
    "mqtt_topic_page": ("mqtt", "topic_page"),
    "mqtt_topic_story": ("mqtt", "topic_story"),
    "mqtt_qos": ("mqtt", "qos"),
    "mqtt_keepalive": ("mqtt", "keepalive"),
    "mqtt_client_id": ("mqtt", "client_id"),
    "capture_fps": ("rates", "capture_fps"),
    "publish_hz": ("rates", "publish_hz"),
    "emotion_hz": ("rates", "emotion_hz"),
    "camera_index": ("camera", "index"),
    "camera_width": ("camera", "width"),
    "camera_height": ("camera", "height"),
    "camera_flip": ("camera", "flip"),
    "camera_backend": ("camera", "backend"),
    "emotion_model": ("paths", "emotion_model"),
    "face_cascade": ("paths", "face_cascade"),
    "log_level": (None, "log_level"),
    "balanceo_policy": (None, "balanceo_policy"),
}


def add_cli_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Registra los argumentos de configuración en un parser.

    Todos usan ``default=argparse.SUPPRESS``, de modo que el Namespace
    resultante solo contenga lo que el usuario escribió realmente. Sin eso los
    valores por defecto del parser pisarían siempre a las variables de entorno
    y el nivel intermedio de la precedencia quedaría muerto.
    """
    sup = argparse.SUPPRESS

    grp = parser.add_argument_group("MQTT")
    grp.add_argument("--mqtt-host", dest="mqtt_host", default=sup)
    grp.add_argument("--mqtt-port", dest="mqtt_port", type=int, default=sup)
    grp.add_argument("--mqtt-ws-port", dest="mqtt_ws_port", type=int, default=sup)
    grp.add_argument("--mqtt-topic-pose", dest="mqtt_topic_pose", default=sup)
    grp.add_argument("--mqtt-topic-page", dest="mqtt_topic_page", default=sup)
    grp.add_argument("--mqtt-topic-story", dest="mqtt_topic_story", default=sup)
    grp.add_argument("--mqtt-qos", dest="mqtt_qos", type=int, choices=(0, 1, 2), default=sup)
    grp.add_argument("--mqtt-keepalive", dest="mqtt_keepalive", type=int, default=sup)
    grp.add_argument("--mqtt-client-id", dest="mqtt_client_id", default=sup)

    grp = parser.add_argument_group("Tasas")
    grp.add_argument("--capture-fps", dest="capture_fps", type=float, default=sup)
    grp.add_argument("--publish-hz", dest="publish_hz", type=float, default=sup)
    grp.add_argument("--emotion-hz", dest="emotion_hz", type=float, default=sup)

    grp = parser.add_argument_group("Cámara")
    grp.add_argument("--camera", dest="camera_index", type=int, default=sup)
    grp.add_argument("--camera-width", dest="camera_width", type=int, default=sup)
    grp.add_argument("--camera-height", dest="camera_height", type=int, default=sup)
    grp.add_argument(
        "--camera-backend",
        dest="camera_backend",
        choices=("dshow", "msmf", "any"),
        default=sup,
    )
    grp.add_argument(
        "--no-flip",
        dest="camera_flip",
        action="store_false",
        default=sup,
        help="No voltear la imagen horizontalmente (por defecto sí se voltea).",
    )

    grp = parser.add_argument_group("Rutas")
    grp.add_argument("--model", dest="emotion_model", type=Path, default=sup)
    grp.add_argument("--cascade", dest="face_cascade", type=Path, default=sup)

    grp = parser.add_argument_group("General")
    grp.add_argument(
        "--log-level",
        dest="log_level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default=sup,
    )
    grp.add_argument(
        "--balanceo-policy",
        dest="balanceo_policy",
        choices=("table", "intersection"),
        default=sup,
    )
    return parser


def from_args(args: argparse.Namespace, base: AppConfig) -> AppConfig:
    """Aplica sobre ``base`` únicamente los argumentos presentes en el
    Namespace (ver ``add_cli_args``)."""
    overrides = vars(args)
    sections: dict[str, dict[str, object]] = {}
    top: dict[str, object] = {}

    for dest, value in overrides.items():
        target = _CLI_MAP.get(dest)
        if target is None:
            continue  # argumento ajeno a la configuración (p. ej. --record)
        section, attr = target
        if section is None:
            top[attr] = value
        else:
            sections.setdefault(section, {})[attr] = value

    config = base
    for section, changes in sections.items():
        config = replace(config, **{section: replace(getattr(config, section), **changes)})
    if top:
        config = replace(config, **top)
    return config


def load(
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    parser: argparse.ArgumentParser | None = None,
) -> AppConfig:
    """Resuelve la configuración completa: defaults < entorno < CLI."""
    base = AppConfig.from_env(env)
    if parser is None:
        parser = argparse.ArgumentParser(prog="telmeow-teleop")
        add_cli_args(parser)
    return from_args(parser.parse_args(argv), base)


if __name__ == "__main__":
    import json

    cfg = load()
    print(
        json.dumps(
            {
                "mqtt": cfg.mqtt.__dict__,
                "rates": cfg.rates.__dict__,
                "camera": cfg.camera.__dict__,
                "paths": {k: str(v) for k, v in cfg.paths.__dict__.items()},
                "log_level": cfg.log_level,
                "balanceo_policy": cfg.balanceo_policy,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
