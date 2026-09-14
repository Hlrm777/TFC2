"""Filtrado temporal de las señales de visión.

MediaPipe produce jitter entre frames: aunque el operador esté quieto, los
landmarks bailan unos píxeles y los ángulos derivados de ellos oscilan. Sin
filtrar, el robot vibra.

El problema tiene una tensión intrínseca: cuanto más se suaviza, menos ruido
queda, pero más retardo se introduce — y en teleoperación el retardo se percibe
como desconexión entre el gesto y la respuesta. Este módulo ofrece las tres
herramientas que el proyecto necesita para tratarla.

DEPENDENCIAS
------------
Solo biblioteca estándar, igual que ``config.py`` y ``limits.py``. Toda la
lógica de filtrado se puede validar con ``pip install pytest && pytest``, sin
instalar TensorFlow ni MediaPipe.

SOBRE EL RELOJ
--------------
``OneEuroFilter`` adapta su corte a la velocidad de la señal, y esa velocidad se
estima con ``dt``, el tiempo entre muestras. Por eso el reloj no es un detalle:
en Windows, ``time.monotonic()`` tiene una resolución de ~15.625 ms, comparable
al ``dt`` de 33.3 ms de una cámara a 30 fps. Con ese reloj, el ``dt`` medido
saldría cuantizado (31.25 o 46.875 ms, hasta un 40 % de error) y a veces
exactamente 0, que en la formulación del filtro es una división por cero.

Se usa ``time.perf_counter``, igualmente monótono pero con resolución de
sub-microsegundo. Está medido y documentado en el hallazgo H11 del proyecto.
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable, Hashable

__all__ = ["OneEuroFilter", "EMAFilter", "SlewLimiter", "ClassHysteresis"]

#: Reloj por defecto. perf_counter y NO monotonic: ver la nota del módulo.
_DEFAULT_CLOCK = time.perf_counter

#: dt de reserva cuando todavía no hay ninguno válido (30 fps).
_FALLBACK_DT = 1.0 / 30.0


def _alpha(cutoff: float, dt: float) -> float:
    """Factor de suavizado de un pasabajos de primer orden.

    Es la discretización habitual: con ``tau = 1/(2*pi*fc)``, el factor vale
    ``dt / (tau + dt)``. Escrito como ``r/(r+1)`` con ``r = 2*pi*fc*dt`` para
    evitar una división intermedia.
    """
    r = 2.0 * math.pi * cutoff * dt
    return r / (r + 1.0)


class OneEuroFilter:
    """Filtro One Euro (Casiez, Roussel & Vogel, CHI 2012).

    Referencia
    ----------
    Géry Casiez, Nicolas Roussel y Daniel Vogel. «1 € Filter: A Simple
    Speed-based Low-pass Filter for Noisy Input in Interactive Systems».
    *Proceedings of the SIGCHI Conference on Human Factors in Computing Systems
    (CHI '12)*, ACM, 2012, pp. 2527-2530. DOI: 10.1145/2207676.2208639

    Por qué este y no un pasabajos fijo
    -----------------------------------
    Es el estándar en interacción en tiempo real porque resuelve la tensión
    entre ruido y retardo **adaptando el corte a la velocidad de la señal**:

    - Cuando la señal está quieta, la velocidad estimada es baja, el corte baja
      y el filtro suaviza fuerte. El jitter desaparece.
    - Cuando la señal se mueve rápido, la velocidad estimada sube, el corte sube
      y el filtro deja pasar el movimiento casi sin retardo.

    Un pasabajos fijo tiene que elegir: o suaviza el jitter y va con retardo, o
    responde rápido y tiembla. El One Euro no.

    Parámetros
    ----------
    min_cutoff:
        Frecuencia de corte (Hz) cuando la señal está quieta. **Es el mando del
        jitter en reposo**: bajarlo suaviza más.
    beta:
        Cuánto sube el corte con la velocidad. **Es el mando del retardo en
        movimiento**: subirlo hace el filtro más reactivo. Con ``beta = 0`` el
        filtro degenera en un pasabajos fijo a ``min_cutoff``.
    d_cutoff:
        Corte del pasabajos que suaviza la propia estimación de velocidad. Rara
        vez hace falta tocarlo.

    Ajuste recomendado por los autores: fijar ``beta = 0`` y bajar ``min_cutoff``
    hasta que el jitter en reposo sea aceptable; luego subir ``beta`` hasta que
    el retardo al moverse deje de notarse.
    """

    def __init__(
        self,
        min_cutoff: float = 1.0,
        beta: float = 0.0,
        d_cutoff: float = 1.0,
        *,
        clock: Callable[[], float] = _DEFAULT_CLOCK,
    ) -> None:
        if min_cutoff <= 0.0:
            raise ValueError(f"min_cutoff debe ser positivo, se recibió {min_cutoff!r}")
        if d_cutoff <= 0.0:
            raise ValueError(f"d_cutoff debe ser positivo, se recibió {d_cutoff!r}")
        if beta < 0.0:
            raise ValueError(f"beta no puede ser negativo, se recibió {beta!r}")

        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self._clock = clock

        self._x_prev: float | None = None
        self._dx_prev: float = 0.0
        self._t_prev: float | None = None
        self._last_dt: float | None = None
        self._degenerate_dt = 0

    @property
    def initialized(self) -> bool:
        return self._x_prev is not None

    @property
    def degenerate_dt_count(self) -> int:
        """Veces que llegó un ``dt <= 0`` y hubo que sustituirlo.

        Si esto crece, el reloj de quien llama no tiene resolución suficiente
        para la tasa de muestreo. Es exactamente el fallo que se documentó en el
        hallazgo H11, y conviene poder verlo en vez de que pase inadvertido.
        """
        return self._degenerate_dt

    def reset(self) -> None:
        """Olvida el estado. La siguiente muestra se toma como nueva."""
        self._x_prev = None
        self._dx_prev = 0.0
        self._t_prev = None
        self._last_dt = None

    def __call__(self, value: float, timestamp: float | None = None) -> float:
        """Filtra una muestra y devuelve el valor suavizado.

        Args:
            value: la muestra.
            timestamp: instante de la muestra, en segundos. Si se omite se toma
                del reloj interno. Debe venir de un reloj monótono de alta
                resolución (ver la nota del módulo).

        Raises:
            ValueError: si ``value`` no es finito. Un NaN que entrara aquí
                contaminaría el estado del filtro de forma permanente, porque
                NaN se propaga a todas las muestras siguientes.
        """
        value = float(value)
        if not math.isfinite(value):
            raise ValueError(f"la muestra no es finita: {value!r}")

        now = self._clock() if timestamp is None else float(timestamp)

        if self._x_prev is None:
            self._x_prev = value
            self._t_prev = now
            return value

        dt = now - self._t_prev
        if dt <= 0.0:
            # Dos muestras en el mismo tick del reloj, o un timestamp fuera de
            # orden. No se descarta la muestra -- lleva información -- pero no
            # se puede derivar con ella: se reutiliza el último dt válido.
            self._degenerate_dt += 1
            dt = self._last_dt if self._last_dt is not None else _FALLBACK_DT
        else:
            self._last_dt = dt

        # 1. Velocidad, suavizada a su vez para que el ruido de la derivada no
        #    dispare el corte en falso.
        dx = (value - self._x_prev) / dt
        dx_hat = self._dx_prev + _alpha(self.d_cutoff, dt) * (dx - self._dx_prev)

        # 2. El corte adaptativo: sube con la velocidad. Es el corazón del
        #    filtro y lo que lo distingue de un pasabajos fijo.
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)

        # 3. Pasabajos sobre el valor, con ese corte.
        x_hat = self._x_prev + _alpha(cutoff, dt) * (value - self._x_prev)

        self._x_prev = x_hat
        self._dx_prev = dx_hat
        self._t_prev = now
        return x_hat


class EMAFilter:
    """Media móvil exponencial, ``y = alpha*x + (1-alpha)*y_prev``.

    Es la línea base contra la que comparar el One Euro en el capítulo de
    resultados. Suaviza con un factor constante, así que **no puede** distinguir
    entre jitter en reposo y movimiento intencionado: el mismo ``alpha`` que
    quita el temblor introduce retardo cuando el operador se mueve.

    Deliberadamente no depende del tiempo entre muestras. Esa es justamente su
    limitación frente al One Euro, y conviene que la comparación la exhiba en
    vez de disimularla.

    Args:
        alpha: peso de la muestra nueva, en (0, 1]. Con 1.0 no filtra nada;
            cuanto más bajo, más suave y más retardo.
    """

    def __init__(self, alpha: float) -> None:
        if not 0.0 < alpha <= 1.0:
            raise ValueError(f"alpha debe estar en (0, 1], se recibió {alpha!r}")
        self.alpha = float(alpha)
        self._y: float | None = None

    @property
    def initialized(self) -> bool:
        return self._y is not None

    def reset(self) -> None:
        self._y = None

    def __call__(self, value: float, timestamp: float | None = None) -> float:
        """Filtra una muestra.

        ``timestamp`` se acepta y se ignora, para que el EMA sea intercambiable
        con el One Euro en el banco de comparación sin cambiar el código que los
        llama.
        """
        value = float(value)
        if not math.isfinite(value):
            raise ValueError(f"la muestra no es finita: {value!r}")
        if self._y is None:
            self._y = value
        else:
            self._y += self.alpha * (value - self._y)
        return self._y


class SlewLimiter:
    """Limita la velocidad de cambio de una señal, en unidades por segundo.

    No es un filtro de ruido y no sustituye al One Euro: no toca las muestras
    que cambian despacio. Lo que hace es **acotar el salto** entre dos muestras
    consecutivas, y por eso resuelve cosas que un pasabajos no resuelve:

    - **Discontinuidades de la propia estimación.** Cuando un brazo sale de
      cuadro y vuelve, el One Euro no protege: adapta su corte al ``dt``, y tras
      un hueco largo el ``dt`` es grande, el factor de suavizado se va a 1 y la
      primera muestra pasa **tal cual**. El robot da un salto. Aquí no.
    - **Vuelta a una pose de reposo.** Perder el seguimiento no debería dejar el
      brazo clavado a mitad de gesto, pero llevarlo al reposo de golpe es peor.
      Con el objetivo puesto en el reposo, el límite convierte el salto en una
      rampa de duración conocida: ``salto / max_rate``.
    - **Cambios de convenio en caliente.** Invertir un signo desde el teclado
      mueve el objetivo 2x; el límite lo hace una rampa en vez de un latigazo.

    El precio, y hay que declararlo porque el proyecto mide la latencia: un
    salto de ``d`` unidades tarda ``d / max_rate`` segundos en completarse. Por
    encima de ``max_rate`` la señal va con retardo. Se elige ``max_rate``
    **por encima de la velocidad del gesto que se quiere reproducir**, de modo
    que el límite solo actúe en las discontinuidades, que es para lo que está.

    Un servo real tiene su propia velocidad máxima, así que limitar el comando
    no es solo estético: acerca el comando a lo que el actuador puede seguir.

    Args:
        max_rate: velocidad máxima, en unidades por segundo. Positiva.
    """

    def __init__(
        self,
        max_rate: float,
        *,
        clock: Callable[[], float] = _DEFAULT_CLOCK,
    ) -> None:
        if not max_rate > 0.0:
            raise ValueError(f"max_rate debe ser positivo, se recibió {max_rate!r}")
        self.max_rate = float(max_rate)
        self._clock = clock
        self._y: float | None = None
        self._t_prev: float | None = None
        self._last_dt: float | None = None
        self._degenerate_dt = 0

    @property
    def initialized(self) -> bool:
        return self._y is not None

    @property
    def value(self) -> float | None:
        """Salida actual, sin consumir una muestra."""
        return self._y

    @property
    def degenerate_dt_count(self) -> int:
        """``dt`` no positivos que hubo que sustituir. Ver ``OneEuroFilter``."""
        return self._degenerate_dt

    def reset(self, value: float | None = None) -> None:
        """Olvida el estado, o lo fija en ``value`` sin rampa.

        Fijarlo es lo que se quiere al arrancar: la primera salida es el reposo,
        y a partir de ahí todo movimiento está limitado. Si se dejara sin
        inicializar, la primera muestra pasaría entera y el arranque sería
        justamente el salto que esta clase existe para evitar.
        """
        if value is not None:
            value = float(value)
            if not math.isfinite(value):
                raise ValueError(f"el valor no es finito: {value!r}")
        self._y = value
        self._t_prev = None
        self._last_dt = None

    def __call__(self, target: float, timestamp: float | None = None) -> float:
        """Avanza hacia ``target`` lo que el límite permita, y devuelve dónde
        se quedó.

        Raises:
            ValueError: si ``target`` no es finito. Igual que en el One Euro: un
                NaN contaminaría el estado de forma permanente.
        """
        target = float(target)
        if not math.isfinite(target):
            raise ValueError(f"el objetivo no es finito: {target!r}")

        now = self._clock() if timestamp is None else float(timestamp)

        if self._y is None:
            self._y = target
            self._t_prev = now
            return target

        if self._t_prev is None:
            # Estado fijado con reset(value): hay salida pero no instante. La
            # primera muestra ya se limita, con el dt de reserva.
            dt = _FALLBACK_DT
        else:
            dt = now - self._t_prev
            if dt <= 0.0:
                self._degenerate_dt += 1
                dt = self._last_dt if self._last_dt is not None else _FALLBACK_DT
            else:
                self._last_dt = dt

        maximo = self.max_rate * dt
        delta = target - self._y
        if delta > maximo:
            delta = maximo
        elif delta < -maximo:
            delta = -maximo

        self._y += delta
        self._t_prev = now
        return self._y


class ClassHysteresis:
    """Estabiliza una señal categórica ruidosa.

    Un clasificador que duda entre dos clases parecidas alterna entre ellas
    frame a frame. Aplicado a la emoción del robot, eso produce una cara que
    parpadea entre expresiones — peor que equivocarse de forma estable, porque
    además se ve como un fallo.

    La clase reportada solo cambia si la candidata cumple **las dos**
    condiciones:

    1. Gana por **mayoría estricta** en las últimas ``n_frames`` predicciones
       (más de la mitad de la ventana, no simple pluralidad).
    2. Su **confianza media** dentro de la ventana supera ``min_confidence``.

    Mientras no se cumplan, se mantiene la clase anterior. El precio es
    latencia: hacen falta al menos ``n_frames // 2 + 1`` predicciones
    coincidentes para cambiar.

    Cuidado con la tasa
    -------------------
    ``n_frames`` cuenta **llamadas**, no frames de vídeo. El clasificador de
    emoción corre a 5 Hz, no a 30: con ``n_frames = 5`` el cambio tarda hasta
    600 ms, y el crossfade del visor dura 350 ms más. Conviene elegir el valor
    pensando en esa tasa.

    Límite conocido: la mayoría no basta sola
    -----------------------------------------
    Ante una alternancia perfecta (A, B, A, B, ...) la ventana queda siempre 3-2
    y la mayoría cambia de bando cada dos pasos, así que **la histéresis por sí
    sola no elimina el parpadeo en ese caso**; solo lo reduce.

    Lo que sí lo elimina es ``min_confidence``, y por eso está: un clasificador
    que alterna entre dos clases es un clasificador dudando, y sus confianzas
    rondan el 0.5. Con ``min_confidence`` por encima de ese valor, el cambio se
    bloquea del todo. Las dos condiciones son complementarias, no redundantes:
    la mayoría filtra el ruido esporádico y la confianza filtra la duda
    sostenida.

    Args:
        n_frames: tamaño de la ventana. Debe ser >= 1.
        min_confidence: confianza media mínima para aceptar un cambio.
        initial: clase reportada antes de la primera decisión. El proyecto
            arranca en NEUTRAL por decisión de diseño documentada.
    """

    def __init__(
        self,
        n_frames: int = 5,
        min_confidence: float = 0.0,
        initial: Hashable | None = None,
    ) -> None:
        if n_frames < 1:
            raise ValueError(f"n_frames debe ser >= 1, se recibió {n_frames!r}")
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError(
                f"min_confidence debe estar en [0, 1], se recibió {min_confidence!r}"
            )
        self.n_frames = int(n_frames)
        self.min_confidence = float(min_confidence)
        self._window: deque[tuple[Hashable, float]] = deque(maxlen=self.n_frames)
        self._current: Hashable | None = initial

    @property
    def current(self) -> Hashable | None:
        """Clase reportada ahora mismo."""
        return self._current

    @property
    def window(self) -> tuple[tuple[Hashable, float], ...]:
        """Contenido de la ventana, para depuración y para el HUD."""
        return tuple(self._window)

    def reset(self, initial: Hashable | None = None) -> None:
        self._window.clear()
        self._current = initial

    def update(self, label: Hashable, confidence: float = 1.0) -> Hashable | None:
        """Añade una predicción y devuelve la clase estabilizada.

        Args:
            label: clase predicha en esta pasada.
            confidence: confianza de esa predicción, en [0, 1].

        Returns:
            La clase que debe mostrarse, que puede seguir siendo la anterior.
        """
        confidence = float(confidence)
        if not math.isfinite(confidence):
            raise ValueError(f"la confianza no es finita: {confidence!r}")
        self._window.append((label, confidence))

        if label == self._current:
            return self._current

        votos = [c for etiqueta, c in self._window if etiqueta == label]
        # Mayoría ESTRICTA sobre la ventana ya llena. Con la ventana a medio
        # llenar la comparación es contra su tamaño actual, así que arrancar es
        # rápido pero cambiar después exige el consenso completo.
        if len(votos) * 2 <= len(self._window):
            return self._current
        if (sum(votos) / len(votos)) < self.min_confidence:
            return self._current

        self._current = label
        return self._current
