"""El robot saluda solo (P5.7, H42).

POR QUE NO SE COPIA EL SALUDO
-----------------------------
Un saludo humano es un giro del antebrazo: el codo abajo y afuera, la mano por
encima del hombro y yendo de lado a lado. El robot no tiene antebrazo. En la
verificación del 2026-10-11, con la mano recogida y por fuera del tronco, el
mapeo ``posicion`` llevaba su brazo a la cruz —que es justo lo que hace bien el
«yo no fui»— y el vaivén de la mano apenas lo movía ±5°. Así que el robot no
imita el saludo: lo RECONOCE y saluda a su manera, con el saludo del visor
(botón «Saludo demo» de ``telmeow_viewer.html``), que es el que el operador ya
conoce. Lo eligió el operador entre tres caminos.

EL DETECTOR
-----------
Mira la mano en la IMAGEN —la coordenada buena de BlazePose— respecto a su
hombro, en anchos de hombro para que no dependa de la distancia a la cámara, y
pide cuatro cosas a la vez:

1. **La mano de fiar**: dentro del cuadro y con su rampa completa
   (``arm_tracker.RAMPA_MANO_S``). Lo decide quien llama: si no, no hay muestra.
2. **Por encima del hombro** (``ALTURA_MIN``), pero **no de la cabeza**
   (``ALTURA_MAX``, en mediana): las dos manos juntándose y separándose sobre
   la cabeza al narrar son otro gesto.
3. **Cerca del plano del hombro** (``PROFUNDIDAD_MAX``, con la profundidad YA
   corregida por el escorzo): es lo que separa el saludo de estirar los brazos
   hacia la cámara y recogerlos, que en la imagen también mueve la mano de lado
   a lado y con el mismo ritmo. Medido: la mano que saluda queda a −11..+12 cm
   del plano; la que se estira, a 10-40 cm. La profundidad de BlazePose sin
   corregir no sirve aquí: pone las dos a ~30 cm.
4. **Un vaivén con ritmo**: ``VAIVENES`` cambios de sentido seguidos, de al
   menos ``AMPLITUD`` cada uno y a menos de ``MEDIO_PERIODO_MAX`` el uno del
   otro. Encadenados, no solo contados: un gesto de narración con dos barridos
   grandes y separados no es un saludo.

Ajustado con las grabaciones del 2026-10-11 y comprobado con las del
2026-09-27: detecta los saludos del protocolo y el saludo con que el operador
abrió las dos narraciones, y en ningún otro sitio —ni al estirar los brazos,
ni en «yo no fui», ni en «el grito», ni en el resto de la narración—. El
precio es la latencia: cinco cambios de sentido son ~1.4 s de saludo antes de
que el robot empiece.

EL SALUDO DEL ROBOT
-------------------
El del visor (``WAVE_CENTER`` y su oscilación), llevado a la rama de la
cinemática inversa del Python: ``(a, f)`` y ``(−a, f − 180)`` ponen el brazo en
la misma dirección, girado 180° sobre su eje, que no se ve porque el brazo es
simétrico. Así el robot pasa de seguir al operador a saludar sin dar la vuelta.
Entra y sale con un paso suave de ``RAMPA``, como el del visor. La cabeza no
se toca: la lleva el head tracker.

Solo biblioteca estándar.
"""

from __future__ import annotations

import math
from collections import deque

__all__ = [
    "DetectorSaludo",
    "senales_saludo",
    "patron_saludo",
    "AMPLITUD",
    "VAIVENES",
    "MEDIO_PERIODO_MAX",
    "ALTURA_MIN",
    "ALTURA_MAX",
    "PROFUNDIDAD_MAX",
    "VENTANA",
    "FIN",
    "RAMPA",
]

#: Recorrido mínimo de cada vaivén de la mano, en anchos de hombro (~5 cm).
AMPLITUD: float = 0.15

