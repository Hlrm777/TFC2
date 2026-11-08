"""La interfaz de narración (P10): el servidor que le da al visor el video, los
cuentos y las órdenes.

``python -m telmeow_teleop.control_panel [--record sesion.csv] [args de main]``

EL COMANDO DE LA SESIÓN. Comprueba el broker y lo levanta si falta, lanza el
pipeline (``main.py``) con la interfaz, y abre el navegador en el modo
narración del visor: el robot y la cámara del operador a la izquierda, el
libro a la derecha (decisiones del operador, 2026-11-08). La ventana de OpenCV
del P7 sigue abierta aparte, como diagnóstico, pero todas las teclas funcionan
también con el navegador delante.

``python -m telmeow_teleop.control_panel --demo``: el mismo servidor sin cámara,
sin MediaPipe y sin broker, con video sintético y un estado falso que obedece
las órdenes. Sirve para ver la página sin sentarse delante de la cámara.

POR QUÉ UN SERVIDOR DENTRO DE ``main.py``
-----------------------------------------
En Windows solo un programa puede abrir la cámara, y ese programa es
``main.py``: la página no puede abrirla por su cuenta. Así que ``main.py`` le
sirve todo en ``http://localhost:8001`` (solo desde la propia máquina):

==========================  ==================================================
``/``                       redirige al visor en modo narración
``/<archivo>``              la carpeta del visor (HTML, STL, PNG)
``GET /api/estado``         lo que la página enseña, ya en palabras
``POST /api/orden``         ``{"orden": nombre, "valor": ...}`` → la cola
``GET /api/historias``      los cuentos con su número de páginas
``GET /historias/<nombre>`` el PDF, solo si ``<nombre>`` está en la lista
``GET /video.mjpg``         la cámara, limpia y en espejo, como MJPEG
==========================  ==================================================

MJPEG es una sucesión de fotos JPEG por una sola conexión, que el navegador
enseña como un video con un simple ``<img>``. Siempre va la ÚLTIMA foto: si el
navegador se retrasa se salta fotos en vez de acumular retraso. Solo se
codifica cuando alguien mira, a 15 por segundo como máximo, y en un hilo
aparte (``EmisorVideo``), para no quitarle tiempo al bucle de visión.

Las órdenes no se ejecutan aquí: entran a una cola que el bucle vacía en cada
frame (``pendientes``) y ejecuta con el mismo código que las teclas. El estado
lo deja el bucle en cada frame cambiando una sola referencia
(``publicar_estado``): ningún hilo ve nada a medias.

Seguridad, lo justo para una máquina de laboratorio: escucha solo en
``127.0.0.1``; rechaza las peticiones cuyo ``Host`` no sea esta máquina (otra
página no puede usarlo por un truco de DNS), las órdenes que vengan de otro
origen y las que no sean JSON (así un formulario de otra web no puede mandar
un ``salir``); y solo sirve los PDF de la lista, nunca rutas.
"""

from __future__ import annotations

import argparse
import functools
import json
import math
import queue
import shutil
import socket
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .config import PROJECT_ROOT, AppConfig, MQTTConfig
from .historias import Biblioteca, Lector
from .ordenes import Orden, orden_desde_json

__all__ = [
    "PUERTO",
    "EmisorVideo",
    "ServidorInterfaz",
    "estado_interfaz",
    "ejecutar_demo",
    "main",
]

#: Puerto de la interfaz: distinto del 8000 del visor de siempre, para poder
#: tener los dos abiertos (lo pedía el enunciado del P10).
PUERTO = 8001

#: La página a la que lleva ``/``.
PAGINA_NARRACION = "/telmeow_viewer.html?modo=narracion"

CARPETA_VISOR = PROJECT_ROOT / "telmeow_viewer"

#: Lo más que se lee del cuerpo de una orden.
_MAX_CUERPO = 4096


# --------------------------------------------------------------------------
# El video
# --------------------------------------------------------------------------


