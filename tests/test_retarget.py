"""Pruebas del retargeting por dirección (P5.3).

Tres anclas externas, igual que en ``test_arm_tracker.py``: ninguna prueba de
signo o de rama se valida contra el propio código bajo prueba.

1. **El rig del visor**, transcrito a mano en ``test_arm_tracker.py``
   (``direccion_del_visor``), que es la cinemática que el visor aplica de verdad.
2. **Las tablas del barrido** de ``limits.py``.
3. **La grabación del 2026-09-13**, donde se midió el defecto que esto corrige.
"""

from __future__ import annotations

import math
import random
from pathlib import Path

import numpy as np
import pytest

from telmeow_teleop.arm_tracker import (
    INCLINACION_FLEXION,
    ArmAngles,
    ArmTracker,
    arm_angles,
    direccion_brazo,
    torso_basis,
)
from telmeow_teleop.filters import OneEuroVector, SlewLimiterND
from telmeow_teleop.limits import (
    FLEXION_RANGE,
    FLEXION_RANGE_EXTENDIDO,
    Side,
    arm_abduccion_range,
    arm_abduccion_range_extendido,
    clamp_arm,
)
from telmeow_teleop.retarget import (
    RetargetBrazo,
    al_frente,
    alcance_robot,
    articulares,
    direccion,
    escala_radial,
    polares,
)
from test_arm_tracker import (
    HOMBRO_DER,
    HOMBRO_IZQ,
    LARGO_BRAZO,
    SUPERIOR,
    direccion_del_visor,
    visor_a_mediapipe,
)

RAIZ = Path(__file__).resolve().parent.parent


def _base(lado):
    if lado is Side.L:
        return HOMBRO_IZQ, torso_basis(HOMBRO_IZQ, HOMBRO_DER, SUPERIOR)
    return HOMBRO_DER, torso_basis(HOMBRO_DER, HOMBRO_IZQ, SUPERIOR)


def _h_del_robot(lado, abduccion, flexion):
    """Dirección ``(L, S, A)`` que el RIG DEL VISOR da a una pose del robot,
    leída con el mismo estimador que lee al operador."""
    hombro, base = _base(lado)
    d = visor_a_mediapipe(direccion_del_visor(lado, abduccion, flexion))
    return direccion_brazo(hombro, hombro + LARGO_BRAZO * d, base, INCLINACION_FLEXION[lado])


def _angulo(u, v):
    u = np.asarray(u) / np.linalg.norm(u)
    v = np.asarray(v) / np.linalg.norm(v)
    return math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(u, v))))))


# --------------------------------------------------------------------------
# 1. Geometría contra el rig
# --------------------------------------------------------------------------


@pytest.mark.parametrize("lado", [Side.L, Side.R])
@pytest.mark.parametrize("abduccion", [-60.0, -30.0, 15.0, 40.0])
@pytest.mark.parametrize("flexion", [-80.0, -30.0, 0.0, 45.0, 85.0])
def test_la_cinematica_directa_es_la_del_rig(lado, abduccion, flexion):
    """``retarget.direccion`` y el rig del visor dan la misma dirección."""
    assert _angulo(direccion(abduccion, flexion), _h_del_robot(lado, abduccion, flexion)) < 0.3


@pytest.mark.parametrize("lado", [Side.L, Side.R])
@pytest.mark.parametrize("abduccion", [-50.0, -20.0, 25.0, 60.0])
@pytest.mark.parametrize("flexion", [-150.0, -100.0, -45.0, 10.0, 80.0])
def test_las_dos_ramas_son_la_misma_direccion_en_el_rig(lado, abduccion, flexion):
    """``(a, f)`` y ``(−a, f ± 180)`` apuntan igual, con el eje de flexión REAL
    (inclinado ~10°). Es lo que justifica la flexión extendida y las tablas
    extendidas por simetría."""
    otra = flexion + 180.0 if flexion < 0 else flexion - 180.0
    assert _angulo(
        _h_del_robot(lado, abduccion, flexion), _h_del_robot(lado, -abduccion, otra)
    ) < 1e-6


