"""Pipeline de teleoperación integrado (PROMPT 7).

``python -m telmeow_teleop.main [--record sesion.csv] [--no-emotion] [--no-arms]``

Los scripts legacy abrían cada uno su ``cv2.VideoCapture(0)``, y en Windows dos
procesos no pueden abrir la misma cámara: cabeza, brazos y emoción nunca
pudieron correr a la vez. Aquí hay UNA captura y UN bucle que alimenta a los
tres estimadores, y UN mensaje MQTT con todo:

    cámara ─► flip ─► BGR ──────────────────────────────► EmotionClassifier (5 Hz)
                       └─► RGB (una vez) ─► HeadTracker          │      ▲
                                        └─► ArmTracker ──────────┼──────┘ manos
                                                 │               │
                              instantánea (pan, tilt, arm*, emo, t)
                                                 │
                                   PublicadorPeriodico (20 Hz, su hilo) ─► MQTT

Tres cadencias, cada una con su reloj (punto 2 del P7):

- **Visión**: cada frame, al ritmo de la cámara.
- **Emoción**: 5 Hz, por la fecha límite que ya lleva ``EmotionClassifier.process``.
  Va en el mismo bucle a propósito (decisión del operador, 2026-10-26): se mide
  primero lo que cuesta y solo si la medida lo pide pasa a un hilo (P7.1).
- **Publicación**: 20 Hz, en un hilo mínimo que no calcula nada. Hace falta
  porque el publisher descarta lo que llega a menos de 45 ms del anterior: si el
  bucle publicara en cada frame, a 30 fps pasaría uno sí y otro no y saldrían
  15 Hz, no 20 (H54).

Lo que se publica es el COMANDO: la cabeza sin rostro omite ``pan``/``tilt`` y el
visor conserva el último valor (punto 12); los brazos nunca tienen huecos
(``ArmTracker.comando``).

Teclas: ``q`` salir · ``c`` calibrar cabeza y emoción a la vez · ``1..6``
forzar expresión (orden de los botones del visor) · ``0`` automático · ``k``
pausar la publicación · ``j`` / ``l`` gesto de página anterior / siguiente ·
ESPACIO cambiar de etiqueta (con ``--record``).

No hay tecla ``m``: el enunciado pedía alternar el espejo, pero voltear la
imagen en caliente invertiría el pan sin aviso (el head tracker asume la imagen
volteada) y el convenio de imitación ya es configuración (``ArmConfig.mirror``,
``HeadConfig.invert_pan``). Lo decidió el operador el 2026-10-26.
"""

from __future__ import annotations

import argparse
import csv
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path

import cv2
import numpy as np

from .arm_tracker import CANALES, LANDMARKS, PUNTOS_MANO, ArmAngles, ArmTracker, dibujar_barra
from .arm_tracker import COLUMNAS_CSV as COLUMNAS_BRAZOS
from .arm_tracker import fila_csv as fila_brazos
from .camera import abrir_camara, brillo_medio, describir
from .config import AppConfig, add_cli_args, from_args
from .emotion_classifier import COLUMNAS_CSV as COLUMNAS_EMOCION
from .emotion_classifier import (
    TECLAS_MANUAL,
    EmotionClassifier,
    crear_backend_o_reserva,
    registrar_inferencia,
)
from .head_tracker import COLUMNAS_CSV as COLUMNAS_CABEZA
from .head_tracker import HeadTracker, apartar_si_existe
from .head_tracker import fila_csv as fila_cabeza
from .limits import Side

__all__ = [
    "PublicadorPeriodico",
    "Grabacion",
    "EstadoPanel",
    "manos_de_blazepose",
    "rutas_de_grabacion",
    "dibujar_panel",
    "COLUMNAS_SESION",
    "ANCHO_PANEL",
    "main",
]

#: Ancho del panel que va a la derecha del video. Con el texto al lado y no
#: encima, nada tapa la cara ni los brazos del operador (decisión del
#: 2026-10-26).
ANCHO_PANEL = 320

#: Por debajo de este brillo medio no hay imagen (H34), igual que en los demos.
BRILLO_MINIMO = 8.0

VENTANA = "Telmeow - teleoperacion"

#: Índices de BlazePose de las dos manos: muñeca, meñique, índice y pulgar
#: (15-22). Son los que se pasan a la regla de «mano sobre la cara» (punto 18).
INDICES_MANOS: tuple[int, ...] = tuple(
    LANDMARKS[lado][nombre] for lado in (Side.L, Side.R) for nombre in PUNTOS_MANO
)

