"""Reconocimiento de la emoción del operador, con backends intercambiables.

Reemplaza a ``legacy/Emotion Recognition.py`` (PROMPT 6). La emoción que se lee
en la cara del narrador viaja en el campo ``emo`` y el visor la pinta en los
ojos del robot, con un fundido de 350 ms.

QUÉ CAMBIA RESPECTO AL LEGACY
-----------------------------
- **Bug 12** — ``model.predict()`` en cada frame. Ahora ``model(x,
  training=False)``, que da **exactamente** las mismas probabilidades y cuesta
  10 ms frente a 66 (medido el 2026-10-19, mediana de 50 llamadas sobre el
  modelo propio), y solo a ``emotion_hz`` (5 Hz), no por frame.
- **Bug 15** — el ``CascadeClassifier`` se construía dentro de ``detect_face``,
  una vez por frame: ~900 KB de XML parseados 30 veces por segundo. Ahora una
  sola vez, al construir el clasificador.
- **Bug 11** — sin suavizado, la clase parpadeaba entre frames. Ahora pasa por
  ``filters.ClassHysteresis``, con mayoría y confianza mínima.
- **El orden de clases ya no está escrito en el código**: se lee del JSON de
  metadatos que acompaña al modelo (``resolver_clases``). Un orden equivocado
  no da ningún error, solo predicciones malas, que es el peor modo de fallo.

LO QUE NO CAMBIA, Y A PROPÓSITO: EL PREPROCESAMIENTO
----------------------------------------------------
El modelo propio aprendió de los recortes que producía un pipeline concreto, y
cualquier diferencia en la inferencia es un cambio de dominio que degrada las
predicciones en silencio. Se conserva, verificado en los tres scripts legacy:

1. El cascade Haar **de OpenCV** (``cv2.data.haarcascades``, 930.127 bytes), no
   la copia de ``legacy/``, que es otro archivo (H16).
2. ``detectMultiScale(gris, scaleFactor=1.1, minNeighbors=5)`` sobre el frame
   entero.
3. Recorte del rectángulo → ``resize(48, 48)`` **sobre BGR** →
   ``cvtColor(BGR2GRAY)`` → ``/255``. Lo habitual es pasar a gris antes de
   redimensionar; aquí no, y como es igual en entrenamiento e inferencia es
   correcto. **No se «arregla».**

Dos cosas que el legacy no hacía, y por qué ninguna rompe lo anterior:

- **Se elige el rostro MAYOR, no el primero que devuelve el detector.** En el
  dataset propio el Haar da más de una detección en 168 de 8000 imágenes, y en
  unas cien de ellas la primera no es la mayor. Con una sola cara en cuadro las
  dos reglas coinciden; con un falso positivo, la mayor es la cara del
  operador. Y la «primera» ni siquiera es fija: OpenCV devuelve las
  detecciones en distinto orden entre llamadas idénticas (ver
  ``elegir_rostro``). ``criterio="primera"`` se acerca a lo que vio el
  entrenamiento, para el P9.
- **Se deshace el espejo antes de detectar.** El dataset se grabó con la
  cámara tal cual (``Creation_of_dataset.py`` no voltea), y el pipeline voltea
  el frame para que el operador se vea como en un espejo. Clasificar la cara
  volteada es pedirle al modelo una imagen que nunca vio, y se nota: sobre las
  7459 caras del dataset propio acierta el **99.9 %** con la cara como se grabó
  y el **59 %** volteada (feliz 50 %, triste 32 %). Ver ``frame_espejado`` y
  ``tools/medir_emocion.py``.

LOS BACKENDS RECIBEN EL RECORTE, NO EL 48×48
--------------------------------------------
El plan escribía la interfaz como ``predict(face_gray_48x48)``. Aquí cada
backend recibe **el recorte del rostro tal como sale del detector** (BGR, a su
tamaño) y lo lleva él a la entrada de su red. El motivo es el mismo principio
de arriba aplicado a los demás: HSEmotion se entrenó con caras en COLOR a
224×224, y darle un 48×48 gris lo mide con una entrada que no es la suya.
Medido sobre el dataset propio, pierde poco en total (75 % → 72 %) pero cambia
QUÉ acierta —sorprendido del 89 al 60 %, neutral del 21 al 53 %—, que es
justo lo que el P9 reporta por clase. Lo común a los tres es el detector y el
recorte, que es lo que hace que comparen las mismas caras. Para el modelo
propio no cambia nada: su ``preprocesar_48`` es el del entrenamiento, línea a
línea.

CONVENIOS DE COLOR Y DE ESPEJO — los fallos silenciosos de este módulo
----------------------------------------------------------------------
- ``EmotionClassifier.process`` recibe el frame en **BGR**, tal como sale de la
  cámara, y NO el RGB que el P7 comparte con MediaPipe. ``BGR2GRAY`` sobre un
  RGB pesa el rojo como si fuera azul (0.114 en vez de 0.299): sale otra cara
  gris, y ningún error. Medido sobre el dataset, al modelo propio no le cambia
  ni un acierto; se respeta igualmente porque es el convenio del entrenamiento.
- **HSEmotion espera RGB** (``PIL.Image.fromarray`` y la normalización de
  ImageNet van en ese orden); su backend convierte. **DeepFace espera BGR**,
  como ``cv2.imread``.
- **El frame llega volteado** (``cfg.camera.flip``), y el clasificador lo
  vuelve a voltear antes de detectar. El rectángulo que expone para el HUD
  está en las coordenadas del frame que recibió, el volteado.

KERAS 3 DIRECTO, NO ``tf.keras``
--------------------------------
DeepFace fija ``TF_USE_LEGACY_KERAS=1`` al importarse, y con eso ``tf.keras``
pasa a ser Keras 2, que no lee el ``.keras`` guardado con Keras 3.3.3
(comprobado: ``tf_keras`` 2.16 rechaza su ``InputLayer`` por ``batch_shape``). El
backend propio importa el paquete ``keras`` (Keras 3) directamente, que no mira
esa variable, así que los dos backends pueden convivir en el mismo proceso
sin que el orden de los imports decida cuál funciona.
"""

from __future__ import annotations

import json
import math
import time
import warnings
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import NamedTuple

import cv2
import numpy as np

from .config import EMOTION_LABELS, VIEWER_EMOTIONS, AppConfig
from .filters import ClassHysteresis

__all__ = [
    "BACKENDS",
    "COLAPSOS",
    "EQUIVALENTES",
    "SIN_EQUIVALENTE",
    "LADO",
    "NEUTRAL",
    "BackendNoDisponible",
    "OrdenDeClasesSupuesto",
    "Prediccion",
    "Distribucion",
    "Emocion",
    "EmotionBackend",
    "KerasBackend",
    "DeepFaceBackend",
    "HSEmotionBackend",
    "EmotionClassifier",
    "crear_backend",
    "colapsar",
    "ruta_cascade",
    "cargar_cascade",
    "detectar_rostro",
    "elegir_rostro",
    "preprocesar_48",
    "ruta_metadatos",
    "leer_metadatos",
    "resolver_clases",
]

