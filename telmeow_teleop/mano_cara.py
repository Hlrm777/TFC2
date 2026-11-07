"""El robot se lleva la mano a la cara (P7.5).

POR QUE SE RECONOCE Y NO SE COPIA
---------------------------------
Con una mano en la cara el brazo del robot saltaba 2.6-5.1° por frame (0.1° con
la mano en otro sitio; sesión ``p7_final``). La mano queda delante de la cabeza,
BlazePose la pierde y la recupera, y cada vez el brazo se va del gesto al codo y
vuelve. Y para nada: el robot no tiene codo, así que su mano no llega a la cara
por mucho que se copie. De modo que, como con el saludo (H42), el robot no imita
el gesto: lo RECONOCE y lo hace a su manera, cada brazo por su lado. Una mano
del operador en la cara lleva a la cara el brazo de ese lado y el otro sigue a
lo suyo; con las dos, los dos, como en «El grito». Lo eligió el operador.

EL DETECTOR
-----------
Mira la mano en la imagen respecto a la MITAD DE LOS HOMBROS, en anchos de
hombro para que no dependa de la distancia a la cámara. Con los hombros y no con
la caja de la cara, porque con la cara tapada Face Mesh se inventa la cara (H58)
y los hombros siguen donde están. No hace falta más: la cara está encima de la
mitad de los hombros, y lo que hay que separar son las manos por dentro de los
hombros y por encima de su línea de las manos en cualquier otro sitio.

Tiene dos zonas para no parpadear en el borde. Para ENTRAR, la mano tiene que
ser de fiar (dentro del cuadro y con su rampa completa, como en el saludo) y
estar en ``ZONA_ENTRADA`` durante ``T_ENTRADA``: pasar la mano por delante de la
cara al narrar no cuenta. Para SALIR tiene que estar fuera de ``ZONA_SALIDA``
(más ancha) durante ``T_SALIDA``, y ahí da igual que sea de fiar: con la mano
delante de la cara BlazePose la pierde a ratos, y eso es justo lo que no tiene
que sacar al robot del gesto.

Medido (``tools/analizar_mano_cara.py``), una mano en la cara cae en
``x`` 0.07-0.40 e ``y`` −0.02..+0.35 («el grito» y cara tapada); «yo no fui», el
saludo y estirar los brazos, en ``x`` ≥ 0.63. El límite conocido es el H55: con
la mano plana contra la oreja BlazePose la pone fuera del cuadro y no se reconoce.

LA POSE DEL ROBOT
-----------------
``POSE`` es, de entre las poses que admiten las tablas de ``limits.py`` con 3° de
margen, la que deja la bolita de la mano más cerca de la pantalla de los ojos:
~20 cm del centro de la pantalla, a la altura de la barbilla y a 6 cm del lado de
la cabeza. Sale de ``tools/pose_mano_cara.py`` con las mallas y la cadena del
visor. Con la cabeza en cualquier sitio de su recorrido la holgura brazo-cabeza
es ≥ 33 mm, cosa que hay que medir aparte porque el visor no comprueba ese par.
Es la misma para los dos brazos: ni las tablas ni la geometría los distinguen a
esta resolución. Y es adonde ya llegaba el mapeo cuando BlazePose veía bien la
mano: en «el grito» del 2026-10-11 los brazos se asentaban en (−58, −120) y
(−55, −128). Lo que cambia no es el destino, es que deja de temblar.

Entra y sale con un paso suave de ``RAMPA``, como el saludo. La cabeza no se toca.

Solo biblioteca estándar.
"""

from __future__ import annotations

import math

__all__ = [
    "DetectorManoCara",
    "senales_mano_cara",
    "POSE",
    "RAMPA",
    "ZONA_ENTRADA",
    "ZONA_SALIDA",
    "T_ENTRADA",
    "T_SALIDA",
]

#: ``(abduccion, flexion)`` del brazo del robot con la mano en la cara, en la
#: rama de la cinemática inversa del Python. Ver el docstring del módulo.
POSE: tuple[float, float] = (-57.0, -125.0)

#: ``(x_min, x_max, y_min, y_max)`` en anchos de hombro desde la mitad de los
#: hombros: ``x`` hacia el lado de la propia mano (0.5 es su hombro, negativo es
#: cruzar al otro lado) e ``y`` hacia arriba (0 es la línea de los hombros).
ZONA_ENTRADA: tuple[float, float, float, float] = (-0.35, 0.45, -0.05, 0.80)