#: Columnas de ``sesion.csv``: una fila por frame, con lo publicado y lo que
#: costó. Las de cabeza, brazos y emoción van en sus propios CSV, con las mismas
#: columnas que los demos, para que las herramientas de ``tools/`` las lean sin
#: cambios.
COLUMNAS_SESION = (
    "t", "t_captura_ms", "etiqueta", "dt_ms", "duplicado",
    "ms_cabeza", "ms_brazos", "ms_emocion", "ms_bucle",
    "pan", "tilt", "armL", "armLtw", "armR", "armRtw", "emo", "gesto", "pausa",
    "mqtt_enviados", "mqtt_descartados", "mqtt_fallidos", "mqtt_hz",
    "brillo", "ancho", "alto",
)

TECLA_SALIR = ord("q")
TECLA_CALIBRAR = ord("c")
TECLA_AUTOMATICO = ord("0")
TECLA_PAUSA = ord("k")
TECLA_PAGINA_ANTERIOR = ord("j")
TECLA_PAGINA_SIGUIENTE = ord("l")
TECLA_ETIQUETA = ord(" ")


# --------------------------------------------------------------------------
# Publicación a su propio ritmo
# --------------------------------------------------------------------------


class PublicadorPeriodico:
    """Envía la última pose cada ``periodo`` segundos, en su propio hilo.

    El bucle de visión deja la pose con ``actualizar`` y sigue; este hilo la
    manda a la tasa del contrato, la vaya dando la cámara deprisa o despacio. No
    calcula nada, así que no le roba tiempo al bucle.

    Cadencia por fecha límite, saltando las ranuras perdidas en vez de
    dispararlas seguidas (punto 10): tras una pausa del sistema, una ráfaga de
    mensajes atrasados la descartaría el limitador del publisher y la tasa
    efectiva bajaría sin motivo real.

    Los gestos de página se encolan y salen en el siguiente tick con la pose
    vigente; el publisher nunca descarta un mensaje con ``gesture``. En pausa no
    sale nada, tampoco un gesto.
    """

    def __init__(self, publicador, periodo: float, *, reloj=time.perf_counter,
                 dormir=time.sleep) -> None:
        self.publicador = publicador
        self.periodo = float(periodo)
        self._reloj = reloj
        self._dormir = dormir
        self._lock = threading.Lock()
        self._pose: dict = {}
        self._gestos: deque[str] = deque()
        self._pausado = False
        self._parar = threading.Event()
        self._hilo: threading.Thread | None = None
        #: Envíos que lanzaron una excepción. El hilo no muere por uno: un hilo
        #: de publicación muerto en silencio dejaría al robot quieto con el
        #: resto del pipeline diciendo que todo va bien. El panel lo enseña.
        self.errores = 0
        self.ultimo_error: str = ""

    @property
    def pausado(self) -> bool:
        return self._pausado

    def alternar_pausa(self) -> bool:
        """Pausa o reanuda. Devuelve si queda en pausa."""
        with self._lock:
            self._pausado = not self._pausado
            if self._pausado:
                self._gestos.clear()
            return self._pausado

    def actualizar(self, **campos) -> None:
        """La pose que saldrá en el próximo tick. Los ``None`` se omiten."""
        with self._lock:
            self._pose = dict(campos)

    def gesto(self, nombre: str) -> bool:
        """Encola un gesto de página. ``False`` si está en pausa y no saldrá."""
        with self._lock:
            if self._pausado:
                return False
            self._gestos.append(nombre)
            return True

    def tick(self) -> bool:
        """Un envío. Devuelve si salió un mensaje."""
        with self._lock:
            if self._pausado or not (self._pose or self._gestos):
                return False
            pose = dict(self._pose)
            gesto = self._gestos.popleft() if self._gestos else None
        return bool(self.publicador.publish(**pose, gesture=gesto))

    def bucle(self) -> None:
        siguiente = self._reloj()
        while not self._parar.is_set():
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 -- ver self.errores
                self.errores += 1
                self.ultimo_error = f"{type(exc).__name__}: {exc}"
            siguiente = max(siguiente + self.periodo, self._reloj())
            self._dormir(max(0.0, siguiente - self._reloj()))

    def arrancar(self) -> None:
        self._parar.clear()
        self._hilo = threading.Thread(target=self.bucle, name="publicacion", daemon=True)
        self._hilo.start()

    def parar(self) -> None:
        self._parar.set()
        if self._hilo is not None:
            self._hilo.join(timeout=2.0)
            self._hilo = None


# --------------------------------------------------------------------------
# Manos para la emoción
# --------------------------------------------------------------------------


