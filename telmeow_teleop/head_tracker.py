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

CONVENCIÓN DE SIGNOS
--------------------
Lo que el módulo calcula, verificado con datos sintéticos (ver las pruebas):

- ``yaw`` positivo  → la cara mira hacia la **derecha de la imagen**.
- ``pitch`` positivo → la cara mira hacia **arriba**.

Traducirlo a «izquierda del operador» **depende del espejo**, y conviene tenerlo
escrito porque es una fuente clásica de confusión:

- Sin espejar, al operador se le ve de frente como a otra persona, así que su
  izquierda cae en la **derecha** de la imagen. Gira a su izquierda → yaw **+**.
- Espejado (``cv2.flip``, que es lo que hace el pipeline para que el operador se
  vea como en un espejo), su izquierda cae en la **izquierda** de la imagen.
  Gira a su izquierda → yaw **−**.

El pitch no se ve afectado: el volteo solo invierte el eje horizontal. Así que
``tilt`` positivo = el operador levanta la barbilla, con espejo o sin él, y eso
coincide con el robot, cuyo gesto de página inclina la cabeza hacia abajo con
``tilt = -30``.

Para el **pan queda por confirmar en el visor** hacia qué lado debe girar el
robot cuando el operador gira a su izquierda: si sale al revés, se corrige con
``invert_pan=True`` sin tocar el código.
"""

from __future__ import annotations

import math
import time

import cv2
import mediapipe as mp
import numpy as np

from .config import AppConfig
from .filters import OneEuroFilter
from .limits import HeadClamp, clamp_head

__all__ = ["HeadTracker", "estimate_head_pose", "MODEL_POINTS_3D", "LANDMARK_IDS"]


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
    yaw, pitch, roll = _euler_from_rotation(rotation)
    return math.degrees(yaw), math.degrees(pitch), math.degrees(roll)


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
        max_num_faces:
            Fijo en 1: se teleopera a una persona.
    """

    def __init__(
        self,
        gain_pan: float = 1.0,
        gain_tilt: float = 1.0,
        *,
        invert_pan: bool = False,
        invert_tilt: bool = False,
        min_cutoff: float = 0.5,
        beta: float = 0.5,
        clock=time.perf_counter,
    ) -> None:
        if gain_pan <= 0.0 or gain_tilt <= 0.0:
            raise ValueError("las ganancias deben ser positivas")

        self.gain_pan = float(gain_pan)
        self.gain_tilt = float(gain_tilt)
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

        self._neutral: tuple[float, float] | None = None
        self._raw: tuple[float, float] | None = None
        self._last: HeadClamp | None = None
        self._frames_sin_rostro = 0

    # -- estado, para el HUD ----------------------------------------------

    @property
    def last_clamp(self) -> HeadClamp | None:
        """Último recorte completo. Lleva los límites y si hubo recorte, que es
        lo que el HUD necesita para pintar las barras."""
        return self._last

    @property
    def raw_angles(self) -> tuple[float, float] | None:
        """Ángulos crudos de solvePnP, antes de neutro, ganancia y filtro."""
        return self._raw

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
        """
        if self._raw is None:
            return False
        self._neutral = self._raw
        self._filter_pan.reset()
        self._filter_tilt.reset()
        return True

    def clear_neutral(self) -> None:
        self._neutral = None
        self._filter_pan.reset()
        self._filter_tilt.reset()

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
            return None

        landmarks = resultado.multi_face_landmarks[0].landmark
        puntos = np.array(
            [(landmarks[i].x * width, landmarks[i].y * height) for i in LANDMARK_IDS],
            dtype=np.float64,
        )

        pose = estimate_head_pose(puntos, (width, height))
        if pose is None:
            self._frames_sin_rostro += 1
            return None

        yaw, pitch, _roll = pose
        if not (math.isfinite(yaw) and math.isfinite(pitch)):
            self._frames_sin_rostro += 1
            return None

        self._frames_sin_rostro = 0
        self._raw = (yaw, pitch)
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

        pan = yaw * self.gain_pan * (-1.0 if self.invert_pan else 1.0)
        tilt = pitch * self.gain_tilt * (-1.0 if self.invert_tilt else 1.0)

        ahora = self._clock() if timestamp is None else timestamp
        pan = self._filter_pan(pan, ahora)
        tilt = self._filter_tilt(tilt, ahora)

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


def _dibujar_ejes(frame_bgr, puntos, image_size) -> None:
    """Dibuja la terna de la cabeza sobre el rostro.

    Es la comprobación visual de que solvePnP converge a algo con sentido: los
    ejes deben quedar clavados en la cara y girar con ella.
    """
    width, height = image_size
    ok, rvec, tvec = cv2.solvePnP(
        MODEL_POINTS_3D,
        puntos,
        camera_matrix(width, height),
        np.zeros((4, 1)),
        flags=cv2.SOLVEPNP_ITERATIVE,
    )
    if not ok:
        return
    ejes = np.array(
        [(200.0, 0.0, 0.0), (0.0, 200.0, 0.0), (0.0, 0.0, 200.0)], dtype=np.float64
    )
    proyectados, _ = cv2.projectPoints(
        ejes, rvec, tvec, camera_matrix(width, height), np.zeros((4, 1))
    )
    origen = tuple(int(v) for v in puntos[0])
    colores = ((0, 0, 255), (0, 255, 0), (255, 0, 0))  # X rojo, Y verde, Z azul
    for punto, color in zip(proyectados.reshape(-1, 2), colores):
        cv2.line(frame_bgr, origen, tuple(int(v) for v in punto), color, 2)


def _demo() -> int:
    """`python -m telmeow_teleop.head_tracker`"""
    cfg = AppConfig.from_env()
    backends = {"dshow": cv2.CAP_DSHOW, "msmf": cv2.CAP_MSMF, "any": cv2.CAP_ANY}
    cap = cv2.VideoCapture(cfg.camera.index, backends[cfg.camera.backend])
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.camera.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.camera.height)
    if not cap.isOpened():
        print(f"No se pudo abrir la cámara {cfg.camera.index}.")
        return 1

    print(
        "c = calibrar neutro · x = quitar neutro · q = salir\n"
        "Los ejes deben quedar clavados en la cara y girar con ella."
    )

    tracker = HeadTracker()
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if cfg.camera.flip:
                frame = cv2.flip(frame, 1)

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pose = tracker.process(rgb)

            if pose is not None:
                height, width = frame.shape[:2]
                res = tracker._face_mesh.process(rgb)
                if res.multi_face_landmarks:
                    lm = res.multi_face_landmarks[0].landmark
                    puntos = np.array(
                        [(lm[i].x * width, lm[i].y * height) for i in LANDMARK_IDS],
                        dtype=np.float64,
                    )
                    _dibujar_ejes(frame, puntos, (width, height))

                recorte = tracker.last_clamp
                texto = f"pan {pose[0]:+7.2f}  tilt {pose[1]:+7.2f}"
                color = (0, 0, 255) if recorte and recorte.clamped else (0, 255, 0)
                cv2.putText(
                    frame, texto, (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2,
                )
                if tracker.raw_angles:
                    cv2.putText(
                        frame,
                        f"crudo {tracker.raw_angles[0]:+7.2f} {tracker.raw_angles[1]:+7.2f}"
                        f"   neutro: {'si' if tracker.neutral else 'no'}",
                        (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1,
                    )
            else:
                cv2.putText(
                    frame, "sin rostro", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 165, 255), 2,
                )

            cv2.imshow("Telmeow - head tracker", frame)
            tecla = cv2.waitKey(1) & 0xFF
            if tecla == ord("q"):
                break
            if tecla == ord("c"):
                print("neutro calibrado" if tracker.set_neutral() else "sin pose que calibrar")
            if tecla == ord("x"):
                tracker.clear_neutral()
                print("neutro eliminado")
    finally:
        tracker.close()
        cap.release()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(_demo())
