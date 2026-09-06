"""Pruebas de los límites articulares.

El riesgo principal de este módulo es de TRANSCRIPCIÓN: las tablas 2D tienen 37
nodos por brazo y un dígito mal copiado no rompe nada visiblemente, solo deja
pasar poses que colisionan. Por eso se verifican los 74 nodos uno a uno contra
una copia independiente de la tabla.
"""

import math

import pytest

from telmeow_teleop.limits import (
    ARM_BALANCEO_1D,
    GIRO_RANGE,
    HEAD_PAN_RANGE,
    HEAD_PAN_TARGET_RANGE,
    HEAD_TILT_RANGE,
    BalanceoPolicy,
    Side,
    arm_balanceo_range,
    clamp_arm,
    clamp_head,
    clamp_scalar,
)

# Copia independiente de las tablas del plan maestro, para contrastar contra la
# transcripción del módulo.
TABLA_IZQ = {
    -90: (-70, 5), -85: (-75, 5), -80: (-75, 5), -75: (-75, 5), -70: (-75, 5),
    -65: (-70, 5), -60: (-70, 5), -55: (-70, 10), -50: (-65, 10), -45: (-65, 10),
    -40: (-65, 10), -35: (-60, 10), -30: (-60, 15), -25: (-55, 15), -20: (-55, 20),
    -15: (-55, 30), -10: (-55, 30), -5: (-55, 30), 0: (-55, 30), 5: (-55, 30),
    10: (-45, 30), 15: (-25, 35), 20: (-20, 35), 25: (-15, 35), 30: (-10, 40),
    35: (-10, 40), 40: (-10, 45), 45: (-5, 50), 50: (-5, 55), 55: (-5, 60),
    60: (-5, 60), 65: (-5, 65), 70: (-5, 65), 75: (-5, 70), 80: (-5, 70),
    85: (-5, 70), 90: (-5, 75),
}

TABLA_DER = {
    -90: (-70, 5), -85: (-75, 5), -80: (-75, 5), -75: (-75, 5), -70: (-75, 5),
    -65: (-70, 5), -60: (-70, 5), -55: (-70, 5), -50: (-65, 10), -45: (-65, 10),
    -40: (-65, 10), -35: (-60, 10), -30: (-60, 15), -25: (-55, 15), -20: (-55, 20),
    -15: (-55, 25), -10: (-55, 30), -5: (-55, 30), 0: (-55, 30), 5: (-55, 30),
    10: (-40, 30), 15: (-25, 35), 20: (-15, 35), 25: (-15, 35), 30: (-10, 40),
    35: (-10, 40), 40: (-5, 45), 45: (-5, 50), 50: (-5, 55), 55: (-5, 60),
    60: (-5, 60), 65: (-5, 65), 70: (-5, 65), 75: (-5, 70), 80: (-5, 70),
    85: (-5, 70), 90: (-5, 70),
}

TABLAS = {Side.L: TABLA_IZQ, Side.R: TABLA_DER}

#: Nodos donde ambos brazos difieren. La asimetría es intencional (proviene del
#: CAD, que no es perfectamente simétrico) y estos tests la fijan para que nadie
#: la "limpie" creyendo que es un error de copia.
NODOS_DISTINTOS = (-55, -15, 10, 20, 40, 90)
NODOS_IGUALES = tuple(g for g in TABLA_IZQ if g not in NODOS_DISTINTOS)


# --------------------------------------------------------------------------
# Los dos casos obligatorios del enunciado
# --------------------------------------------------------------------------


@pytest.mark.parametrize("lado", ["izq", "der"])
def test_balanceo_70_con_giro_0_se_recorta_a_55(lado):
    """Obligatorio: -70° de balanceo con el brazo sin girar se recorta a -55°."""
    resultado = clamp_arm(balanceo=-70.0, giro=0.0, lado=lado)
    assert resultado.balanceo.value == -55.0
    assert resultado.balanceo.clamped is True
    assert (resultado.balanceo.lower, resultado.balanceo.upper) == (-55.0, 30.0)


@pytest.mark.parametrize("lado", ["izq", "der"])
def test_balanceo_70_con_giro_menos_90_no_se_recorta(lado):
    """Obligatorio: con el brazo girado a -90° la tabla admite -70°."""
    resultado = clamp_arm(balanceo=-70.0, giro=-90.0, lado=lado)
    assert resultado.balanceo.value == -70.0
    assert resultado.balanceo.clamped is False
    assert (resultado.balanceo.lower, resultado.balanceo.upper) == (-70.0, 5.0)