def manos_de_blazepose(landmarks, ancho: int, alto: int) -> list[tuple[float, float]]:
    """Píxeles de las manos según BlazePose, para la regla «mano sobre la cara».

    ``landmarks`` es ``ArmTracker.landmarks_imagen`` (normalizado a [0, 1], del
    frame que recibió el tracker, el volteado), así que los píxeles salen en el
    MISMO frame que recibe ``EmotionClassifier.process`` y se comparan sin
    convertir con la caja del rostro.

    Los puntos fuera de la imagen se descartan: BlazePose inventa la mano que no
    ve, y con visibilidad alta (H40). Dentro del cuadro se pasan todos, igual que
    hacía el demo del P6 con MediaPipe Hands; si las ve o no junto a la oreja es
    lo que mide la etiqueta 7 del protocolo (H52).
    """
    if landmarks is None:
        return []
    puntos = []
    for indice in INDICES_MANOS:
        lm = landmarks.landmark[indice]
        u, v = float(lm.x) * ancho, float(lm.y) * alto
        if math.isfinite(u) and math.isfinite(v) and 0.0 <= u <= ancho and 0.0 <= v <= alto:
            puntos.append((u, v))
    return puntos


# --------------------------------------------------------------------------
# Grabación
# --------------------------------------------------------------------------


def rutas_de_grabacion(ruta: Path) -> dict[str, Path]:
    """``sesion.csv`` -> ``sesion.csv``, ``sesion_cabeza.csv``,
    ``sesion_brazos.csv`` y ``sesion_emocion.csv``."""
    ruta = Path(ruta)
    return {
        "sesion": ruta,
        "cabeza": ruta.with_name(f"{ruta.stem}_cabeza{ruta.suffix}"),
        "brazos": ruta.with_name(f"{ruta.stem}_brazos{ruta.suffix}"),
        "emocion": ruta.with_name(f"{ruta.stem}_emocion{ruta.suffix}"),
    }


class Grabacion:
    """Los CSV de una sesión del pipeline.

    ``sesion`` lleva una fila por frame con lo publicado y lo que costó. Los
    otros tres tienen **las columnas exactas de cada demo** (decisión del
    2026-10-26), así que ``tools/analizar_registro.py``,
    ``analizar_registro_brazos.py``, ``reproducir_brazos.py``,
    ``analizar_registro_emocion.py`` y ``evaluar_backends.py`` leen una sesión
    del pipeline como si fuera de su demo. Comparten el origen de ``t``, así que
    se pueden cruzar por tiempo.

    Nada se pisa: lo que ya existiera se aparta con ``_anterior``.
    """

    def __init__(self, ruta: Path, *, brazos: bool, emocion: bool,
                 recortes: Path | None = None, avisar=print) -> None:
        self.rutas = rutas_de_grabacion(ruta)
        if not brazos:
            del self.rutas["brazos"]
        if not emocion:
            del self.rutas["emocion"]
        self._archivos = {}
        self.escritores: dict[str, csv.writer] = {}
        cabeceras = {
            "sesion": COLUMNAS_SESION, "cabeza": COLUMNAS_CABEZA,
            "brazos": COLUMNAS_BRAZOS, "emocion": COLUMNAS_EMOCION,
        }
        try:
            for clave, destino in self.rutas.items():
                apartado = apartar_si_existe(destino)
                if apartado is not None:
                    avisar(f"AVISO: {destino.name} ya existia. Se conserva como {apartado.name}.")
                archivo = open(destino, "w", newline="", encoding="utf-8")
                self._archivos[clave] = archivo
                self.escritores[clave] = csv.writer(archivo)
                self.escritores[clave].writerow(cabeceras[clave])
            self.recortes = None
            if recortes is not None:
                recortes = Path(recortes)
                apartada = apartar_si_existe(recortes)
                if apartada is not None:
                    avisar(f"AVISO: {recortes.name} ya existia. Se conserva como {apartada.name}.")
                recortes.mkdir(parents=True)
                self.recortes = recortes
        except BaseException:
            self.cerrar()
            raise

    def escribir(self, clave: str, fila: list) -> None:
        escritor = self.escritores.get(clave)
        if escritor is not None:
            escritor.writerow(fila)

    def cerrar(self) -> None:
        for archivo in self._archivos.values():
            archivo.close()
        self._archivos.clear()


# --------------------------------------------------------------------------
# Panel
# --------------------------------------------------------------------------


@dataclass
class EstadoPanel:
    """Todo lo que pinta el panel, ya leído de los estimadores.

    Separado del dibujo para poder probar el dibujo sin cámara ni MediaPipe.
    """

    cabeza: tuple[float, float] | None = None
    recorte_cabeza: object = None              # HeadClamp | None
    mando: ArmAngles | None = None
    recortes_brazos: dict = field(default_factory=dict)   # {Side: ArmClamp}
    estimado: dict = field(default_factory=dict)          # {Side: bool}
    saludo: dict = field(default_factory=dict)            # {Side: 0..1}
    brazos_activos: bool = True
    emocion_activa: bool = True
    emocion: str | None = None
    confianza: float | None = None
    cruda: str | None = None
    confianza_cruda: float | None = None
    manual: str | None = None
    manos_en_cara: bool = False
    calibrando: bool = False
    latencia_emocion_ms: float | None = None
    fps: float = 0.0
    duplicados: int = 0
    ms_cabeza: float | None = None
    ms_brazos: float | None = None
    ms_emocion: float | None = None
    ms_bucle: float | None = None
    enlace: bool = False
    enviados: int = 0
    descartados: int = 0
    fallidos: int = 0
    hz: float = 0.0
    errores_publicacion: int = 0
    pausa: bool = False
    brillo: float | None = None
    grabando: bool = False
    etiqueta: int = 0
    aviso: str = ""


