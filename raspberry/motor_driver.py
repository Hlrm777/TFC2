"""Los motores del robot: corre EN LA RASPBERRY PI.

QUÉ HACE
--------
Recibe la pose que publica la PC del narrador, 20 veces por segundo::

    telmeow/teleop/pose  {"pan": -12.4, "tilt": 8.1, "armL": -20.0, "armLtw": 15.0,
                          "armR": 30.5, "armRtw": 50.2, ...}

y la convierte en la posición de cada uno de los seis motores DYNAMIXEL
XL430-W250-T. Todo el cálculo pesado ya viene hecho de la PC: aquí solo se pasa
de grados a pulsos de motor y se vigila que los motores estén bien.

CÓMO LLEGA A LOS MOTORES
------------------------
    Raspberry Pi --USB-C--> OpenRB-150 --cable DYNAMIXEL (TTL)--> los 6 motores

La OpenRB-150 lleva cargado el programa de ejemplo ``usb_to_dynamixel`` de
ROBOTIS, que la convierte en un adaptador: lo que entra por el USB sale tal
cual al cable de los motores. La Raspberry habla con ellos con la biblioteca
oficial ``dynamixel_sdk`` (Protocol 2.0). No hay programa propio dentro de la
OpenRB. La puesta en marcha está en ``raspberry/OPENRB.md``.

DE GRADOS A PULSOS
------------------
Un XL430 divide la vuelta en 4096 pulsos (0.088 grados cada uno); el 2048 es el
centro. Para cada articulación::

    pulsos = cero + signo * grados * 4096 / 360

``cero`` (qué pulso es el 0 de la articulación) y ``signo`` (hacia dónde gira el
motor tal como quedó atornillado) **dependen del montaje** y solo se pueden
fijar con el robot delante. Los de ``ARTICULACIONES`` son de partida; se cambian
con ``--config archivo.json`` sin tocar el código.

SEGURIDAD
---------
- Cada ángulo se recorta a su recorrido (``min``/``max``) antes de convertirlo.
  Es una última red: quien evita los choques entre piezas es la PC
  (``telmeow_teleop/limits.py``), que conoce cómo depende un límite de otro.
- La velocidad de los motores va limitada (perfil), y **el primer movimiento
  es lento**: al arrancar, o al volver tras más de un segundo sin poses, el
  robot puede estar lejos de la pose pedida y no debe ir de golpe.
- Sin poses (el broker se cayó, la PC está en pausa) el robot **se queda
  quieto donde está**, con el par puesto.
- Un motor averiado (sobrecarga, calor) se apaga solo; aquí se anota y no se
  reinicia: puede estar atascado contra algo. Los demás siguen.
- Si un motor o el cable USB desaparecen, se reintenta cada 2 s. El programa no
  se cierra solo.

POR INTERNET
------------
Por una red lejana las poses llegan a tirones. Con la hora de envío que pone la
PC en cada mensaje (``ts``), ``Pose`` las reproduce con su ritmo original unos
ms más tarde e interpola entre ellas: el robot se mueve suave aunque la red no
lo sea. El estado que publica dice cuánto guarda (``colchon_ms``).

USO
---
    python motor_driver.py --broker 192.168.1.50
    python motor_driver.py --escanear               # qué motores contestan
    python motor_driver.py --cambiar-id 1 4         # con UN solo motor conectado
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

__all__ = ["TOPIC_POSE", "TOPIC_ESTADO", "Articulacion", "ARTICULACIONES", "Ajustes",
           "a_pulsos", "a_grados", "cargar_config", "Pose", "Motores", "EnlaceMotores",
           "Controlador", "abrir_bus", "main"]

#: El de ``telmeow_teleop.config.MQTTConfig.topic_pose`` (una prueba lo comprueba).
TOPIC_POSE = "telmeow/teleop/pose"
#: Aquí cuenta el programa, una vez por segundo, cómo están los motores.
TOPIC_ESTADO = "telmeow/robot/motores"

PULSOS_POR_VUELTA = 4096
PULSO_MAX = 4095

# Tabla de control del XL430-W250 (manual de ROBOTIS): dirección de cada dato.
D_ID, D_MODO = 7, 11
D_PAR, D_ERROR_HW = 64, 70
D_ACELERACION, D_VELOCIDAD, D_META = 108, 112, 116
D_POSICION, D_TEMPERATURA = 132, 146
MODO_POSICION = 3
#: Grados por segundo de cada unidad de velocidad del perfil (0.229 rev/min).
GRADOS_S_POR_UNIDAD = 0.229 * 360.0 / 60.0

registro = logging.getLogger("motores")


@dataclass(frozen=True)
class Articulacion:
    """Una articulación: qué campo de la pose la mueve y qué motor es."""

    campo: str          # el nombre en el mensaje de pose
    id: int             # el ID del motor en el cable
    minimo: float       # recorrido, en grados de la articulación
    maximo: float
    signo: int = 1      # +1 o -1: hacia dónde gira el motor al crecer el ángulo
    cero: int = 2048    # el pulso que corresponde a 0 grados


#: Los seis motores. Los ID siguen el cableado del diseño: la cabeza en un
#: puerto de la OpenRB y los brazos encadenados en otro. Los recorridos son los
#: de ``telmeow_teleop/limits.py`` (una prueba lo comprueba). ``signo`` y
#: ``cero`` son de partida: se fijan con el robot montado.
ARTICULACIONES: tuple[Articulacion, ...] = (
    Articulacion("tilt", 1, -39.0, 42.0),        # cuello, motor fijo al cuerpo
    Articulacion("pan", 2, -90.0, 90.0),         # cuello, motor en la cabeza
    Articulacion("armRtw", 3, -180.0, 90.0),     # hombro derecho, flexión (motor fijo)
    Articulacion("armR", 4, -75.0, 70.0),        # hombro derecho, abducción (en el brazo)
    Articulacion("armLtw", 5, -180.0, 90.0),     # hombro izquierdo, flexión
    Articulacion("armL", 6, -75.0, 75.0),        # hombro izquierdo, abducción
)


@dataclass(frozen=True)
class Ajustes:
    """Lo que se afina con el robot delante. Velocidades en unidades del motor
    (1 unidad = 1.374 grados por segundo)."""

    velocidad: int = 130          # tope en marcha normal: ~180 grados/s
    velocidad_lenta: int = 30     # el primer movimiento: ~41 grados/s
    aceleracion: int = 40
    sin_pose_s: float = 1.0       # sin poses este tiempo, el robot se queda quieto
    reintento_s: float = 2.0
    hz: float = 50.0              # vueltas por segundo sin poses nuevas (interpolando)


def a_pulsos(grados: float, articulacion: Articulacion) -> int:
    """El pulso de motor de un ángulo, recortado al recorrido de la
    articulación y al de un motor en modo posición (0 a 4095)."""
    grados = min(max(grados, articulacion.minimo), articulacion.maximo)
    pulsos = round(articulacion.cero + articulacion.signo * grados * PULSOS_POR_VUELTA / 360.0)
    return min(max(pulsos, 0), PULSO_MAX)


def a_grados(pulsos: int, articulacion: Articulacion) -> float:
    return (pulsos - articulacion.cero) * 360.0 / PULSOS_POR_VUELTA / articulacion.signo


def cargar_config(ruta: Path | None) -> tuple[tuple[Articulacion, ...], Ajustes]:
    """``ARTICULACIONES`` y ``Ajustes`` con lo que cambie el JSON de ``ruta``::

        {"articulaciones": {"pan": {"signo": -1, "cero": 2100}},
         "ajustes": {"velocidad": 100}}

    ``ValueError`` si algo no cuadra: mejor no arrancar que mover un motor mal.
    """
    articulaciones, ajustes = ARTICULACIONES, Ajustes()
    if ruta is None:
        return articulaciones, ajustes
    datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
    cambios = datos.get("articulaciones", {})
    desconocidas = set(cambios) - {a.campo for a in articulaciones}
    if desconocidas:
        raise ValueError(f"articulaciones desconocidas: {sorted(desconocidas)}")
    try:
        articulaciones = tuple(replace(a, **cambios.get(a.campo, {})) for a in articulaciones)
        ajustes = replace(ajustes, **datos.get("ajustes", {}))
    except TypeError as exc:
        raise ValueError(f"campo desconocido en {ruta}: {exc}") from exc
    for a in articulaciones:
        if a.signo not in (1, -1) or not 0 <= a.cero <= PULSO_MAX or not 0 <= a.id <= 252 \
                or a.minimo > a.maximo:
            raise ValueError(f"valores imposibles para {a.campo}: {a}")
    if len({a.id for a in articulaciones}) != len(articulaciones):
        raise ValueError("dos articulaciones con el mismo ID de motor")
    return articulaciones, ajustes


class Pose:
    """La pose que tiene que tener el robot. Como en el visor, cada campo es
    opcional y el que falta conserva su último valor. La escribe el hilo de MQTT.

    POR INTERNET, MOVIMIENTOS SUAVES. La PC publica 20 poses por segundo, pero
    por una red lejana no llegan igual de espaciadas: se retrasan unas más que
    otras y llegan a tirones (varias juntas tras un hueco). Movidas tal cual
    llegan, el robot se pararía y luego daría un salto. Si el mensaje trae la
    hora de envío (``ts``, ms de un reloj de la PC que solo avanza), la pose se
    reproduce con su ritmo original, ``retraso_s`` más tarde, y entre una pose y
    la siguiente se interpola, como el colchón de la voz:

    - ``retraso_s`` se mide solo: cuánto se retrasa cada mensaje respecto al
      más rápido de los últimos ``ventana_s`` segundos; se guarda lo justo para
      que llegue a tiempo el ``cuantil`` de ellos (97 %), con un tope de
      ``max_retraso_s``. En un wifi queda en unos pocos ms.
    - Si se acaban las poses (un hueco más largo que el colchón), el robot se
      queda en la última, sin inventarse hacia dónde iba; cuando vuelven, sigue
      desde ahí y alcanza el ritmo acelerando como mucho un 25 %: no hay saltos.
      Con más de 1 s de desfase sí se salta al presente (lo frena el perfil de
      velocidad de los motores y, tras 1 s sin poses, el arranque lento).
    - Nunca va hacia atrás.

    Sin ``ts`` (un publicador viejo, las pruebas), la última pose tal cual.
    """

    def __init__(self, campos, reloj: Callable[[], float] = time.monotonic, *,
                 cuantil: float = 97.0, max_retraso_s: float = 0.5,
                 ventana_s: float = 10.0) -> None:
        self._campos = tuple(campos)
        self._reloj = reloj
        self._candado = threading.Lock()
        self._valores: dict[str, float] = {}
        self._cuando: float | None = None
        self.cuantil, self.max_retraso_s, self.ventana_s = cuantil, max_retraso_s, ventana_s
        self._muestras: list[tuple[float, dict[str, float]]] = []   # (hora de envío, pose)
        self._transitos: list[tuple[float, float]] = []             # (llegada, llegada - envío)
        self._p: float | None = None          # por dónde va la reproducción (hora de envío)
        self._t_prev: float | None = None
        #: Lo que se guarda antes de mover, en segundos (se mide solo).
        self.retraso_s = 0.0
        #: Se enciende con cada pose nueva: el bucle no espera a su turno.
        self.nueva = threading.Event()

    def recibir(self, datos: Any) -> bool:
        if not isinstance(datos, dict):
            return False
        nuevos = {c: float(datos[c]) for c in self._campos
                  if isinstance(datos.get(c), (int, float)) and not isinstance(datos[c], bool)
                  and math.isfinite(datos[c])}
        if not nuevos:
            return False
        ts = datos.get("ts")
        marca = isinstance(ts, (int, float)) and not isinstance(ts, bool) and math.isfinite(ts)
        with self._candado:
            llegada = self._reloj()
            self._valores.update(nuevos)
            self._cuando = llegada
            if not marca:
                self._muestras.clear()
                self._transitos.clear()
            else:
                envio = float(ts) / 1000.0
                if self._muestras and envio <= self._muestras[-1][0]:
                    if envio == self._muestras[-1][0]:
                        self._muestras[-1] = (envio, dict(self._valores))
                else:
                    self._muestras.append((envio, dict(self._valores)))
                    self._transitos.append((llegada, llegada - envio))
                desde = llegada - self.ventana_s
                while self._transitos and self._transitos[0][0] < desde:
                    self._transitos.pop(0)
                while len(self._muestras) > 2 and self._muestras[1][0] < envio - self.ventana_s:
                    self._muestras.pop(0)
        self.nueva.set()
        return True

    def _interpolar(self, p: float) -> dict[str, float]:
        muestras = self._muestras
        if p <= muestras[0][0]:
            return dict(muestras[0][1])
        if p >= muestras[-1][0]:
            return dict(muestras[-1][1])
        bajo, alto = 0, len(muestras) - 1
        while alto - bajo > 1:
            medio = (bajo + alto) // 2
            if muestras[medio][0] <= p:
                bajo = medio
            else:
                alto = medio
        (s0, v0), (s1, v1) = muestras[bajo], muestras[alto]
        f = (p - s0) / (s1 - s0)
        return {c: (v0[c] + f * (v1[c] - v0[c])) if (c in v0 and c in v1) else v1.get(c, v0.get(c))
                for c in set(v0) | set(v1)}

    def leer(self) -> tuple[dict[str, float], float | None]:
        """``(valores, segundos desde la última pose)``; ``None`` si no hubo."""
        with self._candado:
            ahora = self._reloj()
            edad = None if self._cuando is None else ahora - self._cuando
            if not self._muestras or not self._transitos:
                return dict(self._valores), edad
            transitos = [t for _, t in self._transitos]
            base = min(transitos)
            orden = sorted(t - base for t in transitos)
            irregular = orden[min(len(orden) - 1, int(len(orden) * self.cuantil / 100))]
            objetivo = min(self.max_retraso_s, irregular + 0.01) if len(orden) > 1 else 0.0
            if objetivo > self.retraso_s:
                self.retraso_s = objetivo                              # crecer: en el acto
            else:
                self.retraso_s += 0.02 * (objetivo - self.retraso_s)   # encoger: despacio
            deseada = ahora - base - self.retraso_s
            ultima = self._muestras[-1][0]
            if self._p is None or abs(deseada - self._p) > 1.0:
                p = deseada
            else:
                dt = max(0.0, ahora - self._t_prev)
                p = min(max(deseada, self._p + 0.75 * dt), self._p + 1.25 * dt)
                p = max(self._p, min(p, ultima))   # ni hacia atrás ni más allá de lo que hay
            self._p, self._t_prev = p, ahora
            return self._interpolar(p), edad


def abrir_bus(puerto: str, baudios: int):
    """El puerto serie y el protocolo, con la biblioteca de ROBOTIS."""
    from dynamixel_sdk import PacketHandler, PortHandler

    serie = PortHandler(puerto)
    if not serie.openPort() or not serie.setBaudRate(baudios):
        raise OSError(f"no puedo abrir {puerto} a {baudios} baudios")
    return serie, PacketHandler(2.0)


class Motores:
    """Los seis motores sobre un bus ya abierto (``serie``, ``protocolo``)."""

    def __init__(self, serie, protocolo, articulaciones=ARTICULACIONES,
                 ajustes: Ajustes = Ajustes()) -> None:
        from dynamixel_sdk import GroupSyncWrite

        self.serie, self.protocolo = serie, protocolo
        self.articulaciones, self.ajustes = tuple(articulaciones), ajustes
        self._sinc = GroupSyncWrite(serie, protocolo, D_META, 4)
        #: Los ID que contestaron y quedaron con el par puesto.
        self.listos: set[int] = set()
        #: ID -> estado de error de hardware distinto de cero.
        self.averiados: dict[int, int] = {}
        self.temperaturas: dict[int, int] = {}
        self._velocidad_puesta: int | None = None

    # -- una orden a un motor, sin lanzar por un motor que no contesta ---------

    def _escribir(self, id_: int, direccion: int, largo: int, valor: int) -> bool:
        funcion = self.protocolo.write1ByteTxRx if largo == 1 else self.protocolo.write4ByteTxRx
        resultado, error = funcion(self.serie, id_, direccion, valor)
        if resultado != 0 or error & 0x7F:
            registro.warning("motor %d: no acepto la direccion %d = %d (%s %s)", id_, direccion,
                             valor, self.protocolo.getTxRxResult(resultado),
                             self.protocolo.getRxPacketError(error))
            return False
        return True

    def _leer(self, id_: int, direccion: int, largo: int) -> int | None:
        funcion = self.protocolo.read1ByteTxRx if largo == 1 else self.protocolo.read4ByteTxRx
        valor, resultado, _ = funcion(self.serie, id_, direccion)
        return valor if resultado == 0 else None

    # -- puesta en marcha y vigilancia ----------------------------------------

    def preparar(self) -> set[int]:
        """Pone en marcha los motores que aún no lo están: modo posición,
        perfil de velocidad y par. Devuelve los que quedaron listos."""
        for a in self.articulaciones:
            if a.id in self.listos or a.id in self.averiados:
                continue
            _, resultado, _ = self.protocolo.ping(self.serie, a.id)
            if resultado != 0:
                continue
            error = self._leer(a.id, D_ERROR_HW, 1)
            if error:
                self._averia(a.id, error)
                continue
            if (self._escribir(a.id, D_PAR, 1, 0)             # el modo es EEPROM: sin par
                    and self._escribir(a.id, D_MODO, 1, MODO_POSICION)
                    and self._escribir(a.id, D_ACELERACION, 4, self.ajustes.aceleracion)
                    and self._escribir(a.id, D_VELOCIDAD, 4, self.ajustes.velocidad_lenta)
                    and self._escribir(a.id, D_PAR, 1, 1)):
                self.listos.add(a.id)
                self._velocidad_puesta = None
                registro.info("motor %d (%s) listo", a.id, a.campo)
        return set(self.listos)

    def _averia(self, id_: int, error: int) -> None:
        if self.averiados.get(id_) != error:
            registro.error("motor %d AVERIADO (estado 0x%02x): se queda apagado hasta que "
                           "alguien lo revise y lo reinicie", id_, error)
        self.averiados[id_] = error
        self.listos.discard(id_)

    def vigilar(self) -> None:
        """Mira el estado de cada motor listo. El que no contesta deja de estar
        listo (``preparar`` lo volverá a intentar); el averiado se anota."""
        for a in self.articulaciones:
            if a.id not in self.listos:
                continue
            error = self._leer(a.id, D_ERROR_HW, 1)
            if error is None:
                registro.warning("motor %d no contesta", a.id)
                self.listos.discard(a.id)
                continue
            if error:
                self._averia(a.id, error)
                continue
            temperatura = self._leer(a.id, D_TEMPERATURA, 1)
            if temperatura is not None:
                self.temperaturas[a.id] = temperatura

    def posiciones(self) -> dict[str, float]:
        """El ángulo actual de cada articulación lista, en grados."""
        actuales = {}
        for a in self.articulaciones:
            if a.id in self.listos:
                pulsos = self._leer(a.id, D_POSICION, 4)
                if pulsos is not None:
                    actuales[a.campo] = a_grados(pulsos, a)
        return actuales

    # -- movimiento -----------------------------------------------------------

    def velocidad(self, unidades: int) -> None:
        if unidades != self._velocidad_puesta:
            for id_ in sorted(self.listos):
                self._escribir(id_, D_VELOCIDAD, 4, unidades)
            self._velocidad_puesta = unidades

    def mover(self, pose: dict[str, float]) -> dict[int, int]:
        """Manda de una vez la posición de cada motor listo que tenga ángulo
        en ``pose``. Devuelve ``{id: pulsos}`` de lo enviado."""
        enviado = {}
        self._sinc.clearParam()
        for a in self.articulaciones:
            if a.id in self.listos and a.campo in pose:
                pulsos = a_pulsos(pose[a.campo], a)
                self._sinc.addParam(a.id, list(pulsos.to_bytes(4, "little")))
                enviado[a.id] = pulsos
        if enviado:
            resultado = self._sinc.txPacket()
            if resultado != 0:
                raise OSError(f"no salio la orden: {self.protocolo.getTxRxResult(resultado)}")
        return enviado

    def soltar(self) -> None:
        for id_ in sorted(self.listos):
            self._escribir(id_, D_PAR, 1, 0)
        self.listos.clear()

    def cerrar(self) -> None:
        try:
            self.serie.closePort()
        except Exception as exc:  # noqa: BLE001
            registro.debug("al cerrar el puerto: %s", exc)


class EnlaceMotores:
    """La suscripción al topic de pose y el aviso del estado, con reconexión."""

    def __init__(self, pose: Pose, host: str, puerto: int = 1883, *, cliente: Any = None):
        self.pose, self.host, self.puerto = pose, host, puerto
        self.conectado = False
        if cliente is None:
            import paho.mqtt.client as mqtt
            cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id=f"telmeow-motores-{os.getpid()}")
        self._cliente = cliente
        cliente.on_connect = self._al_conectar
        cliente.on_disconnect = self._al_desconectar
        cliente.on_message = self._al_mensaje
        cliente.reconnect_delay_set(min_delay=1, max_delay=30)

    def conectar(self) -> None:
        self._cliente.connect_async(self.host, self.puerto, 30)
        self._cliente.loop_start()

    def cerrar(self) -> None:
        for funcion in (self._cliente.disconnect, self._cliente.loop_stop):
            try:
                funcion()
            except Exception as exc:  # noqa: BLE001
                registro.debug("al cerrar: %s", exc)

    def avisar(self, estado: dict) -> None:
        try:
            self._cliente.publish(TOPIC_ESTADO, json.dumps(estado, separators=(",", ":")),
                                  qos=0, retain=True)
        except Exception as exc:  # noqa: BLE001
            registro.debug("no salio el estado: %s", exc)

    def _al_conectar(self, cliente, userdata, flags, codigo, propiedades=None) -> None:
        if getattr(codigo, "is_failure", False):
            return
        self.conectado = True
        registro.info("conectado al broker %s:%d", self.host, self.puerto)
        cliente.subscribe(TOPIC_POSE, 0)

    def _al_desconectar(self, cliente, userdata, *resto) -> None:
        if self.conectado:
            registro.warning("se perdio el broker; el robot se queda quieto y reintento")
        self.conectado = False

    def _al_mensaje(self, cliente, userdata, mensaje) -> None:
        try:
            self.pose.recibir(json.loads(mensaje.payload.decode("utf-8")))
        except Exception as exc:  # noqa: BLE001 -- nunca tumbar el hilo de MQTT
            registro.debug("mensaje ilegible: %s", exc)


class Controlador:
    """El bucle: abre el bus, prepara los motores y les manda la pose.

    ``paso()`` es una vuelta. ``abrir`` devuelve ``(serie, protocolo)``; si
    falla (USB desenchufado), se reintenta cada ``ajustes.reintento_s``.
    """

    def __init__(self, pose: Pose, abrir: Callable[[], tuple], articulaciones=ARTICULACIONES,
                 ajustes: Ajustes = Ajustes(), *, avisar: Callable[[dict], None] | None = None,
                 reloj: Callable[[], float] = time.monotonic) -> None:
        self.pose, self._abrir = pose, abrir
        self.articulaciones, self.ajustes = tuple(articulaciones), ajustes
        self._avisar, self._reloj = avisar, reloj
        self.motores: Motores | None = None
        self.seguir = True
        self.moviendo = False           # ¿se están mandando poses ahora?
        self.fallos_de_bus = 0
        self._proximo_intento = 0.0
        self._proxima_vigilancia = 0.0
        self._lento_hasta = 0.0

    def _asegurar_bus(self, ahora: float) -> bool:
        if self.motores is not None:
            return True
        if ahora < self._proximo_intento:
            return False
        self._proximo_intento = ahora + self.ajustes.reintento_s
        try:
            self.motores = Motores(*self._abrir(), self.articulaciones, self.ajustes)
            registro.info("puerto de los motores abierto")
            return True
        except Exception as exc:  # noqa: BLE001 -- reintentar, nunca cerrar
            registro.error("no puedo abrir el puerto de los motores (%s); reintento en %.0f s",
                           exc, self.ajustes.reintento_s)
            return False

    def _perder_bus(self, exc: Exception) -> None:
        self.fallos_de_bus += 1
        registro.error("se perdio el puerto de los motores (%s); reintento", exc)
        if self.motores is not None:
            self.motores.cerrar()
        self.motores = None
        self.moviendo = False

    def paso(self) -> None:
        ahora = self._reloj()
        if not self._asegurar_bus(ahora):
            return
        motores = self.motores
        try:
            if ahora >= self._proxima_vigilancia:
                self._proxima_vigilancia = ahora + 1.0
                antes = set(motores.listos)
                motores.vigilar()
                if motores.preparar() - antes:
                    self.moviendo = False       # un motor recién puesto: entrar despacio
                self._contar(ahora)
            valores, edad = self.pose.leer()
            if edad is None or edad > self.ajustes.sin_pose_s or not motores.listos:
                if self.moviendo:
                    registro.warning("sin poses: el robot se queda quieto")
                self.moviendo = False
                return
            if not self.moviendo:
                # Arranque o vuelta tras un silencio: despacio hasta llegar.
                actuales = motores.posiciones()
                lejos = max((abs(valores[c] - actuales[c]) for c in actuales if c in valores),
                            default=0.0)
                lenta = self.ajustes.velocidad_lenta * GRADOS_S_POR_UNIDAD
                self._lento_hasta = ahora + lejos / lenta + 0.3
                motores.velocidad(self.ajustes.velocidad_lenta)
                self.moviendo = True
            elif ahora >= self._lento_hasta:
                motores.velocidad(self.ajustes.velocidad)
            motores.mover(valores)
        except Exception as exc:  # noqa: BLE001 -- el cable USB, sobre todo
            self._perder_bus(exc)

    def _contar(self, ahora: float) -> None:
        if self._avisar is None or self.motores is None:
            return
        _, edad = self.pose.leer()
        self._avisar({
            "listos": sorted(self.motores.listos),
            "faltan": sorted({a.id for a in self.articulaciones} - self.motores.listos
                             - set(self.motores.averiados)),
            "averiados": {str(i): e for i, e in sorted(self.motores.averiados.items())},
            "temperatura": {str(i): t for i, t in sorted(self.motores.temperaturas.items())},
            "moviendo": self.moviendo,
            "colchon_ms": round(self.pose.retraso_s * 1000),
            "sin_pose_s": None if edad is None else round(edad, 1),
        })

    def bucle(self) -> None:
        """Una vuelta en cuanto llega una pose (para no añadirle retraso) y, si
        no llega ninguna, una cada ``1/hz`` segundos para vigilar. Nunca más
        de 100 vueltas por segundo."""
        periodo = 1.0 / self.ajustes.hz
        while self.seguir:
            inicio = time.perf_counter()
            self.pose.nueva.clear()
            try:
                self.paso()
            except Exception:  # noqa: BLE001 -- anotar y seguir
                registro.exception("fallo en una vuelta de los motores; sigo")
            self.pose.nueva.wait(periodo)
            time.sleep(max(0.0, 0.01 - (time.perf_counter() - inicio)))

    def cerrar(self, soltar: bool = False) -> None:
        if self.motores is not None:
            if soltar:
                try:
                    self.motores.soltar()
                except Exception as exc:  # noqa: BLE001
                    registro.warning("al soltar los motores: %s", exc)
            self.motores.cerrar()


# --------------------------------------------------------------------------
# Puesta en marcha: qué motores hay y darles su ID
# --------------------------------------------------------------------------


def escanear(serie, protocolo, hasta: int = 20) -> dict[int, int]:
    """``{id: modelo}`` de los motores que contestan entre 0 y ``hasta``."""
    encontrados = {}
    for id_ in range(hasta + 1):
        modelo, resultado, _ = protocolo.ping(serie, id_)
        if resultado == 0:
            encontrados[id_] = modelo
    return encontrados


def cambiar_id(serie, protocolo, viejo: int, nuevo: int) -> None:
    """Cambia el ID de un motor. ``ValueError`` si no es seguro hacerlo: los
    motores salen de fábrica todos con el ID 1, así que se hace de uno en uno."""
    if not 0 <= nuevo <= 252:
        raise ValueError("el ID nuevo tiene que estar entre 0 y 252")
    presentes = escanear(serie, protocolo, hasta=max(20, viejo, nuevo))
    if viejo not in presentes:
        raise ValueError(f"no contesta ningun motor con el ID {viejo}")
    if nuevo in presentes:
        raise ValueError(f"ya hay un motor con el ID {nuevo}")
    for direccion, valor in ((D_PAR, 0), (D_ID, nuevo)):
        resultado, error = protocolo.write1ByteTxRx(serie, viejo, direccion, valor)
        if resultado != 0 or error & 0x7F:
            raise ValueError(f"el motor no acepto el cambio: "
                             f"{protocolo.getTxRxResult(resultado)} "
                             f"{protocolo.getRxPacketError(error)}")
    if protocolo.ping(serie, nuevo)[1] != 0:
        raise ValueError(f"tras el cambio, el ID {nuevo} no contesta")


def _partir_broker(texto: str) -> tuple[str, int]:
    host, sep, puerto = texto.rpartition(":")
    if sep and puerto.isdigit() and host:
        return host, int(puerto)
    return texto, 1883


def main(argv: list[str] | None = None, *, abrir: Callable[[str, int], tuple] = abrir_bus) -> int:
    env = os.environ
    p = argparse.ArgumentParser(description="Los motores de Telmeow, para la Raspberry Pi.")
    p.add_argument("--broker", default=env.get("TELMEOW_MQTT_HOST", "localhost"))
    p.add_argument("--puerto", default=env.get("TELMEOW_MOTORES_PUERTO", "/dev/ttyACM0"),
                   help="El puerto serie de la OpenRB (por defecto /dev/ttyACM0).")
    p.add_argument("--baudios", type=int, default=57600,
                   help="Los de fabrica de los XL430 son 57600.")
    p.add_argument("--config", type=Path, default=None,
                   help="JSON con el signo y el cero de cada articulacion y los ajustes.")
    p.add_argument("--soltar", action="store_true",
                   help="Al salir, quita el par (los brazos caen por su peso).")
    p.add_argument("--escanear", action="store_true", help="Dice que motores contestan y sale.")
    p.add_argument("--cambiar-id", nargs=2, type=int, metavar=("VIEJO", "NUEVO"),
                   help="Cambia el ID de un motor y sale. Con UN solo motor conectado.")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        articulaciones, ajustes = cargar_config(args.config)
    except (OSError, ValueError) as exc:
        print(f"La configuracion no vale: {exc}", file=sys.stderr)
        return 2

    if args.escanear or args.cambiar_id:
        try:
            serie, protocolo = abrir(args.puerto, args.baudios)
        except Exception as exc:  # noqa: BLE001
            print(f"No puedo abrir {args.puerto}: {exc}", file=sys.stderr)
            return 1
        try:
            if args.cambiar_id:
                cambiar_id(serie, protocolo, *args.cambiar_id)
                print(f"Hecho: el motor {args.cambiar_id[0]} ahora es el {args.cambiar_id[1]}.")
            encontrados = escanear(serie, protocolo)
            esperados = {a.id: a.campo for a in articulaciones}
            for id_, modelo in encontrados.items():
                print(f"  ID {id_}: modelo {modelo} -> {esperados.get(id_, 'no se usa')}")
            faltan = sorted(set(esperados) - set(encontrados))
            print(f"Contestan {len(encontrados)} motores." + (f" Faltan los ID {faltan}."
                                                              if faltan else ""))
            return 0
        except ValueError as exc:
            print(f"No se hizo: {exc}", file=sys.stderr)
            return 1
        finally:
            serie.closePort()

    pose = Pose(a.campo for a in articulaciones)
    enlace = EnlaceMotores(pose, *_partir_broker(args.broker))
    controlador = Controlador(pose, lambda: abrir(args.puerto, args.baudios), articulaciones,
                              ajustes, avisar=enlace.avisar)

    def parar(*_):
        controlador.seguir = False

    signal.signal(signal.SIGTERM, parar)
    enlace.conectar()
    try:
        controlador.bucle()
    except KeyboardInterrupt:
        pass
    finally:
        enlace.cerrar()
        controlador.cerrar(soltar=args.soltar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
