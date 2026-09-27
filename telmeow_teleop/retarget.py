"""Retargeting de la dirección del brazo del operador al cardán del robot.

Sustituye al mapeo ARTICULAR del P5 (descomponer el brazo del operador en los
ángulos del cardán, filtrar cada ángulo, recortarlo), que tenía tres defectos
de fondo. Los tres se midieron en la grabación del 2026-09-13 (P5.3 del plan
maestro):

1. **La costura del frente.** Con la flexión limitada a ±90°, «brazo al frente
   horizontal» solo se alcanza con la flexión en el tope, y admite DOS
   soluciones: ``(+ψ, +90)`` y ``(−ψ, −90)``. El mapeo articular elegía entre
   ellas con el signo de la componente vertical del brazo, que al frente ronda
   el cero y cambia de signo con el ruido. Cada cambio mandaba al comando al
   otro extremo, y el limitador lo interpolaba en espacio articular, pasando
   por la pose en cruz: **el brazo daba una vuelta entera para llegar a una
   posición contigua**. 303 saltos así en 9 minutos, con una mediana de 5° de
   cambio real en la dirección del brazo. El operador pasa el 25-29 % del tiempo
   a ±10° de esa costura: es la zona de «acercar las manos a la cámara».
2. **El filtro sobre ángulos.** Los ángulos del cardán son coordenadas
   esféricas: singulares en el polo (brazo en cruz) y discontinuos en la
   costura. Un pasabajos sobre ellos promedia +90 y −90 y se inventa un 0.
3. **El recorte articular.** El recorrido del hombro humano desborda el del
   robot, y recortar cada ángulo contra su tope dejaba el brazo pegado al
   límite el 67-75 % del tiempo, sin reproducir nada del gesto.

LA IDEA: TRABAJAR CON LA DIRECCIÓN, NO CON LOS ÁNGULOS
------------------------------------------------------
El robot tiene un brazo rígido orientado por un cardán de 2 GDL: lo único que
puede reproducir es **hacia dónde apunta** el brazo. Así que todo se hace sobre
esa dirección, un vector unitario ``h = (L, S, A)`` en el marco del tronco
(lateral hacia afuera, superior, anterior), y los ángulos articulares solo se
calculan al final:

1. **Filtrar el vector** (``filters.OneEuroVector``), que no tiene ni polo ni
   costura.
2. **Proyectarlo al hemisferio frontal.** El robot no puede llevar el brazo por
   detrás del plano frontal (plano posterior del visor).
3. **Expresarlo en polares respecto al polo del cardán**, que es la dirección
   del brazo en cruz (el eje de flexión)::

       ψ = ángulo entre el brazo y el lateral  (0 = en cruz)
       α = hacia dónde se separa de él:  0 = arriba, 90 = al frente, 180 = abajo

   Es la parametrización NATURAL de este mecanismo: la abducción del robot es
   ``±ψ`` y la flexión es ``α`` o ``α − 180``. Ver ``articulares``.
4. **Escalar ψ dentro de la envolvente del robot**, que depende de α y sale
   de las tablas 2D medidas (``alcance_robot``): arriba el robot llega a
   ~30°, al frente a ~70°, abajo a ~55°. El operador cubre ~90° en todas. Se
   escala en vez de recortar para que el gesto se conserve entero. Las
   ganancias provisionales que midió el P5.2 sin este modelo (0.33 en
   abducción, 0.51 en aducción) son justo estos cocientes: 30/90 y ~55/90.
5. **Resolver la cinemática inversa con una sola rama** (``RetargetBrazo``):
   con la flexión extendida a [−180, 90] (``limits.FLEXION_RANGE_EXTENDIDO``)
   la rama de abducción negativa cubre todo el hemisferio frontal y **la
   costura desaparece del espacio de trabajo**. Con la flexión en ±90 sigue
   funcionando, con histéresis, pero hay que pagar el cruce de la costura con
   un paso por la pose en cruz: es una limitación del mecanismo, no del
   estimador.
6. **Amortiguar la flexión cerca del polo.** Con el brazo casi en cruz, α no
   está definida y la flexión no mueve el brazo; dejarla seguir al ruido haría
   girar la pieza del hombro sobre sí misma.

Dependencias: solo biblioteca estándar y ``limits``, igual que ``filters``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .limits import (
    FLEXION_RANGE_EXTENDIDO,
    ArmClamp,
    Side,
    arm_abduccion_range_extendido,
    clamp_arm,
)

__all__ = [
    "polares",
    "direccion",
    "al_frente",
    "alcance_robot",
    "escala_radial",
    "articulares",
    "RetargetBrazo",
    "Retargeting",
    "ALCANCE_HUMANO",
]

#: Ángulo entre el brazo del operador y el lateral que se toma como su
#: recorrido completo, en grados. 90° es el brazo apuntando recto arriba, al
#: frente o abajo. Más allá (cruzando por delante del cuerpo) se satura.
ALCANCE_HUMANO: float = 90.0


# --------------------------------------------------------------------------
# Geometría
# --------------------------------------------------------------------------


def _unitario(h) -> tuple[float, float, float] | None:
    x, y, z = (float(v) for v in h)
    norma = math.sqrt(x * x + y * y + z * z)
    if not math.isfinite(norma) or norma < 1e-9:
        return None
    return (x / norma, y / norma, z / norma)


def polares(h) -> tuple[float, float]:
    """``(ψ, α)`` en grados de una dirección ``h = (L, S, A)``.

    ``ψ`` ∈ [0, 180] es el ángulo con el lateral (el polo). ``α`` ∈ (−180, 180]
    es el azimut alrededor del polo: 0 arriba, 90 al frente, ±180 abajo y
    negativo por detrás. En el polo ``α`` no está definida y vale lo que dé
    ``atan2(0, 0)``; quien la use ahí tiene que amortiguarla.
    """
    L, S, A = (float(v) for v in h)
    psi = math.degrees(math.atan2(math.hypot(S, A), L))
    alfa = math.degrees(math.atan2(A, S))
    return psi, alfa


def direccion(abduccion: float, flexion: float) -> tuple[float, float, float]:
    """Cinemática directa del cardán: ``(L, S, A)`` del brazo del robot.

    ``h = cos(a)·lat + sin(a)·[cos(f)·sup + sin(f)·ant]``, la misma que invierte
    ``arm_tracker.arm_angles`` y la que se verificó en el visor el 2026-09-07
    (P5.1): ``armR = −50, armRtw = −60`` lleva el brazo hacia adelante.
    """
    a = math.radians(abduccion)
    f = math.radians(flexion)
    return (math.cos(a), math.sin(a) * math.cos(f), math.sin(a) * math.sin(f))


def al_frente(h) -> tuple[float, float, float] | None:
    """Proyecta ``h`` al hemisferio frontal (``A ≥ 0``).

    Es el punto admisible más cercano: el robot no puede llevar el brazo por
    detrás del plano frontal. La proyección es continua y, en el caso extremo
    —el brazo apuntando justo hacia atrás—, cae en el polo, donde la flexión se
    amortigua; así que no hay salto ni siquiera ahí. Devuelve ``None`` si ``h``
    es degenerado.
    """
    unitario = _unitario(h)
    if unitario is None:
        return None
    L, S, A = unitario
    if A >= 0.0:
        return unitario
    return _unitario((L, S, 0.0)) or (1.0, 0.0, 0.0)


def alcance_robot(
    alfa: float,
    lado: Side | str,
    rama: int = -1,
    flexion_range: tuple[float, float] = FLEXION_RANGE_EXTENDIDO,
) -> float:
    """Ángulo máximo con el lateral que admite el robot en el azimut ``α``,
    usando la rama ``rama`` de la cinemática inversa.

    Sale de las tablas 2D de ``limits.py``, que son la autoridad: la flexión que
    corresponde a ``α`` en esa rama (recortada a ``flexion_range``) indexa la
    tabla, y el alcance es el tope de la abducción en el sentido de la rama —el
    mínimo, cambiado de signo, en la rama −1; el máximo en la +1—.

    En la rama −1 con la flexión extendida, ``α`` de 0 a 180 recorre la tabla de
    −180 a 0: la zona [−180, −95] medida en el P5.3 y la de agosto a
    continuación. Las dos casan en −95/−90 (−70 en los dos brazos), así que la
    envolvente es continua sin suavizar nada. Las cifras: ~30° arriba, ~70° al
    frente, ~55° abajo.
    """
    lado = Side.parse(lado)
    alfa = min(180.0, max(0.0, float(alfa)))
    flexion = articulares(0.0, alfa, rama)[1]
    flexion = min(flexion_range[1], max(flexion_range[0], flexion))
    lower, upper = arm_abduccion_range_extendido(flexion, lado)
    return max(0.0, -lower) if rama < 0 else max(0.0, upper)


def escala_radial(
    psi_humano: float,
    psi_max_robot: float,
    *,
    alcance_humano: float = ALCANCE_HUMANO,
    rodilla: float = 0.8,
) -> float:
    """Lleva el ángulo con el lateral del operador al del robot.

    Proporcional con saturación suave: el operador con el brazo colgando o al
    frente (ψ = ``alcance_humano``) pide **exactamente** el máximo del robot en
    esa dirección —con el brazo colgando, el reposo—, y más allá (cruzando por
    delante del cuerpo) se queda en él.

    La curva, con ``x = ψ_h / alcance`` y ``k = rodilla``::

        y = s·x                                   x ≤ k
        y = s·k + s·(x−k) − s·(x−k)² / (2(1−k))    k < x < 1
        y = 1                                     x ≥ 1
        s = 2 / (1 + k)

    Es continua y con derivada continua (C¹): llega al tope con pendiente cero.
    Un tope duro convierte el ruido de un operador que pasa del límite en un
    brazo que tiembla contra él; la rodilla lo absorbe. El precio es una
    ganancia algo mayor que la nominal en el tramo lineal (1.11 con k = 0.8).
    """
    if not 0.0 < rodilla < 1.0:
        raise ValueError("rodilla debe estar entre 0 y 1")
    if alcance_humano <= 0.0:
        raise ValueError("alcance_humano debe ser positivo")
    if psi_max_robot <= 0.0:
        return 0.0
    x = max(0.0, float(psi_humano)) / alcance_humano
    k = rodilla
    pendiente = 2.0 / (1.0 + k)
    if x <= k:
        y = pendiente * x
    elif x < 1.0:
        d = x - k
        y = pendiente * (k + d - d * d / (2.0 * (1.0 - k)))
    else:
        y = 1.0
    return psi_max_robot * y


def articulares(psi: float, alfa: float, rama: int) -> tuple[float, float]:
    """``(abducción, flexión)`` que ponen el brazo del robot en ``(ψ, α)``.

    Hay dos soluciones para cada dirección, y ``rama`` elige cuál:

    - ``rama = −1``: abducción ``−ψ``, flexión ``α − 180``. Es la del reposo
      (brazo abajo: ``(−55, 0)``) y la que se usa por defecto.
    - ``rama = +1``: abducción ``+ψ``, flexión ``α``.

    Ponen el brazo en la misma dirección, girado 180° sobre su propio eje, que
    en este brazo no se ve porque es simétrico alrededor de él.
    """
    if rama < 0:
        return (-psi, alfa - 180.0)
    return (psi, alfa)


def _suave(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3.0 - 2.0 * x)


# --------------------------------------------------------------------------
# Retargeting con estado, por brazo
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Retargeting:
    """Lo que salió de un paso de ``RetargetBrazo``, para el HUD y el CSV."""

    abduccion: float
    flexion: float
    psi_humano: float
    alfa: float
    psi_robot: float
    rama: int
    cambio_rama: bool
    recorte: ArmClamp

    @property
    def as_tuple(self) -> tuple[float, float]:
        return (self.abduccion, self.flexion)


class RetargetBrazo:
    """Dirección del brazo del operador → ``(abducción, flexión)`` del robot.

    Es con estado por dos motivos, los dos de continuidad: la **rama** en uso
    (solo cambia si la actual deja de alcanzar el objetivo) y la **flexión
    anterior** (que se amortigua cerca del polo).

    Args:
        lado: canal del robot, para las tablas.
        flexion_range: límite duro de la flexión. Con el extendido,
            ``(−180, 90)``, la rama −1 cubre todo el hemisferio frontal y nunca
            hace falta cambiar de rama. Con ``(−90, 90)`` hace falta en la
            costura del frente.
        rama_preferida: la del reposo. Es con la que se arranca.
        alcance_humano, rodilla: ver ``escala_radial``.
        margen: grados que se quitan a la envolvente de la tabla, para que la
            segunda capa (el BVH del visor) no tenga que actuar en el borde y
            el brazo no vibre contra ella.
        histeresis: cuánto puede pasarse el objetivo del tope de flexión de la
            rama actual, en grados de α, antes de cambiar de rama. Mientras
            tanto el brazo se queda en el tope, que en la costura es la pose
            correcta. Solo actúa con ``flexion_range`` de ±90.
        polo: ``(ψ0, ψ1)`` en grados de ψ del robot. Por debajo de ψ0 la
            flexión se congela; entre ψ0 y ψ1 se le deja seguir cada vez más.
    """

    def __init__(
        self,
        lado: Side | str,
        *,
        flexion_range: tuple[float, float] = FLEXION_RANGE_EXTENDIDO,
        rama_preferida: int = -1,
        alcance_humano: float = ALCANCE_HUMANO,
        rodilla: float = 0.8,
        margen: float = 2.0,
        histeresis: float = 15.0,
        polo: tuple[float, float] = (5.0, 20.0),
    ) -> None:
        if rama_preferida not in (-1, 1):
            raise ValueError("rama_preferida debe ser -1 o +1")
        if not flexion_range[0] < flexion_range[1]:
            raise ValueError("flexion_range vacío")
        if alcance_humano <= 0.0:
            raise ValueError("alcance_humano debe ser positivo")
        if margen < 0.0 or histeresis < 0.0:
            raise ValueError("margen e histeresis no pueden ser negativos")
        if not 0.0 <= polo[0] < polo[1]:
            raise ValueError("polo debe cumplir 0 <= psi0 < psi1")
        self.lado = Side.parse(lado)
        self.flexion_range = (float(flexion_range[0]), float(flexion_range[1]))
        self.rama_preferida = rama_preferida
        self.alcance_humano = float(alcance_humano)
        if not 0.0 < rodilla < 1.0:
            raise ValueError("rodilla debe estar entre 0 y 1")
        self.rodilla = float(rodilla)
        self.margen = float(margen)
        self.histeresis = float(histeresis)
        self.polo = (float(polo[0]), float(polo[1]))
        self._rama = rama_preferida
        self._flexion: float | None = None
        self._cambios = 0

    @property
    def rama(self) -> int:
        return self._rama

    @property
    def cambios_de_rama(self) -> int:
        """Cambios de rama desde el arranque. Con la flexión extendida debe
        quedarse en 0; con ±90 cuenta cuántas veces se cruzó la costura."""
        return self._cambios

    def reset(self, abduccion: float | None = None, flexion: float | None = None) -> None:
        """Vuelve al estado inicial, o lo fija en una pose dada (por ejemplo la
        de reposo) para que la rama y la flexión arranquen coherentes."""
        self._rama = self.rama_preferida
        self._flexion = None
        if abduccion is not None and abduccion != 0.0:
            self._rama = -1 if abduccion < 0.0 else 1
        if flexion is not None and math.isfinite(flexion):
            self._flexion = float(flexion)

    def _flexion_de(self, rama: int, alfa: float) -> float:
        return articulares(0.0, alfa, rama)[1]

    def _admite(self, rama: int, alfa: float, tolerancia: float) -> bool:
        f = self._flexion_de(rama, alfa)
        return self.flexion_range[0] - tolerancia <= f <= self.flexion_range[1] + tolerancia

    def _psi_robot(self, psi_humano: float, alfa: float, rama: int) -> float:
        psi_max = alcance_robot(alfa, self.lado, rama, self.flexion_range)
        return escala_radial(
            psi_humano,
            max(0.0, psi_max - self.margen),
            alcance_humano=self.alcance_humano,
            rodilla=self.rodilla,
        )

    def __call__(self, h) -> Retargeting | None:
        """Un paso. ``h`` es la dirección hombro→destino del operador en el
        marco del robot, ``(L, S, A)``; no hace falta que sea unitaria.

        Devuelve ``None`` si ``h`` es degenerado o no finito; quien llame
        conserva el último valor.
        """
        try:
            frontal = al_frente(h)
        except (TypeError, ValueError):
            return None
        if frontal is None:
            return None
        psi_h, alfa = polares(frontal)
        # Tras proyectar al frente, α ∈ [0, 180] salvo redondeo.
        alfa = min(180.0, max(0.0, alfa))

        # -- rama -----------------------------------------------------------
        # Se decide ANTES de escalar, porque el alcance depende de la rama.
        cambio = False
        if not self._admite(self._rama, alfa, self.histeresis):
            otra = -self._rama
            if self._admite(otra, alfa, 0.0):
                self._rama = otra
                cambio = True
        elif (
            self._rama != self.rama_preferida
            and self._psi_robot(psi_h, alfa, self._rama) < self.polo[0]
            and self._admite(self.rama_preferida, alfa, 0.0)
        ):
            # Cerca del polo el cambio es gratis (el brazo apenas se mueve):
            # se aprovecha para volver a la rama del reposo.
            self._rama = self.rama_preferida
            cambio = True
        if cambio:
            self._cambios += 1

        psi_r = self._psi_robot(psi_h, alfa, self._rama)

        flexion_objetivo = min(
            self.flexion_range[1],
            max(self.flexion_range[0], self._flexion_de(self._rama, alfa)),
        )

        # -- amortiguación en el polo ----------------------------------------
        # Con el brazo casi en cruz la flexión no mueve el brazo y α es puro
        # ruido. Se le deja seguir en proporción a lo lejos que esté del polo.
        if self._flexion is None or cambio:
            flexion = flexion_objetivo
        else:
            peso = _suave((psi_r - self.polo[0]) / (self.polo[1] - self.polo[0]))
            flexion = self._flexion + peso * (flexion_objetivo - self._flexion)

        abduccion = self._rama * psi_r
        # Segunda comprobación contra la tabla, que es la autoridad. Con el
        # margen no debería recortar salvo cerca del polo y en el tope de la
        # flexión en modo ±90.
        recorte = clamp_arm(
            abduccion, flexion, self.lado, flexion_range=self.flexion_range
        )
        abduccion, flexion = recorte.as_tuple
        self._flexion = flexion
        return Retargeting(
            abduccion=abduccion,
            flexion=flexion,
            psi_humano=psi_h,
            alfa=alfa,
            psi_robot=psi_r,
            rama=self._rama,
            cambio_rama=cambio,
            recorte=recorte,
        )
