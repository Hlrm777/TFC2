"""Pruebas del híbrido, la zona de reposo y el escorzo dentro del tracker (P5.5).

Cada grupo fija una petición del operador del 2026-09-27, tal como la hizo:

1. «El híbrido, porque será lo más robusto cuando no se vean mis manos pero sí
   los codos»: la mano manda cuando se ve y el codo es el respaldo.
2. «Al mover solo un brazo, el otro también se activa aunque poquito»: con el
   brazo colgando, el robot se queda EXACTAMENTE en reposo, se mida o no.
3. «Muevo mis brazos para atrás y no vuelve para atrás»: la mano que BlazePose
   ponía 35 cm por delante vuelve al plano con el escorzo (H39).
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

from telmeow_teleop.arm_tracker import (
    LANDMARKS,
    PUNTOS_MANO,
    TARGETS,
    ArmTracker,
)
from telmeow_teleop.config import AppConfig
from telmeow_teleop.escorzo import Camara
from telmeow_teleop.limits import Side
from test_arm_tracker import HOMBRO_IZQ, LandmarkFalso, mundo_falso

ADELANTE = np.array([0.0, 0.0, -1.0])
ABAJO = np.array([0.0, 1.0, 0.0])


class _SinPose:
    def process(self, _frame):
        return None

    def close(self):
        pass


def _tracker(**kwargs):
    reloj = {"t": 0.0}
    tracker = ArmTracker(pose_estimator=_SinPose(), clock=lambda: reloj["t"], **kwargs)
    return tracker, reloj


def _mundo(codo, mano, vis_mano=1.0, vis_codo=1.0):
    """Brazo izquierdo de BlazePose con codo y mano; dedos sobre la muñeca."""
    mundo = mundo_falso(0.9)
    mundo[LANDMARKS[Side.L]["codo"]] = LandmarkFalso(*codo, visibility=vis_codo)
    for nombre in PUNTOS_MANO:
        mundo[LANDMARKS[Side.L][nombre]] = LandmarkFalso(*mano, visibility=vis_mano)
    return mundo


def _con_mundo(mundo, imagen=None, **kwargs):
    resultado = SimpleNamespace(
        pose_world_landmarks=SimpleNamespace(landmark=mundo),
        pose_landmarks=None if imagen is None else SimpleNamespace(landmark=imagen),
    )
    estimador = SimpleNamespace(process=lambda _frame: resultado, close=lambda: None)
    return ArmTracker(
        pose_estimator=estimador, frame_espejado=False, clock=lambda: 0.0, **kwargs
    )


# Brazo doblado: codo abajo y afuera, mano delante del pecho. Aquí la mano y el
# codo apuntan a sitios muy distintos, que es donde el destino importa.
CODO = HOMBRO_IZQ + np.array([0.12, 0.26, -0.08])
MANO = CODO + np.array([-0.05, -0.05, -0.25])


# --------------------------------------------------------------------------
# 1. El híbrido
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "vis_mano, vis_codo, esperado",
    [
        (0.95, 0.95, 1.0),     # mano a la vista: manda entera
        (0.95, 0.10, 1.0),     # aunque el codo no se vea
        (0.10, 0.95, 0.0),     # mano fuera de cuadro: manda el codo
        (0.60, 0.10, 1.0),     # no hay codo de respaldo: la mano, aunque floja
    ],
)
def test_el_peso_de_la_mano(vis_mano, vis_codo, esperado):
    tracker, _ = _tracker()
    assert tracker._peso_mano(vis_mano, vis_codo) == pytest.approx(esperado)


def test_sin_mano_ni_codo_el_brazo_no_se_ve():
    tracker, _ = _tracker()
    assert tracker._peso_mano(0.2, 0.2) is None


def test_entre_medias_el_peso_es_suave():
    tracker, _ = _tracker()
    pesos = [tracker._peso_mano(v, 0.95) for v in np.linspace(0.3, 0.7, 41)]
    assert pesos[0] == pytest.approx(0.0) and pesos[-1] == pytest.approx(1.0)
    assert all(b >= a for a, b in zip(pesos, pesos[1:]))
    assert max(b - a for a, b in zip(pesos, pesos[1:])) < 0.1


@pytest.mark.parametrize("target, vis_mano, vis_codo, esperado", [
    ("mano", 0.95, 0.10, 1.0),
    ("mano", 0.10, 0.95, None),
    ("codo", 0.10, 0.95, 0.0),
    ("codo", 0.95, 0.10, None),
])
def test_mano_y_codo_puros_siguen_como_antes(target, vis_mano, vis_codo, esperado):
    tracker, _ = _tracker(target=target)
    assert tracker._peso_mano(vis_mano, vis_codo) == esperado


def test_con_la_mano_a_la_vista_el_hibrido_es_la_mano():
    salidas = {}
    for target in ("hibrido", "mano"):
        tracker = _con_mundo(_mundo(CODO, MANO), target=target, zona_reposo=0.0)
        pose = tracker.process(None)
        salidas[target] = (pose.armL, pose.armLtw)
    assert salidas["hibrido"] == pytest.approx(salidas["mano"])


def test_con_la_mano_fuera_de_cuadro_el_hibrido_es_el_codo():
    """El caso para el que existe: la mano sale de cuadro y el codo sigue a
    la vista. Con 'mano' el brazo se perdería; con el híbrido lo lleva el codo."""
    mundo = _mundo(CODO, MANO, vis_mano=0.1)
    solo_mano = _con_mundo(mundo, target="mano", zona_reposo=0.0).process(None)
    assert solo_mano.armL is None
    hibrido = _con_mundo(mundo, target="hibrido", zona_reposo=0.0)
    codo = _con_mundo(mundo, target="codo", zona_reposo=0.0)
    pose_h, pose_c = hibrido.process(None), codo.process(None)
    assert (pose_h.armL, pose_h.armLtw) == pytest.approx((pose_c.armL, pose_c.armLtw))
    assert hibrido.pesos_mano[Side.L] == 0.0


def test_la_mano_que_parpadea_no_hace_saltar_el_brazo():
    """La visibilidad de la mano en el borde del cuadro va y viene. El destino
    pasa de la mano al codo sin saltos a medida que baja."""
    anterior = None
    for vis in np.linspace(0.8, 0.2, 61):
        tracker = _con_mundo(_mundo(CODO, MANO, vis_mano=vis), zona_reposo=0.0)
        tracker.process(None)
        d = np.array(tracker.direcciones[Side.L])
        if anterior is not None:
            angulo = math.degrees(math.acos(max(-1.0, min(1.0, float(d @ anterior)))))
            assert angulo < 4.0
        anterior = d


# --------------------------------------------------------------------------
# 2. La zona de reposo
# --------------------------------------------------------------------------


def _direccion_a(grados_desde_abajo: float) -> tuple[float, float, float]:
    """Dirección (L, S, A) inclinada hacia delante desde la vertical."""
    a = math.radians(grados_desde_abajo)
    return (0.0, -math.cos(a), math.sin(a))


def test_dentro_de_la_zona_el_reposo_es_exacto():
    tracker, _ = _tracker()
    reposo = tracker.reposo[Side.L]
    for grados in (0.0, 13.0, 25.0):
        assert tracker.aplicar_reposo(Side.L, _direccion_a(grados), (-52.7, -12.7)) \
            == pytest.approx(reposo)
        assert tracker.pesos_reposo[Side.L] == pytest.approx(1.0)


def test_fuera_de_la_zona_no_toca_nada():
    tracker, _ = _tracker()
    for grados in (40.0, 60.0, 120.0):
        assert tracker.aplicar_reposo(Side.L, _direccion_a(grados), (-30.0, -80.0)) \
            == (-30.0, -80.0)


def test_la_zona_se_funde_sin_costura():
    tracker, _ = _tracker()
    anterior = None
    for grados in np.linspace(20.0, 45.0, 251):
        salida = np.array(tracker.aplicar_reposo(Side.L, _direccion_a(grados), (-30.0, -80.0)))
        if anterior is not None:
            assert np.max(np.abs(salida - anterior)) < 1.0
        anterior = salida


def test_la_zona_se_apaga_con_cero():
    tracker, _ = _tracker(zona_reposo=0.0)
    assert tracker.aplicar_reposo(Side.L, _direccion_a(0.0), (-52.7, -12.7)) == (-52.7, -12.7)


def test_el_brazo_quieto_no_da_el_saltito():
    """La regresión de las fotos 3 y 4: el brazo quieto cuelga en el borde del
    cuadro y su mano se ve a ratos. Medido o no, el comando que llega al robot
    no se mueve del reposo."""
    colgando = HOMBRO_IZQ + 0.3 * (ABAJO + 0.23 * ADELANTE) / np.linalg.norm(ABAJO + 0.23 * ADELANTE)
    mano = HOMBRO_IZQ + 0.58 * (colgando - HOMBRO_IZQ) / 0.3
    reloj = {"t": 0.0}
    visible = {"si": True}
    mundo_visto = _mundo(colgando, mano)
    mundo_perdido = _mundo(colgando, mano, vis_mano=0.1, vis_codo=0.1)

    def procesar(_frame):
        mundo = mundo_visto if visible["si"] else mundo_perdido
        return SimpleNamespace(
            pose_world_landmarks=SimpleNamespace(landmark=mundo), pose_landmarks=None
        )

    tracker = ArmTracker(
        pose_estimator=SimpleNamespace(process=procesar, close=lambda: None),
        frame_espejado=False,
        clock=lambda: reloj["t"],
    )
    reposo = tracker.reposo[Side.L]
    for i in range(120):
        reloj["t"] = i / 15
        visible["si"] = (i // 7) % 2 == 0            # se ve y se pierde a rachas
        mando = tracker.comando(tracker.process(None), reloj["t"])
        assert (mando.armL, mando.armLtw) == pytest.approx(reposo, abs=1e-6)


# --------------------------------------------------------------------------
# 3. El escorzo dentro del tracker
# --------------------------------------------------------------------------


def _escena_foto_1():
    """La foto del H39 hecha escena: manos junto a la cara y codos abajo, a
    1.2 m de una cámara de 640x480. Devuelve el mundo de BlazePose y la imagen
    de la escena VERDADERA.

    El error de BlazePose se imita como lo comete: su esqueleto conserva el
    largo de cada hueso y lo que se equivoca es la orientación. Aquí gira el
    antebrazo hasta apuntar casi a la cámara, que deja la mano unos 25 cm por
    delante. (Mover la mano sin más estiraría el antebrazo a 42 cm, un error que
    BlazePose no comete y que ningún escorzo puede deshacer.)"""
    camara = Camara.desde_imagen(640, 480, 1.0)
    hombro_i = camara.rayo(208, 276) * 1.2
    hombro_d = hombro_i + np.array([0.36, 0.0, 0.0])
    codo = hombro_i + np.array([-0.21, 0.21, 0.02])
    mano = codo + np.array([0.01, -0.265, 0.03])
    caderas = (hombro_i + hombro_d) / 2 + np.array([0.0, 0.5, 0.0])
    reales = {
        (Side.L, "hombro"): hombro_i, (Side.R, "hombro"): hombro_d,
        (Side.L, "codo"): codo, (Side.L, "mano"): mano,
        (Side.L, "cadera"): caderas + [-0.12, 0, 0], (Side.R, "cadera"): caderas + [0.12, 0, 0],
    }
    mundo = [LandmarkFalso() for _ in range(33)]
    imagen = [LandmarkFalso() for _ in range(33)]
    for (lado, nombre), punto in reales.items():
        nombres = PUNTOS_MANO if nombre == "mano" else (nombre,)
        visibilidad = 0.0 if nombre == "cadera" else 1.0
        en_mundo = punto - caderas
        if nombre == "mano":
            antebrazo = float(np.linalg.norm(mano - codo))
            girado = np.array([0.0, -0.1, -1.0]) / np.linalg.norm([0.0, -0.1, -1.0])
            en_mundo = (codo - caderas) + antebrazo * girado
        u, v = camara.proyectar(punto)
        for n in nombres:
            mundo[LANDMARKS[lado][n]] = LandmarkFalso(*en_mundo, visibility=visibilidad)
            imagen[LANDMARKS[lado][n]] = LandmarkFalso(u / 640, v / 480, 0.0, visibilidad)
    return mundo, imagen


def test_el_escorzo_devuelve_al_plano_la_mano_de_la_foto_1():
    mundo, imagen = _escena_foto_1()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    anterior = {}
    for escorzo in (True, False):
        tracker = _con_mundo(mundo, imagen, escorzo=escorzo, zona_reposo=0.0)
        tracker.process(frame)
        anterior[escorzo] = tracker.vectores[Side.L][2]     # componente anterior
        assert tracker.vectores_bp[Side.L][2] > 0.2        # BlazePose: la mano delante
    assert anterior[False] > 0.2
    # La verdadera está 5 cm DETRAS del hombro; el escorzo la deja a pocos cm
    # del plano, del lado que sugiere BlazePose (las dos soluciones de la mano
    # quedan a los dos lados).
    assert abs(anterior[True]) < 0.12


def test_el_escorzo_deja_constancia_de_los_pixeles():
    mundo, imagen = _escena_foto_1()
    tracker = _con_mundo(mundo, imagen, zona_reposo=0.0)
    tracker.process(np.zeros((480, 640, 3), dtype=np.uint8))
    puntos = tracker.puntos_imagen[Side.L]
    assert set(puntos) == {"hombro", "codo", "mano"}
    assert puntos["hombro"] == pytest.approx((208.0, 276.0), abs=0.5)


def test_sin_imagen_el_escorzo_no_cambia_nada():
    mundo, _ = _escena_foto_1()
    con = _con_mundo(mundo, None, escorzo=True, zona_reposo=0.0)
    sin = _con_mundo(mundo, None, escorzo=False, zona_reposo=0.0)
    con.process(None)
    sin.process(None)
    assert con.vectores[Side.L] == pytest.approx(sin.vectores[Side.L])


# --------------------------------------------------------------------------
# 4. Configuración
# --------------------------------------------------------------------------


def test_los_valores_por_defecto():
    tracker, _ = _tracker()
    assert tracker.target == "hibrido" == TARGETS[0]
    assert tracker.zona_reposo == 25.0
    assert tracker.escorzo is True
    cfg = AppConfig.from_env({})
    assert (cfg.arm.target, cfg.arm.zona_reposo, cfg.arm.escorzo, cfg.arm.focal_relativa) \
        == ("hibrido", 25.0, True, 1.0)
    assert cfg.arm.banda_tronco == 0.1


def test_la_configuracion_lee_los_parametros_nuevos():
    cfg = AppConfig.from_env({
        "TELMEOW_ARM_ZONA_REPOSO": "15",
        "TELMEOW_ARM_ESCORZO": "0",
        "TELMEOW_ARM_FOCAL_RELATIVA": "0.83",
        "TELMEOW_ARM_TARGET": "codo",
    })
    assert (cfg.arm.zona_reposo, cfg.arm.escorzo, cfg.arm.focal_relativa, cfg.arm.target) \
        == (15.0, False, 0.83, "codo")


@pytest.mark.parametrize(
    "kwargs",
    [{"target": "pie"}, {"zona_reposo": -1.0}, {"zona_reposo": 90.0}, {"focal_relativa": 0.0}],
)
def test_el_tracker_valida_los_parametros_del_p5_5(kwargs):
    with pytest.raises(ValueError):
        _tracker(**kwargs)
