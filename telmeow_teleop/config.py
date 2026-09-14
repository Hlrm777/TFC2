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
    "ArmConfig",
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
    y **van en el mismo sentido, así que se suman**: el robot llega menos hacia
    abajo (−39° frente a +42°) y el operador también se queda más corto hacia
    abajo. Medido con el protocolo del P4.2 en dos sesiones independientes:
    26.5° arriba frente a 21.6° abajo con la cámara integrada, y 31.0° frente a
    19.2° con la externa. Con un único valor habría que elegir entre pasarse por
    arriba o no llegar nunca por abajo, que era la queja original del operador.

    Ambas suelen necesitar ser mayores que 1: el cabeceo se estima a partir de
    la profundidad de los landmarks, que MediaPipe entrega comprimida. El demo
    del head tracker calcula en vivo las ganancias que harían falta — ver
    ``python -m telmeow_teleop.head_tracker``.

    Amplificar tiene un precio: multiplica también el ruido de la estimación y
    hace llegar antes al tope mecánico. Con estas ganancias, el temblor que
    recibe el robot es de **0.09° en pan y 0.25° en tilt**, sobre rangos de ±50°
    y −39/+42°.

    LOS VALORES POR DEFECTO ESTÁN MEDIDOS, no elegidos. Salen de la sesión del
    2026-09-06 con el protocolo del P4.2 —girar hasta que la pantalla deja de
    leerse con comodidad y sostener la postura— analizada con
    ``tools/analizar_registro.py``, en la configuración de operación: cámara
    integrada a 640x480. Una repetición con la cámara externa da 1.69 / 1.35 /
    2.03, y esa dispersión de ±15 % es la repetibilidad de la calibración.

    **Son de ESTE operador y de ESTA geometría de pantalla.** Dependen de a qué
    distancia se sienta y de cuánto ocupa el monitor en su campo de visión, no
    de la cámara ni del robot. Otro operador, u otro puesto, exige repetir el
    protocolo.

    ``invert_pan`` VIENE ACTIVADO, y el motivo es de diseño de interacción, no
    de visión. Con él, el operador girando a **su** derecha hace que el robot
    gire a **su propia** derecha: el robot reproduce el gesto tal como lo haría
    una persona en el lugar del operador. Es la convención que corresponde al
    robot físico y a los servos, y la que hay que declarar en el documento.

    El precio es que, mirando al robot **de frente** en el visor, la cabeza
    parece moverse al revés que la del operador — igual que le pasa a cualquiera
    que mire a otra persona de frente. Desde atrás, o en primera persona, se ve
    natural. Si algún día el operador supervisa de frente y prefiere el mapeo
    especular, esto es lo único que hay que cambiar; no es un cambio de diseño.
    """

    method: str = "mesh"
    gain_pan: float = 1.49
    gain_tilt_up: float = 1.59
    gain_tilt_down: float = 1.80
    invert_pan: bool = True
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
class ArmConfig:
    """Mapeo de los brazos del operador a los del robot.

    QUE MIDE CADA CANAL. El hombro del robot es un cardán de dos grados de
    libertad que orienta un brazo rígido sin codo. Los nombres del contrato MQTT
    son heredados y uno de ellos miente -- ``armLtw`` sugiere *twist*, o sea
    torsión, pero el canal es flexión. Ver el docstring de ``limits.py``:

    - ``armL`` / ``armR``     -> abducción (+) / aducción (−), plano frontal.
    - ``armLtw`` / ``armRtw`` -> flexión (+) / extensión (−), plano sagital.

    **El robot no tiene rotación interna/externa**, así que esa componente del
    gesto no se reproduce (H28 del plan maestro).

    ``target`` ELIGE EL PUNTO DE DESTINO del vector que orienta el brazo:

    ``"mano"``
        Centroide de los cuatro landmarks de la mano que BlazePose entrega
        (muñeca, meñique, índice y pulgar). Es el valor por defecto y es un
        **retargeting deliberado**, no el ángulo anatómico del hombro: el brazo
        del robot es un eslabón rígido único, así que lo que hay que conservar
        es hacia dónde apunta el brazo completo del operador. Además, sentado a
        la mesa con la cámara integrada, la mano se ve mucho más fiablemente que
        el codo.
    ``"codo"``
        Vector húmero (hombro→codo). Es el ángulo articular del hombro en
        sentido estricto. Se conserva para poder comparar los dos con datos
        sobre los mismos frames, igual que ``head_tracker`` conserva ``pnp``
        junto a ``mesh``.

    Consecuencia a declarar con ``"mano"``: la flexión de codo del operador no
    tiene contrapartida en el robot y no se reproduce; con el codo doblado, la
    mano del robot queda más lejos que la del operador porque su eslabón no se
    acorta.

    ``mirror`` DECIDE QUE BRAZO MANDA SOBRE CUAL. Con ``True`` (por defecto), el
    brazo izquierdo **físico** del operador mueve el brazo izquierdo del robot.
    Es la misma convención que ``HeadConfig.invert_pan``: el robot reproduce la
    pose en **su propio** marco, que es lo que llega a los servos. El precio,
    igual que con la cabeza, es que mirando al robot de frente en el visor se ve
    especular.

    Ojo con el detalle que lo hace no trivial: el frame se voltea
    horizontalmente antes de estimar, así que las etiquetas ``LEFT_*`` de
    BlazePose corresponden al lado **físicamente opuesto** del operador.

    LAS DOS BANDERAS DE INVERSION VIENEN DESACTIVADAS, y ahora eso está
    **verificado en el visor** (2026-09-07), no deducido. El estimador
    reproduce la cinemática inversa exacta del cardán del robot, en su mismo
    orden y con sus mismos ejes, así que lo que sale de él ya son los valores
    articulares del robot: no hay nada que invertir.

    Se conservan configurables igualmente, porque son la palanca barata si
    alguna vez se rehace el rig, y porque en este proyecto ya hubo dos errores
    de signo que pasaron todas las pruebas en verde (H17 y H22).

    ⚠️ **Cuidado con «hacia adelante» como si fuera una propiedad del signo.**
    El comentario del rig del visor dice que ``+armLtw`` lleva el brazo hacia
    adelante, y eso **no puede ser cierto ni falso globalmente**: la componente
    antero-posterior del brazo vale ``sin(abduccion)·sin(flexion)``, así que su
    sentido **se invierte según el brazo esté por encima o por debajo de la
    horizontal**. Con el brazo bajado 50°, una flexión negativa va hacia
    adelante; con el brazo subido 20°, la que va hacia adelante es la positiva.
    Medido en el visor y reproducido por la transcripción del rig. Ver H30.

    Las dos banderas se comparten entre los dos brazos porque la convención del
    robot ya es simétrica —las tablas de ``limits.py`` usan el mismo sentido en
    los dos lados—, y compartirlas reduce a la mitad la superficie del fallo.

    LAS GANANCIAS VALEN 1.0 y están pendientes de calibrar, pero ya se sabe que
    en **abducción van a hacer falta** y que en **flexión no**. Medido el
    2026-09-13 sobre 13.271 frames: el recorrido de abducción del operador
    (±100° y más) desborda por completo el del robot (−55…+32), que salió
    recortado por el tope en el 67-75 % de los frames; la propuesta provisional
    es ~0.33 en abducción y ~0.51 en aducción. En flexión los dos coinciden
    (1.04/1.06).

    El motivo es el **H30** y no tiene arreglo mecánico: el cero del robot está
    en la horizontal, así que su recorrido de abducción es una fracción del de
    un hombro humano. Es distinto del motivo de la cabeza —allí la ganancia
    existe porque el operador no puede girar la cabeza sin perder de vista el
    monitor— pero el efecto es el mismo.

    El valor definitivo sale de una sesión con protocolo; el que se cite tiene
    que venir de ahí y no de la grabación descartada.

    ``min_visibility`` es el umbral de la puntuación de visibilidad de BlazePose
    por debajo del cual un landmark se considera no observado. Un brazo cuyos
    landmarks no lleguen al umbral se descarta **por separado**, sin arrastrar
    al otro.

    QUE PASA CUANDO SE PIERDE UN BRAZO
    ----------------------------------
    Las manos salen y entran del cuadro constantemente, sobre todo sentado a una
    mesa con la cámara integrada. Omitir el canal deja el brazo del robot
    **clavado a mitad de gesto**, que a un espectador le parece un cuelgue; y
    devolverlo al reposo de golpe es peor. Así que hay tres parámetros:

    - ``gracia_s``: cuánto se mantiene el último objetivo antes de empezar a
      volver. Absorbe los parpadeos de visibilidad, que son la mayoría.
    - ``reposo_abduccion`` / ``reposo_flexion``: la pose a la que se vuelve.
      **El reposo del robot no es su cero**: su cero son los brazos en cruz
      (H30), así que el reposo por defecto es −55° de abducción, lo más bajo que
      llega su mecanismo. Pasa por ``clamp_arm``, así que no puede pedir una
      postura imposible.
    - ``velocidad_max``: velocidad angular máxima del comando, en grados por
      segundo. Convierte cualquier salto en una rampa de duración conocida
      (``salto / velocidad_max``: los 55° del reposo tardan ~0.3 s con el valor
      por defecto). Se elige **por encima de la velocidad del gesto narrado**
      para que solo actúe en las discontinuidades; por debajo de ella
      introduciría retardo, y la latencia es una métrica del proyecto.
    """

    target: str = "mano"
    mirror: bool = True
    invert_abduccion: bool = False
    invert_flexion: bool = False
    gain_abduccion: float = 1.0
    gain_aduccion: float = 1.0
    gain_flexion: float = 1.0
    gain_extension: float = 1.0
    min_cutoff: float = 0.5
    beta: float = 0.5
    min_visibility: float = 0.5
    model_complexity: int = 1
    reposo_abduccion: float = -55.0
    reposo_flexion: float = 0.0
    gracia_s: float = 0.5
    velocidad_max: float = 180.0

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "ArmConfig":
        return cls(
            target=_env_str(env, "ARM_TARGET", cls.target),
            mirror=_env_bool(env, "ARM_MIRROR", cls.mirror),
            invert_abduccion=_env_bool(
                env, "ARM_INVERT_ABDUCCION", cls.invert_abduccion
            ),
            invert_flexion=_env_bool(env, "ARM_INVERT_FLEXION", cls.invert_flexion),
            gain_abduccion=_env_float(env, "ARM_GAIN_ABDUCCION", cls.gain_abduccion),
            gain_aduccion=_env_float(env, "ARM_GAIN_ADUCCION", cls.gain_aduccion),
            gain_flexion=_env_float(env, "ARM_GAIN_FLEXION", cls.gain_flexion),
            gain_extension=_env_float(env, "ARM_GAIN_EXTENSION", cls.gain_extension),
            min_cutoff=_env_float(env, "ARM_MIN_CUTOFF", cls.min_cutoff),
            beta=_env_float(env, "ARM_BETA", cls.beta),
            min_visibility=_env_float(env, "ARM_MIN_VISIBILITY", cls.min_visibility),
            model_complexity=_env_int(
                env, "ARM_MODEL_COMPLEXITY", cls.model_complexity
            ),
            reposo_abduccion=_env_float(
                env, "ARM_REPOSO_ABDUCCION", cls.reposo_abduccion
            ),
            reposo_flexion=_env_float(env, "ARM_REPOSO_FLEXION", cls.reposo_flexion),
            gracia_s=_env_float(env, "ARM_GRACIA_S", cls.gracia_s),
            velocidad_max=_env_float(env, "ARM_VELOCIDAD_MAX", cls.velocidad_max),
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
    arm: ArmConfig = field(default_factory=ArmConfig)
    log_level: str = "INFO"
    abduccion_policy: str = "table"

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
            arm=ArmConfig.from_env(env),
            log_level=_env_str(env, "LOG_LEVEL", cls.log_level),
            abduccion_policy=_env_str(env, "ABDUCCION_POLICY", cls.abduccion_policy),
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
    "abduccion_policy": (None, "abduccion_policy"),
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
        "--abduccion-policy",
        dest="abduccion_policy",
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
                "head": cfg.head.__dict__,
                "arm": cfg.arm.__dict__,
                "log_level": cfg.log_level,
                "abduccion_policy": cfg.abduccion_policy,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
