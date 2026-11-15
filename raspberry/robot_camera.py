"""La cámara de la frente del robot: corre EN LA RASPBERRY PI.

QUÉ HACE
--------
Toma la imagen de la cámara del robot (la que ve a los niños) y la sirve por
HTTP para que la interfaz del narrador la enseñe en el panel «Lo que ve el
robot». Es el mismo mecanismo con que la PC enseña la cámara del narrador
(``telmeow_teleop/control_panel.py``): MJPEG, fotos JPEG seguidas por una sola
conexión, que el navegador pinta en un ``<img>`` sin nada más.

    GET /video.mjpg   el video (siempre la foto MÁS RECIENTE: no acumula retraso)
    GET /foto.jpg     una sola foto
    GET /estado       {"cuadros": N, "edad_ms": X, "fuente": "..."} en JSON

La interfaz consulta ``/estado`` cada segundo: si la última foto tiene más de
2 s, avisa «sin señal» en vez de dejar una imagen congelada que parezca en vivo.

DE DÓNDE SALE LA IMAGEN (``--fuente``)
--------------------------------------
- ``picamera2``  la cámara de la Raspberry por el conector CSI (necesita
  ``sudo apt install python3-picamera2``). **Escrita contra la documentación,
  sin cámara delante: no está probada.**
- un número      una cámara USB por OpenCV (``0`` la primera).
- un archivo     un video grabado, en bucle: hace de cámara en las pruebas.
- ``prueba``     una imagen fabricada que se mueve, con la hora: sin cámara.

Si la cámara falla o se desconecta, se reintenta sola cada 2 s; el programa no
se cierra.

PRIVACIDAD
----------
La imagen es de niños. Este programa NO graba nada: solo retransmite la foto
más reciente, por la red local y sin contraseña. Quien pueda entrar en esa red
puede verla; la red de la sesión tiene que ser de confianza. Hay que declararlo.

USO
---
    python robot_camera.py --fuente picamera2
    python robot_camera.py --fuente prueba --puerto 8002
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

__all__ = ["PUERTO", "Ultima", "FuentePrueba", "FuenteOpenCV", "FuentePicamera2",
           "abrir_fuente", "Captura", "ServidorCamara", "main"]

PUERTO = 8002
#: Con una foto más vieja que esto, ``/video.mjpg`` deja de repetirla.
SIN_SENAL_S = 2.0
REINTENTO_S = 2.0

registro = logging.getLogger("camara")


class Ultima:
    """La foto más reciente, ya en JPEG, compartida por todos los que miran."""

    def __init__(self, reloj: Callable[[], float] = time.monotonic) -> None:
        self._cond = threading.Condition()
        self._reloj = reloj
        self.jpeg: bytes | None = None
        self.cuadros = 0
        self._cuando = 0.0
        self.captura_ms = 0

    def poner(self, jpeg: bytes) -> None:
        with self._cond:
            self.jpeg = jpeg
            self.cuadros += 1
            self._cuando = self._reloj()
            self.captura_ms = int(time.time() * 1000)
            self._cond.notify_all()

    def edad(self) -> float | None:
        """Segundos desde la última foto; ``None`` si aún no hubo ninguna."""
        with self._cond:
            return None if self.jpeg is None else self._reloj() - self._cuando

    def esperar_nueva(self, vista: int, espera: float = 1.0) -> tuple[int, bytes | None, int]:
        """Espera una foto posterior a la número ``vista``. Devuelve
        ``(número, jpeg, hora de captura en ms)``; el jpeg es ``None`` si no
        llegó ninguna nueva en ``espera`` segundos."""
        with self._cond:
            if self.cuadros == vista:
                self._cond.wait(espera)
            if self.cuadros == vista:
                return vista, None, 0
            return self.cuadros, self.jpeg, self.captura_ms


# --------------------------------------------------------------------------
# Las fuentes: cada una da fotos (matrices BGR de OpenCV) con ``leer()``
# --------------------------------------------------------------------------


class FuentePrueba:
    """Una imagen fabricada: una barra que se mueve y la hora. Sin cámara."""

    nombre = "prueba"

    def __init__(self, ancho: int, alto: int, fps: float) -> None:
        self.ancho, self.alto, self.periodo = ancho, alto, 1.0 / fps
        self._n = 0
        self._siguiente = time.perf_counter()

    def leer(self):
        import cv2
        import numpy as np

        time.sleep(max(0.0, self._siguiente - time.perf_counter()))
        self._siguiente = max(self._siguiente + self.periodo, time.perf_counter())
        imagen = np.full((self.alto, self.ancho, 3), 40, np.uint8)
        x = (self._n * 8) % self.ancho
        imagen[:, x:x + 12] = (90, 200, 250)
        cv2.putText(imagen, "camara de prueba", (12, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                    (255, 255, 255), 2)
        cv2.putText(imagen, time.strftime("%H:%M:%S") + f"  #{self._n}", (12, self.alto - 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        self._n += 1
        return imagen

    def cerrar(self) -> None:
        pass


class FuenteOpenCV:
    """Una cámara USB (un número) o un video grabado (una ruta), por OpenCV.
    El video se repite en bucle y se lee a su propio ritmo."""

    def __init__(self, origen: str, ancho: int, alto: int, fps: float) -> None:
        import cv2

        self._cv2 = cv2
        self.es_archivo = not origen.isdigit()
        self.nombre = f"video {origen}" if self.es_archivo else f"camara {origen}"
        self._cap = cv2.VideoCapture(origen if self.es_archivo else int(origen))
        if not self._cap.isOpened():
            raise OSError(f"no puedo abrir {self.nombre}")
        if self.es_archivo:
            self.periodo = 1.0 / (self._cap.get(cv2.CAP_PROP_FPS) or fps)
            self._siguiente = time.perf_counter()
        else:
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, ancho)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, alto)
            self._cap.set(cv2.CAP_PROP_FPS, fps)

    def leer(self):
        if self.es_archivo:
            time.sleep(max(0.0, self._siguiente - time.perf_counter()))
            self._siguiente = max(self._siguiente + self.periodo, time.perf_counter())
        bien, imagen = self._cap.read()
        if not bien and self.es_archivo:      # fin del video: desde el principio
            self._cap.set(self._cv2.CAP_PROP_POS_FRAMES, 0)
            bien, imagen = self._cap.read()
        if not bien:
            raise OSError(f"{self.nombre} no da imagen")
        return imagen

    def cerrar(self) -> None:
        self._cap.release()


class FuentePicamera2:
    """La cámara CSI de la Raspberry, con picamera2. SIN PROBAR (no hay cámara)."""

    nombre = "picamera2"

    def __init__(self, ancho: int, alto: int, fps: float) -> None:
        from picamera2 import Picamera2

        self._cam = Picamera2()
        # "RGB888" en picamera2 entrega los bytes en orden B, G, R: el de OpenCV.
        self._cam.configure(self._cam.create_video_configuration(
            main={"size": (ancho, alto), "format": "RGB888"},
            controls={"FrameRate": fps}))
        self._cam.start()

    def leer(self):
        return self._cam.capture_array()

    def cerrar(self) -> None:
        self._cam.stop()
        self._cam.close()


def abrir_fuente(nombre: str, ancho: int, alto: int, fps: float):
    if nombre == "prueba":
        return FuentePrueba(ancho, alto, fps)
    if nombre == "picamera2":
        return FuentePicamera2(ancho, alto, fps)
    return FuenteOpenCV(nombre, ancho, alto, fps)


def _a_jpeg(imagen, calidad: int) -> bytes:
    import cv2

    bien, datos = cv2.imencode(".jpg", imagen, [cv2.IMWRITE_JPEG_QUALITY, calidad])
    if not bien:
        raise ValueError("no se pudo comprimir la foto")
    return datos.tobytes()


class Captura:
    """El hilo que lee la fuente y deja cada foto, ya en JPEG, en ``Ultima``.

    Si abrir o leer falla, cierra la fuente y lo reintenta cada ``reintento``
    segundos: una cámara desconectada no cierra el programa.
    """

    def __init__(self, ultima: Ultima, abrir: Callable[[], Any], *,
                 a_jpeg: Callable[[Any], bytes] = lambda imagen: _a_jpeg(imagen, 80),
                 reintento: float = REINTENTO_S) -> None:
        self.ultima, self._abrir, self._a_jpeg = ultima, abrir, a_jpeg
        self.reintento = reintento
        self.fallos = 0
        self.fuente = ""
        self._parar = threading.Event()
        self._hilo = threading.Thread(target=self._trabajar, name="captura", daemon=True)

    def empezar(self) -> "Captura":
        self._hilo.start()
        return self

    def cerrar(self) -> None:
        self._parar.set()
        self._hilo.join(timeout=3.0)

    def _trabajar(self) -> None:
        while not self._parar.is_set():
            fuente = None
            try:
                fuente = self._abrir()
                self.fuente = getattr(fuente, "nombre", "")
                registro.info("camara abierta: %s", self.fuente)
                while not self._parar.is_set():
                    self.ultima.poner(self._a_jpeg(fuente.leer()))
            except Exception as exc:  # noqa: BLE001 -- reintentar, nunca cerrar
                self.fallos += 1
                registro.error("la camara fallo (%s); reintento en %.0f s", exc, self.reintento)
            finally:
                if fuente is not None:
                    try:
                        fuente.cerrar()
                    except Exception as exc:  # noqa: BLE001
                        registro.warning("al cerrar la camara: %s", exc)
            self._parar.wait(self.reintento)


# --------------------------------------------------------------------------
# El servidor
# --------------------------------------------------------------------------


class _Manejador(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    ultima: Ultima
    captura: Captura | None = None
    parar: threading.Event

    def log_message(self, formato, *args):  # silencio: una línea por foto no sirve
        pass

    def _cabeceras(self, tipo: str, largo: int | None = None) -> None:
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        if largo is not None:
            self.send_header("Content-Length", str(largo))
        self.send_header("Cache-Control", "no-store")
        # La página del narrador viene de otra máquina (su PC): se le deja leer.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def do_GET(self):  # noqa: N802 -- nombre que impone http.server
        ruta = self.path.split("?", 1)[0]
        try:
            if ruta == "/estado":
                edad = self.ultima.edad()
                cuerpo = json.dumps({
                    "cuadros": self.ultima.cuadros,
                    "edad_ms": None if edad is None else round(edad * 1000),
                    "fuente": self.captura.fuente if self.captura else "",
                    "fallos": self.captura.fallos if self.captura else 0,
                }).encode()
                self._cabeceras("application/json", len(cuerpo))
                self.wfile.write(cuerpo)
            elif ruta == "/foto.jpg":
                jpeg = self.ultima.jpeg
                if jpeg is None:
                    self.send_error(503, "todavia no hay imagen")
                    return
                self._cabeceras("image/jpeg", len(jpeg))
                self.wfile.write(jpeg)
            elif ruta == "/video.mjpg":
                self._cabeceras("multipart/x-mixed-replace; boundary=frame")
                vista = 0
                while not self.parar.is_set():
                    vista, jpeg, captura_ms = self.ultima.esperar_nueva(vista)
                    if jpeg is None:
                        continue
                    self.wfile.write(
                        b"--frame\r\nContent-Type: image/jpeg\r\n"
                        + f"Content-Length: {len(jpeg)}\r\nX-Captura-Ms: {captura_ms}\r\n\r\n"
                        .encode() + jpeg + b"\r\n")
            else:
                self.send_error(404)
        except (BrokenPipeError, ConnectionError, TimeoutError):
            pass          # el que miraba se fue


class ServidorCamara:
    """El servidor HTTP, en su hilo. ``puerto=0`` elige uno libre (pruebas)."""

    def __init__(self, ultima: Ultima, captura: Captura | None = None, *,
                 host: str = "0.0.0.0", puerto: int = PUERTO) -> None:
        self._parar = threading.Event()
        manejador = type("Manejador", (_Manejador,),
                         {"ultima": ultima, "captura": captura, "parar": self._parar})
        self._servidor = ThreadingHTTPServer((host, puerto), manejador)
        self._servidor.daemon_threads = True
        self.puerto = self._servidor.server_address[1]
        self._hilo = threading.Thread(target=self._servidor.serve_forever, name="http",
                                      daemon=True)

    def empezar(self) -> "ServidorCamara":
        self._hilo.start()
        return self

    def cerrar(self) -> None:
        self._parar.set()
        self._servidor.shutdown()
        self._servidor.server_close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="La camara de la frente de Telmeow, por HTTP.")
    p.add_argument("--fuente", default="picamera2",
                   help="picamera2, un numero de camara USB, un archivo de video o 'prueba'.")
    p.add_argument("--puerto", type=int, default=PUERTO)
    p.add_argument("--ancho", type=int, default=640)
    p.add_argument("--alto", type=int, default=480)
    p.add_argument("--fps", type=float, default=15.0)
    p.add_argument("--calidad", type=int, default=80, help="Calidad del JPEG, 1-100.")
    p.add_argument("--solo-local", action="store_true",
                   help="Atiende solo a esta misma maquina (para pruebas).")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(message)s")

    ultima = Ultima()
    captura = Captura(ultima, lambda: abrir_fuente(args.fuente, args.ancho, args.alto, args.fps),
                      a_jpeg=lambda imagen: _a_jpeg(imagen, args.calidad)).empezar()
    servidor = ServidorCamara(ultima, captura, puerto=args.puerto,
                              host="127.0.0.1" if args.solo_local else "0.0.0.0").empezar()
    registro.info("sirviendo en el puerto %d: /video.mjpg, /foto.jpg, /estado", servidor.puerto)
    fin = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: fin.set())
    try:
        while not fin.wait(0.5):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        servidor.cerrar()
        captura.cerrar()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
