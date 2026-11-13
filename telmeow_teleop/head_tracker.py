"""Estimación de la pose de la cabeza del operador.

Reemplaza a ``legacy/Face Tracking.py``. Dos cambios de fondo respecto a él.

SE ELIMINA dlib
---------------
El script legacy inicializaba MediaPipe Face Mesh y **nunca lo usaba**: todo el
trabajo lo hacía dlib con ``shape_predictor_68_face_landmarks.dat``, un archivo
de ~100 MB que además está en ``.gitignore``, lo que hacía el repositorio no
reproducible. Face Mesh ya es dependencia del proyecto, no requiere descargas
externas y es lo que declara el documento de tesis.

SE ESTIMA LA POSE POR solvePnP, NO POR arctan2
----------------------------------------------
El legacy calculaba el ángulo con ``arctan2`` sobre dos landmarks proyectados.
Eso **confunde rotación con traslación**: si el operador mueve la cabeza de lado
sin girarla, los dos puntos se desplazan en la imagen igual que si hubiera
girado, y el robot gira sin que nadie haya girado nada.

Aquí se resuelve el problema geométrico correcto: dado un modelo 3D del rostro y
dónde aparecen sus puntos en la imagen, ¿qué rotación de la cabeza explica esa
proyección? Eso es exactamente lo que responde ``cv2.solvePnP`` (Perspective-n-
Point), y es defendible ante un jurado.

DOS MÉTODOS DE ESTIMACIÓN
-------------------------
``method="mesh"`` (por defecto)
    Compara la nube de **cientos** de landmarks del rostro con la capturada en
    la postura neutra, y obtiene la rotación con el algoritmo de Kabsch. Al
    promediar sobre todos los puntos a la vez, el ruido de cada landmark pesa
    poco. No proyecta, así que no sufre las ambigüedades de perspectiva de PnP
    ni necesita conocer la distancia focal.

``method="pnp"``
    El planteamiento clásico: seis puntos y ``cv2.solvePnP``. Se conserva como
    término de comparación para el capítulo de resultados. Es notablemente más
    inestable con el mismo nivel de ruido, y hay una prueba que lo cuantifica.

CONVENCIÓN DE SIGNOS
--------------------
Medido sobre una cara **real** mirando a la cámara (ver ``FRONTAL_ROTATION``:
hacerlo sobre la identidad da resultados invertidos y fue fuente de dos errores
seguidos):

- ``yaw`` positivo  → la cara mira hacia la **izquierda de la imagen**.
- ``pitch`` positivo → la cara mira hacia **arriba**.

Con la imagen **espejada** que usa el pipeline (``cv2.flip``, para que el
operador se vea como en un espejo), la izquierda del operador cae en la
izquierda de la imagen. Por tanto:

    el operador gira la cabeza a SU izquierda  →  ``pan`` positivo
    el operador levanta la barbilla            →  ``tilt`` positivo

El ``tilt`` coincide con el robot, cuyo gesto de página inclina la cabeza hacia
abajo con ``tilt = -30``. Para el ``pan`` **queda por confirmar en el visor**
hacia qué lado debe girar el robot; si sale al revés, ``invert_pan=True`` lo
corrige sin tocar código.
"""

from __future__ import annotations

import math
import time
from collections import deque
from functools import lru_cache
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np

from .camera import abrir_camara, describir
from .config import AppConfig
from .filters import OneEuroFilter, ZonaMuerta
from .limits import HEAD_PAN_TARGET_RANGE, HEAD_TILT_RANGE, HeadClamp, clamp_head

__all__ = [
    "HeadTracker",
    "estimate_head_pose",
    "rotation_between_clouds",
    "landmarks_to_cloud",
    "MODEL_POINTS_3D",
    "LANDMARK_IDS",
    "FRONTAL_ROTATION",
    "apartar_si_existe",
    "COLUMNAS_CSV",
    "fila_csv",
]


#: Modelo 3D canónico de rostro, en milímetros y con la punta de la nariz en el
#: origen. Son las seis referencias habituales para estimación de pose de
#: cabeza: sobran para fijar la rotación (bastarían cuatro) y están bien
#: repartidas por la cara, que es lo que hace estable la solución.
#:
#: Ejes del modelo: +X hacia la derecha de la imagen, +Y hacia arriba,
#: +Z hacia el frente (la nariz sobresale hacia el observador).
MODEL_POINTS_3D = np.array(
    [
        (0.0, 0.0, 0.0),            # punta de la nariz
        (0.0, -330.0, -65.0),       # mentón
        (-225.0, 170.0, -135.0),    # comisura externa del ojo izq. de la imagen
        (225.0, 170.0, -135.0),     # comisura externa del ojo der. de la imagen
        (-150.0, -150.0, -125.0),   # comisura izq. de la boca
        (150.0, -150.0, -125.0),    # comisura der. de la boca
    ],
    dtype=np.float64,
)

#: Índices de Face Mesh correspondientes, EN EL MISMO ORDEN que el modelo 3D.
#: Si este orden se descuadra, solvePnP devuelve una pose sin sentido pero no da
#: ningún error: es un fallo silencioso, así que hay una prueba que lo fija.
LANDMARK_IDS = (1, 152, 33, 263, 61, 291)

#: Rotación de una cara que MIRA A LA CÁMARA, en el sistema de la cámara.
#:
#: No es la identidad, y ese detalle causó un bug real. El modelo 3D tiene la
#: nariz apuntando hacia +Z; la cámara de OpenCV tiene su +Z apuntando hacia la
#: escena. Así que una cara que mira al objetivo está girada ~180° respecto a la
#: identidad — la identidad correspondería a una cara mirando *al revés*, que es
#: físicamente imposible de trackear.
#:
#: Al extraer ángulos de Euler directamente de esa rotación, salían valores
#: cercanos a ±180°, y ahí ``atan2`` da saltos de 360° ante el menor ruido:
#: medido, el cabeceo pasaba de +175° a −180° justo en la posición frontal, que
#: es donde el operador está casi todo el tiempo. Eso producía temblor, saltos
#: bruscos y valores clavados en los topes del robot.
#:
#: La solución es medir la pose **respecto a esta referencia** y no respecto a
#: la identidad: ``R_rel = FRONTAL⁻¹ · R``. Así una cara frontal da 0° y los
#: ángulos varían de forma continua alrededor de ese punto.
FRONTAL_ROTATION = cv2.Rodrigues(np.array([0.0, math.pi, 0.0]))[0]