def test_anclas_del_visor_p5_1():
    """Lo que se VIO en el visor el 2026-09-07 (P5.1), sin pasar por el rig."""
    assert direccion(-50.0, -60.0)[2] > 0.5    # armR −50, tw −60 → adelante
    assert direccion(20.0, 60.0)[2] > 0.25     # armR +20, tw +60 → adelante
    assert direccion(-50.0, 0.0)[1] < -0.7     # −50 baja el brazo
    assert direccion(30.0, 0.0)[1] > 0.45      # +30 lo sube
    assert direccion(0.0, 77.0) == pytest.approx((1.0, 0.0, 0.0))  # polo


@pytest.mark.parametrize("rama", [-1, 1])
def test_articulares_invierte_a_la_cinematica_directa(rama):
    rng = random.Random(3)
    for _ in range(200):
        psi, alfa = rng.uniform(1, 150), rng.uniform(0, 180)
        p, a = polares(direccion(*articulares(psi, alfa, rama)))
        assert p == pytest.approx(psi, abs=1e-6)
        assert a == pytest.approx(alfa, abs=1e-6)


def test_la_rama_negativa_contiene_al_reposo():
    """Brazo abajo = (−ψ, 0): el reposo del robot está en la rama −1."""
    assert articulares(55.0, 180.0, -1) == (-55.0, 0.0)


def test_al_frente_proyecta_y_no_toca_lo_frontal():
    assert al_frente((0.3, 0.4, 0.5)) == pytest.approx(
        tuple(np.array([0.3, 0.4, 0.5]) / np.linalg.norm([0.3, 0.4, 0.5]))
    )
    L, S, A = al_frente((0.5, -0.5, -0.7))
    assert A == 0.0 and S < 0 and L > 0
    # Justo hacia atrás cae en el polo, sin salto.
    assert al_frente((0.0, 0.0, -1.0)) == (1.0, 0.0, 0.0)
    assert al_frente((0.0, 0.0, 0.0)) is None


# --------------------------------------------------------------------------
# 2. Envolvente y escala contra las tablas
# --------------------------------------------------------------------------


@pytest.mark.parametrize("lado", [Side.L, Side.R])
def test_el_alcance_es_continuo_en_la_costura_de_la_tabla(lado):
    alfas = np.linspace(0, 180, 3601)
    alcances = np.array([alcance_robot(a, lado) for a in alfas])
    assert np.max(np.abs(np.diff(alcances))) < 0.1
    # Y dentro de la franja no promete más de lo que admite cada lado.
    for a in np.linspace(85, 95, 41):
        arriba = arm_abduccion_range(a, lado)[1] if a <= 90 else math.inf
        abajo = -arm_abduccion_range(a - 180.0, lado)[0] if a >= 90 else math.inf
        assert alcance_robot(a, lado) <= min(arriba, abajo) + 1e-9


@pytest.mark.parametrize("lado", [Side.L, Side.R])
def test_el_alcance_sale_de_la_tabla(lado):
    assert alcance_robot(0.0, lado) == arm_abduccion_range(0.0, lado)[1]      # arriba
    assert alcance_robot(180.0, lado) == -arm_abduccion_range(0.0, lado)[0]   # abajo
    assert alcance_robot(135.0, lado) == -arm_abduccion_range(-45.0, lado)[0]
    assert alcance_robot(45.0, lado) == arm_abduccion_range(45.0, lado)[1]
    # Las cifras: el robot llega a ~30 arriba, ~55 abajo, ~70 al frente.
    assert alcance_robot(0.0, lado) == pytest.approx(30.0)
    assert alcance_robot(180.0, lado) == pytest.approx(55.0)
    assert 65.0 <= alcance_robot(90.0, lado) <= 75.0


def test_las_ganancias_medidas_en_p5_2_son_la_envolvente():
    """Las ganancias provisionales que el P5.2 midió sin este modelo (0.33 en
    abducción, 0.51 en aducción) son el cociente entre la envolvente del robot
    y los 90° del operador. Arriba cuadra exacto; abajo la medida quedó por
    debajo porque salió de percentiles de una sesión sin protocolo."""
    assert alcance_robot(0.0, Side.L) / 90.0 == pytest.approx(0.33, abs=0.01)
    assert 0.51 <= alcance_robot(180.0, Side.L) / 90.0 <= 0.62


def test_la_escala_llega_exacto_al_tope_en_el_alcance_humano():
    assert escala_radial(0.0, 55.0) == 0.0
    assert escala_radial(90.0, 55.0) == pytest.approx(55.0)
    assert escala_radial(130.0, 55.0) == pytest.approx(55.0)   # cruzando el cuerpo
    assert escala_radial(-5.0, 55.0) == 0.0
    assert escala_radial(40.0, 0.0) == 0.0


