"""Pruebas del head tracker.

La estimación de pose se valida con **datos sintéticos**: se proyecta el modelo
3D del rostro con una rotación conocida y se comprueba que ``estimate_head_pose``
la recupera. Eso permite verificar la corrección y, sobre todo, **los signos**,
sin necesidad de una cara delante de la cámara — que es la parte que de otro
modo solo podría comprobarse a ojo.

Estas pruebas sí necesitan cv2 y numpy (el módulo es de visión, no puede ser
stdlib puro como config/limits/filters).
"""

import math

import cv2
import numpy as np
import pytest

from telmeow_teleop.head_tracker import (
    LANDMARK_IDS,
    MODEL_POINTS_3D,
    HeadTracker,
    camera_matrix,
    estimate_head_pose,
)
from telmeow_teleop.limits import HEAD_PAN_RANGE, HEAD_TILT_RANGE

ANCHO, ALTO = 640, 480
K = camera_matrix(ANCHO, ALTO)
SIN_DISTORSION = np.zeros((4, 1))
#: Cabeza a un metro de la cámara.
TVEC = np.array([[0.0], [0.0], [1000.0]])
#: Un punto 300 mm por delante de la nariz: marca hacia dónde MIRA la cara.
DIRECCION_MIRADA = np.array([[0.0, 0.0, 300.0]])


def proyectar(eje, grados):
    """Proyecta el modelo 3D rotado, como lo vería la cámara."""
    rvec = np.array(eje, dtype=np.float64).reshape(3, 1) * math.radians(grados)
    puntos, _ = cv2.projectPoints(MODEL_POINTS_3D, rvec, TVEC, K, SIN_DISTORSION)
    return puntos.reshape(-1, 2), rvec


def proyectar_mirada(rvec):
    punto, _ = cv2.projectPoints(DIRECCION_MIRADA, rvec, TVEC, K, SIN_DISTORSION)
    return punto.reshape(2)


# --------------------------------------------------------------------------
# Recuperación de la rotación
# --------------------------------------------------------------------------


def test_una_cara_frontal_da_angulos_cero():
    puntos, _ = proyectar([0, 1, 0], 0)
    yaw, pitch, roll = estimate_head_pose(puntos, (ANCHO, ALTO))
    assert yaw == pytest.approx(0.0, abs=1e-3)
    assert pitch == pytest.approx(0.0, abs=1e-3)
    assert roll == pytest.approx(0.0, abs=1e-3)


@pytest.mark.parametrize("grados", [-40, -30, -15, -5, 5, 15, 30, 40])
def test_recupera_el_yaw_impuesto(grados):
    """Ida y vuelta exacta: proyectar con una rotación conocida y resolverla."""
    puntos, _ = proyectar([0, 1, 0], grados)
    yaw, _, _ = estimate_head_pose(puntos, (ANCHO, ALTO))
    assert yaw == pytest.approx(grados, abs=0.1)


@pytest.mark.parametrize("grados", [-30, -15, 15, 30])
def test_recupera_el_pitch_impuesto(grados):
    puntos, _ = proyectar([1, 0, 0], grados)
    _, pitch, _ = estimate_head_pose(puntos, (ANCHO, ALTO))
    assert pitch == pytest.approx(grados, abs=0.1)


def test_los_ejes_no_se_contaminan_entre_si():
    """Girar solo en yaw no debe producir pitch, y viceversa. Es lo que
    distingue una estimación geométrica de un arctan2 sobre dos puntos."""
    puntos, _ = proyectar([0, 1, 0], 30)
    _, pitch, _ = estimate_head_pose(puntos, (ANCHO, ALTO))
    assert pitch == pytest.approx(0.0, abs=0.1)

    puntos, _ = proyectar([1, 0, 0], 30)
    yaw, _, _ = estimate_head_pose(puntos, (ANCHO, ALTO))
    assert yaw == pytest.approx(0.0, abs=0.1)


