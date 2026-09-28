"""Pruebas de la corrección de profundidad por escorzo (P5.5).

El ancla externa es la GEOMETRIA DE LA CAMARA: se construye un brazo 3D
verdadero, se proyecta con una cámara de agujero conocida —eso es «la
imagen»—, se le da a BlazePose una profundidad equivocada, y se exige que la
reconstrucción recupere el brazo verdadero. Ninguna prueba fabrica su
referencia con el propio reconstructor.

Los números de las fotos salen de la sesión del 2026-09-27 (H39): hombro
izquierdo en el píxel (208, 276), codo en (92, 378), mano en (108, 244), con
el operador a ~1.2 m de la cámara.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from telmeow_teleop.escorzo import (
    Camara,
    anclar_hombros,
    interseccion,
    reconstruir_brazo,
)

CAM = Camara.desde_imagen(640, 480, 1.0)


def _angulo(u, v) -> float:
    u = np.asarray(u, dtype=float) / np.linalg.norm(u)
    v = np.asarray(v, dtype=float) / np.linalg.norm(v)
    return math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(u, v))))))


def _reconstruir(hombro, codo, mano, dz_codo_bp, dz_mano_bp, previo=None, camara=CAM):
    """Proyecta el brazo verdadero y lo reconstruye con las profundidades que
    diría BlazePose. Los largos son los verdaderos."""
    return reconstruir_brazo(
        camara,
        hombro,
        camara.proyectar(codo),
        camara.proyectar(mano),
        float(np.linalg.norm(codo - hombro)),
        float(np.linalg.norm(mano - codo)),
        dz_codo_bp,
        dz_mano_bp,
        previo,
    )


HOMBRO_FOTO_1 = CAM.rayo(208, 276) * 1.2


# --------------------------------------------------------------------------
# 1. La cámara
# --------------------------------------------------------------------------


def test_proyectar_y_el_rayo_son_inversos():
    punto = np.array([0.31, -0.12, 0.84])
    u, v = CAM.proyectar(punto)
    assert CAM.rayo(u, v) * punto[2] == pytest.approx(punto)


@pytest.mark.parametrize("args", [(0, 480, 1.0), (640, 0, 1.0), (640, 480, 0.0)])
def test_la_camara_valida_sus_parametros(args):
    with pytest.raises(ValueError):
        Camara.desde_imagen(*args)


# --------------------------------------------------------------------------
# 2. La intersección
# --------------------------------------------------------------------------


def test_si_blazepose_es_coherente_se_queda_su_solucion():
    """Brazo en el plano y brazo hacia la cámara, con BlazePose diciendo la
    verdad: la reconstrucción no cambia nada."""
    hombro = np.array([0.1, 0.0, 0.8])
    for codo, mano in [
        (hombro + [0.21, 0.21, 0.0], hombro + [0.21, -0.05, 0.02]),
        (hombro + [0.05, 0.10, -0.27], hombro + [0.02, 0.05, -0.53]),
    ]:
        codo, mano = np.array(codo), np.array(mano)
        c, m, _ = _reconstruir(
            hombro, codo, mano, codo[2] - hombro[2], mano[2] - codo[2]
        )
        assert c == pytest.approx(codo, abs=1e-6)
        assert m == pytest.approx(mano, abs=1e-6)


def test_la_mano_de_la_foto_1_vuelve_al_plano():
    """El H39. Manos junto a la cara y codos abajo: BlazePose ponía la mano
    unos 35 cm por delante. Para ese píxel las dos soluciones de la mano quedan
    cerca del plano del codo, así que la reconstrucción la devuelve ahí diga
    BlazePose lo que diga."""
    codo, _ = interseccion(CAM.rayo(92, 378), HOMBRO_FOTO_1, 0.30, 0.02)
    rayo_mano = CAM.rayo(108, 244)
    for dz_bp in (-0.37, -0.23, -0.10):
        mano, exacto = interseccion(rayo_mano, codo, 0.27, dz_bp)
        assert exacto
        assert abs(mano[2] - codo[2]) < 0.10
        assert np.linalg.norm(mano - codo) == pytest.approx(0.27)


def test_con_la_profundidad_de_la_foto_1_la_direccion_mejora_de_verdad():
    """Brazo verdadero en el plano —codo abajo y afuera, antebrazo hacia
    arriba—, y BlazePose con el sesgo del H39: codo 10 cm y mano 25 cm hacia la
    cámara. La reconstrucción no es perfecta: las dos soluciones de la mano
    quedan a los dos lados del plano del codo y el sesgo elige la de delante,
    con unos 8 cm de error. Pero la dirección queda varias veces más cerca de
    la verdadera que la de BlazePose."""
    hombro = HOMBRO_FOTO_1
    codo = hombro + np.array([-0.21, 0.21, 0.02])
    mano = codo + np.array([0.01, -0.265, 0.03])
    c, m, _ = _reconstruir(hombro, codo, mano, -0.10, -0.25)
    blazepose = mano + np.array([0.0, 0.0, -0.10 - 0.25 - 0.02 - 0.03])
    error = _angulo(m - hombro, mano - hombro)
    error_bp = _angulo(blazepose - hombro, mano - hombro)
    assert error < 15.0
    assert error_bp > 3 * error


def test_acercar_el_brazo_a_la_camara_sigue_funcionando():
    """El P5.3 no se puede romper: brazo apuntando a la cámara y BlazePose con
    el signo bien pero la magnitud un 30 % corta. Se queda la solución de
    delante, con la perspectiva incluida."""
    hombro = np.array([0.15, 0.05, 0.75])
    codo = hombro + np.array([0.04, 0.12, -0.27])
    mano = codo + np.array([-0.02, -0.04, -0.26])
    c, m, _ = _reconstruir(
        hombro, codo, mano, 0.7 * (codo[2] - hombro[2]), 0.7 * (mano[2] - codo[2])
    )
    assert c == pytest.approx(codo, abs=0.01)
    assert m == pytest.approx(mano, abs=0.01)


def test_la_perspectiva_importa_y_esta_incluida():
    """Una mano 25 cm más cerca de la cámara y fuera del eje se ve mucho más
    desplazada de lo que está. Con una sola escala —proyección débil— el
    antebrazo parecería estar en el plano; el rayo lo reconstruye bien."""
    hombro = np.array([0.20, 0.0, 0.70])
    codo = hombro + np.array([0.10, 0.28, 0.0])
    mano = codo + np.array([0.08, -0.03, -0.25])
    u_c, v_c = CAM.proyectar(codo)
    u_m, v_m = CAM.proyectar(mano)
    escala = hombro[2] / CAM.focal                      # metros por píxel en el hombro
    aparente = math.hypot(u_m - u_c, v_m - v_c) * escala
    assert aparente > 0.9 * np.linalg.norm(mano - codo)  # parece casi en el plano
    _, m, _ = _reconstruir(hombro, codo, mano, 0.0, mano[2] - codo[2])
    assert m == pytest.approx(mano, abs=0.01)


def test_sin_corte_se_toma_el_punto_mas_cercano():
    hombro = np.array([0.0, 0.0, 1.0])
    rayo = CAM.rayo(620, 240)                          # muy lejos del hombro
    punto, exacto = interseccion(rayo, hombro, 0.1, 0.0)
    assert not exacto
    assert np.all(np.isfinite(punto))
    # Es el punto del rayo más cercano al hombro: el vector que los une es
    # perpendicular al rayo.
    assert float(np.dot(punto - hombro, rayo)) == pytest.approx(0.0, abs=1e-9)


def test_la_continuidad_desempata():
    """Con BlazePose justo en el empate entre las dos soluciones —el plano y lo
    que dice BlazePose pesan lo mismo—, decide la del frame anterior, sea cual
    sea. Fuera del empate, la continuidad no basta para imponerse: es solo un
    desempate."""
    from telmeow_teleop.escorzo import PESO_PLANO

    rayo = CAM.rayo(92, 378)
    cerca, _ = interseccion(rayo, HOMBRO_FOTO_1, 0.30, -0.5)
    lejos, _ = interseccion(rayo, HOMBRO_FOTO_1, 0.30, +0.5)
    dz_c = cerca[2] - HOMBRO_FOTO_1[2]
    dz_l = lejos[2] - HOMBRO_FOTO_1[2]
    assert dz_c < -0.15 and abs(dz_l) < 0.05
    empate = (dz_l + dz_c + PESO_PLANO * (abs(dz_l) - abs(dz_c))) / 2
    for previa, esperado in ((dz_c, cerca), (dz_l, lejos)):
        punto, _ = interseccion(
            rayo, HOMBRO_FOTO_1, 0.30, empate, profundidad_previa=previa
        )
        assert punto[2] == pytest.approx(esperado[2])
    # Lejos del empate manda BlazePose aunque la historia diga lo contrario.
    punto, _ = interseccion(rayo, HOMBRO_FOTO_1, 0.30, dz_c, profundidad_previa=dz_l)
    assert punto[2] == pytest.approx(cerca[2])


# --------------------------------------------------------------------------
# 3. El ancla
# --------------------------------------------------------------------------


def test_el_ancla_recupera_la_distancia_de_los_hombros():
    izq = np.array([0.15, -0.30, 1.10])
    der = np.array([-0.20, -0.29, 1.14])                # tronco algo girado
    mundo_izq = izq - (izq + der) / 2
    mundo_der = der - (izq + der) / 2
    a, b = anclar_hombros(
        CAM, CAM.proyectar(izq), CAM.proyectar(der), mundo_izq, mundo_der
    )
    assert a == pytest.approx(izq, abs=0.02)
    assert b == pytest.approx(der, abs=0.02)


def test_el_ancla_degenerada_devuelve_none():
    assert anclar_hombros(CAM, (300, 200), (300, 200), (0, 0, 0), (0.3, 0, 0)) is None
    assert anclar_hombros(CAM, (300, 200), (400, 200), (0, 0, 0), (0, 0, 0.3)) is None


def test_una_escala_equivocada_no_cambia_las_direcciones():
    """Si BlazePose se equivoca de escala —hombros y huesos un 20 % más
    grandes—, toda la escena se escala con ellos y las direcciones del brazo,
    que es lo que se usa, no cambian."""
    izq = np.array([0.15, -0.30, 1.10])
    der = np.array([-0.20, -0.30, 1.10])
    codo = izq + np.array([0.20, 0.20, 0.05])
    mano = codo + np.array([0.02, -0.26, 0.02])
    direcciones = []
    for escala in (1.0, 1.2):
        a, _ = anclar_hombros(
            CAM, CAM.proyectar(izq), CAM.proyectar(der),
            escala * (izq - (izq + der) / 2), escala * (der - (izq + der) / 2),
        )
        c, m, _ = reconstruir_brazo(
            CAM, a, CAM.proyectar(codo), CAM.proyectar(mano),
            escala * float(np.linalg.norm(codo - izq)),
            escala * float(np.linalg.norm(mano - codo)),
            escala * 0.05, escala * 0.02,
        )
        direcciones.append((m - a) / np.linalg.norm(m - a))
    assert _angulo(*direcciones) < 0.5
