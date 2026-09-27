"""Estimación de la pose de los brazos del operador y mapeo a los DOF del robot.

Reemplaza a ``legacy/Hands Tracking.py``, que tenía cuatro defectos concretos.
Los cuatro están corregidos aquí y cada uno tiene su prueba de regresión:

BUG 1
    El bloque de impresión IZQUIERDO usaba ``right_red_angle`` y
    ``right_green_angle``: los ángulos del brazo izquierdo se calculaban y se
    descartaban. Aquí hay **una sola función parametrizada por lado**, así que
    no existe el sitio donde equivocarse.
BUG 2
    Las guardas ``if float(x) == float('nan')`` nunca se cumplen, porque NaN no
    es igual a nada, ni a sí mismo. Aquí la comprobación la hace
    ``limits.clamp_arm``, que lanza ``ValueError``, y el brazo afectado se
    descarta reutilizando su último valor válido.
BUG 3
    ``arccos(arm_length / max_arm_length)`` devuelve NaN en cuanto el cociente
    supera 1, cosa que ocurre siempre que la muñeca se aleja del hombro más que
    la distancia hombro-cadera. Aquí no hay cocientes de longitudes: los ángulos
    salen de productos escalares entre **vectores unitarios**, acotados a
    [-1, 1] antes del arcoseno.
BUG 4
    ``angle_green`` medía la muñeca respecto a la **cadera**, que no es un
    ángulo de la articulación del hombro. Aquí el pivote es siempre el hombro.

LOS DOS GRADOS DE LIBERTAD, CON SU NOMBRE ANATOMICO
---------------------------------------------------
El hombro del robot es un **cardán de dos grados de libertad** que orienta un
brazo rígido de un solo eslabón, sin codo. Leídos de los ejes del rig en
``telmeow_viewer/telmeow_viewer.html`` (H28 del plan maestro):

- ``armL`` / ``armR``      -> **abducción (+) / aducción (−)**, plano frontal.
- ``armLtw`` / ``armRtw``  -> **flexión (+) / extensión (−)**, plano sagital.

**El robot no tiene rotación interna/externa** (rotación axial del húmero). El
enunciado original del PROMPT 5 pedía estimar esa rotación desde el vector
codo→muñeca y enviarla al canal ``tw``; eso describe un grado de libertad que el
robot no tiene, y hacerlo movería el brazo hacia adelante en respuesta a que el
operador rota el antebrazo. El sufijo ``tw`` (*twist*) es vocabulario heredado
del visor y no se puede cambiar sin romper el contrato MQTT.

Consecuencia para el documento: la rotación axial del brazo del operador **no se
reproduce**, y se declara. La limitación que sí queda, y que sí es medible, es
otra: la abducción vive en el **plano de imagen** mientras que la flexión
depende de la **coordenada de profundidad** de BlazePose, que es su componente
débil en monocular. Por eso la flexión es el canal menos fiable de los cuatro.

EL VECTOR QUE ORIENTA EL BRAZO
------------------------------
``target="mano"`` (por defecto) usa el centroide de los cuatro landmarks de la
mano que BlazePose entrega —muñeca, meñique, índice y pulgar—; ``target="codo"``
usa el vector húmero. Ver ``config.ArmConfig`` para el razonamiento y para lo
que hay que declarar al usar cada uno.

Los dos se conservan conmutables, igual que ``head_tracker`` conserva ``pnp``
junto a ``mesh``, para que la elección sea un resultado medido y no una decisión
sin justificar.

COMO SE EVITAN LOS ERRORES DE SIGNO
-----------------------------------
Este módulo tiene cuatro canales con signo más una decisión de espejo, o sea
cuatro veces la superficie del fallo que en el PROMPT 4 dejó el cabeceo
invertido pasando 400 pruebas en verde (H22). Tres medidas deliberadas:

1. **La terna del tronco es invariante al espejo.** Ninguno de sus tres vectores
   sale de un producto vectorial, porque espejar la imagen invierte la
   quiralidad y con ella el signo de todo producto vectorial. Ver
   ``torso_basis``.
2. **El cruce de lados vive en un solo sitio**, ``canal_del_robot``, con su
   tabla de verdad escrita. Todo lo que va después —filtros, recorte, CSV,
   JSON— viaja ya en el marco del robot.
3. **El sentido de cada canal es un parámetro, no una constante.** Los ejes del
   rig, derivados sobre el papel, contradicen a los comentarios del propio
   visor. Se resuelve moviendo un solo slider en el visor y mirando, no
   deduciéndolo: ``invert_abduccion`` e ``invert_flexion`` son lo único que hay
   que tocar, y el demo las alterna en caliente con las teclas ``b`` y ``f``.

Modo demo::

    python -m telmeow_teleop.arm_tracker --publish --record brazos.csv
"""

from __future__ import annotations

import math
import time
from collections import deque
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np

from .camera import abrir_camara, brillo_medio, describir
from .config import AppConfig
from .filters import EMAFilter, OneEuroFilter, OneEuroVector, SlewLimiter, SlewLimiterND
from .head_tracker import apartar_si_existe
from .limits import (
    ARM_ABDUCCION_1D,
    FLEXION_RANGE,
    FLEXION_RANGE_EXTENDIDO,
    ArmClamp,
    Side,
    clamp_arm,
)
from .retarget import ALCANCE_HUMANO, RetargetBrazo, Retargeting

__all__ = [
    "ArmTracker",
    "ArmAngles",
    "torso_basis",
    "arm_angles",
    "direccion_brazo",
    "canal_del_robot",
    "MAPEOS",
    "LANDMARKS",
    "PUNTOS_MANO",
    "EJE_ANTERIOR",
    "INCLINACION_FLEXION",
    "CANALES",
]


# --------------------------------------------------------------------------
# Landmarks de BlazePose
# --------------------------------------------------------------------------

#: Índices de ``mp.solutions.pose.PoseLandmark`` por lado.
#:
#: Se escriben literales en vez de leerse del enum para que las funciones puras
#: de este módulo se puedan probar sin instanciar MediaPipe. Hay una prueba que
#: los contrasta contra el enum real, que es la que impide que se desincronicen.
LANDMARKS: dict[Side, dict[str, int]] = {
    Side.L: {
        "hombro": 11,
        "codo": 13,
        "muneca": 15,
        "menique": 17,
        "indice": 19,
        "pulgar": 21,
        "cadera": 23,
    },
    Side.R: {
        "hombro": 12,
        "codo": 14,
        "muneca": 16,
        "menique": 18,
        "indice": 20,
        "pulgar": 22,
        "cadera": 24,
    },
}

#: Los cuatro puntos que forman la mano en ``target="mano"``.
PUNTOS_MANO: tuple[str, ...] = ("muneca", "menique", "indice", "pulgar")

#: Dirección ANTERIOR del operador (hacia dónde mira) en el sistema de
#: coordenadas de ``pose_world_landmarks``.
#:
#: MediaPipe entrega ese sistema con x hacia la derecha de la imagen, y hacia
#: abajo y z hacia el fondo de la escena, así que la dirección hacia la cámara
#: —que es hacia donde mira un operador sentado de frente— es −z.
#:
#: Voltear el frame horizontalmente niega la x, no la z, así que este eje **no
#: se ve afectado por el espejo**. Y si el signo estuviera al revés, lo corrige
#: ``invert_flexion`` sin tocar código: por eso el sentido se confirma en el
#: visor y no aquí.
EJE_ANTERIOR: np.ndarray = np.array([0.0, 0.0, -1.0], dtype=np.float64)

#: Dirección SUPERIOR de reserva, en el mismo sistema: −y es hacia arriba.
#:
#: Solo se usa si las caderas nunca llegaron a verse. Asume al operador sentado
#: y erguido, y hay que declararlo si acaba siendo el camino habitual.
EJE_SUPERIOR_RESERVA: np.ndarray = np.array([0.0, -1.0, 0.0], dtype=np.float64)

#: Los dos mapeos del operador al robot. Ver ``ArmTracker`` y ``retarget.py``.
#:
#: - ``"direccion"`` (por defecto, P5.3): filtra y escala la DIRECCION del
#:   brazo y resuelve la cinemática inversa al final, con una sola rama.
#: - ``"articular"`` (el del P5 original): descompone en ángulos, los filtra y
#:   los recorta uno a uno. Tiene la costura del frente —el brazo da la vuelta
#:   al acercar las manos a la cámara— y se conserva solo para comparar.
MAPEOS: tuple[str, ...] = ("direccion", "articular")

#: Nombre del campo MQTT de cada (lado del robot, eje).
CANALES: dict[Side, tuple[str, str]] = {
    Side.L: ("armL", "armLtw"),
    Side.R: ("armR", "armRtw"),
}

#: Alfa del suavizado de la vertical del tronco.
#:
#: La orientación del tronco es cuasi-estática: el operador narra sentado y no
#: se dobla treinta veces por segundo. Un alfa bajo la estabiliza sin coste de
#: retardo perceptible, y evita que el ruido de los landmarks de cadera —que son
#: los peor observados de todos, porque a menudo caen fuera de cuadro— se
#: propague a los cuatro canales a la vez.
ALFA_TRONCO: float = 0.05

#: Componente fuera del eje por debajo de la cual la flexión no está definida.
#:
#: Es ``|sin(abduccion)|``, así que 0.02 corresponde a ±1.1° de abducción: el
#: brazo prácticamente en cruz, alineado con el eje de la flexión. Es la
#: singularidad del cardán, y en este robot **cae en su postura de reposo** —
#: ver la nota de ``arm_angles``.
#:
#: El umbral es estrecho a propósito. Ensancharlo dejaría la flexión sin definir
#: en una franja amplia alrededor de la horizontal, que es donde el operador va
#: a estar buena parte del tiempo; y el problema real es el cero exacto, donde
#: ``atan2`` devuelve ±180 en vez de un valor ruidoso.
_SAGITAL_MINIMA: float = 0.02

#: Inclinación del eje de flexión respecto al eje latero-lateral ideal, en
#: grados y en el sentido POSITIVO DE LA ABDUCCION, por canal del robot.
#:
#: El cardán del robot no es ideal: el eje del servo de flexión no es puramente
#: lateral, sino que está girado ~10° respecto a él. Sale de los ejes del rig
#: (``telmeow_viewer.html:274`` y ``:276``): ``(-0.985, 0, ∓0.17)`` es el eje
#: lateral ``(-1, 0, 0)`` rotado ``asin(0.17) ≈ 9.9°`` alrededor del eje de
#: abducción de ese brazo. La simetría entre los dos lados no es casual: el
#: hombro es la misma pieza reflejada.
#:
#: Sin corregirlo, el retargeting arrastra una diafonía entre canales: ~1.3° de
#: abducción parásita a 30° de flexión y ~10° a 90°. Con él, la ida y la vuelta
#: cierran exactamente, que es lo que permite que la prueba contra el rig sea
#: estrecha y siga sirviendo para detectar errores.
#:
#: El signo depende del sentido de la ABDUCCION, no del de la flexión, así que
#: no queda pendiente de la verificación en el visor. Si algún día
#: ``invert_abduccion`` se pusiera a True, esta inclinación cambia de signo con
#: ella; ``ArmTracker`` ya lo hace.
INCLINACION_FLEXION: dict[Side, float] = {
    Side.L: math.degrees(math.asin(0.174)),
    Side.R: math.degrees(math.asin(0.170)),
}