# --------------------------------------------------------------------------
# El sentido físico de los signos
# --------------------------------------------------------------------------


def test_yaw_positivo_significa_mirar_a_la_derecha_de_la_imagen():
    """Fija la convención. Con la imagen ESPEJADA que usa el pipeline, la
    izquierda del operador cae en la izquierda de la imagen, así que girar a su
    izquierda produce yaw NEGATIVO. Es la fuente clásica de confusión y por eso
    queda escrita en una prueba."""
    puntos, rvec = proyectar([0, 1, 0], 20)
    yaw, _, _ = estimate_head_pose(puntos, (ANCHO, ALTO))
    mirada_x, _ = proyectar_mirada(rvec)
    assert yaw > 0
    assert mirada_x > ANCHO / 2  # mira a la derecha de la imagen


def test_yaw_negativo_significa_mirar_a_la_izquierda_de_la_imagen():
    puntos, rvec = proyectar([0, 1, 0], -20)
    yaw, _, _ = estimate_head_pose(puntos, (ANCHO, ALTO))
    mirada_x, _ = proyectar_mirada(rvec)
    assert yaw < 0
    assert mirada_x < ANCHO / 2


def test_pitch_positivo_significa_mirar_hacia_arriba():
    """El pitch NO depende del espejo: voltear la imagen solo invierte el eje
    horizontal. Así que tilt positivo = levantar la barbilla, siempre."""
    puntos, rvec = proyectar([1, 0, 0], 20)
    _, pitch, _ = estimate_head_pose(puntos, (ANCHO, ALTO))
    _, mirada_y = proyectar_mirada(rvec)
    assert pitch > 0
    assert mirada_y < ALTO / 2  # en imagen, menor y = más arriba


def test_el_yaw_es_monotono():
    valores = [
        estimate_head_pose(proyectar([0, 1, 0], g)[0], (ANCHO, ALTO))[0]
        for g in range(-40, 41, 10)
    ]
    assert valores == sorted(valores)


# --------------------------------------------------------------------------
# El orden de los puntos
# --------------------------------------------------------------------------


def test_el_modelo_y_los_landmarks_tienen_el_mismo_numero_de_puntos():
    """Si se descuadran, solvePnP devuelve una pose sin sentido y NO da ningún
    error: es un fallo silencioso."""
    assert len(MODEL_POINTS_3D) == len(LANDMARK_IDS) == 6


def test_desordenar_los_puntos_estropea_la_estimacion():
    """Confirma que el orden importa de verdad, y que emparejar mal el modelo
    con los landmarks no pasaría inadvertido si alguna vez se toca."""
    puntos, _ = proyectar([0, 1, 0], 25)
    correcta = estimate_head_pose(puntos, (ANCHO, ALTO))
    permutada = estimate_head_pose(puntos[[1, 0, 3, 2, 5, 4]], (ANCHO, ALTO))
    assert abs(correcta[0] - permutada[0]) > 5.0


def test_numero_de_puntos_equivocado_falla_de_inmediato():
    with pytest.raises(ValueError):
        estimate_head_pose(np.zeros((4, 2)), (ANCHO, ALTO))


# --------------------------------------------------------------------------
# La cámara
# --------------------------------------------------------------------------


def test_la_matriz_de_camara_tiene_el_centro_en_el_centro():
    K640 = camera_matrix(640, 480)
    assert K640[0, 2] == 320.0
    assert K640[1, 2] == 240.0
    assert K640[0, 0] == K640[1, 1] == 640.0


def test_la_resolucion_no_cambia_los_angulos():
    """La focal se aproxima con el ancho de la imagen, así que la escala se
    cancela: un mismo giro debe medirse igual a 640 y a 1280 de ancho."""
    rvec = np.array([0.0, 1.0, 0.0]).reshape(3, 1) * math.radians(25)
    angulos = []
    for w, h in ((640, 480), (1280, 960)):
        pts, _ = cv2.projectPoints(
            MODEL_POINTS_3D, rvec, TVEC, camera_matrix(w, h), SIN_DISTORSION
        )
        angulos.append(estimate_head_pose(pts.reshape(-1, 2), (w, h))[0])
    assert angulos[0] == pytest.approx(angulos[1], abs=0.1)


