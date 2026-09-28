"""Pruebas de la confianza en la mano y del codo en el borde (P5.6, H40).

La grabación de validación del 2026-09-27 enseñó que la visibilidad de
BlazePose no basta para fiarse de una mano. Con el brazo colgando, la mano
queda por debajo del cuadro y BlazePose la inventa: salta de un frame al
siguiente entre «colgando, fuera de la imagen» y «doblada hacia arriba, delante
del pecho», con visibilidades de 0.7-0.96 en las dos. Seguirla era el saltito
del brazo quieto, ahora más grande que el original: con el brazo derecho
saludando, el izquierdo colgando estaba en reposo solo el 16 % del tiempo.

La escena de estas pruebas es la de la grabación: el codo del brazo colgando
justo en el borde inferior, la mano de verdad por debajo del cuadro, y la
inventada doblada por delante del pecho, dentro de la imagen.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

from telmeow_teleop.arm_tracker import LANDMARKS, MARGEN_BORDE, PUNTOS_MANO, ArmTracker
from telmeow_teleop.escorzo import FOCAL_RELATIVA, Camara
from telmeow_teleop.limits import Side
from test_arm_tracker import LandmarkFalso

ANCHO, ALTO = 640, 480
FRAME = np.zeros((ALTO, ANCHO, 3), dtype=np.uint8)
CAMARA = Camara.desde_imagen(ANCHO, ALTO, FOCAL_RELATIVA)
PASO = 1.0 / 20.0

# El hombro izquierdo del operador —etiqueta L de BlazePose, sin espejo— a 1.1 m.
HOMBRO_I = CAMARA.rayo(190, 356) * 1.1
HOMBRO_D = HOMBRO_I + np.array([0.33, 0.0, 0.0])
CADERAS = (HOMBRO_I + HOMBRO_D) / 2 + np.array([0.0, 0.5, 0.0])
# El brazo colgando: el codo justo en el borde inferior, la mano bajo el cuadro.
CODO = HOMBRO_I + np.array([0.0, 0.28, 0.0])
MANO_COLGANDO = CODO + np.array([0.0, 0.26, 0.0])
# La mano que inventa BlazePose: doblada hacia arriba, delante del pecho.
MANO_DOBLADA = CODO + np.array([0.03, -0.20, -0.15])
# El codo que da BlazePose cuando se equivoca: girado 30° hacia la cámara.
CODO_ADELANTE = HOMBRO_I + 0.28 * np.array(
    [0.0, math.cos(math.radians(30)), -math.sin(math.radians(30))]
)


def escena(codo, mano, vis_mano=0.9, vis_codo=0.9, codo_mundo=None):
    """``(mundo, imagen)`` de BlazePose para una escena VERDADERA en el marco de
    la cámara. ``codo_mundo`` es donde pone BlazePose el codo si se equivoca;
    los píxeles salen siempre de la escena verdadera."""
    mundo = [LandmarkFalso(visibility=0.0) for _ in range(33)]
    imagen = [LandmarkFalso(visibility=0.0) for _ in range(33)]

    def poner(lado, nombres, real, en_mundo, vis):
        u, v = CAMARA.proyectar(real)
        for nombre in nombres:
            indice = LANDMARKS[lado][nombre]
            mundo[indice] = LandmarkFalso(*(en_mundo - CADERAS), visibility=vis)
            imagen[indice] = LandmarkFalso(u / ANCHO, v / ALTO, 0.0, vis)

    poner(Side.L, ("hombro",), HOMBRO_I, HOMBRO_I, 1.0)
    poner(Side.R, ("hombro",), HOMBRO_D, HOMBRO_D, 1.0)
    for lado, dx in ((Side.L, -0.12), (Side.R, 0.12)):
        cadera = CADERAS + np.array([dx, 0.0, 0.0])
        poner(lado, ("cadera",), cadera, cadera, 0.0)
    poner(Side.L, ("codo",), codo, codo if codo_mundo is None else codo_mundo, vis_codo)
    poner(Side.L, PUNTOS_MANO, mano, mano, vis_mano)
    return mundo, imagen


def tracker_con_escenas(**kwargs):
    """Tracker que en cada frame ve ``estado["escena"]`` (``None`` = sin pose)."""
    reloj = {"t": 0.0}
    estado = {"escena": None}

    def procesar(_frame):
        if estado["escena"] is None:
            return SimpleNamespace(pose_world_landmarks=None, pose_landmarks=None)
        mundo, imagen = estado["escena"]
        return SimpleNamespace(
            pose_world_landmarks=SimpleNamespace(landmark=mundo),
            pose_landmarks=SimpleNamespace(landmark=imagen),
        )

    tracker = ArmTracker(
        pose_estimator=SimpleNamespace(process=procesar, close=lambda: None),
        frame_espejado=False,
        clock=lambda: reloj["t"],
        **kwargs,
    )
    return tracker, reloj, estado


def paso(tracker, reloj, estado, t, que_se_ve):
    reloj["t"] = t
    estado["escena"] = que_se_ve
    return tracker.comando(tracker.process(FRAME), t)


def test_la_escena_es_la_de_la_grabacion():
    borde = MARGEN_BORDE * ALTO
    assert ALTO - borde < CAMARA.proyectar(CODO)[1] < ALTO           # codo en el borde
    assert CAMARA.proyectar(MANO_COLGANDO)[1] > ALTO                 # mano bajo el cuadro
    u, v = CAMARA.proyectar(MANO_DOBLADA)
    assert borde < u < ANCHO - borde and borde < v < ALTO - borde    # inventada, dentro


# --------------------------------------------------------------------------
# 1. La rampa
# --------------------------------------------------------------------------


def test_la_confianza_sube_a_lo_largo_de_la_rampa():
    tracker, reloj, estado = tracker_con_escenas(zona_reposo=0.0)
    vista = escena(CODO, MANO_DOBLADA)
    for t, esperada in ((0.0, 0.0), (0.25, 0.5), (0.5, 1.0), (1.0, 1.0)):
        paso(tracker, reloj, estado, t, vista)
        assert tracker.confianzas_mano[Side.L] == pytest.approx(esperada)
    assert tracker.pesos_mano[Side.L] == pytest.approx(1.0)


def test_con_rampa_cero_la_mano_cuenta_en_cuanto_entra():
    tracker, reloj, estado = tracker_con_escenas(zona_reposo=0.0, rampa_mano=0.0)
    paso(tracker, reloj, estado, 0.0, escena(CODO, MANO_DOBLADA))
    assert tracker.confianzas_mano[Side.L] == 1.0
    assert tracker.pesos_mano[Side.L] == pytest.approx(1.0)


def test_la_mano_fuera_de_la_imagen_no_cuenta_aunque_blazepose_la_vea():
    """Visibilidad 0.9 y fuera del cuadro: es una suposición, no una medida. El
    brazo lo lleva el codo."""
    tracker, reloj, estado = tracker_con_escenas(zona_reposo=0.0)
    colgando = escena(CODO, MANO_COLGANDO, vis_mano=0.9)
    for i in range(30):
        paso(tracker, reloj, estado, i * PASO, colgando)
        assert tracker.confianzas_mano[Side.L] == 0.0
        assert tracker.pesos_mano[Side.L] == 0.0


def test_sin_pose_la_confianza_vuelve_a_empezar():
    tracker, reloj, estado = tracker_con_escenas(zona_reposo=0.0)
    vista = escena(CODO, MANO_DOBLADA)
    for i in range(21):
        paso(tracker, reloj, estado, i * PASO, vista)
    assert tracker.confianzas_mano[Side.L] == pytest.approx(1.0)
    paso(tracker, reloj, estado, 1.05, None)
    paso(tracker, reloj, estado, 1.10, vista)
    assert tracker.confianzas_mano[Side.L] == pytest.approx(0.0)


def test_sin_imagen_la_confianza_no_interviene():
    """Pruebas y reproducciones desde un CSV: no hay cuadro que mirar."""
    mundo, _ = escena(CODO, MANO_DOBLADA)
    resultado = SimpleNamespace(
        pose_world_landmarks=SimpleNamespace(landmark=mundo), pose_landmarks=None
    )
    tracker = ArmTracker(
        pose_estimator=SimpleNamespace(process=lambda _f: resultado, close=lambda: None),
        frame_espejado=False,
        clock=lambda: 0.0,
    )
    tracker.process(None)
    assert tracker.confianzas_mano[Side.L] == 1.0


# --------------------------------------------------------------------------
# 2. La regresión: la mano inventada y el brazo quieto
# --------------------------------------------------------------------------


def test_la_mano_inventada_no_mueve_el_brazo_quieto():
    """La etiqueta 2 de la grabación: el brazo cuelga, y BlazePose alterna cada
    frame entre la mano verdadera, bajo el cuadro, y una doblada delante del
    pecho, las dos con visibilidad 0.9. El robot no se mueve del reposo."""
    tracker, reloj, estado = tracker_con_escenas()
    reposo = tracker.reposo[Side.L]
    colgando = escena(CODO, MANO_COLGANDO)
    inventada = escena(CODO, MANO_DOBLADA)
    for i in range(60):
        mando = paso(tracker, reloj, estado, i * PASO, inventada if i % 2 else colgando)
        assert (mando.armL, mando.armLtw) == pytest.approx(reposo, abs=1e-6)
        assert tracker.pesos_mano[Side.L] < 0.05


def test_la_misma_mano_sostenida_si_mueve_el_brazo():
    """La rampa no apaga las manos: la misma mano doblada, sostenida en cuadro,
    cuenta al cabo de medio segundo y el robot la sigue. Lo que se descarta es
    la que aparece y desaparece."""
    tracker, reloj, estado = tracker_con_escenas()
    reposo = np.array(tracker.reposo[Side.L])
    doblada = escena(CODO, MANO_DOBLADA)
    for i in range(31):
        mando = paso(tracker, reloj, estado, i * PASO, doblada)
    assert tracker.pesos_mano[Side.L] == pytest.approx(1.0)
    assert np.hypot(*(np.array([mando.armL, mando.armLtw]) - reposo)) > 10.0


# --------------------------------------------------------------------------
# 3. El codo en el borde
# --------------------------------------------------------------------------


def test_sin_mano_el_codo_del_borde_no_se_corrige():
    """El codo en el borde es una extrapolación: con el brazo colgando, el
    escorzo lo hacía saltar entre sus dos soluciones. Se queda el de BlazePose."""
    tracker, reloj, estado = tracker_con_escenas(zona_reposo=0.0)
    paso(tracker, reloj, estado, 0.0, escena(CODO, MANO_COLGANDO, codo_mundo=CODO_ADELANTE))
    assert tracker.vectores_codo[Side.L] == pytest.approx(tracker.vectores_codo_bp[Side.L])
    assert tracker.vectores_codo_bp[Side.L][2] > 0.1                  # BlazePose: delante


def test_con_la_mano_a_la_vista_el_codo_del_borde_si_se_corrige():
    """Con la mano a la vista, el codo del borde es un punto bien visto donde se
    juntan brazo y antebrazo —las manos junto a la cara con los codos abajo—, y
    el escorzo lo devuelve al plano."""
    tracker, reloj, estado = tracker_con_escenas(zona_reposo=0.0, rampa_mano=0.0)
    paso(tracker, reloj, estado, 0.0, escena(CODO, MANO_DOBLADA, codo_mundo=CODO_ADELANTE))
    assert tracker.vectores_codo_bp[Side.L][2] > 0.1
    assert abs(tracker.vectores_codo[Side.L][2]) < 0.02


# --------------------------------------------------------------------------
# 4. El ancla de los hombros, para el CSV
# --------------------------------------------------------------------------


@pytest.mark.parametrize("frame_espejado, signo", [(False, 1.0), (True, -1.0)])
def test_el_ancla_de_los_hombros_va_por_canal(frame_espejado, signo):
    mundo, _ = escena(CODO, MANO_DOBLADA)
    girado = list(mundo)
    hombro_l = mundo[LANDMARKS[Side.L]["hombro"]]
    girado[LANDMARKS[Side.L]["hombro"]] = LandmarkFalso(
        hombro_l.x, hombro_l.y, hombro_l.z + 0.05, visibility=1.0
    )
    tracker = ArmTracker(
        pose_estimator=SimpleNamespace(process=lambda _f: None, close=lambda: None),
        frame_espejado=frame_espejado,
        clock=lambda: 0.0,
    )
    ancho, dz = tracker._medir_hombros(girado)
    assert ancho == pytest.approx(0.33)
    # El canal L es el hombro de la etiqueta L sin espejo, y el de la R con él.
    assert dz == pytest.approx(signo * 0.05)
