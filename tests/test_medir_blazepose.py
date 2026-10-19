"""Pruebas de ``tools/medir_blazepose.py`` sin cámara ni MediaPipe: un BlazePose
falso con un coste conocido y un reloj falso."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import medir_blazepose as mb  # noqa: E402


class _RelojFalso:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _pose_falsa(reloj, ms_por_complejidad, con_persona=True):
    """Un BlazePose que «tarda» lo indicado en cada process()."""
    def crear(complejidad):
        landmark = [SimpleNamespace(visibility=0.9) for _ in range(33)]
        def procesar(_frame):
            reloj.t += ms_por_complejidad[complejidad] / 1000.0
            return SimpleNamespace(
                pose_landmarks=SimpleNamespace(landmark=landmark) if con_persona else None
            )
        return SimpleNamespace(process=procesar, close=lambda: None)
    return crear


def test_mide_el_coste_de_cada_complejidad():
    reloj = _RelojFalso()
    crear = _pose_falsa(reloj, {0: 4.0, 1: 8.0, 2: 20.0})
    frames = [None] * 30
    resultados = [mb.medir(frames, c, repeticiones=2, crear=crear, reloj=reloj) for c in (0, 1, 2)]
    assert [r["ms_mediana"] for r in resultados] == pytest.approx([4.0, 8.0, 20.0])
    assert all(r["frames"] == 60 for r in resultados)          # el calentamiento no cuenta
    assert all(r["con_persona"] == 100.0 for r in resultados)
    assert resultados[0]["vis_hombros"] == pytest.approx(0.9)


def test_avisa_si_no_hay_persona():
    reloj = _RelojFalso()
    crear = _pose_falsa(reloj, {1: 5.0}, con_persona=False)
    r = mb.medir([None] * 20, 1, crear=crear, reloj=reloj)
    assert r["con_persona"] == 0.0
    assert r["vis_munecas"] != r["vis_munecas"]               # NaN: no hubo nadie


def test_el_percentil():
    assert mb._percentil([1.0, 2.0, 3.0, 4.0, 5.0], 50) == pytest.approx(3.0)
    assert mb._percentil([1.0, 2.0, 3.0, 4.0, 5.0], 100) == pytest.approx(5.0)