# --------------------------------------------------------------------------
# La ganancia y el recorte, sin cámara
# --------------------------------------------------------------------------


class RelojFalso:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def avanzar(self, s=1.0 / 30.0):
        self.t += s


def tracker_con(**kwargs):
    """HeadTracker con reloj controlado y filtrado prácticamente desactivado.

    `beta` alto y `min_cutoff` alto dejan pasar la señal casi intacta, para que
    estas pruebas midan la GANANCIA y el RECORTE, no el suavizado — que ya tiene
    sus propias pruebas en test_filters.py.
    """
    kwargs.setdefault("min_cutoff", 1000.0)
    kwargs.setdefault("beta", 0.0)
    return HeadTracker(clock=RelojFalso(), **kwargs)


@pytest.mark.parametrize(
    "ganancia, esperado", [(1.0, 20.0), (2.0, 40.0), (0.5, 10.0)]
)
def test_la_ganancia_multiplica_el_giro_del_operador(ganancia, esperado):
    """La ganancia relaciona cuánto gira el operador con cuánto gira el robot.
    Con 2.0, girar 20° mueve el robot 40°."""
    t = tracker_con(gain_pan=ganancia)
    try:
        pan, _ = t.map_to_robot(20.0, 0.0, timestamp=0.0)
        assert pan == pytest.approx(esperado, abs=0.5)
    finally:
        t.close()


def test_la_ganancia_de_tilt_es_independiente_de_la_de_pan():
    """Están separadas porque los rangos del robot no se parecen: pan ±90° es
    comparable al cuello humano, pero tilt −39°/+42° se queda muy corto."""
    t = tracker_con(gain_pan=1.0, gain_tilt=0.5)
    try:
        pan, tilt = t.map_to_robot(20.0, 20.0, timestamp=0.0)
        assert pan == pytest.approx(20.0, abs=0.5)
        assert tilt == pytest.approx(10.0, abs=0.5)
    finally:
        t.close()


def test_amplificar_llega_antes_al_tope_del_robot():
    """El precio de amplificar. El operador inclina 25°, que cabe de sobra en el
    rango del robot; con ganancia 2.0 se sale y hay que recortar."""
    suave = tracker_con(gain_tilt=1.0)
    fuerte = tracker_con(gain_tilt=2.0)
    try:
        _, tilt_suave = suave.map_to_robot(0.0, -25.0, timestamp=0.0)
        _, tilt_fuerte = fuerte.map_to_robot(0.0, -25.0, timestamp=0.0)
        assert tilt_suave == pytest.approx(-25.0, abs=0.5)
        assert suave.last_clamp.clamped is False
        assert tilt_fuerte == pytest.approx(HEAD_TILT_RANGE[0])
        assert fuerte.last_clamp.clamped is True
    finally:
        suave.close()
        fuerte.close()


def test_la_salida_nunca_se_sale_de_los_limites():
    t = tracker_con(gain_pan=5.0, gain_tilt=5.0)
    try:
        pan, tilt = t.map_to_robot(80.0, 80.0, timestamp=0.0)
        assert HEAD_PAN_RANGE[0] <= pan <= HEAD_PAN_RANGE[1]
        assert HEAD_TILT_RANGE[0] <= tilt <= HEAD_TILT_RANGE[1]
        assert t.last_clamp.clamped is True
    finally:
        t.close()