#: Cambios de sentido seguidos para dar el saludo por empezado (~2.5 idas y
#: vueltas). Con cuatro se colaba un gesto de narración de 0.2 s.
VAIVENES: int = 5

#: Tiempo máximo entre dos cambios de sentido para que cuenten como el mismo
#: vaivén, en segundos. Los saludos medidos van de 0.27 a 0.57 s.
MEDIO_PERIODO_MAX: float = 0.7

#: Altura mínima de la mano sobre su hombro, en anchos de hombro. Medidos: los
#: saludos llevan la mano 0.2-0.65 anchos por encima.
ALTURA_MIN: float = 0.1

#: Altura máxima, en anchos de hombro: la mediana de la última ``VENTANA``.
#: Las manos por encima de la cabeza son otro gesto. En la sesión del
#: 2026-10-19 el operador, narrando, juntaba y separaba las dos manos sobre la
#: cabeza (a 1.35-1.87 anchos), y una de ellas pasaba por saludo; los saludos de
#: verdad van a 0.3-0.55 de mediana, y el que más sube, el que abre la
#: narración, toca 1.36 a ratos con la mediana en 0.55. Por eso una mediana y
#: no un tope por frame: un saludo que sube un momento no se corta.
ALTURA_MAX: float = 1.2

#: Profundidad máxima de la mano (corregida por escorzo) respecto al plano del
#: hombro, en metros: la mediana de la última ``VENTANA``.
PROFUNDIDAD_MAX: float = 0.15

#: Ventana de las medianas de altura y profundidad, en segundos.
VENTANA: float = 1.5

#: Segundos sin cambio de sentido para dar el saludo por terminado.
FIN: float = 0.8

#: Entrada y salida del saludo del robot, en segundos (la del visor, 900 ms).
RAMPA: float = 0.9

# -- el saludo del visor (telmeow_viewer.html, botón b-wave) -------------------
# WAVE_CENTER armR 30, armRtw 50; armR = 30 + 12·sin(1.2·s),
# armRtw = 50 + 8·sin(4·s), con s = t·3.64. Aquí, en la rama del Python.
_CENTRO_VISOR = (30.0, 50.0)
_AMPLITUD_VISOR = (12.0, 8.0)
_PULSACIONES_VISOR = (1.2, 4.0)
_CADENCIA_VISOR = 3.64


def patron_saludo(t: float) -> tuple[float, float]:
    """``(abduccion, flexion)`` del saludo del robot ``t`` segundos después de
    empezar, en la rama de la cinemática inversa del Python (abducción ≤ 0,
    flexión en [−180, 0]). Vale para los dos brazos: el robot es simétrico y el
    Python usa el mismo sentido en los dos."""
    s = t * _CADENCIA_VISOR
    abduccion = _CENTRO_VISOR[0] + _AMPLITUD_VISOR[0] * math.sin(_PULSACIONES_VISOR[0] * s)
    flexion = _CENTRO_VISOR[1] + _AMPLITUD_VISOR[1] * math.sin(_PULSACIONES_VISOR[1] * s)
    return (-abduccion, flexion - 180.0)


def senales_saludo(
    mano: tuple[float, float],
    hombro: tuple[float, float],
    hombro_contrario: tuple[float, float],
    profundidad: float,
) -> tuple[float, float, float] | None:
    """La muestra del detector desde los píxeles de un brazo: ``(x, y, p)``,
    con ``x`` la mano respecto al hombro en horizontal e ``y`` su altura sobre
    él (+ = por encima), las dos en anchos de hombro, y ``p`` la profundidad de
    la mano respecto al plano del hombro en metros (+ = hacia la cámara).
    ``None`` si algo no es finito o los hombros coinciden."""
    ancho = math.hypot(hombro[0] - hombro_contrario[0], hombro[1] - hombro_contrario[1])
    valores = (*mano, *hombro, profundidad)
    if not (ancho > 1e-6 and all(math.isfinite(v) for v in valores)):
        return None
    return ((mano[0] - hombro[0]) / ancho, (hombro[1] - mano[1]) / ancho, float(profundidad))