_FUENTE = cv2.FONT_HERSHEY_SIMPLEX
_BLANCO = (235, 235, 235)
_GRIS = (150, 150, 150)
_VERDE = (120, 230, 120)
_ROJO = (60, 60, 255)
_AMBAR = (0, 190, 255)
_CIAN = (230, 220, 90)


def _texto(lienzo, x: int, y: int, texto: str, color=_BLANCO, escala: float = 0.45,
           grosor: int = 1) -> None:
    cv2.putText(lienzo, texto, (x, y), _FUENTE, escala, color, grosor, cv2.LINE_AA)


def _ms(valor: float | None) -> str:
    return "--" if valor is None else f"{valor:.0f}"


def dibujar_panel(lienzo: np.ndarray, x0: int, estado: EstadoPanel) -> None:
    """Pinta el panel en las columnas ``x0 .. x0 + ANCHO_PANEL`` de ``lienzo``.

    No toca nada a la izquierda de ``x0``: el video queda limpio. Pone al lado
    de cada interpretación el dato del que sale —la predicción cruda junto a la
    expresión enviada, ``~`` en el brazo que no se midió este frame—, que es la
    lección del H31: con la conclusión sola no se nota cuando miente.
    """
    lienzo[:, x0:x0 + ANCHO_PANEL] = (25, 25, 25)
    x = x0 + 10
    y = 22

    if estado.pausa:
        _texto(lienzo, x, y, "PAUSA (k): no se publica", _AMBAR, 0.55, 2)
    elif not estado.enlace:
        _texto(lienzo, x, y, "SIN ENLACE MQTT", _ROJO, 0.55, 2)
    else:
        _texto(lienzo, x, y, "publicando", _VERDE, 0.55, 2)
    y += 20
    if estado.calibrando:
        _texto(lienzo, x, y, "CALIBRANDO: cara relajada,", _AMBAR, 0.45, 1)
        y += 16
        _texto(lienzo, x, y, "mirando la pantalla", _AMBAR, 0.45, 1)
    y += 20

    # -- los seis ángulos -------------------------------------------------
    filas = []
    rc = estado.recorte_cabeza
    if estado.cabeza is None:
        filas.append(("pan", None, None, False, "sin rostro"))
        filas.append(("tilt", None, None, False, ""))
    else:
        filas.append(("pan", estado.cabeza[0], rc.pan if rc else None, False, ""))
        filas.append(("tilt", estado.cabeza[1], rc.tilt if rc else None, False, ""))
    for canal in (Side.L, Side.R):
        campo_abd, campo_flex = CANALES[canal]
        if not estado.brazos_activos or estado.mando is None:
            filas.append((campo_abd, None, None, False, "--no-arms" if canal is Side.L else ""))
            filas.append((campo_flex, None, None, False, ""))
            continue
        recorte = estado.recortes_brazos.get(canal)
        sin_medir = not estado.estimado.get(canal, False)
        filas.append((campo_abd, getattr(estado.mando, campo_abd),
                      recorte.abduccion if recorte else None, sin_medir, ""))
        filas.append((campo_flex, getattr(estado.mando, campo_flex),
                      recorte.flexion if recorte else None, sin_medir, ""))
    for nombre, valor, eje, sin_medir, nota in filas:
        marca = "~" if sin_medir else " "
        if valor is None:
            _texto(lienzo, x, y, f"{nombre:<6}   --   {nota}", _GRIS)
        else:
            recortado = bool(eje and eje.clamped)
            _texto(lienzo, x, y, f"{nombre:<6}{marca}{valor:+7.1f}",
                   _ROJO if recortado else _BLANCO)
            if eje is not None:
                dibujar_barra(lienzo, x + 140, y, eje.fraction, recortado)
        y += 18
    activos = [c for c in (Side.L, Side.R) if estado.saludo.get(c, 0.0) > 0.0]
    if activos:
        _texto(lienzo, x, y, "SALUDO " + " ".join(CANALES[c][0] for c in activos), _CIAN)
    y += 22

    # -- expresión ---------------------------------------------------------
    if not estado.emocion_activa:
        _texto(lienzo, x, y, "emocion: desactivada", _GRIS)
        y += 18
    else:
        conf = "" if estado.confianza is None else f" {estado.confianza:.2f}"
        modo = "  MANUAL" if estado.manual else ""
        _texto(lienzo, x, y, f"ROBOT {estado.emocion}{conf}{modo}", _CIAN, 0.5, 1)
        y += 18
        if estado.cruda is None:
            cruda = "crudo: sin cara" if not estado.calibrando else "crudo: calibrando"
        else:
            cc = "" if estado.confianza_cruda is None else f" {estado.confianza_cruda:.2f}"
            cruda = f"crudo: {estado.cruda}{cc}"
        extra = "  MANOS EN CARA" if estado.manos_en_cara else ""
        _texto(lienzo, x, y, cruda + extra, _AMBAR if extra else _GRIS)
        y += 18
        if not estado.brazos_activos:
            _texto(lienzo, x, y, "manos: sin BlazePose (--no-arms)", _GRIS)
            y += 18
    y += 8

    # -- tasas y costes ------------------------------------------------------
    _texto(lienzo, x, y, f"camara {estado.fps:4.1f} fps   dup {estado.duplicados}",
           _VERDE if estado.fps >= 20 else _AMBAR)
    y += 18
    _texto(lienzo, x, y, f"ms cab {_ms(estado.ms_cabeza)}  braz {_ms(estado.ms_brazos)}"
                         f"  emo {_ms(estado.ms_emocion)}  total {_ms(estado.ms_bucle)}", _GRIS)
    y += 18
    if estado.latencia_emocion_ms is not None:
        _texto(lienzo, x, y, f"inferencia emocion {estado.latencia_emocion_ms:.0f} ms", _GRIS)
        y += 18
    _texto(lienzo, x, y, f"MQTT {'ok' if estado.enlace else 'NO'}  {estado.hz:4.1f} Hz",
           _VERDE if estado.enlace else _ROJO)
    y += 18
    _texto(lienzo, x, y, f"enviados {estado.enviados}  desc {estado.descartados}",
           _GRIS)
    y += 18
    _texto(lienzo, x, y, f"perdidos {estado.fallidos}",
           _ROJO if estado.fallidos else _GRIS)
    if estado.errores_publicacion:
        _texto(lienzo, x + 120, y, f"ERRORES {estado.errores_publicacion}", _ROJO, 0.45, 2)
    y += 18
    if estado.brillo is not None:
        if estado.brillo < BRILLO_MINIMO:
            _texto(lienzo, x, y, f"CAMARA NEGRA (brillo {estado.brillo:.1f})", _ROJO, 0.45, 2)
        else:
            _texto(lienzo, x, y, f"brillo {estado.brillo:.0f}", _GRIS)
        y += 18
    if estado.grabando:
        _texto(lienzo, x, y, f"REC  etiqueta {estado.etiqueta}", _ROJO, 0.5, 2)
        y += 20
    if estado.aviso:
        _texto(lienzo, x, y, estado.aviso, _AMBAR)

    alto = lienzo.shape[0]
    _texto(lienzo, x, alto - 26, "q salir  c calibrar  1-6/0 expresion", _GRIS, 0.4)
    _texto(lienzo, x, alto - 10, "k pausa  j/l pagina  ESPACIO etiqueta", _GRIS, 0.4)