#: Backends disponibles, en el orden en que los recorre la tecla ``b`` del demo.
#: ``config.EMOTION_BACKENDS`` repite la lista (sin importar este módulo) y una
#: prueba comprueba que coinciden.
BACKENDS: tuple[str, ...] = ("keras", "deepface", "hsemotion")

#: Lado de la entrada de los modelos propios: el legacy y el de FER-2013 (P8).
LADO = 48

#: Expresión de arranque, por decisión de diseño del plan (P6, punto 7).
NEUTRAL = "neutral"

#: Parámetros de ``detectMultiScale`` de los tres scripts legacy. Cambiarlos
#: cambia qué rectángulo se recorta, y con él la entrada del modelo.
HAAR_SCALE_FACTOR = 1.1
HAAR_MIN_NEIGHBORS = 5

#: Holgura de la fecha límite de la inferencia, en segundos. Solo absorbe el
#: redondeo de ir sumando el periodo (0.2 + 0.2 + … no da exactamente 1.6):
#: sin ella, un frame que llega justo en su ranura se la salta.
_HOLGURA_S = 1e-6

#: Nombre del cascade dentro de ``cv2.data.haarcascades``.
CASCADE = "haarcascade_frontalface_default.xml"

#: Clases de las librerías que SÍ tienen expresión en el robot. Las claves van en
#: minúsculas: DeepFace las da así ('happy') y HSEmotion con mayúscula
#: ('Happiness'), y se comparan sin distinguir.
EQUIVALENTES: dict[str, str] = {
    "happy": "feliz",
    "happiness": "feliz",
    "sad": "triste",
    "sadness": "triste",
    "surprise": "sorprendido",
    "neutral": "neutral",
    "angry": "enojado",
    "anger": "enojado",
}

#: Clases que el robot NO tiene, y la expresión más parecida, que es adonde va
#: su probabilidad con ``colapso="cercana"``:
#:
#: - asco y desprecio → enojado: con la ira forman la «tríada hostil» de Izard
#:   (1977), las tres emociones de rechazo hacia otro.
#: - miedo → sorprendido: comparten las unidades de acción 1, 2, 5 y 26 (cejas
#:   arriba, párpado superior levantado, boca abierta), y es la confusión
#:   clásica de los estudios transculturales de Ekman.
#:
#: Es un criterio sobre la CARA, que es lo que el robot enseña, no sobre la
#: emoción. Antes de citar las referencias en la tesis, verificarlas.
SIN_EQUIVALENTE: dict[str, str] = {
    "disgust": "enojado",
    "contempt": "enojado",
    "fear": "sorprendido",
}

#: ``"cercana"`` suma la probabilidad de las clases sin equivalente a la
#: expresión más parecida; ``"ninguna"`` las deja fuera, y si gana una de ellas
#: la predicción no tiene etiqueta (no vota).
COLAPSOS: tuple[str, ...] = ("cercana", "ninguna")


class BackendNoDisponible(ImportError):
    """Un backend opcional no está instalado.

    Es una ``ImportError`` para que quien ya las capture no tenga que cambiar,
    pero con un mensaje que dice qué hacer. El programa sigue funcionando con el
    backend ``keras``: los otros dos solo hacen falta para el P9.
    """

    def __init__(self, nombre: str, causa: BaseException | None = None) -> None:
        super().__init__(
            f"El backend '{nombre}' no está instalado ({causa}). Es opcional: "
            f"se instala en un entorno aparte con 'pip install -r "
            f"requirements-benchmark.txt' (ver la cabecera de ese archivo). "
            f"El clasificador sigue funcionando con 'keras'."
        )
        self.nombre = nombre


class OrdenDeClasesSupuesto(UserWarning):
    """El orden de clases del modelo no se leyó: se supuso el legacy."""


# --------------------------------------------------------------------------
# Detección y preprocesamiento — idénticos al entrenamiento
# --------------------------------------------------------------------------


def ruta_cascade(configurada: Path | None = None) -> Path:
    """La ruta configurada o, si es ``None``, la del cascade de OpenCV.

    ``None`` es el valor por defecto de ``config.PathConfig.face_cascade`` y
    significa «el que usaron los tres scripts legacy» (P6, punto 9).
    """
    if configurada is not None:
        return Path(configurada)
    return Path(cv2.data.haarcascades) / CASCADE


def cargar_cascade(ruta: Path | None = None):
    """Construye el clasificador Haar UNA vez, y comprueba que cargó.

    OpenCV no lanza nada con una ruta mala: devuelve un clasificador vacío que
    solo revienta más tarde, dentro de ``detectMultiScale``, con un mensaje que
    no menciona el archivo. Se comprueba aquí para que el error diga cuál es.
    """
    ruta = ruta_cascade(ruta)
    cascade = cv2.CascadeClassifier(str(ruta))
    if cascade.empty():
        raise FileNotFoundError(
            f"No se pudo cargar el cascade Haar de {ruta}. OpenCV no avisa con "
            f"una ruta mala: devuelve un clasificador vacío."
        )
    return cascade


def elegir_rostro(caras, criterio: str = "mayor") -> tuple[int, int, int, int] | None:
    """Elige un rectángulo ``(x, y, w, h)`` de los que devolvió el detector.

    ``"mayor"`` (por defecto) toma el de más área: con un falso positivo en el
    fondo, la cara del operador es la grande. Y es **determinista**: depende
    solo del conjunto de rectángulos, con desempate por posición.

    ``"primera"`` toma el primero de la lista, como hacían los scripts legacy
    —y por tanto el entrenamiento—. Pero **el orden de la lista no es fijo**:
    OpenCV reparte la detección en varios hilos, y entre dos llamadas idénticas
    devolvió el mismo conjunto en otro orden en 16 de 40 imágenes con varias
    detecciones (con un hilo, nunca). En esas fotos la «primera» del
    entrenamiento fue al azar, así que el P9 puede acercarse a sus recortes con
    este criterio, pero no reproducirlos exactamente.

    Con una sola detección, las dos dan lo mismo.
    """
    if criterio not in ("mayor", "primera"):
        raise ValueError(f"criterio debe ser 'mayor' o 'primera', se recibió {criterio!r}")
    if len(caras) == 0:
        return None
    if criterio == "primera":
        x, y, w, h = caras[0]
    else:
        # Área, y a igual área la de más arriba y luego la de más a la
        # izquierda: así el resultado no depende del orden de la lista.
        x, y, w, h = max(caras, key=lambda c: (int(c[2]) * int(c[3]), -int(c[1]), -int(c[0])))
    return int(x), int(y), int(w), int(h)