#: Cuánto se conserva la vertical del tronco derivada de las caderas cuando
#: dejan de verse, en segundos.
#:
#: Retenerla un instante es correcto: la orientación del tronco es
#: cuasi-estática y las caderas parpadean por debajo del umbral con facilidad,
#: sobre todo sentado a una mesa, donde caen cerca del borde del cuadro.
#:
#: Retenerla indefinidamente NO lo es, y es el fallo que tenía este módulo: la
#: bandera se quedaba en «de caderas» para siempre tras el primer frame bueno,
#: así que el HUD y el CSV afirmaban que la vertical era observada mientras en
#: realidad era un valor congelado de hacía minutos. Una vertical retenida un
#: minuto es PEOR que la reserva, porque la reserva es un supuesto declarado
#: —operador sentado y erguido— y la retenida es un supuesto invisible. Pasado
#: este plazo se olvida y se cae a la reserva, que sí se anuncia.
RETENCION_TRONCO_S: float = 2.0


# --------------------------------------------------------------------------
# Geometría (funciones puras, probables sin cámara ni MediaPipe)
# --------------------------------------------------------------------------


def _normalizar(vector: np.ndarray) -> np.ndarray | None:
    norma = float(np.linalg.norm(vector))
    if not math.isfinite(norma) or norma < 1e-9:
        return None
    return vector / norma


