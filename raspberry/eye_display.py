"""La pantalla de los ojos: corre EN LA RASPBERRY PI del robot.

QUÉ HACE
--------
Pinta en la pantalla interna de la cabeza (5 pulgadas, 800x480) la expresión
que lleva el mensaje de pose que publica la PC del narrador, 20 veces por
segundo::

    telmeow/teleop/pose   {"pan": ..., "tilt": ..., "emo": "feliz", ...}

De ese mensaje solo lee ``emo`` (o ``emotion``, su alias). Es el mismo
comportamiento que la pantalla de ojos del visor 3D
(``telmeow_viewer.html``, ``setEmotion``): las mismas seis imágenes, los mismos
nombres y alias en inglés, y el mismo fundido de 350 ms entre expresiones.

LAS REGLAS
----------
- **Nunca una pantalla de error, nunca cerrarse solo.** Una expresión que no se
  conoce o cuya imagen falta se ignora y se queda la que había; lo mismo si el
  broker se cae (y reintenta solo, de 1 a 30 s).
- **Los ojos no se quedan a oscuras.** Arranca en ``neutral`` sin esperar a
  ningún mensaje.

LAS IMÁGENES
------------
Los seis PNG de ``telmeow_viewer/`` (``01_NEUTRAL.png`` ... ``06_CONCENTRADO.png``,
800x480, fondo negro). En la Raspberry se copian a ``raspberry/ojos/``; dentro
del repositorio se leen directamente de ``telmeow_viewer/``.

USO
---
    python eye_display.py --broker 192.168.1.50 --display 1
    python eye_display.py --windowed --prueba     # sin MQTT: teclas 1-6

Esc o ``q`` cierran. No importa nada de ``telmeow_teleop`` ni de los otros
archivos de esta carpeta.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

__all__ = ["TOPIC_POSE", "ARCHIVOS", "ALIAS", "FUNDIDO_S", "nombre_de", "carpeta_de_ojos",
           "Expresion", "EnlaceOjos", "PantallaOjos", "main"]

#: El de ``telmeow_teleop.config.MQTTConfig.topic_pose`` (una prueba lo comprueba).
TOPIC_POSE = "telmeow/teleop/pose"

#: Las mismas que ``EMO_FILES`` y ``EMO_ALIAS`` del visor (una prueba lo comprueba).
ARCHIVOS = {
    "neutral": "01_NEUTRAL.png", "sorprendido": "02_SORPRENDIDO.png",
    "feliz": "03_FELIZ.png", "enojado": "04_ENOJADO.png",
    "triste": "05_TRISTE.png", "concentrado": "06_CONCENTRADO.png",
}
ALIAS = {"happy": "feliz", "sad": "triste", "surprise": "sorprendido",
         "surprised": "sorprendido", "angry": "enojado", "anger": "enojado",
         "focused": "concentrado"}
#: Duración del fundido entre expresiones, como en el visor (350 ms).
FUNDIDO_S = 0.35
FONDO = (0, 0, 0)
CUADROS_POR_SEGUNDO = 30

registro = logging.getLogger("ojos")


def nombre_de(valor: Any) -> str | None:
    """El nombre de la expresión que pide ``valor``, o ``None`` si no es una
    de las seis (ni un alias)."""
    if not isinstance(valor, str):
        return None
    clave = valor.strip().lower()
    clave = ALIAS.get(clave, clave)
    return clave if clave in ARCHIVOS else None


def carpeta_de_ojos() -> Path:
    """Donde están los PNG: ``ojos/`` junto a este archivo (la Raspberry) o,
    si no existe, ``telmeow_viewer/`` (dentro del repositorio)."""
    aqui = Path(__file__).resolve().parent
    propia = aqui / "ojos"
    return propia if propia.is_dir() else aqui.parent / "telmeow_viewer"


class Expresion:
    """La expresión que TOCA, según el último mensaje. La escribe el hilo de
    MQTT y la lee el de la pantalla."""

    def __init__(self, inicial: str = "neutral") -> None:
        self._candado = threading.Lock()
        self._nombre = inicial

    def pedir(self, valor: Any) -> bool:
        nombre = nombre_de(valor)
        if nombre is None:
            return False
        with self._candado:
            self._nombre = nombre
        return True

    def leer(self) -> str:
        with self._candado:
            return self._nombre


class EnlaceOjos:
    """La suscripción al topic de pose, con reconexión sola (como el resto)."""

    def __init__(self, expresion: Expresion, host: str, puerto: int = 1883, *,
                 topic: str = TOPIC_POSE, cliente: Any = None) -> None:
        self.expresion = expresion
        self.host, self.puerto, self.topic = host, puerto, topic
        self.conectado = False
        self._ultima_rara: Any = None
        if cliente is None:
            import paho.mqtt.client as mqtt
            cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id=f"telmeow-ojos-{os.getpid()}")
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
                registro.warning("al cerrar: %s", exc)

    def _al_conectar(self, cliente, userdata, flags, codigo, propiedades=None) -> None:
        if getattr(codigo, "is_failure", False):
            registro.warning("el broker rechazo la conexion: %s", codigo)
            return
        self.conectado = True
        registro.info("conectado al broker %s:%d", self.host, self.puerto)
        cliente.subscribe(self.topic, 0)

    def _al_desconectar(self, cliente, userdata, *resto) -> None:
        if self.conectado:
            registro.warning("se perdio el broker; se queda la expresion y reintento")
        self.conectado = False

    def _al_mensaje(self, cliente, userdata, mensaje) -> None:
        try:
            datos = json.loads(mensaje.payload.decode("utf-8"))
            if not isinstance(datos, dict):
                return
            pedida = datos.get("emo", datos.get("emotion"))
            if pedida is None or self.expresion.pedir(pedida):
                return
            if pedida != self._ultima_rara:   # una vez, no 20 por segundo
                self._ultima_rara = pedida
                registro.warning("expresion desconocida: %r; se queda la que habia", pedida)
        except Exception as exc:  # noqa: BLE001 -- nunca tumbar el hilo de MQTT
            registro.debug("mensaje ilegible: %s", exc)


class PantallaOjos:
    """La ventana de pygame: enseña la expresión pedida, con un fundido."""

    def __init__(self, expresion: Expresion, carpeta: Path, *, ventana: bool = False,
                 monitor: int = 0, teclas: bool = False, reloj=time.monotonic) -> None:
        import pygame

        self.pg = pygame
        self.expresion = expresion
        self.teclas = teclas
        self._reloj = reloj
        pygame.display.init()
        if ventana:
            self.lienzo = pygame.display.set_mode((800, 480), display=monitor)
        else:
            self.lienzo = pygame.display.set_mode((0, 0), pygame.FULLSCREEN, display=monitor)
            pygame.mouse.set_visible(False)
        pygame.display.set_caption("Telmeow - los ojos")
        self.tamano = self.lienzo.get_size()
        self.imagenes = self._cargar(Path(carpeta))
        self.seguir = True
        #: La expresión que se ve (o hacia la que va el fundido) y la anterior.
        self.actual: str | None = None
        self._anterior: str | None = None
        self._inicio_fundido = 0.0
        self._pintado: tuple | None = None
        self.tic = pygame.time.Clock()

    def _cargar(self, carpeta: Path) -> dict:
        ancho, alto = self.tamano
        imagenes = {}
        for nombre, archivo in ARCHIVOS.items():
            try:
                cruda = self.pg.image.load(str(carpeta / archivo)).convert()
                w, h = cruda.get_size()
                escala = min(ancho / w, alto / h)
                tam = (max(1, round(w * escala)), max(1, round(h * escala)))
                if tam != (w, h):
                    cruda = self.pg.transform.smoothscale(cruda, tam)
                imagenes[nombre] = (cruda, ((ancho - tam[0]) // 2, (alto - tam[1]) // 2))
            except Exception as exc:  # noqa: BLE001 -- sin esa imagen, esa expresion se ignora
                registro.error("no puedo cargar %s: %s", carpeta / archivo, exc)
        if not imagenes:
            registro.error("no hay ninguna imagen de ojos en %s: la pantalla se queda en negro",
                           carpeta)
        return imagenes

    def _eventos(self) -> None:
        pg = self.pg
        for evento in pg.event.get():
            if evento.type == pg.QUIT:
                self.seguir = False
            elif evento.type == pg.KEYDOWN:
                if evento.key in (pg.K_ESCAPE, pg.K_q):
                    self.seguir = False
                elif self.teclas and pg.K_1 <= evento.key <= pg.K_6:
                    self.expresion.pedir(list(ARCHIVOS)[evento.key - pg.K_1])

    def paso(self) -> None:
        self._eventos()
        pedida = self.expresion.leer()
        ahora = self._reloj()
        if pedida != self.actual and pedida in self.imagenes:
            self._anterior, self.actual = self.actual, pedida
            self._inicio_fundido = ahora
        if self.actual is None:
            return
        avance = min(1.0, (ahora - self._inicio_fundido) / FUNDIDO_S)
        if self._anterior is None:
            avance = 1.0
        clave = (self.actual, self._anterior if avance < 1.0 else None, round(avance, 2))
        if clave == self._pintado:
            return
        self.lienzo.fill(FONDO)
        if avance < 1.0:
            self.lienzo.blit(*self.imagenes[self._anterior])
        imagen, donde = self.imagenes[self.actual]
        imagen.set_alpha(round(255 * avance))
        self.lienzo.blit(imagen, donde)
        imagen.set_alpha(255)
        self.pg.display.flip()
        self._pintado = clave

    def bucle(self) -> None:
        while self.seguir:
            try:
                self.paso()
            except Exception:  # noqa: BLE001 -- anotar y seguir
                registro.exception("fallo en una vuelta de los ojos; sigo")
            self.tic.tick(CUADROS_POR_SEGUNDO)

    def cerrar(self) -> None:
        self.pg.quit()


def _partir_broker(texto: str) -> tuple[str, int]:
    host, sep, puerto = texto.rpartition(":")
    if sep and puerto.isdigit() and host:
        return host, int(puerto)
    return texto, 1883


def main(argv: list[str] | None = None) -> int:
    env = os.environ
    p = argparse.ArgumentParser(
        description="La pantalla de los ojos de Telmeow, para la Raspberry Pi.")
    p.add_argument("--broker", default=env.get("TELMEOW_MQTT_HOST", "localhost"),
                   help="La maquina donde corre Mosquitto (IP o nombre, con :puerto).")
    p.add_argument("--eyes-dir", type=Path, default=None,
                   help="Carpeta con los seis PNG. Por defecto ojos/ junto a este archivo.")
    p.add_argument("--display", type=int, default=0, help="Numero del monitor.")
    p.add_argument("--windowed", action="store_true", help="En una ventana de 800x480.")
    p.add_argument("--prueba", action="store_true",
                   help="Sin MQTT: las teclas 1 a 6 cambian la expresion.")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(message)s")

    expresion = Expresion()
    pantalla = PantallaOjos(expresion, args.eyes_dir or carpeta_de_ojos(),
                            ventana=args.windowed, monitor=args.display, teclas=args.prueba)
    enlace = None
    if args.prueba:
        print("Modo de prueba: teclas 1-6 = " + ", ".join(ARCHIVOS) + ". Esc = salir.")
    else:
        enlace = EnlaceOjos(expresion, *_partir_broker(args.broker))
        enlace.conectar()

    def parar(*_):
        pantalla.seguir = False

    signal.signal(signal.SIGTERM, parar)
    try:
        pantalla.bucle()
    except KeyboardInterrupt:
        pass
    finally:
        if enlace is not None:
            enlace.cerrar()
        pantalla.cerrar()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
