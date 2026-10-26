"""Pruebas de ``tools/evaluar_backends.py``.

La que importa es el ANCLA: una sesión grabada por el camino real del demo
(``registrar_inferencia``: el CSV y los PNG) tiene que reproducirse exactamente
con el backend con que se grabó. Con un backend falso que decide por el
contenido del recorte, y con el modelo propio y el Haar de verdad sobre fotos
del dataset. Si los recortes no fueran lo que vio el backend en vivo, ninguna
comparación entre modelos valdría.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import evaluar_backends as eb  # noqa: E402

from telmeow_teleop.config import EMOTION_LABELS, PROJECT_ROOT  # noqa: E402
from telmeow_teleop.emotion_classifier import (  # noqa: E402
    _COLUMNAS_CSV,
    Distribucion,
    EmotionBackend,
    EmotionClassifier,
    registrar_inferencia,
)

MODELO = PROJECT_ROOT / "emotion_model.keras"
DATASET = PROJECT_ROOT / "emotions_dataset"
CAJA = (40, 30, 40, 40)                         # en el frame SIN espejo


class _PorBrillo(EmotionBackend):
    """Decide por el brillo medio del recorte: así el resultado depende de
    QUÉ píxeles recibe, que es lo que el ancla tiene que comprobar."""

    nombre = "brillo"

    def distribucion(self, rostro_bgr):
        nivel = float(rostro_bgr.mean())
        etiqueta = EMOTION_LABELS[min(4, int(nivel // 50))]
        confianza = 0.5 + (nivel % 50) / 100.0
        probabilidades = dict.fromkeys(EMOTION_LABELS, 0.0)
        probabilidades[etiqueta] = confianza
        return Distribucion(etiqueta, confianza, probabilidades, etiqueta)


class _Siempre(EmotionBackend):
    def __init__(self, etiqueta, nombre="siempre"):
        self.etiqueta, self.nombre = etiqueta, nombre

    def distribucion(self, rostro_bgr):
        probabilidades = dict.fromkeys(EMOTION_LABELS, 0.0)
        probabilidades[self.etiqueta] = 0.9
        return Distribucion(self.etiqueta, 0.9, probabilidades, self.etiqueta)


def _frame(nivel, ruido):
    """Un frame volteado (como el del pipeline) con la cara de un nivel de gris."""
    original = ruido.integers(0, 256, (120, 160, 3), dtype=np.uint8)
    x, y, w, h = CAJA
    original[y:y + h, x:x + w] = np.clip(
        nivel + ruido.integers(-10, 10, (h, w, 3)), 0, 255).astype(np.uint8)
    return cv2.flip(original, 1)


def _grabar_sesion(tmp_path, backend, pasos, detector=None, recalibrar_en=()):
    """Graba como el demo: ``EmotionClassifier`` y ``registrar_inferencia``.

    Cada paso es ``(etiqueta, nivel o None sin cara, manos)``."""
    actual = {}
    clasificador = EmotionClassifier(
        backend, frame_espejado=True,
        detector=detector or (lambda frame: None if actual["nivel"] is None else CAJA))
    ruido = np.random.default_rng(11)
    csv_ruta = tmp_path / "sesion.csv"
    carpeta = tmp_path / "sesion_recortes"
    with open(csv_ruta, "w", newline="", encoding="utf-8") as f:
        escritor = csv.writer(f)
        escritor.writerow(_COLUMNAS_CSV)
        for i, (etiqueta, nivel, manos) in enumerate(pasos):
            actual["nivel"] = nivel
            frame = _frame(nivel if nivel is not None else 0, ruido)
            t = 0.2 * i
            if i in recalibrar_en:
                clasificador.calibrar_neutro()          # la tecla c
            clasificador.process(frame, timestamp=t, manos=[(160 - 60, 50)] if manos else None)
            registrar_inferencia(escritor, t, etiqueta, clasificador, frame, 100.0, carpeta)
    return csv_ruta, carpeta


PASOS = ([(1, 60, False)] * 8 + [(2, 160, False)] * 8 + [(7, 220, True)] * 6
         + [(8, 120, False)] * 3 + [(8, None, False)] * 14 + [(0, 20, False)] * 6)


def test_el_ancla_reproduce_la_prediccion_y_lo_que_mando_el_robot(tmp_path):
    csv_ruta, carpeta = _grabar_sesion(tmp_path, _PorBrillo(), PASOS)
    filas = eb.cargar_sesion(csv_ruta, carpeta)
    ev = eb.evaluar(filas, carpeta, _PorBrillo())
    assert ev.comparables == sum(1 for f in filas if f["rostro"]) > 30
    assert ev.coinciden == ev.comparables
    assert [n["emocion"] for n in ev.filas] == [f["emocion"] for f in filas]
    # Y no es trivial: hubo varias expresiones, manos y un retorno a neutral.
    assert len({f["emocion"] for f in filas}) >= 3
    assert any(f["manos"] for f in filas) and any(f["retorno"] for f in filas)


def test_el_ancla_detecta_recortes_que_no_son_los_de_la_grabacion(tmp_path):
    csv_ruta, carpeta = _grabar_sesion(tmp_path, _PorBrillo(), PASOS)
    filas = eb.cargar_sesion(csv_ruta, carpeta)
    for fila in filas:
        if fila["rostro"]:
            fila["rx"] = str(int(fila["rx"]) + 8)     # la caja desplazada 8 px
    ev = eb.evaluar(filas, carpeta, _PorBrillo())
    assert ev.coinciden < ev.comparables


def test_otro_backend_cambia_lo_que_habria_hecho_el_robot(tmp_path):
    csv_ruta, carpeta = _grabar_sesion(tmp_path, _PorBrillo(), PASOS)
    filas = eb.cargar_sesion(csv_ruta, carpeta)
    ev = eb.evaluar(filas, carpeta, _Siempre("triste"))
    assert ev.coinciden is None                       # no es el backend grabado
    por_etiqueta = {r.etiqueta: r for r in ev.resumenes}
    assert por_etiqueta[2].ok is True and por_etiqueta[1].ok is False
    assert eb.aciertos_crudos(ev)["triste"] == (8, 8)
    assert eb.aciertos_crudos(ev)["feliz"] == (0, 8)
    # Las decisiones del sistema no dependen del modelo: las manos no votan y
    # sin cara vuelve a neutral en la misma fila que en la grabación.
    assert [n["retorno"] for n in ev.filas] == [f["retorno"] for f in filas]
    assert por_etiqueta[8].retorno_s == pytest.approx(2.0)


def test_una_sesion_sin_recortes_da_un_error_claro(tmp_path):
    csv_ruta, carpeta = _grabar_sesion(tmp_path, _PorBrillo(), PASOS)
    with pytest.raises(ValueError, match="guardar-recortes"):
        eb.cargar_sesion(csv_ruta, tmp_path / "otra")


def test_el_informe(tmp_path, capsys):
    csv_ruta, carpeta = _grabar_sesion(tmp_path, _PorBrillo(), PASOS)
    eb.imprimir(eb.evaluar(eb.cargar_sesion(csv_ruta, carpeta), carpeta, _PorBrillo()))
    salida = capsys.readouterr().out
    assert "EXACTA" in salida and "coste por cara" in salida


@pytest.mark.skipif(not (MODELO.is_file() and DATASET.is_dir()),
                    reason="hacen falta emotion_model.keras y emotions_dataset/")
def test_el_ancla_con_el_haar_y_el_modelo_propio_de_verdad(tmp_path):
    """Fotos del dataset por el clasificador real y el camino del demo: el
    evaluador, desde los PNG, tiene que dar la misma predicción bit a bit."""
    pytest.importorskip("keras")
    from telmeow_teleop.emotion_classifier import KerasBackend

    backend = KerasBackend(MODELO)
    clasificador = EmotionClassifier(backend, frame_espejado=True)
    # Las dos primeras que hay de cada clase (faltan 400 por clase, H44).
    nombres = [a.name for c in EMOTION_LABELS for a in sorted(DATASET.glob(f"{c}_*.jpg"))[:2]]
    csv_ruta, carpeta = tmp_path / "real.csv", tmp_path / "real_recortes"
    with open(csv_ruta, "w", newline="", encoding="utf-8") as f:
        escritor = csv.writer(f)
        escritor.writerow(_COLUMNAS_CSV)
        for i, nombre in enumerate(nombres):
            frame = cv2.flip(cv2.imread(str(DATASET / nombre)), 1)   # como llega del pipeline
            clasificador.process(frame, timestamp=0.2 * i)
            registrar_inferencia(escritor, 0.2 * i, i, clasificador, frame, 100.0, carpeta)
    filas = eb.cargar_sesion(csv_ruta, carpeta)
    assert sum(f["rostro"] for f in filas) >= 4
    ev = eb.evaluar(filas, carpeta, backend)
    assert ev.coinciden == ev.comparables


class _Relativo(_Siempre):
    """Un backend que se calibra con la cara neutra, como los blendshapes
    relativos: guarda qué caras recibió."""

    relativo = True

    def calibrar(self, rostros):
        self.calibradas = rostros
        return len(rostros)


def test_un_backend_relativo_se_calibra_con_los_primeros_segundos_de_la_0(tmp_path):
    # 60 inferencias de la 0 (12 s) con manos en las 10 primeras, y luego la 1.
    pasos = [(0, 90, i < 10) for i in range(60)] + [(1, 200, False)] * 10
    csv_ruta, carpeta = _grabar_sesion(tmp_path, _PorBrillo(), pasos)
    filas = eb.cargar_sesion(csv_ruta, carpeta)
    backend = _Relativo("neutral")
    ev = eb.evaluar(filas, carpeta, backend, calibracion_s=10.0)
    # 10 s a 5 Hz son 50 inferencias, menos las 10 con las manos sobre la cara.
    assert ev.calibracion == len(backend.calibradas) == 40
    assert all(c.shape == (CAJA[3], CAJA[2], 3) for c in backend.calibradas)
    # Un backend que no es relativo no se calibra.
    assert eb.evaluar(filas, carpeta, _Siempre("neutral")).calibracion is None


def test_los_criterios_son_lo_que_habria_hecho_el_robot(tmp_path, capsys):
    csv_ruta, carpeta = _grabar_sesion(tmp_path, _PorBrillo(), PASOS)
    filas = eb.cargar_sesion(csv_ruta, carpeta)
    ev = eb.evaluar(filas, carpeta, _Siempre("triste"))
    c = eb.criterios(ev)
    por_etiqueta = {r.etiqueta: r for r in ev.resumenes}
    assert c["triste"] == dict(por_etiqueta[2].enviadas)["triste"] > 0.5
    assert c["lectura_neutral"] is None                 # la sesión no tiene etiqueta 5
    assert c["manos_cambios"] == por_etiqueta[7].cambios
    assert c["ok"] == sum(1 for r in ev.resumenes if r.ok)
    eb.imprimir_criterios([ev])
    assert "CRITERIOS DEL P6.4" in capsys.readouterr().out


class _PorBrilloRelativo(_PorBrillo):
    """Decide por el brillo del recorte RESPECTO al de la cara neutra
    calibrada: el ancla solo sale si se calibra con las mismas caras."""

    nombre = "brillo_relativo"
    relativo = True

    def __init__(self):
        self.neutro = None

    @property
    def calibrado(self):
        return self.neutro is not None

    def calibrar(self, rostros):
        self.neutro = float(np.mean([r.mean() for r in rostros]))
        return len(rostros)

    def distribucion(self, rostro_bgr):
        return super().distribucion(rostro_bgr - (self.neutro - 100.0))


def test_el_ancla_con_un_backend_relativo_calibra_con_las_filas_de_la_grabacion(tmp_path):
    pasos = [(0, 90 + i, False) for i in range(20)] + list(PASOS)
    csv_ruta, carpeta = _grabar_sesion(tmp_path, _PorBrilloRelativo(), pasos)
    filas = eb.cargar_sesion(csv_ruta, carpeta)
    calibrando = [f for f in filas if f["calibrando"]]
    assert len(calibrando) == 16                 # 3 s a 5 Hz, desde la primera cara
    ev = eb.evaluar(filas, carpeta, _PorBrilloRelativo())
    assert ev.calibracion == 16
    assert ev.coinciden == ev.comparables == sum(1 for f in filas if f["rostro"]) - 16
    assert [n["emocion"] for n in ev.filas] == [f["emocion"] for f in filas]


def test_el_ancla_con_una_segunda_calibracion_a_mitad_de_sesion(tmp_path):
    """Como el 2026-10-25: el operador volvió a pulsar c a los 12 s. Cada
    calibración se rehace donde ocurrió, con SUS caras; juntarlas daría otra
    neutra, y la predicción no coincidiría con la grabada."""
    pasos = ([(0, 90 + i, False) for i in range(30)]
             + [(0, 140 + i, False) for i in range(20)] + list(PASOS))
    csv_ruta, carpeta = _grabar_sesion(tmp_path, _PorBrilloRelativo(), pasos,
                                       recalibrar_en=(30,))
    filas = eb.cargar_sesion(csv_ruta, carpeta)
    episodios = eb.episodios_de_calibracion(filas)
    assert [len(caras) for caras, _ in episodios] == [16, 16]
    ev = eb.evaluar(filas, carpeta, _PorBrilloRelativo())
    assert ev.calibracion == 16 and "2 calibracion" in ev.origen_calibracion
    assert ev.coinciden == ev.comparables > 30
    assert [n["emocion"] for n in ev.filas] == [f["emocion"] for f in filas]
    # Y el analizador reproduce la grabación, con la ventana vaciada al calibrar.
    assert eb.are.comparar_con_grabado(filas).exacta
