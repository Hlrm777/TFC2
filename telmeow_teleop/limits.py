"""Límites articulares del robot Telmeow y recorte (clamping) de poses.

LOS DOS GRADOS DE LIBERTAD DEL HOMBRO, CON SU NOMBRE ANATÓMICO
--------------------------------------------------------------
El hombro del robot es un **cardán de dos grados de libertad** que orienta un
brazo rígido de un solo eslabón (``Brazo_Der_W.stl`` / ``Brazo_Izq_W.stl``, sin
codo). Leídos de los ejes del rig en ``telmeow_viewer/telmeow_viewer.html``:

===============  =====================  ====================================
Canal MQTT       Eje del servo          Movimiento
===============  =====================  ====================================
``armL``/``armR``   antero-posterior    **abducción (+) / aducción (−)**,
                                        en el plano frontal
``armLtw``/``armRtw`` latero-lateral    **flexión (+) / extensión (−)**,
                                        en el plano sagital
===============  =====================  ====================================

**El robot NO tiene rotación interna/externa** (rotación axial del húmero). No
hay un tercer grado de libertad en el hombro, así que esa componente del gesto
del operador no se reproduce y se declara como limitación (H28 del plan
maestro).

El sufijo ``tw`` de ``armLtw`` significa *twist* y por tanto miente: nombra como
torsión un canal que es flexión. Los nombres de campo del JSON no se pueden
cambiar sin romper el contrato con el visor, pero el vocabulario de este módulo
sí es el correcto.

ORIGEN DE LOS DATOS
-------------------
Las tablas de este módulo provienen de un barrido con detección de colisión por
BVH sobre el modelo CAD del robot, con paso de 5° y refinado a 1° en los
extremos. Cada combinación (flexión, abducción) se evaluó buscando interferencia
entre el brazo y el tronco.

Los barridos se midieron **con los demás grados de libertad en posición home**.
Por lo tanto NO cubren combinaciones simultáneas de todos los DOF: describen el
espacio admisible de un brazo aislado, no el del robot completo en una postura
arbitraria. Es una limitación conocida del método y se declara como tal.

LA DEPENDENCIA ABDUCCIÓN-FLEXIÓN
--------------------------------
El rango de abducción admisible **depende de la flexión**. Con el brazo flexado
hacia adelante se despeja del tronco y puede abducir mucho más que con el brazo
colgando en posición neutra. Por eso el recorte de la abducción no puede hacerse
contra un par de constantes: hay que interpolar la tabla 2D en la flexión
actual.

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
    "AbduccionPolicy",
    "AxisClamp",
    "ArmClamp",
    "HeadClamp",
    "FLEXION_RANGE",
    "FLEXION_RANGE_EXTENDIDO",
    "HEAD_PAN_RANGE",
    "HEAD_PAN_TARGET_RANGE",
    "HEAD_TILT_RANGE",
    "ARM_ABDUCCION_1D",
    "clamp_scalar",
    "clamp_head",
    "clamp_arm",
    "arm_abduccion_range",
    "arm_abduccion_range_extendido",
]


class Side(str, Enum):
    """Lado del brazo. Hereda de ``str`` para servir de clave de dict y de
    valor serializable a JSON sin conversión previa."""

    L = "L"
    R = "R"

    @property
    def opposite(self) -> "Side":
        """El otro lado.

        Vive aquí y no en quien la use porque el arm tracker tiene que cruzar
        lados dos veces —del espejo de la cámara y de la política de mapeo— y
        una función de cruce escrita a mano en cada sitio es exactamente la
        clase de duplicado que acaba desincronizándose.
        """
        return Side.R if self is Side.L else Side.L

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


class AbduccionPolicy(Enum):
    """Política de recorte de la abducción.

    TABLE
        Solo la tabla 2D interpolada en la flexión actual. Es el comportamiento
        del sistema y el valor por defecto.
    INTERSECTION
        Además intersecta con ``ARM_ABDUCCION_1D``. Política conservadora que se
        conserva para poder contrastarla en pruebas; NO la usa el pipeline.
    """

    TABLE = "table"
    INTERSECTION = "intersection"


# --------------------------------------------------------------------------
# Rangos 1D
# --------------------------------------------------------------------------

FLEXION_RANGE: tuple[float, float] = (-90.0, 90.0)

#: Rango de flexión EXTENDIDO, el que usa el retargeting por dirección (P5.3).
#:
#: ±90° **no es un límite del mecanismo**: es el rango de los sliders del visor,
#: y el barrido 2D solo exploró dentro de él. El servo previsto (Dynamixel
#: XL430-W250-T) gira 360° en modo posición. Lo que sí impone ±90° es una
#: COSTURA en «brazo al frente horizontal»: para cruzarla el brazo tiene que
#: pasar por la pose en cruz (ver ``retarget.py``). Extender la flexión hasta
#: −180° permite cubrir todo el hemisferio frontal con una sola rama de la
#: cinemática inversa, sin esa costura.
#:
#: Verificado el 2026-09-27 con los detectores del visor: la zona [−180, −95]
#: está barrida (``_TABLE_EXT_L``/``_R``), y la unión y el motor que viaja con el
#: brazo no chocan con el tronco en ninguna pose admisible.
#:
#: El cable del hombro NO está modelado en el CAD, así que no se verifica: se
#: declara como supuesto de diseño. El mapeo por dirección solo usa flexión en
#: [−180, 0], o sea 180° de recorrido, el mismo que el ±90° original desplazado
#: 90°; montado con el cable relajado en flexión −90° (brazo al frente), la
#: torsión queda en ±90°, igual que en el diseño original.
FLEXION_RANGE_EXTENDIDO: tuple[float, float] = (-180.0, 90.0)
HEAD_PAN_RANGE: tuple[float, float] = (-90.0, 90.0)
HEAD_TILT_RANGE: tuple[float, float] = (-39.0, 42.0)  # asimétrico

#: Rango objetivo del pan para el operador (§3.2.1).
#:
#: El operador teleopera viendo un monitor. Si girara tanto la cabeza como para
#: cubrir los ±90° mecánicos, dejaría de ver la pantalla. Con ±50° basta para
#: la mediación de lectura y el operador mantiene la pantalla en su campo de
#: visión. ``suggested_gains()`` usa este rango, no ``HEAD_PAN_RANGE``.
HEAD_PAN_TARGET_RANGE: tuple[float, float] = (-50.0, 50.0)

#: Abducción admisible con el brazo en flexión = 0°, como metadato documental.
#:
#: Los límites 1D de abducción corresponden a la rebanada de la tabla 2D en
#: flexión = 0°, medida con barrido refinado a 1°. La tabla 2D usa pasos de 5°,
#: por lo que a flexión = 0° reporta (-55, +30) frente al (-55, +32) del barrido
#: fino: la diferencia está dentro de la resolución de medición y la tabla
#: resulta ligeramente conservadora. No son límites globales -- a flexiones
#: distintas de cero el brazo se despeja del tronco y el rango admisible
#: cambia, que es precisamente lo que el barrido 2D midió.
#:
#: Por eso ``clamp_arm`` NO los aplica bajo la política por defecto.
ARM_ABDUCCION_1D: dict[Side, tuple[float, float]] = {
    Side.L: (-55.0, 32.0),
    Side.R: (-55.0, 33.0),
}


# --------------------------------------------------------------------------
# Tablas 2D: (flexion, abduccion_min, abduccion_max), por flexion ascendente
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

# Claves de flexión precalculadas para la búsqueda binaria. Se busca SIEMPRE sobre
# la flexión, nunca sobre los valores de abducción: bal_min no es monótona
# (-70 -> -75 -> -70 -> -55 -> -25 -> -5) y una búsqueda sobre ella daría
# resultados sin sentido.
_ARM_FLEXION_KEYS: dict[Side, tuple[float, ...]] = {
    side: tuple(row[0] for row in table) for side, table in _ARM_TABLES.items()
}


# --------------------------------------------------------------------------
# Zona de flexión extendida [-180, -95], MEDIDA (P5.3, 2026-09-27)
# --------------------------------------------------------------------------
#
# Mismo criterio y mismo procedimiento que las tablas de arriba: el `probe()`
# del visor (plano posterior + BVH brazo-tronco), paso 5°, rango contiguo
# alrededor de abducción 0 y demás DOF en home. El arnés que lo corrió
# reprodujo las 37 filas de las tablas de arriba SIN UNA SOLA DIFERENCIA en los
# dos brazos, así que estas filas son comparables con ellas.
#
# No son el reflejo exacto de la tabla (``(a, f) ≡ (−a, f ± 180)``): coinciden
# en 12 de 18 filas en el brazo izquierdo y en 9 en el derecho, y en el resto
# difieren en un paso de 5° (dos, una vez, en el máximo del derecho a −170), en
# los dos sentidos. El brazo solo es simétrico a ±2 mm alrededor de su eje, y
# con pasos de 5° eso basta para mover una fila. En
# el límite que usa el mapeo por dirección (el mínimo) el reflejo prometía 5° de
# más en 3 filas del izquierdo y 2 del derecho, más que el margen de 2° de
# ``retarget``; por eso manda lo medido y no el reflejo.

_TABLE_EXT_L: tuple[tuple[float, float, float], ...] = (
    (-180.0, -30.0, 55.0), (-175.0, -30.0, 55.0), (-170.0, -30.0, 45.0),
    (-165.0, -30.0, 30.0), (-160.0, -35.0, 20.0), (-155.0, -35.0, 15.0),
    (-150.0, -35.0, 15.0), (-145.0, -40.0, 10.0), (-140.0, -45.0, 10.0),
    (-135.0, -50.0, 10.0), (-130.0, -55.0, 10.0), (-125.0, -60.0, 10.0),
    (-120.0, -60.0, 5.0), (-115.0, -65.0, 5.0), (-110.0, -65.0, 5.0),
    (-105.0, -65.0, 5.0), (-100.0, -70.0, 5.0), (-95.0, -70.0, 5.0),
)

_TABLE_EXT_R: tuple[tuple[float, float, float], ...] = (
    (-180.0, -30.0, 55.0), (-175.0, -30.0, 55.0), (-170.0, -30.0, 50.0),
    (-165.0, -30.0, 30.0), (-160.0, -35.0, 20.0), (-155.0, -35.0, 15.0),
    (-150.0, -40.0, 15.0), (-145.0, -40.0, 10.0), (-140.0, -45.0, 10.0),
    (-135.0, -50.0, 10.0), (-130.0, -55.0, 10.0), (-125.0, -60.0, 10.0),
    (-120.0, -60.0, 5.0), (-115.0, -65.0, 5.0), (-110.0, -65.0, 5.0),
    (-105.0, -65.0, 5.0), (-100.0, -70.0, 5.0), (-95.0, -70.0, 5.0),
)

#: Tabla completa de [-180, 90]: la zona medida en el P5.3 seguida de la del
#: barrido de agosto. Es la que usa ``arm_abduccion_range_extendido``.
_ARM_TABLES_EXT: dict[Side, tuple[tuple[float, float, float], ...]] = {
    Side.L: _TABLE_EXT_L + _TABLE_L,
    Side.R: _TABLE_EXT_R + _TABLE_R,
}
_ARM_FLEXION_KEYS_EXT: dict[Side, tuple[float, ...]] = {
    side: tuple(row[0] for row in table) for side, table in _ARM_TABLES_EXT.items()
}


# --------------------------------------------------------------------------
# Resultados
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AxisClamp:
    """Resultado del recorte de un eje.

    Lleva el rango aplicable en el instante del cálculo para que la interfaz
    del operador pueda dibujar la barra de posición sin recalcular nada. En el
    abducción, ``lower``/``upper`` son los valores YA interpolados para la flexión
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
    """Resultado del recorte de un brazo (abducción + flexión)."""

    abduccion: AxisClamp
    flexion: AxisClamp
    lado: Side

    @property
    def clamped(self) -> bool:
        return self.abduccion.clamped or self.flexion.clamped

    @property
    def as_tuple(self) -> tuple[float, float]:
        return (self.abduccion.value, self.flexion.value)


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


