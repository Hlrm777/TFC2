"""La voz del robot: corre EN LA RASPBERRY PI (y su otra mitad, en la PC).

QUÉ HACE
--------
Lleva la voz del narrador al altavoz del robot y, a la vez, lo que oye el
micrófono del robot (los niños) a la PC del narrador. Los dos sentidos a la vez,
sin turnos: si un niño habla mientras el narrador lee, su voz llega igual.

    PC: micrófono del narrador ──red──► Raspberry ──► altavoz (la boca)
    PC: auriculares del narrador ◄──red── Raspberry ◄── micrófono (el pecho)

El micrófono y el altavoz van en UNA sola tarjeta de sonido USB (la propuesta es
la Waveshare «USB TO AUDIO»): una sola tarjeta tiene un solo reloj, y eso es lo
que necesita el cancelador de eco (abajo). La PC usa este mismo archivo
(``telmeow_teleop/audio_operador.py``): el núcleo es igual en los dos lados.

EL ECO, Y CÓMO SE QUITA
-----------------------
El micrófono del pecho oye también la voz del narrador saliendo por la boca. Sin
hacer nada, el narrador se oiría a sí mismo con retraso. ``Cancelador`` lo
quita restando NÚMEROS, no voltajes: sabe exactamente qué mandó al altavoz,
aprende sobre la marcha con qué retraso y con qué forma vuelve por el
micrófono (un filtro adaptativo, el mismo principio que usan las
videollamadas) y resta esa copia de lo grabado. Lo que queda es lo nuevo: los
niños. Mientras los niños hablan deja de aprender, para no confundir su voz con
el eco.

Es un cancelador sencillo escrito aquí, para poder probarlo sin hardware con un
eco fabricado. Si en el robot real no bastara, Raspberry Pi OS trae uno más
completo (el de WebRTC, como módulo de PipeWire): se arranca este programa con
``--sin-cancelar`` y se le da el dispositivo «echo-cancel» del sistema. Lo que
ningún cancelador lineal quita del todo es la distorsión de un altavoz pequeño
a mucho volumen: se baja el volumen o se aparta el altavoz del micrófono.

EL CAMINO POR LA RED
--------------------
Paquetes UDP de 20 ms de sonido (16 000 muestras por segundo, 16 bits, una
voz): 256 kbit/s en cada sentido, nada para una red local. Cada paquete lleva
su número de orden; ``Colchon`` los guarda unos 60 ms antes de reproducirlos
para que lleguen ordenados, rellena con silencio los que se pierden y tira los
que llegan tarde: el sonido nunca se atasca ni acumula retraso. El colchón se
ajusta solo: por internet, donde los paquetes llegan más irregulares, crece
hasta donde haga falta, y cuando la red se calma vuelve a encoger.

POR INTERNET (el robot y el narrador en sitios distintos)
---------------------------------------------------------
Los routers de las casas no dejan entrar conexiones desde fuera, y el sonido va
sin cifrar. Las dos cosas se resuelven con una VPN (Tailscale u otra): une la PC
y la Raspberry en una red privada y cifrada, como si estuvieran en el mismo
wifi, y a este programa se le da la dirección de la PC en esa red. Lo explica
el README («Narrar desde otro sitio, por internet»).

El robot manda a la PC (``--pc``, la misma máquina del broker) y la PC responde
a la dirección de la que le llega el sonido del robot: no hay que decirle a la
PC la IP del robot. Para que nadie más de la red pueda llevarse la voz del
narrador, la PC solo acepta un robot si se le da su IP.

PRIVACIDAD
----------
Ni aquí ni en la PC se graba nada: el sonido solo se retransmite, por la red
local y sin cifrar. Como con la cámara, la red de la sesión tiene que ser de
confianza, y hay que declararlo.

USO (en la Raspberry)
---------------------
    python robot_audio.py --pc 192.168.1.50
    python robot_audio.py --listar          # qué tarjetas de sonido ve

Si la tarjeta no está o se desenchufa, reintenta cada 2 s; no se cierra.
**Escrito sin la tarjeta delante: la parte del sonido real no está probada.**
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import signal
import socket
import struct
import sys
import threading
import time
from typing import Callable

import numpy as np

__all__ = ["TASA", "MUESTRAS", "PUERTO", "codificar", "decodificar", "bloques_de_ms", "Colchon",
           "Cancelador", "EnlaceAudio", "Nucleo", "DispositivoReal", "Supervisor", "main"]

TASA = 16_000            # muestras por segundo: de sobra para la voz
MUESTRAS = 320           # por paquete: 20 ms
PUERTO = 8003            # donde escucha la PC
REINTENTO_S = 2.0

MAGIA = b"TMAU"
VERSION = 1
#: magia, versión, 3 bytes de relleno, número de orden, hora de envío en ms.
CABECERA = struct.Struct("!4sB3xII")

registro = logging.getLogger("audio")


# --------------------------------------------------------------------------
# Los paquetes
# --------------------------------------------------------------------------


def codificar(orden: int, t_ms: int, muestras: np.ndarray) -> bytes:
    """Un paquete: la cabecera y 320 muestras en 16 bits con signo."""
    datos = np.clip(np.asarray(muestras, dtype=np.float32), -1.0, 1.0)
    enteros = np.round(datos * 32767.0).astype("<i2")
    return CABECERA.pack(MAGIA, VERSION, orden & 0xFFFFFFFF, t_ms & 0xFFFFFFFF) + enteros.tobytes()


def decodificar(paquete: bytes) -> tuple[int, int, np.ndarray] | None:
    """``(orden, t_ms, muestras)``, o ``None`` si no es un paquete nuestro."""
    if len(paquete) != CABECERA.size + 2 * MUESTRAS:
        return None
    magia, version, orden, t_ms = CABECERA.unpack_from(paquete)
    if magia != MAGIA or version != VERSION:
        return None
    muestras = np.frombuffer(paquete, dtype="<i2", offset=CABECERA.size).astype(np.float32)
    return orden, t_ms, muestras / 32767.0


def _ms() -> int:
    return int(time.monotonic() * 1000) & 0xFFFFFFFF


# --------------------------------------------------------------------------
# El colchón: ordena lo que llega y nunca se atasca
# --------------------------------------------------------------------------


def _energia(x: np.ndarray) -> float:
    return float(np.dot(x, x)) / len(x)


def bloques_de_ms(ms: float) -> int:
    """Cuántos paquetes de 20 ms caben en ``ms`` (al menos uno)."""
    return max(1, math.ceil(ms * TASA / 1000 / MUESTRAS))


class Colchon:
    """Guarda ``objetivo`` paquetes antes de empezar a sonar.

    ``sacar()`` da SIEMPRE 20 ms: el paquete que toca, o silencio si se perdió
    o aún no hay nada. Si se acumulan demasiados (la red se atascó y luego
    soltó todo de golpe), tira los más viejos: mejor un salto que ir cada vez
    más retrasado. Un número de orden muy por debajo del esperado es que el
    otro lado se reinició: se empieza de cero.

    **Se ajusta solo** (``adaptable``), para que sirva igual en un wifi que
    por internet entre países. Mide cuánto se retrasa cada paquete respecto al
    más rápido de los últimos ~10 s (la irregularidad de la red) y guarda lo
    justo para que llegue a tiempo el ``cuantil`` de ellos (97 %): el resto,
    los que se retrasan muchísimo, se dan por perdidos, porque esperarlos
    añadiría retraso a todo lo demás. Si el colchón llega a vaciarse, crece
    20 ms en el acto. Encoge de 20 en 20 ms, sin bajar del ``objetivo`` del
    principio, y para que encoger quite retraso de verdad se salta un paquete
    sobrante, solo si es silencio (entre frase y frase), para que no se oiga.

    Un paquete perdido suena como el anterior, a media voz, en vez de un corte
    seco; si se pierden dos seguidos, silencio.
    """

    def __init__(self, objetivo: int = 3, maximo: int = 10, *, adaptable: bool = True,
                 objetivo_max: int = 20, calma_s: float = 2.0, cuantil: float = 97.0,
                 reloj: Callable[[], int] = _ms) -> None:
        self.objetivo_min = self.objetivo = objetivo
        self.objetivo_max = max(objetivo, objetivo_max) if adaptable else objetivo
        self.maximo, self._reloj = maximo, reloj
        self._calma = max(1, int(calma_s * TASA / MUESTRAS))
        self._lock = threading.Lock()
        self._paquetes: dict[int, tuple[int, np.ndarray]] = {}
        self._siguiente: int | None = None
        self._sonando = False
        self._desde_problema = 0
        self._desde_crecer = 10**9
        self._desde_salto = 0
        self.adaptable = adaptable
        self.cuantil = cuantil
        self._transitos: list[int] = []
        self._desde_calculo = 0
        self._anterior: np.ndarray | None = None
        self._perdidos_seguidos = 0
        self.recibidos = self.perdidos = self.tarde = self.tirados = self.huecos = 0
        self.crecidas = self.encogidas = self.saltados = 0
        self.latencias_ms: list[int] = []     # solo tiene sentido con el mismo reloj

    @property
    def retraso_ms(self) -> int:
        """Lo que guarda antes de sonar, en ms."""
        return self.objetivo * MUESTRAS * 1000 // TASA

    def _limite(self) -> int:
        return max(self.maximo, self.objetivo + 7)

    def _problema(self) -> None:
        """Se vació: crecer 20 ms (como mucho, una vez cada 100 ms)."""
        self._desde_problema = 0
        if self.objetivo < self.objetivo_max and self._desde_crecer >= 5:
            self.objetivo += 1
            self.crecidas += 1
            self._desde_crecer = 0

    def deseado(self) -> int | None:
        """Los paquetes que harían falta según la irregularidad medida."""
        if len(self._transitos) < 50:
            return None
        t = np.asarray(self._transitos, dtype=np.int64)
        irregular = float(np.percentile(t - t.min(), self.cuantil))
        return min(self.objetivo_max,
                   max(self.objetivo_min, math.ceil(irregular * TASA / 1000 / MUESTRAS) + 1))

    def _ajustar(self) -> None:
        self._desde_calculo += 1
        if not self.adaptable or self._desde_calculo < 10:
            return
        self._desde_calculo = 0
        deseado = self.deseado()
        if deseado is None:
            return
        if deseado > self.objetivo:
            self.crecidas += deseado - self.objetivo
            self.objetivo = deseado
            self._desde_problema = 0
        elif deseado < self.objetivo and self._desde_problema >= self._calma:
            self.objetivo -= 1
            self.encogidas += 1
            self._desde_problema = 0

    def meter(self, orden: int, t_ms: int, muestras: np.ndarray) -> None:
        with self._lock:
            self.recibidos += 1
            referencia = self._siguiente if self._sonando else min(self._paquetes, default=None)
            if referencia is not None and referencia - orden > 500:   # el otro lado se reinició
                self._paquetes.clear()
                self._siguiente, self._sonando = None, False
            if self.adaptable:
                # Llegada (reloj de aquí) menos salida (reloj del otro): el desfase
                # entre relojes es fijo, así que lo que varía es lo que tardó.
                self._transitos.append((self._reloj() - t_ms) & 0xFFFFFFFF)
                del self._transitos[:-500]
            if self._sonando and orden < self._siguiente:
                self.tarde += 1
                return
            self._paquetes[orden] = (t_ms, muestras)
            if self._siguiente is None:
                self._siguiente = orden
            while len(self._paquetes) > self._limite():
                viejo = min(self._paquetes)
                del self._paquetes[viejo]
                self.tirados += 1
                self._siguiente = min(self._paquetes)

    def sacar(self) -> np.ndarray:
        with self._lock:
            self._desde_crecer += 1
            self._desde_salto += 1
            if not self._sonando:
                if len(self._paquetes) < self.objetivo:
                    return np.zeros(MUESTRAS, dtype=np.float32)
                self._sonando = True
                self._siguiente = min(self._paquetes)
            if not self._paquetes:
                # Se vació: volver a llenar antes de sonar otra vez.
                self.huecos += 1
                self._problema()
                self._sonando = False
                self._siguiente = None
                return np.zeros(MUESTRAS, dtype=np.float32)
            self._desde_problema += 1
            self._ajustar()
            dato = self._paquetes.pop(self._siguiente, None)
            self._siguiente += 1
            # Sobran paquetes (el colchón encogió o la red soltó varios juntos):
            # saltarse este si es silencio, y así quitar 20 ms de retraso.
            while (dato is not None and len(self._paquetes) > self.objetivo
                   and self._desde_salto >= 5 and _energia(dato[1]) < 1e-5
                   and self._siguiente in self._paquetes):
                self.saltados += 1
                self._desde_salto = 0
                dato = self._paquetes.pop(self._siguiente)
                self._siguiente += 1
            if dato is None:
                self.perdidos += 1
                self._perdidos_seguidos += 1
                if self._perdidos_seguidos == 1 and self._anterior is not None:
                    return (self._anterior * 0.5).astype(np.float32)
                return np.zeros(MUESTRAS, dtype=np.float32)
            self._perdidos_seguidos = 0
            t_ms, muestras = dato
            self._anterior = muestras
            latencia = (self._reloj() - t_ms) & 0xFFFFFFFF
            if latencia < 60_000:
                self.latencias_ms.append(latencia)
                del self.latencias_ms[:-500]
            return muestras


# --------------------------------------------------------------------------
# El cancelador de eco
# --------------------------------------------------------------------------


class Cancelador:
    """Quita del micrófono el eco de lo que sonó por el altavoz.

    Un filtro adaptativo en el dominio de la frecuencia, por bloques
    («PBFDAF»): el filtro modela el camino altavoz → aire → micrófono,
    incluido el retraso de la tarjeta, hasta ``cola_s`` segundos. En cada
    bloque predice el eco a partir de lo último que sonó, lo resta, y corrige
    el filtro con el error que queda (NLMS, el paso normalizado por la
    potencia de lo que sonó).

    Deja de aprender (``doble``) cuando, ya convergido, lo que queda tras
    restar es demasiado grande para ser solo eco: es alguien hablando cerca
    del robot. Si eso dura más de ``reaprender_s`` con el altavoz sonando, el
    camino del eco cambió (alguien movió el robot) y vuelve a aprender. Si el
    resultado saliera más fuerte que lo grabado, se da lo grabado tal cual y
    el filtro se borra: nunca empeora la señal.
    """

    def __init__(self, bloque: int = MUESTRAS, cola_s: float = 0.4, tasa: int = TASA,
                 mu: float = 0.5, reaprender_s: float = 4.0) -> None:
        self.B = bloque
        self.P = max(1, math.ceil(cola_s * tasa / bloque))
        n = bloque + 1
        self.X = np.zeros((self.P, n), dtype=np.complex128)
        self.W = np.zeros((self.P, n), dtype=np.complex128)
        self._x_prev = np.zeros(bloque)
        self._sd = self._se = self._sy = 0.0
        self.umbral_doble = 4.0
        self.mu = mu
        self._delta = 2 * bloque * 1e-8            # unos −80 dBFS: el suelo
        self._lejos = np.zeros(self.P)             # energía de lo que sonó, por bloque
        self.erle = 1.0                            # cuánto eco quita, suavizado (lineal)
        self.convergido = False
        self.doble = False
        self._mantener = 0
        self._doble_seguido = 0
        self._reaprender = max(1, int(reaprender_s * tasa / bloque))
        self.bloques = self.adaptados = self.reinicios = 0

    @property
    def erle_db(self) -> float:
        return 10.0 * math.log10(max(self.erle, 1e-12))

    def borrar(self) -> None:
        self.W[:] = 0
        self.erle, self.convergido, self.doble = 1.0, False, False
        self.reinicios += 1

    def procesar(self, d: np.ndarray, x: np.ndarray) -> np.ndarray:
        """``d``: lo que grabó el micrófono; ``x``: lo que se mandó al altavoz
        en el mismo instante. Los dos en [−1, 1]. Devuelve ``d`` sin el eco."""
        B = self.B
        d = np.asarray(d, dtype=np.float64)
        x = np.asarray(x, dtype=np.float64)
        self.bloques += 1
        espectro = np.fft.rfft(np.concatenate([self._x_prev, x]))
        self._x_prev = x
        self.X = np.roll(self.X, 1, axis=0)
        self.X[0] = espectro
        self._lejos = np.roll(self._lejos, 1)
        self._lejos[0] = _energia(x)
        y = np.fft.irfft((self.W * self.X).sum(axis=0), n=2 * B)[B:]
        e = d - y

        pd, pe, py = _energia(d), _energia(e), _energia(y)
        # Energías suavizadas (~0.5 s), para decidir sin dejarse llevar por un bloque.
        self._sd = 0.95 * self._sd + 0.05 * pd
        self._se = 0.95 * self._se + 0.05 * pe
        self._sy = 0.8 * self._sy + 0.2 * py
        # ¿Sonó algo dentro de la cola? Su eco puede estar llegando ahora, aunque
        # el altavoz ya calle (entre sílaba y sílaba): ahí también se aprende.
        lejos = self._lejos.max() > 1e-7
        if self._mantener:
            self._mantener -= 1
        if not lejos:
            self.doble = self._mantener > 0
            return (e if pe <= pd else d).astype(np.float32)

        if self.bloques > 25 and self._se > 2.0 * self._sd and self._sd > 1e-9:
            # Medio segundo peor que no hacer nada: el filtro se desvió.
            self.borrar()
            self._se = self._sd
            return d.astype(np.float32)

        # Lo que queda tras restar, comparado con lo que debería quedar si solo
        # hubiera eco (el eco estimado partido por lo que el filtro ya quita):
        # 6 dB por encima es que alguien habla cerca del robot.
        if self.convergido and pe > self.umbral_doble * max(py, self._sy) / self.erle + 1e-9:
            self._mantener = 8
        self.doble = self._mantener > 0
        if self.doble:
            self._doble_seguido += 1
            if self._doble_seguido > self._reaprender:
                # Demasiado rato «hablando»: más bien cambió el camino del eco.
                self.convergido = False
                self._mantener = 0
                self._doble_seguido = 0
                self.reinicios += 1
        else:
            self._doble_seguido = 0
            if pe > 0:
                self.erle = 0.9 * self.erle + 0.1 * min(pd / max(pe, 1e-12), 1e4)
            self.convergido = self.erle > 4.0       # más de 6 dB

        if not self.doble:
            E = np.fft.rfft(np.concatenate([np.zeros(B), e]))
            # El paso, normalizado por la potencia de toda la cola en cada frecuencia.
            potencia = (np.abs(self.X) ** 2).sum(axis=0)
            G = self.mu * np.conj(self.X) * E / (potencia + self._delta)
            g = np.fft.irfft(G, n=2 * B, axis=1)
            g[:, B:] = 0.0                         # el filtro solo mira hacia atrás
            self.W += np.fft.rfft(g, axis=1)
            self.adaptados += 1
        # Un bloque suelto peor que lo grabado no se manda.
        return (e if pe <= pd else d).astype(np.float32)


# --------------------------------------------------------------------------
# La red
# --------------------------------------------------------------------------


class EnlaceAudio:
    """El socket UDP: manda lo del micrófono y mete en ``colchon`` lo que llega.

    ``destino`` fijo (el robot manda a la PC) o aprendido de quien manda (la
    PC responde al robot). Con ``solo_de``, solo se acepta esa IP.
    """

    def __init__(self, colchon: Colchon, *, puerto_local: int = 0,
                 destino: tuple[str, int] | None = None, solo_de: str | None = None,
                 host_local: str = "0.0.0.0", reloj: Callable[[], float] = time.monotonic) -> None:
        self.colchon = colchon
        self.destino = destino
        self.aprender = destino is None
        self.solo_de = solo_de
        self._reloj = reloj
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind((host_local, puerto_local))
        self.socket.settimeout(0.5)
        self._orden = 0
        self.ultimo = None              # cuándo llegó el último paquete bueno
        self.enviados = self.rechazados = 0
        self._seguir = True
        self._hilo = threading.Thread(target=self._recibir, name="audio-red", daemon=True)

    @property
    def puerto(self) -> int:
        return self.socket.getsockname()[1]

    def empezar(self) -> "EnlaceAudio":
        self._hilo.start()
        return self

    def edad_s(self) -> float | None:
        return None if self.ultimo is None else self._reloj() - self.ultimo

    def enviar(self, muestras: np.ndarray) -> None:
        destino = self.destino
        if destino is None:
            return
        try:
            self.socket.sendto(codificar(self._orden, _ms(), muestras), destino)
            self._orden += 1
            self.enviados += 1
        except OSError as exc:              # red caída: se sigue intentando
            registro.debug("no salio un paquete: %s", exc)

    def _recibir(self) -> None:
        while self._seguir:
            try:
                paquete, origen = self.socket.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                if not self._seguir:
                    return
                time.sleep(0.1)
                continue
            if self.solo_de is not None and origen[0] != self.solo_de:
                self.rechazados += 1
                continue
            leido = decodificar(paquete)
            if leido is None:
                self.rechazados += 1
                continue
            if self.aprender and self.destino != origen:
                registro.info("el otro lado esta en %s:%d", *origen)
                self.destino = origen
            self.ultimo = self._reloj()
            self.colchon.meter(*leido)

    def cerrar(self) -> None:
        self._seguir = False
        try:
            self.socket.close()
        except OSError:
            pass
        if self._hilo.is_alive():
            self._hilo.join(timeout=1.0)


# --------------------------------------------------------------------------
# El núcleo: lo que pasa cada 20 ms
# --------------------------------------------------------------------------


class Nucleo:
    """Cada 20 ms la tarjeta entrega lo grabado y pide qué sonar: ``bloque``.

    Saca del colchón lo que toca sonar, quita del micrófono el eco de lo que
    sonó, lo manda por la red y devuelve lo que hay que sonar. Igual en el
    robot y en la PC. ``al_procesar(mic, limpio, sonado)`` es para medir.
    """

    def __init__(self, enlace: EnlaceAudio, cancelador: Cancelador | None = None, *,
                 volumen: float = 1.0,
                 al_procesar: Callable[[np.ndarray, np.ndarray, np.ndarray], None] | None = None):
        self.enlace, self.cancelador, self.volumen = enlace, cancelador, volumen
        self.al_procesar = al_procesar
        self.bloques = 0

    def bloque(self, mic: np.ndarray) -> np.ndarray:
        sonar = np.clip(self.enlace.colchon.sacar() * self.volumen, -1.0, 1.0)
        mic = np.asarray(mic, dtype=np.float32)
        limpio = mic if self.cancelador is None else self.cancelador.procesar(mic, sonar)
        self.enlace.enviar(limpio)
        if self.al_procesar is not None:
            self.al_procesar(mic, limpio, sonar)
        self.bloques += 1
        return sonar.astype(np.float32)


# --------------------------------------------------------------------------
# La tarjeta de sonido
# --------------------------------------------------------------------------


class DispositivoReal:
    """La tarjeta de sonido por PortAudio (``sounddevice``), entrada y salida
    en un solo flujo: así lo grabado y lo sonado van con el mismo reloj.

    En la Raspberry: ``sudo apt install libportaudio2``. SIN PROBAR con la
    tarjeta de verdad.
    """

    def __init__(self, entrada=None, salida=None, tasa: int = TASA, bloque: int = MUESTRAS):
        self.entrada, self.salida, self.tasa, self.bloque = entrada, salida, tasa, bloque
        self._flujo = None
        self.fallos = 0

    def abrir(self, procesar: Callable[[np.ndarray], np.ndarray]) -> None:
        import sounddevice as sd

        def llamada(indata, outdata, frames, tiempo, estado):  # noqa: ARG001
            try:
                outdata[:, 0] = procesar(indata[:, 0])
            except Exception:  # noqa: BLE001 -- nunca tumbar el hilo de sonido
                self.fallos += 1
                outdata.fill(0)

        self._flujo = sd.Stream(samplerate=self.tasa, blocksize=self.bloque, dtype="float32",
                                channels=1, device=(self.entrada, self.salida),
                                latency="low", callback=llamada)
        self._flujo.start()

    def vivo(self) -> bool:
        return self._flujo is not None and bool(self._flujo.active)

    def cerrar(self) -> None:
        if self._flujo is not None:
            try:
                self._flujo.close()
            except Exception:  # noqa: BLE001
                pass
            self._flujo = None


class Supervisor:
    """Abre la tarjeta y, si no está o se cae, reintenta cada 2 s."""

    def __init__(self, nucleo: Nucleo, dispositivo, reintento_s: float = REINTENTO_S):
        self.nucleo, self.dispositivo, self.reintento_s = nucleo, dispositivo, reintento_s
        self.seguir = True
        self.abierto = False
        self.error = ""
        self._hilo: threading.Thread | None = None

    def paso(self) -> None:
        if self.dispositivo.vivo():
            self.abierto = True
            return
        if self.abierto:
            registro.warning("se perdio la tarjeta de sonido; reintento")
        self.abierto = False
        self.dispositivo.cerrar()
        try:
            self.dispositivo.abrir(self.nucleo.bloque)
            self.abierto = True
            self.error = ""
            registro.info("tarjeta de sonido abierta")
        except Exception as exc:  # noqa: BLE001 -- reintentar, nunca cerrar
            if str(exc) != self.error:
                registro.error("no puedo abrir la tarjeta de sonido (%s); reintento cada %.0f s",
                               exc, self.reintento_s)
            self.error = str(exc)

    def bucle(self) -> None:
        while self.seguir:
            self.paso()
            espera = 0.5 if self.abierto else self.reintento_s
            fin = time.monotonic() + espera
            while self.seguir and time.monotonic() < fin:
                time.sleep(0.05)

    def empezar(self) -> "Supervisor":
        self._hilo = threading.Thread(target=self.bucle, name="audio-tarjeta", daemon=True)
        self._hilo.start()
        return self

    def cerrar(self) -> None:
        self.seguir = False
        if self._hilo is not None:
            self._hilo.join(timeout=2.0)
        self.dispositivo.cerrar()


def _partir(texto: str, puerto: int) -> tuple[str, int]:
    host, sep, numero = texto.rpartition(":")
    if sep and numero.isdigit() and host:
        return host, int(numero)
    return texto, puerto


def _dispositivo(texto: str | None):
    if texto is None or texto == "":
        return None
    return int(texto) if texto.isdigit() else texto


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="La voz de Telmeow, para la Raspberry Pi.")
    p.add_argument("--pc", default=os.environ.get("TELMEOW_MQTT_HOST", "localhost"),
                   help=f"La PC del narrador (la del broker), con :PUERTO si no es {PUERTO}.")
    p.add_argument("--dispositivo", default=None,
                   help="La tarjeta de sonido, por numero o parte del nombre (--listar).")
    p.add_argument("--volumen", type=float, default=1.0, help="Multiplica lo que suena.")
    p.add_argument("--colchon-ms", type=float, default=60.0,
                   help="Lo que se guarda antes de sonar al empezar (y lo minimo). Crece solo "
                        "si la red llega irregular; por internet se puede empezar en 120.")
    p.add_argument("--colchon-max-ms", type=float, default=400.0,
                   help="Hasta donde puede crecer el colchon.")
    p.add_argument("--sin-cancelar", action="store_true",
                   help="Sin el cancelador de eco de aqui (p. ej. si se usa el de PipeWire).")
    p.add_argument("--listar", action="store_true", help="Dice que tarjetas de sonido hay y sale.")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(message)s")
    if args.listar:
        import sounddevice as sd

        print(sd.query_devices())
        return 0

    colchon = Colchon(bloques_de_ms(args.colchon_ms),
                      objetivo_max=bloques_de_ms(max(args.colchon_ms, args.colchon_max_ms)))
    enlace = EnlaceAudio(colchon, destino=_partir(args.pc, PUERTO)).empezar()
    nucleo = Nucleo(enlace, None if args.sin_cancelar else Cancelador(), volumen=args.volumen)
    tarjeta = _dispositivo(args.dispositivo)
    supervisor = Supervisor(nucleo, DispositivoReal(tarjeta, tarjeta))
    registro.info("mandando el sonido a %s:%d", *enlace.destino)

    def parar(*_):
        supervisor.seguir = False

    signal.signal(signal.SIGTERM, parar)
    try:
        supervisor.bucle()
    except KeyboardInterrupt:
        pass
    finally:
        supervisor.cerrar()
        enlace.cerrar()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
