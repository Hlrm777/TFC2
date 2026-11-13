"""P10.2 (H72): la compresión del cabeceo hacia abajo.

Con la ganancia de 1.80 el robot tocaba su tope (−39°) a los 21° del operador, y
al narrar el operador baja hasta 37° para leer el pie de la página: el robot se
quedaba parado. Desde el 2026-11-13 la ganancia es la de siempre hasta 8° y de
ahí decae para que el tope llegue a los 37° (``comprimir_tilt_abajo``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from telmeow_teleop.config import AppConfig
from telmeow_teleop.head_tracker import (
    COLUMNAS_CSV,
    HeadTracker,
    comprimir_tilt_abajo,
    fila_csv,
)
from telmeow_teleop.limits import HEAD_TILT_RANGE

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))

TOPE = -HEAD_TILT_RANGE[0]
G, RECTO, HASTA = 1.80, 8.0, 37.0


def curva(abajo, ganancia=G, recto=RECTO, hasta=HASTA):
    return comprimir_tilt_abajo(abajo, ganancia, TOPE, recto, hasta)


@pytest.mark.parametrize("abajo", [0.0, 0.5, 2.0, 5.0, 8.0])
def test_cerca_del_frente_es_la_ganancia_de_siempre(abajo):
    assert curva(abajo) == pytest.approx(G * abajo, abs=1e-12)


def test_el_tope_llega_justo_donde_se_pide():
    assert curva(HASTA) == pytest.approx(TOPE, abs=1e-6)
    assert curva(HASTA - 1.0) < TOPE < curva(HASTA + 1.0)


def test_es_continua_y_sin_codo_al_empezar_a_comprimir():
    """El valor y la pendiente coinciden a los dos lados de ``recto``."""
    e = 1e-4
    assert curva(RECTO + e) == pytest.approx(curva(RECTO), abs=1e-3)
    pendiente_antes = (curva(RECTO) - curva(RECTO - e)) / e
    pendiente_despues = (curva(RECTO + e) - curva(RECTO)) / e
    assert pendiente_antes == pytest.approx(G, abs=1e-6)
    assert pendiente_despues == pytest.approx(G, abs=1e-3)


def test_es_monotona_y_la_pendiente_solo_baja():
    """El robot nunca se mueve al revés que el operador, ni se para antes del tope."""
    valores = [curva(0.25 * k) for k in range(0, 200)]
    pasos = [b - a for a, b in zip(valores, valores[1:])]
    assert all(p > 0.0 for p in pasos)
    assert all(b <= a + 1e-9 for a, b in zip(pasos, pasos[1:]))


def test_nunca_baja_mas_que_el_mapeo_lineal():
    assert all(curva(x) <= G * x + 1e-9 for x in (1, 8, 9, 15, 25, 37, 50))


@pytest.mark.parametrize("ganancia, recto, hasta", [
    (1.80, 8.0, 0.0),      # apagada
    (1.00, 8.0, 37.0),     # el tope ya queda más allá de 37° con la ganancia sola
    (1.80, 25.0, 37.0),    # el tramo recto ya llega al tope: lo corta el recorte
    (1.80, 40.0, 37.0),    # recto más allá de hasta: no hay nada que comprimir
])
def test_sin_nada_que_comprimir_es_lineal(ganancia, recto, hasta):
    for x in (1.0, 10.0, 20.0, 30.0, 45.0):
        assert curva(x, ganancia, recto, hasta) == pytest.approx(ganancia * x)


def _tracker(**kw):
    class Reloj:
        t = 0.0

        def __call__(self):
            return self.t

    kw.setdefault("min_cutoff", 1000.0)
    kw.setdefault("beta", 0.0)
    return HeadTracker(clock=Reloj(), **kw)


def test_el_tracker_ya_no_se_para_a_los_21_grados():
    """Lo que pasaba en la sesión del 2026-11-12: de 21 a 37° del operador el
    robot estaba clavado en −39. Con la compresión sigue bajando hasta los 37."""
    lineal = _tracker(gain_tilt_down=G)
    comprimido = _tracker(gain_tilt_down=G, tilt_down_recto=RECTO, tilt_down_hasta=HASTA)
    try:
        for k, abajo in enumerate((22.0, 28.0, 34.0)):
            assert lineal.map_to_robot(0.0, -abajo, timestamp=float(k))[1] == pytest.approx(-TOPE)
            assert lineal.last_clamp.clamped
        salidas = []
        for k, abajo in enumerate((5.0, 22.0, 28.0, 34.0, 36.9)):
            salidas.append(comprimido.map_to_robot(0.0, -abajo, timestamp=float(k))[1])
            assert not comprimido.last_clamp.clamped
        assert salidas[0] == pytest.approx(-G * 5.0, abs=0.05)         # cerca del frente, igual
        assert all(b < a - 0.2 for a, b in zip(salidas, salidas[1:]))   # sigue bajando
        assert comprimido.map_to_robot(0.0, -45.0, timestamp=9.0)[1] == pytest.approx(-TOPE)
    finally:
        lineal.close()
        comprimido.close()


def test_hacia_arriba_no_cambia_nada():
    a = _tracker(gain_tilt_up=1.59, gain_tilt_down=G)
    b = _tracker(gain_tilt_up=1.59, gain_tilt_down=G, tilt_down_recto=RECTO, tilt_down_hasta=HASTA)
    try:
        for k, arriba in enumerate((1.0, 10.0, 20.0)):
            assert (a.map_to_robot(3.0, arriba, timestamp=float(k))
                    == b.map_to_robot(3.0, arriba, timestamp=float(k)))
    finally:
        a.close()
        b.close()


def test_con_el_tilt_invertido_la_compresion_va_con_el_operador():
    """La curva se elige por lo que hace el OPERADOR, como la ganancia."""
    t = _tracker(gain_tilt_down=G, tilt_down_recto=RECTO, tilt_down_hasta=HASTA, invert_tilt=True)
    try:
        assert t.map_to_robot(0.0, -20.0, timestamp=0.0)[1] == pytest.approx(curva(20.0), abs=0.05)
    finally:
        t.close()


def test_los_parametros_negativos_se_rechazan():
    with pytest.raises(ValueError):
        HeadTracker(tilt_down_hasta=-1.0)


def test_la_configuracion_y_el_csv_llevan_la_compresion():
    cfg = AppConfig.from_env({})
    assert (cfg.head.tilt_down_recto, cfg.head.tilt_down_hasta) == (8.0, 37.0)
    apagada = AppConfig.from_env({"TELMEOW_HEAD_TILT_DOWN_HASTA": "0"})
    assert apagada.head.tilt_down_hasta == 0.0
    t = HeadTracker.desde_config(cfg)
    try:
        assert (t.tilt_down_recto, t.tilt_down_hasta) == (8.0, 37.0)
        assert COLUMNAS_CSV[-2:] == ("tilt_down_recto", "tilt_down_hasta")
        fila = dict(zip(COLUMNAS_CSV, fila_csv(t, 0.0, 0, (0.0, 0.0), 640, 480)))
        assert (fila["tilt_down_recto"], fila["tilt_down_hasta"]) == ("8", "37")
    finally:
        t.close()


# --------------------------------------------------------------------------
# La simulación con que se decidió
# --------------------------------------------------------------------------


def test_los_segundos_en_el_tope_no_cuentan_los_huecos_sin_cara():
    import simular_compresion_tilt as sct

    tope = HEAD_TILT_RANGE[0]
    tiempos = [0.0, 0.05, 0.10, 2.10, 2.15]
    assert sct.segundos_en_tope(tiempos, [tope, tope, tope, tope, 0.0]) == pytest.approx(0.15)
    assert sct.segundos_en_tope(tiempos, [0.0] * 5) == 0.0


def test_el_criterio_de_la_simulacion():
    import simular_compresion_tilt as sct

    # 2 s bajando hasta 35° a 20 filas por segundo, y vuelta.
    bajada = [(0.05 * k, -35.0 * k / 40.0) for k in range(41)]
    filas = bajada + [(2.05 + 0.05 * k, -35.0 + 35.0 * k / 40.0) for k in range(41)]
    r = sct.simular(filas, 1.59, G, RECTO, HASTA)
    assert r.tope_lineal_s > 1.0 and r.tope_comprimido_s == 0.0
    assert r.max_diferencia_recta == 0.0 and r.cumple
    apagada = sct.simular(filas, 1.59, G, RECTO, 0.0)
    assert apagada.tope_comprimido_s == apagada.tope_lineal_s and not apagada.cumple


def test_la_compresion_cumple_sobre_la_sesion_grabada():
    """La cifra del plan (2026-11-13): en la narración del 2026-11-12 el robot
    pasa de 15.8 s en el tope (sin filtro) a ninguno."""
    import simular_compresion_tilt as sct

    ruta = RAIZ / "p10_narracion_cabeza.csv"
    if not ruta.exists():
        pytest.skip("falta p10_narracion_cabeza.csv")
    h = AppConfig.from_env({}).head
    r = sct.simular(sct.leer(ruta), h.gain_tilt_up, h.gain_tilt_down,
                    h.tilt_down_recto, h.tilt_down_hasta)
    assert r.tope_lineal_s > 10.0
    assert r.cumple, r