def arm_abduccion_range(
    flexion: float,
    lado: Side | str,
    *,
    policy: AbduccionPolicy = AbduccionPolicy.TABLE,
) -> tuple[float, float]:
    """Rango de abducción admisible para una flexión dado, interpolado linealmente.

    El flexion se recorta previamente a ``FLEXION_RANGE``: la tabla solo está definida
    en [-90, 90] y extrapolar fuera de ahí no tiene sentido físico.

    ``bal_min`` y ``bal_max`` se interpolan por separado, porque son dos
    envolventes lineales a trozos independientes.

    Es pública para que la interfaz del operador pueda dibujar la envolvente
    completa muestreando la flexión, sin construir un ``ArmClamp`` por muestra.
    """
    side = Side.parse(lado)
    flexion = _check_finite(flexion, "flexion")
    flexion = min(FLEXION_RANGE[1], max(FLEXION_RANGE[0], flexion))

    keys = _ARM_FLEXION_KEYS[side]
    table = _ARM_TABLES[side]

    index = bisect.bisect_left(keys, flexion)
    if index < len(keys) and keys[index] == flexion:
        # Nodo exacto: se devuelve tal cual, sin aritmética de punto flotante.
        lower, upper = table[index][1], table[index][2]
    else:
        low_flex, low_min, low_max = table[index - 1]
        high_flex, high_min, high_max = table[index]
        t = (flexion - low_flex) / (high_flex - low_flex)
        lower = low_min + t * (high_min - low_min)
        upper = low_max + t * (high_max - low_max)

    if policy is AbduccionPolicy.INTERSECTION:
        one_d_min, one_d_max = ARM_ABDUCCION_1D[side]
        lower = max(lower, one_d_min)
        upper = min(upper, one_d_max)

    return (lower, upper)


