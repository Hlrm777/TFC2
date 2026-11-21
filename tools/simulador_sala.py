"""Una sala de mentira para probar la voz del robot sin altavoz ni micrófono.

``voz_sintetica`` fabrica algo que se parece a la voz para un cancelador de
eco: ruido con el espectro de la voz (más fuerte en los graves), que sube y
baja como las sílabas (unas 4 por segundo) y con pausas entre frases. No es
voz de verdad —no se entiende nada—, pero tiene lo que le importa al filtro:
energía repartida por las frecuencias de la voz y silencios.

``camino_eco`` es el camino altavoz → aire → micrófono: un retraso (el de la
tarjeta y el aire) y una cola que se apaga (los rebotes en la carcasa y la
sala), con la ganancia que se pida.

``SalaSimulada`` hace de tarjeta de sonido para ``raspberry/robot_audio.py``:
cada 20 ms le da al programa lo que «grabó el micrófono» —el eco de lo que el
programa mandó sonar, más un niño que habla cuando toca, más un poco de
ruido— y se queda con lo que el programa mandó sonar. Lleva por separado el
eco y el niño, para poder medir cuánto eco quitó el programa y cuánto niño
dejó pasar.

``DispositivoSintetico`` hace de tarjeta de la PC: su micrófono «dice» una voz
sintética y guarda lo que le llega.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

import numpy as np

TASA = 16_000
MUESTRAS = 320


def voz_sintetica(segundos: float, semilla: int, tasa: int = TASA, nivel: float = 0.1,
                  pausas: bool = True) -> np.ndarray:
    """Algo con el espectro y el ritmo de la voz. ``nivel``: valor eficaz al hablar."""
    rng = np.random.default_rng(semilla)
    n = int(segundos * tasa)
    ruido = rng.standard_normal(n)
    # Graves fuertes, agudos débiles: un filtro de un polo (como la voz, ~−6 dB/octava).
    color = np.empty(n)
    acumulado = 0.0
    for i, v in enumerate(ruido):
        acumulado = 0.95 * acumulado + v
        color[i] = acumulado
    color -= np.convolve(color, np.ones(64) / 64, mode="same")   # sin lo más grave (<~250 Hz)
    t = np.arange(n) / tasa
    silabas = np.clip(np.sin(2 * np.pi * (3.5 + rng.uniform(-0.5, 0.5)) * t
                             + rng.uniform(0, 2 * np.pi)), 0, None) ** 0.7
    envolvente = silabas
    if pausas:
        frases = np.ones(n)
        i = 0
        while i < n:
            largo = int(rng.uniform(1.0, 2.5) * tasa)
            pausa = int(rng.uniform(0.2, 0.6) * tasa)
            frases[i + largo:i + largo + pausa] = 0.0
            i += largo + pausa
        envolvente = envolvente * frases
    senal = color * envolvente
    eficaz = np.sqrt(np.mean(senal[envolvente > 0.3] ** 2)) if np.any(envolvente > 0.3) else 1.0
    return (senal / eficaz * nivel).astype(np.float64)


def camino_eco(retraso_s: float = 0.06, cola_s: float = 0.08, ganancia: float = 0.7,
               semilla: int = 7, tasa: int = TASA) -> np.ndarray:
    """La respuesta del camino altavoz → micrófono."""
    rng = np.random.default_rng(semilla)
    retraso = int(retraso_s * tasa)
    cola = int(cola_s * tasa)
    h = np.zeros(retraso + cola)
    caida = np.exp(-np.arange(cola) / (cola / 5))
    h[retraso:] = rng.standard_normal(cola) * caida
    h[retraso] += 3.0                      # el camino directo, el más fuerte
    # Ganancia: el eco tiene ``ganancia`` veces la energía de lo que sonó.
    return h * np.sqrt(ganancia / np.sum(h ** 2))


class _Reloj20ms:
    """Llama a ``tic`` cada 20 ms en un hilo, sin acumular retraso."""

    def __init__(self, tic: Callable[[], None], tiempo_real: bool = True):
        self._tic, self._tiempo_real = tic, tiempo_real
        self._seguir = False
        self._hilo: threading.Thread | None = None

    def _bucle(self) -> None:
        siguiente = time.perf_counter()
        while self._seguir:
            self._tic()
            if self._tiempo_real:
                siguiente += MUESTRAS / TASA
                espera = siguiente - time.perf_counter()
                if espera > 0:
                    time.sleep(espera)
                else:
                    siguiente = time.perf_counter()

    def empezar(self) -> None:
        self._seguir = True
        self._hilo = threading.Thread(target=self._bucle, daemon=True)
        self._hilo.start()

    def parar(self) -> None:
        self._seguir = False
        if self._hilo is not None:
            self._hilo.join(timeout=2.0)
            self._hilo = None


class SalaSimulada:
    """La tarjeta del robot, con la sala. Misma interfaz que ``DispositivoReal``."""

    def __init__(self, h: np.ndarray, cerca: np.ndarray | None = None,
                 ruido: float = 1e-4, semilla: int = 3, tiempo_real: bool = True):
        self.h = h
        self.cerca = cerca if cerca is not None else np.zeros(0)
        self.ruido = ruido
        self._rng = np.random.default_rng(semilla)
        self._historia = np.zeros(len(h) + MUESTRAS)
        self._i = 0
        self.sonado: list[np.ndarray] = []
        self.ecos: list[np.ndarray] = []
        self.ninos: list[np.ndarray] = []
        self.mics: list[np.ndarray] = []
        self.tiempos: list[float] = []
        self._procesar = None
        self._reloj = _Reloj20ms(self._tic, tiempo_real)
        self.abierta = False
        self.fallar_al_abrir = False

    def _tic(self) -> None:
        # Lo que suena en este bloque sale del programa; su eco llega con el retraso de h.
        eco = np.convolve(self._historia, self.h)[len(self._historia) - MUESTRAS:len(self._historia)]
        nino = self.cerca[self._i:self._i + MUESTRAS]
        if len(nino) < MUESTRAS:
            nino = np.pad(nino, (0, MUESTRAS - len(nino)))
        mic = eco + nino + self.ruido * self._rng.standard_normal(MUESTRAS)
        sonar = np.asarray(self._procesar(mic.astype(np.float32)), dtype=np.float64)
        # La historia de lo sonado, para el eco de los bloques que vienen.
        self._historia = np.concatenate([self._historia[MUESTRAS:], sonar])
        self.sonado.append(sonar)
        self.ecos.append(eco)
        self.ninos.append(nino)
        self.mics.append(mic)
        self.tiempos.append(time.perf_counter())
        self._i += MUESTRAS

    def abrir(self, procesar) -> None:
        if self.fallar_al_abrir:
            raise OSError("no hay tarjeta de sonido (simulado)")
        self._procesar = procesar
        self.abierta = True
        self._reloj.empezar()

    def vivo(self) -> bool:
        return self.abierta

    def cerrar(self) -> None:
        self._reloj.parar()
        self.abierta = False


class DispositivoSintetico:
    """La tarjeta de la PC: el micrófono dice ``voz``; se guarda lo que suena."""

    def __init__(self, voz: np.ndarray, tiempo_real: bool = True):
        self.voz = voz
        self._i = 0
        self.oido: list[np.ndarray] = []
        self.dicho: list[np.ndarray] = []
        self.tiempos: list[float] = []
        self._procesar = None
        self._reloj = _Reloj20ms(self._tic, tiempo_real)
        self.abierta = False

    def _tic(self) -> None:
        trozo = self.voz[self._i:self._i + MUESTRAS]
        if len(trozo) < MUESTRAS:
            trozo = np.pad(trozo, (0, MUESTRAS - len(trozo)))
        self._i += MUESTRAS
        self.dicho.append(trozo)
        self.tiempos.append(time.perf_counter())
        self.oido.append(np.asarray(self._procesar(trozo.astype(np.float32)), dtype=np.float64))

    def abrir(self, procesar) -> None:
        self._procesar = procesar
        self.abierta = True
        self._reloj.empezar()

    def vivo(self) -> bool:
        return self.abierta

    def cerrar(self) -> None:
        self._reloj.parar()
        self.abierta = False