def _codificador_jpeg(calidad: int):
    def codificar(frame) -> bytes:
        import cv2

        ok, datos = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, int(calidad)])
        if not ok:
            raise RuntimeError("cv2.imencode no pudo codificar el frame")
        return datos.tobytes()

    return codificar


class EmisorVideo:
    """La última imagen de la cámara, en JPEG, para quien la mire.

    El bucle de visión llama a ``entregar`` en cada frame, y eso solo guarda
    la referencia: no copia ni codifica. Un hilo aparte codifica la última
    imagen cuando hay alguien mirando y como mucho ``fps_max`` veces por
    segundo; cada conexión manda la última que haya, así que una conexión
    lenta se salta imágenes en vez de acumular retraso.

    El frame que se entrega no se puede modificar después: ``main.py`` le pasa
    el BGR volteado, que nadie dibuja (el panel dibuja sobre una copia).
    """

    def __init__(self, fps_max: float = 15.0, calidad: int = 70, *, codificar=None,
                 reloj=time.perf_counter, dormir=time.sleep) -> None:
        self.periodo = 1.0 / float(fps_max)
        self._codificar = codificar or _codificador_jpeg(calidad)
        self._reloj = reloj
        self._dormir = dormir
        self._cond = threading.Condition()
        self._frame = None
        self._n_frame = 0
        self._n_codificado = 0
        self._jpeg: bytes | None = None
        self._n_jpeg = 0
        self._clientes = 0
        self._parar = False
        self._hilo: threading.Thread | None = None
        #: Frames que no se pudieron codificar. El hilo no muere por uno.
        self.errores = 0

    @property
    def clientes(self) -> int:
        return self._clientes

    def entregar(self, frame) -> None:
        with self._cond:
            self._frame = frame
            self._n_frame += 1
            if self._clientes:
                self._cond.notify_all()

    def conectar(self) -> None:
        with self._cond:
            self._clientes += 1
            self._cond.notify_all()

    def desconectar(self) -> None:
        with self._cond:
            self._clientes = max(0, self._clientes - 1)

    def siguiente(self, visto: int, timeout: float = 1.0) -> tuple[int, bytes] | None:
        """La imagen más nueva que ``visto``, esperando hasta ``timeout``."""
        with self._cond:
            self._cond.wait_for(lambda: self._n_jpeg > visto or self._parar, timeout)
            if self._n_jpeg > visto and self._jpeg is not None:
                return self._n_jpeg, self._jpeg
            return None

    def paso(self) -> bool:
        """Codifica la última imagen si hay alguien mirando y es nueva. Devuelve
        si codificó. Es lo que hace el hilo en cada vuelta; aparte, para las
        pruebas."""
        with self._cond:
            if not self._clientes or self._frame is None or self._n_frame == self._n_codificado:
                return False
            frame, n = self._frame, self._n_frame
        try:
            jpeg = self._codificar(frame)
        except Exception:  # noqa: BLE001 -- ver self.errores
            self.errores += 1
            with self._cond:
                self._n_codificado = n
            return False
        with self._cond:
            self._jpeg = jpeg
            self._n_jpeg += 1
            self._n_codificado = n
            self._cond.notify_all()
        return True

    def _bucle(self) -> None:
        ultimo = -math.inf
        while True:
            with self._cond:
                self._cond.wait_for(
                    lambda: self._parar or (self._clientes > 0 and self._n_frame != self._n_codificado),
                    0.5)
                if self._parar:
                    return
            espera = ultimo + self.periodo - self._reloj()
            if espera > 0:
                self._dormir(espera)
            if self.paso():
                ultimo = self._reloj()

    def arrancar(self) -> None:
        self._parar = False
        self._hilo = threading.Thread(target=self._bucle, name="video", daemon=True)
        self._hilo.start()

    def parar(self) -> None:
        with self._cond:
            self._parar = True
            self._cond.notify_all()
        if self._hilo is not None:
            self._hilo.join(timeout=2.0)
            self._hilo = None


