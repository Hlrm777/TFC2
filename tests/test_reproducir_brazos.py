"""Pruebas de ``tools/reproducir_brazos.py``: la reproducción offline de una
grabación por el ``ArmTracker`` real.

Existe para que las cifras con que se eligieron los parámetros del P5.6 se
puedan volver a sacar —la primera reproducción vivía en la carpeta temporal y se
perdió—, así que las pruebas fuertes son dos:

1. Reproducida con sus propios parámetros, una grabación del P5.6 da los
   comandos que se publicaron. Eso prueba la reconstrucción.
2. Las grabaciones del 2026-09-27 dan las cifras que cita el plan maestro
   (P5.6): las tablas del brazo quieto y de la punta del robot.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

from telmeow_teleop.arm_tracker import EJE_SUPERIOR_RESERVA, torso_basis, vector_brazo
from telmeow_teleop.limits import Side

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))

import reproducir_brazos as rb  # noqa: E402

VERIFICACION = RAIZ / "verificacion_2026-10-11.csv"
VALIDACION = RAIZ / "validacion_2026-09-27.csv"
INTENTO = RAIZ / "validacion_2026-09-27_intento.csv"


@pytest.mark.parametrize("semilla", range(20))
def test_a_mundo_deshace_vector_brazo(semilla):
    rng = np.random.default_rng(semilla)
    hombro = rng.normal(size=3)
    contrario = hombro + np.array([0.3, 0.02, 0.05]) * rng.choice([-1, 1])
    base = torso_basis(hombro, contrario, EJE_SUPERIOR_RESERVA)
    punto = hombro + rng.normal(scale=0.3, size=3)
    inclinacion = rng.uniform(-15, 15)
    vector = vector_brazo(hombro, punto, base, inclinacion)
    assert hombro + rb.a_mundo(vector, base, inclinacion) == pytest.approx(punto, abs=1e-12)


@pytest.mark.skipif(not VERIFICACION.exists(), reason="falta la grabación del 2026-10-11")
def test_reproduce_los_comandos_que_se_publicaron():
    """La verificación se grabó con el P5.6, sin el saludo: reproducida con esos
    parámetros, los comandos coinciden. Lo que difiere son los ~3 % de frames
    en que la grabación usó las caderas (y lo que arrastran), que el CSV no
    permite reconstruir."""
    filas = rb.leer(VERIFICACION)
    salida = rb.reproducir(filas, saludo=False)
    for canal, r in rb.comparar(filas, salida).items():
        assert r["mediana"] < 0.05, canal
        assert r["p95"] < 1.0, canal
    assert salida["incompletos"] == 0


def _resumen(ruta, **kwargs):
    return rb.resumen(rb.reproducir(rb.leer(ruta), **kwargs))


@pytest.mark.skipif(not VALIDACION.exists(), reason="falta la grabación del 2026-09-27")
def test_saca_las_cifras_del_p5_6():
    """Las tablas del P5.6 en el plan maestro: brazo quieto en reposo 94/94/71/100 %,
    la punta del robot a 0.4/23° de la cruz con las manos junto a la cara y a
    61.7/55° con los brazos estirados. Sin el saludo, que es posterior."""
    r = _resumen(VALIDACION, saludo=False)
    quietos = [r[(e, Side.parse(s))]["en_reposo"] for e, s in (("0", "L"), ("0", "R"), ("2", "L"), ("3", "R"))]
    assert quietos == pytest.approx([94, 94, 71, 100], abs=1.0)
    assert r[("1", Side.L)]["desde_la_cruz"] == pytest.approx(0.4, abs=0.5)
    assert r[("1", Side.R)]["desde_la_cruz"] == pytest.approx(23.0, abs=0.5)
    assert r[("4", Side.L)]["desde_la_cruz"] == pytest.approx(61.7, abs=0.5)
    assert r[("4", Side.R)]["desde_la_cruz"] == pytest.approx(55.0, abs=0.5)


@pytest.mark.skipif(not VALIDACION.exists(), reason="falta la grabación del 2026-09-27")
def test_con_la_focal_vieja_la_etiqueta_1_se_queda_lejos_de_la_cruz():
    """La otra mitad del H41: con la focal del head tracker (y el alcance de
    entonces) las manos junto a la cara dejaban el robot a 27/29° de la cruz."""
    r = _resumen(VALIDACION, saludo=False, focal_relativa=1.0, alcance_humano=90.0)
    assert r[("1", Side.L)]["desde_la_cruz"] == pytest.approx(27.4, abs=1.0)
    assert r[("1", Side.R)]["desde_la_cruz"] == pytest.approx(29.0, abs=1.0)


@pytest.mark.skipif(not INTENTO.exists(), reason="falta el intento del 2026-09-27")
def test_saca_las_cifras_del_intento():
    r = _resumen(INTENTO, saludo=False)
    quietos = [r[(e, Side.parse(s))]["en_reposo"] for e, s in (("0", "L"), ("0", "R"), ("2", "R"))]
    assert quietos == pytest.approx([100, 100, 99], abs=1.0)
    assert r[("1", Side.L)]["desde_la_cruz"] == pytest.approx(10.3, abs=0.5)
    assert r[("1", Side.R)]["desde_la_cruz"] == pytest.approx(15.9, abs=0.5)


@pytest.mark.skipif(not VERIFICACION.exists(), reason="falta la grabación del 2026-10-11")
def test_la_linea_de_comandos(capsys):
    assert rb.main([str(VERIFICACION), "--sin-saludo", "--comparar", "--focal", "0.7"]) == 0
    texto = capsys.readouterr().out
    assert "Comandos reproducidos frente a los grabados" in texto
    assert "caderas" in texto           # el aviso: esa grabación las usó a ratos
    assert all(ord(c) < 128 for c in texto)


def test_una_grabacion_demasiado_vieja_se_rechaza(tmp_path):
    vieja = tmp_path / "vieja.csv"
    vieja.write_text("t,etiqueta,armL\n0.0,0,-55\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        rb.leer(vieja)


def test_sin_ancla_se_usa_la_separacion_por_defecto():
    assert math.isclose(rb.ANCHO_HOMBROS_POR_DEFECTO, 0.315)