#: La de salida, más ancha, para que la mano no parpadee en el borde.
ZONA_SALIDA: tuple[float, float, float, float] = (-0.60, 0.60, -0.30, 1.00)

#: Segundos seguidos en la zona de entrada, con la mano de fiar, para reconocer
#: el gesto: la mano que cruza por delante de la cara al narrar no llega.
T_ENTRADA: float = 0.3

#: Segundos seguidos fuera de la zona de salida para darlo por terminado: lo que
#: tarda el robot en empezar a bajar el brazo. Con 0.5 ya aguanta las pérdidas de
#: BlazePose: en ``p7_final`` el derecho se pierde a ratos durante los 23 s de cara
#: tapada y el gesto no se corta ni una vez, igual que con 1.0.
T_SALIDA: float = 0.5

#: Entrada y salida del brazo en la pose, en segundos (la del saludo).
RAMPA: float = 0.9


def senales_mano_cara(
    mano: tuple[float, float],
    hombro: tuple[float, float],
    hombro_contrario: tuple[float, float],
) -> tuple[float, float] | None:
    """La muestra del detector desde los píxeles de un brazo: ``(x, y)``, ver
    ``ZONA_ENTRADA``. ``None`` si algo no es finito o los hombros coinciden."""
    ancho = math.hypot(hombro[0] - hombro_contrario[0], hombro[1] - hombro_contrario[1])
    if not (ancho > 1e-6 and all(math.isfinite(v) for v in (*mano, *hombro, *hombro_contrario))):
        return None
    signo = 1.0 if hombro[0] >= hombro_contrario[0] else -1.0
    medio_x = (hombro[0] + hombro_contrario[0]) / 2.0
    medio_y = (hombro[1] + hombro_contrario[1]) / 2.0
    return (signo * (mano[0] - medio_x) / ancho, (medio_y - mano[1]) / ancho)


def _en_zona(muestra: tuple[float, float], zona: tuple[float, float, float, float]) -> bool:
    x, y = muestra
    return zona[0] <= x <= zona[1] and zona[2] <= y <= zona[3]


class DetectorManoCara:
    """Reconoce la mano en la cara de UN brazo, frame a frame. Ver el docstring
    del módulo para el criterio y los umbrales."""

    def __init__(
        self,
        zona_entrada: tuple[float, float, float, float] = ZONA_ENTRADA,
        zona_salida: tuple[float, float, float, float] = ZONA_SALIDA,
        t_entrada: float = T_ENTRADA,
        t_salida: float = T_SALIDA,
    ) -> None:
        if not (t_entrada >= 0.0 and t_salida >= 0.0):
            raise ValueError("los tiempos del detector no pueden ser negativos")
        contenida = (
            zona_salida[0] <= zona_entrada[0] and zona_entrada[1] <= zona_salida[1]
            and zona_salida[2] <= zona_entrada[2] and zona_entrada[3] <= zona_salida[3]
        )
        if not contenida:
            # Si no, se podría estar a la vez dentro de la de entrada y fuera de
            # la de salida: el detector oscilaría sin que la mano se mueva.
            raise ValueError("la zona de entrada tiene que caber en la de salida")
        self.zona_entrada = zona_entrada
        self.zona_salida = zona_salida
        self.t_entrada = float(t_entrada)
        self.t_salida = float(t_salida)
        self.reiniciar()

    def reiniciar(self) -> None:
        self._activo = False
        self._dentro_desde: float | None = None
        self._fuera_desde: float | None = None

    @property
    def activo(self) -> bool:
        return self._activo

    def actualizar(
        self, t: float, muestra: tuple[float, float] | None, de_fiar: bool = True
    ) -> bool:
        """Una muestra de ``senales_mano_cara``, o ``None`` si no se pudo medir
        la mano en este frame. ``de_fiar`` solo cuenta para entrar. Devuelve si
        la mano está en la cara."""
        if not self._activo:
            if muestra is not None and de_fiar and _en_zona(muestra, self.zona_entrada):
                if self._dentro_desde is None:
                    self._dentro_desde = t
                if t - self._dentro_desde >= self.t_entrada:
                    self._activo = True
                    self._fuera_desde = None
            else:
                self._dentro_desde = None
            return self._activo

        if muestra is not None and _en_zona(muestra, self.zona_salida):
            self._fuera_desde = None
            return True
        if self._fuera_desde is None:
            self._fuera_desde = t
        if t - self._fuera_desde >= self.t_salida:
            self._activo = False
            self._dentro_desde = None
        return self._activo
