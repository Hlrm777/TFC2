"""Pruebas del clasificador de emoción (P6).

Lo que se puede equivocar en silencio aquí son CONVENIOS —el orden de las
clases, el del preprocesamiento, el color, el espejo—, y una prueba que fija un
convenio con el mismo código que lo usa es consistente consigo misma y ciega al
error (H17, H22, H29). Por eso cada convenio se ata a algo de FUERA:

- el orden de clases, a las etiquetas que el operador puso a sus fotos al
  grabarlas (los nombres de archivo de ``emotions_dataset/``);
- el preprocesamiento, al código de los scripts legacy, copiado tal cual;
- las expresiones del visor, al HTML del visor;
- ``model(x, training=False)``, a ``model.predict``, otro camino de código.

Las pruebas que necesitan el modelo (``emotion_model.keras``) o el dataset se
saltan si no están: los dos viven fuera de git.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import types
import warnings
from pathlib import Path

import cv2
import numpy as np
import pytest

from telmeow_teleop.config import (
    EMOTION_BACKENDS,
    EMOTION_LABELS,
    PROJECT_ROOT,
    VIEWER_EMOTIONS,
    AppConfig,
    EmotionConfig,
)
from telmeow_teleop.emotion_classifier import (
    BACKENDS,
    LADO,
    BackendNoDisponible,
    DeepFaceBackend,
    Distribucion,
    Emocion,
    EmotionBackend,
    EmotionClassifier,
    HSEmotionBackend,
    KerasBackend,
    OrdenDeClasesSupuesto,
    cargar_cascade,
    colapsar,
    crear_backend,
    detectar_rostro,
    elegir_rostro,
    leer_metadatos,
    preprocesar_48,
    resolver_clases,
    ruta_cascade,
    ruta_metadatos,
)

VISOR = PROJECT_ROOT / "telmeow_viewer" / "telmeow_viewer.html"
MODELO = PROJECT_ROOT / "emotion_model.keras"
DATASET = PROJECT_ROOT / "emotions_dataset"
LEGACY = PROJECT_ROOT / "legacy"

requiere_modelo = pytest.mark.skipif(
    not MODELO.is_file(), reason="emotion_model.keras no está (vive fuera de git)"
)
requiere_dataset = pytest.mark.skipif(
    not DATASET.is_dir(), reason="emotions_dataset/ no está (vive fuera de git)"
)


def _legacy(rostro):
    """El preprocesamiento de ``legacy/Emotion Recognition.py``, copiado tal cual."""
    face = cv2.resize(rostro, (48, 48))
    face = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)
    face = face / 255.0
    face = np.expand_dims(face, axis=-1)
    face = np.expand_dims(face, axis=0)
    return face


# --------------------------------------------------------------------------
# El contrato con el visor y con la configuración
# --------------------------------------------------------------------------


def test_las_expresiones_del_visor_son_las_de_sus_botones_y_en_su_orden():
    """El orden de VIEWER_EMOTIONS es el de las teclas 1..6 del P7: sale del
    visor, no se elige."""
    botones = re.findall(r'data-emo="([a-z]+)"', VISOR.read_text(encoding="utf-8"))
    assert tuple(botones) == VIEWER_EMOTIONS


def test_el_visor_tiene_textura_para_las_seis_expresiones():
    html = VISOR.read_text(encoding="utf-8")
    bloque = re.search(r"const EMO_FILES = \{(.*?)\};", html, re.S).group(1)
    assert set(re.findall(r"(\w+):'", bloque)) == set(VIEWER_EMOTIONS)


def test_el_modelo_emite_cinco_y_concentrado_es_solo_manual():
    assert set(EMOTION_LABELS) < set(VIEWER_EMOTIONS)
    assert set(VIEWER_EMOTIONS) - set(EMOTION_LABELS) == {"concentrado"}


def test_los_backends_de_la_configuracion_son_los_del_modulo():
    assert EMOTION_BACKENDS == BACKENDS


def test_la_configuracion_de_emocion_por_defecto():
    e = EmotionConfig()
    assert (e.backend, e.n_frames, e.min_confidence, e.colapso) == ("keras", 5, 0.6, "cercana")
    assert e.hsemotion_model == "enet_b0_8_best_afew"
    assert e.min_lado_rostro == 60


def test_la_configuracion_de_emocion_desde_el_entorno():
    cfg = AppConfig.from_env({
        "TELMEOW_EMOTION_BACKEND": "hsemotion",
        "TELMEOW_EMOTION_N_FRAMES": "7",
        "TELMEOW_EMOTION_MIN_CONFIDENCE": "0.7",
        "TELMEOW_EMOTION_COLAPSO": "ninguna",
        "TELMEOW_EMOTION_MIN_LADO_ROSTRO": "0",
    })
    assert cfg.emotion.backend == "hsemotion"
    assert cfg.emotion.n_frames == 7
    assert cfg.emotion.min_confidence == 0.7
    assert cfg.emotion.colapso == "ninguna"
    assert cfg.emotion.min_lado_rostro == 0


def test_el_backend_se_elige_por_linea_de_comandos():
    from telmeow_teleop.config import load

    assert load(["--emotion-backend", "deepface"], env={}).emotion.backend == "deepface"
    with pytest.raises(SystemExit):
        load(["--emotion-backend", "otro"], env={})


# --------------------------------------------------------------------------
# Preprocesamiento: idéntico al del entrenamiento
# --------------------------------------------------------------------------


def test_los_tres_scripts_legacy_redimensionan_antes_de_pasar_a_gris():
    """El ancla externa de la prueba siguiente: el orden está en el legacy."""
    for nombre in ("Emotion Recognition.py", "Training Emotion Recognition.py"):
        texto = (LEGACY / nombre).read_text(encoding="utf-8")
        resize = texto.index("cv2.resize(face, (48, 48))")
        gris = texto.index("cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)")
        assert resize < gris, nombre


@pytest.mark.parametrize("forma", [(123, 117, 3), (300, 300, 3), (48, 48, 3), (40, 31, 3)])
def test_el_preprocesamiento_es_el_del_legacy_bit_a_bit(forma):
    rostro = np.random.default_rng(7).integers(0, 256, forma, dtype=np.uint8)
    nuestro = preprocesar_48(rostro)
    assert nuestro.shape == (1, LADO, LADO, 1)
    assert nuestro.dtype == np.float32
    assert np.array_equal(nuestro, _legacy(rostro).astype(np.float32))


def test_la_prueba_del_preprocesamiento_distingue_el_orden_habitual():
    """La prueba anterior no pasa por casualidad: pasar a gris ANTES de
    redimensionar —lo habitual— da otro tensor (±1 nivel de gris en uno de cada
    cuatro píxeles, por el redondeo entre los dos pasos)."""
    rostro = np.random.default_rng(7).integers(0, 256, (123, 117, 3), dtype=np.uint8)
    gris_primero = cv2.resize(cv2.cvtColor(rostro, cv2.COLOR_BGR2GRAY), (48, 48)) / 255.0
    assert not np.array_equal(preprocesar_48(rostro)[0, :, :, 0],
                              gris_primero.astype(np.float32))


def test_acepta_un_recorte_ya_en_gris_como_los_de_fer2013():
    gris = np.random.default_rng(3).integers(0, 256, (48, 48), dtype=np.uint8)
    esperado = (gris / 255.0).astype(np.float32).reshape(1, 48, 48, 1)
    assert np.array_equal(preprocesar_48(gris), esperado)
    assert np.array_equal(preprocesar_48(gris[:, :, None]), esperado)


def test_rechaza_un_recorte_que_no_es_uint8():
    """Uno ya dividido entre 255 se volvería a dividir: una cara casi negra."""
    with pytest.raises(ValueError, match="uint8"):
        preprocesar_48(np.full((60, 60, 3), 0.5, dtype=np.float32))


def test_rechaza_un_recorte_vacio():
    with pytest.raises(ValueError, match="vacío"):
        preprocesar_48(np.zeros((0, 10, 3), dtype=np.uint8))


# --------------------------------------------------------------------------
# Detección
# --------------------------------------------------------------------------


def test_el_cascade_por_defecto_es_el_de_opencv_con_el_que_se_entreno():
    """H16: el de OpenCV 4.10 pesa 930.127 bytes; la copia de legacy/, 963.441.
    Si una actualización de OpenCV cambia el archivo, cambia el preprocesamiento
    con el que se entrenó el modelo, y hay que saberlo."""
    ruta = ruta_cascade(None)
    assert ruta.parent == Path(cv2.data.haarcascades)
    assert ruta.stat().st_size == 930_127
    assert (LEGACY / "haarcascade_frontalface_default.xml").stat().st_size == 963_441


def test_una_ruta_de_cascade_mala_falla_al_cargar_y_no_al_detectar(tmp_path):
    with pytest.raises(FileNotFoundError, match="no_existe.xml"):
        cargar_cascade(tmp_path / "no_existe.xml")


def test_elegir_rostro_mayor_o_primero():
    caras = [(10, 10, 20, 20), (100, 100, 80, 80), (5, 5, 30, 30)]
    assert elegir_rostro(caras) == (100, 100, 80, 80)
    assert elegir_rostro(caras, "primera") == (10, 10, 20, 20)
    assert elegir_rostro([]) is None
    with pytest.raises(ValueError):
        elegir_rostro(caras, "otra")


def test_el_rostro_mayor_no_depende_del_orden_de_las_detecciones():
    """OpenCV devuelve las detecciones en distinto orden entre llamadas
    idénticas cuando usa varios hilos (16 de 40 imágenes con varias caras).
    'mayor' solo puede depender del conjunto; 'primera', del orden."""
    from itertools import permutations

    caras = [(10, 10, 20, 20), (100, 100, 80, 80), (5, 5, 30, 30), (300, 50, 80, 80)]
    mayores = {elegir_rostro(list(p)) for p in permutations(caras)}
    assert mayores == {(300, 50, 80, 80)}            # empate de área: la de más arriba
    primeras = {elegir_rostro(list(p), "primera") for p in permutations(caras)}
    assert len(primeras) == 4


@requiere_dataset
def test_el_haar_encuentra_la_cara_del_operador():
    img = cv2.imread(str(DATASET / "feliz_1.jpg"))
    cascade = cargar_cascade()
    rect = detectar_rostro(img, cascade)
    assert rect is not None and rect[2] > 150
    # Con minSize=60 el recorte es el mismo (medido en las 8000 imágenes).
    assert detectar_rostro(img, cascade, min_lado=60) == rect


def test_el_cascade_se_construye_una_sola_vez(monkeypatch):
    """Bug 15: el legacy lo construía en cada frame."""
    construcciones = []
    real = cv2.CascadeClassifier

    def contando(*args):
        construcciones.append(args)
        return real(*args)

    monkeypatch.setattr(cv2, "CascadeClassifier", contando)
    c = EmotionClassifier(_BackendFalso([("feliz", 0.9)]), frame_espejado=False)
    negro = np.zeros((120, 160, 3), dtype=np.uint8)
    for i in range(30):
        c.process(negro, timestamp=i * 0.2)
    assert c.inferencias == 30
    assert len(construcciones) == 1


def test_un_criterio_de_rostro_mal_escrito_falla_al_construir():
    with pytest.raises(ValueError):
        EmotionClassifier(_BackendFalso([("feliz", 0.9)]), criterio_rostro="grande")


# --------------------------------------------------------------------------
# Orden de clases: de los metadatos, nunca inferido
# --------------------------------------------------------------------------


def _meta(tmp_path, contenido) -> Path:
    ruta = tmp_path / "modelo.meta.json"
    ruta.write_text(contenido if isinstance(contenido, str) else json.dumps(contenido),
                    encoding="utf-8")
    return ruta


def test_la_ruta_de_metadatos_es_la_que_fija_el_p8():
    assert ruta_metadatos(Path("m/emotion_model_fer2013.keras")) == Path(
        "m/emotion_model_fer2013.meta.json")
    assert ruta_metadatos(MODELO) == PROJECT_ROOT / "emotion_model.meta.json"


def test_los_metadatos_mandan_sobre_el_orden_legacy(tmp_path):
    orden = ["enojado", "neutral", "sorprendido", "triste", "feliz"]
    _meta(tmp_path, {"clases": orden, "fecha": "2026-10-19"})
    with warnings.catch_warnings():
        warnings.simplefilter("error")          # sin aviso: el orden se LEYÓ
        clases, meta = resolver_clases(tmp_path / "modelo.keras", 5)
    assert clases == tuple(orden)
    assert meta["fecha"] == "2026-10-19"


def test_sin_metadatos_y_cinco_salidas_supone_el_orden_legacy_y_avisa(tmp_path):
    with pytest.warns(OrdenDeClasesSupuesto, match="SUPONE"):
        clases, meta = resolver_clases(tmp_path / "modelo.keras", 5)
    assert clases == EMOTION_LABELS
    assert meta is None


def test_el_aviso_sale_aunque_otra_libreria_silencie_los_warnings(tmp_path):
    """DeepFace hace warnings.filterwarnings("ignore") al importarse (0.0.102,
    DeepFace.py), y el P9 lo usa en el mismo proceso que el modelo propio."""
    with warnings.catch_warnings(record=True) as avisos:
        warnings.simplefilter("ignore")
        warnings.warn("control", OrdenDeClasesSupuesto)
        resolver_clases(tmp_path / "modelo.keras", 5)
    mensajes = [str(a.message) for a in avisos]
    assert "control" not in mensajes               # el filtro de verdad silencia
    assert any("SUPONE" in m for m in mensajes)


def test_sin_metadatos_y_otro_numero_de_salidas_no_supone_nada(tmp_path):
    with pytest.raises(ValueError, match="7 salidas"):
        resolver_clases(tmp_path / "modelo.keras", 7)


@pytest.mark.parametrize(
    "contenido, mensaje",
    [
        ({"clases": ["feliz", "triste", "sorprendido", "neutral"]}, "4 clases"),
        ({"clases": ["happy", "triste", "sorprendido", "neutral", "enojado"]}, "happy"),
        ({"clases": ["feliz", "feliz", "sorprendido", "neutral", "enojado"]}, "repetidas"),
        ({"orden": EMOTION_LABELS}, "clases"),
        ({"clases": "feliz,triste"}, "lista"),
        ("{no es json", "JSON"),
    ],
)
def test_metadatos_que_no_cuadran_se_rechazan(tmp_path, contenido, mensaje):
    _meta(tmp_path, contenido)
    with pytest.raises(ValueError, match=mensaje):
        resolver_clases(tmp_path / "modelo.keras", 5)


def test_un_modelo_de_cuatro_clases_es_valido_si_lo_declara(tmp_path):
    """Un dataset sin sorpresa (FACES, §3.1) daría un modelo de cuatro."""
    _meta(tmp_path, {"clases": ["feliz", "triste", "neutral", "enojado"]})
    assert resolver_clases(tmp_path / "modelo.keras", 4)[0] == (
        "feliz", "triste", "neutral", "enojado")


def test_los_metadatos_del_modelo_propio_tienen_el_orden_legacy():
    meta = leer_metadatos(PROJECT_ROOT / "emotion_model.meta.json")
    assert tuple(meta["clases"]) == EMOTION_LABELS


# --------------------------------------------------------------------------
# Colapso de las clases de las librerías
# --------------------------------------------------------------------------

#: Una salida de DeepFace: porcentajes, claves en minúscula.
_DEEPFACE = {"angry": 1.0, "disgust": 0.0, "fear": 2.0, "happy": 90.0,
             "sad": 3.0, "surprise": 1.0, "neutral": 3.0}


def test_mapea_por_nombre_y_normaliza_porcentajes():
    d = colapsar(_DEEPFACE)
    assert d.etiqueta == "feliz"
    assert d.confianza == pytest.approx(0.90)
    assert d.nativa == "happy"
    assert sum(d.probabilidades.values()) == pytest.approx(1.0)
    assert set(d.probabilidades) == set(EMOTION_LABELS)


def test_cercana_suma_la_masa_de_la_clase_sin_equivalente():
    """El ejemplo del docstring: con enfado 0.30, asco 0.25 y alegría 0.45 gana
    enojado con 0.55, aunque la clase nativa ganadora sea la alegría."""
    nativas = {"angry": 30, "disgust": 25, "happy": 45, "fear": 0,
               "sad": 0, "surprise": 0, "neutral": 0}
    d = colapsar(nativas, "cercana")
    assert (d.etiqueta, d.nativa) == ("enojado", "happy")
    assert d.confianza == pytest.approx(0.55)


def test_ninguna_deja_fuera_las_clases_sin_equivalente():
    nativas = {"angry": 30, "disgust": 25, "happy": 45, "fear": 0,
               "sad": 0, "surprise": 0, "neutral": 0}
    d = colapsar(nativas, "ninguna")
    assert (d.etiqueta, d.confianza) == ("feliz", pytest.approx(0.45))
    assert sum(d.probabilidades.values()) == pytest.approx(0.75)


def test_ninguna_no_vota_si_gana_una_clase_sin_equivalente():
    d = colapsar({"fear": 70, "surprise": 20, "neutral": 10}, "ninguna")
    assert d.etiqueta is None
    assert d.nativa == "fear"
    assert d.confianza == pytest.approx(0.70)
    assert colapsar({"fear": 70, "surprise": 20, "neutral": 10}, "cercana").etiqueta == "sorprendido"


def test_las_clases_de_hsemotion_con_mayuscula():
    d = colapsar({"Anger": 0.1, "Contempt": 0.3, "Disgust": 0.0, "Fear": 0.0,
                  "Happiness": 0.35, "Neutral": 0.05, "Sadness": 0.1, "Surprise": 0.1})
    assert d.etiqueta == "enojado"            # desprecio + ira = 0.4 > alegría 0.35
    assert d.confianza == pytest.approx(0.4)


def test_una_clase_desconocida_se_rechaza_en_vez_de_adivinarla():
    with pytest.raises(ValueError, match="joy"):
        colapsar({"joy": 0.9, "sad": 0.1})


@pytest.mark.parametrize("nativas", [{}, {"happy": 0.0, "sad": 0.0},
                                     {"happy": -1.0, "sad": 2.0},
                                     {"happy": float("nan"), "sad": 1.0}])
def test_puntuaciones_invalidas_se_rechazan(nativas):
    with pytest.raises(ValueError):
        colapsar(nativas)


def test_un_colapso_mal_escrito_se_rechaza():
    with pytest.raises(ValueError, match="colapso"):
        colapsar(_DEEPFACE, "cerca")


# --------------------------------------------------------------------------
# Backends opcionales
# --------------------------------------------------------------------------


def test_backend_no_disponible_es_un_import_error():
    assert issubclass(BackendNoDisponible, ImportError)


def test_sin_deepface_el_error_dice_que_hacer(monkeypatch):
    monkeypatch.setitem(sys.modules, "deepface", None)
    with pytest.raises(BackendNoDisponible, match="requirements-benchmark.txt"):
        DeepFaceBackend()


def test_sin_hsemotion_el_error_dice_que_hacer(monkeypatch):
    monkeypatch.setitem(sys.modules, "hsemotion", None)
    monkeypatch.setitem(sys.modules, "hsemotion.facial_emotions", None)
    with pytest.raises(BackendNoDisponible, match="keras"):
        crear_backend("hsemotion")


def test_un_backend_desconocido_se_rechaza():
    with pytest.raises(ValueError, match="otro"):
        crear_backend("otro")


def test_el_modulo_no_carga_tensorflow_ni_las_librerias_opcionales_al_importarse():
    """Se importa en un proceso limpio con deepface y hsemotion bloqueados."""
    codigo = (
        "import sys\n"
        "sys.modules['deepface'] = None\n"
        "sys.modules['hsemotion'] = None\n"
        "import telmeow_teleop.emotion_classifier\n"
        "print('keras' in sys.modules, 'tensorflow' in sys.modules, "
        "'mediapipe' in sys.modules)\n"
    )
    salida = subprocess.run([sys.executable, "-c", codigo], cwd=PROJECT_ROOT,
                            capture_output=True, text=True, timeout=120)
    assert salida.returncode == 0, salida.stderr
    assert salida.stdout.split() == ["False", "False", "False"]


def _deepface_falso(monkeypatch, llamadas):
    modulo = types.ModuleType("deepface")

    class DeepFace:
        @staticmethod
        def analyze(**kwargs):
            llamadas.append(kwargs)
            return [{"emotion": dict(_DEEPFACE), "dominant_emotion": "happy"}]

    modulo.DeepFace = DeepFace
    monkeypatch.setitem(sys.modules, "deepface", modulo)


def test_deepface_recibe_el_recorte_en_bgr_sin_volver_a_detectar(monkeypatch):
    llamadas = []
    _deepface_falso(monkeypatch, llamadas)
    backend = DeepFaceBackend()
    rostro = np.zeros((60, 50, 3), dtype=np.uint8)
    rostro[..., 0] = 255                       # azul puro, en BGR
    assert backend.predict(rostro) == ("feliz", pytest.approx(0.90))
    enviado = llamadas[-1]
    assert enviado["detector_backend"] == "skip"
    assert enviado["actions"] == ("emotion",)
    assert enviado["img_path"][..., 0].min() == 255 and enviado["img_path"][..., 2].max() == 0


def _hsemotion_falso(monkeypatch, recibidas, idx_to_class, puntuaciones):
    paquete = types.ModuleType("hsemotion")
    submodulo = types.ModuleType("hsemotion.facial_emotions")

    class HSEmotionRecognizer:
        def __init__(self, model_name, device):
            self.model_name, self.device = model_name, device
            self.idx_to_class = dict(idx_to_class)

        def predict_emotions(self, face_img, logits=True):
            assert logits is False                 # probabilidades, no logits
            recibidas.append(face_img)
            return "x", np.array(puntuaciones, dtype=np.float32)

    submodulo.HSEmotionRecognizer = HSEmotionRecognizer
    paquete.facial_emotions = submodulo
    monkeypatch.setitem(sys.modules, "hsemotion", paquete)
    monkeypatch.setitem(sys.modules, "hsemotion.facial_emotions", submodulo)


_HSE_8 = {0: "Anger", 1: "Contempt", 2: "Disgust", 3: "Fear",
          4: "Happiness", 5: "Neutral", 6: "Sadness", 7: "Surprise"}


def test_hsemotion_recibe_rgb():
    """Su ``predict_emotions`` hace PIL.Image.fromarray y normaliza con las
    medias de ImageNet en orden R, G, B (código fuente de hsemotion 0.3.0)."""
    recibidas = []
    with pytest.MonkeyPatch.context() as mp:
        _hsemotion_falso(mp, recibidas, _HSE_8, [0, 0, 0, 0, 0.9, 0.1, 0, 0])
        backend = HSEmotionBackend()
        rostro = np.zeros((60, 50, 3), dtype=np.uint8)
        rostro[..., 0] = 255                   # azul puro, en BGR
        assert backend.predict(rostro).etiqueta == "feliz"
    assert recibidas[-1][..., 2].min() == 255 and recibidas[-1][..., 0].max() == 0


def test_hsemotion_lee_el_orden_de_clases_del_paquete():
    """El mismo vector de puntuaciones con otro ``idx_to_class`` da otra
    etiqueta: el orden no está escrito en este módulo."""
    puntuaciones = [0, 0, 0, 0, 0.9, 0.1, 0, 0]
    with pytest.MonkeyPatch.context() as mp:
        _hsemotion_falso(mp, [], _HSE_8, puntuaciones)
        assert HSEmotionBackend().predict(np.zeros((9, 9, 3), np.uint8)).etiqueta == "feliz"
    otro = dict(_HSE_8)
    otro[4], otro[7] = "Surprise", "Happiness"
    with pytest.MonkeyPatch.context() as mp:
        _hsemotion_falso(mp, [], otro, puntuaciones)
        assert HSEmotionBackend().predict(np.zeros((9, 9, 3), np.uint8)).etiqueta == "sorprendido"


# Con las librerías DE VERDAD. Solo corren en el entorno de
# requirements-benchmark.txt (en .venv se saltan), y la primera vez descargan
# los pesos. Atan lo que los falsos de arriba suponen: el formato de la salida
# y los nombres de las clases de cada librería.


@requiere_dataset
def test_deepface_de_verdad():
    pytest.importorskip("deepface")
    import os

    backend = DeepFaceBackend()
    assert os.environ.get("TF_USE_LEGACY_KERAS") == "1"          # H45
    img = cv2.imread(str(DATASET / "feliz_1.jpg"))
    x, y, w, h = detectar_rostro(img, cargar_cascade())
    d = backend.distribucion(img[y:y + h, x:x + w])
    assert d.nativa in {"angry", "disgust", "fear", "happy", "sad", "surprise", "neutral"}
    assert sum(d.probabilidades.values()) == pytest.approx(1.0)
    if MODELO.is_file():                     # el propio sigue leyendo su .keras
        assert KerasBackend(MODELO).predict(img[y:y + h, x:x + w]).etiqueta == "feliz"


@requiere_dataset
def test_hsemotion_de_verdad():
    pytest.importorskip("hsemotion")
    backend = HSEmotionBackend()
    assert set(backend._clases) == set(_HSE_8.values())
    img = cv2.imread(str(DATASET / "sorprendido_10.jpg"))
    x, y, w, h = detectar_rostro(img, cargar_cascade())
    d = backend.distribucion(img[y:y + h, x:x + w])
    assert sum(d.probabilidades.values()) == pytest.approx(1.0, abs=1e-5)


# --------------------------------------------------------------------------
# El clasificador: tasa, histéresis, manual y espejo — sin cámara ni modelo
# --------------------------------------------------------------------------


class _BackendFalso(EmotionBackend):
    """Devuelve las respuestas en orden y repite la última."""

    nombre = "falso"

    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.recortes = []

    def distribucion(self, rostro_bgr):
        self.recortes.append(rostro_bgr)
        etiqueta, confianza = (
            self.respuestas.pop(0) if len(self.respuestas) > 1 else self.respuestas[0]
        )
        probabilidades = dict.fromkeys(EMOTION_LABELS, 0.0)
        if etiqueta is not None:
            probabilidades[etiqueta] = confianza
        return Distribucion(etiqueta, confianza, probabilidades, etiqueta or "fear")


def _clasificador(respuestas, detector=lambda frame: (10, 20, 30, 40), **kwargs):
    kwargs.setdefault("frame_espejado", False)
    return EmotionClassifier(_BackendFalso(respuestas), detector=detector, **kwargs)


FRAME = np.zeros((120, 160, 3), dtype=np.uint8)


def test_arranca_en_neutral_y_sin_confianza():
    c = _clasificador([("feliz", 0.9)])
    assert c.emocion == Emocion("neutral", None)
    assert c.manual is None


@pytest.mark.parametrize("fps", [15.0, 30.0, 60.0])
def test_infiere_a_5_hz_y_no_por_frame(fps):
    c = _clasificador([("feliz", 0.9)], emotion_hz=5.0)
    n = int(2.0 * fps)
    for i in range(n):
        c.process(FRAME, timestamp=i / fps)
    assert c.inferencias == 10                      # 2 s a 5 Hz, sea cual sea la cámara
    assert len(c.backend.recortes) == 10


def test_entre_inferencias_devuelve_lo_ultimo_decidido():
    c = _clasificador([("feliz", 0.9), ("triste", 0.9)])
    assert c.process(FRAME, timestamp=0.0) == ("feliz", pytest.approx(0.9))
    assert c.process(FRAME, timestamp=0.1) == ("feliz", pytest.approx(0.9))
    assert c.inferencias == 1


def test_tras_un_paron_no_recupera_inferencias_en_rafaga():
    c = _clasificador([("feliz", 0.9)])
    for t in (0.0, 0.2, 1.5):                         # 1.3 s parado
        c.process(FRAME, timestamp=t)
    for t in (1.53, 1.57, 1.6, 1.65):               # ninguno llega a 1.5 + 0.2
        c.process(FRAME, timestamp=t)
    assert c.inferencias == 3
    c.process(FRAME, timestamp=1.7)
    assert c.inferencias == 4


def test_cambiar_de_expresion_exige_mayoria_de_la_ventana():
    c = _clasificador([("feliz", 0.9)] * 5 + [("triste", 0.9)] * 3, n_frames=5)
    t = 0.0
    for _ in range(5):
        c.process(FRAME, timestamp=t)
        t += 0.2
    assert c.emocion.etiqueta == "feliz"
    for esperado in ("feliz", "feliz", "triste"):   # el tercer voto da la mayoría
        assert c.process(FRAME, timestamp=t).etiqueta == esperado
        t += 0.2


def test_un_clasificador_que_duda_no_cambia_la_expresion():
    """Alternancia perfecta con confianzas de 0.5: la mayoría sola no la frena,
    la confianza mínima sí (ver filters.ClassHysteresis)."""
    respuestas = [("feliz", 0.5), ("triste", 0.5)] * 10
    c = _clasificador(respuestas, n_frames=5, min_confidence=0.6)
    for i in range(20):
        assert c.process(FRAME, timestamp=0.2 * i).etiqueta == "neutral"


def test_la_confianza_es_la_media_de_los_votos_de_la_expresion_vigente():
    c = _clasificador([("feliz", 0.9), ("feliz", 0.7), ("triste", 0.95)], n_frames=5)
    for i in range(3):
        emocion = c.process(FRAME, timestamp=0.2 * i)
    assert emocion == ("feliz", pytest.approx(0.8))


def test_sin_rostro_se_mantiene_la_expresion_y_la_confianza_no_se_congela():
    caras = iter([(10, 20, 30, 40), None, None, (10, 20, 30, 40)])
    c = _clasificador([("feliz", 0.9)], detector=lambda frame: next(caras))
    assert c.process(FRAME, timestamp=0.0) == ("feliz", pytest.approx(0.9))
    assert c.process(FRAME, timestamp=0.2) == ("feliz", None)
    c.process(FRAME, timestamp=0.4)
    assert c.inferencias_sin_rostro == 2
    assert c.rostro is None and c.ultima is None
    assert len(c.backend.recortes) == 1            # sin cara no se llama a la red
    assert c.process(FRAME, timestamp=0.6) == ("feliz", pytest.approx(0.9))
    assert c.inferencias_sin_rostro == 0


def test_una_prediccion_sin_equivalente_no_vota():
    c = _clasificador([("feliz", 0.9), (None, 0.8)])
    c.process(FRAME, timestamp=0.0)
    assert c.process(FRAME, timestamp=0.2) == ("feliz", None)
    assert c.ultima.etiqueta is None and c.ultima.nativa == "fear"
    assert len(c.ventana) == 1


def test_concentrado_es_manual_y_la_inferencia_sigue_corriendo():
    c = _clasificador([("feliz", 0.9)])
    assert c.toggle_manual("concentrado") == "concentrado"
    assert c.process(FRAME, timestamp=0.0) == ("concentrado", None)
    assert c.inferencias == 1                       # sigue mirando la cara
    assert c.toggle_manual("concentrado") is None   # la misma otra vez la suelta
    assert c.emocion == ("feliz", pytest.approx(0.9))


def test_al_volver_a_automatico_la_expresion_es_la_de_la_cara_de_ahora():
    c = _clasificador([("feliz", 0.9)] * 5 + [("triste", 0.9)] * 5)
    c.toggle_manual("enojado")
    for i in range(10):
        assert c.process(FRAME, timestamp=0.2 * i).etiqueta == "enojado"
    c.clear_manual()
    assert c.emocion.etiqueta == "triste"


def test_forzar_otra_expresion_la_sustituye():
    c = _clasificador([("feliz", 0.9)])
    c.toggle_manual("feliz")
    assert c.toggle_manual("triste") == "triste"
    assert c.manual == "triste"


def test_una_expresion_que_el_visor_no_tiene_se_rechaza():
    c = _clasificador([("feliz", 0.9)])
    with pytest.raises(ValueError, match="visor"):
        c.toggle_manual("asco")


def test_cambiar_de_backend_conserva_la_expresion_y_vacia_la_ventana():
    c = _clasificador([("feliz", 0.9)])
    c.process(FRAME, timestamp=0.0)
    c.cambiar_backend(_BackendFalso([("triste", 0.55)]))
    assert c.emocion == ("feliz", None)
    assert c.ventana == ()
    c.process(FRAME, timestamp=0.2)
    assert c.emocion.etiqueta == "feliz"            # 0.55 < 0.6: el voto no basta


def test_el_espejo_se_deshace_antes_de_detectar_y_de_clasificar():
    """El frame llega volteado; el detector y la red tienen que ver la cara
    como se grabó el dataset, y el rectángulo del HUD, como la ve el operador."""
    original = np.zeros((100, 200, 3), dtype=np.uint8)
    original[:, :100] = 50                          # mitad izquierda, sin voltear
    original[:, 100:] = 200
    vistos = []

    def detector(frame):
        vistos.append(frame)
        return (10, 10, 30, 30)

    c = _clasificador([("feliz", 0.9)], detector=detector, frame_espejado=True)
    c.process(cv2.flip(original, 1), timestamp=0.0)
    assert np.array_equal(vistos[-1], original)
    assert (c.backend.recortes[-1] == 50).all()
    assert c.rostro == (160, 10, 30, 30)            # 200 - 10 - 30
    volteado = cv2.flip(original, 1)
    x, y, w, h = c.rostro
    assert (volteado[y:y + h, x:x + w] == 50).all()


def test_una_tasa_no_positiva_se_rechaza():
    with pytest.raises(ValueError):
        _clasificador([("feliz", 0.9)], emotion_hz=0.0)


def test_desde_config_toma_tasa_histeresis_y_espejo():
    cfg = AppConfig.from_env({
        "TELMEOW_EMOTION_HZ": "4", "TELMEOW_EMOTION_N_FRAMES": "3",
        "TELMEOW_EMOTION_MIN_CONFIDENCE": "0.7", "TELMEOW_CAMERA_FLIP": "0",
    })
    c = EmotionClassifier.desde_config(cfg, backend=_BackendFalso([("feliz", 0.9)]))
    assert (c.n_frames, c.min_confidence, c.frame_espejado) == (3, 0.7, False)
    for i in range(8):
        c.process(FRAME, timestamp=i / 8)
    assert c.inferencias == 4                       # 1 s a 4 Hz


def test_el_demo_corre_entero_con_camara_y_teclas_simuladas(monkeypatch, tmp_path):
    """El modo demo solo corre con una cámara delante, así que ninguna otra
    prueba lo ejecuta (pyflakes ve los nombres, no los errores de ejecución).
    Aquí corre de punta a punta: cámara, teclas y backends falsos, sin ventana,
    y con --record."""
    import telmeow_teleop.camera as camara
    import telmeow_teleop.emotion_classifier as ec

    foto = DATASET / "feliz_1.jpg"
    frame = cv2.imread(str(foto)) if foto.is_file() else np.zeros((480, 640, 3), np.uint8)

    class _Cap:
        def __init__(self):
            self.n = 0

        def read(self):
            self.n += 1
            return (self.n <= 40), frame.copy()

        def get(self, _propiedad):
            return 640.0

        def release(self):
            pass

    teclas = iter([ord("6"), ord("b"), ord("0"), ord(" "), ord("1"), ord("1"), ord("b")]
                  + [255] * 25 + [ord("q")])
    construidos = []

    def fabrica(nombre, cfg=None):
        construidos.append(nombre)
        if nombre == "hsemotion":
            raise BackendNoDisponible(nombre, ImportError("simulado"))
        backend = _BackendFalso([("feliz", 0.9), ("triste", 0.8)])
        backend.nombre = nombre
        return backend

    monkeypatch.setattr(camara, "abrir_camara", lambda cfg: _Cap())
    monkeypatch.setattr(ec, "crear_backend", fabrica)
    monkeypatch.setattr(cv2, "imshow", lambda *a: None)
    monkeypatch.setattr(cv2, "waitKey", lambda *a: next(teclas))
    monkeypatch.setattr(cv2, "destroyAllWindows", lambda: None)
    monkeypatch.setenv("TELMEOW_EMOTION_HZ", "1000")      # una inferencia por frame
    registro = tmp_path / "emocion.csv"

    assert ec._demo(["--record", str(registro)]) == 0
    assert construidos[:3] == ["keras", "deepface", "hsemotion"]   # 'b' se salta el ausente
    filas = registro.read_text(encoding="utf-8").splitlines()
    assert filas[0].split(",") == ec._COLUMNAS_CSV
    assert len(filas) > 20
    assert any(",concentrado," in f for f in filas)      # la tecla 6 llegó al CSV


# --------------------------------------------------------------------------
# Con el modelo propio de verdad
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def backend():
    if not MODELO.is_file():
        pytest.skip("emotion_model.keras no está (vive fuera de git)")
    pytest.importorskip("keras")
    return KerasBackend(MODELO)


def _muestra(paso: int):
    """1 de cada ``paso`` imágenes del dataset, con su etiqueta de archivo."""
    archivos = sorted(a for a in DATASET.glob("*.jpg") if a.name.split("_")[0] in EMOTION_LABELS)
    return [(a, a.name.split("_")[0]) for a in archivos[::paso]]


def _aciertos(backend_, paso: int) -> tuple[int, int]:
    cascade = cargar_cascade()
    aciertos = total = 0
    for archivo, clase in _muestra(paso):
        img = cv2.imread(str(archivo))
        rect = detectar_rostro(img, cascade, criterio="primera")
        if rect is None:
            continue
        x, y, w, h = rect
        total += 1
        aciertos += backend_.predict(img[y:y + h, x:x + w]).etiqueta == clase
    return aciertos, total


@requiere_modelo
def test_el_modelo_carga_con_el_orden_de_sus_metadatos(backend):
    assert backend.clases == EMOTION_LABELS
    assert backend.metadatos is not None
    assert backend.modelo.input_shape[1:] == (48, 48, 1)
    assert backend.modelo.output_shape[-1] == 5


@requiere_modelo
def test_sin_metadatos_el_modelo_propio_avisa(tmp_path):
    pytest.importorskip("keras")
    with pytest.warns(OrdenDeClasesSupuesto):
        sin_meta = KerasBackend(MODELO, ruta_meta=tmp_path / "no_existe.meta.json")
    assert sin_meta.clases == EMOTION_LABELS


@requiere_modelo
@requiere_dataset
def test_llamar_al_modelo_da_lo_mismo_que_predict(backend):
    """Bug 12: el camino nuevo frente al del legacy, sobre una cara real."""
    img = cv2.imread(str(DATASET / "triste_1.jpg"))
    x, y, w, h = detectar_rostro(img, cargar_cascade())
    rostro = img[y:y + h, x:x + w]
    d = backend.distribucion(rostro)
    por_predict = backend.modelo.predict(_legacy(rostro), verbose=0)[0]
    nuestro = [d.probabilidades[c] for c in backend.clases]
    assert np.allclose(nuestro, por_predict, atol=1e-6)
    assert d.etiqueta == "triste"


@requiere_modelo
@requiere_dataset
def test_el_orden_de_clases_cuadra_con_las_etiquetas_del_operador(backend):
    """El ancla externa del orden: el nombre de archivo que puso el operador al
    grabar. Es el dataset de entrenamiento, así que NO mide la accuracy (eso es
    el P9): mide que el pipeline reproduce el entrenamiento. Medido: 7454 de
    7459 en todo el dataset (tools/medir_emocion.py)."""
    aciertos, total = _aciertos(backend, 80)
    assert total >= 80
    assert aciertos / total >= 0.95


@requiere_modelo
@requiere_dataset
def test_la_prueba_del_orden_detecta_un_orden_equivocado(tmp_path):
    """La prueba anterior no pasa por casualidad: con las clases rotadas una
    posición, los aciertos se hunden."""
    pytest.importorskip("keras")
    rotado = list(EMOTION_LABELS[1:]) + [EMOTION_LABELS[0]]
    meta = tmp_path / "rotado.meta.json"
    meta.write_text(json.dumps({"clases": rotado}), encoding="utf-8")
    aciertos, total = _aciertos(KerasBackend(MODELO, ruta_meta=meta), 80)
    assert aciertos / total < 0.2


@requiere_modelo
@requiere_dataset
def test_con_el_frame_volteado_se_clasifica_la_cara_sin_voltear(backend):
    """De punta a punta con el Haar y la red de verdad: el frame volteado del
    pipeline da EXACTAMENTE lo mismo que la imagen tal como se grabó."""
    img = cv2.imread(str(DATASET / "sorprendido_10.jpg"))
    directo = EmotionClassifier(backend, frame_espejado=False)
    espejado = EmotionClassifier(backend, frame_espejado=True)
    directo.process(img, timestamp=0.0)
    espejado.process(cv2.flip(img, 1), timestamp=0.0)
    assert directo.ultima is not None              # que no pase por no ver a nadie
    assert directo.ultima.etiqueta == "sorprendido"
    assert espejado.ultima == directo.ultima
    x, y, w, h = directo.rostro
    assert espejado.rostro == (img.shape[1] - x - w, y, w, h)


@requiere_modelo
def test_el_backend_propio_no_depende_de_tf_use_legacy_keras(tmp_path):
    """DeepFace fija TF_USE_LEGACY_KERAS=1 al importarse. Con esa variable,
    ``tf.keras`` deja de ser Keras 3; el backend propio usa ``keras`` directo y
    tiene que seguir cargando el modelo."""
    codigo = (
        "import numpy as np\n"
        "from telmeow_teleop.emotion_classifier import KerasBackend\n"
        "from telmeow_teleop.config import PROJECT_ROOT\n"
        "b = KerasBackend(PROJECT_ROOT / 'emotion_model.keras')\n"
        "print(b.predict(np.zeros((60, 60, 3), np.uint8)).etiqueta)\n"
    )
    import os

    entorno = dict(os.environ, TF_USE_LEGACY_KERAS="1", TF_CPP_MIN_LOG_LEVEL="3")
    salida = subprocess.run([sys.executable, "-c", codigo], cwd=PROJECT_ROOT, env=entorno,
                            capture_output=True, text=True, timeout=300)
    assert salida.returncode == 0, salida.stderr[-2000:]
    assert salida.stdout.strip().splitlines()[-1] in EMOTION_LABELS


@requiere_modelo
def test_un_modelo_que_no_termina_en_softmax_se_rechaza(tmp_path):
    """Con logits, la «confianza» no sería una probabilidad y el umbral de la
    histéresis no significaría nada."""
    keras = pytest.importorskip("keras")
    entrada = keras.Input((48, 48, 1))
    salida = keras.layers.Dense(5)(keras.layers.Flatten()(entrada))
    ruta = tmp_path / "logits.keras"
    keras.Model(entrada, salida).save(ruta)
    shutil.copy(PROJECT_ROOT / "emotion_model.meta.json", tmp_path / "logits.meta.json")
    with pytest.raises(ValueError, match="softmax"):
        KerasBackend(ruta)