# --------------------------------------------------------------------------
# Lo que la página enseña
# --------------------------------------------------------------------------


def estado_interfaz(*, enlace: bool, fallo_reciente: bool = False, pausa_tecla: bool = False,
                    pausa_luz: bool = False, pausa_cara: bool = False,
                    emocion_activa: bool = True, expresion: str | None = None,
                    manual: str | None = None, calibrando: bool = False,
                    lector: Lector | None = None, grabando: bool = False, etiqueta: int = 0,
                    aviso: str = "", ws_port: int = MQTTConfig.ws_port,
                    topic_pose: str = MQTTConfig.topic_pose, demo: bool = False) -> dict:
    """El estado que pinta la página, YA EN PALABRAS y sin cifras de
    diagnóstico (decisión del operador, 2026-11-02): las cifras siguen en la
    ventana de OpenCV y en ``tools/``.

    Lo único que se pone en rojo además de la falta de enlace es que se
    pierdan mensajes (``fallo_reciente``): el v2 del P10 exige que se vea
    (P7, punto 9), porque sin eso todo puede decir «conectado» mientras al
    broker no llega nada.
    """
    if not enlace:
        robot = ("sin_enlace", "Sin enlace con el robot")
    elif fallo_reciente:
        robot = ("fallos", "Se están perdiendo mensajes")
    elif pausa_luz:
        robot = ("pausa", "Pausa: poca luz")
    elif pausa_cara:
        robot = ("pausa", "Pausa: no te veo")
    elif pausa_tecla:
        robot = ("pausa", "En pausa (k)")
    else:
        robot = ("ok", "Robot conectado")
    if not emocion_activa:
        texto_expresion = "Expresión: sin reconocimiento"
    elif calibrando:
        texto_expresion = "Calibrando: cara relajada, mirando la pantalla"
    elif manual:
        texto_expresion = f"Expresión fijada: {manual}"
    else:
        texto_expresion = f"Expresión automática: {expresion or 'neutral'}"
    abierto = lector is not None and lector.abierto
    return {
        "listo": True,
        "demo": demo,
        "robot": {"clave": robot[0], "texto": robot[1]},
        "pausa": pausa_tecla or pausa_luz or pausa_cara,
        # La de la tecla aparte: el botón dice «Reanudar» solo si la quita él.
        "pausa_tecla": pausa_tecla,
        "expresion": {"texto": texto_expresion, "actual": expresion, "manual": manual,
                      "calibrando": calibrando, "activa": emocion_activa},
        "cuento": {
            "nombre": lector.historia.nombre if abierto else None,
            "titulo": lector.historia.titulo if abierto else None,
            "pagina": lector.pagina if abierto else None,
            "total": lector.total if abierto else None,
        },
        "grabacion": {"activa": grabando, "etiqueta": etiqueta,
                      "texto": f"Grabando · etiqueta {etiqueta}" if grabando else ""},
        "aviso": aviso,
        "mqtt": {"ws_port": ws_port, "topic_pose": topic_pose},
    }


# --------------------------------------------------------------------------
# El servidor
# --------------------------------------------------------------------------