def test_la_escala_es_monotona_y_sin_esquinas():
    """C¹: sin salto de pendiente en la rodilla ni en el tope. Una esquina es
    donde un operador en el límite hace vibrar el brazo."""
    xs = np.linspace(0, 120, 2401)
    ys = np.array([escala_radial(x, 60.0) for x in xs])
    assert np.all(np.diff(ys) >= -1e-12)
    pendiente = np.diff(ys) / np.diff(xs)
    assert np.max(np.abs(np.diff(pendiente))) < 0.01


@pytest.mark.parametrize("kwargs", [{"rodilla": 0.0}, {"rodilla": 1.0}, {"alcance_humano": 0.0}])
def test_la_escala_valida_parametros(kwargs):
    with pytest.raises(ValueError):
        escala_radial(10.0, 50.0, **kwargs)


# --------------------------------------------------------------------------
# 3. Límites extendidos
# --------------------------------------------------------------------------


@pytest.mark.parametrize("lado", [Side.L, Side.R])
def test_el_rango_extendido_coincide_con_la_tabla_dentro_de_90(lado):
    for f in np.linspace(-90, 90, 37):
        assert arm_abduccion_range_extendido(f, lado) == arm_abduccion_range(f, lado)


@pytest.mark.parametrize("lado", [Side.L, Side.R])
def test_el_rango_extendido_es_la_tabla_reflejada(lado):
    lo, hi = arm_abduccion_range(45.0, lado)
    assert arm_abduccion_range_extendido(-135.0, lado) == (-hi, -lo)
    lo, hi = arm_abduccion_range(-30.0, lado)
    assert arm_abduccion_range_extendido(150.0, lado) == (-hi, -lo)


def test_clamp_arm_por_defecto_sigue_en_90():
    """El contrato viejo no cambia: sin pedirlo, la flexión se recorta a ±90."""
    assert clamp_arm(-40.0, -135.0, "R").as_tuple[1] == FLEXION_RANGE[0]
    assert clamp_arm(-40.0, -135.0, "R", flexion_range=FLEXION_RANGE_EXTENDIDO).as_tuple == (
        -40.0,
        -135.0,
    )


def test_el_visor_admite_la_flexion_extendida():
    """El slider de flexión del visor tiene que llegar al mismo tope que el
    Python, o la pose recibida por MQTT no se podría reproducir a mano."""
    html = (RAIZ / "telmeow_viewer" / "telmeow_viewer.html").read_text(encoding="utf-8")
    for id_ in ("s-arLtw", "s-arRtw"):
        assert f'id="{id_}" min="{FLEXION_RANGE_EXTENDIDO[0]:g}"' in html


# --------------------------------------------------------------------------
# 4. RetargetBrazo: la costura
# --------------------------------------------------------------------------


def _ruido_en_la_costura(n=400, semilla=7):
    """Brazo al frente y horizontal, con la componente vertical cambiando de
    signo por ruido: lo que produce «acercar las manos a la cámara»."""
    rng = random.Random(semilla)
    for _ in range(n):
        yield (0.25 + rng.gauss(0, 0.02), rng.gauss(0, 0.03), 0.95 + rng.gauss(0, 0.02))


def test_el_mapeo_articular_salta_de_rama_en_la_costura():
    """El defecto, reproducido: con el brazo al frente, el signo de la
    abducción que da ``arm_angles`` sigue al ruido. (En la grabación del
    2026-09-13, 303 saltos así.)"""
    hombro, base = _base(Side.R)
    u_lat, u_ant, u_sup = base
    signos = set()
    for L, S, A in _ruido_en_la_costura():
        a, _ = arm_angles(hombro, hombro + L * u_lat + S * u_sup + A * u_ant, base)
        signos.add(a > 0)
    assert signos == {True, False}


@pytest.mark.parametrize("lado", [Side.L, Side.R])
def test_en_la_costura_el_retargeting_no_salta(lado):
    r = RetargetBrazo(lado)
    salidas = [r(h).as_tuple for h in _ruido_en_la_costura()]
    saltos = np.abs(np.diff(np.array(salidas), axis=0))
    assert r.cambios_de_rama == 0
    # Sin filtrar queda el ruido de la entrada (unos grados); lo que no puede
    # quedar es el salto de rama, que es de 100° o más.
    assert saltos.max() < 12.0
    assert all(a < 0 for a, _ in salidas)
    # Y el brazo del robot va hacia ADELANTE, que es lo que se pedía.
    for a, f in salidas:
        assert direccion(a, f)[2] > 0.8