def arm_abduccion_range_extendido(
    flexion: float,
    lado: Side | str,
    *,
    policy: AbduccionPolicy = AbduccionPolicy.TABLE,
) -> tuple[float, float]:
    """Como ``arm_abduccion_range`` pero válida para flexiones en [−180, 180].

    - **[−180, 90]: tabla medida.** La zona [−180, −95] se barrió en el P5.3 con
      el mismo criterio que la de agosto (ver ``_TABLE_EXT_L``); entre −95 y −90
      se interpola entre las dos.
    - **(90, 180]: reflejo, sin medir.** ``(a, f)`` y ``(−a, f − 180)`` ponen el
      brazo en la misma dirección, girado 180° sobre su propio eje, así que se
      usa ``rango(f) = (−max(f − 180), −min(f − 180))``. Con
      ``FLEXION_RANGE_EXTENDIDO`` esa zona es inalcanzable (el tope es +90); se
      conserva para que la función esté definida en todo el círculo. En la
      zona que sí se midió, el reflejo acertó en 9-12 de 18 filas y falló por
      uno o dos pasos de 5° en el resto: si alguna vez se usa esta zona, hay
      que barrerla.
    """
    side = Side.parse(lado)
    flexion = _check_finite(flexion, "flexion")
    flexion = min(180.0, max(-180.0, flexion))
    if flexion > FLEXION_RANGE[1]:
        lower, upper = arm_abduccion_range(flexion - 180.0, side, policy=policy)
        return (-upper, -lower)

    keys = _ARM_FLEXION_KEYS_EXT[side]
    table = _ARM_TABLES_EXT[side]
    index = bisect.bisect_left(keys, flexion)
    if index < len(keys) and keys[index] == flexion:
        lower, upper = table[index][1], table[index][2]
    else:
        low_flex, low_min, low_max = table[index - 1]
        high_flex, high_min, high_max = table[index]
        t = (flexion - low_flex) / (high_flex - low_flex)
        lower = low_min + t * (high_min - low_min)
        upper = low_max + t * (high_max - low_max)

    if policy is AbduccionPolicy.INTERSECTION:
        one_d_min, one_d_max = ARM_ABDUCCION_1D[side]
        lower = max(lower, one_d_min)
        upper = min(upper, one_d_max)
    return (lower, upper)