def _euler_from_rotation(rotation: np.ndarray) -> tuple[float, float, float]:
    """Ángulos de Euler (yaw, pitch, roll) en radianes, desde una matriz de
    rotación.

    Se protege el caso degenerado (*gimbal lock*), en el que el cabeceo alcanza
    ±90° y yaw y roll dejan de ser distinguibles.
    """
    sy = math.hypot(rotation[0, 0], rotation[1, 0])
    if sy > 1e-6:
        pitch = math.atan2(rotation[2, 1], rotation[2, 2])
        yaw = math.atan2(-rotation[2, 0], sy)
        roll = math.atan2(rotation[1, 0], rotation[0, 0])
    else:
        pitch = math.atan2(-rotation[1, 2], rotation[1, 1])
        yaw = math.atan2(-rotation[2, 0], sy)
        roll = 0.0
    return yaw, pitch, roll


def camera_matrix(width: int, height: int) -> np.ndarray:
    """Matriz de cámara aproximada, sin calibración previa.

    Se asume la distancia focal igual al ancho de la imagen y el centro óptico
    en el centro del sensor. Es la aproximación habitual cuando no se ha
    calibrado la cámara, y basta para estimar rotación: un error en la focal
    afecta sobre todo a la distancia estimada, no a los ángulos.

    Es, aun así, una fuente de error a declarar: sin calibrar, los ángulos
    tienen un sesgo sistemático que no se puede cuantificar.
    """
    focal = float(width)
    return np.array(
        [[focal, 0.0, width / 2.0], [0.0, focal, height / 2.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )


def estimate_head_pose(
    points_2d: np.ndarray, image_size: tuple[int, int]
) -> tuple[float, float, float] | None:
    """Resuelve la pose de la cabeza a partir de los seis puntos en la imagen.

    Función pura y sin MediaPipe, para poder probarla con datos sintéticos: se
    proyecta el modelo 3D con una rotación conocida y se comprueba que esto la
    recupera. Sin eso, la corrección de los signos solo podría verificarse con
    una cara delante de la cámara.

    Args:
        points_2d: array (6, 2) con los puntos de ``LANDMARK_IDS`` en píxeles,
            en el mismo orden que ``MODEL_POINTS_3D``.
        image_size: ``(ancho, alto)`` en píxeles.

    Returns:
        ``(yaw, pitch, roll)`` en **grados**, o ``None`` si solvePnP no converge.
    """
    points_2d = np.ascontiguousarray(points_2d, dtype=np.float64)
    if points_2d.shape != (len(MODEL_POINTS_3D), 2):
        raise ValueError(
            f"se esperaban {len(MODEL_POINTS_3D)} puntos 2D, "
            f"se recibió un array de forma {points_2d.shape}"
        )

    width, height = image_size
    ok, rotation_vector, _ = cv2.solvePnP(
        MODEL_POINTS_3D,
        points_2d,
        camera_matrix(width, height),
        np.zeros((4, 1)),  # sin distorsión: la cámara no está calibrada
        flags=cv2.SOLVEPNP_ITERATIVE,
    )
    if not ok:
        return None

    rotation, _ = cv2.Rodrigues(rotation_vector)
    # Respecto a la pose frontal, NO respecto a la identidad. Ver
    # FRONTAL_ROTATION: medirlo contra la identidad ponía los ángulos cerca de
    # ±180°, donde saltan 360° ante el menor ruido.
    relative = FRONTAL_ROTATION.T @ rotation
    yaw, pitch, roll = _euler_from_rotation(relative)
    return math.degrees(yaw), math.degrees(pitch), math.degrees(roll)


def rotation_between_clouds(
    reference: np.ndarray, current: np.ndarray
) -> np.ndarray:
    """Rotación rígida que lleva una nube de puntos a otra (algoritmo de Kabsch).

    Ambas nubes deben tener los MISMOS puntos en el MISMO orden. Devuelve la
    matriz de rotación 3x3 que mejor explica el cambio, en el sentido de mínimos
    cuadrados sobre todos los puntos a la vez.

    Por qué esto estabiliza tanto
    -----------------------------
    ``solvePnP`` resuelve la pose con **seis** puntos: el ruido de cada uno pesa
    mucho, y en ángulos grandes la solución se vuelve ambigua (hay poses
    distintas que proyectan casi igual, y el solver salta entre ellas — eso es
    lo que hace que los ejes dibujados se estiren y bailen).

    Kabsch usa **cientos** de puntos y promedia su ruido con una única
    descomposición SVD. No hay ambigüedad de perspectiva porque no proyecta:
    compara dos nubes 3D directamente. Tampoco necesita conocer la distancia
    focal de la cámara, que aquí está sin calibrar.

    A cambio, mide la rotación **respecto a la nube de referencia**, no respecto
    a un sistema absoluto. Para teleoperación eso no es una limitación sino lo
    que se quiere: la referencia es la postura neutra del operador, que es
    justamente el origen desde el que hay que medir.
    """
    if reference.shape != current.shape or reference.ndim != 2:
        raise ValueError(
            f"las nubes deben tener la misma forma (N, 3); "
            f"se recibió {reference.shape} y {current.shape}"
        )

    # Centrar: la traslación no interesa, solo la rotación.
    p = reference - reference.mean(axis=0)
    q = current - current.mean(axis=0)

    u, _, vt = np.linalg.svd(p.T @ q)
    # Corrección de reflexión: sin esto, el SVD puede devolver una matriz con
    # determinante −1, que es una reflexión y no una rotación física.
    d = np.sign(np.linalg.det(vt.T @ u.T))
    return vt.T @ np.diag([1.0, 1.0, d]) @ u.T


def landmarks_to_cloud(landmarks, width: int, height: int) -> np.ndarray:
    """Convierte los landmarks de Face Mesh en una nube de puntos coherente.

    MediaPipe entrega ``x`` e ``y`` normalizados por el ANCHO y el ALTO
    respectivamente, y ``z`` en una escala «parecida a la de x». Si se usaran
    tal cual, con una imagen no cuadrada los tres ejes tendrían escalas
    distintas y la rotación saldría deformada. Aquí se llevan los tres a
    píxeles usando el ancho para x y z, y el alto para y.

    LA ``y`` VA NEGADA, y no es un detalle cosmético. MediaPipe usa la
    convención de la imagen, donde ``y`` crece HACIA ABAJO; ``MODEL_POINTS_3D``,
    que es lo que resuelve el método 'pnp', tiene la ``y`` hacia ARRIBA (mentón
    en −330, ojos en +170). Descomponer una rotación en dos sistemas cuya ``y``
    apunta al revés **invierte el pitch y deja el yaw intacto**: los dos
    métodos daban tilt de signo contrario para el mismo gesto, y como 'mesh' es
    el de por defecto, el operador levantaba la cabeza y el robot la bajaba. El
    pan no se veía afectado, lo que hacía el fallo aún más difícil de leer.

    Las pruebas sintéticas no lo detectaron porque solo fijaban el signo del
    camino 'pnp'; hay una prueba nueva que ata los dos métodos a la misma
    convención.
    """
    return np.array(
        [(lm.x * width, -lm.y * height, lm.z * width) for lm in landmarks],
        dtype=np.float64,
    )


@lru_cache(maxsize=32)
def _escala_compresion(ganancia: float, tope: float, recto: float, hasta: float) -> float | None:
    """La constante ``A`` de ``comprimir_tilt_abajo``, o ``None`` si no hay que
    comprimir: apagada, o el tope ya queda más allá de ``hasta`` con la ganancia
    sola, o el tramo recto ya llega al tope."""
    if hasta <= 0.0 or hasta <= recto or ganancia * hasta <= tope or ganancia * recto >= tope:
        return None
    falta = tope - ganancia * recto            # lo que le queda al robot tras el tramo recto
    tramo = ganancia * (hasta - recto)         # lo que recorrería sin comprimir
    # A·(1 − exp(−tramo/A)) crece con A desde 0 hasta ``tramo``: bisección.
    bajo, alto = 1e-6, 1e6
    for _ in range(200):
        medio = math.sqrt(bajo * alto)
        if medio * (1.0 - math.exp(-tramo / medio)) < falta:
            bajo = medio
        else:
            alto = medio
    return math.sqrt(bajo * alto)


def comprimir_tilt_abajo(abajo: float, ganancia: float, tope: float,
                         recto: float, hasta: float) -> float:
    """Grados que baja la cabeza del robot cuando el operador baja ``abajo``
    (los dos positivos) — P10.2, H72.

    Hasta ``recto`` grados es la ganancia de siempre, ``ganancia · abajo``. De
    ahí en adelante la pendiente decae de forma continua (una exponencial que
    arranca con pendiente ``ganancia``, así que no hay codo que se vea) y la
    curva pasa por ``tope`` justo en ``hasta``::

        ganancia·recto + A·(1 − exp(−ganancia·(abajo − recto)/A))

    Más allá de ``hasta`` sigue creciendo y lo corta el recorte de siempre. Sin
    compresión (``hasta`` 0, o un tope que la ganancia sola no alcanza antes de
    ``hasta``) devuelve ``ganancia · abajo``. Es monótona: el robot nunca se
    mueve al revés que el operador.
    """
    if abajo <= recto:
        return ganancia * abajo
    escala = _escala_compresion(float(ganancia), float(tope), float(recto), float(hasta))
    if escala is None:
        return ganancia * abajo
    return ganancia * recto + escala * (1.0 - math.exp(-ganancia * (abajo - recto) / escala))


class RuidoMalla:
    """Cuánto tiembla la Face Mesh, en grados crudos, sin confundirlo con mover
    la cabeza (P7.6).

    Usa la segunda diferencia, ``x[t] − 2·x[t−1] + x[t−2]``: un giro de la cabeza
    es suave de un frame a otro y casi no la mueve, y el ruido sí. Con ruido
    blanco de desviación σ, la segunda diferencia tiene desviación σ·√6, y la
    mediana de su valor absoluto es 0.6745 veces eso: de ahí sale σ, con la
    mediana para que los pocos frames de un movimiento brusco no cuenten. En
    cada frame se toma el peor de los dos ejes, así que es un índice y no la σ
    de un eje: con el mismo ruido σ en pan y en tilt da 1.56·σ. Los umbrales
    (``config.HeadConfig``) se fijaron con este mismo índice, sobre lo grabado.

    Medido (``tools/filtros_cabeza.py`` y las sesiones del P7): con la luz de
    trabajo, σ ≈ 1.0° de mediana (0.8-1.4 según la sesión); con la luz apagada,
    2.3° (p75, primera toma, la cámara a 10 fps); en la sesión del P4, 0.4°.

    Quien lo alimenta decide qué frames cuentan: con las manos en la cara la
    malla se inventa la cara (H58) y su temblor no es el de la luz.
    """

    def __init__(self, ventana_s: float = 3.0, minimo: int = 10) -> None:
        self.ventana_s = float(ventana_s)
        self.minimo = int(minimo)
        self._previos: deque[tuple[float, float]] = deque(maxlen=2)
        self._segundas: deque[tuple[float, float]] = deque()

    def reiniciar(self) -> None:
        self._previos.clear()
        self._segundas.clear()

    def cortar(self) -> None:
        """Un frame que no cuenta (sin cara, o con las manos encima): la
        siguiente segunda diferencia no puede saltárselo."""
        self._previos.clear()

    def actualizar(self, t: float, crudo: tuple[float, float]) -> None:
        if len(self._previos) == 2:
            (p2, q2), (p1, q1) = self._previos
            d2 = max(abs(crudo[0] - 2.0 * p1 + p2), abs(crudo[1] - 2.0 * q1 + q2))
            self._segundas.append((t, d2))
        self._previos.append(crudo)
        while self._segundas and t - self._segundas[0][0] > self.ventana_s:
            self._segundas.popleft()

    @property
    def sigma(self) -> float | None:
        """σ estimada en la última ventana, o ``None`` si aún no hay muestras."""
        if len(self._segundas) < self.minimo:
            return None
        valores = sorted(d for _, d in self._segundas)
        return valores[len(valores) // 2] / 0.6745 / math.sqrt(6.0)


class HeadTracker:
    """Convierte los frames de la cámara en los ángulos `pan` y `tilt` del robot.

    Cadena de proceso, en este orden:

    1. Face Mesh localiza los landmarks del rostro.
    2. ``solvePnP`` resuelve la rotación de la cabeza.
    3. Se resta la **pose neutra** calibrada (ver ``set_neutral``).
    4. Se aplica la **ganancia** de cada canal.
    5. Se **suaviza** con un filtro One Euro por canal.
    6. Se **recorta** a los límites del robot.

    El orden importa: la ganancia se aplica sobre la desviación respecto al
    neutro, no sobre el ángulo absoluto; y el recorte va al final para que la
    salida nunca viole los límites, pase lo que pase antes.

    Args:
        gain_pan, gain_tilt:
            Multiplicador entre el giro del operador y el del robot. Con 1.0 el
            robot copia el movimiento; con 2.0 lo exagera; con 0.5 lo atenúa.

            Están separados a propósito porque los rangos no se parecen: el pan
            del robot (±90°) es comparable al del cuello humano, pero su tilt
            (−39°/+42°) se queda muy corto —una persona baja la barbilla mucho
            más—, así que con ganancia 1.0 el robot se queda clavado en el tope
            en cuanto el operador mire hacia abajo.

            Amplificar tiene un precio: multiplica también el ruido de la
            estimación, y hace llegar antes al tope mecánico. Valor a calibrar
            con una persona real; 1.0 es solo un punto de partida honesto.
        invert_pan, invert_tilt:
            Invierten el sentido de cada canal. Existen porque el sentido
            correcto solo se confirma mirando el visor, y así se corrige sin
            tocar el código.
        min_cutoff, beta:
            Parámetros del filtro One Euro. ``min_cutoff`` gobierna el temblor
            en reposo (bajarlo suaviza más) y ``beta`` el retardo al moverse
            (subirlo lo hace más reactivo).
        zona_muerta:
            Grados que la salida del filtro tiene que alejarse para que el
            robot se mueva (``filters.ZonaMuerta``). Quita el temblor con la
            persona quieta a cambio de quedarse hasta ese ancho corto al
            pararse. Con 0 no actúa.
        max_num_faces:
            Fijo en 1: se teleopera a una persona.
    """

    def __init__(
        self,
        gain_pan: float = 1.0,
        gain_tilt: float = 1.0,
        *,
        gain_tilt_up: float | None = None,
        gain_tilt_down: float | None = None,
        method: str = "mesh",
        invert_pan: bool = False,
        invert_tilt: bool = False,
        min_cutoff: float = 0.5,
        beta: float = 0.5,
        zona_muerta: float = 0.0,
        tilt_down_recto: float = 0.0,
        tilt_down_hasta: float = 0.0,
        clock=time.perf_counter,
    ) -> None:
        if min(tilt_down_recto, tilt_down_hasta) < 0.0:
            raise ValueError("tilt_down_recto y tilt_down_hasta no pueden ser negativos")
        self.tilt_down_recto = float(tilt_down_recto)
        self.tilt_down_hasta = float(tilt_down_hasta)
        arriba = gain_tilt if gain_tilt_up is None else gain_tilt_up
        abajo = gain_tilt if gain_tilt_down is None else gain_tilt_down
        if min(gain_pan, arriba, abajo) <= 0.0:
            raise ValueError("las ganancias deben ser positivas")
        if method not in ("mesh", "pnp"):
            raise ValueError(f"method debe ser 'mesh' o 'pnp', se recibió {method!r}")
        self._extremos = {"pan": [0.0, 0.0], "tilt": [0.0, 0.0]}

        self.gain_pan = float(gain_pan)
        self.gain_tilt_up = float(arriba)
        self.gain_tilt_down = float(abajo)
        self.method = method
        self.invert_pan = invert_pan
        self.invert_tilt = invert_tilt
        self._clock = clock

        self._face_mesh = mp.solutions.face_mesh.FaceMesh(
            static_image_mode=False,
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )

        self._filter_pan = OneEuroFilter(min_cutoff, beta, clock=clock)
        self._filter_tilt = OneEuroFilter(min_cutoff, beta, clock=clock)
        # Tras el One Euro y antes del recorte: así el recorte sigue mandando y
        # el HUD pinta lo que de verdad se publica (P7.4).
        self._zona_pan = ZonaMuerta(zona_muerta)
        self._zona_tilt = ZonaMuerta(zona_muerta)

        self._neutral: tuple[float, float] | None = None
        self._raw: tuple[float, float] | None = None
        self._last: HeadClamp | None = None
        self._frames_sin_rostro = 0
        # Estado del método 'mesh': la nube de referencia es la postura neutra.
        self._reference_cloud: np.ndarray | None = None
        self._last_cloud: np.ndarray | None = None
        self._last_points_2d: np.ndarray | None = None
        self._caja: tuple[int, int, int, int] | None = None

    @classmethod
    def desde_config(cls, cfg: AppConfig) -> "HeadTracker":
        """El tracker con los parámetros de ``cfg.head``.

        Lo usan el demo y el pipeline integrado (P7), y es a propósito el
        MISMO camino: las ganancias y los signos se calibraron y verificaron con
        el demo, y si el pipeline construyera el tracker por su cuenta un campo
        olvidado lo dejaría con el valor por defecto sin ningún aviso.
        """
        return cls(
            gain_pan=cfg.head.gain_pan,
            gain_tilt_up=cfg.head.gain_tilt_up,
            gain_tilt_down=cfg.head.gain_tilt_down,
            tilt_down_recto=cfg.head.tilt_down_recto,
            tilt_down_hasta=cfg.head.tilt_down_hasta,
            method=cfg.head.method,
            invert_pan=cfg.head.invert_pan,
            invert_tilt=cfg.head.invert_tilt,
            min_cutoff=cfg.head.min_cutoff,
            beta=cfg.head.beta,
            zona_muerta=cfg.head.zona_muerta,
        )

    # -- estado, para el HUD ----------------------------------------------

    @property
    def last_clamp(self) -> HeadClamp | None:
        """Último recorte completo. Lleva los límites y si hubo recorte, que es
        lo que el HUD necesita para pintar las barras."""
        return self._last

    @property
    def raw_angles(self) -> tuple[float, float] | None:
        """Ángulos crudos del estimador, antes de ganancia y filtro."""
        return self._raw

    @property
    def zona_muerta(self) -> float:
        """Ancho de la zona muerta en uso, en grados del robot."""
        return self._zona_pan.ancho

    @zona_muerta.setter
    def zona_muerta(self, ancho: float) -> None:
        """Cambiarla en marcha (P7.6: crece con el ruido de la malla). No
        reinicia nada: la salida sigue donde estaba y la nueva anchura cuenta
        desde la próxima muestra."""
        ancho = float(ancho)
        if not (math.isfinite(ancho) and ancho >= 0.0):
            raise ValueError(f"ancho debe ser finito y no negativo, se recibió {ancho!r}")
        self._zona_pan.ancho = ancho
        self._zona_tilt.ancho = ancho

    @property
    def caja(self) -> tuple[int, int, int, int] | None:
        """``(x, y, w, h)`` en píxeles que abarca la malla de la cara en el
        último frame, o ``None`` si no hubo cara.

        La usa el pipeline para saber si las manos tapan la cara (H58): con la
        cara tapada, Face Mesh no avisa, sino que se inventa una cara detrás de
        las manos, y la pose que sale de ella no es la del operador.
        """
        return self._caja

    @property
    def face_scale(self) -> float | None:
        """Distancia en píxeles entre las comisuras externas de los ojos.

        Es un indicador de cuán grande se ve el rostro, y por tanto de a qué
        distancia está el operador. Importa porque cuantos menos píxeles ocupa
        la cara, más ruidosos son los landmarks: permite cruzar el temblor
        medido con la distancia de trabajo en vez de discutirlo a ojo.
        """
        if self._last_points_2d is None:
            return None
        # Índices 2 y 3 de LANDMARK_IDS: los dos extremos de los ojos.
        return float(
            np.linalg.norm(self._last_points_2d[2] - self._last_points_2d[3])
        )

    @property
    def raw_extremes(self) -> dict[str, tuple[float, float]]:
        """Mínimo y máximo crudos observados, por eje. Para calibrar."""
        return {k: (v[0], v[1]) for k, v in self._extremos.items()}

    def reset_extremes(self) -> None:
        self._extremos = {"pan": [0.0, 0.0], "tilt": [0.0, 0.0]}

    def suggested_gains(self) -> dict[str, float | None]:
        """Ganancias que harían falta para alcanzar el objetivo de cada eje.

        Procedimiento de calibración: calibrar el neutro, girar la cabeza hasta
        el ángulo en que **la pantalla deja de leerse con comodidad**, y leer
        estos valores. Son las ganancias con las que ese recorrido útil cubre
        justo el objetivo.

        El tilt se propone **por separado hacia arriba y hacia abajo**, porque
        las dos asimetrías van en el mismo sentido y se suman: el robot llega
        menos hacia abajo (−39° frente a +42°) y el operador también se queda
        más corto hacia abajo (medido, 26.5° arriba frente a 21.6° abajo). Con
        un único valor habría que elegir entre pasarse por arriba o no llegar
        nunca por abajo.

        El pan sí usa un solo valor, porque su objetivo es simétrico (±50°) y
        las medidas no sostienen una asimetría estable del operador. De las dos
        propuestas se toma la **menor**, que es la del lado de MAYOR recorrido:
        así ningún sentido se pasa del objetivo, a cambio de que el lado corto
        se quede algo por debajo. Es la elección conservadora, y con la
        asimetría medida no es cosmética — son 1.49 frente a 1.69.

        OBJETIVOS DISTINTOS POR EJE, y no es un descuido. El tilt apunta a su
        rango mecánico completo (`HEAD_TILT_RANGE`); el pan apunta a
        `HEAD_PAN_TARGET_RANGE`, que es ±50° y no los ±90° que el cuello del
        robot admite, porque lo que acota al operador no es su cuello sino su
        campo de visión sobre el monitor (§3.2.1 del plan maestro). Dividir por
        el rango mecánico daba una ganancia 1.8× más alta de la que corresponde.
        """
        minimo_pan, maximo_pan = self._extremos["pan"]
        propuestas_pan = []
        if minimo_pan < -1e-6:
            propuestas_pan.append(abs(HEAD_PAN_TARGET_RANGE[0] / minimo_pan))
        if maximo_pan > 1e-6:
            propuestas_pan.append(abs(HEAD_PAN_TARGET_RANGE[1] / maximo_pan))

        minimo_tilt, maximo_tilt = self._extremos["tilt"]
        return {
            "pan": min(propuestas_pan) if propuestas_pan else None,
            "tilt_up": (
                abs(HEAD_TILT_RANGE[1] / maximo_tilt) if maximo_tilt > 1e-6 else None
            ),
            "tilt_down": (
                abs(HEAD_TILT_RANGE[0] / minimo_tilt) if minimo_tilt < -1e-6 else None
            ),
        }

    @property
    def neutral(self) -> tuple[float, float] | None:
        return self._neutral

    @property
    def frames_sin_rostro(self) -> int:
        return self._frames_sin_rostro

    @property
    def degenerate_dt_count(self) -> int:
        """Muestras en las que el reloj no tuvo resolución suficiente.

        Si esto crece, los timestamps no sirven para estimar velocidad y el
        filtro One Euro pierde su ventaja. Ver la nota del módulo ``filters``.
        """
        return (
            self._filter_pan.degenerate_dt_count
            + self._filter_tilt.degenerate_dt_count
        )

    # -- calibración -------------------------------------------------------

    def set_neutral(self) -> bool:
        """Toma la pose actual como origen. Devuelve si había una pose que tomar.

        Sin esto, la postura natural del operador se traduce en un sesgo
        constante del robot: si trabaja con la cabeza ligeramente girada, el
        robot se queda permanentemente girado.

        En el método ``mesh`` esto vuelve a capturar la nube de referencia, que
        es más que un origen numérico: pasa a ser el molde del rostro concreto
        del operador contra el que se compara todo lo demás.
        """
        if self.method == "mesh":
            if self._last_cloud is None:
                return False
            self._reference_cloud = self._last_cloud
        else:
            if self._raw is None:
                return False
            self._neutral = self._raw
        self._reiniciar_filtros()
        return True

    def clear_neutral(self) -> None:
        self._neutral = None
        self._reference_cloud = None
        self._reiniciar_filtros()

    def _reiniciar_filtros(self) -> None:
        self._filter_pan.reset()
        self._filter_tilt.reset()
        self._zona_pan.reset()
        self._zona_tilt.reset()

    # -- proceso -----------------------------------------------------------

    def process(self, frame_rgb: np.ndarray) -> tuple[float, float] | None:
        """Estima ``(pan, tilt)`` en grados desde un frame RGB.

        Returns:
            La pose ya recortada, o ``None`` si no hay rostro, si solvePnP no
            converge o si algún ángulo salió no finito. Quien llame debe
            reutilizar el último valor válido en ese caso, no publicar ceros: un
            salto a home por una detección perdida se ve como un tirón.
        """
        height, width = frame_rgb.shape[:2]
        resultado = self._face_mesh.process(frame_rgb)
        if not resultado.multi_face_landmarks:
            self._frames_sin_rostro += 1
            self._caja = None
            return None

        landmarks = resultado.multi_face_landmarks[0].landmark
        xs = [lm.x * width for lm in landmarks]
        ys = [lm.y * height for lm in landmarks]
        self._caja = (int(min(xs)), int(min(ys)),
                      int(max(xs) - min(xs)), int(max(ys) - min(ys)))
        self._last_points_2d = np.array(
            [(landmarks[i].x * width, landmarks[i].y * height) for i in LANDMARK_IDS],
            dtype=np.float64,
        )

        if self.method == "mesh":
            nube = landmarks_to_cloud(landmarks, width, height)
            self._last_cloud = nube
            if self._reference_cloud is None:
                # Autocalibración: la primera cara vista pasa a ser la
                # referencia, para que el modo funcione sin pedir nada. Se puede
                # recapturar en cualquier momento con set_neutral().
                self._reference_cloud = nube
            rotacion = rotation_between_clouds(self._reference_cloud, nube)
            yaw, pitch, _roll = (
                math.degrees(a) for a in _euler_from_rotation(rotacion)
            )
        else:
            pose = estimate_head_pose(self._last_points_2d, (width, height))
            if pose is None:
                self._frames_sin_rostro += 1
                return None
            yaw, pitch, _roll = pose

        if not (math.isfinite(yaw) and math.isfinite(pitch)):
            self._frames_sin_rostro += 1
            return None

        self._frames_sin_rostro = 0
        self._raw = (yaw, pitch)
        for eje, valor in (("pan", yaw), ("tilt", pitch)):
            self._extremos[eje][0] = min(self._extremos[eje][0], valor)
            self._extremos[eje][1] = max(self._extremos[eje][1], valor)
        return self.map_to_robot(yaw, pitch)

    def map_to_robot(
        self, yaw: float, pitch: float, timestamp: float | None = None
    ) -> tuple[float, float] | None:
        """Convierte los ángulos de la cabeza del operador en los del robot.

        Cadena: restar el neutro → aplicar la ganancia → suavizar → recortar.

        Está separada de ``process`` para poder probarla sin cámara ni
        MediaPipe: es donde viven la ganancia y el recorte, que son las
        decisiones que hay que poder verificar.

        Devuelve ``None`` si algún ángulo no es finito.
        """
        # Se comprueba AQUÍ, antes de tocar nada. Si se dejara para más abajo,
        # el filtro de pan podría procesar su muestra y el de tilt reventar con
        # la suya, dejando los dos canales desincronizados en el tiempo.
        if not (math.isfinite(yaw) and math.isfinite(pitch)):
            return None

        # Desviación respecto al neutro calibrado. La ganancia se aplica sobre
        # esta desviación, no sobre el ángulo absoluto.
        if self._neutral is not None:
            yaw -= self._neutral[0]
            pitch -= self._neutral[1]

        # Ganancia de tilt distinta según el sentido. Se elige por el signo del
        # movimiento DEL OPERADOR (antes de invertir), porque las ganancias se
        # calibran contra lo que él alcanza cómodamente, no contra lo que acabe
        # haciendo el robot.
        #
        # Bajar la barbilla es el caso peor, y por partida doble: el robot llega
        # menos hacia abajo (−39° frente a +42°) y el operador también. Con una
        # sola ganancia hay que elegir entre pasarse arriba o no llegar abajo.
        # La función es continua en 0, así que no hay salto al cruzar.
        #
        # Hacia abajo, además, la ganancia se comprime lejos del frente (P10.2,
        # H72): al narrar el operador baja hasta 37° para leer el pie de la
        # página, y con la ganancia sola el robot se quedaba parado en su tope
        # desde los 21°. Ver ``comprimir_tilt_abajo``.
        if pitch >= 0.0:
            tilt = pitch * self.gain_tilt_up
        else:
            tilt = -comprimir_tilt_abajo(-pitch, self.gain_tilt_down, -HEAD_TILT_RANGE[0],
                                         self.tilt_down_recto, self.tilt_down_hasta)

        pan = yaw * self.gain_pan * (-1.0 if self.invert_pan else 1.0)
        tilt *= -1.0 if self.invert_tilt else 1.0

        ahora = self._clock() if timestamp is None else timestamp
        pan = self._zona_pan(self._filter_pan(pan, ahora))
        tilt = self._zona_tilt(self._filter_tilt(tilt, ahora))

        try:
            recorte = clamp_head(pan, tilt)
        except ValueError:
            # clamp_head rechaza NaN e infinitos a propósito, para que no
            # lleguen al robot. Se descarta el frame.
            return None

        self._last = recorte
        return recorte.as_tuple

    def close(self) -> None:
        self._face_mesh.close()

    def __enter__(self) -> "HeadTracker":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


# --------------------------------------------------------------------------
# Modo demo
# --------------------------------------------------------------------------


def apartar_si_existe(ruta: Path) -> Path | None:
    """Si ``ruta`` ya existe, la renombra con el sufijo ``_anterior``.

    Devuelve dónde quedó la anterior, o None si no había ninguna.

    Existe porque una sesión de medición cuesta diez minutos de operador y no se
    puede rehacer idéntica —la postura, la luz y la distancia no vuelven a ser
    las mismas—. Ya se perdió una así: se grabó la segunda toma con el mismo
    nombre que la primera y la primera dejó de existir, con ella la única copia
    de los datos de la cámara externa.

    NO pregunta ni aborta a propósito: interrumpir con una pregunta en el
    arranque es justo lo que no se quiere durante una sesión con el operador
    sentado y calibrado. Aparta y sigue.
    """
    if not ruta.exists():
        return None
    apartado = ruta.with_name(f"{ruta.stem}_anterior{ruta.suffix}")
    contador = 2
    while apartado.exists():
        apartado = ruta.with_name(f"{ruta.stem}_anterior{contador}{ruta.suffix}")
        contador += 1
    ruta.replace(apartado)
    return apartado


def _dibujar_orientacion(frame_bgr, origen, pan: float, tilt: float, largo=90.0) -> None:
    """Dibuja la terna de orientación a partir de la pose YA FILTRADA.

    Deliberadamente NO se recalcula la pose aquí. La primera versión de este
    demo volvía a llamar a ``solvePnP`` sobre los puntos crudos para dibujar,
    saltándose el filtro: los ejes temblaban mucho más que los números que
    mostraba al lado, y además podían estirarse cuando el solver saltaba entre
    soluciones ambiguas.

    Ahora se dibuja exactamente lo que se le envía al robot, que es lo único que
    importa comprobar.
    """
    yaw, pitch = math.radians(pan), math.radians(tilt)
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    rotacion = np.array(
        [[cy, sy * sp, sy * cp], [0.0, cp, -sp], [-sy, cy * sp, cy * cp]]
    )
    ejes = np.eye(3) * largo
    colores = ((0, 0, 255), (0, 255, 0), (255, 0, 0))  # X rojo, Y verde, Z azul
    ox, oy = int(origen[0]), int(origen[1])
    for columna, color in zip((rotacion @ ejes).T, colores):
        # Proyección ortográfica: basta para ver la orientación, y no introduce
        # la deformación por perspectiva que hacía "estirarse" a los ejes.
        #
        # La componente horizontal va NEGADA porque el frame está espejado: sin
        # esto el eje azul (hacia dónde mira la cabeza) se iba al lado contrario
        # del giro real, aunque el valor de `pan` fuera correcto. Era un fallo
        # solo del dibujo, pero de los que hacen desconfiar del número.
        cv2.line(
            frame_bgr,
            (ox, oy),
            (ox - int(columna[0]), oy - int(columna[1])),
            color,
            2,
        )


#: Altura en píxeles donde empieza el bloque de ayuda a la calibración.
ALTO_TEXTO = 145

#: Columnas del CSV de ``--record``, en orden. ``tools/analizar_registro.py``
#: las lee por nombre; las escriben el demo y el pipeline integrado (P7).
COLUMNAS_CSV = (
    "t", "etiqueta", "metodo", "raw_pan", "raw_tilt",
    "pan", "tilt", "gain_pan", "gain_tilt_up", "gain_tilt_down",
    "recortado", "escala_cara", "ancho", "alto",
    # Desde el 2026-11-13 (P10.2): la compresión del cabeceo hacia abajo.
    "tilt_down_recto", "tilt_down_hasta",
)


def fila_csv(tracker: HeadTracker, t: float, etiqueta: int,
             pose: tuple[float, float], ancho: int, alto: int) -> list:
    """La fila de ``COLUMNAS_CSV`` para un frame CON pose.

    Solo se graban los frames con rostro, igual que en el P4: el analizador
    mide temblor y recorrido, y una fila sin pose no aporta a ninguno.
    """
    crudo = tracker.raw_angles or (float("nan"), float("nan"))
    recorte = tracker.last_clamp
    return [
        f"{t:.4f}", etiqueta, tracker.method,
        f"{crudo[0]:.4f}", f"{crudo[1]:.4f}",
        f"{pose[0]:.4f}", f"{pose[1]:.4f}",
        f"{tracker.gain_pan:.2f}",
        f"{tracker.gain_tilt_up:.2f}", f"{tracker.gain_tilt_down:.2f}",
        int(bool(recorte and recorte.clamped)),
        f"{tracker.face_scale or float('nan'):.2f}", ancho, alto,
        f"{tracker.tilt_down_recto:g}", f"{tracker.tilt_down_hasta:g}",
    ]


def _demo(argv: list[str] | None = None) -> int:
    """`python -m telmeow_teleop.head_tracker [--record CSV] [--publish]`

    Las dos banderas son independientes y se combinan: ``--record`` deja el
    dato para analizarlo después, ``--publish`` deja ver el robot moverse en el
    visor mientras tanto.
    """
    import argparse
    import csv

    parser = argparse.ArgumentParser(prog="head_tracker")
    parser.add_argument(
        "--record",
        metavar="CSV",
        help="Registra cada frame en un CSV, para analizar los datos después "
             "en vez de juzgar a ojo. La tecla ESPACIO cambia de etiqueta, "
             "para separar las fases de la prueba.",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publica la pose en el broker MQTT, para ver la cabeza moverse en "
             "el visor 3D mientras se calibra. Convive con --record.",
    )
    args = parser.parse_args(argv)

    cfg = AppConfig.from_env()
    cap = abrir_camara(cfg.camera)
    if cap is None:
        print(
            f"No se pudo abrir la camara {cfg.camera.index}. "
            f"Prueba 'python tools/list_cameras.py' para ver que indices hay."
        )
        return 1
    print(
        describir(cap, cfg.camera)
        + f" Formato {cfg.camera.fourcc or 'por defecto'}.\n"
    )

    print(
        "CALIBRACION DE GANANCIA\n"
        "  El limite NO es tu cuello, es TU PANTALLA: teleoperas mirando el\n"
        "  monitor, asi que el recorrido util acaba donde dejas de leerlo.\n"
        "  1. Sientate a la distancia de trabajo real.\n"
        "  2. Pulsa 'c' mirando de frente, en tu postura natural, y 'r' para\n"
        "     reiniciar los extremos.\n"
        "  3. Gira DESPACIO hasta el punto en que la pantalla deja de leerse\n"
        "     con comodidad, y MANTENLO 5 SEGUNDOS ahi.\n"
        "  4. ESPACIO para cerrar la etiqueta, y repite: otro lado, arriba y\n"
        "     abajo. NO vuelvas al centro dentro de una etiqueta: mezcla la\n"
        "     postura medida con el camino de ida y vuelta y la inutiliza.\n"
        "  5. Lee 'sugerida' en pantalla, o analiza el CSV con\n"
        "     'python tools/analizar_registro.py <archivo>', que da la cifra\n"
        "     por percentil y avisa si una etiqueta salio contaminada.\n\n"
        "TECLAS\n"
        "  c = calibrar neutro    n = borrarlo    r = reiniciar extremos\n"
        "  z/Z = ganancia tilt ARRIBA -/+\n"
        "  v/V = ganancia tilt ABAJO  -/+\n"
        "  a/A = ganancia pan         -/+\n"
        "  t = alternar metodo (mesh <-> pnp)\n"
        "  ESPACIO = cambiar de etiqueta (solo con --record)\n"
        "  q = salir\n"
    )

    tracker = HeadTracker.desde_config(cfg)

    # Publicación opcional al visor 3D. Es lo que permite comprobar el SENTIDO
    # del giro: el HUD dice el signo del ángulo, pero hacia qué lado gira el
    # robot con ese signo solo se ve en el visor.
    #
    # Se importa aquí dentro y no arriba a propósito: el módulo tiene que poder
    # usarse sin paho-mqtt instalado y sin broker levantado, que es como se
    # ejecuta en la mayoría de las pruebas.
    publicador = None
    if args.publish:
        from .publisher import PosePublisher

        publicador = PosePublisher(cfg.mqtt, cfg.rates).connect()
        if publicador.wait_connected(timeout=5.0):
            print(
                f"Publicando en {cfg.mqtt.host}:{cfg.mqtt.port} "
                f"topic '{cfg.mqtt.topic_pose}' a {cfg.rates.publish_hz:g} Hz\n"
            )
        else:
            # No se aborta: paho reintenta solo y el enlace puede subir a
            # mitad de sesión. Pero hay que decirlo, porque con QoS 0 lo que se
            # publique mientras tanto se pierde sin dejar rastro.
            print(
                f"AVISO: no hay enlace con el broker en {cfg.mqtt.host}:"
                f"{cfg.mqtt.port}. Se sigue reintentando; mira el contador "
                f"'perdidos' del HUD.\n"
            )

    registro = None
    escritor = None
    etiqueta = 0
    if args.record:
        apartado = apartar_si_existe(Path(args.record))
        if apartado is not None:
            print(f"AVISO: ese CSV ya existia. Se conserva como {apartado.name}.\n")
        registro = open(args.record, "w", newline="", encoding="utf-8")
        escritor = csv.writer(registro)
        escritor.writerow(COLUMNAS_CSV)
        print(f"Registrando en {args.record}. ESPACIO cambia de etiqueta.\n")

    t0 = time.perf_counter()
    marcas_fps: deque[float] = deque(maxlen=30)
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if cfg.camera.flip:
                frame = cv2.flip(frame, 1)

            marcas_fps.append(time.perf_counter())
            fps_real = (
                (len(marcas_fps) - 1) / (marcas_fps[-1] - marcas_fps[0])
                if len(marcas_fps) > 1 and marcas_fps[-1] > marcas_fps[0]
                else 0.0
            )

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pose = tracker.process(rgb)
            height, width = frame.shape[:2]

            if pose is not None:
                origen = (
                    tracker._last_points_2d[0]
                    if tracker._last_points_2d is not None
                    else (width / 2, height / 2)
                )
                _dibujar_orientacion(frame, origen, pose[0], pose[1])

                recorte = tracker.last_clamp
                color = (0, 0, 255) if recorte and recorte.clamped else (0, 255, 0)
                cv2.putText(
                    frame, f"pan {pose[0]:+7.2f}   tilt {pose[1]:+7.2f}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2,
                )
                if recorte and recorte.clamped:
                    cv2.putText(
                        frame, "TOPE DEL ROBOT", (10, 115),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2,
                    )
                if tracker.raw_angles:
                    cv2.putText(
                        frame,
                        f"crudo {tracker.raw_angles[0]:+7.2f} {tracker.raw_angles[1]:+7.2f}",
                        (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1,
                    )
            else:
                cv2.putText(
                    frame, "sin rostro", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 165, 255), 2,
                )

            cv2.putText(
                frame,
                f"{tracker.method}  pan {tracker.gain_pan:.2f}  "
                f"tilt ^{tracker.gain_tilt_up:.2f} v{tracker.gain_tilt_down:.2f}"
                f"   {fps_real:4.1f} fps",
                (10, 88), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                (255, 255, 0) if fps_real >= 20 else (0, 165, 255), 2,
            )

            # Ayuda de calibración: extremos alcanzados y la ganancia que haría
            # falta para que ese recorrido cubra justo el rango del robot.
            extremos = tracker.raw_extremes
            sugeridas = tracker.suggested_gains()
            filas = (
                ("pan", extremos["pan"], HEAD_PAN_TARGET_RANGE, sugeridas["pan"], None),
                ("tilt", extremos["tilt"], HEAD_TILT_RANGE,
                 sugeridas["tilt_down"], sugeridas["tilt_up"]),
            )
            for fila, (eje, (lo, hi), rango, sug_a, sug_b) in enumerate(filas):
                if sug_b is None:
                    texto = f"sugerida {sug_a:.2f}" if sug_a else "sugerida --"
                else:
                    abajo = f"{sug_a:.2f}" if sug_a else "--"
                    arriba = f"{sug_b:.2f}" if sug_b else "--"
                    texto = f"sugeridas v{abajo} ^{arriba}"
                cv2.putText(
                    frame,
                    f"{eje:>4s} crudo [{lo:+6.1f},{hi:+6.1f}]  robot "
                    f"[{rango[0]:+.0f},{rango[1]:+.0f}]  {texto}",
                    (10, ALTO_TEXTO + fila * 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 255, 180), 1,
                )

            if publicador is not None and pose is not None:
                # La pose YA filtrada y recortada, la misma que se dibuja y se
                # graba. El publisher limita la tasa por descarte, así que
                # llamarlo en cada frame es correcto aunque la cámara vaya más
                # deprisa que `publish_hz`.
                publicador.publish(pan=pose[0], tilt=pose[1])

            if escritor is not None and pose is not None:
                escritor.writerow(fila_csv(
                    tracker, time.perf_counter() - t0, etiqueta, pose, width, height))

            cv2.putText(
                frame, f"etiqueta {etiqueta}" if escritor else "", (10, 200),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 200, 100), 2,
            )

            if publicador is not None:
                # El contador de perdidos NO es decorativo. Con QoS 0, si el
                # enlace no está arriba, paho tira el mensaje sin encolarlo: sin
                # este número el HUD anunciaría 20 Hz mientras al broker no
                # llega absolutamente nada, que es el peor modo de fallo — el
                # que parece que funciona.
                enlace = "MQTT" if publicador.connected else "MQTT SIN ENLACE"
                cv2.putText(
                    frame,
                    f"{enlace}  {publicador.effective_hz:4.1f} Hz  "
                    f"enviados {publicador.message_count}  "
                    f"perdidos {publicador.failed_count}",
                    (10, 224), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (0, 255, 0) if publicador.connected else (0, 0, 255), 1,
                )

            cv2.imshow("Telmeow - head tracker", frame)
            tecla = cv2.waitKey(1) & 0xFF
            if tecla == ord("q"):
                break
            if tecla == ord(" "):
                etiqueta += 1
                print(f"--- etiqueta {etiqueta} ---")
            elif tecla == ord("c"):
                print("neutro recalibrado" if tracker.set_neutral() else "sin pose que calibrar")
            elif tecla == ord("n"):
                tracker.clear_neutral()
                print("neutro borrado")
            elif tecla == ord("r"):
                tracker.reset_extremes()
                print("extremos reiniciados")
            elif tecla == ord("t"):
                tracker.method = "pnp" if tracker.method == "mesh" else "mesh"
                tracker.clear_neutral()
                tracker.reset_extremes()
                print(f"metodo: {tracker.method}")
            elif tecla in (ord("z"), ord("Z")):
                paso = 0.05 if tecla == ord("Z") else -0.05
                tracker.gain_tilt_up = max(0.05, round(tracker.gain_tilt_up + paso, 2))
                print(f"ganancia tilt ARRIBA = {tracker.gain_tilt_up:.2f}")
            elif tecla in (ord("v"), ord("V")):
                paso = 0.05 if tecla == ord("V") else -0.05
                tracker.gain_tilt_down = max(0.05, round(tracker.gain_tilt_down + paso, 2))
                print(f"ganancia tilt ABAJO = {tracker.gain_tilt_down:.2f}")
            elif tecla in (ord("a"), ord("A")):
                paso = 0.05 if tecla == ord("A") else -0.05
                tracker.gain_pan = max(0.05, round(tracker.gain_pan + paso, 2))
                print(f"ganancia pan = {tracker.gain_pan:.2f}")
    finally:
        tracker.close()
        cap.release()
        cv2.destroyAllWindows()
        if publicador is not None:
            print(
                f"\nMQTT: {publicador.message_count} enviados, "
                f"{publicador.dropped_count} descartados por tasa, "
                f"{publicador.failed_count} perdidos sin enlace"
            )
            publicador.disconnect()
        if registro is not None:
            registro.close()
            print(f"\nRegistro guardado en {args.record}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_demo())