@pytest.mark.parametrize("lado", [Side.L, Side.R])
def test_barrer_de_arriba_a_abajo_por_delante_es_continuo(lado):
    """El operador sube el brazo por delante y lo baja: α de 0 a 180 a ψ fijo.
    Con la flexión extendida, una sola rama y la flexión recorre −180 → 0 sin
    un salto."""
    r = RetargetBrazo(lado)
    r.reset(-55.0, 0.0)
    salidas = []
    for alfa in np.linspace(0, 180, 361):
        rad = math.radians(alfa)
        psi = math.radians(80)
        h = (math.cos(psi), math.sin(psi) * math.cos(rad), math.sin(psi) * math.sin(rad))
        salidas.append(r(h).as_tuple)
    salidas = np.array(salidas)
    assert r.cambios_de_rama == 0
    assert np.all(np.diff(salidas[:, 1]) > 0)                 # flexión monótona
    assert np.max(np.abs(np.diff(salidas, axis=0))) < 1.0   # pasos de 0.5° en α
    assert salidas[0, 1] == pytest.approx(-180.0)
    assert salidas[-1, 1] == pytest.approx(0.0)


@pytest.mark.parametrize("lado", [Side.L, Side.R])
def test_con_flexion_de_90_la_costura_se_cruza_una_vez_y_con_histeresis(lado):
    """Modo de reserva, si el CAD no admitiera −180: la flexión nunca sale de
    ±90 y la costura se cruza con UN cambio de rama por pasada, no uno por
    frame de ruido."""
    r = RetargetBrazo(lado, flexion_range=FLEXION_RANGE)
    for alfa in np.linspace(180, 0, 181):
        rad, psi = math.radians(alfa), math.radians(70)
        a, f = r((math.cos(psi), math.sin(psi) * math.cos(rad), math.sin(psi) * math.sin(rad))).as_tuple
        assert FLEXION_RANGE[0] <= f <= FLEXION_RANGE[1]
    assert r.cambios_de_rama == 1
    # Oscilando en la costura no hay más cambios.
    antes = r.cambios_de_rama
    for alfa in (85, 95, 88, 97, 83, 92) * 20:
        rad, psi = math.radians(alfa), math.radians(70)
        r((math.cos(psi), math.sin(psi) * math.cos(rad), math.sin(psi) * math.sin(rad)))
    assert r.cambios_de_rama == antes


@pytest.mark.parametrize("lado", [Side.L, Side.R])
def test_la_salida_respeta_la_tabla_y_el_frente(lado):
    rng = random.Random(11)
    r = RetargetBrazo(lado)
    for _ in range(2000):
        h = (rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1))
        paso = r(h)
        if paso is None:
            continue
        a, f = paso.as_tuple
        lo, hi = arm_abduccion_range_extendido(f, lado)
        assert lo - 1e-9 <= a <= hi + 1e-9
        assert FLEXION_RANGE_EXTENDIDO[0] <= f <= FLEXION_RANGE_EXTENDIDO[1]
        assert direccion(a, f)[2] >= -1e-9          # nunca por detrás


@pytest.mark.parametrize("lado", [Side.L, Side.R])
def test_el_azimut_se_conserva_a_traves_del_rig(lado):
    """Extremo a extremo contra el rig: el operador apunta a una dirección, el
    retargeting da ángulos, el RIG DEL VISOR los convierte en una dirección, y
    esa dirección tiene el mismo azimut (arriba/frente/abajo) que la pedida."""
    rng = random.Random(5)
    for _ in range(300):
        psi, alfa = rng.uniform(25, 100), rng.uniform(2, 178)
        h = direccion(*articulares(psi, alfa, 1))
        r = RetargetBrazo(lado)
        a, f = r(h).as_tuple
        _, alfa_robot = polares(_h_del_robot(lado, a, f))
        assert alfa_robot == pytest.approx(alfa, abs=0.5)


