"""Pruebas de ``tools/medir_emocion.py`` sin dataset ni modelo: un detector, un
backend, una red y una malla falsos, con lo que se sabe que tienen que dar."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import medir_emocion as me  # noqa: E402

from telmeow_teleop.emotion_classifier import Prediccion  # noqa: E402


def test_la_clase_sale_del_nombre_que_puso_el_operador():
    assert me.clase_de(Path("x/feliz_123.jpg")) == "feliz"
    assert me.clase_de(Path("sorprendido_1.jpg")) == "sorprendido"
    assert me.clase_de(Path("asco_3.jpg")) is None
    assert me.clase_de(Path("Thumbs.db")) is None


def test_el_percentil():
    assert me._percentil([1.0, 2.0, 3.0, 4.0, 5.0], 50) == pytest.approx(3.0)
    assert me._percentil([1.0, 2.0, 3.0, 4.0, 5.0], 100) == pytest.approx(5.0)
    assert me._percentil([], 50) != me._percentil([], 50)          # NaN


class _Reloj:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class _RedFalsa:
    """9 ms por llamada directa y 64 por predict; la primera cuesta 640."""

    def __init__(self, reloj, diferencia=0.0):
        self.reloj, self.diferencia, self.llamadas = reloj, diferencia, 0

    def __call__(self, x, training=False):
        assert training is False
        self.llamadas += 1
        self.reloj.t += 0.640 if self.llamadas == 1 else 0.009
        return np.full((1, 5), 0.2, dtype=np.float32)

    def predict(self, x, verbose=0):
        self.reloj.t += 0.064
        return np.full((1, 5), 0.2 + self.diferencia, dtype=np.float32)


def test_mide_la_red_por_las_dos_vias_y_aparte_la_primera_llamada():
    reloj = _Reloj()
    r = me.medir_red(_RedFalsa(reloj), repeticiones=10, reloj=reloj)
    assert r["primera_ms"] == pytest.approx(640.0)
    assert r["llamada_ms"] == pytest.approx(9.0)
    assert r["predict_ms"] == pytest.approx(64.0)
    assert r["max_dif"] == 0.0


def test_la_medida_de_la_red_detecta_que_las_dos_vias_difieran():
    reloj = _Reloj()
    assert me.medir_red(_RedFalsa(reloj, 0.01), repeticiones=4, reloj=reloj)["max_dif"] > 0.009


class _CascadeFalso:
    """Devuelve ``caras`` sin minSize y ``caras_min`` con él."""

    def __init__(self, caras, caras_min=None):
        self.caras = caras
        self.caras_min = caras if caras_min is None else caras_min

    def detectMultiScale(self, gris, scaleFactor, minNeighbors, minSize=None):
        return list(self.caras if minSize is None else self.caras_min)


class _BackendPorBrillo:
    """«feliz» si el recorte es oscuro y «triste» si es claro."""

    def predict(self, rostro):
        return Prediccion("feliz" if rostro.mean() < 128 else "triste", 0.9)


def _imagen():
    img = np.zeros((100, 200, 3), dtype=np.uint8)
    img[:, :100] = 50
    img[:, 100:] = 200
    return img


def test_la_cara_volteada_se_clasifica_volteada():
    """La misma caja sobre la imagen volteada cae en la otra mitad: el
    instrumento tiene que ver la diferencia que existe para medir."""
    r = me.analizar_imagen(_imagen(), "feliz", _CascadeFalso([(10, 10, 30, 30)]),
                           _BackendPorBrillo())
    assert r.aciertos["tal cual"] is True
    assert r.aciertos["volteada"] is False


class _BackendQueMira:
    """Guarda lo que recibe y acierta solo si llega en BGR de 3 canales."""

    def __init__(self):
        self.recibido = []

    def predict(self, rostro):
        self.recibido.append(rostro)
        bgr = rostro.ndim == 3 and rostro[..., 0].mean() > rostro[..., 2].mean()
        return Prediccion("feliz" if bgr else "triste", 0.9)


def test_cada_variante_le_da_al_backend_lo_que_dice():
    img = np.zeros((100, 200, 3), dtype=np.uint8)
    img[..., 0] = 200                                  # azul, en BGR
    backend = _BackendQueMira()
    r = me.analizar_imagen(img, "feliz", _CascadeFalso([(10, 10, 60, 60)]), backend,
                           variantes=("tal cual", "colores al reves", "48x48 gris"))
    assert r.aciertos == {"tal cual": True, "colores al reves": False, "48x48 gris": False}
    tal_cual, al_reves, gris = backend.recibido
    assert tal_cual.shape == (60, 60, 3) and tal_cual[..., 0].min() == 200
    assert al_reves[..., 2].min() == 200 and al_reves[..., 0].max() == 0
    assert gris.shape == (48, 48)


def test_sin_cara_no_cuenta_como_fallo():
    r = me.analizar_imagen(_imagen(), "feliz", _CascadeFalso([]), _BackendPorBrillo())
    assert r.aciertos == {}
    assert r.caras == []


def test_el_resumen_cuenta_detecciones_y_cambios_por_minsize():
    registros = [
        me.Registro("feliz", caras=[(0, 0, 300, 300)], caras_min=[(0, 0, 300, 300)],
                    aciertos={"tal cual": True, "volteada": False}),
        # dos caras, la primera pequeña: minSize se la lleva y cambia la 'primera'
        me.Registro("feliz", caras=[(5, 5, 40, 40), (0, 0, 280, 280)],
                    caras_min=[(0, 0, 280, 280)],
                    aciertos={"tal cual": True, "volteada": True}),
        # solo una cara pequeña: con minSize se pierde
        me.Registro("triste", caras=[(5, 5, 50, 50)], caras_min=[],
                    aciertos={"tal cual": False}),
        me.Registro("triste"),                              # sin cara
    ]
    r = me.resumir(registros)
    assert (r["imagenes"], r["con_cara"], r["varias"]) == (4, 3, 1)
    assert r["primera_no_mayor"] == 1
    assert r["min_distinto_mayor"] == 0
    assert r["min_distinto_primera"] == 1
    assert r["min_perdidas"] == 1
    assert r["detecciones"][("triste", 0)] == 1
    assert r["aciertos"]["feliz"] == {"tal cual": (2, 2), "volteada": (1, 2)}
    assert r["aciertos"]["triste"] == {"tal cual": (0, 1)}


def test_la_escala_de_cara_se_lee_del_registro_del_head_tracker(tmp_path):
    csv = tmp_path / "cabeza.csv"
    csv.write_text("t,escala_cara,ancho\n0.0,60.5,640\n0.1,,640\n0.2,nan,640\n0.3,61.5,640\n",
                   encoding="utf-8")
    assert me.escala_registrada(csv) == [60.5, 61.5]


def test_la_relacion_entre_la_caja_y_los_ojos():
    """Comisuras a 60 px en una imagen de 640×480 y una caja de 120: 2.0. Son
    los landmarks 33 y 263, los de ``HeadTracker.face_scale``."""
    puntos = [SimpleNamespace(x=0.0, y=0.0) for _ in range(478)]
    puntos[33] = SimpleNamespace(x=290 / 640, y=200 / 480)
    puntos[263] = SimpleNamespace(x=350 / 640, y=200 / 480)
    malla = SimpleNamespace(process=lambda rgb: SimpleNamespace(
        multi_face_landmarks=[SimpleNamespace(landmark=puntos)]))
    imagenes = [np.zeros((480, 640, 3), dtype=np.uint8)]
    assert me.relacion_haar_ojos(imagenes, _CascadeFalso([(260, 150, 120, 120)]), malla) == [
        pytest.approx(2.0)]
    sin_malla = SimpleNamespace(process=lambda rgb: SimpleNamespace(multi_face_landmarks=None))
    assert me.relacion_haar_ojos(imagenes, _CascadeFalso([(260, 150, 120, 120)]), sin_malla) == []
