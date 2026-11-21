"""Internet de mentira entre dos programas que hablan por UDP.

``RedSimulada`` se pone en medio: uno le manda paquetes creyendo que es el
otro, y ella los reenvía con el retraso, la irregularidad y las pérdidas que
se le pidan; las respuestas vuelven por el mismo camino. Así se prueba en una
sola máquina lo que pasaría con el robot y el narrador en países distintos.

- ``retraso_ms``: lo que tarda cada paquete en cruzar (la mitad de un «ping»).
- ``variacion_ms``: cuánto se aparta cada paquete de ese retraso (el «jitter»).
  Se sortea de una exponencial: casi todos llegan cerca de lo normal y alguno,
  bastante más tarde, como pasa por internet. Con variación, unos adelantan a
  otros: llegan desordenados.
- ``perdida``: la fracción de paquetes que no llega nunca.

Cifras de referencia, para elegir (orden de magnitud, no medidas aquí): dentro
de un país, 10-40 ms de retraso; entre países de un mismo continente, 30-80; de
un continente a otro, 70-150; la variación, de unos pocos ms en una buena red a
30-50 ms en un wifi cargado o una red móvil.
"""

from __future__ import annotations

import heapq
import random
import socket
import threading
import time


class RedSimulada:
    """Un relé UDP con retraso, variación y pérdidas, en ``127.0.0.1``.

    El que habla primero manda a ``puerto``; la red reenvía a ``destino`` y
    responde a quien le habló. Para el otro lado, el relé ES el primero.
    """

    def __init__(self, destino: tuple[str, int], *, retraso_ms: float = 80.0,
                 variacion_ms: float = 20.0, perdida: float = 0.01, semilla: int = 1) -> None:
        self.destino = destino
        self.retraso_s, self.variacion_s = retraso_ms / 1000, variacion_ms / 1000
        self.perdida = perdida
        self._azar = random.Random(semilla)
        self._entrada = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)   # el primero habla aquí
        self._entrada.bind(("127.0.0.1", 0))
        self._salida = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)    # hacia el destino
        self._salida.bind(("127.0.0.1", 0))
        for s in (self._entrada, self._salida):
            s.settimeout(0.2)
        self._origen: tuple[str, int] | None = None
        self._cola: list[tuple[float, int, socket.socket, bytes, tuple[str, int]]] = []
        self._n = 0
        self._cond = threading.Condition()
        self._seguir = True
        self.pasados = self.perdidos = 0
        self._hilos = [threading.Thread(target=self._oir, args=(self._entrada, True), daemon=True),
                       threading.Thread(target=self._oir, args=(self._salida, False), daemon=True),
                       threading.Thread(target=self._mandar, daemon=True)]

    @property
    def puerto(self) -> int:
        return self._entrada.getsockname()[1]

    def empezar(self) -> "RedSimulada":
        for h in self._hilos:
            h.start()
        return self

    def _oir(self, sock: socket.socket, de_ida: bool) -> None:
        while self._seguir:
            try:
                datos, origen = sock.recvfrom(4096)
            except (socket.timeout, OSError):
                continue
            if de_ida:
                self._origen = origen
                por, hacia = self._salida, self.destino
            else:
                if self._origen is None:
                    continue
                por, hacia = self._entrada, self._origen
            if self._azar.random() < self.perdida:
                self.perdidos += 1
                continue
            extra = self._azar.expovariate(1 / self.variacion_s) if self.variacion_s > 0 else 0.0
            with self._cond:
                self._n += 1
                heapq.heappush(self._cola, (time.perf_counter() + self.retraso_s + extra,
                                            self._n, por, datos, hacia))
                self._cond.notify()

    def _mandar(self) -> None:
        while self._seguir:
            with self._cond:
                while self._seguir and (not self._cola or self._cola[0][0] > time.perf_counter()):
                    espera = (self._cola[0][0] - time.perf_counter()) if self._cola else 0.2
                    self._cond.wait(max(0.0005, min(espera, 0.2)))
                if not self._seguir:
                    return
                _, _, por, datos, hacia = heapq.heappop(self._cola)
            try:
                por.sendto(datos, hacia)
                self.pasados += 1
            except OSError:
                pass

    def cerrar(self) -> None:
        self._seguir = False
        with self._cond:
            self._cond.notify_all()
        for h in self._hilos:
            h.join(timeout=1.0)
        for s in (self._entrada, self._salida):
            s.close()