def torso_basis(
    hombro: np.ndarray,
    hombro_contrario: np.ndarray,
    superior: np.ndarray,
    eje_anterior: np.ndarray = EJE_ANTERIOR,
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Terna ortonormal del tronco vista desde UN brazo.

    Devuelve ``(u_lat, u_ant, u_sup)``, donde ``u_lat`` apunta **hacia afuera
    del brazo en cuestión**, ``u_ant`` hacia el frente del operador y ``u_sup``
    hacia su cabeza. Devuelve ``None`` si la geometría es degenerada (puntos
    coincidentes, vectores paralelos, coordenadas no finitas).

    POR QUE NINGUN VECTOR SALE DE UN PRODUCTO VECTORIAL
    ---------------------------------------------------
    Es la decisión que hace este módulo inmune al espejo, y conviene entenderla
    porque es contraintuitiva: lo natural sería sacar ``u_ant`` como
    ``u_lat × u_sup``, y sería un error.

    Voltear el frame horizontalmente niega la coordenada x de todos los
    landmarks, lo que convierte un sistema de mano derecha en uno de mano
    izquierda. Un producto vectorial **cambia de signo** bajo esa
    transformación; un producto escalar no. Así que un ``u_ant`` obtenido por
    producto vectorial apuntaría hacia adelante o hacia atrás según si la cámara
    está espejada, y la flexión saldría invertida sin que nada lo delate.

    Los tres vectores se construyen aquí de forma que cada uno **conserva su
    significado** bajo el espejo:

    - ``u_sup`` sale de landmarks (cadera → hombros), y espejar no cambia hacia
      dónde está la cabeza.
    - ``u_lat`` sale del hombro contrario hacia el propio, así que apunta hacia
      afuera de ESE brazo sea cual sea la etiqueta que BlazePose le ponga.
    - ``u_ant`` sale del eje óptico, que un volteo horizontal no toca.

    Los tres se ortogonalizan por Gram-Schmidt en ese orden.
    """
    u_sup = _normalizar(np.asarray(superior, dtype=np.float64))
    if u_sup is None:
        return None

    lateral = np.asarray(hombro, dtype=np.float64) - np.asarray(
        hombro_contrario, dtype=np.float64
    )
    u_lat = _normalizar(lateral - float(np.dot(lateral, u_sup)) * u_sup)
    if u_lat is None:
        return None

    anterior = np.asarray(eje_anterior, dtype=np.float64)
    anterior = (
        anterior
        - float(np.dot(anterior, u_sup)) * u_sup
        - float(np.dot(anterior, u_lat)) * u_lat
    )
    u_ant = _normalizar(anterior)
    if u_ant is None:
        return None

    return (u_lat, u_ant, u_sup)


def direccion_brazo(
    hombro: np.ndarray,
    destino: np.ndarray,
    base: tuple[np.ndarray, np.ndarray, np.ndarray],
    inclinacion: float = 0.0,
) -> tuple[float, float, float] | None:
    """Dirección unitaria hombro→destino en el marco del robot: ``(L, S, A)``.

    Componentes sobre la terna del tronco (lateral hacia afuera, superior,
    anterior), con la inclinación del eje de flexión ya deshecha (ver
    ``INCLINACION_FLEXION`` y ``arm_angles``). Es la entrada del retargeting por
    dirección, y también la de ``arm_angles``, que solo la pasa a ángulos.

    Devuelve ``None`` si el brazo tiene longitud nula o algo no es finito.
    """
    h = _normalizar(
        np.asarray(destino, dtype=np.float64) - np.asarray(hombro, dtype=np.float64)
    )
    if h is None:
        return None

    u_lat, u_ant, u_sup = base
    componente_lateral = float(np.dot(h, u_lat))
    componente_superior = float(np.dot(h, u_sup))
    componente_anterior = float(np.dot(h, u_ant))

    # Deshacer la inclinación del eje de flexión: una rotación de -inclinacion
    # en el plano frontal, que es el de la abducción. Su signo depende del
    # sentido de la abducción y no del de la flexión, así que no queda pendiente
    # de la verificación del canal `tw`.
    if inclinacion:
        radianes = math.radians(inclinacion)
        coseno, seno = math.cos(radianes), math.sin(radianes)
        componente_lateral, componente_superior = (
            componente_lateral * coseno + componente_superior * seno,
            -componente_lateral * seno + componente_superior * coseno,
        )
    resultado = (componente_lateral, componente_superior, componente_anterior)
    if not all(math.isfinite(v) for v in resultado):
        return None
    return resultado


def arm_angles(
    hombro: np.ndarray,
    destino: np.ndarray,
    base: tuple[np.ndarray, np.ndarray, np.ndarray],
    inclinacion: float = 0.0,
) -> tuple[float, float | None] | None:
    """Descompone la dirección del brazo en ``(abduccion, flexion)``, en grados.

    Es la **cinemática inversa exacta del cardán del robot**, en su mismo orden
    de composición: la flexión es la articulación padre y la abducción la hija,
    tal como están anidadas en el rig del visor
    (``gArmRbal = makeJoint(gArmRtwist, ...)``).

    EL CERO NO ES EL BRAZO COLGANDO, ES EL BRAZO EN HORIZONTAL
    -----------------------------------------------------------
    Es lo más contraintuitivo de este módulo y conviene leerlo antes de tocar
    nada. **La postura de reposo del robot tiene los brazos extendidos hacia los
    lados**, no colgando: con ``armR = 0`` el brazo apunta a lo largo del eje
    latero-lateral. Verificado en el visor el 2026-09-07 (H30), no deducido.

    De ahí sale todo lo demás:

    - ``abduccion`` es la **elevación respecto a la horizontal**, en el plano
      frontal. 0 = brazo en cruz, negativo = bajando hacia el cuerpo, positivo =
      subiendo hacia la cabeza. Los topes de ``limits.py`` cobran sentido
      inmediato: −55° es lo que el brazo baja desde la horizontal, y +30° lo que
      sube antes de chocar con la cabeza. **El robot no puede dejar los brazos
      colgando**: su límite son 55° por debajo de la horizontal.
    - El brazo del operador colgando corresponde a ``abduccion = −90``, que el
      recorte lleva a −55. Es correcto y es una limitación del mecanismo, no del
      estimador.

    Con ``h`` la dirección unitaria hombro→destino y sus tres componentes
    ``L = h·u_lat``, ``S = h·u_sup``, ``A = h·u_ant``, la composición directa
    del cardán es::

        h = cos(a)·u_lat + sin(a)·[cos(f)·u_sup + sin(f)·u_ant]

    y su inversa, tomando la rama ``f ∈ (−90, 90)`` que es la que usa el robot::

        a = signo(S) · atan2(hypot(S, A), L)
        f = atan2(A, S)  si S ≥ 0,  atan2(−A, −S)  si S < 0

    Nada de cocientes de longitudes: todo sale de productos escalares entre
    vectores unitarios. Es la corrección del BUG 3, por construcción y no por
    parche.

    ``inclinacion`` CORRIGE QUE EL CARDAN NO ES IDEAL. El eje de flexión está
    girado ~10° respecto al latero-lateral puro, alrededor del eje de abducción
    (ver ``INCLINACION_FLEXION``). Como la dirección de reposo del brazo va
    sobre ese mismo eje, deshacerlo es **una sola rotación de −inclinación en el
    plano frontal** antes de descomponer, sin desplazamiento que sumar después.

    LA SINGULARIDAD ESTA EN EL REPOSO DEL ROBOT, Y ESO SI DUELE
    -----------------------------------------------------------
    Con el brazo en horizontal —``abduccion ≈ 0``— la dirección del brazo se
    alinea con el eje de la flexión, y **la flexión deja de mover el brazo**.
    Comprobado en el visor: con ``armR = 0``, barrer ``armRtw`` de 0 a 90° no
    desplaza el brazo ni un píxel; solo gira la pieza del hombro sobre sí misma.

    No es un defecto del estimador ni algo que se pueda reparametrizar: es la
    singularidad del propio mecanismo, y cae justo en su postura de reposo. La
    consecuencia práctica hay que declararla: **con el brazo en cruz, el robot
    tiene un solo grado de libertad útil**, y el gesto de señalar al frente
    exige bajar o subir el brazo antes de que la flexión sirva de algo.

    Ahí el ``atan2`` no devuelve un valor ruidoso sino uno **falso y grande**:
    con los dos argumentos en cero da 0 o ±180 según el signo del cero en coma
    flotante, y ese ±180 recortaría al tope y daría un tirón visible. Por eso,
    cuando la componente fuera del eje cae por debajo de ``_SAGITAL_MINIMA``, se
    devuelve la flexión como ``None``: es información que no existe, y quien
    llama debe conservar el último valor válido en vez de inventarse uno.

    Returns:
        ``(abduccion, flexion)``, con ``flexion`` a ``None`` en la
        singularidad. ``None`` entero si el brazo tiene longitud nula o si algo
        no es finito.
    """
    direccion = direccion_brazo(hombro, destino, base, inclinacion)
    if direccion is None:
        return None
    componente_lateral, componente_superior, componente_anterior = direccion

    fuera_del_eje = math.hypot(componente_superior, componente_anterior)
    signo = -1.0 if componente_superior < 0.0 else 1.0
    abduccion = signo * math.degrees(math.atan2(fuera_del_eje, componente_lateral))
    if not math.isfinite(abduccion):
        return None

    if fuera_del_eje < _SAGITAL_MINIMA:
        return (abduccion, None)

    flexion = math.degrees(
        math.atan2(signo * componente_anterior, signo * componente_superior)
    )
    if not math.isfinite(flexion):
        return (abduccion, None)
    return (abduccion, flexion)


def canal_del_robot(
    etiqueta: Side, *, frame_espejado: bool, mirror: bool
) -> Side:
    """Traduce la etiqueta de lado de BlazePose al canal del robot.

    **Este es el único punto del módulo donde un lado del operador se ata a un
    canal del robot.** Todo lo que viene después —ganancias, filtros, recorte,
    CSV y JSON— viaja ya en el marco del robot, así que no hay forma de que la
    grabación y lo que se publica por MQTT discrepen.

    Son dos cruces distintos y se escriben por separado a propósito:

    1. **Etiqueta de BlazePose → lado FISICO del operador.** BlazePose etiqueta
       según la persona que ve. Si el frame llega espejado (``cv2.flip``, que es
       lo habitual para que el operador se vea como en un espejo), la persona
       que ve es la imagen especular del operador, así que sus ``LEFT_*`` son el
       lado físico **derecho**. Sin espejo, coinciden.
    2. **Lado físico del operador → canal del robot.** Con ``mirror=True`` (por
       defecto) coinciden: el brazo izquierdo físico del operador mueve
       ``armL``. Es la misma convención cerrada para la cabeza en el P4.1 — el
       robot reproduce la pose en su propio marco, que es lo que llega a los
       servos. Con ``mirror=False`` se cruzan, y el robot imita como un espejo.

    Tabla de verdad completa, con ``mirror=True``::

        etiqueta  frame_espejado  ->  lado fisico   canal
        L         False               izquierdo     armL
        L         True                derecho       armR
        R         False               derecho       armR
        R         True                izquierdo     armL

    Con ``mirror=False`` la última columna se cruza en las cuatro filas.
    """
    fisico = etiqueta.opposite if frame_espejado else etiqueta
    return fisico if mirror else fisico.opposite


# --------------------------------------------------------------------------
# Resultado
# --------------------------------------------------------------------------


class ArmAngles:
    """Los cuatro canales del robot, con ``None`` para los que no se pudieron
    estimar en este frame.

    No es una 4-tupla —que es lo que pedía el enunciado— porque los brazos se
    descartan **por separado**: uno fuera de cuadro, o con un landmark NaN, no
    puede tumbar al otro. Un canal en ``None`` se omite del JSON, y el visor
    conserva su último valor (``PosePublisher.publish`` ya omite los ``None``).
    Así, «reutilizar el último valor válido» no necesita que nadie mantenga
    estado.
    """

    __slots__ = ("armL", "armLtw", "armR", "armRtw")

    def __init__(
        self,
        armL: float | None = None,
        armLtw: float | None = None,
        armR: float | None = None,
        armRtw: float | None = None,
    ) -> None:
        self.armL = armL
        self.armLtw = armLtw
        self.armR = armR
        self.armRtw = armRtw

    @property
    def as_tuple(self) -> tuple[float | None, float | None, float | None, float | None]:
        return (self.armL, self.armLtw, self.armR, self.armRtw)

    @property
    def vacio(self) -> bool:
        """True si no se pudo estimar ningún canal."""
        return all(v is None for v in self.as_tuple)

    def __repr__(self) -> str:
        def fmt(v: float | None) -> str:
            return "--" if v is None else f"{v:+.1f}"

        return (
            f"ArmAngles(armL={fmt(self.armL)}, armLtw={fmt(self.armLtw)}, "
            f"armR={fmt(self.armR)}, armRtw={fmt(self.armRtw)})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ArmAngles):
            return NotImplemented
        return self.as_tuple == other.as_tuple


# --------------------------------------------------------------------------
# El tracker
# --------------------------------------------------------------------------


class ArmTracker:
    """Estima los cuatro canales de brazo del robot desde frames RGB.

    Dos mapeos, conmutables en caliente (``mapeo``, tecla ``k`` del demo):

    ``"direccion"`` (por defecto, P5.3)
        dirección del brazo → One Euro **vectorial** → proyección al frente →
        escala dentro de la envolvente del robot → cinemática inversa de una
        sola rama → ``clamp_arm``. Ver ``retarget.py``. Los sentidos salen de la
        cinemática directa verificada en el visor, así que las banderas de
        inversión y las cuatro ganancias **no intervienen**: su papel lo cumple
        ``alcance_humano``.
    ``"articular"`` (el del P5 original)
        dirección → ángulos del cardán → ganancia por sentido → inversión → One
        Euro por ángulo → ``clamp_arm``. Tiene la costura del frente; se conserva
        para poder comparar los dos sobre la misma sesión.

    Args:
        mirror: Con ``True`` el brazo izquierdo físico del operador manda sobre
            ``armL``. Ver ``canal_del_robot``.
        frame_espejado: Si el frame que llega a ``process`` ya viene volteado
            horizontalmente. Debe coincidir con ``cfg.camera.flip``: es un
            **hecho sobre la entrada**, no una preferencia, y va separado de
            ``mirror``, que sí es una política.
        target: ``"mano"`` (centroide de los cuatro landmarks de la mano) o
            ``"codo"`` (vector húmero). Ver ``config.ArmConfig``.
        invert_abduccion, invert_flexion: sentido de cada eje, compartido por
            los dos brazos porque la convención del robot es simétrica.
            **Pendientes de verificar contra el visor.**
        gain_*: multiplicadores por sentido. 1.0 copia el gesto.
        min_visibility: umbral por debajo del cual un landmark se considera no
            observado.
        reposo_abduccion, reposo_flexion: pose a la que vuelve un brazo cuando
            se pierde su seguimiento. Ver ``comando``.
        gracia_s: cuánto se mantiene el último objetivo antes de empezar a
            volver al reposo, en segundos.
        velocidad_max: velocidad angular máxima del comando, en grados por
            segundo. Ver ``comando`` y ``filters.SlewLimiter``.
        retencion_tronco_s: cuánto se conserva la vertical del tronco derivada
            de las caderas cuando dejan de verse. Ver ``RETENCION_TRONCO_S``.
        mapeo: ``"direccion"`` o ``"articular"``.
        flexion_min: tope inferior de la flexión. −180 (por defecto) quita la
            costura del frente; −90 es el rango que cubrió el barrido. Ver
            ``limits.FLEXION_RANGE_EXTENDIDO``.
        alcance_humano, rodilla, margen_tabla, histeresis_rama:
            parámetros del retargeting por dirección; ver
            ``retarget.RetargetBrazo``.
    """

    def __init__(
        self,
        *,
        mirror: bool = True,
        frame_espejado: bool = True,
        target: str = "mano",
        invert_abduccion: bool = False,
        invert_flexion: bool = False,
        gain_abduccion: float = 1.0,
        gain_aduccion: float = 1.0,
        gain_flexion: float = 1.0,
        gain_extension: float = 1.0,
        min_cutoff: float = 0.5,
        beta: float = 0.5,
        min_visibility: float = 0.5,
        model_complexity: int = 1,
        reposo_abduccion: float = -55.0,
        reposo_flexion: float = 0.0,
        gracia_s: float = 0.5,
        velocidad_max: float = 180.0,
        retencion_tronco_s: float = RETENCION_TRONCO_S,
        mapeo: str = "direccion",
        flexion_min: float = FLEXION_RANGE_EXTENDIDO[0],
        alcance_humano: float = ALCANCE_HUMANO,
        rodilla: float = 0.8,
        margen_tabla: float = 2.0,
        histeresis_rama: float = 15.0,
        pose_estimator=None,
        clock=time.perf_counter,
    ) -> None:
        if mapeo not in MAPEOS:
            raise ValueError(f"mapeo debe ser uno de {MAPEOS}, se recibió {mapeo!r}")
        if not -180.0 <= flexion_min <= FLEXION_RANGE[0]:
            raise ValueError("flexion_min debe estar entre -180 y -90")
        if target not in ("mano", "codo"):
            raise ValueError(f"target debe ser 'mano' o 'codo', se recibió {target!r}")
        ganancias = (gain_abduccion, gain_aduccion, gain_flexion, gain_extension)
        if min(ganancias) <= 0.0:
            raise ValueError("las ganancias deben ser positivas")
        if not 0.0 <= min_visibility <= 1.0:
            raise ValueError("min_visibility debe estar entre 0 y 1")
        if gracia_s < 0.0:
            raise ValueError("gracia_s no puede ser negativo")
        if retencion_tronco_s < 0.0:
            raise ValueError("retencion_tronco_s no puede ser negativo")

        self.mirror = mirror
        self.frame_espejado = frame_espejado
        self.target = target
        self.invert_abduccion = invert_abduccion
        self.invert_flexion = invert_flexion
        self.gain_abduccion = gain_abduccion
        self.gain_aduccion = gain_aduccion
        self.gain_flexion = gain_flexion
        self.gain_extension = gain_extension
        self.min_visibility = min_visibility
        self.gracia_s = float(gracia_s)
        self.retencion_tronco_s = float(retencion_tronco_s)

        self._clock = clock
        # ``pose_estimator`` permite usar la cadena de mapeo sin BlazePose: las
        # pruebas y tools/comparar_mapeo_brazos.py reproducen una sesión grabada
        # desde el CSV, sin cámara. Tiene que ofrecer process() y close().
        self._pose = pose_estimator if pose_estimator is not None else mp.solutions.pose.Pose(
            static_image_mode=False,
            model_complexity=model_complexity,
            # Deliberadamente DESACTIVADO. MediaPipe trae su propio suavizado
            # temporal, y dejarlo puesto encadenaría dos filtros: la reducción de
            # temblor que se midiera dejaría de ser atribuible al One Euro, que
            # es la cifra que este proyecto reporta (P3, P4.5). Con esto, el One
            # Euro es el único filtro temporal del camino.
            smooth_landmarks=False,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )

        # Un filtro por canal del robot y eje: cuatro en total.
        self._filtros: dict[tuple[Side, str], OneEuroFilter] = {
            (lado, eje): OneEuroFilter(min_cutoff, beta, clock=clock)
            for lado in (Side.L, Side.R)
            for eje in ("abduccion", "flexion")
        }
        # Vertical del tronco, suavizada componente a componente.
        self._filtros_tronco = tuple(EMAFilter(ALFA_TRONCO) for _ in range(3))

        self._u_sup: np.ndarray | None = None
        self._tronco_fresco = False
        self._t_tronco: float | None = None
        self._frames_sin_pose = 0
        self._raw: dict[Side, tuple[float, float]] = {}
        self._flexion_valida: dict[Side, float] = {}
        self._clamps: dict[Side, ArmClamp] = {}
        self._visibilidades: dict[str, float] = {}
        self._puntos_destino: dict[Side, int] = {}
        self._landmarks_imagen = None
        self._extremos: dict[Side, dict[str, list[float]]] = {
            lado: {"abduccion": [0.0, 0.0], "flexion": [0.0, 0.0]}
            for lado in (Side.L, Side.R)
        }

        # -- retargeting por dirección (P5.3) --------------------------------
        self._mapeo = mapeo
        self.flexion_range: tuple[float, float] = (
            float(flexion_min),
            FLEXION_RANGE[1],
        )
        # beta en unidades de vector unitario por segundo: 1/s ≈ 57°/s. Se
        # convierte para que el mismo ARM_BETA signifique lo mismo en los dos
        # mapeos.
        self._filtros_direccion: dict[Side, OneEuroVector] = {
            lado: OneEuroVector(min_cutoff, beta * 180.0 / math.pi, clock=clock)
            for lado in (Side.L, Side.R)
        }
        self._retarget: dict[Side, RetargetBrazo] = {
            lado: RetargetBrazo(
                lado,
                flexion_range=self.flexion_range,
                alcance_humano=alcance_humano,
                rodilla=rodilla,
                margen=margen_tabla,
                histeresis=histeresis_rama,
            )
            for lado in (Side.L, Side.R)
        }
        self._direcciones: dict[Side, tuple[float, float, float]] = {}
        self._retargetings: dict[Side, Retargeting] = {}

        # -- etapa de salida: de la estimación al comando ------------------
        # La pose de reposo pasa por clamp_arm igual que cualquier otra: así no
        # hay forma de que una configuración descuidada mande al robot a una
        # postura que su mecanismo no admite.
        self._reposo: dict[Side, tuple[float, float]] = {
            lado: clamp_arm(
                reposo_abduccion, reposo_flexion, lado,
                flexion_range=self.flexion_range,
            ).as_tuple
            for lado in (Side.L, Side.R)
        }
        for lado, (abd, flex) in self._reposo.items():
            self._retarget[lado].reset(abd, flex)
        self._limitadores: dict[Side, tuple[SlewLimiter, SlewLimiter]] = {}
        self._objetivo: dict[Side, tuple[float, float]] = {}
        for lado in (Side.L, Side.R):
            abd, flex = self._reposo[lado]
            limitador_abd = SlewLimiter(velocidad_max, clock=clock)
            limitador_flex = SlewLimiter(velocidad_max, clock=clock)
            # Arrancar FIJADOS en el reposo y no sin inicializar: si el primer
            # objetivo llegara a un limitador virgen, pasaría entero y el
            # arranque sería justo el salto que esto evita.
            limitador_abd.reset(abd)
            limitador_flex.reset(flex)
            self._limitadores[lado] = (limitador_abd, limitador_flex)
            self._objetivo[lado] = (abd, flex)
        # En el mapeo por dirección el límite es COORDINADO (sobre la norma de
        # las dos articulaciones): un salto grande —volver al reposo, recuperar
        # el brazo— recorre la recta entre las dos poses en vez de una L.
        self._limitadores_nd: dict[Side, SlewLimiterND] = {}
        for lado in (Side.L, Side.R):
            limitador = SlewLimiterND(velocidad_max, clock=clock)
            limitador.reset(self._reposo[lado])
            self._limitadores_nd[lado] = limitador
        self._t_estimado: dict[Side, float] = {}
        self._estimado: dict[Side, bool] = {Side.L: False, Side.R: False}

    # -- estado para el HUD y la calibración -------------------------------

    @property
    def last_clamp(self) -> dict[Side, ArmClamp]:
        """Último recorte por canal del robot, con ``lower``/``upper``/
        ``fraction``/``clamped`` listos para las barras del HUD.

        En el brazo, ``lower`` y ``upper`` de la abducción son los interpolados
        PARA LA FLEXION ACTUAL, así que la barra se estrecha y se ensancha en
        vivo. Es el comportamiento correcto, no un defecto.
        """
        return dict(self._clamps)

    @property
    def mapeo(self) -> str:
        return self._mapeo

    @mapeo.setter
    def mapeo(self, valor: str) -> None:
        """Cambia de mapeo en caliente SIN salto: el limitador del mapeo nuevo
        arranca donde está el comando del viejo."""
        if valor not in MAPEOS:
            raise ValueError(f"mapeo debe ser uno de {MAPEOS}, se recibió {valor!r}")
        if valor == self._mapeo:
            return
        for lado in (Side.L, Side.R):
            limitador_abd, limitador_flex = self._limitadores[lado]
            nd = self._limitadores_nd[lado]
            if valor == "direccion":
                actual = (limitador_abd.value, limitador_flex.value)
                if None not in actual:
                    nd.reset(actual)
                    self._retarget[lado].reset(*actual)
            else:
                actual = nd.value
                if actual is not None:
                    # En articular la flexión vuelve a ±90: se recorta para que
                    # el limitador no arranque fuera del rango de ese mapeo.
                    abd, flex = clamp_arm(actual[0], actual[1], lado).as_tuple
                    limitador_abd.reset(abd)
                    limitador_flex.reset(flex)
            self._filtros_direccion[lado].reset()
        self._mapeo = valor

    @property
    def direcciones(self) -> dict[Side, tuple[float, float, float]]:
        """Dirección hombro→destino del último frame, ``(L, S, A)`` en el marco
        del robot y SIN filtrar, por canal. Va al CSV: con ella se puede
        reproducir offline cualquier mapeo sobre la misma sesión
        (``tools/comparar_mapeo_brazos.py``)."""
        return dict(self._direcciones)

    @property
    def retargetings(self) -> dict[Side, Retargeting]:
        """Último paso del retargeting por dirección, por canal: ψ, α, rama."""
        return dict(self._retargetings)

    @property
    def cambios_de_rama(self) -> dict[Side, int]:
        return {lado: r.cambios_de_rama for lado, r in self._retarget.items()}

    @property
    def raw_angles(self) -> dict[Side, tuple[float, float]]:
        """``(abduccion, flexion)`` crudos del operador, por canal del robot,
        antes de ganancia, inversión, filtro y recorte."""
        return dict(self._raw)

    @property
    def visibilidades(self) -> dict[str, float]:
        """Puntuación de visibilidad del último frame, por landmark.

        Es el dato que decide si el codo se ve lo bastante como para usar
        ``target="codo"`` y si las caderas sostienen la vertical del tronco. Va
        al CSV desde la primera grabación a propósito: una sesión grabada sin
        estas columnas no se recupera releyéndola (H26).
        """
        return dict(self._visibilidades)

    @property
    def tronco_de_caderas(self) -> bool:
        """True si las caderas se vieron **en este frame** y actualizaron la
        vertical del tronco.

        OJO al matiz, que costó una lectura falsa del HUD: esto es *fresco en
        este frame*, no *alguna vez*. Antes la bandera se quedaba puesta para
        siempre tras el primer frame bueno, así que el HUD y el CSV afirmaban
        «vertical de caderas» con la visibilidad de cadera a 0.00. Para saber
        qué se está usando de verdad, incluido el caso intermedio, está
        ``origen_tronco``.
        """
        return self._tronco_fresco

    @property
    def puntos_destino(self) -> dict[Side, int]:
        """De cuántos landmarks salió el punto de destino de cada canal.

        Con ``target="codo"`` siempre es 1. Con ``target="mano"`` va de 1 a 4,
        y su media a lo largo de una grabación es la que dice si el centroide
        de cuatro puntos es real o si en la práctica colapsa a la muñeca.
        """
        return dict(self._puntos_destino)

    @property
    def origen_tronco(self) -> str:
        """De dónde sale la vertical del tronco ahora mismo.

        - ``"caderas"``: observadas en este frame.
        - ``"retenido"``: derivada de las caderas, pero de un frame anterior;
          sigue dentro de ``retencion_tronco_s``.
        - ``"reserva"``: vertical de imagen. **Asume al operador sentado y
          erguido**, y es el caso que hay que declarar en la tesis si resulta
          ser el habitual.
        """
        if self._tronco_fresco:
            return "caderas"
        return "retenido" if self._u_sup is not None else "reserva"

    @property
    def edad_tronco_s(self) -> float | None:
        """Segundos desde la última vez que las caderas actualizaron la
        vertical, o ``None`` si nunca lo hicieron."""
        if self._t_tronco is None:
            return None
        return self._clock() - self._t_tronco

    @property
    def landmarks_imagen(self):
        """Landmarks en coordenadas de imagen del último frame, para dibujar.

        Son los normalizados de ``pose_landmarks``, no los de
        ``pose_world_landmarks`` que usa la estimación: los primeros están en el
        plano de la imagen y sirven para pintar el esqueleto encima; los
        segundos están en metros con origen en la cadera y no se pueden pintar.
        """
        return self._landmarks_imagen

    @property
    def frames_sin_pose(self) -> int:
        return self._frames_sin_pose

    @property
    def degenerate_dt_count(self) -> int:
        """``dt`` no positivos que los filtros tuvieron que sustituir.

        Con ``time.monotonic()`` en Windows serían muchos: su resolución de
        ~15.6 ms es comparable a los 33 ms de un frame a 30 fps, así que dos
        frames seguidos pueden leer el mismo instante y dar ``dt = 0``, que en el
        filtro es una división por cero. Con ``perf_counter`` debería quedarse en
        cero; si sube, algo está pasando el reloj equivocado.
        """
        return sum(f.degenerate_dt_count for f in self._filtros.values())

    @property
    def raw_extremes(self) -> dict[Side, dict[str, tuple[float, float]]]:
        """Recorrido acumulado del operador por canal, para calibrar."""
        return {
            lado: {eje: (lo, hi) for eje, (lo, hi) in ejes.items()}
            for lado, ejes in self._extremos.items()
        }

    def reset_extremes(self) -> None:
        for ejes in self._extremos.values():
            for extremo in ejes.values():
                extremo[0] = 0.0
                extremo[1] = 0.0

    def suggested_gains(self) -> dict[str, float | None]:
        """Ganancias que harían falta para que el recorrido observado cubra el
        rango del robot, por sentido.

        DIFERENCIA IMPORTANTE RESPECTO A LA CABEZA, y es estructural: el rango
        de abducción **no es constante**, depende de la flexión. Dividir por el
        rango de la tabla daría un número distinto en cada frame. Como objetivo
        de calibración se usa ``ARM_ABDUCCION_1D``, la rebanada a flexión 0°,
        igual que el P4.3 separó ``HEAD_PAN_TARGET_RANGE`` del tope mecánico del
        pan. La flexión sí usa su rango mecánico completo, ±90°.

        Se toma el recorrido del brazo que MAS haya alcanzado en cada sentido:
        una ganancia por canal y no por brazo, porque el robot es simétrico y
        dos ganancias distintas por lado harían que el mismo gesto saliera
        asimétrico.

        Devuelve ``None`` en los sentidos donde el operador apenas se movió: una
        excursión de 2° propondría una ganancia de 25, que no significa nada.
        """
        minimo_excursion = 5.0
        peor: dict[str, float] = {}
        for lado, ejes in self._extremos.items():
            objetivo_abd = ARM_ABDUCCION_1D[lado]
            for clave, valor, tope in (
                ("abduccion", ejes["abduccion"][1], objetivo_abd[1]),
                ("aduccion", ejes["abduccion"][0], objetivo_abd[0]),
                ("flexion", ejes["flexion"][1], FLEXION_RANGE[1]),
                ("extension", ejes["flexion"][0], FLEXION_RANGE[0]),
            ):
                if abs(valor) <= minimo_excursion:
                    continue
                propuesta = abs(tope / valor)
                # El mínimo entre los dos brazos: la ganancia tiene que servir
                # al que más lejos llega, o ese se pasaría de tope.
                peor[clave] = min(peor.get(clave, propuesta), propuesta)

        return {
            clave: peor.get(clave)
            for clave in ("abduccion", "aduccion", "flexion", "extension")
        }

    # -- proceso -----------------------------------------------------------

    def _punto(self, mundo, lado: Side, nombre: str) -> np.ndarray:
        lm = mundo[LANDMARKS[lado][nombre]]
        return np.array([lm.x, lm.y, lm.z], dtype=np.float64)

    def _visibilidad(self, mundo, lado: Side, nombre: str) -> float:
        return float(getattr(mundo[LANDMARKS[lado][nombre]], "visibility", 1.0))

    def _destino(self, mundo, lado: Side) -> tuple[np.ndarray, float, int]:
        """Punto que orienta el brazo, su visibilidad y de cuántos landmarks
        salió, según ``target``."""
        if self.target == "codo":
            return (
                self._punto(mundo, lado, "codo"),
                self._visibilidad(mundo, lado, "codo"),
                1,
            )
        # Promediar los landmarks de la mano en vez de usar solo la muñeca: son
        # varias estimaciones del mismo sitio, y el promedio cancela parte de su
        # ruido independiente. Es la versión barata —sin añadir ningún modelo—
        # de pedir más puntos en la mano.
        #
        # LA PUERTA ES LA MUÑECA, NO EL MINIMO DE LOS CUATRO. Con el mínimo,
        # cualquier dedo mal estimado descartaba la mano entera, y BlazePose
        # estima los dedos con bastante menos confianza que la muñeca incluso
        # con la mano a la vista. Medido en la grabación del 2026-09-13: el
        # mínimo de los cuatro pasaba el umbral en el 45-48 % de los frames
        # mientras el codo —un solo landmark— lo pasaba en el 90 %, lo que hacía
        # parecer que la mano se ve peor que el codo cuando buena parte de la
        # diferencia era el propio agregador.
        #
        # Y hay un motivo de método además del técnico: `target="codo"` se mide
        # con la visibilidad de UN landmark, así que gatear la mano con el mínimo
        # de cuatro sesgaba la comparación mano-frente-a-codo, que es justo la
        # decisión que esa medición tiene que resolver. Con la muñeca de puerta,
        # los dos casos se miden igual.
        vis_ancla = self._visibilidad(mundo, lado, "muneca")
        puntos = [self._punto(mundo, lado, "muneca")]
        for nombre in PUNTOS_MANO[1:]:
            if self._visibilidad(mundo, lado, nombre) >= self.min_visibility:
                puntos.append(self._punto(mundo, lado, nombre))
        return np.mean(puntos, axis=0), vis_ancla, len(puntos)

    def _actualizar_tronco(self, mundo, ahora: float) -> np.ndarray | None:
        """Vertical del tronco, suavizada, o la reserva si no hay caderas.

        Tres estados, y distinguirlos importa: observada en este frame,
        retenida de un frame anterior reciente, o la reserva. Ver
        ``origen_tronco`` y ``RETENCION_TRONCO_S``.
        """
        vis_cadera = min(
            self._visibilidad(mundo, Side.L, "cadera"),
            self._visibilidad(mundo, Side.R, "cadera"),
        )
        self._visibilidades["cadera"] = vis_cadera
        self._tronco_fresco = False

        if vis_cadera >= self.min_visibility:
            hombros = (
                self._punto(mundo, Side.L, "hombro")
                + self._punto(mundo, Side.R, "hombro")
            ) / 2.0
            caderas = (
                self._punto(mundo, Side.L, "cadera")
                + self._punto(mundo, Side.R, "cadera")
            ) / 2.0
            crudo = hombros - caderas
            if np.all(np.isfinite(crudo)):
                suave = np.array(
                    [f(float(v)) for f, v in zip(self._filtros_tronco, crudo)],
                    dtype=np.float64,
                )
                normalizado = _normalizar(suave)
                if normalizado is not None:
                    self._u_sup = normalizado
                    self._t_tronco = ahora
                    self._tronco_fresco = True

        if self._u_sup is not None and self._t_tronco is not None:
            if ahora - self._t_tronco <= self.retencion_tronco_s:
                return self._u_sup
            # Caducó. Se OLVIDA en vez de seguir arrastrándola: un supuesto
            # declarado (la reserva) es mejor que uno invisible.
            self._u_sup = None
            self._t_tronco = None
            for filtro in self._filtros_tronco:
                filtro.reset()

        # No hay caderas recientes: se asume operador sentado y erguido.
        return EJE_SUPERIOR_RESERVA

    def process(self, frame_rgb: np.ndarray) -> ArmAngles | None:
        """Estima los cuatro canales del robot desde un frame RGB.

        Returns:
            Un ``ArmAngles`` con ``None`` en los canales que no se pudieron
            estimar, o ``None`` si no se detectó ninguna pose. Cada brazo se
            descarta por separado.
        """
        self._estimado = {Side.L: False, Side.R: False}
        resultado = self._pose.process(frame_rgb)
        mundo = getattr(resultado, "pose_world_landmarks", None)
        if mundo is None:
            self._frames_sin_pose += 1
            self._landmarks_imagen = None
            self._tronco_fresco = False
            return None
        mundo = mundo.landmark
        self._landmarks_imagen = resultado.pose_landmarks

        self._frames_sin_pose = 0
        self._raw.clear()
        self._direcciones.clear()
        ahora = self._clock()
        u_sup = self._actualizar_tronco(mundo, ahora)
        if u_sup is None:
            return ArmAngles()

        salida = ArmAngles()
        for etiqueta in (Side.L, Side.R):
            canal = canal_del_robot(
                etiqueta, frame_espejado=self.frame_espejado, mirror=self.mirror
            )
            angulos = self._brazo(mundo, etiqueta, canal, u_sup, ahora)
            if angulos is None:
                continue
            campo_abd, campo_flex = CANALES[canal]
            setattr(salida, campo_abd, angulos[0])
            setattr(salida, campo_flex, angulos[1])
            self._estimado[canal] = True
        return salida

    def _brazo(
        self,
        mundo,
        etiqueta: Side,
        canal: Side,
        u_sup: np.ndarray,
        timestamp: float,
    ) -> tuple[float, float] | None:
        """Un brazo, de landmarks a ángulos del robot. ``None`` = descartarlo."""
        vis_hombro = self._visibilidad(mundo, etiqueta, "hombro")
        destino, vis_destino, n_destino = self._destino(mundo, etiqueta)
        # Cuántos landmarks formaron el punto de destino. Es lo que dice si la
        # promesa de «cuatro puntos de mano son más estables que la muñeca
        # sola» se cumple de verdad o si el centroide colapsa casi siempre a la
        # muñeca, en cuyo caso esa afirmación no se puede defender.
        self._puntos_destino[canal] = n_destino
        # Las visibilidades se registran con el nombre del CANAL DEL ROBOT, no
        # con la etiqueta de BlazePose: el CSV tiene que poder leerse sin volver
        # a razonar sobre el espejo.
        self._visibilidades[f"hombro_{canal.value}"] = vis_hombro
        self._visibilidades[f"destino_{canal.value}"] = vis_destino
        self._visibilidades[f"codo_{canal.value}"] = self._visibilidad(
            mundo, etiqueta, "codo"
        )
        if min(vis_hombro, vis_destino) < self.min_visibility:
            return None

        base = torso_basis(
            self._punto(mundo, etiqueta, "hombro"),
            self._punto(mundo, etiqueta.opposite, "hombro"),
            u_sup,
        )
        if base is None:
            return None

        # La inclinación del eje de flexión es del rig del CANAL que este brazo
        # manda, no del lado del operador. Y acompaña al sentido de la
        # abducción: si se invirtiera, la corrección tiene que invertirse con
        # ella o pasaría de restar 10° a sumarlos.
        inclinacion = INCLINACION_FLEXION[canal]
        if self.invert_abduccion:
            inclinacion = -inclinacion

        hombro = self._punto(mundo, etiqueta, "hombro")
        direccion = direccion_brazo(hombro, destino, base, inclinacion)
        crudo = arm_angles(hombro, destino, base, inclinacion)
        if crudo is None or direccion is None:
            return None
        self._direcciones[canal] = direccion

        abduccion, flexion = crudo
        if flexion is None:
            # Singularidad del cardán: el brazo está horizontal hacia el
            # costado y la flexión no existe. Se conserva la última válida de
            # ESTE canal en vez de inventarse una, porque la flexión también es
            # el índice de la tabla 2D: sin ella no se puede saber cuánta
            # abducción admite el robot, y un valor falso encogería o ensancharía
            # el rango sin motivo.
            anterior = self._flexion_valida.get(canal)
            flexion = 0.0 if anterior is None else anterior
        else:
            self._flexion_valida[canal] = flexion

        self._raw[canal] = (abduccion, flexion)
        for eje, valor in (("abduccion", abduccion), ("flexion", flexion)):
            extremo = self._extremos[canal][eje]
            extremo[0] = min(extremo[0], valor)
            extremo[1] = max(extremo[1], valor)

        if self._mapeo == "direccion":
            return self.map_direccion(canal, direccion, timestamp)
        return self.map_to_robot(canal, abduccion, flexion, timestamp)

    def map_direccion(
        self,
        canal: Side | str,
        direccion,
        timestamp: float | None = None,
    ) -> tuple[float, float] | None:
        """Retargeting por dirección para un canal: One Euro vectorial →
        ``RetargetBrazo``. Ver ``retarget.py``.

        Separada de ``process`` por lo mismo que ``map_to_robot``: se puede
        probar —y reproducir desde un CSV— sin cámara ni MediaPipe.

        Devuelve ``None`` si la dirección no es finita o es degenerada; quien
        llame conserva el último valor de ese brazo.
        """
        canal = Side.parse(canal)
        try:
            h = tuple(float(v) for v in direccion)
        except (TypeError, ValueError):
            return None
        if len(h) != 3 or not all(math.isfinite(v) for v in h):
            return None
        ahora = self._clock() if timestamp is None else timestamp
        suave = self._filtros_direccion[canal](h, ahora)
        paso = self._retarget[canal](suave)
        if paso is None:
            return None
        self._retargetings[canal] = paso
        self._clamps[canal] = paso.recorte
        return paso.as_tuple

    def map_to_robot(
        self,
        canal: Side | str,
        abduccion: float,
        flexion: float,
        timestamp: float | None = None,
    ) -> tuple[float, float] | None:
        """Convierte los ángulos del operador en los del robot, para un canal.

        Cadena: ganancia por sentido → inversión → One Euro → ``clamp_arm``.

        Está separada de ``process`` para poder probarla sin cámara ni
        MediaPipe: es donde viven las ganancias, los signos y el recorte, que
        son las decisiones que hay que poder verificar.

        Devuelve ``None`` si algún ángulo no es finito, en cuyo caso quien llame
        debe **reutilizar el último valor válido de ese brazo** — que se
        consigue omitiendo sus campos del JSON, no publicando ceros.
        """
        canal = Side.parse(canal)
        # Se comprueba AQUI, antes de tocar nada. Si se dejara para después, el
        # filtro de abducción podría procesar su muestra y el de flexión reventar
        # con la suya, dejando los dos ejes del mismo brazo desincronizados.
        if not (math.isfinite(abduccion) and math.isfinite(flexion)):
            return None

        # La ganancia se elige por el signo del movimiento DEL OPERADOR, antes de
        # invertir: se calibran contra lo que él alcanza cómodamente, no contra
        # lo que acabe haciendo el robot. Las dos funciones son continuas en 0,
        # así que no hay salto al cruzar.
        g_abd = self.gain_abduccion if abduccion >= 0.0 else self.gain_aduccion
        g_flex = self.gain_flexion if flexion >= 0.0 else self.gain_extension

        valor_abd = abduccion * g_abd * (-1.0 if self.invert_abduccion else 1.0)
        valor_flex = flexion * g_flex * (-1.0 if self.invert_flexion else 1.0)

        ahora = self._clock() if timestamp is None else timestamp
        valor_abd = self._filtros[(canal, "abduccion")](valor_abd, ahora)
        valor_flex = self._filtros[(canal, "flexion")](valor_flex, ahora)

        try:
            recorte = clamp_arm(valor_abd, valor_flex, canal)
        except ValueError:
            # clamp_arm rechaza NaN e infinitos a propósito, para que no lleguen
            # al robot. BlazePose los produce. Se descarta ESTE brazo; el otro
            # sigue su camino. Es la respuesta correcta al BUG 2.
            return None

        self._clamps[canal] = recorte
        return recorte.as_tuple

    def comando(
        self, pose: ArmAngles | None, timestamp: float | None = None
    ) -> ArmAngles:
        """Convierte la ESTIMACION en el COMANDO que se publica.

        Están separadas a propósito, y la distinción va a la tesis: ``process``
        devuelve lo que se pudo **medir** —con ``None`` donde no se pudo—, y
        esto devuelve lo que se le **manda** al robot, que nunca tiene huecos.
        Mezclarlas haría que el CSV no pudiera distinguir «el brazo estaba
        aquí» de «el brazo no se veía y el comando venía de otra parte», que es
        exactamente la ambigüedad del H26.

        Tres reglas, en orden:

        1. **Si el brazo se estimó**, el objetivo es lo estimado.
        2. **Si no, y lleva menos de ``gracia_s`` sin estimarse**, se mantiene el
           último objetivo. Es lo que evita que un parpadeo de visibilidad
           —frecuentísimo con la mano en el borde del cuadro— tire los brazos al
           suelo a mitad de gesto.
        3. **Si no, el objetivo es la pose de reposo.** Perder el seguimiento no
           debe dejar el brazo clavado donde estaba: el robot se queda con un
           gesto que ya nadie sostiene, y a un espectador le parece un cuelgue.

        Y sobre las tres, un **límite de velocidad angular** por canal. Es lo
        que hace que las transiciones se vean como un movimiento y no como un
        teletransporte, y protege de la discontinuidad que el One Euro no puede
        filtrar: tras un hueco largo su ``dt`` es grande, su factor de suavizado
        se va a 1 y la primera muestra de vuelta pasa entera. Ver
        ``filters.SlewLimiter``, que documenta el precio en latencia.

        Ojo a una consecuencia del H30: el reposo del robot **no es su cero**.
        Su cero son los brazos en cruz, así que la pose de reposo por defecto
        es −55° de abducción —lo más bajo que llega su mecanismo—, no 0.
        """
        ahora = self._clock() if timestamp is None else float(timestamp)
        salida = ArmAngles()
        for canal in (Side.L, Side.R):
            campo_abd, campo_flex = CANALES[canal]
            medido_abd = getattr(pose, campo_abd) if pose is not None else None
            medido_flex = getattr(pose, campo_flex) if pose is not None else None

            if medido_abd is not None and medido_flex is not None:
                objetivo = (medido_abd, medido_flex)
                self._t_estimado[canal] = ahora
            else:
                ultimo = self._t_estimado.get(canal)
                en_gracia = ultimo is not None and ahora - ultimo <= self.gracia_s
                # En gracia se conserva el OBJETIVO, no la salida actual: así el
                # brazo termina el movimiento que llevaba en vez de congelarse
                # donde le pilló el hueco.
                objetivo = self._objetivo[canal] if en_gracia else self._reposo[canal]

            self._objetivo[canal] = objetivo
            if self._mapeo == "direccion":
                abd, flex = self._limitadores_nd[canal](objetivo, ahora)
                setattr(salida, campo_abd, abd)
                setattr(salida, campo_flex, flex)
            else:
                limitador_abd, limitador_flex = self._limitadores[canal]
                setattr(salida, campo_abd, limitador_abd(objetivo[0], ahora))
                setattr(salida, campo_flex, limitador_flex(objetivo[1], ahora))
        return salida

    @property
    def estimado(self) -> dict[Side, bool]:
        """Si cada canal se estimó en el último ``process``.

        Es lo que distingue en el CSV un ángulo medido de uno que viene de la
        gracia o de la rampa al reposo. Sin esta columna, el analizador
        calcularía el temblor sobre tramos de rampa y daría una cifra que no
        es del estimador.
        """
        return dict(self._estimado)

    @property
    def objetivos(self) -> dict[Side, tuple[float, float]]:
        """Objetivo vigente de cada canal, antes del límite de velocidad."""
        return dict(self._objetivo)

    @property
    def reposo(self) -> dict[Side, tuple[float, float]]:
        """Pose de reposo por canal, ya recortada a lo que el robot admite."""
        return dict(self._reposo)

    def close(self) -> None:
        self._pose.close()

    def __enter__(self) -> "ArmTracker":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


# --------------------------------------------------------------------------
# Modo demo
# --------------------------------------------------------------------------

#: Altura y del bloque inferior del HUD.
ALTO_TEXTO = 160

#: Brillo medio (0-255) por debajo del cual se avisa de cámara negra.
#:
#: Medido el 2026-09-13 en la cámara integrada: con la exposición clavada en
#: manual −6 el brillo medio era **2.2**, y con la automática **117.9**. Una
#: escena legítimamente oscura pero utilizable se queda muy por encima de 8, así
#: que el umbral separa «hay poca luz» de «no hay imagen».
BRILLO_MINIMO: float = 8.0

#: Ancho en píxeles de las barras de rango.
ANCHO_BARRA = 150


def _dibujar_barra(frame_bgr, x: int, y: int, fraccion: float, recortado: bool) -> None:
    """Barra de posición dentro del rango ADMISIBLE del canal.

    En el brazo, el rango de abducción se interpola para la flexión actual, así
    que la barra se estrecha y se ensancha en vivo. Es el comportamiento
    correcto: enseña que el brazo gana recorrido al flexionarse y despejarse del
    tronco.
    """
    color = (0, 0, 255) if recortado else (0, 200, 0)
    cv2.rectangle(frame_bgr, (x, y - 8), (x + ANCHO_BARRA, y + 2), (90, 90, 90), 1)
    posicion = int(x + max(0.0, min(1.0, fraccion)) * ANCHO_BARRA)
    cv2.rectangle(frame_bgr, (posicion - 2, y - 8), (posicion + 2, y + 2), color, -1)


def _direccion_csv(direccion) -> list[str]:
    if direccion is None:
        return ["", "", ""]
    return [f"{v:.5f}" for v in direccion]


def _retarget_csv(paso: Retargeting | None, vivo: bool) -> list[str]:
    # Solo si se estimó ESTE frame: un ψ de hace un segundo no es una medida.
    if paso is None or not vivo:
        return ["", "", ""]
    return [f"{paso.psi_humano:.3f}", f"{paso.alfa:.3f}", str(paso.rama)]


def _demo(argv: list[str] | None = None) -> int:
    import argparse
    import csv

    parser = argparse.ArgumentParser(prog="arm_tracker")
    parser.add_argument(
        "--record",
        metavar="CSV",
        help="Registra cada frame en un CSV, con los angulos crudos del "
        "operador y los publicados al robot. Si el archivo ya existe, la "
        "grabacion anterior se conserva con el sufijo _anterior.",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publica la pose en el broker MQTT, para ver los brazos moverse "
        "en el visor 3D. Es la unica forma de verificar el SENTIDO de cada "
        "canal: el HUD da el signo, pero no hacia donde se mueve el robot.",
    )
    args = parser.parse_args(argv)

    cfg = AppConfig.from_env()
    cap = abrir_camara(cfg.camera)
    if cap is None:
        print(
            f"No se pudo abrir la camara {cfg.camera.index}. "
            f"Prueba 'python tools/list_cameras.py' para ver que indices hay."
        )
        return 1

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(describir(cap, cfg.camera) + "\n")

    print(
        "ENCUADRE\n"
        "  El arm tracker necesita HOMBROS y MANOS en cuadro, y a ser posible\n"
        "  tambien las caderas: de ellas sale la vertical del tronco. Si no se\n"
        "  ven, se usa la vertical de imagen como reserva, que asume que estas\n"
        "  sentado y erguido. El HUD dice cual de las tres esta activa:\n"
        "  'caderas' (vistas ahora), 'retenido Xs' (de hace un momento) o\n"
        "  'RESERVA vertical'.\n\n"
        "  MediaPipe solo dibuja los landmarks que pasan de 0.5, el mismo umbral\n"
        "  que usa el tracker. Asi que si no ves el esqueleto del brazo pintado,\n"
        "  el tracker tampoco lo esta usando.\n\n"
        "CUANDO SE PIERDE UN BRAZO\n"
        "  El canal no se congela: mantiene el ultimo objetivo medio segundo\n"
        "  —para absorber parpadeos— y luego vuelve al reposo por una rampa. El\n"
        "  HUD marca con '~' los canales cuyo valor NO viene de una medida de\n"
        "  este frame. Ojo: el reposo del robot no es su cero, son los brazos en\n"
        "  cruz; el reposo por defecto es -55 de abduccion (H30).\n\n"
        "VERIFICAR EL SENTIDO DE CADA CANAL (esto es lo importante)\n"
        "  Lanza con --publish y mira el visor. Mueve UN SOLO brazo cada vez y\n"
        "  comprueba las cuatro afirmaciones:\n"
        "    1. Separas tu brazo IZQUIERDO del cuerpo -> se separa el izquierdo\n"
        "       del robot (con el espejo en izq->izq).\n"
        "    2. Lo levantas hacia ADELANTE -> el del robot va hacia adelante.\n"
        "    3. y 4. Idem con el derecho.\n"
        "  Si un canal va al reves, corrigelo con 'b' (abduccion) o 'f'\n"
        "  (flexion) y anota el valor: son ARM_INVERT_ABDUCCION y\n"
        "  ARM_INVERT_FLEXION. NO deduzcas el signo mirando el HUD.\n\n"
        "  Ojo al comparar: el visor renderiza a pocos fps cuando no esta en\n"
        "  primer plano, y su camara sigue deslizandose por amortiguacion\n"
        "  varios segundos despues de un arrastre. Comparar dos capturas de\n"
        "  momentos distintos NO es fiable; ya llevo a una conclusion falsa una\n"
        "  vez. Mueve una sola cosa y no toques la camara entre medias.\n\n"
        "MAPEO (P5.3)\n"
        "  Por defecto 'direccion': se filtra y escala HACIA DONDE APUNTA el\n"
        "  brazo y los angulos del robot se calculan al final, con una sola\n"
        "  rama. Asi el brazo no da la vuelta al acercar las manos a la camara.\n"
        "  'k' alterna con el mapeo 'articular' del P5 para comparar. En\n"
        "  'direccion' las ganancias y las teclas b/f no intervienen.\n\n"
        "TECLAS\n"
        "  k = alternar mapeo (direccion <-> articular)\n"
        "  m = alternar espejo (que brazo manda sobre cual)\n"
        "  t = alternar destino (mano <-> codo)\n"
        "  b = invertir abduccion    f = invertir flexion\n"
        "  r = reiniciar extremos\n"
        "  z/Z = ganancia ABDUCCION -/+     x/X = ganancia ADUCCION  -/+\n"
        "  v/V = ganancia FLEXION   -/+     n/N = ganancia EXTENSION -/+\n"
        "  ESPACIO = cambiar de etiqueta (solo con --record)\n"
        "  q = salir\n"
    )

    tracker = ArmTracker(
        mirror=cfg.arm.mirror,
        frame_espejado=cfg.camera.flip,
        target=cfg.arm.target,
        invert_abduccion=cfg.arm.invert_abduccion,
        invert_flexion=cfg.arm.invert_flexion,
        gain_abduccion=cfg.arm.gain_abduccion,
        gain_aduccion=cfg.arm.gain_aduccion,
        gain_flexion=cfg.arm.gain_flexion,
        gain_extension=cfg.arm.gain_extension,
        min_cutoff=cfg.arm.min_cutoff,
        beta=cfg.arm.beta,
        min_visibility=cfg.arm.min_visibility,
        model_complexity=cfg.arm.model_complexity,
        reposo_abduccion=cfg.arm.reposo_abduccion,
        reposo_flexion=cfg.arm.reposo_flexion,
        gracia_s=cfg.arm.gracia_s,
        velocidad_max=cfg.arm.velocidad_max,
        mapeo=cfg.arm.mapeo,
        flexion_min=cfg.arm.flexion_min,
        alcance_humano=cfg.arm.alcance_humano,
        rodilla=cfg.arm.rodilla,
        margen_tabla=cfg.arm.margen_tabla,
        histeresis_rama=cfg.arm.histeresis_rama,
    )
    print(
        f"mapeo = {tracker.mapeo}   flexion en "
        f"[{tracker.flexion_range[0]:g}, {tracker.flexion_range[1]:g}]\n"
    )

    # Import perezoso a propósito: el módulo tiene que poder usarse sin
    # paho-mqtt instalado y sin broker levantado, que es como corre en pruebas.
    publicador = None
    if args.publish:
        from .publisher import PosePublisher

        publicador = PosePublisher(cfg.mqtt, cfg.rates).connect()
        if publicador.wait_connected(timeout=5.0):
            print(
                f"Publicando en {cfg.mqtt.host}:{cfg.mqtt.port} "
                f"topic '{cfg.mqtt.topic_pose}' a {cfg.rates.publish_hz:g} Hz\n"
            )
        else:
            print(
                f"AVISO: no hay enlace con el broker en {cfg.mqtt.host}:"
                f"{cfg.mqtt.port}. Se sigue reintentando; mira el contador "
                f"'perdidos' del HUD.\n"
            )

    registro = None
    escritor = None
    etiqueta = 0
    if args.record:
        apartado = apartar_si_existe(Path(args.record))
        if apartado is not None:
            print(f"AVISO: ese CSV ya existia. Se conserva como {apartado.name}.\n")
        registro = open(args.record, "w", newline="", encoding="utf-8")
        escritor = csv.writer(registro)
        # Los sufijos _L y _R son SIEMPRE el canal del robot, también en las
        # columnas crudo_* y vis_*. Es deliberado: el CSV tiene que poder leerse
        # sin volver a razonar sobre el espejo, y la columna 'espejo' deja
        # constancia de con qué política se grabó.
        #
        # Y una distinción que hay que respetar al leerlo: 'crudo_*' es lo
        # MEDIDO y 'arm*' es lo PUBLICADO. No siempre coinciden: cuando un
        # brazo no se ve, 'crudo_*' queda vacío y 'arm*' sigue teniendo valor,
        # porque el comando vuelve al reposo por una rampa. 'estimado_*' dice
        # cuál de los dos casos es cada fila, y sin esa columna el temblor se
        # calcularía sobre tramos de rampa.
        escritor.writerow(
            [
                "t", "etiqueta", "target", "espejo", "frame_espejado",
                "crudo_abd_L", "crudo_flex_L", "crudo_abd_R", "crudo_flex_R",
                "armL", "armLtw", "armR", "armRtw",
                "estimado_L", "estimado_R",
                "recortado_L", "recortado_R",
                "vis_hombro_L", "vis_destino_L", "vis_codo_L",
                "vis_hombro_R", "vis_destino_R", "vis_codo_R",
                "puntos_destino_L", "puntos_destino_R",
                "vis_cadera", "tronco_caderas", "tronco_origen",
                "gain_abduccion", "gain_aduccion",
                "gain_flexion", "gain_extension",
                "ancho", "alto",
                # P5.3. La dirección cruda permite reproducir OFFLINE cualquier
                # mapeo sobre esta misma sesión (tools/comparar_mapeo_brazos.py).
                "mapeo", "flexion_min",
                "h_lat_L", "h_sup_L", "h_ant_L",
                "h_lat_R", "h_sup_R", "h_ant_R",
                "psi_L", "alfa_L", "rama_L",
                "psi_R", "alfa_R", "rama_R",
            ]
        )
        print(f"Registrando en {args.record}. ESPACIO cambia de etiqueta.\n")

    t0 = time.perf_counter()
    marcas_fps: deque[float] = deque(maxlen=30)
    dibujo = mp.solutions.drawing_utils
    conexiones = mp.solutions.pose.POSE_CONNECTIONS

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if cfg.camera.flip:
                frame = cv2.flip(frame, 1)

            marcas_fps.append(time.perf_counter())
            fps_real = (
                (len(marcas_fps) - 1) / (marcas_fps[-1] - marcas_fps[0])
                if len(marcas_fps) > 1 and marcas_fps[-1] > marcas_fps[0]
                else 0.0
            )

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pose = tracker.process(rgb)
            # La estimación puede tener huecos; el comando no. Lo que se
            # publica, se graba y se muestra es el COMANDO, y la columna
            # 'estimado_*' dice si venía de una medida o de la rampa.
            mando = tracker.comando(pose)
            estimado = tracker.estimado
            puntos_destino = tracker.puntos_destino

            if tracker.landmarks_imagen is not None:
                dibujo.draw_landmarks(frame, tracker.landmarks_imagen, conexiones)

            crudos = tracker.raw_angles
            recortes = tracker.last_clamp
            direcciones = tracker.direcciones
            retargetings = tracker.retargetings
            for fila, canal in enumerate((Side.L, Side.R)):
                campo_abd, campo_flex = CANALES[canal]
                valor_abd = getattr(mando, campo_abd)
                valor_flex = getattr(mando, campo_flex)
                vivo = estimado.get(canal, False)
                recorte = recortes.get(canal)
                if recorte is not None and recorte.clamped and vivo:
                    color = (0, 0, 255)
                elif vivo:
                    color = (0, 255, 0)
                else:
                    color = (120, 120, 120)
                marca = " " if vivo else "~"
                texto_abd = "  --  " if valor_abd is None else f"{valor_abd:+7.2f}"
                texto_flex = "  --  " if valor_flex is None else f"{valor_flex:+7.2f}"
                y = 30 + fila * 52
                cv2.putText(
                    frame,
                    f"{campo_abd:5s}{marca}abd {texto_abd}   flex {texto_flex}",
                    (10, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.62, color, 2,
                )
                if recorte is not None:
                    _dibujar_barra(
                        frame, 10, y + 20,
                        recorte.abduccion.fraction, recorte.abduccion.clamped,
                    )
                    _dibujar_barra(
                        frame, 10 + ANCHO_BARRA + 20, y + 20,
                        recorte.flexion.fraction, recorte.flexion.clamped,
                    )
                crudo = crudos.get(canal)
                if crudo is not None:
                    cv2.putText(
                        frame,
                        f"crudo {crudo[0]:+6.1f} {crudo[1]:+6.1f}",
                        (10 + 2 * ANCHO_BARRA + 40, y + 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1,
                    )

            if tracker.mapeo == "direccion":
                partes = []
                for canal in (Side.L, Side.R):
                    paso = retargetings.get(canal)
                    if paso is not None and estimado.get(canal, False):
                        partes.append(
                            f"{canal.value}: psi {paso.psi_humano:5.1f}->"
                            f"{paso.psi_robot:4.1f} alfa {paso.alfa:5.1f}"
                        )
                cv2.putText(
                    frame,
                    "   ".join(partes),
                    (10, 118),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 200, 255), 1,
                )

            espejo = "izq->izq" if tracker.mirror else "izq->der"
            signos = (
                f"abd{'-' if tracker.invert_abduccion else '+'}"
                f" flex{'-' if tracker.invert_flexion else '+'}"
            )
            cv2.putText(
                frame,
                f"{tracker.mapeo}  {tracker.target}  {espejo}  {signos}   "
                f"{fps_real:4.1f} fps",
                (10, 134),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                (255, 255, 0) if fps_real >= 20 else (0, 165, 255), 2,
            )

            vis = tracker.visibilidades
            # Tres estados y no dos, y el del medio es el que faltaba: una
            # vertical retenida NO es una vertical observada. Decir 'caderas'
            # con la visibilidad de cadera a 0.00 era el HUD mintiendo.
            origen_tronco = tracker.origen_tronco
            edad = tracker.edad_tronco_s
            if origen_tronco == "caderas":
                texto_tronco, color_tronco = "caderas", (180, 255, 180)
            elif origen_tronco == "retenido":
                texto_tronco = f"retenido {edad:.1f}s" if edad is not None else "retenido"
                color_tronco = (0, 255, 255)
            else:
                texto_tronco, color_tronco = "RESERVA vertical", (0, 165, 255)
            cv2.putText(
                frame,
                f"tronco: {texto_tronco}   cadera {vis.get('cadera', 0.0):.2f}   "
                f"codo L {vis.get('codo_L', 0.0):.2f} R {vis.get('codo_R', 0.0):.2f}",
                (10, ALTO_TEXTO),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, color_tronco, 1,
            )

            sugeridas = tracker.suggested_gains()

            def _sug(clave: str) -> str:
                valor = sugeridas[clave]
                return "--" if valor is None else f"{valor:.2f}"

            cv2.putText(
                frame,
                f"ganancias {tracker.gain_abduccion:.2f}/{tracker.gain_aduccion:.2f}"
                f" {tracker.gain_flexion:.2f}/{tracker.gain_extension:.2f}"
                f"   sugeridas {_sug('abduccion')}/{_sug('aduccion')}"
                f" {_sug('flexion')}/{_sug('extension')}",
                (10, ALTO_TEXTO + 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 255, 180), 1,
            )

            if tracker.frames_sin_pose:
                cv2.putText(
                    frame,
                    f"sin pose ({tracker.frames_sin_pose})",
                    (10, ALTO_TEXTO + 44),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 165, 255), 2,
                )
                # Distinguir «no te detecto» de «no veo nada». Son dos fallos
                # distintos y la ventana se ve igual en los dos: el 2026-09-13
                # la cámara apareció negra a 30 fps con la exposición heredada
                # de otro programa, y sin este aviso no hay forma de saber que
                # el problema no es el detector. Se mide solo cuando ya hay
                # sospecha, para no pagar el promedio en cada frame.
                if tracker.frames_sin_pose > 30:
                    brillo = brillo_medio(frame)
                    if brillo < BRILLO_MINIMO:
                        cv2.putText(
                            frame,
                            f"CAMARA NEGRA (brillo {brillo:.1f}): revisa la tapa "
                            f"o pon TELMEOW_CAMERA_AUTO_EXPOSURE=1",
                            (10, ALTO_TEXTO + 66),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2,
                        )

            # Se publica SIEMPRE, también sin pose: es lo que hace que los
            # brazos vuelvan al reposo en vez de quedarse clavados cuando el
            # operador sale de cuadro.
            if publicador is not None:
                publicador.publish(
                    armL=mando.armL, armLtw=mando.armLtw,
                    armR=mando.armR, armRtw=mando.armRtw,
                )

            if escritor is not None:
                nada = (float("nan"), float("nan"))
                cl = crudos.get(Side.L, nada)
                cr = crudos.get(Side.R, nada)
                rl = recortes.get(Side.L)
                rr = recortes.get(Side.R)
                vivo_l = estimado.get(Side.L, False)
                vivo_r = estimado.get(Side.R, False)

                def _f(valor: float | None) -> str:
                    return "" if valor is None else f"{valor:.4f}"

                escritor.writerow(
                    [
                        f"{time.perf_counter() - t0:.4f}", etiqueta,
                        tracker.target, int(tracker.mirror),
                        int(tracker.frame_espejado),
                        f"{cl[0]:.4f}", f"{cl[1]:.4f}",
                        f"{cr[0]:.4f}", f"{cr[1]:.4f}",
                        _f(mando.armL), _f(mando.armLtw),
                        _f(mando.armR), _f(mando.armRtw),
                        int(vivo_l), int(vivo_r),
                        int(bool(rl and rl.clamped)), int(bool(rr and rr.clamped)),
                        f"{vis.get('hombro_L', float('nan')):.3f}",
                        f"{vis.get('destino_L', float('nan')):.3f}",
                        f"{vis.get('codo_L', float('nan')):.3f}",
                        f"{vis.get('hombro_R', float('nan')):.3f}",
                        f"{vis.get('destino_R', float('nan')):.3f}",
                        f"{vis.get('codo_R', float('nan')):.3f}",
                        puntos_destino.get(Side.L, ""),
                        puntos_destino.get(Side.R, ""),
                        f"{vis.get('cadera', float('nan')):.3f}",
                        int(tracker.tronco_de_caderas), origen_tronco,
                        f"{tracker.gain_abduccion:.2f}",
                        f"{tracker.gain_aduccion:.2f}",
                        f"{tracker.gain_flexion:.2f}",
                        f"{tracker.gain_extension:.2f}",
                        width, height,
                        tracker.mapeo, f"{tracker.flexion_range[0]:g}",
                        *_direccion_csv(direcciones.get(Side.L)),
                        *_direccion_csv(direcciones.get(Side.R)),
                        *_retarget_csv(retargetings.get(Side.L), vivo_l),
                        *_retarget_csv(retargetings.get(Side.R), vivo_r),
                    ]
                )
                cv2.putText(
                    frame,
                    f"etiqueta {etiqueta}",
                    (10, ALTO_TEXTO + 88),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 200, 100), 2,
                )

            if publicador is not None:
                enlace = "MQTT" if publicador.connected else "MQTT SIN ENLACE"
                cv2.putText(
                    frame,
                    f"{enlace}  {publicador.effective_hz:4.1f} Hz  "
                    f"enviados {publicador.message_count}  "
                    f"perdidos {publicador.failed_count}",
                    (10, ALTO_TEXTO + 88),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    (0, 255, 0) if publicador.connected else (0, 0, 255), 1,
                )

            cv2.imshow("Telmeow - arm tracker", frame)

            tecla = cv2.waitKey(1) & 0xFF
            if tecla == ord("q"):
                break
            elif tecla == ord(" "):
                etiqueta += 1
                print(f"--- etiqueta {etiqueta} ---")
            elif tecla == ord("m"):
                tracker.mirror = not tracker.mirror
                print(f"espejo: {'izq->izq' if tracker.mirror else 'izq->der'}")
            elif tecla == ord("t"):
                tracker.target = "codo" if tracker.target == "mano" else "mano"
                tracker.reset_extremes()
                print(f"destino = {tracker.target}")
            elif tecla == ord("k"):
                tracker.mapeo = (
                    "articular" if tracker.mapeo == "direccion" else "direccion"
                )
                print(f"mapeo = {tracker.mapeo}")
            elif tecla == ord("b"):
                tracker.invert_abduccion = not tracker.invert_abduccion
                print(f"invert_abduccion = {tracker.invert_abduccion}")
                if tracker.mapeo == "direccion":
                    print("  (no interviene en el mapeo 'direccion')")
            elif tecla == ord("f"):
                tracker.invert_flexion = not tracker.invert_flexion
                print(f"invert_flexion = {tracker.invert_flexion}")
                if tracker.mapeo == "direccion":
                    print("  (no interviene en el mapeo 'direccion')")
            elif tecla == ord("r"):
                tracker.reset_extremes()
                print("extremos reiniciados")
            elif tecla in (ord("z"), ord("Z")):
                paso = 0.05 if tecla == ord("Z") else -0.05
                tracker.gain_abduccion = max(
                    0.05, round(tracker.gain_abduccion + paso, 2)
                )
                print(f"ganancia ABDUCCION = {tracker.gain_abduccion:.2f}")
            elif tecla in (ord("x"), ord("X")):
                paso = 0.05 if tecla == ord("X") else -0.05
                tracker.gain_aduccion = max(
                    0.05, round(tracker.gain_aduccion + paso, 2)
                )
                print(f"ganancia ADUCCION = {tracker.gain_aduccion:.2f}")
            elif tecla in (ord("v"), ord("V")):
                paso = 0.05 if tecla == ord("V") else -0.05
                tracker.gain_flexion = max(0.05, round(tracker.gain_flexion + paso, 2))
                print(f"ganancia FLEXION = {tracker.gain_flexion:.2f}")
            elif tecla in (ord("n"), ord("N")):
                paso = 0.05 if tecla == ord("N") else -0.05
                tracker.gain_extension = max(
                    0.05, round(tracker.gain_extension + paso, 2)
                )
                print(f"ganancia EXTENSION = {tracker.gain_extension:.2f}")
    finally:
        tracker.close()
        cap.release()
        cv2.destroyAllWindows()
        if publicador is not None:
            print(
                f"\nMQTT: {publicador.message_count} enviados, "
                f"{publicador.dropped_count} descartados por tasa, "
                f"{publicador.failed_count} perdidos sin enlace"
            )
            publicador.disconnect()
        if registro is not None:
            registro.close()
            print(f"\nRegistro guardado en {args.record}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_demo())