def test_invertir_cambia_el_sentido():
    """Existe porque el sentido correcto solo se confirma mirando el visor: si
    el robot gira al revés que el operador, se corrige sin tocar el código."""
    normal, invertido = tracker_con(), tracker_con(invert_pan=True)
    try:
        pan_normal, _ = normal.map_to_robot(20.0, 0.0, timestamp=0.0)
        pan_invertido, _ = invertido.map_to_robot(20.0, 0.0, timestamp=0.0)
        assert pan_normal == pytest.approx(-pan_invertido, abs=0.5)
    finally:
        normal.close()
        invertido.close()


def test_ganancias_no_positivas_se_rechazan():
    for kwargs in ({"gain_pan": 0.0}, {"gain_tilt": -1.0}):
        with pytest.raises(ValueError):
            HeadTracker(**kwargs)


# --------------------------------------------------------------------------
# Calibración del neutro
# --------------------------------------------------------------------------


def test_sin_pose_no_hay_nada_que_calibrar():
    t = tracker_con()
    try:
        assert t.set_neutral() is False
    finally:
        t.close()


def test_el_neutro_elimina_el_sesgo_de_la_postura_natural():
    """Sin calibrar, un operador que trabaja con la cabeza ligeramente girada
    deja al robot permanentemente girado."""
    t = tracker_con()
    try:
        t._raw = (15.0, 8.0)  # postura natural del operador
        assert t.set_neutral() is True
        pan, tilt = t.map_to_robot(15.0, 8.0, timestamp=0.0)
        assert pan == pytest.approx(0.0, abs=0.5)
        assert tilt == pytest.approx(0.0, abs=0.5)
    finally:
        t.close()


def test_el_neutro_se_resta_antes_de_la_ganancia():
    """Se amplifica la DESVIACIÓN respecto al reposo, no el ángulo absoluto."""
    t = tracker_con(gain_pan=2.0)
    try:
        t._raw = (10.0, 0.0)
        t.set_neutral()
        pan, _ = t.map_to_robot(20.0, 0.0, timestamp=0.0)
        assert pan == pytest.approx(20.0, abs=0.5)  # (20-10)*2, no 20*2
    finally:
        t.close()


def test_quitar_el_neutro():
    t = tracker_con()
    try:
        t._raw = (15.0, 0.0)
        t.set_neutral()
        t.clear_neutral()
        assert t.neutral is None
        pan, _ = t.map_to_robot(15.0, 0.0, timestamp=0.0)
        assert pan == pytest.approx(15.0, abs=0.5)
    finally:
        t.close()


# --------------------------------------------------------------------------
# Robustez
# --------------------------------------------------------------------------


@pytest.mark.parametrize("malo", [float("nan"), float("inf")])
def test_un_angulo_no_finito_descarta_el_frame(malo):
    """clamp_head rechaza NaN a propósito para que no llegue al robot; el
    tracker lo captura y devuelve None en vez de propagarlo."""
    t = tracker_con()
    try:
        assert t.map_to_robot(malo, 0.0, timestamp=0.0) is None
    finally:
        t.close()


def test_last_clamp_da_al_hud_lo_que_necesita():
    """El HUD del PROMPT 7 pinta barras con el rango y marca en rojo el recorte,
    sin recalcular nada."""
    t = tracker_con()
    try:
        t.map_to_robot(10.0, 5.0, timestamp=0.0)
        c = t.last_clamp
        assert (c.pan.lower, c.pan.upper) == HEAD_PAN_RANGE
        assert (c.tilt.lower, c.tilt.upper) == HEAD_TILT_RANGE
        assert 0.0 <= c.pan.fraction <= 1.0
    finally:
        t.close()


def test_el_rango_de_tilt_del_robot_es_menor_que_el_de_una_persona():
    """Justifica que la ganancia de tilt sea configurable por separado: el pan
    del robot (±90°) es comparable al humano, pero su tilt no."""
    recorrido_pan = HEAD_PAN_RANGE[1] - HEAD_PAN_RANGE[0]
    recorrido_tilt = HEAD_TILT_RANGE[1] - HEAD_TILT_RANGE[0]
    assert recorrido_tilt < recorrido_pan / 2
