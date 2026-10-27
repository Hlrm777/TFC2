"""Pruebas de las dos medidas de latencia del P7.3, sin cámara ni broker.

- ``mqtt_monitor.latencia_ms``: hora de llegada menos el campo ``t``.
- ``medir_latencia_camara.latencias_de_destellos``: sobre una cámara sintética
  con una latencia CONOCIDA, que es lo único que dice si el detector mide lo que
  dice medir.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))

import medir_latencia_camara as mlc  # noqa: E402
import mqtt_monitor as mm  # noqa: E402


def test_la_latencia_es_la_llegada_menos_t():
    assert mm.latencia_ms({"pan": 1.0, "t": 1_000_000}, 1_000_087.5) == pytest.approx(87.5)


@pytest.mark.parametrize("cuerpo", [{"pan": 1.0}, {"t": "ayer"}, {"t": True}, None, [1, 2]])
def test_sin_t_numerico_no_hay_latencia(cuerpo):
    assert mm.latencia_ms(cuerpo, 1.0) is None


def test_resumen_de_latencias():
    resumen = mm.resumen_latencias([float(v) for v in range(1, 101)])
    assert resumen["n"] == 100
    assert resumen["mediana"] == pytest.approx(50.5)
    assert resumen["p95"] == pytest.approx(95.0)
    assert (resumen["min"], resumen["max"]) == (1.0, 100.0)
    assert mm.resumen_latencias([]) is None


def _camara_sintetica(latencia_s, fps=30.0, duracion=12.0, periodo=1.0, ruido=0.5,
                      negro=40.0, blanco=60.0, desfase=0.0137):
    """Frames leídos a ``fps`` que ven la pantalla como estaba ``latencia_s``
    antes de leerlos."""
    rng = np.random.default_rng(3)
    cambios = [(1.5 + desfase + k * periodo, (k + 1) % 2) for k in range(int((duracion - 2) / periodo))]

    def nivel(t):
        actual = 0
        for hora, n in cambios:
            if hora <= t:
                actual = n
        return actual

    frames = []
    for k in range(int(duracion * fps)):
        t = k / fps
        b = blanco if nivel(t - latencia_s) else negro
        frames.append((t, b + rng.normal(scale=ruido)))
    return frames, cambios


@pytest.mark.parametrize("latencia_ms", [40.0, 100.0, 180.0])
def test_recupera_una_latencia_conocida(latencia_ms):
    """Con frames cada 33 ms, el primer frame que ve el cambio llega entre la
    latencia real y un periodo después: nunca antes."""
    frames, cambios = _camara_sintetica(latencia_ms / 1000.0)
    medidas = mlc.latencias_de_destellos(frames, cambios)
    assert len(medidas) == len(cambios)
    assert min(medidas) >= latencia_ms - 1e-6
    assert max(medidas) <= latencia_ms + 1000.0 / 30.0 + 1e-6


def test_sin_contraste_no_mide_nada():
    frames, cambios = _camara_sintetica(0.1, negro=50.0, blanco=51.0)
    assert mlc.latencias_de_destellos(frames, cambios) == []


def test_un_cambio_sin_frames_alrededor_se_descarta():
    frames, cambios = _camara_sintetica(0.1)
    cambios = cambios + [(100.0, 1)]
    assert len(mlc.latencias_de_destellos(frames, cambios)) == len(cambios) - 1
