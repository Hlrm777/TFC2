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
    FRONTAL_ROTATION,
    LANDMARK_IDS,
    MODEL_POINTS_3D,
    HeadTracker,
    camera_matrix,
    estimate_head_pose,
    rotation_between_clouds,
)
from telmeow_teleop.limits import HEAD_PAN_RANGE, HEAD_TILT_RANGE

ANCHO, ALTO = 640, 480
K = camera_matrix(ANCHO, ALTO)
SIN_DISTORSION = np.zeros((4, 1))
#: Cabeza a un metro de la cámara.
TVEC = np.array([[0.0], [0.0], [1000.0]])
#: Un punto 300 mm por delante de la nariz: marca hacia dónde MIRA la cara.
DIRECCION_MIRADA = np.array([[0.0, 0.0, 300.0]])


def rotacion(eje, grados):
    return cv2.Rodrigues(np.array(eje, dtype=np.float64) * math.radians(grados))[0]


def proyectar(eje, grados):
    """Proyecta el modelo 3D de una cara REAL girada `grados` sobre `eje`.

    La rotación se compone sobre ``FRONTAL_ROTATION``, no sobre la identidad.
    Este detalle no es cosmético: la identidad corresponde a una cara mirando
    AL REVÉS, que es físicamente imposible de trackear. Las primeras versiones
    de estas pruebas usaban la identidad y por eso NO detectaron el salto de
    360° que sí aparecía con una cara real frente a la cámara.
    """
    matriz = FRONTAL_ROTATION @ rotacion(eje, grados)
    rvec, _ = cv2.Rodrigues(matriz)
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


def test_yaw_positivo_significa_mirar_a_la_izquierda_de_la_imagen():
    """Fija la convención, medida sobre una cara REAL (no sobre la identidad).

    Con la imagen ESPEJADA que usa el pipeline, la izquierda del operador cae en
    la izquierda de la imagen. Así que: el operador gira a SU izquierda → la
    mirada va a la izquierda de la imagen → yaw POSITIVO.
    """
    puntos, rvec = proyectar([0, 1, 0], 20)
    yaw, _, _ = estimate_head_pose(puntos, (ANCHO, ALTO))
    mirada_x, _ = proyectar_mirada(rvec)
    assert yaw > 0
    assert mirada_x < ANCHO / 2


def test_yaw_negativo_significa_mirar_a_la_derecha_de_la_imagen():
    puntos, rvec = proyectar([0, 1, 0], -20)
    yaw, _, _ = estimate_head_pose(puntos, (ANCHO, ALTO))
    mirada_x, _ = proyectar_mirada(rvec)
    assert yaw < 0
    assert mirada_x > ANCHO / 2


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
    t = tracker_con(method="pnp")
    try:
        assert t.set_neutral() is False
    finally:
        t.close()


def test_el_neutro_elimina_el_sesgo_de_la_postura_natural():
    """Sin calibrar, un operador que trabaja con la cabeza ligeramente girada
    deja al robot permanentemente girado."""
    t = tracker_con(method="pnp")
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
    t = tracker_con(method="pnp", gain_pan=2.0)
    try:
        t._raw = (10.0, 0.0)
        t.set_neutral()
        pan, _ = t.map_to_robot(20.0, 0.0, timestamp=0.0)
        assert pan == pytest.approx(20.0, abs=0.5)  # (20-10)*2, no 20*2
    finally:
        t.close()


def test_quitar_el_neutro():
    t = tracker_con(method="pnp")
    try:
        t._raw = (15.0, 0.0)
        t.set_neutral()
        t.clear_neutral()
        assert t.neutral is None
        pan, _ = t.map_to_robot(15.0, 0.0, timestamp=0.0)
        assert pan == pytest.approx(15.0, abs=0.5)
    finally:
        t.close()


def test_en_modo_mesh_el_neutro_es_la_nube_de_referencia():
    """En 'mesh' la postura neutra no es un número que se resta: es el molde del
    rostro del operador contra el que se compara todo lo demás."""
    t = tracker_con(method="mesh")
    try:
        assert t.set_neutral() is False  # todavía no se ha visto ninguna cara
        t._last_cloud = np.random.default_rng(0).normal(size=(100, 3))
        assert t.set_neutral() is True
    finally:
        t.close()


# --------------------------------------------------------------------------
# El método 'mesh': rotación entre nubes de puntos (Kabsch)
# --------------------------------------------------------------------------


def nube_de_prueba(n=200, semilla=0):
    """Nube con la dispersión de un rostro, en píxeles."""
    rng = np.random.default_rng(semilla)
    return rng.normal(scale=40.0, size=(n, 3))


@pytest.mark.parametrize("grados", [-40, -20, -5, 5, 20, 40])
def test_kabsch_recupera_la_rotacion(grados):
    referencia = nube_de_prueba()
    R = rotacion([0, 1, 0], grados)
    recuperada = rotation_between_clouds(referencia, referencia @ R.T)
    assert np.allclose(recuperada, R, atol=1e-8)