# --------------------------------------------------------------------------
# El bucle
# --------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m telmeow_teleop.main",
        description="Pipeline integrado: una cámara, cabeza + brazos + emoción, "
                    "un mensaje MQTT a 20 Hz.",
    )
    propios = parser.add_argument_group("Pipeline")
    propios.add_argument(
        "--broker", metavar="HOST[:PUERTO]",
        help="Broker MQTT (atajo de --mqtt-host y --mqtt-port).",
    )
    propios.add_argument(
        "--no-emotion", action="store_true",
        help="Sin emoción: no se construye el backend ni se carga ningún modelo.",
    )
    propios.add_argument(
        "--no-arms", action="store_true",
        help="Sin brazos: no corre BlazePose (y la emoción se queda sin manos).",
    )
    propios.add_argument(
        "--record", metavar="CSV", type=Path,
        help="Graba la sesión: CSV y, al lado, _cabeza, _brazos y _emocion con "
             "las columnas de cada demo. ESPACIO cambia de etiqueta.",
    )
    propios.add_argument(
        "--guardar-recortes", metavar="CARPETA", type=Path,
        help="Con --record: guarda la cara de cada inferencia, para evaluar otros "
             "modelos sobre las mismas caras (tools/evaluar_backends.py).",
    )
    add_cli_args(parser)
    return parser


def _aplicar_broker(cfg: AppConfig, broker: str | None) -> AppConfig:
    if not broker:
        return cfg
    host, _, puerto = broker.partition(":")
    cambios = {"host": host} if host else {}
    if puerto:
        cambios["port"] = int(puerto)
    return replace(cfg, mqtt=replace(cfg.mqtt, **cambios))


def _crear_publicador(cfg: AppConfig):
    # Aquí y no arriba: el módulo tiene que poder importarse sin paho-mqtt, que
    # es como corre en la mayoría de las pruebas.
    from .publisher import PosePublisher

    return PosePublisher(cfg.mqtt, cfg.rates)


