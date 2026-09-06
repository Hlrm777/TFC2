"""Límites articulares del robot Telmeow y recorte (clamping) de poses.

ORIGEN DE LOS DATOS
-------------------
Las tablas de este módulo provienen de un barrido con detección de colisión por
BVH sobre el modelo CAD del robot, con paso de 5° y refinado a 1° en los
extremos. Cada combinación (giro, balanceo) se evaluó buscando interferencia
entre el brazo y el tronco.

Los barridos se midieron **con los demás grados de libertad en posición home**.
Por lo tanto NO cubren combinaciones simultáneas de todos los DOF: describen el
espacio admisible de un brazo aislado, no el del robot completo en una postura
arbitraria. Es una limitación conocida del método y se declara como tal.

LA DEPENDENCIA BALANCEO-GIRO
----------------------------
El rango de balanceo admisible **depende del giro**. Con el brazo girado hacia
afuera se despeja del tronco y puede abducir mucho más que con el brazo en
posición neutra. Por eso el recorte del balanceo no puede hacerse contra un par
de constantes: hay que interpolar la tabla 2D en el giro actual.

Este módulo es la primera de dos capas de seguridad. El visor 3D vuelve a
clampar por BVH y señaliza colisión si una pose inválida llegara a pasar.

DEPENDENCIAS
------------
Solo biblioteca estándar, deliberadamente. Eso permite validar toda la lógica
de límites con ``pip install pytest && pytest``, sin instalar TensorFlow ni
MediaPipe.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "Side",
    "BalanceoPolicy",
    "AxisClamp",
    "ArmClamp",
    "HeadClamp",
    "GIRO_RANGE",
    "HEAD_PAN_RANGE",
    "HEAD_PAN_TARGET_RANGE",
    "HEAD_TILT_RANGE",
    "ARM_BALANCEO_1D",
    "clamp_scalar",
    "clamp_head",
    "clamp_arm",
    "arm_balanceo_range",
]


class Side(str, Enum):
    """Lado del brazo. Hereda de ``str`` para servir de clave de dict y de
    valor serializable a JSON sin conversión previa."""

    L = "L"
    R = "R"

    @classmethod
    def parse(cls, value: "Side | str") -> "Side":
        """Acepta 'L'/'R', 'left'/'right' e 'izq'/'der' (y sus formas largas),
        sin distinguir mayúsculas. Se valida en tiempo de ejecución porque el
        lado puede venir de la línea de comandos o de un mensaje MQTT."""
        if isinstance(value, cls):
            return value
        key = str(value).strip().lower()
        if key in _SIDE_ALIASES:
            return _SIDE_ALIASES[key]
        raise ValueError(
            f"lado no reconocido: {value!r}. "
            f"Valores válidos: {sorted(_SIDE_ALIASES)}"
        )


_SIDE_ALIASES: dict[str, Side] = {
    "l": Side.L,
    "left": Side.L,
    "izq": Side.L,
    "izquierdo": Side.L,
    "izquierda": Side.L,
    "r": Side.R,
    "right": Side.R,
    "der": Side.R,
    "derecho": Side.R,
    "derecha": Side.R,
}


class BalanceoPolicy(Enum):
    """Política de recorte del balanceo.

    TABLE
        Solo la tabla 2D interpolada en el giro actual. Es el comportamiento
        del sistema y el valor por defecto.
    INTERSECTION
        Además intersecta con ``ARM_BALANCEO_1D``. Política conservadora que se
        conserva para poder contrastarla en pruebas; NO la usa el pipeline.
    """

    TABLE = "table"
    INTERSECTION = "intersection"


# --------------------------------------------------------------------------
# Rangos 1D
# --------------------------------------------------------------------------

GIRO_RANGE: tuple[float, float] = (-90.0, 90.0)
HEAD_PAN_RANGE: tuple[float, float] = (-90.0, 90.0)
HEAD_TILT_RANGE: tuple[float, float] = (-39.0, 42.0)  # asimétrico

#: Rango objetivo del pan para el operador (§3.2.1).
#:
#: El operador teleopera viendo un monitor. Si girara tanto la cabeza como para
#: cubrir los ±90° mecánicos, dejaría de ver la pantalla. Con ±50° basta para
#: la mediación de lectura y el operador mantiene la pantalla en su campo de
#: visión. ``suggested_gains()`` usa este rango, no ``HEAD_PAN_RANGE``.
HEAD_PAN_TARGET_RANGE: tuple[float, float] = (-50.0, 50.0)

#: Balanceo admisible con el brazo en giro = 0°, como metadato documental.
#:
#: Los límites 1D de balanceo corresponden a la rebanada de la tabla 2D en
#: giro = 0°, medida con barrido refinado a 1°. La tabla 2D usa pasos de 5°,
#: por lo que a giro = 0° reporta (-55, +30) frente al (-55, +32) del barrido
#: fino: la diferencia está dentro de la resolución de medición y la tabla
#: resulta ligeramente conservadora. No son límites globales -- a giros
#: distintos de cero el brazo se despeja del tronco y el rango admisible
#: cambia, que es precisamente lo que el barrido 2D midió.
#:
#: Por eso ``clamp_arm`` NO los aplica bajo la política por defecto.
ARM_BALANCEO_1D: dict[Side, tuple[float, float]] = {
    Side.L: (-55.0, 32.0),
    Side.R: (-55.0, 33.0),
}


# --------------------------------------------------------------------------
# Tablas 2D: (giro, balanceo_min, balanceo_max), ordenadas por giro ascendente
# --------------------------------------------------------------------------

_TABLE_L: tuple[tuple[float, float, float], ...] = (
    (-90.0, -70.0, 5.0), (-85.0, -75.0, 5.0), (-80.0, -75.0, 5.0),
    (-75.0, -75.0, 5.0), (-70.0, -75.0, 5.0), (-65.0, -70.0, 5.0),
    (-60.0, -70.0, 5.0), (-55.0, -70.0, 10.0), (-50.0, -65.0, 10.0),
    (-45.0, -65.0, 10.0), (-40.0, -65.0, 10.0), (-35.0, -60.0, 10.0),
    (-30.0, -60.0, 15.0), (-25.0, -55.0, 15.0), (-20.0, -55.0, 20.0),
    (-15.0, -55.0, 30.0), (-10.0, -55.0, 30.0), (-5.0, -55.0, 30.0),
    (0.0, -55.0, 30.0), (5.0, -55.0, 30.0), (10.0, -45.0, 30.0),
    (15.0, -25.0, 35.0), (20.0, -20.0, 35.0), (25.0, -15.0, 35.0),
    (30.0, -10.0, 40.0), (35.0, -10.0, 40.0), (40.0, -10.0, 45.0),
    (45.0, -5.0, 50.0), (50.0, -5.0, 55.0), (55.0, -5.0, 60.0),
    (60.0, -5.0, 60.0), (65.0, -5.0, 65.0), (70.0, -5.0, 65.0),
    (75.0, -5.0, 70.0), (80.0, -5.0, 70.0), (85.0, -5.0, 70.0),
    (90.0, -5.0, 75.0),
)

_TABLE_R: tuple[tuple[float, float, float], ...] = (
    (-90.0, -70.0, 5.0), (-85.0, -75.0, 5.0), (-80.0, -75.0, 5.0),
    (-75.0, -75.0, 5.0), (-70.0, -75.0, 5.0), (-65.0, -70.0, 5.0),
    (-60.0, -70.0, 5.0), (-55.0, -70.0, 5.0), (-50.0, -65.0, 10.0),
    (-45.0, -65.0, 10.0), (-40.0, -65.0, 10.0), (-35.0, -60.0, 10.0),
    (-30.0, -60.0, 15.0), (-25.0, -55.0, 15.0), (-20.0, -55.0, 20.0),
    (-15.0, -55.0, 25.0), (-10.0, -55.0, 30.0), (-5.0, -55.0, 30.0),
    (0.0, -55.0, 30.0), (5.0, -55.0, 30.0), (10.0, -40.0, 30.0),
    (15.0, -25.0, 35.0), (20.0, -15.0, 35.0), (25.0, -15.0, 35.0),
    (30.0, -10.0, 40.0), (35.0, -10.0, 40.0), (40.0, -5.0, 45.0),
    (45.0, -5.0, 50.0), (50.0, -5.0, 55.0), (55.0, -5.0, 60.0),
    (60.0, -5.0, 60.0), (65.0, -5.0, 65.0), (70.0, -5.0, 65.0),
    (75.0, -5.0, 70.0), (80.0, -5.0, 70.0), (85.0, -5.0, 70.0),
    (90.0, -5.0, 70.0),
)

_ARM_TABLES: dict[Side, tuple[tuple[float, float, float], ...]] = {
    Side.L: _TABLE_L,
    Side.R: _TABLE_R,
}

# Claves de giro precalculadas para la búsqueda binaria. Se busca SIEMPRE sobre
# el giro, nunca sobre los valores de balanceo: bal_min no es monótona
# (-70 -> -75 -> -70 -> -55 -> -25 -> -5) y una búsqueda sobre ella daría
# resultados sin sentido.
_ARM_GIRO_KEYS: dict[Side, tuple[float, ...]] = {
    side: tuple(row[0] for row in table) for side, table in _ARM_TABLES.items()
}


# --------------------------------------------------------------------------
# Resultados
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AxisClamp:
    """Resultado del recorte de un eje.

    Lleva el rango aplicable en el instante del cálculo para que la interfaz
    del operador pueda dibujar la barra de posición sin recalcular nada. En el
    balanceo, ``lower``/``upper`` son los valores YA interpolados para el giro
    actual, de modo que la barra se estrecha y ensancha en vivo.
    """

    value: float   # valor ya recortado
    raw: float     # entrada original, antes de recortar
    lower: float   # límite inferior aplicable en este instante
    upper: float   # límite superior aplicable en este instante
    clamped: bool  # True si hubo recorte

    @property
    def fraction(self) -> float:
        """Posición del valor dentro del rango, normalizada a [0, 1].

        Un rango degenerado (lower == upper) devuelve 0.5 en vez de dividir
        entre cero.
        """
        span = self.upper - self.lower
        if span <= 0.0:
            return 0.5
        return min(1.0, max(0.0, (self.value - self.lower) / span))


@dataclass(frozen=True)
class ArmClamp:
    """Resultado del recorte de un brazo (balanceo + giro)."""

    balanceo: AxisClamp
    giro: AxisClamp
    lado: Side

    @property
    def clamped(self) -> bool:
        return self.balanceo.clamped or self.giro.clamped

    @property
    def as_tuple(self) -> tuple[float, float]:
        return (self.balanceo.value, self.giro.value)


@dataclass(frozen=True)
class HeadClamp:
    """Resultado del recorte de la cabeza (pan + tilt)."""

    pan: AxisClamp
    tilt: AxisClamp

    @property
    def clamped(self) -> bool:
        return self.pan.clamped or self.tilt.clamped

    @property
    def as_tuple(self) -> tuple[float, float]:
        return (self.pan.value, self.tilt.value)


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------


def _check_finite(value: float, name: str) -> float:
    """Rechaza NaN e infinitos.

    Los scripts legacy intentaban atajar NaN con ``if float(x) == float('nan')``,
    que es SIEMPRE falso porque NaN no es igual a nada, ni a sí mismo. Esas
    guardas nunca se ejecutaron, así que en la práctica los NaN sí llegaban a
    propagarse. Aquí se falla de forma explícita para que el bucle de captura
    descarte el frame en vez de mandar basura al robot.
    """
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} no es un número finito: {value!r}")
    return number


def clamp_scalar(value: float, lower: float, upper: float) -> AxisClamp:
    """Recorta un escalar a [lower, upper] y describe el resultado.

    Los bordes son inclusivos: un valor exactamente igual a un límite NO se
    marca como recortado.
    """
    clamped_value = min(upper, max(lower, value))
    return AxisClamp(
        value=clamped_value,
        raw=value,
        lower=lower,
        upper=upper,
        clamped=clamped_value != value,
    )


def arm_balanceo_range(
    giro: float,
    lado: Side | str,
    *,
    policy: BalanceoPolicy = BalanceoPolicy.TABLE,
) -> tuple[float, float]:
    """Rango de balanceo admisible para un giro dado, interpolado linealmente.

    El giro se recorta previamente a ``GIRO_RANGE``: la tabla solo está definida
    en [-90, 90] y extrapolar fuera de ahí no tiene sentido físico.

    ``bal_min`` y ``bal_max`` se interpolan por separado, porque son dos
    envolventes lineales a trozos independientes.

    Es pública para que la interfaz del operador pueda dibujar la envolvente
    completa muestreando el giro, sin construir un ``ArmClamp`` por muestra.
    """
    side = Side.parse(lado)
    giro = _check_finite(giro, "giro")
    giro = min(GIRO_RANGE[1], max(GIRO_RANGE[0], giro))

    keys = _ARM_GIRO_KEYS[side]
    table = _ARM_TABLES[side]

    index = bisect.bisect_left(keys, giro)
    if index < len(keys) and keys[index] == giro:
        # Nodo exacto: se devuelve tal cual, sin aritmética de punto flotante.
        lower, upper = table[index][1], table[index][2]
    else:
        low_giro, low_min, low_max = table[index - 1]
        high_giro, high_min, high_max = table[index]
        t = (giro - low_giro) / (high_giro - low_giro)
        lower = low_min + t * (high_min - low_min)
        upper = low_max + t * (high_max - low_max)

    if policy is BalanceoPolicy.INTERSECTION:
        one_d_min, one_d_max = ARM_BALANCEO_1D[side]
        lower = max(lower, one_d_min)
        upper = min(upper, one_d_max)

    return (lower, upper)


# --------------------------------------------------------------------------
# API principal
# --------------------------------------------------------------------------


def clamp_arm(
    balanceo: float,
    giro: float,
    lado: Side | str,
    *,
    policy: BalanceoPolicy = BalanceoPolicy.TABLE,
) -> ArmClamp:
    """Recorta la pose de un brazo a su espacio admisible.

    El ORDEN de las operaciones importa:

    1. Se recorta el GIRO con su límite duro de +-90°. Es un límite real del
       mecanismo y además es el índice de la tabla 2D: buscar con un giro fuera
       de rango no tendría sentido.
    2. Con el giro ya recortado se interpola la tabla 2D para obtener el rango
       de balanceo admisible EN ESE GIRO.
    3. Se recorta el BALANCEO con ese rango.

    Los límites 1D de ``ARM_BALANCEO_1D`` NO se aplican bajo la política por
    defecto: son la rebanada de la tabla en giro = 0°, no un límite global.
    Aplicarlos colapsaría el rango y le quitaría al brazo el recorrido extra
    que gana al girar y despejarse del tronco.

    Args:
        balanceo: abducción en el plano frontal, en grados. Positivo hacia
            afuera.
        giro: rotación axial del brazo, en grados.
        lado: 'L'/'R', 'left'/'right' o 'izq'/'der'.
        policy: ver ``BalanceoPolicy``.

    Returns:
        ArmClamp con ambos ejes recortados y el rango aplicado en cada uno.

    Raises:
        ValueError: si algún ángulo es NaN o infinito, o si el lado no se
            reconoce.
    """
    side = Side.parse(lado)
    balanceo = _check_finite(balanceo, "balanceo")
    giro = _check_finite(giro, "giro")

    # 1. El giro primero: es límite duro y además índice de la tabla.
    giro_clamp = clamp_scalar(giro, GIRO_RANGE[0], GIRO_RANGE[1])

    # 2. Rango de balanceo admisible en el giro ya recortado.
    lower, upper = arm_balanceo_range(giro_clamp.value, side, policy=policy)

    # 3. El balanceo, contra ese rango.
    balanceo_clamp = clamp_scalar(balanceo, lower, upper)

    return ArmClamp(balanceo=balanceo_clamp, giro=giro_clamp, lado=side)


def clamp_head(pan: float, tilt: float) -> HeadClamp:
    """Recorta la pose de la cabeza.

    Pan y tilt son independientes entre sí: el barrido no encontró acoplamiento
    entre ambos. El rango de tilt es asimétrico (-39°, +42°) porque el mentón
    del robot choca contra el pecho antes de que la nuca choque contra la
    espalda.

    Raises:
        ValueError: si algún ángulo es NaN o infinito.
    """
    pan = _check_finite(pan, "pan")
    tilt = _check_finite(tilt, "tilt")
    return HeadClamp(
        pan=clamp_scalar(pan, HEAD_PAN_RANGE[0], HEAD_PAN_RANGE[1]),
        tilt=clamp_scalar(tilt, HEAD_TILT_RANGE[0], HEAD_TILT_RANGE[1]),
    )