def test_kabsch_ignora_la_traslacion():
    """Solo interesa la rotación: que el operador se acerque o se aparte de la
    cámara no debe mover la cabeza del robot."""
    referencia = nube_de_prueba()
    R = rotacion([1, 0, 0], 15)
    desplazada = referencia @ R.T + np.array([100.0, -50.0, 30.0])
    assert np.allclose(rotation_between_clouds(referencia, desplazada), R, atol=1e-8)


def test_kabsch_nunca_devuelve_una_reflexion():
    """Sin la corrección del determinante, el SVD puede devolver una reflexión
    en vez de una rotación: el robot movería la cabeza al espejo."""
    rng = np.random.default_rng(3)
    for _ in range(20):
        R = rotation_between_clouds(nube_de_prueba(semilla=rng.integers(1e6)),
                                    nube_de_prueba(semilla=rng.integers(1e6)))
        assert np.linalg.det(R) == pytest.approx(1.0)


def test_kabsch_aguanta_el_ruido_de_los_landmarks():
    """La ventaja del método: promedia el ruido sobre cientos de puntos.

    Con un ruido por punto del orden del que tiene MediaPipe, el error de la
    rotación recuperada se queda muy por debajo de un grado.
    """
    rng = np.random.default_rng(7)
    referencia = nube_de_prueba(n=400)
    R = rotacion([0, 1, 0], 25)
    observada = referencia @ R.T + rng.normal(scale=1.5, size=(400, 3))
    recuperada = rotation_between_clouds(referencia, observada)
    error = math.degrees(
        math.acos(min(1.0, (np.trace(recuperada.T @ R) - 1.0) / 2.0))
    )
    assert error < 1.0


def test_kabsch_es_mas_estable_que_pnp_con_ruido():
    """La razón de que 'mesh' sea el método por defecto.

    Mismo nivel de ruido por punto: solvePnP dispone de 6 puntos y Kabsch de
    400. Se compara la dispersión del ángulo estimado a lo largo de varias
    realizaciones del ruido.
    """
    rng = np.random.default_rng(11)
    R = rotacion([0, 1, 0], 20)

    # Kabsch sobre la nube completa
    referencia = nube_de_prueba(n=400)
    angulos_mesh = []
    for _ in range(20):
        observada = referencia @ R.T + rng.normal(scale=1.5, size=(400, 3))
        recuperada = rotation_between_clouds(referencia, observada)
        angulos_mesh.append(math.degrees(math.atan2(-recuperada[2, 0], recuperada[0, 0])))

    # solvePnP sobre los 6 puntos, con el mismo ruido por punto
    puntos, _ = proyectar([0, 1, 0], 20)
    angulos_pnp = []
    for _ in range(20):
        ruidosos = puntos + rng.normal(scale=1.5, size=puntos.shape)
        angulos_pnp.append(estimate_head_pose(ruidosos, (ANCHO, ALTO))[0])

    assert np.std(angulos_mesh) < np.std(angulos_pnp)


def test_kabsch_exige_nubes_del_mismo_tamano():
    with pytest.raises(ValueError):
        rotation_between_clouds(nube_de_prueba(10), nube_de_prueba(20))


# --------------------------------------------------------------------------
# Ayuda a la calibración de la ganancia
# --------------------------------------------------------------------------


def test_sin_movimiento_no_hay_ganancia_que_sugerir():
    t = tracker_con()
    try:
        assert t.suggested_gains() == {"pan": None, "tilt": None}
    finally:
        t.close()


def test_la_ganancia_sugerida_hace_que_el_recorrido_comodo_cubra_el_robot():
    """Si el operador solo alcanza 20° cómodamente y el robot llega a 42°, hace
    falta una ganancia de 2.1 para que ese recorrido cubra todo el rango."""
    t = tracker_con()
    try:
        t._extremos["tilt"] = [-19.5, 20.0]  # −39/−19.5 = 2.0 ; 42/20 = 2.1
        # Se toma el MENOR de los dos, para no dejar corto ningún lado.
        assert t.suggested_gains()["tilt"] == pytest.approx(2.0, abs=0.01)
    finally:
        t.close()


def test_aplicar_la_ganancia_sugerida_lleva_justo_al_tope():
    """Comprobación de extremo a extremo del procedimiento de calibración."""
    t = tracker_con()
    try:
        t._extremos["tilt"] = [-20.0, 20.0]
        sugerida = t.suggested_gains()["tilt"]
        t.gain_tilt = sugerida
        _, tilt = t.map_to_robot(0.0, -20.0, timestamp=0.0)
        assert tilt == pytest.approx(HEAD_TILT_RANGE[0], abs=0.5)
        assert t.last_clamp.clamped is False  # llega al tope sin pasarse
    finally:
        t.close()


def test_los_extremos_se_registran_y_se_reinician():
    t = tracker_con()
    try:
        t._extremos["pan"] = [-30.0, 25.0]
        assert t.raw_extremes["pan"] == (-30.0, 25.0)
        t.reset_extremes()
        assert t.raw_extremes["pan"] == (0.0, 0.0)
    finally:
        t.close()


def test_method_invalido_se_rechaza():
    with pytest.raises(ValueError):
        HeadTracker(method="otro")


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