def _fps(marcas: deque) -> float:
    if len(marcas) > 1 and marcas[-1] > marcas[0]:
        return (len(marcas) - 1) / (marcas[-1] - marcas[0])
    return 0.0


def _hz(muestras: deque, ahora: float, enviados: int) -> float:
    """Mensajes por segundo en el último segundo, desde el contador.

    No se usa ``PosePublisher.effective_hz`` a propósito: poda una cola que el
    hilo de publicación también poda, y dos hilos podando a la vez pueden
    sacar el mismo elemento. El contador solo lo escribe ese hilo.
    """
    muestras.append((ahora, enviados))
    while len(muestras) > 2 and ahora - muestras[1][0] >= 1.0:
        muestras.popleft()
    t_inicio, n_inicio = muestras[0]
    return (enviados - n_inicio) / (ahora - t_inicio) if ahora > t_inicio else 0.0


def _f(valor: float | None, formato: str = ".4f") -> str:
    return "" if valor is None else format(valor, formato)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.guardar_recortes is not None and not args.record:
        parser.error("--guardar-recortes necesita --record: el CSV dice qué recorte es cada fila")
    cfg = _aplicar_broker(from_args(args, AppConfig.from_env()), args.broker)
    if not cfg.camera.flip:
        # Los brazos y la emoción saben si el frame viene volteado; el head
        # tracker no: su convenio de signos se midió con la imagen espejada.
        print("AVISO: sin espejo (--no-flip) el pan de la cabeza va AL REVES de lo "
              "verificado en el P4.1. Los brazos y la emocion lo compensan; la cabeza no.\n")

    clasificador = None
    if not args.no_emotion:
        print(f"Cargando el backend de emocion '{cfg.emotion.backend}'...")
        backend = crear_backend_o_reserva(cfg.emotion.backend, cfg)
        if backend is None:
            print("AVISO: no hay ningun backend de emocion; se sigue sin emocion.\n")
        else:
            clasificador = EmotionClassifier.desde_config(cfg, backend=backend)
    if args.guardar_recortes is not None and clasificador is None:
        parser.error("--guardar-recortes necesita la emocion")

    cap = abrir_camara(cfg.camera)
    if cap is None:
        print(
            f"No se pudo abrir la camara {cfg.camera.index}. "
            f"Prueba 'python tools/list_cameras.py' para ver que indices hay."
        )
        return 1

    cabeza = None
    brazos = None
    publicador = None
    periodico = None
    grabacion = None
    try:
        print(describir(cap, cfg.camera) + "\n")
        cabeza = HeadTracker.desde_config(cfg)
        brazos = None if args.no_arms else ArmTracker.desde_config(cfg)
        if clasificador is not None and getattr(clasificador.backend, "relativo", False):
            print(
                f"CALIBRACION: al aparecer tu cara, mira la pantalla con la cara\n"
                f"  relajada {clasificador.calibracion_s:g} s. La tecla c vuelve a tomar\n"
                f"  a la vez la cara neutra y la postura neutra de la cabeza.\n"
            )
        print("TECLAS: q salir · c calibrar · 1..6 expresion · 0 automatico · "
              "k pausa · j/l pagina · ESPACIO etiqueta\n")

        if args.record:
            grabacion = Grabacion(args.record, brazos=brazos is not None,
                                  emocion=clasificador is not None,
                                  recortes=args.guardar_recortes)
            print("Registrando en " + ", ".join(p.name for p in grabacion.rutas.values()) + "\n")

        publicador = _crear_publicador(cfg)
        publicador.connect()
        print(f"Conectando a {cfg.mqtt.host}:{cfg.mqtt.port}...")
        # Antes de emitir (punto 8): con QoS 0, lo publicado sin enlace se pierde.
        if publicador.wait_connected(timeout=5.0):
            print("Enlace MQTT establecido.\n")
        else:
            print(f"AVISO: sin enlace con {cfg.mqtt.host}:{cfg.mqtt.port}. Se sigue "
                  f"reintentando; mira 'perdidos' en el panel.\n")
        periodico = PublicadorPeriodico(publicador, cfg.rates.publish_period)
        periodico.arrancar()

        _bucle(cfg, cap, cabeza, brazos, clasificador, periodico, publicador, grabacion)
    except KeyboardInterrupt:
        print("\nInterrumpido.")
    finally:
        # Cierre limpio en cualquier caso (punto 7): q, Ctrl-C o excepción.
        if periodico is not None:
            periodico.parar()
        if publicador is not None:
            print(f"MQTT: {publicador.message_count} enviados, "
                  f"{publicador.dropped_count} descartados, "
                  f"{publicador.failed_count} perdidos.")
            publicador.disconnect()
        cap.release()
        cv2.destroyAllWindows()
        for tracker in (cabeza, brazos):
            if tracker is not None:
                tracker.close()
        if grabacion is not None:
            grabacion.cerrar()
            print("Registro guardado en " + ", ".join(str(p) for p in grabacion.rutas.values()))
    return 0