class _Manejador(SimpleHTTPRequestHandler):
    """Las rutas de la interfaz; lo demás, la carpeta del visor."""

    server_version = "TelmeowInterfaz/1.0"

    @property
    def interfaz(self) -> "ServidorInterfaz":
        return self.server.interfaz

    def log_message(self, formato, *args):  # noqa: D401 -- sin ruido en la consola
        pass

    def _host_valido(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host in self.interfaz.hosts_validos

    def _json(self, codigo: int, datos) -> None:
        cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):  # noqa: N802 -- nombre que impone http.server
        if not self._host_valido():
            self._json(403, {"ok": False, "error": "host no permitido"})
            return
        ruta = urlsplit(self.path).path
        if ruta == "/":
            self.send_response(302)
            self.send_header("Location", PAGINA_NARRACION)
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif ruta == "/api/estado":
            self._json(200, self.interfaz.estado)
        elif ruta == "/api/historias":
            biblioteca = self.interfaz.biblioteca
            self._json(200, {"carpeta": str(biblioteca.carpeta), "existe": biblioteca.existe,
                             "historias": [h.como_dict() for h in biblioteca.listar()]})
        elif ruta.startswith("/historias/"):
            self._pdf(unquote(ruta[len("/historias/"):]))
        elif ruta == "/video.mjpg":
            self._video()
        else:
            super().do_GET()

    def do_HEAD(self):  # noqa: N802
        if not self._host_valido():
            self._json(403, {"ok": False, "error": "host no permitido"})
            return
        super().do_HEAD()

    def do_POST(self):  # noqa: N802
        if not self._host_valido():
            self._json(403, {"ok": False, "error": "host no permitido"})
            return
        if urlsplit(self.path).path != "/api/orden":
            self._json(404, {"ok": False, "error": "no existe"})
            return
        origen = self.headers.get("Origin")
        if origen is not None and origen.lower() not in self.interfaz.origenes_validos:
            self._json(403, {"ok": False, "error": "origen no permitido"})
            return
        if not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
            self._json(415, {"ok": False, "error": "se esperaba application/json"})
            return
        try:
            largo = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            largo = -1
        if not 0 < largo <= _MAX_CUERPO:
            self._json(400, {"ok": False, "error": "cuerpo vacio o demasiado largo"})
            return
        try:
            orden = orden_desde_json(json.loads(self.rfile.read(largo).decode("utf-8")))
        except (ValueError, UnicodeDecodeError) as exc:
            self._json(400, {"ok": False, "error": str(exc)})
            return
        if orden.nombre == "historia" and self.interfaz.biblioteca.buscar(orden.valor) is None:
            self._json(404, {"ok": False, "error": f"cuento no encontrado: {orden.valor}"})
            return
        self.interfaz.ordenes.put(orden)
        self._json(202, {"ok": True})

    def _pdf(self, nombre: str) -> None:
        historia = self.interfaz.biblioteca.buscar(nombre)
        if historia is None or not historia.ruta.is_file():
            self._json(404, {"ok": False, "error": "cuento no encontrado"})
            return
        tamano = historia.ruta.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(tamano))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with open(historia.ruta, "rb") as archivo:
            shutil.copyfileobj(archivo, self.wfile)

    def _video(self) -> None:
        emisor = self.interfaz.emisor
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        emisor.conectar()
        try:
            visto = 0
            while not self.interfaz.parando:
                nuevo = emisor.siguiente(visto, timeout=1.0)
                if nuevo is None:
                    continue
                visto, jpeg = nuevo
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                 b"Content-Length: %d\r\n\r\n" % len(jpeg))
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
        except OSError:
            pass                      # el navegador cerró la conexión
        finally:
            emisor.desconectar()