def test_el_brazo_colgando_pide_el_reposo():
    r = RetargetBrazo(Side.R, margen=0.0)
    a, f = r((0.0, -1.0, 0.0)).as_tuple
    assert (a, f) == pytest.approx((-55.0, 0.0))


def test_cerca_del_polo_la_flexion_no_sigue_al_ruido():
    """Brazo casi en cruz: α es ruido y la flexión no mueve el brazo. Dejarla
    seguir haría girar la pieza del hombro sobre sí misma."""
    r = RetargetBrazo(Side.R)
    r.reset(-40.0, -60.0)
    rng = random.Random(2)
    for _ in range(200):
        ang = rng.uniform(0, 2 * math.pi)
        r((1.0, 0.02 * math.cos(ang), 0.02 * math.sin(ang)))
    assert r(( 1.0, 0.0, 0.02)).flexion == pytest.approx(-60.0, abs=1.0)


def test_entrada_degenerada():
    r = RetargetBrazo(Side.L)
    assert r((0.0, 0.0, 0.0)) is None
    assert r((float("nan"), 0.0, 1.0)) is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"rama_preferida": 0},
        {"flexion_range": (10.0, -10.0)},
        {"alcance_humano": 0.0},
        {"margen": -1.0},
        {"polo": (20.0, 5.0)},
        {"rodilla": 1.0},
    ],
)
def test_retarget_valida_parametros(kwargs):
    with pytest.raises(ValueError):
        RetargetBrazo(Side.L, **kwargs)


# --------------------------------------------------------------------------
# 5. Filtros nuevos
# --------------------------------------------------------------------------


def test_one_euro_vectorial_no_gira_el_vector():
    """Un solo corte para las tres componentes: la salida queda en el segmento
    entre el estado anterior y la muestra, así que el filtro puede retrasar la
    dirección pero no desviarla."""
    f = OneEuroVector(1.0, 0.0)
    u = np.array([1.0, 0.0, 0.0])
    v = np.array([0.0, 0.6, 0.8])
    f(u, 0.0)
    y = np.array(f(v, 1 / 30))
    t = float(np.dot(y - u, v - u) / np.dot(v - u, v - u))
    assert 0.0 < t < 1.0
    assert np.allclose(y, u + t * (v - u))


def test_one_euro_vectorial_constante_y_validacion():
    f = OneEuroVector(0.5, 20.0)
    for i in range(10):
        assert f((0.1, 0.2, 0.3), i / 30) == pytest.approx((0.1, 0.2, 0.3))
    with pytest.raises(ValueError):
        f((float("nan"), 0.0, 0.0), 1.0)
    with pytest.raises(ValueError):
        f((0.0, 0.0), 2.0)
    with pytest.raises(ValueError):
        OneEuroVector(0.0)


def test_slew_nd_recorre_la_recta():
    """Límite sobre la norma: las dos articulaciones llegan a la vez, por la
    recta, en vez de una L."""
    s = SlewLimiterND(180.0)
    s.reset((-55.0, 0.0))
    objetivo = np.array([-30.0, -180.0])
    inicio = np.array([-55.0, 0.0])
    t = 0.0
    for _ in range(200):
        t += 1 / 60
        y = np.array(s(objetivo, t))
        # Colineal con inicio → objetivo.
        d = objetivo - inicio
        assert abs(float(np.cross(d, y - inicio))) < 1e-6
    assert np.allclose(y, objetivo)


def test_slew_nd_acota_la_norma_del_paso():
    s = SlewLimiterND(90.0)
    s.reset((0.0, 0.0))
    s((0.0, 0.0), 0.0)
    y = s((100.0, 100.0), 0.1)
    assert math.hypot(*y) == pytest.approx(9.0)
    with pytest.raises(ValueError):
        SlewLimiterND(0.0)
    with pytest.raises(ValueError):
        s((float("inf"), 0.0), 0.2)


# --------------------------------------------------------------------------
# 6. Integración en el ArmTracker
# --------------------------------------------------------------------------


class _SinPose:
    def process(self, _frame):
        return None

    def close(self):
        pass


def _tracker(**kwargs):
    reloj = {"t": 0.0}
    tracker = ArmTracker(pose_estimator=_SinPose(), clock=lambda: reloj["t"], **kwargs)
    return tracker, reloj