def detectar_rostro(
    frame_bgr: np.ndarray,
    cascade,
    *,
    criterio: str = "mayor",
    min_lado: int | None = None,
) -> tuple[int, int, int, int] | None:
    """Rectángulo ``(x, y, w, h)`` del rostro, o ``None`` si no hay ninguno.

    Mismos parámetros que el legacy (``scaleFactor=1.1``, ``minNeighbors=5``),
    sobre el frame entero pasado a gris.

    ``min_lado`` pasa ``minSize`` al detector, que deja de buscar caras más
    pequeñas. Aquí vale ``None`` por defecto, que es el detector del legacy tal
    cual; el clasificador usa 60 (``config.EmotionConfig.min_lado_rostro``), que
    lo deja en la mitad de tiempo sin cambiar el recorte de ninguna cara del
    dataset (solo el de 7 falsos positivos pequeños).
    """
    gris = frame_bgr if frame_bgr.ndim == 2 else cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    extra = {} if not min_lado else {"minSize": (int(min_lado), int(min_lado))}
    caras = cascade.detectMultiScale(
        gris,
        scaleFactor=HAAR_SCALE_FACTOR,
        minNeighbors=HAAR_MIN_NEIGHBORS,
        **extra,
    )
    return elegir_rostro(caras, criterio)


def _comprobar_recorte(rostro: np.ndarray) -> None:
    if not isinstance(rostro, np.ndarray) or rostro.size == 0:
        raise ValueError("el recorte del rostro está vacío")
    if rostro.dtype != np.uint8:
        # Un recorte en float ya dividido entre 255 se volvería a dividir, y la
        # red vería una cara casi negra sin ningún error.
        raise ValueError(
            f"se esperaba un recorte uint8, como el de la cámara; llegó {rostro.dtype}"
        )
    if not (rostro.ndim == 2 or (rostro.ndim == 3 and rostro.shape[2] in (1, 3))):
        raise ValueError(f"forma de recorte no válida: {rostro.shape}")


def preprocesar_48(rostro: np.ndarray) -> np.ndarray:
    """Recorte de rostro → tensor ``(1, 48, 48, 1)`` como en el entrenamiento.

    Orden de los tres scripts legacy, y **hay que conservarlo**: ``resize`` a
    48×48 sobre el recorte en BGR, luego ``BGR2GRAY``, luego ``/255``. El
    ``resize`` usa la interpolación por defecto de OpenCV (bilineal), que es la
    que usó el legacy al no indicar ninguna.

    La división se hace en float64, como en el legacy (``face / 255.0``), y se
    pasa a float32 al final, que es lo que Keras habría hecho por su cuenta: el
    tensor es el mismo bit a bit.

    Acepta también un recorte YA en gris (2D o con un canal), que es como vienen
    las imágenes de FER-2013 (P8, P9): ahí no hay color que convertir.
    """
    _comprobar_recorte(rostro)
    if rostro.ndim == 3 and rostro.shape[2] == 1:
        rostro = rostro[:, :, 0]
    reducido = cv2.resize(rostro, (LADO, LADO))
    if reducido.ndim == 3:
        reducido = cv2.cvtColor(reducido, cv2.COLOR_BGR2GRAY)
    normalizado = reducido / 255.0
    return normalizado.reshape(1, LADO, LADO, 1).astype(np.float32)


def _a_bgr(rostro: np.ndarray) -> np.ndarray:
    """El recorte en BGR de 3 canales, para las librerías que lo piden."""
    _comprobar_recorte(rostro)
    if rostro.ndim == 3 and rostro.shape[2] == 3:
        return np.ascontiguousarray(rostro)
    if rostro.ndim == 3:
        rostro = rostro[:, :, 0]
    return cv2.cvtColor(rostro, cv2.COLOR_GRAY2BGR)


# --------------------------------------------------------------------------
# Orden de clases del modelo propio
# --------------------------------------------------------------------------


def ruta_metadatos(ruta_modelo: Path) -> Path:
    """``emotion_model.keras`` → ``emotion_model.meta.json``.

    Es el nombre que fija el P8 para el modelo de FER-2013
    (``emotion_model_fer2013.meta.json``).
    """
    return Path(ruta_modelo).with_suffix(".meta.json")