class _ServidorHTTP(ThreadingHTTPServer):
    """``ThreadingHTTPServer`` que NO comparte el puerto.

    ``http.server`` activa ``SO_REUSEADDR``, y en Windows eso deja abrir dos
    servidores en el mismo puerto sin ningún error (lo destapó una prueba del
    P10): una segunda sesión compartiría el 8001 con la primera en silencio, y
    cada petición iría a una u otra. Aquí el segundo falla al abrir.
    """

    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):          # solo existe en Windows
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class ServidorInterfaz:
    """El servidor de la interfaz: lo arranca ``main.py`` con ``--interfaz``.

    ``puerto=0`` elige uno libre (las pruebas); ``puerto`` dice cuál quedó.
    Lanza ``OSError`` si el puerto está ocupado.
    """

    def __init__(self, carpeta_visor: Path, biblioteca: Biblioteca, *,
                 host: str = "127.0.0.1", puerto: int = PUERTO,
                 emisor: EmisorVideo | None = None) -> None:
        self.carpeta_visor = Path(carpeta_visor)
        self.biblioteca = biblioteca
        self.emisor = emisor or EmisorVideo()
        self.ordenes: queue.SimpleQueue[Orden] = queue.SimpleQueue()
        #: Lo que devuelve ``/api/estado``. Se sustituye entero, nunca se edita.
        self.estado: dict = {"listo": False}
        self.parando = False
        manejador = functools.partial(_Manejador, directory=str(self.carpeta_visor))
        self._httpd = _ServidorHTTP((host, puerto), manejador)
        self._httpd.interfaz = self
        self._hilo: threading.Thread | None = None
        real = self.puerto
        self.hosts_validos = {f"{nombre}:{real}" for nombre in ("localhost", "127.0.0.1")}
        self.origenes_validos = {f"http://{h}" for h in self.hosts_validos}

    @property
    def puerto(self) -> int:
        return self._httpd.server_address[1]

    @property
    def url(self) -> str:
        return f"http://localhost:{self.puerto}/"

    def arrancar(self) -> None:
        self.emisor.arrancar()
        self._hilo = threading.Thread(target=self._httpd.serve_forever, name="interfaz",
                                      daemon=True)
        self._hilo.start()

    def parar(self) -> None:
        self.parando = True
        if self._hilo is not None:
            self._httpd.shutdown()
            self._hilo.join(timeout=2.0)
            self._hilo = None
        self._httpd.server_close()
        self.emisor.parar()

    def pendientes(self) -> list[Orden]:
        """Las órdenes que llegaron desde la última vez. Las vacía el bucle."""
        ordenes = []
        while True:
            try:
                ordenes.append(self.ordenes.get_nowait())
            except queue.Empty:
                return ordenes

    def publicar_estado(self, estado: dict) -> None:
        self.estado = estado


# --------------------------------------------------------------------------
# El modo demo: la página sin cámara
# --------------------------------------------------------------------------


def _frame_demo(t: float, ancho: int = 640, alto: int = 480):
    import cv2
    import numpy as np

    y, x = np.mgrid[0:alto, 0:ancho]
    frame = np.zeros((alto, ancho, 3), dtype=np.uint8)
    frame[..., 0] = (90 + 60 * np.sin(x / 90.0 + t)).astype(np.uint8)
    frame[..., 1] = (70 + 40 * np.cos(y / 70.0 - t / 2)).astype(np.uint8)
    frame[..., 2] = 60
    centro = (int(ancho / 2 + 150 * math.sin(t)), int(alto / 2 + 80 * math.cos(1.3 * t)))
    cv2.circle(frame, centro, 40, (230, 220, 90), -1)
    cv2.putText(frame, "DEMO - sin camara", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
                (255, 255, 255), 2, cv2.LINE_AA)
    return frame


