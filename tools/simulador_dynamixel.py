"""Motores DYNAMIXEL XL430 simulados, a nivel de bytes del cable.

POR QUÉ EXISTE
--------------
El robot no se fabrica: no hay motores ni placa OpenRB donde probar
``raspberry/motor_driver.py``. Este simulador hace de los motores **hablando su
mismo protocolo** (DYNAMIXEL Protocol 2.0): recibe los bytes que la biblioteca
oficial de ROBOTIS (``dynamixel_sdk``) pondría en el cable y contesta los bytes
que contestaría un motor. Así el programa de los motores se prueba con la
biblioteca de verdad, no con un doble escrito a su medida: si el programa pide
algo mal formado, o a una dirección equivocada, aquí falla igual que fallaría
en el robot.

El control de errores de cada paquete (CRC) está escrito aquí por separado, a
partir de la definición del protocolo, y no copiado de la biblioteca: que los
dos se entiendan es una comprobación cruzada.

QUÉ SIMULA
----------
- Las instrucciones que usa el programa: ping, leer, escribir, escritura
  sincronizada (la de las seis posiciones a la vez) y reinicio.
- La tabla de control del XL430-W250 (manual de ROBOTIS): modelo 1060, ID,
  modo, límites, par, velocidad y aceleración del perfil, posición pedida y
  actual, temperatura, voltaje y estado de error.
- Las reglas del motor: la zona EEPROM (direcciones < 64) no se escribe con el
  par puesto; una posición fuera de los límites se rechaza; lo de solo lectura
  no se escribe; un motor con error de hardware suelta el par y no obedece
  hasta que se reinicia.
- El movimiento: la posición actual va hacia la pedida a la velocidad del
  perfil (0.229 rev/min por unidad; 0 = la máxima del motor).

QUÉ NO SIMULA
-------------
La carga, la inercia, el par, el calentamiento real, el ruido eléctrico ni los
tiempos del cable. Los códigos de error exactos que da un motor de verdad al
rechazar una escritura se han puesto según la definición del protocolo
(acceso = 7, límite = 6) y no se han contrastado con un motor.

DOS FORMAS DE ENCHUFARLO
------------------------
- ``PuertoSimulado``: ocupa el sitio del ``PortHandler`` de la biblioteca, en
  el mismo proceso. Vale en cualquier sistema (las pruebas).
- ``servir_en_pty``: solo en Linux. Crea un puerto serie falso del sistema
  (``/dev/pts/N``) que se abre con el ``PortHandler`` de verdad, como se
  abriría ``/dev/ttyACM0`` en la Raspberry.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

__all__ = ["crc16", "Motor", "SimuladorDynamixel", "PuertoSimulado", "servir_en_pty",
           "MODELO_XL430_W250", "PULSOS_POR_VUELTA"]

MODELO_XL430_W250 = 1060
PULSOS_POR_VUELTA = 4096
#: Velocidad sin carga del XL430-W250 a 12 V (61 rev/min), en pulsos por segundo.
VELOCIDAD_MAXIMA = 61 * PULSOS_POR_VUELTA / 60.0
#: Pulsos por segundo de cada unidad de «Profile Velocity» (0.229 rev/min).
PULSOS_POR_UNIDAD = 0.229 * PULSOS_POR_VUELTA / 60.0

# Direcciones de la tabla de control (XL430-W250, manual de ROBOTIS).
D_MODELO, D_ID, D_BAUDIOS, D_MODO = 0, 7, 8, 11
D_LIMITE_MAX, D_LIMITE_MIN = 48, 52
D_PAR, D_LED, D_ERROR_HW = 64, 65, 70
D_ACELERACION, D_VELOCIDAD, D_META = 108, 112, 116
D_MOVIENDO, D_POSICION, D_VOLTAJE, D_TEMPERATURA = 122, 132, 144, 146
TAMANO_TABLA = 147
#: Zonas de solo lectura dentro de la RAM.
SOLO_LECTURA = set(range(D_ERROR_HW, D_ERROR_HW + 1)) | set(range(120, TAMANO_TABLA))
SOLO_LECTURA |= set(range(0, 7))        # modelo, versión

INST_PING, INST_LEER, INST_ESCRIBIR, INST_REINICIAR = 0x01, 0x02, 0x03, 0x08
INST_ESTADO, INST_ESCRITURA_SINC = 0x55, 0x83
ERR_INSTRUCCION, ERR_CRC, ERR_RANGO, ERR_LARGO, ERR_LIMITE, ERR_ACCESO = 2, 3, 4, 5, 6, 7
ALERTA = 0x80
TODOS = 0xFE


def crc16(datos: bytes) -> int:
    """El CRC de Protocol 2.0: 16 bits, polinomio 0x8005, sin reflejar, desde 0."""
    crc = 0
    for byte in datos:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x8005) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


class Motor:
    """Un XL430: su tabla de control y cómo se mueve."""

    def __init__(self, id_: int, posicion: int = 2048,
                 reloj: Callable[[], float] = time.monotonic) -> None:
        self.tabla = bytearray(TAMANO_TABLA)
        self._reloj = reloj
        self._posicion = float(posicion)
        self._ultimo = reloj()
        self.poner(D_MODELO, 2, MODELO_XL430_W250)
        self.poner(D_ID, 1, id_)
        self.poner(D_BAUDIOS, 1, 1)
        self.poner(D_MODO, 1, 3)
        self.poner(D_LIMITE_MAX, 4, 4095)
        self.poner(D_LIMITE_MIN, 4, 0)
        self.poner(D_META, 4, posicion)
        self.poner(D_VOLTAJE, 2, 120)
        self.poner(D_TEMPERATURA, 1, 30)
        #: Todas las posiciones que se le han pedido y aceptó, en orden.
        self.metas: list[int] = []

    @property
    def id(self) -> int:
        return self.tabla[D_ID]

    def leer(self, direccion: int, largo: int) -> int:
        return int.from_bytes(self.tabla[direccion:direccion + largo], "little")

    def poner(self, direccion: int, largo: int, valor: int) -> None:
        self.tabla[direccion:direccion + largo] = (valor & (256 ** largo - 1)).to_bytes(
            largo, "little")

    @property
    def par(self) -> bool:
        return bool(self.tabla[D_PAR])

    @property
    def posicion(self) -> float:
        self.avanzar()
        return self._posicion

    def averiar(self, bits: int = 0x20) -> None:
        """Un error de hardware (0x20 = sobrecarga): el motor suelta el par."""
        self.tabla[D_ERROR_HW] = bits
        self.tabla[D_PAR] = 0

    def reiniciar(self) -> None:
        self.avanzar()
        self.tabla[D_ERROR_HW] = 0
        self.tabla[D_PAR] = 0
        self.poner(D_META, 4, round(self._posicion))

    def avanzar(self) -> None:
        """Mueve la posición actual hacia la pedida, lo que toque por el tiempo
        pasado. Sin par, el motor no se mueve."""
        ahora = self._reloj()
        dt, self._ultimo = ahora - self._ultimo, ahora
        if self.par:
            meta = self.leer(D_META, 4)
            unidades = self.leer(D_VELOCIDAD, 4)
            paso = (unidades * PULSOS_POR_UNIDAD if unidades else VELOCIDAD_MAXIMA) * dt
            falta = meta - self._posicion
            self._posicion = meta if abs(falta) <= paso else \
                self._posicion + (paso if falta > 0 else -paso)
            self.tabla[D_MOVIENDO] = int(self._posicion != meta)
        else:
            self.tabla[D_MOVIENDO] = 0
        self.poner(D_POSICION, 4, round(self._posicion))

    def escribir(self, direccion: int, datos: bytes) -> int:
        """Aplica una escritura. Devuelve el código de error (0 = bien)."""
        fin = direccion + len(datos)
        if fin > TAMANO_TABLA or not datos:
            return ERR_RANGO
        if any(d in SOLO_LECTURA for d in range(direccion, fin)):
            return ERR_ACCESO
        if direccion < 64 and self.par:
            return ERR_ACCESO                 # EEPROM con el par puesto
        if direccion == D_PAR and datos[0] and self.tabla[D_ERROR_HW]:
            return ERR_ACCESO                 # averiado: no acepta el par
        if direccion <= D_META < fin:
            if fin - D_META < 4:
                return ERR_LARGO
            meta = int.from_bytes(datos[D_META - direccion:D_META - direccion + 4],
                                  "little", signed=True)
            if self.tabla[D_MODO] == 3 and not \
                    self.leer(D_LIMITE_MIN, 4) <= meta <= self.leer(D_LIMITE_MAX, 4):
                return ERR_LIMITE
            self.avanzar()
            self.metas.append(meta)
        if direccion == D_PAR and datos[0] and not self.par:
            # Al poner el par, el motor se queda donde está.
            self.avanzar()
            self.poner(D_META, 4, round(self._posicion))
        self.tabla[direccion:fin] = datos
        return 0


def _quitar_relleno(parametros: bytes) -> bytes:
    return parametros.replace(b"\xff\xff\xfd\xfd", b"\xff\xff\xfd")


def _paquete(id_: int, instruccion: int, parametros: bytes) -> bytes:
    cuerpo = bytes([instruccion]) + parametros
    cuerpo = cuerpo.replace(b"\xff\xff\xfd", b"\xff\xff\xfd\xfd")
    largo = len(cuerpo) + 2
    cabecera = b"\xff\xff\xfd\x00" + bytes([id_, largo & 0xFF, largo >> 8])
    crc = crc16(cabecera + cuerpo)
    return cabecera + cuerpo + bytes([crc & 0xFF, crc >> 8])


class SimuladorDynamixel:
    """Un bus con varios motores. ``recibir(bytes)`` devuelve la respuesta."""

    def __init__(self, ids=(1, 2, 3, 4, 5, 6), reloj: Callable[[], float] = time.monotonic):
        self.reloj = reloj
        self.motores: list[Motor] = [Motor(i, reloj=reloj) for i in ids]
        self._entrada = b""
        self._candado = threading.Lock()
        #: Paquetes recibidos: (id, instrucción, parámetros).
        self.paquetes: list[tuple[int, int, bytes]] = []
        self.corruptos = 0

    def motor(self, id_: int) -> Motor | None:
        return next((m for m in self.motores if m.id == id_), None)

    def desenchufar(self, id_: int) -> Motor:
        motor = self.motor(id_)
        self.motores.remove(motor)
        return motor

    def recibir(self, datos: bytes) -> bytes:
        with self._candado:
            self._entrada += bytes(datos)
            salida = b""
            while True:
                inicio = self._entrada.find(b"\xff\xff\xfd\x00")
                if inicio < 0:
                    self._entrada = self._entrada[-3:]
                    break
                self._entrada = self._entrada[inicio:]
                if len(self._entrada) < 7:
                    break
                largo = self._entrada[5] | self._entrada[6] << 8
                if len(self._entrada) < 7 + largo:
                    break
                paquete, self._entrada = self._entrada[:7 + largo], self._entrada[7 + largo:]
                if crc16(paquete[:-2]) != (paquete[-2] | paquete[-1] << 8) or largo < 3:
                    self.corruptos += 1
                    continue
                salida += self._atender(paquete[4], paquete[7], _quitar_relleno(paquete[8:-2]))
            return salida

    def _atender(self, id_: int, instruccion: int, p: bytes) -> bytes:
        self.paquetes.append((id_, instruccion, p))
        if instruccion == INST_ESCRITURA_SINC:
            if len(p) < 4:
                return b""
            direccion, largo = p[0] | p[1] << 8, p[2] | p[3] << 8
            for i in range(4, len(p) - largo, largo + 1):
                motor = self.motor(p[i])
                if motor is not None:
                    motor.escribir(direccion, p[i + 1:i + 1 + largo])
            return b""                         # la escritura sincronizada no se contesta
        salida = b""
        for motor in [m for m in self.motores if id_ in (m.id, TODOS)]:
            id_respuesta = motor.id
            error, datos = 0, b""
            if instruccion == INST_PING:
                datos = bytes(motor.tabla[D_MODELO:D_MODELO + 2]) + b"\x2d"
            elif instruccion == INST_LEER and len(p) == 4:
                direccion, largo = p[0] | p[1] << 8, p[2] | p[3] << 8
                if direccion + largo > TAMANO_TABLA or largo == 0:
                    error = ERR_RANGO
                else:
                    motor.avanzar()
                    datos = bytes(motor.tabla[direccion:direccion + largo])
            elif instruccion == INST_ESCRIBIR and len(p) >= 3:
                error = motor.escribir(p[0] | p[1] << 8, p[2:])
            elif instruccion == INST_REINICIAR:
                motor.reiniciar()
            else:
                error = ERR_INSTRUCCION
            if motor.tabla[D_ERROR_HW]:
                error |= ALERTA
            if id_ != TODOS or instruccion == INST_PING:
                salida += _paquete(id_respuesta, INST_ESTADO, bytes([error]) + datos)
        return salida


class PuertoSimulado:
    """Ocupa el sitio del ``PortHandler`` de ``dynamixel_sdk``: lo que la
    biblioteca escribe va al simulador, y lo que lee es su respuesta.

    ``roto = True`` hace que el puerto falle como un USB desenchufado.
    """

    def __init__(self, simulador: SimuladorDynamixel, baudios: int = 57600) -> None:
        self.simulador = simulador
        self.is_using = False
        self.is_open = False
        self.roto = False
        self._baudios = baudios
        self._pendiente = b""

    def _comprobar(self) -> None:
        if self.roto:
            raise OSError("el puerto serie desaparecio")

    def openPort(self) -> bool:  # noqa: N802 -- los nombres son los de la biblioteca
        self._comprobar()
        self.is_open = True
        self.is_using = False       # como un PortHandler recién creado
        self._pendiente = b""
        return True

    def closePort(self) -> None:  # noqa: N802
        self.is_open = False

    def setBaudRate(self, baudios: int) -> bool:  # noqa: N802
        self._comprobar()
        self._baudios = baudios
        return True

    def getBaudRate(self) -> int:  # noqa: N802
        return self._baudios

    def clearPort(self) -> None:  # noqa: N802
        self._comprobar()
        self._pendiente = b""

    def writePort(self, paquete) -> int:  # noqa: N802
        self._comprobar()
        self._pendiente += self.simulador.recibir(bytes(paquete))
        return len(paquete)

    def readPort(self, largo: int) -> bytes:  # noqa: N802
        self._comprobar()
        datos, self._pendiente = self._pendiente[:largo], self._pendiente[largo:]
        return datos

    def setPacketTimeout(self, largo: int) -> None:  # noqa: N802
        pass

    def setPacketTimeoutMillis(self, ms: float) -> None:  # noqa: N802
        pass

    def isPacketTimeout(self) -> bool:  # noqa: N802
        # Un motor que no contesta: no hay nada pendiente y no lo va a haber.
        return not self._pendiente


def servir_en_pty(simulador: SimuladorDynamixel) -> tuple[str, Callable[[], None]]:
    """Solo en Linux: un puerto serie falso del sistema atendido por el
    simulador. Devuelve ``(ruta, parar)``; la ruta se abre como ``/dev/ttyACM0``."""
    import os
    import pty
    import tty

    maestro, esclavo = pty.openpty()
    tty.setraw(maestro)
    tty.setraw(esclavo)
    ruta = os.ttyname(esclavo)
    seguir = threading.Event()
    seguir.set()

    def atender() -> None:
        while seguir.is_set():
            try:
                datos = os.read(maestro, 1024)
            except OSError:
                break
            if datos:
                respuesta = simulador.recibir(datos)
                if respuesta:
                    os.write(maestro, respuesta)

    hilo = threading.Thread(target=atender, name="simulador-pty", daemon=True)
    hilo.start()

    def parar() -> None:
        seguir.clear()
        for descriptor in (esclavo, maestro):
            try:
                os.close(descriptor)
            except OSError:
                pass

    return ruta, parar