def test_la_tabla_2d_manda_sobre_los_limites_1d():
    """Caso discriminante: separa esta implementación de la incorrecta.

    Con giro +90° la tabla del brazo izquierdo admite hasta +75° de balanceo.
    Si el resultado fuera +32 (el límite 1D), significaría que clamp_arm está
    aplicando ARM_BALANCEO_1D, que es justo lo que NO debe hacer: esos valores
    son la rebanada de la tabla en giro = 0°, no un límite global.
    """
    resultado = clamp_arm(balanceo=70.0, giro=90.0, lado="izq")
    assert resultado.balanceo.value == 70.0
    assert resultado.balanceo.clamped is False
    assert resultado.balanceo.value != ARM_BALANCEO_1D[Side.L][1]


# --------------------------------------------------------------------------
# Transcripción de las tablas
# --------------------------------------------------------------------------


@pytest.mark.parametrize("lado", [Side.L, Side.R])
@pytest.mark.parametrize("giro", sorted(TABLA_IZQ))
def test_nodos_de_la_tabla(lado, giro):
    assert arm_balanceo_range(float(giro), lado) == TABLAS[lado][giro]


@pytest.mark.parametrize("giro", NODOS_DISTINTOS)
def test_nodos_donde_los_brazos_difieren(giro):
    assert arm_balanceo_range(float(giro), Side.L) != arm_balanceo_range(float(giro), Side.R)


@pytest.mark.parametrize("giro", NODOS_IGUALES)
def test_nodos_donde_los_brazos_coinciden(giro):
    assert arm_balanceo_range(float(giro), Side.L) == arm_balanceo_range(float(giro), Side.R)


def test_las_tablas_cubren_todo_el_rango_en_pasos_de_5():
    assert sorted(TABLA_IZQ) == list(range(-90, 95, 5))
    assert sorted(TABLA_DER) == list(range(-90, 95, 5))


# --------------------------------------------------------------------------
# Interpolación
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "giro, lado, esperado",
    [
        # Punto medio entre 10:(-45,30) y 15:(-25,35) -> izquierdo
        (12.5, Side.L, (-35.0, 32.5)),
        # Punto medio entre 10:(-40,30) y 15:(-25,35) -> derecho
        (12.5, Side.R, (-32.5, 32.5)),
        # Cruza una divergencia L/R: -55 difiere entre brazos
        (-52.5, Side.L, (-67.5, 10.0)),
        (-52.5, Side.R, (-67.5, 7.5)),
        # Zona plana: interpolar no debe mover nada
        (2.5, Side.L, (-55.0, 30.0)),
        (2.5, Side.R, (-55.0, 30.0)),
    ],
)
def test_interpolacion_lineal(giro, lado, esperado):
    resultado = arm_balanceo_range(giro, lado)
    assert resultado == pytest.approx(esperado)


def test_interpolacion_es_continua_en_los_nodos():
    """Acercarse a un nodo por la izquierda debe converger a su valor exacto."""
    for giro in (-55.0, 10.0, 40.0):
        cerca = arm_balanceo_range(giro - 1e-9, Side.L)
        exacto = arm_balanceo_range(giro, Side.L)
        assert cerca == pytest.approx(exacto, abs=1e-6)


# --------------------------------------------------------------------------
# Recorte del giro
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "giro_entrada, giro_esperado",
    [(120.0, 90.0), (-150.0, -90.0), (90.0, 90.0), (-90.0, -90.0)],
)
def test_el_giro_se_recorta_antes_de_consultar_la_tabla(giro_entrada, giro_esperado):
    resultado = clamp_arm(balanceo=0.0, giro=giro_entrada, lado="izq")
    assert resultado.giro.value == giro_esperado
    assert resultado.giro.clamped is (giro_entrada != giro_esperado)
    # El rango de balanceo debe ser el del extremo, no una extrapolación.
    assert (resultado.balanceo.lower, resultado.balanceo.upper) == TABLA_IZQ[int(giro_esperado)]


def test_el_giro_reporta_su_propio_rango():
    resultado = clamp_arm(balanceo=0.0, giro=200.0, lado="der")
    assert (resultado.giro.lower, resultado.giro.upper) == GIRO_RANGE