class _PipelineDemo:
    """Hace de ``main.py`` para la página: obedece las órdenes y publica el
    estado, sin cámara ni MQTT."""

    def __init__(self, servidor: ServidorInterfaz) -> None:
        self.servidor = servidor
        self.lector = Lector()
        self.pausa = False
        self.manual: str | None = None
        self.etiqueta = 0
        self.calibrando_hasta = 0.0
        self.aviso = ""
        self.aviso_hasta = 0.0
        self.salir = False

    def _avisar(self, texto: str, ahora: float) -> None:
        self.aviso, self.aviso_hasta = texto, ahora + 2.0

    def ejecutar(self, orden: Orden, ahora: float) -> None:
        nombre = orden.nombre
        print(f"orden: {orden.para_registro()}")
        if nombre == "salir":
            self.salir = True
        elif nombre == "etiqueta":
            self.etiqueta += 1
        elif nombre == "calibrar":
            self.calibrando_hasta = ahora + 3.0
        elif nombre == "expresion":
            self.manual = None if self.manual == orden.valor else orden.valor
        elif nombre == "automatico":
            self.manual = None
        elif nombre == "pausa":
            self.pausa = not self.pausa
        elif nombre == "historia":
            historia = self.servidor.biblioteca.buscar(orden.valor)
            if historia is not None and historia.legible:
                self.lector.elegir(historia)
            else:
                self._avisar(f"cuento no disponible: {orden.valor}", ahora)
        elif nombre in ("pagina_anterior", "pagina_siguiente"):
            destino = self.lector.destino(-1 if nombre == "pagina_anterior" else +1)
            if not self.lector.abierto:
                self._avisar("elige un cuento", ahora)
            elif self.pausa:
                self._avisar("en pausa: no se puede pasar de pagina", ahora)
            elif destino is None:
                self._avisar("es la ultima pagina" if nombre == "pagina_siguiente"
                             else "es la primera pagina", ahora)
            else:
                self.lector.ir(destino)

    def paso(self, ahora: float) -> None:
        for orden in self.servidor.pendientes():
            self.ejecutar(orden, ahora)
        if ahora > self.aviso_hasta:
            self.aviso = ""
        self.servidor.emisor.entregar(_frame_demo(ahora))
        self.servidor.publicar_estado(estado_interfaz(
            enlace=True, pausa_tecla=self.pausa, expresion=self.manual or "neutral",
            manual=self.manual, calibrando=ahora < self.calibrando_hasta, lector=self.lector,
            grabando=True, etiqueta=self.etiqueta, aviso=self.aviso, demo=True))


def ejecutar_demo(puerto: int = PUERTO, carpeta: Path | None = None, abrir: bool = True,
                  duracion_s: float | None = None) -> int:
    """El servidor con video sintético y un estado falso. Ctrl-C (o la orden
    salir) termina."""
    carpeta = carpeta or AppConfig.from_env().paths.stories_dir
    servidor = ServidorInterfaz(CARPETA_VISOR, Biblioteca(carpeta), puerto=puerto)
    demo = _PipelineDemo(servidor)
    servidor.arrancar()
    print(f"DEMO de la interfaz en {servidor.url} (cuentos de {carpeta}). Ctrl-C para salir.")
    if abrir:
        import webbrowser

        webbrowser.open(servidor.url)
    inicio = time.perf_counter()
    try:
        while not demo.salir:
            ahora = time.perf_counter()
            if duracion_s is not None and ahora - inicio > duracion_s:
                break
            demo.paso(ahora)
            time.sleep(1.0 / 30.0)
    except KeyboardInterrupt:
        print("\nInterrumpido.")
    finally:
        servidor.parar()
    return 0


# --------------------------------------------------------------------------
# El comando de la sesión
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m telmeow_teleop.control_panel",
        description="La sesión de narración: broker, pipeline con la interfaz y el navegador. "
                    "Los demás argumentos van a telmeow_teleop.main (--record, --camera...).")
    parser.add_argument("--demo", action="store_true",
                        help="la interfaz sin camara ni broker, con video sintetico")
    parser.add_argument("--puerto", type=int, default=PUERTO,
                        help=f"puerto de la interfaz (por defecto {PUERTO})")
    parser.add_argument("--sin-navegador", action="store_true",
                        help="no abrir el navegador")
    parser.add_argument("--stories-dir", type=Path, default=None,
                        help="carpeta de los cuentos en PDF")
    args, resto = parser.parse_known_args(argv)
    if args.demo:
        return ejecutar_demo(args.puerto, args.stories_dir, abrir=not args.sin_navegador)

    from .broker import asegurar_broker

    arrancados: list = []
    try:
        if not asegurar_broker(arrancados):
            return 1
        from .main import main as pipeline

        argumentos = ["--interfaz", str(args.puerto)]
        if not args.sin_navegador:
            argumentos.append("--abrir-navegador")
        if args.stories_dir is not None:
            argumentos += ["--stories-dir", str(args.stories_dir)]
        return pipeline(argumentos + resto)
    finally:
        for proceso in arrancados:
            proceso.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