# --------------------------------------------------------------------------
# API principal
# --------------------------------------------------------------------------


def clamp_arm(
    abduccion: float,
    flexion: float,
    lado: Side | str,
    *,
    policy: AbduccionPolicy = AbduccionPolicy.TABLE,
    flexion_range: tuple[float, float] = FLEXION_RANGE,
) -> ArmClamp:
    """Recorta la pose de un brazo a su espacio admisible.

    El ORDEN de las operaciones importa:

    1. Se recorta la FLEXION con su límite duro de +-90°. Es un límite real del
       mecanismo y además es el índice de la tabla 2D: buscar con una flexión fuera
       de rango no tendría sentido.
    2. Con la flexión ya recortado se interpola la tabla 2D para obtener el rango
       de abducción admisible EN ESA FLEXION.
    3. Se recorta el BALANCEO con ese rango.

    Los límites 1D de ``ARM_ABDUCCION_1D`` NO se aplican bajo la política por
    defecto: son la rebanada de la tabla en flexión = 0°, no un límite global.
    Aplicarlos colapsaría el rango y le quitaría al brazo el recorrido extra
    que gana al girar y despejarse del tronco.

    Args:
        abduccion: abducción en el plano frontal, en grados. Positivo hacia
            afuera.
        flexion: rotación axial del brazo, en grados.
        lado: 'L'/'R', 'left'/'right' o 'izq'/'der'.
        policy: ver ``AbduccionPolicy``.
        flexion_range: límite duro de la flexión. Por defecto ±90°, que es lo
            que el barrido cubre; el retargeting por dirección pasa
            ``FLEXION_RANGE_EXTENDIDO`` y fuera de ±90° el rango de abducción
            sale de ``arm_abduccion_range_extendido``.

    Returns:
        ArmClamp con ambos ejes recortados y el rango aplicado en cada uno.

    Raises:
        ValueError: si algún ángulo es NaN o infinito, o si el lado no se
            reconoce.
    """
    side = Side.parse(lado)
    abduccion = _check_finite(abduccion, "abduccion")
    flexion = _check_finite(flexion, "flexion")

    # 1. El flexion primero: es límite duro y además índice de la tabla.
    flexion_clamp = clamp_scalar(flexion, flexion_range[0], flexion_range[1])

    # 2. Rango de abducción admisible en la flexión ya recortado.
    lower, upper = arm_abduccion_range_extendido(
        flexion_clamp.value, side, policy=policy
    )

    # 3. El abduccion, contra ese rango.
    abduccion_clamp = clamp_scalar(abduccion, lower, upper)

    return ArmClamp(abduccion=abduccion_clamp, flexion=flexion_clamp, lado=side)


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