def test_clamped_agregado():
    dentro = clamp_arm(balanceo=0.0, giro=0.0, lado="izq")
    assert dentro.clamped is False
    fuera = clamp_arm(balanceo=0.0, giro=200.0, lado="izq")
    assert fuera.clamped is True


# --------------------------------------------------------------------------
# Sin recorte / idempotencia
# --------------------------------------------------------------------------


def test_valor_interior_no_se_toca():
    resultado = clamp_arm(balanceo=10.0, giro=0.0, lado="izq")
    assert resultado.balanceo.value == resultado.balanceo.raw == 10.0
    assert resultado.balanceo.clamped is False


@pytest.mark.parametrize("lado", [Side.L, Side.R])
@pytest.mark.parametrize("balanceo", [-120.0, -55.0, 0.0, 37.0, 120.0])
@pytest.mark.parametrize("giro", [-130.0, -37.5, 0.0, 12.5, 130.0])
def test_idempotencia(balanceo, giro, lado):
    """Recortar una pose ya recortada no debe cambiarla."""
    una_vez = clamp_arm(balanceo, giro, lado)
    dos_veces = clamp_arm(*una_vez.as_tuple, lado)
    assert dos_veces.as_tuple == pytest.approx(una_vez.as_tuple)
    assert dos_veces.clamped is False


# --------------------------------------------------------------------------
# Contrato con la interfaz del operador (PROMPT 7)
# --------------------------------------------------------------------------


def test_fraction_recorre_el_rango():
    assert clamp_scalar(-55.0, -55.0, 30.0).fraction == 0.0
    assert clamp_scalar(30.0, -55.0, 30.0).fraction == 1.0
    assert clamp_scalar(-12.5, -55.0, 30.0).fraction == pytest.approx(0.5)


def test_fraction_con_rango_degenerado_no_divide_entre_cero():
    assert clamp_scalar(5.0, 5.0, 5.0).fraction == 0.5


def test_el_rango_del_balanceo_sigue_al_giro():
    """La barra del HUD debe estrecharse y ensancharse según el giro."""
    estrecho = clamp_arm(0.0, 0.0, "izq").balanceo
    ancho = clamp_arm(0.0, -90.0, "izq").balanceo
    assert (ancho.upper - ancho.lower) < (estrecho.upper - estrecho.lower)


# --------------------------------------------------------------------------
# Lado
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "alias, esperado",
    [
        ("L", Side.L), ("l", Side.L), ("left", Side.L), ("LEFT", Side.L),
        ("izq", Side.L), ("izquierdo", Side.L), ("  izq  ", Side.L),
        ("R", Side.R), ("right", Side.R), ("der", Side.R), ("derecha", Side.R),
        (Side.L, Side.L), (Side.R, Side.R),
    ],
)
def test_side_parse(alias, esperado):
    assert Side.parse(alias) is esperado


@pytest.mark.parametrize("alias", ["X", "", "ambos", "1"])
def test_side_parse_rechaza_lo_desconocido(alias):
    with pytest.raises(ValueError):
        Side.parse(alias)


def test_side_es_serializable_como_str():
    assert Side.L == "L"
    assert f"{Side.R.value}" == "R"


# --------------------------------------------------------------------------
# NaN e infinitos
# --------------------------------------------------------------------------


@pytest.mark.parametrize("malo", [float("nan"), float("inf"), float("-inf")])
def test_clamp_arm_rechaza_no_finitos(malo):
    with pytest.raises(ValueError):
        clamp_arm(balanceo=malo, giro=0.0, lado="izq")
    with pytest.raises(ValueError):
        clamp_arm(balanceo=0.0, giro=malo, lado="izq")


@pytest.mark.parametrize("malo", [float("nan"), float("inf")])
def test_clamp_head_rechaza_no_finitos(malo):
    with pytest.raises(ValueError):
        clamp_head(pan=malo, tilt=0.0)
    with pytest.raises(ValueError):
        clamp_head(pan=0.0, tilt=malo)


def test_la_guarda_legacy_de_nan_era_inservible():
    """Documenta el bug 2 del plan maestro: NaN nunca es igual a sí mismo, así
    que `if float(x) == float('nan')` jamás se cumplía."""
    assert (float("nan") == float("nan")) is False
    assert math.isnan(float("nan")) is True


