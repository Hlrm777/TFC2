"""La medición de la cámara de la sesión del P10 (``tools/medir_contraluz.py``).

No abre ninguna cámara: una captura y un tracker falsos. Lo que se fija es lo
que el informe de la sesión lee después: el ruido de la malla por tanda y el CSV.
"""

from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

import numpy as np
import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))

import medir_contraluz as mc  # noqa: E402
import sesion_verificacion as sv  # noqa: E402


def test_el_ruido_de_la_malla_no_confunde_moverse_con_temblar():
    """Una cabeza que gira de forma suave da ~0; la misma con un vaivén de 1° por
    foto, no. Es el índice que hace crecer la zona muerta de la cabeza."""
    tiempos = [0.05 * k for k in range(100)]
    suave = [(0.2 * k, 0.1 * k) for k in range(100)]
    con_ruido = [(0.2 * k + (1.0 if k % 2 else -1.0), 0.1 * k) for k in range(100)]
    assert mc.ruido_de_malla(tiempos, suave) == pytest.approx(0.0, abs=1e-9)
    assert mc.ruido_de_malla(tiempos, con_ruido) > 1.0


def test_sin_cara_no_hay_ruido_que_dar():
    tiempos = [0.05 * k for k in range(40)]
    assert mc.ruido_de_malla(tiempos, [None] * 40) is None
    # Un hueco sin cara no se salta: la segunda diferencia empieza de nuevo.
    crudos = [(0.0, 0.0)] * 20 + [None] + [(30.0, 0.0)] * 19
    assert mc.ruido_de_malla(tiempos, crudos) == pytest.approx(0.0, abs=1e-9)


class _Captura:
    """Entrega fotos grises a 20 por segundo de un reloj falso."""

    def __init__(self, reloj):
        self._reloj = reloj

    def read(self):
        self._reloj[0] += 0.05
        return True, np.full((48, 64, 3), 120, dtype=np.uint8)


class _Cabeza:
    """Un tracker que ve la cara salvo una foto de cada cinco."""

    def __init__(self):
        self.n = 0
        self.raw_angles = None

    def process(self, rgb):
        self.n += 1
        if self.n % 5 == 0:
            return None
        self.raw_angles = (0.1 * self.n, 0.0)
        return (0.0, 0.0)


def test_una_tanda_con_malla_da_el_ruido_y_cuantas_fotos_tenian_cara(monkeypatch):
    reloj = [0.0]
    monkeypatch.setattr(mc.time, "perf_counter", lambda: reloj[0])
    sin = mc.medir(_Captura(reloj), calentar=0.5, segundos=4.0)
    assert sin.fps == pytest.approx(20.0, rel=0.05) and sin.brillo == pytest.approx(120.0)
    assert sin.ruido_malla is None and sin.con_cara is None
    con = mc.medir(_Captura(reloj), calentar=0.5, segundos=4.0, cabeza=_Cabeza())
    assert con.con_cara == pytest.approx(0.8, abs=0.03)
    assert con.ruido_malla is not None and math.isfinite(con.ruido_malla)


def test_el_csv_de_la_medicion_es_el_que_lee_el_informe(tmp_path):
    """Dos caminos: ``medir_contraluz`` lo escribe y ``sesion_verificacion`` lo lee."""
    medidas = {(1, 1): mc.Medida(15.0, 81.0, 145.0, 1.4, 1.00, 1.0),
               (1, 0): mc.Medida(30.0, 48.0, 115.0, 2.0, 1.20, 1.0),
               (2, 1): mc.Medida(15.0, 81.0, 147.0, 1.5, 1.10, 1.0),
               (2, 0): mc.Medida(30.1, 48.0, 116.0, 2.0, None, 0.0)}
    with open(tmp_path / "p10_contraluz.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(mc.COLUMNAS_CSV)
        w.writerows(mc.fila_csv(v, c, m) for (v, c), m in medidas.items())
    leido = sv.leer_contraluz(tmp_path / "p10")
    assert leido[1]["fps"] == pytest.approx(15.0) and leido[0]["fps"] == pytest.approx(30.05)
    assert leido[1]["ruido_malla"] == pytest.approx(1.05)
    assert leido[0]["ruido_malla"] == pytest.approx(1.20)       # la tanda sin cara no cuenta
    assert sv.leer_contraluz(tmp_path / "otra") is None