def test_el_mapeo_por_defecto_es_direccion_con_flexion_extendida():
    tracker, _ = _tracker()
    assert tracker.mapeo == "direccion"
    assert tracker.flexion_range == FLEXION_RANGE_EXTENDIDO
    assert tracker.reposo[Side.L] == (-55.0, 0.0)


def test_el_tracker_no_da_la_vuelta_al_acercar_las_manos():
    """El caso reportado por el operador, de punta a punta: manos hacia la
    cámara con ruido, publicando lo que sale de ``comando``."""
    tracker, reloj = _tracker()
    t = 0.0
    previo = None
    for h in _ruido_en_la_costura(n=300):
        t += 1 / 30
        reloj["t"] = t
        salida = tracker.map_direccion(Side.R, h, t)
        mando = tracker.comando(ArmAngles(armR=salida[0], armRtw=salida[1]), t)
        actual = np.array([mando.armR, mando.armRtw])
        if previo is not None and t > 1.5:     # tras la rampa desde el reposo
            assert np.max(np.abs(actual - previo)) < 6.0
            assert direccion(*actual)[2] > 0.8
        previo = actual
    assert tracker.cambios_de_rama[Side.R] == 0


def test_cambiar_de_mapeo_en_caliente_no_salta():
    tracker, reloj = _tracker()
    t = 0.0
    for _ in range(60):
        t += 1 / 30
        salida = tracker.map_direccion(Side.L, (0.3, -0.2, 0.9), t)
        mando = tracker.comando(ArmAngles(armL=salida[0], armLtw=salida[1]), t)
    antes = (mando.armL, mando.armLtw)
    tracker.mapeo = "articular"
    t += 1 / 30
    despues = tracker.comando(ArmAngles(), t)
    assert abs(despues.armL - antes[0]) <= 6.0 + 1e-6
    assert abs(despues.armLtw - antes[1]) <= 6.0 + 1e-6
    with pytest.raises(ValueError):
        tracker.mapeo = "otro"


def test_map_direccion_descarta_lo_no_finito():
    tracker, _ = _tracker()
    assert tracker.map_direccion(Side.L, (float("nan"), 0.0, 1.0), 0.0) is None
    assert tracker.map_direccion(Side.L, (0.0, 0.0), 0.0) is None
    assert tracker.map_direccion(Side.L, (0.0, 0.0, 0.0), 0.0) is None


@pytest.mark.parametrize("kwargs", [{"mapeo": "mixto"}, {"flexion_min": -200.0}, {"flexion_min": 0.0}])
def test_el_tracker_valida_los_parametros_nuevos(kwargs):
    with pytest.raises(ValueError):
        _tracker(**kwargs)


def test_la_configuracion_lee_el_mapeo_del_entorno():
    from telmeow_teleop.config import AppConfig

    cfg = AppConfig.from_env({"TELMEOW_ARM_MAPEO": "articular", "TELMEOW_ARM_FLEXION_MIN": "-90"})
    assert cfg.arm.mapeo == "articular"
    assert cfg.arm.flexion_min == -90.0
    assert AppConfig.from_env({}).arm.mapeo == "direccion"


# --------------------------------------------------------------------------
# 7. Contra la grabación real
# --------------------------------------------------------------------------

CSV_13 = RAIZ / "brazos_2026-09-13_descartada.csv"


@pytest.mark.skipif(not CSV_13.exists(), reason="falta la grabación del 2026-09-13")
def test_la_grabacion_del_13_sin_saltos_de_rama():
    """La sesión real por los dos mapeos: el articular tiene los saltos que se
    midieron, el de dirección ninguno, y la componente anterior (hacia la
    cámara) se sigue mucho mejor."""
    import sys

    sys.path.insert(0, str(RAIZ / "tools"))
    from comparar_mapeo_brazos import leer_sesion, metricas, reproducir

    sesion = leer_sesion(CSV_13)
    articular = reproducir(sesion, "articular")
    nuevo = reproducir(sesion, "direccion")
    for lado in (Side.L, Side.R):
        viejo_m = metricas(sesion, articular, lado)
        nuevo_m = metricas(sesion, nuevo, lado)
        assert viejo_m["saltos"] > 100
        assert nuevo_m["saltos"] == 0
        assert nuevo["cambios_de_rama"][lado] == 0
        assert nuevo_m["correlacion"][2] > viejo_m["correlacion"][2] + 0.2
        assert nuevo_m["temblor_p95"] < viejo_m["temblor_p95"]