class DetectorSaludo:
    """Reconoce el saludo de UN brazo, frame a frame. Ver el docstring del
    módulo para el criterio y los umbrales."""

    def __init__(
        self,
        amplitud: float = AMPLITUD,
        vaivenes: int = VAIVENES,
        medio_periodo: float = MEDIO_PERIODO_MAX,
        altura: float = ALTURA_MIN,
        altura_max: float = ALTURA_MAX,
        profundidad: float = PROFUNDIDAD_MAX,
        ventana: float = VENTANA,
        fin: float = FIN,
    ) -> None:
        if not (
            amplitud > 0 and vaivenes >= 1 and medio_periodo > 0 and ventana > 0
            and fin > 0 and altura_max > altura
        ):
            raise ValueError("parámetros del detector de saludo no válidos")
        self.amplitud = float(amplitud)
        self.vaivenes = int(vaivenes)
        self.medio_periodo = float(medio_periodo)
        self.altura = float(altura)
        self.altura_max = float(altura_max)
        self.profundidad = float(profundidad)
        self.ventana = float(ventana)
        self.fin = float(fin)
        self.reiniciar()

    def reiniciar(self) -> None:
        self._sentido: int | None = None
        self._extremo: float | None = None
        self._cadena = 0
        self._ultimo_giro: float | None = None
        # (t, altura, profundidad) de la última VENTANA, para las medianas.
        self._recientes: deque[tuple[float, float, float]] = deque()
        self._activo = False

    @property
    def activo(self) -> bool:
        return self._activo

    def actualizar(self, t: float, muestra: tuple[float, float, float] | None) -> bool:
        """Una muestra de ``senales_saludo``, o ``None`` si la mano no es de
        fiar en este frame (fuera de cuadro, sin ver o en su rampa). Devuelve si
        el saludo está en curso."""
        if muestra is None or muestra[1] < self.altura:
            self.reiniciar()
            return False
        x, y, profundidad = muestra
        self._recientes.append((t, y, profundidad))
        while self._recientes and t - self._recientes[0][0] > self.ventana:
            self._recientes.popleft()

        giro = False
        if self._sentido is None:
            self._sentido, self._extremo = 0, x
        elif self._sentido == 0:
            # Todavía sin sentido: se fija al primer recorrido de AMPLITUD, sin
            # contarlo como cambio.
            if abs(x - self._extremo) >= self.amplitud:
                self._sentido = 1 if x > self._extremo else -1
                self._extremo = x
        elif (x - self._extremo) * self._sentido > 0:
            self._extremo = x          # sigue en el mismo sentido
        elif abs(x - self._extremo) >= self.amplitud:
            giro = True                # volvió AMPLITUD desde el extremo
            self._sentido = -self._sentido
            self._extremo = x

        if giro:
            encadenado = (
                self._ultimo_giro is not None
                and t - self._ultimo_giro <= self.medio_periodo
            )
            self._cadena = self._cadena + 1 if encadenado else 1
            self._ultimo_giro = t
        elif self._ultimo_giro is not None and t - self._ultimo_giro > self.medio_periodo:
            self._cadena = 0

        # Cerca del plano del hombro y no por encima de la cabeza, medido como
        # mediana: un momento fuera no corta un saludo.
        en_su_sitio = (
            self._mediana(2) <= self.profundidad
            and self._mediana(1) <= self.altura_max
        )
        if self._cadena >= self.vaivenes and en_su_sitio:
            self._activo = True
        if self._activo and (
            not en_su_sitio
            or self._ultimo_giro is None
            or t - self._ultimo_giro > self.fin
        ):
            self._activo = False
        return self._activo

    def _mediana(self, columna: int) -> float:
        valores = sorted(v[columna] for v in self._recientes if math.isfinite(v[columna]))
        if not valores:
            return 0.0
        return valores[len(valores) // 2]
