"""Los backends de emoción que eligió el operador en el P6.4 (2026-10-25).

El clasificador de operación es ``CombinadoBackend``: la media a partes
iguales de las probabilidades de dos modelos que fallan en cosas distintas.

- ``OnnxHSEmotionBackend``: HSEmotion/EmotiEffLib (Savchenko) exportado a ONNX,
  ``mbf_va_mtl``, entrenado con AffectNet. Corre con onnxruntime, sin torch.
  No vio nunca al operador.
- ``BlendshapesBackend``: los 52 coeficientes de la cara de MediaPipe Face
  Landmarker («blendshapes») y una regresión logística entrenada con los del
  dataset propio (``tools/entrenar_blendshapes.py``), **relativa a la cara
  neutra del operador**, que se calibra al arrancar (``EmotionClassifier``,
  tecla ``c`` en el demo).

Sobre la grabación del protocolo v3 (plan maestro, P6.4): triste 98 % frente a
0 % del modelo propio, y leyendo en voz alta 0.8 cambios por minuto frente a
12.1. Los dos se importan aquí y no en ``emotion_classifier`` para que quien
use solo el modelo propio no cargue MediaPipe Tasks ni onnxruntime.

Los modelos descargados van en ``modelos_externos/`` (fuera de git; los baja
``tools/descargar_modelos.py``, que comprueba su SHA-256), y los pesos de la
regresión, que pesan unos KB, en la raíz del repositorio.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import cv2
import numpy as np

from .config import EMOTION_LABELS
from .emotion_classifier import (
    BackendNoDisponible,
    Distribucion,
    EmotionBackend,
    _a_bgr,
    colapsar,
)

#: Unidades de acción de las cinco expresiones (Ekman y Friesen, FACS) y el
#: blendshape de MediaPipe que más se le parece. Es la selección «FACS»: se
#: fija por la teoría, no mirando qué separa mejor en la grabación.
#:
#: - feliz: AU6 ``cheekSquint``, AU12 ``mouthSmile``.
#: - triste: AU1 ``browInnerUp``, AU4 ``browDown``, AU15 ``mouthFrown``.
#: - sorprendido: AU1, AU2 ``browOuterUp``, AU5 ``eyeWide``, AU26 ``jawOpen``.
#: - enojado: AU4, AU5, AU7 ``eyeSquint``, AU23/24 ``mouthPress``, y AU9
#:   ``noseSneer`` (asco, que el robot junta con el enojo).
#: - y los de la boca que cambian al hablar o al hacer pucheros: AU18
#:   ``mouthPucker``, AU20 ``mouthStretch``, AU17 ``mouthShrugLower`` y
#:   ``mouthShrugUpper``.
FACS: tuple[str, ...] = (
    "browInnerUp", "browOuterUp", "browDown", "eyeWide", "cheekSquint",
    "eyeSquint", "mouthSmile", "mouthFrown", "mouthPress", "jawOpen",
    "mouthStretch", "noseSneer", "mouthPucker", "mouthShrugUpper",
    "mouthShrugLower",
)

#: Clases de los modelos de 8 de EmotiEffLib, en el orden de su salida
#: (``facial_analysis.py``, ``idx_to_emotion_class``, rama main en ``520a051``).
CLASES_HSEMOTION_8 = ("Anger", "Contempt", "Disgust", "Fear", "Happiness",
                      "Neutral", "Sadness", "Surprise")


def _comprobar_archivo(ruta: Path) -> None:
    if not Path(ruta).is_file():
        raise FileNotFoundError(
            f"falta {ruta}. Se descarga con 'python tools/descargar_modelos.py', "
            f"que comprueba su SHA-256 (plan maestro, P6.4).")


def rasgos_simetricos(puntuaciones: dict[str, float]) -> dict[str, float]:
    """Junta cada par izquierda/derecha en su media y quita ``_neutral``.

    La media no depende de que la imagen esté o no en espejo: el dataset y los
    recortes se clasifican sin espejo, pero así no hay que confiar en ello.
    """
    grupos: dict[str, list[float]] = {}
    for nombre, valor in puntuaciones.items():
        if nombre == "_neutral":
            continue
        grupos.setdefault(re.sub(r"(Left|Right)$", "", nombre), []).append(float(valor))
    return {k: sum(v) / len(v) for k, v in sorted(grupos.items())}


class Landmarker:
    """MediaPipe Face Landmarker en modo imagen, solo con los blendshapes."""

    def __init__(self, ruta_task: Path) -> None:
        _comprobar_archivo(ruta_task)
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision

        self._mp = mp
        self._landmarker = vision.FaceLandmarker.create_from_options(
            vision.FaceLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=str(ruta_task)),
                running_mode=vision.RunningMode.IMAGE,
                num_faces=1,
                output_face_blendshapes=True,
            ))

    def blendshapes(self, imagen_bgr: np.ndarray) -> dict[str, float] | None:
        """Los 52 coeficientes de la cara, o ``None`` si no la encuentra."""
        rgb = cv2.cvtColor(_a_bgr(imagen_bgr), cv2.COLOR_BGR2RGB)
        res = self._landmarker.detect(self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb))
        if not res.face_blendshapes:
            return None
        return {c.category_name: float(c.score) for c in res.face_blendshapes[0]}


class BlendshapesBackend(EmotionBackend):
    """Regresión logística sobre los blendshapes del recorte.

    Los pesos los escribe ``tools/entrenar_blendshapes.py`` en un JSON con los
    rasgos, su media y desviación (para estandarizar), las clases, ``W`` y
    ``b``. Con ``"relativo": true`` los rasgos se miden respecto a una cara
    neutra: la del dataset al entrenar, y la del operador (``calibrar``) al
    operar, que es lo que hace el head tracker con la tecla ``c``. Es además la
    mitigación de la edad (plan maestro, §3.3.3): la cara neutra de una persona
    mayor se lee como triste o enojada si no se resta.

    Si el Face Landmarker no encuentra cara en el recorte, la etiqueta es
    ``None`` y la inferencia no vota, como una clase sin equivalente.
    """

    nombre = "blendshapes"

    def __init__(self, ruta_pesos: Path, ruta_task: Path) -> None:
        self.ruta = Path(ruta_pesos)
        _comprobar_archivo(self.ruta)
        self.modelo = self.ruta.name
        pesos = json.loads(self.ruta.read_text(encoding="utf-8"))
        self.rasgos: list[str] = pesos["rasgos"]
        self.clases: list[str] = pesos["clases"]
        if sorted(self.clases) != sorted(EMOTION_LABELS):
            raise ValueError(f"{self.ruta.name}: clases {self.clases}, se esperaban {EMOTION_LABELS}")
        self.relativo = bool(pesos.get("relativo", False))
        self._media = np.asarray(pesos["media"], dtype=np.float64)
        self._desv = np.asarray(pesos["desviacion"], dtype=np.float64)
        self._W = np.asarray(pesos["W"], dtype=np.float64)
        self._b = np.asarray(pesos["b"], dtype=np.float64)
        self.neutro: np.ndarray | None = None
        self._landmarker = Landmarker(ruta_task)

    @property
    def calibrado(self) -> bool:
        """Si ya puede clasificar: sin ``relativo``, siempre."""
        return not self.relativo or self.neutro is not None

    def vector(self, rostro_bgr: np.ndarray) -> np.ndarray | None:
        b = self._landmarker.blendshapes(rostro_bgr)
        if b is None:
            return None
        r = rasgos_simetricos(b)
        return np.array([r[k] for k in self.rasgos], dtype=np.float64)

    def calibrar(self, rostros: list[np.ndarray]) -> int:
        """Fija la cara neutra del operador con la media de ``rostros``.

        Devuelve cuántos sirvieron. Sin ``relativo`` no hace nada.
        """
        if not self.relativo:
            return 0
        vectores = [v for v in map(self.vector, rostros) if v is not None]
        if not vectores:
            raise ValueError("ningún rostro de calibración tuvo cara para el Face Landmarker")
        self.neutro = np.mean(vectores, axis=0)
        return len(vectores)

    def probabilidades(self, v: np.ndarray) -> np.ndarray:
        if self.relativo:
            if self.neutro is None:
                raise RuntimeError("backend relativo sin calibrar: llama a calibrar() antes")
            v = v - self.neutro
        z = (v - self._media) / self._desv @ self._W + self._b
        z = np.exp(z - z.max())
        return z / z.sum()

    def distribucion(self, rostro_bgr: np.ndarray) -> Distribucion:
        v = self.vector(rostro_bgr)
        if v is None:
            return Distribucion(None, 0.0, dict.fromkeys(EMOTION_LABELS, 0.0), "sin cara")
        p = dict(zip(self.clases, map(float, self.probabilidades(v))))
        etiqueta = max(p, key=p.get)
        return Distribucion(etiqueta, p[etiqueta], p, etiqueta)


class OnnxHSEmotionBackend(EmotionBackend):
    """HSEmotion en ONNX, preparado como ``EfficientNetONNX._preprocess``.

    RGB, ``resize`` al lado del modelo, ``/255`` y normalización por canal:
    ImageNet para EfficientNet (224 px) y 0.5/0.5 para MobileFaceNet
    (``mbf_``, 112 px). La salida son logits; los ``_mtl`` añaden valencia y
    activación al final, que no son clases y se quitan.
    """

    nombre = "hsemotion_onnx"

    def __init__(self, ruta_onnx: Path, *, colapso: str = "cercana") -> None:
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise BackendNoDisponible(self.nombre, exc) from exc
        self.ruta = Path(ruta_onnx)
        _comprobar_archivo(self.ruta)
        self.modelo = self.ruta.name
        self.colapso = colapso
        if self.modelo.startswith("mbf_"):
            self._lado, media, desv = 112, (0.5, 0.5, 0.5), (0.5, 0.5, 0.5)
        else:
            self._lado, media, desv = 224, (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)
        self._media = np.array(media, dtype=np.float64)
        self._desv = np.array(desv, dtype=np.float64)
        ort.set_default_logger_severity(3)
        self._sesion = ort.InferenceSession(str(self.ruta), providers=["CPUExecutionProvider"])
        self._entrada = self._sesion.get_inputs()[0].name
        self.distribucion(np.zeros((self._lado, self._lado, 3), dtype=np.uint8))

    def tensor(self, rostro_bgr: np.ndarray) -> np.ndarray:
        rgb = cv2.cvtColor(_a_bgr(rostro_bgr), cv2.COLOR_BGR2RGB)
        x = cv2.resize(rgb, (self._lado, self._lado)) / 255
        x = (x - self._media) / self._desv
        return x.transpose(2, 0, 1).astype("float32")[np.newaxis, ...]

    def distribucion(self, rostro_bgr: np.ndarray) -> Distribucion:
        salida = self._sesion.run(None, {self._entrada: self.tensor(rostro_bgr)})[0][0]
        logits = np.asarray(salida[: len(CLASES_HSEMOTION_8)], dtype=np.float64)
        p = np.exp(logits - logits.max())
        p /= p.sum()
        return colapsar(dict(zip(CLASES_HSEMOTION_8, map(float, p))), self.colapso)


class CombinadoBackend(EmotionBackend):
    """La media, a partes iguales, de las probabilidades de varios backends.

    Se promedian las cinco expresiones del robot, ya colapsadas por cada uno,
    y gana la de más probabilidad media. A partes iguales a propósito: unos
    pesos ajustados con la grabación darían una cifra que no vale fuera de
    ella. Un backend que no ve cara (los blendshapes, si el Face Landmarker no
    la encuentra) se queda fuera de esa media; si no la ve ninguno, no vota.

    Se calibra si alguno de sus backends lo hace (``relativo``).
    """

    nombre = "combinado"

    def __init__(self, backends: list[EmotionBackend]) -> None:
        if len(backends) < 2:
            raise ValueError("un combinado necesita al menos dos backends")
        self.backends = list(backends)
        self.modelo = "+".join(getattr(b, "modelo", "") or b.nombre for b in self.backends)
        self.relativo = any(getattr(b, "relativo", False) for b in self.backends)

    @property
    def calibrado(self) -> bool:
        return all(getattr(b, "calibrado", True) for b in self.backends)

    def calibrar(self, rostros: list[np.ndarray]) -> int:
        usadas = [b.calibrar(rostros) for b in self.backends if getattr(b, "relativo", False)]
        return min(usadas) if usadas else 0

    def distribucion(self, rostro_bgr: np.ndarray) -> Distribucion:
        partes = [b.distribucion(rostro_bgr) for b in self.backends]
        validas = [d for d in partes if d.etiqueta is not None]
        if not validas:
            return Distribucion(None, 0.0, dict.fromkeys(EMOTION_LABELS, 0.0), "sin cara")
        p = {e: sum(d.probabilidades[e] for d in validas) / len(validas) for e in EMOTION_LABELS}
        etiqueta = max(p, key=p.get)
        return Distribucion(etiqueta, p[etiqueta], p, etiqueta)


def crear_combinado(ruta_onnx: Path, ruta_pesos: Path, ruta_task: Path,
                    *, colapso: str = "cercana") -> CombinadoBackend:
    """El de operación: ``mbf_va_mtl`` + blendshapes FACS relativos."""
    return CombinadoBackend([
        OnnxHSEmotionBackend(ruta_onnx, colapso=colapso),
        BlendshapesBackend(ruta_pesos, ruta_task),
    ])