# --------------------------------------------------------------------------
# Política alternativa
# --------------------------------------------------------------------------


def test_politica_interseccion_si_aplica_los_limites_1d():
    tabla = clamp_arm(-70.0, -90.0, "izq", policy=BalanceoPolicy.TABLE)
    interseccion = clamp_arm(-70.0, -90.0, "izq", policy=BalanceoPolicy.INTERSECTION)
    assert tabla.balanceo.value == -70.0
    assert interseccion.balanceo.value == -55.0
    assert tabla.balanceo.value != interseccion.balanceo.value


def test_la_politica_por_defecto_es_la_tabla():
    por_defecto = clamp_arm(-70.0, -90.0, "izq")
    explicita = clamp_arm(-70.0, -90.0, "izq", policy=BalanceoPolicy.TABLE)
    assert por_defecto.balanceo.value == explicita.balanceo.value


# --------------------------------------------------------------------------
# Cabeza
# --------------------------------------------------------------------------


def test_cabeza_dentro_de_rango():
    resultado = clamp_head(pan=0.0, tilt=0.0)
    assert resultado.as_tuple == (0.0, 0.0)
    assert resultado.clamped is False


@pytest.mark.parametrize(
    "pan_entrada, pan_esperado", [(120.0, 90.0), (-120.0, -90.0), (45.0, 45.0)]
)
def test_cabeza_pan(pan_entrada, pan_esperado):
    resultado = clamp_head(pan=pan_entrada, tilt=0.0)
    assert resultado.pan.value == pan_esperado


@pytest.mark.parametrize(
    "tilt_entrada, tilt_esperado, recortado",
    [
        (50.0, 42.0, True),
        (-50.0, -39.0, True),
        (42.0, 42.0, False),   # borde inclusivo
        (-39.0, -39.0, False),  # borde inclusivo
    ],
)
def test_cabeza_tilt_es_asimetrico(tilt_entrada, tilt_esperado, recortado):
    resultado = clamp_head(pan=0.0, tilt=tilt_entrada)
    assert resultado.tilt.value == tilt_esperado
    assert resultado.tilt.clamped is recortado


def test_cabeza_ejes_independientes():
    resultado = clamp_head(pan=200.0, tilt=10.0)
    assert resultado.pan.clamped is True
    assert resultado.tilt.value == 10.0
    assert resultado.tilt.clamped is False


def test_cabeza_fraction_refleja_la_asimetria_del_tilt():
    """Con tilt = 0 la fracción NO es 0.5, porque el rango es (-39, +42).

    Si alguien "simetrizara" el rango por descuido, este test lo detecta.
    """
    resultado = clamp_head(pan=0.0, tilt=0.0)
    assert resultado.pan.fraction == pytest.approx(0.5)
    assert resultado.tilt.fraction == pytest.approx(39.0 / 81.0)
    assert resultado.tilt.fraction != pytest.approx(0.5)


def test_rangos_de_cabeza_declarados():
    assert HEAD_PAN_RANGE == (-90.0, 90.0)
    assert HEAD_TILT_RANGE == (-39.0, 42.0)
    assert HEAD_PAN_TARGET_RANGE == (-50.0, 50.0)


def test_el_objetivo_del_pan_cabe_dentro_del_rango_mecanico():
    """Son cosas distintas: el objetivo es de CALIBRACIÓN, el rango es el tope.

    El objetivo puede ser más estrecho —lo es, porque al operador lo acota su
    campo de visión sobre el monitor y no el cuello del robot (§3.2.1)— pero
    nunca puede salirse del rango mecánico: eso pediría al robot un ángulo que
    no existe.
    """
    assert HEAD_PAN_RANGE[0] <= HEAD_PAN_TARGET_RANGE[0]
    assert HEAD_PAN_TARGET_RANGE[1] <= HEAD_PAN_RANGE[1]


def test_el_objetivo_del_pan_no_recorta_nada():
    """`clamp_head` sigue recortando a ±90°. El objetivo no es un límite nuevo.

    Si alguien lo confundiera y clampara a ±50°, el robot perdería recorrido que
    sí tiene.
    """
    assert clamp_head(pan=70.0, tilt=0.0).pan.value == pytest.approx(70.0)
    assert clamp_head(pan=70.0, tilt=0.0).clamped is False
