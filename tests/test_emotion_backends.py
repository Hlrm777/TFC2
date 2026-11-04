"""Pruebas de ``telmeow_teleop.emotion_backends`` y ``tools/entrenar_blendshapes.py``.

Los backends del P6.4 se prueban con un Face Landmarker falso (la regresión y
la calibración no dependen de MediaPipe) y, si los modelos descargados están en
``modelos_externos/``, con los de verdad.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import entrenar_blendshapes as eb  # noqa: E402

import telmeow_teleop.emotion_backends as bc  # noqa: E402
from telmeow_teleop.config import EMOTION_LABELS, PROJECT_ROOT, PathConfig  # noqa: E402

DATASET = PROJECT_ROOT / "emotions_dataset"
TASK = PathConfig().face_landmarker
#: Para los backends con el Face Landmarker falso: no se abre.
TASK_FALSO = Path("no_se_abre.task")


def test_los_rasgos_simetricos_juntan_izquierda_y_derecha():
    r = bc.rasgos_simetricos({"_neutral": 0.9, "mouthSmileLeft": 0.2,
                              "mouthSmileRight": 0.6, "jawOpen": 0.3})
    assert r == pytest.approx({"jawOpen": 0.3, "mouthSmile": 0.4})


class _LandmarkerFalso:
    """Devuelve los blendshapes que diga el brillo del recorte: 0 sin cara."""

    def __init__(self, *_):
        pass

    def blendshapes(self, imagen):
        nivel = float(imagen.mean()) / 255
        if nivel == 0:
            return None
        return {"mouthSmileLeft": nivel, "mouthSmileRight": nivel, "jawOpen": 0.1}


def _pesos(tmp_path, relativo=False):
    """Una regresión a mano: ``mouthSmile`` alto es feliz; bajo, neutral."""
    clases = list(EMOTION_LABELS)
    W = np.zeros((2, 5))
    W[1, clases.index("feliz")] = 4.0
    W[1, clases.index("neutral")] = -4.0
    ruta = tmp_path / "pesos.json"
    ruta.write_text(json.dumps({
        "rasgos": ["jawOpen", "mouthSmile"], "clases": clases, "relativo": relativo,
        # Relativo: los rasgos ya llegan menos la neutra, centrados en 0, y
        # el sesgo inclina el empate hacia neutral.
        "media": [0.0, 0.0] if relativo else [0.1, 0.5], "desviacion": [1.0, 0.1],
        "W": W.tolist(), "b": [0.0, 0.0, 0.0, 0.5 if relativo else 0.0, 0.0],
    }), encoding="utf-8")
    return ruta


@pytest.fixture
def landmarker_falso(monkeypatch):
    monkeypatch.setattr(bc, "Landmarker", _LandmarkerFalso)


def _cara(nivel):
    return np.full((40, 40, 3), nivel, dtype=np.uint8)


def test_blendshapes_clasifica_con_los_pesos_del_json(tmp_path, landmarker_falso):
    backend = bc.BlendshapesBackend(_pesos(tmp_path), TASK_FALSO)
    alta, baja = backend.distribucion(_cara(230)), backend.distribucion(_cara(30))
    assert alta.etiqueta == "feliz" and baja.etiqueta == "neutral"
    assert sum(alta.probabilidades.values()) == pytest.approx(1.0)
    assert backend.modelo == "pesos.json"


def test_blendshapes_sin_cara_no_vota(tmp_path, landmarker_falso):
    d = bc.BlendshapesBackend(_pesos(tmp_path), TASK_FALSO).distribucion(_cara(0))
    assert d.etiqueta is None and d.confianza == 0.0


def test_blendshapes_relativo_mide_respecto_a_la_neutra_del_operador(tmp_path, landmarker_falso):
    backend = bc.BlendshapesBackend(_pesos(tmp_path, relativo=True), TASK_FALSO)
    with pytest.raises(RuntimeError, match="calibrar"):
        backend.distribucion(_cara(200))
    # Un operador cuya cara neutra ya da 200/255 de sonrisa: sin calibrar sería
    # feliz; respecto a su neutra, la misma cara es neutral.
    assert backend.calibrar([_cara(200)] * 3 + [_cara(0)]) == 3
    assert backend.distribucion(_cara(200)).etiqueta == "neutral"
    assert backend.distribucion(_cara(255)).etiqueta == "feliz"


def test_blendshapes_rechaza_clases_que_no_son_las_del_robot(tmp_path, landmarker_falso):
    ruta = _pesos(tmp_path)
    pesos = json.loads(ruta.read_text(encoding="utf-8"))
    pesos["clases"][0] = "asco"
    ruta.write_text(json.dumps(pesos), encoding="utf-8")
    with pytest.raises(ValueError, match="clases"):
        bc.BlendshapesBackend(ruta, TASK_FALSO)


def test_la_regresion_separa_un_problema_separable():
    rng = np.random.default_rng(3)
    centros = np.eye(5) * 3
    y = np.repeat(np.arange(5), 40)
    X = centros[y] + rng.normal(0, 0.3, (200, 5))
    modelo = eb.ajustar(X, y, 5, iteraciones=500)
    assert (eb.predecir(modelo, X) == y).mean() > 0.99


def test_la_validacion_es_el_final_de_la_numeracion_de_cada_clase():
    numeros = np.array([1, 2, 3, 4, 5, 10, 20, 30, 40, 50])
    clases = np.array([0] * 5 + [1] * 5)
    assert eb.particion(numeros, clases).tolist() == [False] * 4 + [True] + [False] * 4 + [True]
    assert eb.clase_y_numero(Path("triste_1234.jpg")) == ("triste", 1234)
    assert eb.clase_y_numero(Path("otra_cosa.jpg")) is None


@pytest.mark.skipif(not (TASK.is_file() and DATASET.is_dir()),
                    reason="hacen falta modelos_externos/face_landmarker.task y emotions_dataset/")
def test_el_face_landmarker_de_verdad_da_los_52_coeficientes():
    import cv2

    foto = cv2.imread(str(sorted(DATASET.glob("feliz_*.jpg"))[0]))
    b = bc.Landmarker(TASK).blendshapes(foto)
    assert b is not None and len(b) == 52
    r = bc.rasgos_simetricos(b)
    assert set(bc.FACS) <= set(r)
    assert r["mouthSmile"] > 0.3                        # es una foto de feliz


ONNX = PathConfig().emotion_onnx


@pytest.mark.skipif(not ONNX.is_file(), reason="falta modelos_externos/mbf_va_mtl.onnx")
def test_hsemotion_onnx_quita_valencia_y_activacion():
    pytest.importorskip("onnxruntime")
    backend = bc.OnnxHSEmotionBackend(ONNX)
    salida = backend._sesion.run(None, {backend._entrada: backend.tensor(_cara(120))})[0]
    assert salida.shape == (1, len(bc.CLASES_HSEMOTION_8) + 2)
    d = backend.distribucion(_cara(120))
    assert d.etiqueta in EMOTION_LABELS
    assert sum(d.probabilidades.values()) == pytest.approx(1.0)


class _Fijo(bc.EmotionBackend):
    """Devuelve siempre las mismas probabilidades; ``None`` es «sin cara»."""

    nombre = "fijo"

    def __init__(self, probabilidades, modelo="fijo", relativo=False):
        self.p, self.modelo, self.relativo = probabilidades, modelo, relativo
        self.calibradas = None

    def calibrar(self, rostros):
        self.calibradas = len(rostros)
        return len(rostros)

    def distribucion(self, rostro_bgr):
        if self.p is None:
            return bc.Distribucion(None, 0.0, dict.fromkeys(EMOTION_LABELS, 0.0), "sin cara")
        p = dict.fromkeys(EMOTION_LABELS, 0.0) | self.p
        e = max(p, key=p.get)
        return bc.Distribucion(e, p[e], p, e)


def test_el_combinado_promedia_las_probabilidades():
    a = _Fijo({"feliz": 0.6, "enojado": 0.4}, "a.onnx")
    b = _Fijo({"enojado": 0.5, "neutral": 0.5}, "b.json")
    d = bc.CombinadoBackend([a, b]).distribucion(_cara(100))
    # feliz 0.30, enojado 0.45, neutral 0.25: gana una que no ganaba ninguno solo.
    assert d.etiqueta == "enojado" and d.confianza == pytest.approx(0.45)
    assert sum(d.probabilidades.values()) == pytest.approx(1.0)
    assert bc.CombinadoBackend([a, b]).modelo == "a.onnx+b.json"


def test_el_combinado_deja_fuera_al_que_no_ve_cara():
    a = _Fijo({"triste": 0.7, "neutral": 0.3})
    d = bc.CombinadoBackend([a, _Fijo(None)]).distribucion(_cara(100))
    assert d.etiqueta == "triste" and d.confianza == pytest.approx(0.7)
    assert bc.CombinadoBackend([_Fijo(None), _Fijo(None)]).distribucion(_cara(100)).etiqueta is None


def test_el_combinado_calibra_solo_a_los_relativos():
    fijo, relativo = _Fijo({"feliz": 1.0}), _Fijo({"feliz": 1.0}, relativo=True)
    combinado = bc.CombinadoBackend([fijo, relativo])
    assert combinado.relativo
    assert combinado.calibrar([_cara(100)] * 4) == 4
    assert relativo.calibradas == 4 and fijo.calibradas is None
    assert not bc.CombinadoBackend([fijo, _Fijo({"feliz": 1.0})]).relativo
    with pytest.raises(ValueError):
        bc.CombinadoBackend([fijo])


@pytest.mark.skipif(not (TASK.is_file() and ONNX.is_file() and DATASET.is_dir()),
                    reason="hacen falta los modelos de modelos_externos/ y emotions_dataset/")
def test_el_combinado_de_operacion_de_punta_a_punta():
    """``crear_backend('combinado')`` y el clasificador de verdad: con fotos
    neutras del dataset se calibra solo (3 s), y luego una sonrisa da feliz."""
    pytest.importorskip("onnxruntime")
    import cv2

    from telmeow_teleop.config import AppConfig
    from telmeow_teleop.emotion_classifier import EmotionClassifier, crear_backend

    cfg = AppConfig()
    backend = crear_backend("combinado", cfg)
    assert backend.modelo == "mbf_va_mtl.onnx+emotion_blendshapes_facs_relativo_p65.json+cejas0.15"
    c = EmotionClassifier.desde_config(cfg, backend=backend)
    neutras = sorted(DATASET.glob("neutral_*.jpg"))[:20]
    felices = sorted(DATASET.glob("feliz_*.jpg"))[:10]
    t = 0.0
    for ruta in neutras + felices:
        c.process(cv2.flip(cv2.imread(str(ruta)), 1), timestamp=t)   # como llega volteado
        t += 0.2
    assert c.calibraciones == 1 and not c.calibracion_pendiente
    assert c.emocion.etiqueta == "feliz"


# --------------------------------------------------------------------------
# La regla de las cejas (P6.5, 2026-11-03)
# --------------------------------------------------------------------------


class _ConCejas(_Fijo):
    """Como los blendshapes: dice cuánto subieron las cejas en la última cara."""

    def __init__(self, probabilidades, cejas):
        super().__init__(probabilidades)
        self._cejas = cejas

    def cejas(self):
        return self._cejas


SORPRESA = {"sorprendido": 0.7, "neutral": 0.3}


@pytest.mark.parametrize("cejas, esperada", [(0.02, "neutral"), (0.40, "sorprendido"), (None, "sorprendido")])
def test_una_sorpresa_sin_cejas_pasa_a_neutral(cejas, esperada):
    """La boca abierta al hablar no es sorpresa: sin las cejas arriba, la
    probabilidad de sorprendido pasa a neutral. Sin dato de cejas (el Face
    Landmarker no vio la cara), no se toca."""
    combinado = bc.CombinadoBackend([_Fijo(SORPRESA), _ConCejas(SORPRESA, cejas)], umbral_cejas=0.15)
    d = combinado.distribucion(_cara(100))
    assert d.etiqueta == esperada
    assert sum(d.probabilidades.values()) == pytest.approx(1.0)
    if esperada == "neutral":
        assert d.probabilidades["sorprendido"] == 0.0 and d.confianza == pytest.approx(1.0)
        assert d.nativa == "sorprendido sin cejas"


def test_sin_umbral_la_regla_no_existe():
    combinado = bc.CombinadoBackend([_Fijo(SORPRESA), _ConCejas(SORPRESA, 0.0)])
    assert combinado.distribucion(_cara(100)).etiqueta == "sorprendido"
    assert "cejas" not in combinado.modelo


def test_la_regla_solo_toca_la_sorpresa_y_queda_en_el_modelo():
    feliz = {"feliz": 0.8, "neutral": 0.2}
    combinado = bc.CombinadoBackend([_Fijo(feliz), _ConCejas(feliz, 0.0)], umbral_cejas=0.15)
    assert combinado.distribucion(_cara(100)).etiqueta == "feliz"
    assert combinado.modelo.endswith("+cejas0.15")


def test_los_blendshapes_dicen_las_cejas_relativas_a_la_neutra(tmp_path, monkeypatch):
    class Cejas:
        def __init__(self, *_):
            pass

        def blendshapes(self, imagen):
            nivel = float(imagen.mean()) / 255
            return {"browOuterUpLeft": nivel, "browOuterUpRight": nivel,
                    "browInnerUp": nivel / 2, "mouthSmileLeft": 0.1, "mouthSmileRight": 0.1}

    monkeypatch.setattr(bc, "Landmarker", Cejas)
    clases = list(EMOTION_LABELS)
    ruta = tmp_path / "p.json"
    ruta.write_text(json.dumps({
        "rasgos": ["browInnerUp", "browOuterUp", "mouthSmile"], "clases": clases, "relativo": True,
        "media": [0, 0, 0], "desviacion": [1, 1, 1], "W": np.zeros((3, 5)).tolist(), "b": [0] * 5,
    }), encoding="utf-8")
    b = bc.BlendshapesBackend(ruta, TASK_FALSO)
    assert b.cejas() is None                          # aún no vio ninguna cara
    b.calibrar([_cara(51)])                           # neutra: browOuterUp 0.2
    b.distribucion(_cara(153))                        # browOuterUp 0.6
    assert b.cejas() == pytest.approx(0.4)            # la mayor de las dos, relativa


def test_la_regla_de_las_cejas_sale_de_la_configuracion(monkeypatch):
    from telmeow_teleop.config import AppConfig
    from telmeow_teleop import emotion_classifier as ec

    assert AppConfig().emotion.umbral_cejas == 0.15         # encendida (2026-11-03)
    assert AppConfig.from_env({"TELMEOW_EMOTION_UMBRAL_CEJAS": "0"}).emotion.umbral_cejas == 0.0
    cfg = AppConfig.from_env({"TELMEOW_EMOTION_UMBRAL_CEJAS": "0.15"})
    recibido = {}

    def crear_combinado(*_a, umbral_cejas=0.0, **_k):
        recibido["umbral"] = umbral_cejas
        return "combinado"

    monkeypatch.setattr(bc, "crear_combinado", crear_combinado)
    assert ec.crear_backend("combinado", cfg) == "combinado"
    assert recibido["umbral"] == 0.15
