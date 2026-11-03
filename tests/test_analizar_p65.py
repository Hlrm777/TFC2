"""Pruebas del análisis del P6.5 (``tools/analizar_p65.py``), sin modelos.

Lo que necesita modelos —extraer los blendshapes de los recortes y pasar el
combinado— se probó de punta a punta con dos sesiones del protocolo v3
reetiquetadas; aquí se fija lo que decide: qué es acierto en cada tramo, la
comparación de las gafas, de dónde sale la cara neutra de cada fila y los
criterios.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np
import pytest

from telmeow_teleop.config import EMOTION_LABELS
from telmeow_teleop.emotion_classifier import COLUMNAS_CSV as COLUMNAS_EMOCION

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))

import analizar_p65 as p  # noqa: E402
from entrenar_blendshapes import predecir  # noqa: E402


def _fila(etiqueta, emocion, prediccion=None, t=0.0, rostro=1, manos=0, manual=None,
          calibrando=0):
    return {"etiqueta": etiqueta, "emocion": emocion, "prediccion": prediccion or emocion,
            "t": t, "rostro": rostro, "manos": manos, "manual": manual,
            "calibrando": calibrando}


def _sesion(aciertos_por_etiqueta: dict[int, float], n=20):
    """Una sesión en que en cada etiqueta el robot acierta esa fracción."""
    filas, t = [], 0.0
    for etiqueta, acierto in sorted(aciertos_por_etiqueta.items()):
        esperada = p.ESPERADAS[etiqueta]
        otra = "neutral" if esperada != "neutral" else "feliz"
        buenas = round(acierto * n)
        for k in range(n):
            filas.append(_fila(etiqueta, esperada if k < buenas else otra, t=t))
            t += 0.2
    return filas


def test_los_recortes_de_una_sesion():
    assert p.recortes_de(Path("p65_g1_emocion.csv")) == Path("p65_g1_recortes")
    assert p.recortes_de(Path("otra.csv")) == Path("otra_recortes")


def test_cada_tramo_junta_sus_dos_repeticiones_y_sin_las_filas_que_calibran():
    filas = [_fila(0, "neutral", calibrando=1)] * 5 + _sesion({1: 1.0, 5: 0.5, 2: 0.0})
    r = p.resumir(filas)
    assert r["triste sin ceño"].robot == pytest.approx(75.0)    # (100 + 50) / 2
    assert r["triste sin ceño"].inferencias == 40
    assert r["triste con ceño"].robot == 0.0
    assert r["neutral"].inferencias == 0                          # solo calibraba
    assert r["enojado"].robot is None


def test_el_crudo_solo_cuenta_lo_que_vota():
    filas = [_fila(3, "neutral", "enojado", manos=1), _fila(3, "neutral", "enojado", manual="feliz"),
             _fila(3, "enojado", "enojado"), _fila(3, "neutral", "neutral", rostro=0)]
    r = p.resumir(filas)["enojado"]
    assert r.crudo == 100.0                                       # una que vota, bien
    assert r.robot == 25.0                                        # el robot, en las cuatro


def test_leyendo_cuenta_los_cambios_por_minuto():
    filas = [_fila(10, e, t=0.5 * k) for k, e in enumerate(["neutral"] * 50 + ["sorprendido"] * 10 + ["neutral"] * 61)]
    assert p.resumir(filas)["lectura"].cambios_por_minuto == pytest.approx(2.0)


@pytest.mark.parametrize("sin_acierto, pesan", [(0.75, True), (0.65, False)])
def test_las_gafas_pesan_si_sin_ellas_sube_mas_de_10_puntos(sin_acierto, pesan):
    etiquetas = (1, 2, 3, 5, 6, 7)
    con = [p.resumir(_sesion({e: 0.6 for e in etiquetas})) for _ in range(2)]
    sin = [p.resumir(_sesion({e: sin_acierto for e in etiquetas})) for _ in range(2)]
    res = p.comparar_gafas(con, sin)
    assert res.diferencia == pytest.approx(100 * (sin_acierto - 0.6))
    assert res.pesan is pesan


def test_la_neutra_de_cada_fila_es_la_de_la_ultima_calibracion_terminada():
    filas = [_fila(0, "neutral")] * 10
    vectores = {i: np.full(3, float(i)) for i in range(10)}
    episodios = [([0, 1, 2], 2), ([6, 7], 7)]
    neutras = p.neutras_por_fila(filas, vectores, episodios)
    assert 0 not in neutras and 1 not in neutras                  # antes de terminar la 1.ª
    assert neutras[2] == pytest.approx(np.full(3, 1.0))
    assert neutras[6] == pytest.approx(np.full(3, 1.0))
    assert neutras[9] == pytest.approx(np.full(3, 6.5))           # la de la recalibración


def test_las_muestras_de_una_sesion_son_relativas_y_con_la_clase_de_su_etiqueta():
    filas = ([_fila(0, "neutral", calibrando=1)] * 3 + [_fila(2, "neutral")] * 2
             + [_fila(3, "enojado", manos=1)] + [_fila(99, "neutral")])
    vectores = {0: np.zeros(2), 1: np.zeros(2), 2: np.zeros(2),
                3: np.array([1.0, 2.0]), 4: np.array([3.0, 4.0]), 5: np.ones(2), 6: np.ones(2)}
    X, y = p.muestras_de_sesion(filas, vectores, [([0, 1, 2], 2)])
    assert X.tolist() == [[1.0, 2.0], [3.0, 4.0]]                 # sin la que calibra, ni manos, ni la 99
    assert [EMOTION_LABELS[k] for k in y] == ["triste", "triste"]  # la 2: triste con ceño


def test_entrenar_da_los_pesos_que_lee_blendshapes_backend():
    rng = np.random.default_rng(0)
    centros = np.eye(5) * 4
    y = np.repeat(np.arange(5), 40)
    X = centros[y] + rng.normal(scale=0.3, size=(200, 5))
    pesos = p.entrenar(X, y, [f"r{k}" for k in range(5)], {"sesiones": ["x"]})
    assert pesos["relativo"] is True and pesos["clases"] == list(EMOTION_LABELS)
    assert set(pesos) >= {"rasgos", "media", "desviacion", "W", "b", "sesiones"}
    modelo = {k: np.asarray(pesos[k]) for k in ("media", "desviacion", "W", "b")}
    assert (predecir(modelo, X) == y).mean() > 0.95


def _tramos(triste_ceno, enojado, cambios=0.0):
    r = {n: p.Tramo(n, 50.0, 50.0, 10, None) for n, _ in p.TRAMOS}
    r["triste con ceño"] = p.Tramo("triste con ceño", triste_ceno, None, 10, None)
    r["enojado"] = p.Tramo("enojado", enojado, None, 10, None)
    r["lectura"] = p.Tramo("lectura", 100.0, None, 10, cambios)
    return r


@pytest.mark.parametrize("cand, vale", [
    (_tramos(60.0, 88.0), True),            # sube la tristeza, el enojo cae 2
    (_tramos(60.0, 80.0), False),           # el enojo cae 10
    (_tramos(20.0, 90.0), False),           # la tristeza no sube
    (_tramos(60.0, 90.0, cambios=3.0), False),   # leyendo, falsas alarmas
])
def test_el_criterio_del_reentrenamiento(cand, vale):
    v = p.criterio_reentreno(_tramos(20.0, 90.0), cand)
    assert v.vale is vale
    assert bool(v.motivos) is not vale


def test_sin_los_tramos_que_decide_no_vale_y_dice_por_que():
    base = {n: p.Tramo(n, None, None, 0, None) for n, _ in p.TRAMOS}
    v = p.criterio_reentreno(base, base)
    assert not v.vale
    assert any("tristeza con ceño" in m for m in v.motivos)


def _csv_emocion(ruta, filas):
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        escritor = csv.DictWriter(f, fieldnames=COLUMNAS_EMOCION, restval="")
        escritor.writeheader()
        for fila in filas:
            escritor.writerow({
                "t": f"{fila['t']:.4f}", "etiqueta": fila["etiqueta"], "backend": "combinado",
                "rostro": fila["rostro"], "prediccion": fila["prediccion"],
                "confianza_pred": "0.9", "emocion": fila["emocion"], "manual": "",
                "brillo": "140", "n_frames": 5, "min_confidence": 0.5,
                "manos": fila["manos"], "calibrando": fila["calibrando"],
            })


def test_la_orden_gafas(tmp_path, capsys):
    etiquetas = (1, 2, 3, 5, 6, 7, 10)
    rutas = {}
    for nombre, acierto in (("g1", 0.5), ("g2", 0.5), ("s1", 0.8), ("s2", 0.8)):
        rutas[nombre] = tmp_path / f"p65_{nombre}_emocion.csv"
        _csv_emocion(rutas[nombre], _sesion({e: acierto for e in etiquetas}))
    assert p.main(["gafas", "--con", str(rutas["g1"]), str(rutas["g2"]),
                   "--sin", str(rutas["s1"]), str(rutas["s2"])]) == 0
    salida = capsys.readouterr().out
    assert "+30.0 puntos" in salida
    assert "las gafas PESAN" in salida
