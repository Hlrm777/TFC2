"""La pantalla del cuento (P11): corre EN LA RASPBERRY PI del robot.

QUÉ HACE
--------
Pinta, a pantalla completa y sobre fondo negro, la página del cuento que el
narrador tiene abierta en su interfaz (P10). No decide nada: obedece a dos
mensajes MQTT que publica la PC del narrador, los dos RETENIDOS (el broker
guarda el último y se lo da a quien se conecte después)::

    telmeow/teleop/story   {"story": "El punto - Peter Reynolds.pdf", "page": 1}
    telmeow/teleop/page    {"page": 7, "total": 31}

El gesto de cabeza del robot NO sale de aquí: lo lleva el mensaje de pose, que
la PC manda en el mismo instante que el de página (``evento_pagina`` en
``telmeow_teleop/main.py``). Este programa solo pinta.

QUÉ REEMPLAZA
-------------
Al Firefox por escritorio remoto, donde el operador pasaba de página con las
flechas: la página y el gesto del robot no podían salir del mismo evento.

LAS TRES REGLAS
---------------
1. **La página se dibuja antes de que haga falta.** Al llegar un cuento, un
   hilo aparte convierte TODAS sus páginas en imágenes del tamaño de la
   pantalla y las guarda en memoria; pasar de página es enseñar una imagen ya
   hecha (unos milisegundos). Se empieza por la página pedida y sus vecinas.
2. **Nunca una pantalla de error, nunca cerrarse solo.** Si el broker se cae,
   si el cuento no está en la carpeta, si un PDF está roto o una página no se
   deja dibujar: se queda la última página buena en pantalla, se anota en el
   registro (``journalctl``) y se sigue. Hay niños mirando.
3. **Al volver, se pone al día solo.** Como los dos mensajes van retenidos, al
   reconectar (o al arrancar a mitad de la sesión) recibe el cuento y la página
   vigentes sin que nadie toque nada.

DEPENDENCIAS (``raspberry/requirements.txt``)
---------------------------------------------
``pygame`` (la ventana), ``pypdfium2`` (dibuja el PDF; trae su propio motor,
no necesita poppler) y ``paho-mqtt``. Este archivo NO importa nada de
``telmeow_teleop``: a la Raspberry se copia solo la carpeta ``raspberry/``.

USO
---
    python story_display.py --broker 192.168.1.50 --stories-dir ~/cuentos
    python story_display.py --windowed --prueba ~/cuentos/trucas.pdf   # sin MQTT

Esc o ``q`` cierran. En el modo de prueba, las flechas pasan de página.
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
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

__all__ = [
    "TOPIC_CUENTO", "TOPIC_PAGINA", "encajar", "buscar_cuento", "Guion",
    "Imagen", "Prerenderizador", "dibujar_pdf", "Enlace", "Pantalla", "main",
]

#: Los mismos de ``telmeow_teleop.config.MQTTConfig`` (una prueba lo comprueba).
TOPIC_CUENTO = "telmeow/teleop/story"
TOPIC_PAGINA = "telmeow/teleop/page"

FONDO = (0, 0, 0)
GRIS = (70, 70, 70)
#: Alto de la barra de progreso, como fracción del alto de la pantalla.
ALTO_BARRA = 0.006
CUADROS_POR_SEGUNDO = 30

registro = logging.getLogger("pantalla")


# --------------------------------------------------------------------------
# Lógica sin pantalla
# --------------------------------------------------------------------------


def encajar(ancho: float, alto: float, ancho_max: int, alto_max: int) -> tuple[int, int, int, int]:
    """Escala ``ancho x alto`` para que quepa entero en la pantalla sin
    deformarse, centrado. Devuelve ``(x, y, ancho, alto)`` en píxeles."""
    if ancho <= 0 or alto <= 0 or ancho_max <= 0 or alto_max <= 0:
        return 0, 0, max(1, ancho_max), max(1, alto_max)
    escala = min(ancho_max / ancho, alto_max / alto)
    w = max(1, min(ancho_max, round(ancho * escala)))
    h = max(1, min(alto_max, round(alto * escala)))
    return (ancho_max - w) // 2, (alto_max - h) // 2, w, h


def _normal(nombre: str) -> str:
    # «túnel» puede venir como una letra con tilde o como «u» + tilde suelta
    # según el sistema que copió el archivo: se comparan en la misma forma.
    return unicodedata.normalize("NFC", nombre)


def buscar_cuento(carpeta: Path, nombre: Any) -> Path | None:
    """El PDF de la carpeta que se llama EXACTAMENTE ``nombre``, o ``None``.

    Solo se comparan nombres de archivos que están en la carpeta: nada que
    llegue por la red puede apuntar fuera de ella (``..``, rutas, barras).
    """
    if not isinstance(nombre, str) or not nombre:
        return None
    try:
        buscado = _normal(nombre)
        for ruta in Path(carpeta).iterdir():
            if ruta.suffix.lower() == ".pdf" and _normal(ruta.name) == buscado and ruta.is_file():
                return ruta
    except OSError as exc:
        registro.warning("no puedo leer la carpeta de cuentos %s: %s", carpeta, exc)
    return None


def _entero(valor: Any) -> int | None:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    return int(valor) if valor == int(valor) and valor >= 1 else None


class Guion:
    """Qué cuento y qué página TOCA enseñar, según los mensajes recibidos.

    Lo escribe el hilo de MQTT y lo lee el de la pantalla; por eso el candado.
    No sabe nada de PDF ni de píxeles.

    El caso delicado es el arranque: los dos mensajes retenidos pueden llegar
    en cualquier orden. Si la página llega ANTES que el cuento, se guarda y
    vale más que el ``page`` del mensaje del cuento (que es siempre 1, porque
    se publicó al abrirlo). Con un cuento ya recibido, uno DISTINTO sí empieza
    en su propia página, y el mismo repetido no mueve nada.
    """

    def __init__(self) -> None:
        self._candado = threading.Lock()
        self.cuento: str | None = None
        self.pagina: int = 1
        self._pagina_suelta: int | None = None

    def al_cuento(self, datos: Any) -> bool:
        """Mensaje del topic del cuento. Devuelve si lo aceptó."""
        if not isinstance(datos, dict) or not isinstance(datos.get("story"), str) \
                or not datos["story"]:
            return False
        with self._candado:
            if datos["story"] == self.cuento:
                # El mismo cuento otra vez (el retenido, al reconectar): su
                # ``page`` es la del día en que se abrió. La página vigente la
                # trae el otro topic; hacerle caso aquí haría parpadear la 1.
                return True
            pagina = self._pagina_suelta if self.cuento is None else None
            self._pagina_suelta = None
            self.cuento = datos["story"]
            self.pagina = pagina or _entero(datos.get("page")) or 1
        return True

    def a_la_pagina(self, datos: Any) -> bool:
        """Mensaje del topic de página. Devuelve si lo aceptó."""
        pagina = _entero(datos.get("page")) if isinstance(datos, dict) else None
        if pagina is None:
            return False
        with self._candado:
            if self.cuento is None:
                self._pagina_suelta = pagina
            else:
                self.pagina = pagina
        return True

    def mover(self, paso: int, total: int) -> None:
        """Las flechas del modo de prueba."""
        with self._candado:
            self.pagina = min(max(1, self.pagina + paso), max(1, total))

    def leer(self) -> tuple[str | None, int]:
        with self._candado:
            return self.cuento, self.pagina


# --------------------------------------------------------------------------
# Del PDF a imágenes, antes de que hagan falta
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Imagen:
    """Una página ya dibujada: píxeles RGB seguidos, fila a fila."""

    ancho: int
    alto: int
    datos: bytes


def _a_rgb(crudo: bytes, ancho: int, alto: int, paso: int, canales: int) -> bytes:
    """Deja los píxeles en RGB seguido: quita el relleno de final de fila
    (``paso`` es lo que ocupa una fila, a veces más que sus píxeles) y pasa a
    tres canales lo que venga en gris (1) o con transparencia (4)."""
    fila = ancho * canales
    if paso != fila:
        crudo = b"".join(crudo[y * paso: y * paso + fila] for y in range(alto))
    else:
        crudo = crudo[:fila * alto]
    if canales == 3:
        return crudo
    rgb = bytearray(ancho * alto * 3)
    for canal in range(3):
        rgb[canal::3] = crudo[canal::4] if canales == 4 else crudo
    return bytes(rgb)


def dibujar_pdf(ruta: Path, tamano: tuple[int, int]):
    """Abre el PDF y devuelve ``(paginas, dibujar, cerrar)``.

    ``dibujar(i)`` da la ``Imagen`` de la página ``i`` (desde 0) escalada para
    caber en ``tamano``. Es la única parte que toca pypdfium2.
    """
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(ruta))

    def dibujar(indice: int) -> Imagen:
        pagina = pdf[indice]
        try:
            ancho, alto = pagina.get_size()
            _, _, w, _ = encajar(ancho, alto, *tamano)
            mapa = pagina.render(scale=w / ancho, rev_byteorder=True)
            try:
                return Imagen(mapa.width, mapa.height, _a_rgb(
                    bytes(mapa.buffer), mapa.width, mapa.height, mapa.stride,
                    mapa.n_channels))
            finally:
                mapa.close()
        finally:
            pagina.close()

    return len(pdf), dibujar, pdf.close


class Prerenderizador:
    """Dibuja en un hilo aparte todas las páginas del cuento vigente.

    ``pedir(ruta, pagina)`` cambia de cuento (o solo avisa de la página en que
    va la lectura): el hilo dibuja primero esa página, luego la siguiente y la
    anterior, y después el resto. ``imagen(ruta, pagina)`` la entrega si ya
    está. Un cuento nuevo tira las imágenes del anterior.

    Nada de lo que falle aquí sale del hilo: un PDF que no abre o una página
    que no se dibuja se anotan y se sigue con lo demás.
    """

    def __init__(self, tamano: tuple[int, int],
                 abrir: Callable[[Path, tuple[int, int]], tuple] = dibujar_pdf) -> None:
        self.tamano = tamano
        self._abrir = abrir
        self._cond = threading.Condition()
        self._ruta: Path | None = None
        self._pagina = 1
        self._total = 0
        self._hechas: dict[int, Imagen] = {}
        self._fallidas: set[int] = set()
        self._version = 0
        self._vivo = True
        self._hilo = threading.Thread(target=self._trabajar, name="prerender", daemon=True)
        self._hilo.start()

    # -- lo que llama la pantalla ------------------------------------------

    def pedir(self, ruta: Path, pagina: int) -> None:
        with self._cond:
            if ruta != self._ruta:
                self._ruta = ruta
                self._total = 0
                self._hechas = {}
                self._fallidas = set()
                self._version += 1
            self._pagina = max(1, int(pagina))
            self._cond.notify_all()

    def imagen(self, ruta: Path, pagina: int) -> Imagen | None:
        with self._cond:
            return self._hechas.get(pagina) if ruta == self._ruta else None

    def progreso(self) -> tuple[int, int]:
        """``(páginas resueltas, páginas del cuento)``; ``(0, 0)`` sin cuento
        abierto todavía."""
        with self._cond:
            return len(self._hechas) + len(self._fallidas), self._total

    @property
    def total(self) -> int:
        with self._cond:
            return self._total

    def cerrar(self) -> None:
        with self._cond:
            self._vivo = False
            self._cond.notify_all()
        self._hilo.join(timeout=2.0)

    # -- el hilo -------------------------------------------------------------

    def _siguiente(self) -> int | None:
        """La página que toca dibujar (desde 1), con el candado tomado."""
        pendientes = [p for p in range(1, self._total + 1)
                      if p not in self._hechas and p not in self._fallidas]
        if not pendientes:
            return None
        actual = self._pagina
        # La pedida, la siguiente, la anterior; luego hacia delante.
        return min(pendientes, key=lambda p: (
            0 if p == actual else 1 if p == actual + 1 else 2 if p == actual - 1
            else 3 if p > actual else 4, p))

    def _trabajar(self) -> None:
        version_abierta = -1
        dibujar = cerrar = None
        inicio = 0.0
        while True:
            with self._cond:
                while self._vivo and (self._ruta is None or (
                        self._version == version_abierta and self._siguiente() is None)):
                    self._cond.wait()
                if not self._vivo:
                    break
                version, ruta = self._version, self._ruta
            if version != version_abierta:
                if cerrar is not None:
                    _sin_fallar(cerrar)
                dibujar = cerrar = None
                version_abierta = version
                try:
                    total, dibujar, cerrar = self._abrir(ruta, self.tamano)
                except Exception as exc:  # noqa: BLE001 -- regla 2
                    registro.error("no puedo abrir %s: %s", ruta.name, exc)
                    total = 0
                with self._cond:
                    if self._version == version:
                        self._total = total
                inicio = time.perf_counter()
                continue
            with self._cond:
                pagina = self._siguiente() if self._version == version else None
            if pagina is None or dibujar is None:
                continue
            try:
                hecha = dibujar(pagina - 1)
            except Exception as exc:  # noqa: BLE001 -- regla 2
                registro.error("no puedo dibujar la pagina %d de %s: %s", pagina, ruta.name, exc)
                hecha = None
            with self._cond:
                if self._version != version:
                    continue
                if hecha is None:
                    self._fallidas.add(pagina)
                else:
                    self._hechas[pagina] = hecha
                if len(self._hechas) + len(self._fallidas) == self._total:
                    registro.info("%s: %d paginas listas en %.1f s", ruta.name,
                                  len(self._hechas), time.perf_counter() - inicio)
        if cerrar is not None:
            _sin_fallar(cerrar)


def _sin_fallar(funcion: Callable[[], Any]) -> None:
    try:
        funcion()
    except Exception as exc:  # noqa: BLE001 -- regla 2
        registro.warning("al cerrar: %s", exc)


# --------------------------------------------------------------------------
# MQTT
# --------------------------------------------------------------------------


class Enlace:
    """La suscripción a los dos topics, con reconexión sola.

    ``connect_async`` + ``reconnect_delay_set``, como ``publisher.py``: si el
    broker no está o se cae, paho reintenta en segundo plano (de 1 a 30 s) y
    aquí no se entera nadie. La suscripción se hace en ``on_connect``, así que
    se repite en cada reconexión y el broker vuelve a dar los retenidos.
    """

    def __init__(self, guion: Guion, host: str, puerto: int = 1883, *,
                 topic_cuento: str = TOPIC_CUENTO, topic_pagina: str = TOPIC_PAGINA,
                 cliente: Any = None) -> None:
        self.guion = guion
        self.host, self.puerto = host, puerto
        self.topic_cuento, self.topic_pagina = topic_cuento, topic_pagina
        self.conectado = False
        if cliente is None:
            import paho.mqtt.client as mqtt
            cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id=f"telmeow-pantalla-{os.getpid()}")
        self._cliente = cliente
        cliente.on_connect = self._al_conectar
        cliente.on_disconnect = self._al_desconectar
        cliente.on_message = self._al_mensaje
        cliente.reconnect_delay_set(min_delay=1, max_delay=30)

    def conectar(self) -> None:
        self._cliente.connect_async(self.host, self.puerto, 30)
        self._cliente.loop_start()

    def cerrar(self) -> None:
        _sin_fallar(self._cliente.disconnect)
        _sin_fallar(self._cliente.loop_stop)

    def _al_conectar(self, cliente, userdata, flags, codigo, propiedades=None) -> None:
        if getattr(codigo, "is_failure", False):
            registro.warning("el broker rechazo la conexion: %s", codigo)
            return
        self.conectado = True
        registro.info("conectado al broker %s:%d", self.host, self.puerto)
        cliente.subscribe([(self.topic_cuento, 0), (self.topic_pagina, 0)])

    def _al_desconectar(self, cliente, userdata, *resto) -> None:
        if self.conectado:
            registro.warning("se perdio el broker; se queda la ultima pagina y reintento")
        self.conectado = False

    def _al_mensaje(self, cliente, userdata, mensaje) -> None:
        try:
            datos = json.loads(mensaje.payload.decode("utf-8"))
            if mensaje.topic == self.topic_cuento:
                aceptado = self.guion.al_cuento(datos)
            elif mensaje.topic == self.topic_pagina:
                aceptado = self.guion.a_la_pagina(datos)
            else:
                return
            if not aceptado:
                registro.warning("mensaje sin sentido en %s: %r", mensaje.topic, datos)
        except Exception as exc:  # noqa: BLE001 -- regla 2
            registro.warning("mensaje ilegible en %s: %s", mensaje.topic, exc)


# --------------------------------------------------------------------------
# La pantalla
# --------------------------------------------------------------------------


class Pantalla:
    """La ventana de pygame y su bucle.

    ``paso()`` es una vuelta: mira qué toca según el ``Guion``, se lo pide al
    ``Prerenderizador`` y, si la imagen está, la enseña. Mientras no está, se
    queda lo que había (la página anterior, o el negro del arranque).
    """

    def __init__(self, guion: Guion, carpeta: Path, *, ventana: bool = False,
                 monitor: int = 0, flechas: bool = False,
                 abrir: Callable[[Path, tuple[int, int]], tuple] = dibujar_pdf) -> None:
        import pygame

        self.pg = pygame
        self.guion = guion
        self.carpeta = Path(carpeta)
        self.flechas = flechas
        pygame.display.init()
        pygame.font.init()
        if ventana:
            self.lienzo = pygame.display.set_mode((960, 540), display=monitor)
        else:
            self.lienzo = pygame.display.set_mode((0, 0), pygame.FULLSCREEN, display=monitor)
            pygame.mouse.set_visible(False)
        pygame.display.set_caption("Telmeow - el cuento")
        self.tamano = self.lienzo.get_size()
        self.pre = Prerenderizador(self.tamano, abrir)
        self.reloj = pygame.time.Clock()
        self.seguir = True
        #: Lo que hay en pantalla ahora: (cuento, página) o None.
        self.mostrada: tuple[str, int] | None = None
        self._cuento_resuelto: tuple[str, Path | None] | None = None
        self._ultimo_intento = 0.0
        self._ultima_barra: tuple[int, int] | None = None
        self._pagina_en_pantalla = None
        self._pintar_espera()

    # -- dibujo --------------------------------------------------------------

    def _pintar_espera(self) -> None:
        self.lienzo.fill(FONDO)
        try:
            letra = self.pg.font.Font(None, max(18, self.tamano[1] // 36))
            texto = letra.render("Telmeow", True, GRIS)
            self.lienzo.blit(texto, texto.get_rect(center=(self.tamano[0] // 2,
                                                           self.tamano[1] // 2)))
        except Exception as exc:  # noqa: BLE001 -- sin letra, negro y ya
            registro.debug("sin letra: %s", exc)
        self.pg.display.flip()

    def _pintar(self, imagen: Imagen) -> None:
        superficie = self.pg.image.frombuffer(imagen.datos, (imagen.ancho, imagen.alto), "RGB")
        x, y, w, h = encajar(imagen.ancho, imagen.alto, *self.tamano)
        if (w, h) != (imagen.ancho, imagen.alto):
            superficie = self.pg.transform.smoothscale(superficie, (w, h))
        self._pagina_en_pantalla = (superficie, (x, y))
        self._repintar()

    def _repintar(self) -> None:
        self.lienzo.fill(FONDO)
        if self._pagina_en_pantalla is not None:
            self.lienzo.blit(*self._pagina_en_pantalla)
        hechas, total = self._ultima_barra or (0, 0)
        if total and hechas < total:
            alto = max(2, round(self.tamano[1] * ALTO_BARRA))
            ancho = round(self.tamano[0] * hechas / total)
            self.pg.draw.rect(self.lienzo, GRIS, (0, self.tamano[1] - alto, ancho, alto))
        self.pg.display.flip()

    # -- una vuelta ----------------------------------------------------------

    def _ruta_del_cuento(self, cuento: str) -> Path | None:
        """La ruta del cuento, buscándola de nuevo cada 2 s si no está: así
        basta copiar el PDF que faltaba para que aparezca, sin reiniciar."""
        ahora = time.monotonic()
        resuelto = self._cuento_resuelto
        if resuelto is not None and resuelto[0] == cuento and (
                resuelto[1] is not None or ahora - self._ultimo_intento < 2.0):
            return resuelto[1]
        ruta = buscar_cuento(self.carpeta, cuento)
        if ruta is None and (resuelto is None or resuelto[0] != cuento):
            registro.error("el cuento %r no esta en %s; se queda lo que habia",
                           cuento, self.carpeta)
        self._cuento_resuelto = (cuento, ruta)
        self._ultimo_intento = ahora
        return ruta

    def _eventos(self) -> None:
        pg = self.pg
        for evento in pg.event.get():
            if evento.type == pg.QUIT:
                self.seguir = False
            elif evento.type == pg.KEYDOWN:
                if evento.key in (pg.K_ESCAPE, pg.K_q):
                    self.seguir = False
                elif self.flechas and evento.key in (pg.K_RIGHT, pg.K_DOWN, pg.K_SPACE,
                                                     pg.K_PAGEDOWN):
                    self.guion.mover(+1, self.pre.total)
                elif self.flechas and evento.key in (pg.K_LEFT, pg.K_UP, pg.K_PAGEUP):
                    self.guion.mover(-1, self.pre.total)

    def paso(self) -> None:
        self._eventos()
        cuento, pagina = self.guion.leer()
        if cuento is None:
            return
        ruta = self._ruta_del_cuento(cuento)
        if ruta is None:
            return
        self.pre.pedir(ruta, pagina)
        if self.mostrada != (cuento, pagina):
            imagen = self.pre.imagen(ruta, pagina)
            if imagen is not None:
                self._pintar(imagen)
                self.mostrada = (cuento, pagina)
        barra = self.pre.progreso()
        if barra != self._ultima_barra:
            self._ultima_barra = barra
            if self._pagina_en_pantalla is not None or barra[1]:
                self._repintar()

    def bucle(self) -> None:
        while self.seguir:
            try:
                self.paso()
            except Exception:  # noqa: BLE001 -- regla 2: anotar y seguir
                registro.exception("fallo en una vuelta de la pantalla; sigo")
            self.reloj.tick(CUADROS_POR_SEGUNDO)

    def cerrar(self) -> None:
        self.pre.cerrar()
        self.pg.quit()


# --------------------------------------------------------------------------
# Línea de órdenes
# --------------------------------------------------------------------------


def _argumentos(argv: list[str] | None) -> argparse.Namespace:
    env = os.environ
    p = argparse.ArgumentParser(
        description="La pantalla del cuento de Telmeow (P11), para la Raspberry Pi.")
    p.add_argument("--broker", default=env.get("TELMEOW_MQTT_HOST", "localhost"),
                   help="La maquina donde corre Mosquitto: IP o nombre, con :puerto "
                        "si no es el 1883. Por defecto TELMEOW_MQTT_HOST o localhost.")
    p.add_argument("--stories-dir", type=Path,
                   default=Path(env.get("TELMEOW_STORIES_DIR", Path.home() / "cuentos")),
                   help="Carpeta con los PDF, con los MISMOS nombres de archivo que en "
                        "la PC del narrador. Por defecto ~/cuentos.")
    p.add_argument("--display", type=int, default=0,
                   help="Numero del monitor (0 = el primero).")
    p.add_argument("--windowed", action="store_true",
                   help="En una ventana de 960x540 en vez de a pantalla completa.")
    p.add_argument("--prueba", nargs="?", const="", default=None, metavar="PDF",
                   help="Modo de prueba SIN MQTT: abre ese PDF (o el primero de la "
                        "carpeta) y las flechas pasan de pagina.")
    return p.parse_args(argv)


def _partir_broker(texto: str) -> tuple[str, int]:
    host, sep, puerto = texto.rpartition(":")
    if sep and puerto.isdigit() and host:
        return host, int(puerto)
    return texto, 1883


def main(argv: list[str] | None = None) -> int:
    args = _argumentos(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(message)s")
    guion = Guion()
    carpeta = args.stories_dir.expanduser()
    enlace = None

    if args.prueba is not None:
        if args.prueba:
            ruta = Path(args.prueba).expanduser()
            carpeta = ruta.parent
        else:
            candidatos = sorted(carpeta.glob("*.pdf")) if carpeta.is_dir() else []
            ruta = candidatos[0] if candidatos else None
        if ruta is None or not ruta.is_file():
            print(f"No encuentro el PDF de prueba ({args.prueba or carpeta}).", file=sys.stderr)
            return 2
        guion.al_cuento({"story": ruta.name, "page": 1})
        print(f"Modo de prueba: {ruta.name}. Flechas = pagina, Esc = salir.")
    else:
        if not carpeta.is_dir():
            registro.error("la carpeta de cuentos %s no existe; la pantalla se queda en "
                           "negro hasta que exista y tenga los PDF", carpeta)
        host, puerto = _partir_broker(args.broker)
        enlace = Enlace(guion, host, puerto)

    pantalla = Pantalla(guion, carpeta, ventana=args.windowed, monitor=args.display,
                        flechas=args.prueba is not None)

    def parar(*_):  # systemctl stop manda SIGTERM
        pantalla.seguir = False

    signal.signal(signal.SIGTERM, parar)
    if enlace is not None:
        enlace.conectar()
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