def leer_metadatos(ruta: Path) -> dict:
    """Lee y valida el JSON de metadatos de un modelo.

    Lo único obligatorio es ``"clases"``: la lista de etiquetas **en el orden de
    las salidas de la red**. Cada una tiene que ser una de ``EMOTION_LABELS``, y
    sin repetir. No hace falta que estén las cinco —un modelo entrenado con un
    dataset que no tenga sorpresa tendría cuatro—, pero sí que sean del robot:
    una etiqueta en inglés o mal escrita se rechaza aquí en vez de llegar al
    visor, que la ignoraría sin avisar.

    El resto de campos (fecha, accuracy, hash del dataset, preprocesamiento…)
    son documentación y se conservan tal cual en ``KerasBackend.metadatos``.
    """
    ruta = Path(ruta)
    try:
        meta = json.loads(ruta.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{ruta.name} no es un JSON válido: {exc}") from exc
    if not isinstance(meta, dict) or "clases" not in meta:
        raise ValueError(f"{ruta.name} no tiene el campo 'clases'")
    clases = meta["clases"]
    if not isinstance(clases, list) or not all(isinstance(c, str) for c in clases):
        raise ValueError(f"'clases' de {ruta.name} debe ser una lista de cadenas")
    if len(set(clases)) != len(clases):
        raise ValueError(f"'clases' de {ruta.name} tiene etiquetas repetidas: {clases}")
    ajenas = [c for c in clases if c not in EMOTION_LABELS]
    if ajenas:
        raise ValueError(
            f"'clases' de {ruta.name} incluye {ajenas}, que no son expresiones "
            f"del robot {EMOTION_LABELS}"
        )
    return meta


def resolver_clases(
    ruta_modelo: Path, n_salidas: int, ruta_meta: Path | None = None
) -> tuple[tuple[str, ...], dict | None]:
    """El orden de clases de un modelo: de sus metadatos, o el legacy con aviso.

    El orden **no se infiere** (P6, punto 1). Si el JSON existe, manda, y tiene
    que tener tantas clases como salidas la red. Si no existe:

    - con 5 salidas se supone el orden legacy (``EMOTION_LABELS``) y se emite un
      ``OrdenDeClasesSupuesto``: si el modelo se entrenó con otro orden, cada
      predicción saldrá con la etiqueta de otra clase y nada más lo delatará;
    - con otro número de salidas no hay nada razonable que suponer, y se lanza.
    """
    ruta_meta = ruta_metadatos(ruta_modelo) if ruta_meta is None else Path(ruta_meta)
    if ruta_meta.is_file():
        meta = leer_metadatos(ruta_meta)
        clases = tuple(meta["clases"])
        if len(clases) != n_salidas:
            raise ValueError(
                f"{ruta_meta.name} declara {len(clases)} clases y la red tiene "
                f"{n_salidas} salidas"
            )
        return clases, meta
    if n_salidas != len(EMOTION_LABELS):
        raise ValueError(
            f"No existe {ruta_meta.name} y la red tiene {n_salidas} salidas: sin "
            f"metadatos no hay orden de clases que suponer"
        )
    # Se fuerza a mostrarlo aunque alguien haya silenciado los warnings, y no es
    # hipotético: DeepFace hace warnings.filterwarnings("ignore") al importarse,
    # y el P9 lo importa en el mismo proceso que este backend.
    with warnings.catch_warnings():
        warnings.simplefilter("always", OrdenDeClasesSupuesto)
        warnings.warn(
            f"No existe {ruta_meta.name}: se SUPONE el orden legacy {EMOTION_LABELS}. "
            f"Si el modelo se entrenó con otro orden, las predicciones saldrán mal "
            f"sin ningún error.",
            OrdenDeClasesSupuesto,
            stacklevel=3,
        )
    return EMOTION_LABELS, None


# --------------------------------------------------------------------------
# Lo que devuelve un backend
# --------------------------------------------------------------------------


class Prediccion(NamedTuple):
    """La interfaz común del P6: ``predict(rostro) -> (etiqueta, confianza)``."""

    etiqueta: str | None
    confianza: float


@dataclass(frozen=True)
class Distribucion:
    """Todo lo que dice un backend de un rostro, para el HUD y el registro.

    Attributes:
        etiqueta: la expresión del robot que gana, o ``None`` si ganó una clase
            sin equivalente y el colapso es ``"ninguna"`` (no vota).
        confianza: su probabilidad, en [0, 1].
        probabilidades: una por expresión del robot (``EMOTION_LABELS``), ya
            colapsadas.
        nativa: la clase ganadora con el nombre del backend ('fear',
            'Happiness'…). En el backend propio coincide con ``etiqueta``.
    """

    etiqueta: str | None
    confianza: float
    probabilidades: Mapping[str, float]
    nativa: str


def _validar_colapso(colapso: str) -> None:
    if colapso not in COLAPSOS:
        raise ValueError(f"colapso debe ser uno de {COLAPSOS}, se recibió {colapso!r}")


def colapsar(nativas: Mapping[str, float], colapso: str = "cercana") -> Distribucion:
    """Lleva las clases de una librería a las cinco expresiones del robot.

    Se mapea **por nombre**, nunca por posición: el orden de las clases es un
    convenio de cada librería y de cada versión, y suponerlo es justo el fallo
    silencioso que este módulo existe para evitar. Una clase que no esté en
    ``EQUIVALENTES`` ni en ``SIN_EQUIVALENTE`` se rechaza: significa que la
    librería cambió y hay que revisar el mapeo, no adivinarlo.

    Las puntuaciones se normalizan por su suma, así que da igual que lleguen en
    porcentaje (DeepFace) o en probabilidad (HSEmotion).

    Con ``"cercana"`` la probabilidad de cada clase sin equivalente se SUMA a su
    expresión más parecida, y gana la expresión con más masa. Puede ganar una
    que no era la clase nativa ganadora: con enfado 0.30, asco 0.25 y alegría
    0.45, gana ``enojado`` con 0.55. Es lo que habría aprendido un modelo
    entrenado con las clases ya fusionadas.

    Con ``"ninguna"`` gana la clase nativa con más probabilidad, y si es una sin
    equivalente la etiqueta es ``None``.
    """
    _validar_colapso(colapso)
    if not nativas:
        raise ValueError("no hay puntuaciones que colapsar")
    valores = {str(k): float(v) for k, v in nativas.items()}
    total = sum(valores.values())
    if not math.isfinite(total) or total <= 0.0 or min(valores.values()) < 0.0:
        raise ValueError(f"puntuaciones no válidas: {valores}")
    p = {k: v / total for k, v in valores.items()}

    probabilidades = dict.fromkeys(EMOTION_LABELS, 0.0)
    for nombre, prob in p.items():
        clave = nombre.lower()
        if clave in EQUIVALENTES:
            probabilidades[EQUIVALENTES[clave]] += prob
        elif clave in SIN_EQUIVALENTE:
            if colapso == "cercana":
                probabilidades[SIN_EQUIVALENTE[clave]] += prob
        else:
            raise ValueError(
                f"clase desconocida {nombre!r}: la librería devolvió una etiqueta "
                f"que este módulo no sabe mapear. Revisar EQUIVALENTES."
            )

    nativa = max(p, key=p.get)
    if colapso == "cercana":
        etiqueta = max(probabilidades, key=probabilidades.get)
        return Distribucion(etiqueta, probabilidades[etiqueta], probabilidades, nativa)
    if nativa.lower() in SIN_EQUIVALENTE:
        return Distribucion(None, p[nativa], probabilidades, nativa)
    etiqueta = EQUIVALENTES[nativa.lower()]
    return Distribucion(etiqueta, probabilidades[etiqueta], probabilidades, nativa)


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------


class EmotionBackend(ABC):
    """Interfaz común de los tres enfoques que compara el capítulo de resultados.

    Un backend recibe el **recorte del rostro** —BGR uint8, del tamaño que dio
    el detector— y aplica el preprocesamiento con el que se entrenó SU red. Ver
    el docstring del módulo para por qué no recibe el 48×48 gris.
    """

    #: Nombre corto, uno de ``BACKENDS``.
    nombre: str = ""

    @abstractmethod
    def distribucion(self, rostro_bgr: np.ndarray) -> Distribucion:
        """Clasifica un recorte de rostro y lo cuenta todo."""

    def predict(self, rostro_bgr: np.ndarray) -> Prediccion:
        """``(etiqueta, confianza)`` de un recorte de rostro."""
        d = self.distribucion(rostro_bgr)
        return Prediccion(d.etiqueta, d.confianza)


class KerasBackend(EmotionBackend):
    """El modelo propio: el legacy (5 clases) o el de FER-2013 (P8).

    El orden de clases sale de ``<modelo>.meta.json`` (``resolver_clases``).

    Al construirlo se hace una inferencia en vacío: la primera llamada a la red
    cuesta mucho más que las siguientes (medido entre 74 y 640 ms según el
    proceso, frente a 9-11), y es mejor pagarla al arrancar que en el primer
    frame del bucle. De paso se comprueba
    que la red termina en softmax: si diera logits, la «confianza» no sería una
    probabilidad y el umbral de la histéresis no significaría nada.
    """

    nombre = "keras"

    def __init__(self, ruta_modelo: Path, *, ruta_meta: Path | None = None) -> None:
        ruta_modelo = Path(ruta_modelo)
        if not ruta_modelo.is_file():
            raise FileNotFoundError(
                f"No existe el modelo de emociones {ruta_modelo}. Está fuera de "
                f"git (*.keras en .gitignore): hay que copiarlo a mano."
            )
        # Import perezoso: TensorFlow tarda ~6 s en cargar, y quien solo quiere
        # las funciones puras de este módulo no tiene por qué pagarlo. Y es
        # 'keras' (Keras 3) y no 'tf.keras': ver el docstring del módulo.
        import keras

        self.ruta = ruta_modelo
        self._modelo = keras.saving.load_model(ruta_modelo, compile=False)
        entrada = tuple(self._modelo.input_shape[1:])
        if entrada != (LADO, LADO, 1):
            raise ValueError(
                f"{ruta_modelo.name} espera {entrada} y el preprocesamiento da "
                f"({LADO}, {LADO}, 1)"
            )
        n_salidas = int(self._modelo.output_shape[-1])
        self.clases, self.metadatos = resolver_clases(ruta_modelo, n_salidas, ruta_meta)

        salida = self._inferir(np.zeros((1, LADO, LADO, 1), dtype=np.float32))
        if np.any(salida < 0.0) or abs(float(salida.sum()) - 1.0) > 1e-3:
            raise ValueError(
                f"{ruta_modelo.name} no termina en softmax (la salida suma "
                f"{float(salida.sum()):.3f}): su confianza no sería una probabilidad"
            )

    @property
    def modelo(self):
        """La red de Keras, para las herramientas de medida (``tools/``)."""
        return self._modelo

    def _inferir(self, x: np.ndarray) -> np.ndarray:
        # model(x, training=False) y NO model.predict(x): mismas probabilidades,
        # 7 veces más rápido, y sin el sobrecoste por llamada de predict (bug 12).
        return np.asarray(self._modelo(x, training=False))[0]

    def distribucion(self, rostro_bgr: np.ndarray) -> Distribucion:
        salida = self._inferir(preprocesar_48(rostro_bgr))
        probabilidades = dict.fromkeys(EMOTION_LABELS, 0.0)
        for clase, valor in zip(self.clases, salida):
            probabilidades[clase] = float(valor)
        indice = int(np.argmax(salida))
        etiqueta = self.clases[indice]
        return Distribucion(etiqueta, float(salida[indice]), probabilidades, etiqueta)


class DeepFaceBackend(EmotionBackend):
    """DeepFace (Serengil), su modelo de emociones de 7 clases.

    Se le pasa el recorte con ``detector_backend="skip"``: la cara ya está
    detectada, y que DeepFace buscara otra con su propio detector rompería la
    regla de que los tres backends clasifiquen las mismas caras. Recibe BGR,
    que es su convenio (el de ``cv2.imread``).

    La primera vez en una máquina descarga sus pesos de GitHub a
    ``~/.deepface``; se fuerza al construirlo, con una inferencia en vacío.
    """

    nombre = "deepface"

    def __init__(self, *, colapso: str = "cercana") -> None:
        _validar_colapso(colapso)
        try:
            from deepface import DeepFace
        except ImportError as exc:
            raise BackendNoDisponible(self.nombre, exc) from exc
        self.colapso = colapso
        self._deepface = DeepFace
        self.distribucion(np.zeros((LADO, LADO, 3), dtype=np.uint8))

    def distribucion(self, rostro_bgr: np.ndarray) -> Distribucion:
        resultado = self._deepface.analyze(
            img_path=_a_bgr(rostro_bgr),
            actions=("emotion",),
            detector_backend="skip",
            enforce_detection=False,
            silent=True,
        )
        # Una lista de un elemento por cara en las versiones actuales; un dict
        # suelto en las antiguas.
        if isinstance(resultado, list):
            resultado = resultado[0]
        return colapsar(resultado["emotion"], self.colapso)


class HSEmotionBackend(EmotionBackend):
    """HSEmotion (Savchenko), EfficientNet-B0 entrenada en AffectNet, en CPU.

    El modelo por defecto, ``enet_b0_8_best_afew``, tiene 8 clases: las 5 del
    robot más asco, miedo y desprecio. El orden se lee del propio paquete
    (``idx_to_class``), no se escribe aquí.

    **Recibe RGB**: ``predict_emotions`` hace ``PIL.Image.fromarray`` y normaliza
    con las medias de ImageNet en ese orden. El backend convierte; pasarle el
    BGR del recorte le cambiaría el color de la piel sin ningún error.

    La primera vez en una máquina descarga el modelo de GitHub a
    ``~/.hsemotion``.
    """

    nombre = "hsemotion"

    def __init__(self, modelo: str = "enet_b0_8_best_afew", *, colapso: str = "cercana") -> None:
        _validar_colapso(colapso)
        try:
            from hsemotion.facial_emotions import HSEmotionRecognizer
        except ImportError as exc:
            raise BackendNoDisponible(self.nombre, exc) from exc
        self.colapso = colapso
        self.modelo = modelo
        self._reconocedor = HSEmotionRecognizer(model_name=modelo, device="cpu")
        indices = self._reconocedor.idx_to_class
        self._clases = tuple(indices[i] for i in range(len(indices)))
        self.distribucion(np.zeros((LADO, LADO, 3), dtype=np.uint8))

    def distribucion(self, rostro_bgr: np.ndarray) -> Distribucion:
        rgb = cv2.cvtColor(_a_bgr(rostro_bgr), cv2.COLOR_BGR2RGB)
        _, puntuaciones = self._reconocedor.predict_emotions(rgb, logits=False)
        # zip y no indexado: los modelos '_mtl' añaden valencia y activación al
        # final del vector, y esas dos no son clases.
        return colapsar(dict(zip(self._clases, map(float, puntuaciones))), self.colapso)


def crear_backend(nombre: str, cfg: AppConfig | None = None) -> EmotionBackend:
    """Construye un backend por su nombre, con la configuración dada.

    Raises:
        BackendNoDisponible: si es ``deepface`` o ``hsemotion`` y la librería no
            está instalada.
    """
    cfg = cfg or AppConfig()
    if nombre == "keras":
        return KerasBackend(cfg.paths.emotion_model)
    if nombre == "deepface":
        return DeepFaceBackend(colapso=cfg.emotion.colapso)
    if nombre == "hsemotion":
        return HSEmotionBackend(cfg.emotion.hsemotion_model, colapso=cfg.emotion.colapso)
    raise ValueError(f"backend desconocido {nombre!r}; válidos: {BACKENDS}")


# --------------------------------------------------------------------------
# El clasificador
# --------------------------------------------------------------------------


class Emocion(NamedTuple):
    """Lo que devuelve ``process``: la expresión que se manda al robot.

    ``etiqueta`` nunca es ``None``: es siempre una de las seis del visor.

    ``confianza`` es la confianza media de los votos que sostienen esa etiqueta
    en la ventana de la histéresis, y vale ``None`` cuando no hay una medida
    que la respalde **ahora**: en modo manual, antes de la primera inferencia,
    si la última inferencia no encontró cara o si la clase ganadora no tenía
    equivalente. No se rellena con el último valor conocido a propósito: un
    número congelado que parece vivo es el H31.
    """

    etiqueta: str
    confianza: float | None


class EmotionClassifier:
    """Del frame de la cámara a la expresión del robot.

    Cadena, a ``emotion_hz`` y no por frame:

    1. Deshacer el espejo del frame (``frame_espejado``).
    2. Detectar el rostro con el Haar de OpenCV y recortarlo.
    3. Clasificar el recorte con el backend.
    4. Estabilizar con ``ClassHysteresis`` (mayoría y confianza mínima).

    Entre inferencias, ``process`` devuelve lo último decidido. Sin cara, la
    expresión se mantiene: no se vuelve a neutral, igual que la cabeza del robot
    se queda donde estaba cuando el head tracker pierde el rostro.

    Args:
        backend: el ``EmotionBackend`` que clasifica.
        emotion_hz: inferencias por segundo. Se planifican por fecha límite, así
            que la media es exacta aunque los frames lleguen a otra tasa.
        n_frames, min_confidence: los de ``ClassHysteresis``. ``n_frames``
            cuenta INFERENCIAS: a 5 Hz, 5 son una ventana de 1 s.
        cascade: ruta del cascade Haar; ``None`` es el de OpenCV.
        frame_espejado: si el frame llega volteado (``cfg.camera.flip``). Es un
            hecho de la captura, no una preferencia: el dataset se grabó sin
            voltear, y la cara se clasifica en esa orientación.
        criterio_rostro, min_lado_rostro: ver ``elegir_rostro`` y
            ``detectar_rostro``.
        detector: sustituye al Haar por otra función ``frame -> (x, y, w, h) |
            None``. Solo para las pruebas.
        clock: fuente de tiempo de la planificación. ``perf_counter`` (§0.7).
    """

    def __init__(
        self,
        backend: EmotionBackend,
        *,
        emotion_hz: float = 5.0,
        n_frames: int = 5,
        min_confidence: float = 0.6,
        cascade: Path | None = None,
        frame_espejado: bool = True,
        criterio_rostro: str = "mayor",
        min_lado_rostro: int | None = 60,
        detector: Callable[[np.ndarray], tuple[int, int, int, int] | None] | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        if not emotion_hz > 0.0:
            raise ValueError(f"emotion_hz debe ser positivo, se recibió {emotion_hz!r}")
        self.backend = backend
        self.frame_espejado = bool(frame_espejado)
        self._periodo = 1.0 / float(emotion_hz)
        self._histeresis = ClassHysteresis(n_frames, min_confidence, initial=NEUTRAL)
        if detector is None:
            # UNA sola vez (bug 15). elegir_rostro valida el criterio aquí, y
            # no en el primer frame.
            elegir_rostro((), criterio_rostro)
            detector = partial(
                detectar_rostro,
                cascade=cargar_cascade(cascade),
                criterio=criterio_rostro,
                min_lado=min_lado_rostro,
            )
        self._detector = detector
        self._clock = clock

        self._manual: str | None = None
        self._proxima: float | None = None
        self._ultima: Distribucion | None = None
        self._rostro: tuple[int, int, int, int] | None = None
        self._t_ultima: float | None = None
        self._inferencias = 0
        self._sin_rostro = 0
        self._latencia_ms: float | None = None

    @classmethod
    def desde_config(
        cls, cfg: AppConfig, backend: EmotionBackend | None = None
    ) -> "EmotionClassifier":
        """El clasificador con todo lo que dice la configuración."""
        return cls(
            backend if backend is not None else crear_backend(cfg.emotion.backend, cfg),
            emotion_hz=cfg.rates.emotion_hz,
            n_frames=cfg.emotion.n_frames,
            min_confidence=cfg.emotion.min_confidence,
            cascade=cfg.paths.face_cascade,
            frame_espejado=cfg.camera.flip,
            min_lado_rostro=cfg.emotion.min_lado_rostro or None,
        )

    # -- estado, para el HUD y el registro ---------------------------------

    @property
    def emocion(self) -> Emocion:
        """Lo que se manda al robot ahora mismo. Ver ``Emocion``."""
        if self._manual is not None:
            return Emocion(self._manual, None)
        actual = self._histeresis.current
        if self._ultima is None or self._ultima.etiqueta is None:
            return Emocion(actual, None)
        votos = [c for etiqueta, c in self._histeresis.window if etiqueta == actual]
        return Emocion(actual, sum(votos) / len(votos) if votos else None)

    @property
    def manual(self) -> str | None:
        """La expresión forzada por el operador, o ``None`` en automático."""
        return self._manual

    @property
    def ultima(self) -> Distribucion | None:
        """Lo que dijo el backend en la última inferencia, CRUDO: antes de la
        histéresis. ``None`` si en ella no había cara."""
        return self._ultima

    @property
    def rostro(self) -> tuple[int, int, int, int] | None:
        """Rectángulo del rostro de la última inferencia, en las coordenadas del
        frame que recibió ``process`` (o sea, el volteado)."""
        return self._rostro

    @property
    def t_ultima(self) -> float | None:
        """Instante de la última inferencia, en el reloj de ``clock``."""
        return self._t_ultima

    @property
    def inferencias(self) -> int:
        """Inferencias hechas: el registro escribe una fila por cada una."""
        return self._inferencias

    @property
    def inferencias_sin_rostro(self) -> int:
        """Inferencias SEGUIDAS sin cara. Vuelve a 0 al encontrarla."""
        return self._sin_rostro

    @property
    def latencia_ms(self) -> float | None:
        """Lo que costó la última inferencia: detección, recorte y red."""
        return self._latencia_ms

    @property
    def ventana(self) -> tuple[tuple[str, float], ...]:
        """Los votos de la histéresis, para el HUD."""
        return self._histeresis.window

    @property
    def n_frames(self) -> int:
        return self._histeresis.n_frames

    @property
    def min_confidence(self) -> float:
        return self._histeresis.min_confidence

    # -- modo manual -------------------------------------------------------

    def toggle_manual(self, emocion: str) -> str | None:
        """Fuerza una expresión, o la suelta si ya era esa. Devuelve la vigente.

        Admite las seis del visor, ``concentrado`` incluida: el modelo no la
        emite porque no es una emoción que se lea en la cara sino un estado del
        narrador, y solo el operador sabe cuándo está en él.

        Mientras hay una forzada la inferencia SIGUE corriendo, para que al
        volver a automático la expresión sea la de la cara de ese momento y no
        la de antes de forzar.
        """
        if emocion not in VIEWER_EMOTIONS:
            raise ValueError(
                f"expresión desconocida {emocion!r}; el visor tiene {VIEWER_EMOTIONS}"
            )
        self._manual = None if self._manual == emocion else emocion
        return self._manual

    def clear_manual(self) -> None:
        """Vuelve a automático."""
        self._manual = None

    # -- backend -----------------------------------------------------------

    def cambiar_backend(self, backend: EmotionBackend) -> None:
        """Cambia de backend en caliente.

        Vacía la ventana —tiene votos del backend anterior, con su propia escala
        de confianza— pero conserva la expresión que se muestra, para que el
        robot no salte a neutral al cambiar.
        """
        self.backend = backend
        self._histeresis.reset(initial=self._histeresis.current)
        self._ultima = None

    # -- proceso -----------------------------------------------------------

    def process(self, frame_bgr: np.ndarray, timestamp: float | None = None) -> Emocion:
        """Expresión del robot a partir de un frame de la cámara, **en BGR**.

        Se puede llamar en cada frame: solo infiere cuando toca según
        ``emotion_hz``, y entre medias devuelve lo último decidido.
        """
        ahora = self._clock() if timestamp is None else float(timestamp)
        if self._proxima is None or ahora >= self._proxima - _HOLGURA_S:
            # Por fecha límite, para que la media sea exactamente emotion_hz
            # aunque cada frame llegue un poco después de su ranura. Si el bucle
            # estuvo parado más de un periodo, las ranuras perdidas NO se
            # recuperan en ráfaga: se planifica desde ahora (P7, punto 10).
            siguiente = (ahora if self._proxima is None else self._proxima) + self._periodo
            self._proxima = siguiente if siguiente > ahora else ahora + self._periodo
            self._inferir(frame_bgr, ahora)
        return self.emocion

    def _inferir(self, frame_bgr: np.ndarray, ahora: float) -> None:
        inicio = time.perf_counter()
        frame = cv2.flip(frame_bgr, 1) if self.frame_espejado else frame_bgr
        rectangulo = self._detector(frame)
        self._inferencias += 1
        self._t_ultima = ahora
        if rectangulo is None:
            self._ultima = None
            self._rostro = None
            self._sin_rostro += 1
        else:
            x, y, w, h = rectangulo
            distribucion = self.backend.distribucion(frame[y:y + h, x:x + w])
            self._ultima = distribucion
            self._sin_rostro = 0
            self._rostro = (
                (frame.shape[1] - x - w, y, w, h) if self.frame_espejado else (x, y, w, h)
            )
            if distribucion.etiqueta is not None:
                self._histeresis.update(distribucion.etiqueta, distribucion.confianza)
        self._latencia_ms = (time.perf_counter() - inicio) * 1000.0


# --------------------------------------------------------------------------
# Modo demo
# --------------------------------------------------------------------------

#: Teclas 1..6 → expresión forzada, en el orden de los botones del visor.
TECLAS_MANUAL = {ord(str(i + 1)): e for i, e in enumerate(VIEWER_EMOTIONS)}

_COLUMNAS_CSV = (
    ["t", "etiqueta", "backend", "rostro", "x", "y", "w", "h",
     "prediccion", "nativa", "confianza_pred"]
    + [f"p_{e}" for e in EMOTION_LABELS]
    + ["emocion", "confianza", "manual", "latencia_ms", "brillo",
       "n_frames", "min_confidence", "colapso"]
)


def _fila_csv(t, etiqueta, clasificador: EmotionClassifier, brillo: float) -> list:
    d = clasificador.ultima
    rect = clasificador.rostro or ("", "", "", "")
    emocion = clasificador.emocion
    return (
        [f"{t:.4f}", etiqueta, clasificador.backend.nombre,
         int(clasificador.rostro is not None), *rect,
         (d.etiqueta or "") if d else "", d.nativa if d else "",
         f"{d.confianza:.4f}" if d else ""]
        + [f"{d.probabilidades[e]:.4f}" if d else "" for e in EMOTION_LABELS]
        + [emocion.etiqueta,
           "" if emocion.confianza is None else f"{emocion.confianza:.4f}",
           clasificador.manual or "",
           f"{clasificador.latencia_ms:.1f}" if clasificador.latencia_ms else "",
           f"{brillo:.1f}", clasificador.n_frames, f"{clasificador.min_confidence:g}",
           getattr(clasificador.backend, "colapso", "")]
    )


def _demo(argv: list[str] | None = None) -> int:
    """``python -m telmeow_teleop.emotion_classifier [--backend B] [--publish] [--record CSV]``"""
    import argparse
    import csv
    from collections import deque

    from .camera import abrir_camara, brillo_medio, describir

    cfg = AppConfig.from_env()
    parser = argparse.ArgumentParser(prog="emotion_classifier")
    parser.add_argument(
        "--backend", choices=BACKENDS, default=cfg.emotion.backend,
        help="Backend con el que arrancar. La tecla 'b' los recorre.",
    )
    parser.add_argument(
        "--publish", action="store_true",
        help="Publica la emoción en el broker, para ver los ojos del robot en "
             "el visor. Es lo que verifica el orden de clases de punta a punta.",
    )
    parser.add_argument(
        "--record", metavar="CSV",
        help="Registra una fila por inferencia, para calibrar la histéresis con "
             "datos. ESPACIO cambia de etiqueta.",
    )
    args = parser.parse_args(argv)

    backends: dict[str, EmotionBackend] = {}
    no_disponibles: set[str] = set()

    def obtener(nombre: str) -> EmotionBackend | None:
        if nombre in backends:
            return backends[nombre]
        if nombre in no_disponibles:
            return None
        print(f"Cargando el backend '{nombre}'...")
        try:
            backends[nombre] = crear_backend(nombre, cfg)
        except (BackendNoDisponible, FileNotFoundError) as exc:
            print(f"AVISO: {exc}\n")
            no_disponibles.add(nombre)
            return None
        return backends[nombre]

    inicial = obtener(args.backend) or obtener("keras")
    if inicial is None:
        return 1

    cap = abrir_camara(cfg.camera)
    if cap is None:
        print(
            f"No se pudo abrir la camara {cfg.camera.index}. "
            f"Prueba 'python tools/list_cameras.py' para ver que indices hay."
        )
        return 1
    print(describir(cap, cfg.camera) + f" Formato {cfg.camera.fourcc or 'por defecto'}.\n")

    clasificador = EmotionClassifier.desde_config(cfg, backend=inicial)
    if isinstance(inicial, KerasBackend):
        print(f"Modelo {inicial.ruta.name}, clases {inicial.clases}.")
    print(
        f"Inferencia a {cfg.rates.emotion_hz:g} Hz; histeresis de "
        f"{clasificador.n_frames} inferencias y confianza minima "
        f"{clasificador.min_confidence:g}.\n"
    )
    print(
        "VERIFICACION (P6.1)\n"
        "  Con --publish y el visor abierto, pon cada expresion y SOSTENLA 3 s:\n"
        "  feliz, triste, sorprendido, neutral, enojado. Los ojos del robot\n"
        "  tienen que poner LA MISMA. Si salen cambiadas, el orden de clases\n"
        "  esta mal aunque todo lo demas parezca funcionar.\n\n"
        "TECLAS\n"
        "  1..6 = forzar neutral, feliz, triste, sorprendido, enojado,\n"
        "         concentrado (la misma otra vez la suelta)\n"
        "  0 = volver a automatico    b = siguiente backend\n"
        "  ESPACIO = cambiar de etiqueta (solo con --record)    q = salir\n"
    )

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
            print(
                f"AVISO: no hay enlace con el broker en {cfg.mqtt.host}:"
                f"{cfg.mqtt.port}. Se sigue reintentando; mira el contador "
                f"'perdidos' del HUD.\n"
            )

    registro = None
    escritor = None
    etiqueta = 0
    if args.record:
        # Aquí dentro y no arriba: head_tracker importa MediaPipe, y este demo
        # solo lo necesita para no pisar una grabación anterior.
        from .head_tracker import apartar_si_existe

        apartado = apartar_si_existe(Path(args.record))
        if apartado is not None:
            print(f"AVISO: ese CSV ya existia. Se conserva como {apartado.name}.\n")
        registro = open(args.record, "w", newline="", encoding="utf-8")
        escritor = csv.writer(registro)
        escritor.writerow(_COLUMNAS_CSV)
        print(f"Registrando en {args.record}. ESPACIO cambia de etiqueta.\n")

    fuente = cv2.FONT_HERSHEY_SIMPLEX
    t0 = time.perf_counter()
    marcas_fps: deque[float] = deque(maxlen=30)
    marcas_inferencia: deque[float] = deque(maxlen=10)
    vistas = 0
    brillo = 0.0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if cfg.camera.flip:
                frame = cv2.flip(frame, 1)
            ahora = time.perf_counter()
            marcas_fps.append(ahora)

            emocion = clasificador.process(frame, ahora)
            if clasificador.inferencias != vistas:
                vistas = clasificador.inferencias
                marcas_inferencia.append(ahora)
                brillo = brillo_medio(frame)
                if escritor is not None:
                    escritor.writerow(_fila_csv(ahora - t0, etiqueta, clasificador, brillo))

            if publicador is not None:
                publicador.publish(emo=emocion.etiqueta)

            # -- HUD: lo decidido, y al lado el dato crudo del que sale -----
            d = clasificador.ultima
            if clasificador.rostro is not None:
                x, y, w, h = clasificador.rostro
                vota = d is not None and d.etiqueta is not None
                color = (0, 255, 0) if vota else (0, 165, 255)
                cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
                if d is not None:
                    cv2.putText(
                        frame, f"{d.nativa} {d.confianza:.2f}", (x, max(15, y - 10)),
                        fuente, 0.6, color, 2,
                    )

            if clasificador.manual is not None:
                texto, color = f"ROBOT: {emocion.etiqueta} (MANUAL)", (255, 0, 255)
            else:
                conf = "--" if emocion.confianza is None else f"{emocion.confianza:.2f}"
                texto, color = f"ROBOT: {emocion.etiqueta} {conf}", (0, 255, 0)
            cv2.putText(frame, texto, (10, 30), fuente, 0.8, color, 2)

            if clasificador.inferencias_sin_rostro:
                crudo = f"sin rostro ({clasificador.inferencias_sin_rostro} inferencias)"
            elif d is not None:
                crudo = (
                    f"modelo: {d.etiqueta or 'sin equivalente'} {d.confianza:.2f}"
                    f" ({d.nativa})"
                )
            else:
                crudo = "modelo: --"
            cv2.putText(frame, crudo, (10, 56), fuente, 0.55, (200, 200, 200), 1)

            if d is not None:
                for i, e in enumerate(EMOTION_LABELS):
                    fila = 78 + i * 16
                    largo = int(120 * d.probabilidades[e])
                    cv2.rectangle(frame, (110, fila - 10), (110 + largo, fila), (255, 200, 0), -1)
                    cv2.putText(frame, e, (10, fila), fuente, 0.45, (255, 255, 255), 1)

            fps = (
                (len(marcas_fps) - 1) / (marcas_fps[-1] - marcas_fps[0])
                if len(marcas_fps) > 1 and marcas_fps[-1] > marcas_fps[0] else 0.0
            )
            hz = (
                (len(marcas_inferencia) - 1) / (marcas_inferencia[-1] - marcas_inferencia[0])
                if len(marcas_inferencia) > 1 and marcas_inferencia[-1] > marcas_inferencia[0]
                else 0.0
            )
            latencia = clasificador.latencia_ms or 0.0
            cv2.putText(
                frame,
                f"{clasificador.backend.nombre}  {hz:3.1f} Hz  {latencia:3.0f} ms"
                f"   camara {fps:4.1f} fps",
                (10, 172), fuente, 0.55, (255, 255, 0), 1,
            )
            votos = " ".join(e[:4] for e, _ in clasificador.ventana)
            cv2.putText(frame, f"ventana: {votos}", (10, 194), fuente, 0.5, (180, 255, 180), 1)
            if brillo < 8.0:
                cv2.putText(
                    frame, f"CAMARA NEGRA (brillo {brillo:.1f})", (10, 216),
                    fuente, 0.6, (0, 0, 255), 2,
                )
            if escritor is not None:
                cv2.putText(frame, f"etiqueta {etiqueta}", (10, 240), fuente, 0.6, (255, 200, 100), 2)
            if publicador is not None:
                enlace = "MQTT" if publicador.connected else "MQTT SIN ENLACE"
                cv2.putText(
                    frame,
                    f"{enlace}  {publicador.effective_hz:4.1f} Hz  "
                    f"enviados {publicador.message_count}  "
                    f"perdidos {publicador.failed_count}",
                    (10, 264), fuente, 0.5,
                    (0, 255, 0) if publicador.connected else (0, 0, 255), 1,
                )

            cv2.imshow("Telmeow - emocion", frame)
            tecla = cv2.waitKey(1) & 0xFF
            if tecla == ord("q"):
                break
            if tecla == ord(" "):
                etiqueta += 1
                print(f"--- etiqueta {etiqueta} ---")
            elif tecla == ord("0"):
                clasificador.clear_manual()
                print("automatico")
            elif tecla in TECLAS_MANUAL:
                vigente = clasificador.toggle_manual(TECLAS_MANUAL[tecla])
                print(f"manual: {vigente}" if vigente else "automatico")
            elif tecla == ord("b"):
                actual = BACKENDS.index(clasificador.backend.nombre)
                for paso in range(1, len(BACKENDS)):
                    siguiente = obtener(BACKENDS[(actual + paso) % len(BACKENDS)])
                    if siguiente is not None:
                        clasificador.cambiar_backend(siguiente)
                        print(f"backend: {siguiente.nombre}")
                        break
                else:
                    print("no hay otro backend instalado")
    finally:
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