def _bucle(cfg, cap, cabeza, brazos, clasificador, periodico, publicador, grabacion) -> None:
    import mediapipe as mp

    dibujo = mp.solutions.drawing_utils
    conexiones = mp.solutions.pose.POSE_CONNECTIONS
    t0 = time.perf_counter()
    marcas: deque[float] = deque(maxlen=30)
    previo = None
    duplicados = 0
    etiqueta = 0
    inferencias_vistas = 0
    calibraciones_vistas = 0
    t_anterior = None
    gesto_registrado = ""
    aviso = ""
    aviso_hasta = 0.0
    lienzo = None
    muestras_hz: deque[tuple[float, int]] = deque()
    ms_bucle_previo = None

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        ahora = time.perf_counter()
        t_captura_ms = time.time() * 1000.0
        if cfg.camera.flip:
            frame = cv2.flip(frame, 1)
        alto, ancho = frame.shape[:2]
        marcas.append(ahora)
        dt_ms = None if t_anterior is None else 1000.0 * (ahora - t_anterior)
        t_anterior = ahora
        # H19: contar lecturas no mide fps si la cámara repite frames. Se
        # compara una submuestra con el anterior, que cuesta microsegundos.
        muestra = frame[::16, ::16]
        duplicado = previo is not None and np.array_equal(muestra, previo)
        previo = muestra.copy()
        duplicados += int(duplicado)

        # Un solo cvtColor por frame, compartido por las dos redes de MediaPipe.
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        inicio = time.perf_counter()
        pose_cabeza = cabeza.process(rgb)
        ms_cabeza = 1000.0 * (time.perf_counter() - inicio)

        mando = None
        ms_brazos = None
        if brazos is not None:
            inicio = time.perf_counter()
            mando = brazos.comando(brazos.process(rgb))
            ms_brazos = 1000.0 * (time.perf_counter() - inicio)

        emocion = None
        ms_emocion = None
        infirio = False
        if clasificador is not None:
            inicio = time.perf_counter()
            manos = (
                (lambda: manos_de_blazepose(brazos.landmarks_imagen, ancho, alto))
                if brazos is not None else None
            )
            # El BGR volteado y SIN dibujar (punto 14): es el convenio del
            # entrenamiento, y el recorte que se graba tiene que ser lo que vio.
            emocion = clasificador.process(frame, ahora, manos=manos)
            if clasificador.inferencias != inferencias_vistas:
                inferencias_vistas = clasificador.inferencias
                infirio = True
                ms_emocion = 1000.0 * (time.perf_counter() - inicio)
                if clasificador.calibraciones != calibraciones_vistas:
                    calibraciones_vistas = clasificador.calibraciones
                    print("cara neutra calibrada")

        pan, tilt = pose_cabeza if pose_cabeza is not None else (None, None)
        campos = {"pan": pan, "tilt": tilt, "t": t_captura_ms}
        if mando is not None:
            campos.update(armL=mando.armL, armLtw=mando.armLtw,
                          armR=mando.armR, armRtw=mando.armRtw)
        if emocion is not None:
            campos["emo"] = emocion.etiqueta
        periodico.actualizar(**campos)

        brillo = brillo_medio(muestra)
        t = ahora - t0
        if grabacion is not None:
            if pose_cabeza is not None:
                grabacion.escribir("cabeza", fila_cabeza(cabeza, t, etiqueta, pose_cabeza, ancho, alto))
            if brazos is not None:
                grabacion.escribir("brazos", fila_brazos(brazos, mando, t, etiqueta, ancho, alto))
            if infirio:
                registrar_inferencia(grabacion.escritores.get("emocion"), t, etiqueta,
                                     clasificador, frame, brillo_medio(frame),
                                     grabacion.recortes)

        # -- dibujo: video limpio a la izquierda, panel a la derecha --------
        if lienzo is None or lienzo.shape[:2] != (alto, ancho + ANCHO_PANEL):
            lienzo = np.zeros((alto, ancho + ANCHO_PANEL, 3), dtype=np.uint8)
        video = frame.copy()
        if brazos is not None and brazos.landmarks_imagen is not None:
            dibujo.draw_landmarks(video, brazos.landmarks_imagen, conexiones)
        if clasificador is not None and clasificador.rostro is not None:
            x, y, w, h = clasificador.rostro
            cv2.rectangle(video, (x, y), (x + w, y + h),
                          (0, 190, 255) if clasificador.manos_en_cara else (230, 220, 90), 2)
        lienzo[:, :ancho] = video

        cruda = clasificador.ultima if clasificador is not None else None
        if time.perf_counter() > aviso_hasta:
            aviso = ""
        hz = _hz(muestras_hz, ahora, publicador.message_count)
        estado = EstadoPanel(
            cabeza=pose_cabeza,
            recorte_cabeza=cabeza.last_clamp,
            mando=mando,
            recortes_brazos=brazos.last_clamp if brazos is not None else {},
            estimado=brazos.estimado if brazos is not None else {},
            saludo=brazos.pesos_saludo if brazos is not None else {},
            brazos_activos=brazos is not None,
            emocion_activa=clasificador is not None,
            emocion=emocion.etiqueta if emocion is not None else None,
            confianza=emocion.confianza if emocion is not None else None,
            cruda=cruda.etiqueta if cruda is not None else None,
            confianza_cruda=cruda.confianza if cruda is not None else None,
            manual=clasificador.manual if clasificador is not None else None,
            manos_en_cara=bool(clasificador and clasificador.manos_en_cara),
            calibrando=bool(clasificador and (clasificador.calibrando
                                              or clasificador.calibracion_pendiente)),
            latencia_emocion_ms=clasificador.latencia_ms if clasificador is not None else None,
            fps=_fps(marcas),
            duplicados=duplicados,
            ms_cabeza=ms_cabeza,
            ms_brazos=ms_brazos,
            ms_emocion=ms_emocion,
            # El del frame anterior: el de este no se sabe hasta pintar el panel.
            ms_bucle=ms_bucle_previo,
            enlace=publicador.connected,
            enviados=publicador.message_count,
            descartados=publicador.dropped_count,
            fallidos=publicador.failed_count,
            hz=hz,
            errores_publicacion=periodico.errores,
            pausa=periodico.pausado,
            brillo=brillo,
            grabando=grabacion is not None,
            etiqueta=etiqueta,
            aviso=aviso,
        )
        dibujar_panel(lienzo, ancho, estado)
        # Todo lo del frame salvo esperar a la cámara y el imshow/waitKey:
        # estimadores, grabación y dibujo.
        ms_bucle = ms_bucle_previo = 1000.0 * (time.perf_counter() - ahora)

        if grabacion is not None:
            grabacion.escribir("sesion", [
                f"{t:.4f}", f"{t_captura_ms:.1f}", etiqueta, _f(dt_ms, ".2f"),
                int(duplicado), _f(ms_cabeza, ".2f"), _f(ms_brazos, ".2f"),
                _f(ms_emocion, ".2f"), _f(ms_bucle, ".2f"),
                _f(pan), _f(tilt),
                *(_f(getattr(mando, c)) if mando is not None else ""
                  for c in ("armL", "armLtw", "armR", "armRtw")),
                emocion.etiqueta if emocion is not None else "",
                gesto_registrado, int(periodico.pausado),
                publicador.message_count, publicador.dropped_count,
                publicador.failed_count, f"{hz:.1f}",
                f"{brillo:.1f}", ancho, alto,
            ])
        gesto_registrado = ""

        cv2.imshow(VENTANA, lienzo)
        tecla = cv2.waitKey(1) & 0xFF
        if tecla == TECLA_SALIR:
            break
        elif tecla == TECLA_ETIQUETA:
            etiqueta += 1
            print(f"--- etiqueta {etiqueta} ---")
        elif tecla == TECLA_CALIBRAR:
            # Las dos piden lo mismo al operador —mirar la pantalla con la cara
            # relajada—, así que una tecla calibra las dos (punto 19).
            de_cabeza = cabeza.set_neutral()
            de_emocion = clasificador.calibrar_neutro() if clasificador is not None else False
            print(f"calibrar: cabeza {'si' if de_cabeza else 'sin rostro'}, "
                  f"emocion {'en curso' if de_emocion else 'no aplica'}")
        elif tecla in TECLAS_MANUAL:
            if clasificador is None:
                aviso, aviso_hasta = "sin emocion: tecla ignorada", time.perf_counter() + 2.0
            else:
                vigente = clasificador.toggle_manual(TECLAS_MANUAL[tecla])
                print(f"expresion forzada: {vigente or 'ninguna (automatico)'}")
        elif tecla == TECLA_AUTOMATICO:
            if clasificador is not None:
                clasificador.clear_manual()
                print("expresion: automatico")
        elif tecla == TECLA_PAUSA:
            print("publicacion en PAUSA" if periodico.alternar_pausa() else "publicacion reanudada")
        elif tecla in (TECLA_PAGINA_ANTERIOR, TECLA_PAGINA_SIGUIENTE):
            gesto = "page_prev" if tecla == TECLA_PAGINA_ANTERIOR else "page_next"
            if periodico.gesto(gesto):
                gesto_registrado = gesto
                print(f"gesto {gesto}")
            else:
                aviso, aviso_hasta = "en pausa: gesto no enviado", time.perf_counter() + 2.0


if __name__ == "__main__":
    raise SystemExit(main())
