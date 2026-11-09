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
ESPACIO cambiar de etiqueta (con ``--record``). Las teclas son órdenes
(``ordenes.py``) que ejecuta ``ejecutar_orden``.

Con ``--interfaz`` (P10) el pipeline sirve además la interfaz de narración en
``http://localhost:8001``: el visor con la cámara del operador, el libro y los
botones, cuyas órdenes entran por una cola y se ejecutan como las teclas. El
comando de la sesión es ``python -m telmeow_teleop.control_panel``, que además
levanta el broker y abre el navegador (ver ``control_panel.py``).

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
from .config import AppConfig, MQTTConfig, add_cli_args, from_args
from .emotion_classifier import COLUMNAS_CSV as COLUMNAS_EMOCION
from .emotion_classifier import (
    MARGEN_MANOS,
    EmotionClassifier,
    crear_backend_o_reserva,
    mano_sobre_rostro,
    registrar_inferencia,
)
from .head_tracker import COLUMNAS_CSV as COLUMNAS_CABEZA
from .head_tracker import HeadTracker, RuidoMalla, apartar_si_existe
from .head_tracker import fila_csv as fila_cabeza
from .historias import Biblioteca, Lector
from .limits import Side
from .ordenes import ORDENES, ORDENES_DE_TECLA, Orden

__all__ = [
    "PublicadorPeriodico",
    "GuardaCabeza",
    "HiloEmocion",
    "EstadoEmocion",
    "paso_suave",
    "Grabacion",
    "EstadoPanel",
    "manos_de_blazepose",
    "rutas_de_grabacion",
    "dibujar_panel",
    "COLUMNAS_SESION",
    "ANCHO_PANEL",
    "Orden",
    "ORDENES",
    "ORDENES_DE_TECLA",
    "Sesion",
    "ejecutar_orden",
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
    # Desde el P7.1 (2026-11-02): 1 si la emoción corría en su hilo, 0 en el
    # bucle (--emocion-en-bucle). En las filas con inferencia, 'ms_emocion' es
    # lo que costó inferir, en el hilo que lo hizo.
    "emocion_hilo",
    # Desde el 2026-11-03 (H58): 1 si las manos tapaban la cara y la cabeza
    # publicada era la guardada, no la medida. 'pan' y 'tilt' son SIEMPRE lo
    # publicado; la medida está en el CSV de cabeza.
    "cabeza_tapada",
    # Desde el 2026-11-07 (P7.6): si la malla fallaba (MallaInestable), su ruido
    # (RuidoMalla, grados crudos), la zona muerta en uso y la pausa por poca luz.
    "malla_falla", "ruido_malla", "zona_muerta", "pausa_luz",
    # Desde el 2026-11-07 (P7.7): la pausa por cara perdida sin manos encima.
    "pausa_cara",
    # Desde el 2026-11-08 (P10): el cuento y la página vigentes al acabar el
    # frame, y las órdenes ejecutadas en él ("nombre=valor@origen", varias
    # separadas por "|"). Desde el P10 el gesto se anota en el frame en que
    # salió; antes caía en el siguiente.
    "historia", "pagina", "orden",
)

# Las teclas y las órdenes viven en ``ordenes.py`` (solo biblioteca estándar),
# para que el servidor de la interfaz las valide sin cargar MediaPipe.


# --------------------------------------------------------------------------
# Lo que comparten las órdenes y el bucle
# --------------------------------------------------------------------------


@dataclass
class Sesion:
    """Lo que las órdenes leen y cambian, compartido con el bucle de visión.

    Solo lo toca el hilo del bucle: las órdenes del navegador llegan por una
    cola y se ejecutan aquí, como las teclas.
    """

    cabeza: object
    hilo: object
    periodico: object
    #: Los cuentos (P10): de dónde se eligen y en qué página va la lectura.
    biblioteca: Biblioteca | None = None
    lector: Lector = field(default_factory=Lector)
    topic_cuento: str = MQTTConfig.topic_story
    etiqueta: int = 0
    aviso: str = ""
    aviso_hasta: float = 0.0
    #: El gesto que salió en este frame, para la columna ``gesto``.
    gesto_registrado: str = ""
    #: Las órdenes ejecutadas en este frame, para la columna ``orden``.
    orden_registrada: str = ""
    salir: bool = False

    def avisar(self, texto: str, segundos: float = 2.0) -> None:
        self.aviso = texto
        self.aviso_hasta = time.perf_counter() + segundos

    def caducar_aviso(self) -> None:
        if time.perf_counter() > self.aviso_hasta:
            self.aviso = ""


def ejecutar_orden(sesion: Sesion, orden: Orden) -> None:
    """Hace lo que pide la orden. Solo desde el hilo del bucle."""
    nombre = orden.nombre
    cabeza, hilo, periodico = sesion.cabeza, sesion.hilo, sesion.periodico
    registro = orden.para_registro()
    sesion.orden_registrada = (f"{sesion.orden_registrada}|{registro}"
                               if sesion.orden_registrada else registro)
    if nombre == "salir":
        sesion.salir = True
    elif nombre == "etiqueta":
        sesion.etiqueta += 1
        print(f"--- etiqueta {sesion.etiqueta} ---")
    elif nombre == "calibrar":
        # Las dos piden lo mismo al operador —mirar la pantalla con la cara
        # relajada—, así que una orden calibra las dos (punto 19).
        de_cabeza = cabeza.set_neutral()
        if de_cabeza:
            periodico.suavizar()
        de_emocion = (hilo is not None
                      and getattr(hilo.clasificador.backend, "relativo", False))
        if de_emocion:
            hilo.ordenar("calibrar_neutro")
        print(f"calibrar: cabeza {'si' if de_cabeza else 'sin rostro'}, "
              f"emocion {'en curso' if de_emocion else 'no aplica'}")
    elif nombre == "expresion":
        if hilo is None:
            sesion.avisar("sin emocion: tecla ignorada")
        else:
            hilo.ordenar("toggle_manual", orden.valor)
            # La misma tecla otra vez la suelta (toggle_manual).
            vigente = None if hilo.estado.manual == orden.valor else orden.valor
            print(f"expresion forzada: {vigente or 'ninguna (automatico)'}")
    elif nombre == "automatico":
        if hilo is not None:
            hilo.ordenar("clear_manual")
            print("expresion: automatico")
    elif nombre == "pausa":
        print("publicacion en PAUSA" if periodico.alternar_pausa() else "publicacion reanudada")
    elif nombre in ("pagina_anterior", "pagina_siguiente"):
        gesto = "page_prev" if nombre == "pagina_anterior" else "page_next"
        if sesion.lector.abierto:
            _pasar_pagina(sesion, gesto, -1 if nombre == "pagina_anterior" else +1)
        elif periodico.gesto(gesto):
            # Sin cuento, como en el P7: solo el gesto.
            sesion.gesto_registrado = gesto
            print(f"gesto {gesto}")
        else:
            sesion.avisar("en pausa: gesto no enviado")
    elif nombre == "historia":
        _abrir_cuento(sesion, orden.valor)
    else:
        raise ValueError(f"orden desconocida: {nombre!r}")


def _pasar_pagina(sesion: Sesion, gesto: str, paso: int) -> None:
    """Con un cuento abierto: la página y su gesto salen juntos o no sale nada
    (``evento_pagina``). En pausa no se pasa de página (decisión del operador,
    2026-11-08)."""
    lector, periodico = sesion.lector, sesion.periodico
    destino = lector.destino(paso)
    if destino is None:
        sesion.avisar("es la ultima pagina" if paso > 0 else "es la primera pagina")
    elif periodico.evento_pagina(gesto, destino, lector.total):
        lector.ir(destino)
        sesion.gesto_registrado = gesto
        print(f"pagina {destino}/{lector.total} ({gesto})")
    elif periodico.pausado:
        sesion.avisar("en pausa: no se puede pasar de pagina")
    else:
        sesion.avisar("sin enlace: la pagina no salio")


def _abrir_cuento(sesion: Sesion, nombre: str | None) -> None:
    """Abre el cuento en la página 1 y lo anuncia, retenido, por el topic del
    cuento y por el de la página: así la página retenida no se queda con la del
    cuento anterior. No lleva gesto, así que también vale en pausa."""
    historia = (sesion.biblioteca.buscar(nombre)
                if sesion.biblioteca is not None and nombre else None)
    if historia is None or not historia.legible:
        sesion.avisar(f"cuento no disponible: {nombre}")
        return
    sesion.lector.elegir(historia)
    periodico = sesion.periodico
    salio = periodico.publicar_evento(sesion.topic_cuento,
                                      {"story": historia.nombre, "page": 1}, retain=True)
    salio = periodico.publicar_evento(periodico.topic_pagina,
                                      {"page": 1, "total": historia.paginas},
                                      retain=True) and salio
    if not salio:
        sesion.avisar("sin enlace: el cuento no se anuncio")
    print(f"cuento: {historia.titulo} ({historia.paginas} paginas)")


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

    Con un cuento abierto (P10) la página va por ``evento_pagina``, que no
    encola: manda en el acto la pose con el gesto y la página, y la página por
    su topic, con la pausa comprobada bajo el mismo candado. Página y gesto van
    siempre juntos o no va ninguno (decisión del operador, 2026-11-08).

    Al reanudar, los ángulos no saltan: durante ``rampa_s`` segundos se mezclan
    la última pose enviada y la del operador, con un paso suave (arranque y
    frenado), y si el operador se mueve mientras tanto la rampa lo sigue. Lo
    pidió el operador el 2026-11-02: el salto de golpe a la pose actual se veía
    raro. La expresión no se mezcla; el visor ya la funde en 350 ms.
    """

    #: Los campos que se mezclan en la rampa: los ángulos.
    ANGULOS = ("pan", "tilt", "armL", "armLtw", "armR", "armRtw")

    def __init__(self, publicador, periodo: float, *, rampa_s: float = 1.0,
                 topic_pagina: str | None = None,
                 reloj=time.perf_counter, dormir=time.sleep) -> None:
        self.publicador = publicador
        self.periodo = float(periodo)
        self.rampa_s = max(0.0, float(rampa_s))
        self.topic_pagina = topic_pagina or MQTTConfig.topic_page
        self._reloj = reloj
        self._dormir = dormir
        self._lock = threading.Lock()
        # Serializa los envíos: el del hilo de 20 Hz y el de evento_pagina, que
        # sale del bucle. El publisher no tiene candado propio y sus contadores
        # no aguantan dos hilos a la vez. Orden: _lock antes que _envio, nunca
        # al revés (tick suelta _lock antes de tomar _envio).
        self._envio = threading.Lock()
        self._pose: dict = {}
        self._gestos: deque[str] = deque()
        self._pausado = False
        self._motivos: set[str] = set()            # P7.6-7: pausas automáticas
        self._enviada: dict[str, float] = {}       # últimos ángulos que salieron
        self._rampa_desde: dict[str, float] | None = None
        self._rampa_inicio = 0.0
        self._parar = threading.Event()
        self._hilo: threading.Thread | None = None
        #: Envíos que lanzaron una excepción. El hilo no muere por uno: un hilo
        #: de publicación muerto en silencio dejaría al robot quieto con el
        #: resto del pipeline diciendo que todo va bien. El panel lo enseña.
        self.errores = 0
        self.ultimo_error: str = ""

    @property
    def pausado(self) -> bool:
        """Si no sale nada: por la tecla o por una pausa automática."""
        return self._pausado or bool(self._motivos)

    @property
    def pausado_por_tecla(self) -> bool:
        return self._pausado

    @property
    def pausado_por_luz(self) -> bool:
        return "luz" in self._motivos

    @property
    def pausado_sin_cara(self) -> bool:
        return "cara" in self._motivos

    def alternar_pausa(self) -> bool:
        """Pausa o reanuda con la tecla. Devuelve si queda en pausa (por la
        tecla; las automáticas van aparte y no las quita la tecla)."""
        with self._lock:
            antes = self.pausado
            self._pausado = not self._pausado
            self._transicion(antes)
            return self._pausado

    def fijar_pausa(self, motivo: str, activa: bool) -> None:
        """Una pausa automática: ``"luz"`` (``VigiaRuido``, P7.6) o ``"cara"``
        (``VigiaCara``, P7.7). Conviven entre sí y con la de la tecla: solo se
        publica si no hay ninguna."""
        with self._lock:
            antes = self.pausado
            if activa:
                self._motivos.add(motivo)
            else:
                self._motivos.discard(motivo)
            self._transicion(antes)

    def fijar_pausa_luz(self, activa: bool) -> None:
        self.fijar_pausa("luz", activa)

    def suavizar(self) -> None:
        """Lo siguiente sale mezclado desde lo último que salió, con la rampa de
        reanudar: para cuando la pose da un salto que no es del operador, como al
        tomar un neutro nuevo de la cabeza (P7.7)."""
        with self._lock:
            if self.rampa_s > 0.0 and self._enviada and not self.pausado:
                self._rampa_desde = dict(self._enviada)
                self._rampa_inicio = self._reloj()

    def _transicion(self, antes: bool) -> None:
        """Con el lock: al entrar en pausa se tiran los gestos; al salir, la
        rampa desde lo último que salió, sea cual sea la pausa que acabó."""
        ahora = self.pausado
        if ahora and not antes:
            self._gestos.clear()
        elif antes and not ahora and self.rampa_s > 0.0 and self._enviada:
            self._rampa_desde = dict(self._enviada)
            self._rampa_inicio = self._reloj()

    def actualizar(self, **campos) -> None:
        """La pose que saldrá en el próximo tick. Los ``None`` se omiten."""
        with self._lock:
            self._pose = dict(campos)

    def gesto(self, nombre: str) -> bool:
        """Encola un gesto de página. ``False`` si está en pausa y no saldrá."""
        with self._lock:
            if self.pausado:
                return False
            self._gestos.append(nombre)
            return True

    def publicar_evento(self, topic: str, datos: dict, *, retain: bool = False) -> bool:
        """Un evento por otro topic (el cuento), sin pisar el envío del hilo."""
        with self._envio:
            return bool(self.publicador.publicar_evento(topic, datos, retain=retain))

    def evento_pagina(self, gesto: str, pagina: int, total: int) -> bool:
        """Cambio de página de un cuento (P10): la pose vigente con ``gesture`` y
        ``page`` por el topic de pose y, si salió, ``{"page", "total"}``
        retenido por el de página. Todo en el acto, desde quien llama.

        Devuelve ``False`` sin mandar nada si hay pausa (de cualquier motivo), y
        también si el mensaje de pose no sale a la red: quien llama no avanza
        la página. La pausa se comprueba con el mismo candado con que se envía,
        así que no puede entrar entre las dos cosas.
        """
        with self._lock:
            if self.pausado:
                return False
            pose = dict(self._pose)
            if self._rampa_desde is not None:
                pose = self._mezclar(pose)
            with self._envio:
                salio = bool(self.publicador.publish(**pose, gesture=gesto, page=pagina))
                if salio:
                    self.publicador.publicar_evento(
                        self.topic_pagina, {"page": int(pagina), "total": int(total)},
                        retain=True)
            if salio:
                self._enviada.update(
                    {k: pose[k] for k in self.ANGULOS if isinstance(pose.get(k), (int, float))})
        return salio

    def tick(self) -> bool:
        """Un envío. Devuelve si salió un mensaje."""
        with self._lock:
            if self.pausado or not (self._pose or self._gestos):
                return False
            pose = dict(self._pose)
            gesto = self._gestos.popleft() if self._gestos else None
            if self._rampa_desde is not None:
                pose = self._mezclar(pose)
        with self._envio:
            salio = bool(self.publicador.publish(**pose, gesture=gesto))
        with self._lock:
            self._enviada.update(
                {k: pose[k] for k in self.ANGULOS if isinstance(pose.get(k), (int, float))})
        return salio

    def _mezclar(self, pose: dict) -> dict:
        """La pose del operador mezclada con la de antes de la pausa (con el lock)."""
        avance = (self._reloj() - self._rampa_inicio) / self.rampa_s
        if avance >= 1.0:
            self._rampa_desde = None
            return pose
        peso = paso_suave(max(0.0, avance))
        mezclada = dict(pose)
        for clave in self.ANGULOS:
            desde, hacia = self._rampa_desde.get(clave), pose.get(clave)
            if isinstance(desde, (int, float)) and isinstance(hacia, (int, float)):
                mezclada[clave] = desde + (hacia - desde) * peso
        return mezclada

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


def paso_suave(x: float) -> float:
    """Smoothstep, 3x² − 2x³: de 0 a 1 con velocidad nula en los dos extremos.
    El mismo que usa el visor en las transiciones del saludo."""
    x = min(1.0, max(0.0, x))
    return x * x * (3.0 - 2.0 * x)


def _mezcla(desde, hacia, peso: float) -> tuple[float, float]:
    return tuple(a + (b - a) * peso for a, b in zip(desde, hacia))


class GuardaCabeza:
    """Lo que se publica de la cabeza cuando las manos tapan la cara (H58).

    Face Mesh no avisa de una cara tapada: se inventa una detrás de las manos,
    y su pose salta 6-13° por frame (verificación del 2026-11-03; sin tapar,
    menos de 1°). Lo decidió el operador ese día. Con la cara tapada:

    - la cabeza se queda en la última pose buena: la de ``antes_s`` antes de
      que se vieran las manos encima, porque las primeras poses inventadas
      llegan antes de que BlazePose las vea;
    - si sigue tapada ``espera_s``, va al frente (0, 0) con un paso suave de
      ``rampa_s``, como los brazos van al reposo y la emoción a neutral;
    - al destaparse, vuelve a la pose del operador mezclando durante
      ``rampa_s``, como al reanudar la pausa.

    Las manos cuentan como encima hasta ``retencion_s`` después de la última
    vez que se vieron: BlazePose las pierde a ratos (H52, H55).

    DESDE EL P7.6 (2026-11-07) LAS MANOS SOLAS NO BASTAN. Con las manos en las
    mejillas («el grito») la malla sigue viendo la cara —el 100 % de los frames
    en p75, con saltos crudos de 1.9-2.5° en el p90— y la cabeza tiene que
    seguir al operador; lo pidió él. Lo que delata la cara tapada es que la
    malla falla: la pierde (59 % de los frames) o salta (p90 de 13°). Así que la
    guarda entra con las manos encima Y la malla fallando (``malla_falla``, que
    da ``MallaInestable``), y sale cuando se van las manos o cuando la malla
    lleva ``suelta_s`` sin fallar. Mientras las manos están encima y la guarda no
    actúa, la cabeza va con un límite de velocidad, ``limite_grados_s``: las
    primeras poses inventadas llegan antes de que se note el fallo, y así no se
    ven. Con ``malla_falla`` por defecto (``True``) se comporta como antes.
    """

    def __init__(self, *, espera_s: float = 2.0, rampa_s: float = 1.0,
                 retencion_s: float = 0.5, antes_s: float = 0.3,
                 limite_grados_s: float = 60.0, suelta_s: float = 1.0) -> None:
        self.espera_s = espera_s
        self.rampa_s = rampa_s
        self.retencion_s = retencion_s
        self.antes_s = antes_s
        self.limite_grados_s = limite_grados_s
        self.suelta_s = suelta_s
        self._historial: deque[tuple[float, tuple[float, float]]] = deque()
        self._ultima_mano: float | None = None
        self._ultima_falla: float | None = None
        self._por_cara = False
        self._manos_desde: float | None = None
        self._t_salida: float | None = None
        self._tapada_desde: float | None = None
        self._fija: tuple[float, float] = (0.0, 0.0)
        self._salida: tuple[float, float] | None = None
        self._volviendo: tuple[tuple[float, float], float] | None = None

    @property
    def tapada(self) -> bool:
        return self._tapada_desde is not None

    def _pose_antes(self, ahora: float) -> tuple[float, float] | None:
        anteriores = [p for t, p in self._historial if t <= ahora - self.antes_s]
        if anteriores:
            return anteriores[-1]
        return self._historial[0][1] if self._historial else None

    def paso(self, ahora: float, pose: tuple[float, float] | None,
             manos_encima: bool, malla_falla: bool = True,
             sin_cara: bool = False) -> tuple[float, float] | None:
        """La pose que se publica: ``pose`` medida, o la guardada si está tapada.
        ``None`` (sin cara y sin manos encima) se omite del JSON, como siempre."""
        if manos_encima:
            self._ultima_mano = ahora
        manos = self._ultima_mano is not None and ahora - self._ultima_mano <= self.retencion_s
        if manos and self._manos_desde is None:
            self._manos_desde = ahora
        elif not manos:
            self._manos_desde = None
        if malla_falla:
            self._ultima_falla = ahora
        # Una vez dentro, sigue mientras haya manos, salvo que la malla lleve
        # ``suelta_s`` sin fallar: un fallo suelto con las manos en las mejillas
        # no puede dejar la cabeza fija todo el gesto (en p75, el 73 % de «el
        # grito» por un solo tropiezo de la malla).
        sigue = (self._tapada_desde is not None and self._ultima_falla is not None
                 and ahora - self._ultima_falla < self.suelta_s)
        if manos:
            tapada = sigue or malla_falla
        else:
            # P7.7: sin manos, la cara perdida (luz apagada, fuera de cuadro):
            # la misma guarda, quieta y a los 2 s al frente, y se suelta cuando
            # la malla vuelve a ir bien.
            tapada = sin_cara or (self._por_cara and sigue)
        if tapada:
            if self._tapada_desde is None:
                self._tapada_desde = ahora
                self._por_cara = not manos
                if self._manos_desde is not None and ahora - self._manos_desde <= self.antes_s:
                    # Entró a la vez que las manos: la pose de antes de verlas,
                    # porque las primeras inventadas llegan antes (H58).
                    self._fija = self._pose_antes(ahora) or self._salida or (0.0, 0.0)
                else:
                    # Entró después, con la cabeza siguiendo al operador con el
                    # límite de velocidad: desde donde está, sin salto.
                    self._fija = self._salida or self._pose_antes(ahora) or (0.0, 0.0)
                self._volviendo = None
            peso = paso_suave((ahora - self._tapada_desde - self.espera_s) / self.rampa_s)
            self._salida = _mezcla(self._fija, (0.0, 0.0), peso)
            self._t_salida = ahora
            return self._salida
        if self._tapada_desde is not None:           # se acaba de destapar
            self._tapada_desde = None
            self._por_cara = False
            self._historial.clear()
            if self._salida is not None:
                self._volviendo = (self._salida, ahora)
        if pose is None:
            return None
        self._historial.append((ahora, pose))
        while self._historial and ahora - self._historial[0][0] > 1.0:
            self._historial.popleft()
        salida = pose
        if self._volviendo is not None:
            desde, inicio = self._volviendo
            peso = paso_suave((ahora - inicio) / self.rampa_s)
            if peso >= 1.0:
                self._volviendo = None
            else:
                salida = _mezcla(desde, pose, peso)
        if manos and self.limite_grados_s > 0.0 and self._salida is not None \
                and self._t_salida is not None:
            maximo = self.limite_grados_s * max(0.0, ahora - self._t_salida)
            salida = tuple(a + max(-maximo, min(maximo, b - a)) for a, b in zip(self._salida, salida))
        self._salida = salida
        self._t_salida = ahora
        return salida


class MallaInestable:
    """Si la Face Mesh ha dejado de ser de fiar: sin cara durante ``sin_cara_s``,
    o con la mediana de sus últimos ``n`` saltos crudos por encima de
    ``umbral`` grados (P7.6).

    Con la cara a la vista los saltos crudos van por 1-2.5° (p90, con las manos
    en las mejillas incluido); con la cara tapada, la malla se inventa una y
    salta hasta 13° (H58). La mediana de cinco no se deja llevar por un salto
    suelto. Simulado sobre p75: con las manos en las mejillas la guarda pasa de
    actuar el 86-91 % de esas etiquetas al 0-1 %; con la cara tapada sigue
    actuando.
    """

    def __init__(self, n: int = 5, umbral: float = 3.0, sin_cara_s: float = 0.3) -> None:
        self.umbral = float(umbral)
        self.sin_cara_s = float(sin_cara_s)
        self._saltos: deque[float] = deque(maxlen=int(n))
        self._previo: tuple[float, float] | None = None
        self._ultima_cara: float | None = None
        #: Si en el último paso la cara llevaba perdida ``sin_cara_s`` (P7.7).
        self.sin_cara = False

    def paso(self, ahora: float, crudo: tuple[float, float] | None) -> bool:
        """Los ángulos crudos de este frame (``None`` sin cara). Devuelve si la
        malla falla."""
        if crudo is None:
            self._previo = None
            self.sin_cara = self._ultima_cara is None or ahora - self._ultima_cara >= self.sin_cara_s
            return self.sin_cara
        self.sin_cara = False
        self._ultima_cara = ahora
        if self._previo is not None:
            self._saltos.append(max(abs(crudo[0] - self._previo[0]), abs(crudo[1] - self._previo[1])))
        self._previo = crudo
        if len(self._saltos) < 3:
            return False
        ordenados = sorted(self._saltos)
        return ordenados[len(ordenados) // 2] > self.umbral


#: Ángulos crudos de la cabeza, en grados, dentro de los que su temblor cuenta
#: como ruido de la luz. De perfil la malla tiembla más por sí sola: en
#: p7_final, con la cabeza girada 40-50°, σ llegó a 3.4-4, y habría parado el
#: robot como si faltara luz.
FRONTAL_RUIDO = (20.0, 15.0)

#: Segundos desde la última vez que se vieron las manos sobre la cara durante
#: los que la malla no cuenta para el ruido: tapada se inventa la cara (H58), y
#: BlazePose pierde las manos hasta ~2.5 s seguidos (p7_final).
SIN_MANOS_RUIDO_S = 3.0


def cuenta_para_ruido(crudo: tuple[float, float] | None, ahora: float,
                      ultima_mano_cara: float | None) -> bool:
    """Si el temblor de la malla en este frame es el de la luz (P7.6): hay
    cara, está de frente y hace ``SIN_MANOS_RUIDO_S`` que no hay manos encima."""
    if crudo is None or abs(crudo[0]) > FRONTAL_RUIDO[0] or abs(crudo[1]) > FRONTAL_RUIDO[1]:
        return False
    return ultima_mano_cara is None or ahora - ultima_mano_cara >= SIN_MANOS_RUIDO_S


class VigiaRuido:
    """La zona muerta de la cabeza según el ruido de la malla, y la pausa por
    poca luz (P7.6, decidido con el operador el 2026-11-07).

    La zona es ``por_sigma`` grados por grado de σ (``head_tracker.RuidoMalla``),
    entre ``base`` y ``maximo``. Si haría falta más que ``maximo`` durante
    ``pausa_s`` seguidos, pide pausa; la quita cuando la zona necesaria lleva
    ``reanuda_s`` por debajo de ``margen`` veces el máximo. La histéresis evita
    que el robot entre y salga de la pausa en el borde.
    """

    def __init__(self, base: float, maximo: float, por_sigma: float = 1.0,
                 pausa_s: float = 1.0, reanuda_s: float = 3.0, margen: float = 0.85) -> None:
        self.base, self.maximo, self.por_sigma = float(base), float(maximo), float(por_sigma)
        self.pausa_s, self.reanuda_s, self.margen = float(pausa_s), float(reanuda_s), float(margen)
        self.zona = self.base
        self.pausa = False
        self._exceso_desde: float | None = None
        self._calma_desde: float | None = None

    @classmethod
    def desde_config(cls, cfg: AppConfig) -> "VigiaRuido":
        h = cfg.head
        return cls(h.zona_muerta, h.zona_muerta_max, h.zona_por_ruido,
                   h.pausa_ruido_s, h.reanuda_ruido_s)

    def paso(self, ahora: float, sigma: float | None) -> tuple[float, bool]:
        """``(zona, pausa)`` con la σ de este frame (``None``: sin medida, se
        mantiene lo que había)."""
        if sigma is None:
            return self.zona, self.pausa
        necesaria = self.por_sigma * float(sigma)
        self.zona = min(self.maximo, max(self.base, necesaria))
        if necesaria > self.maximo:
            self._calma_desde = None
            if self._exceso_desde is None:
                self._exceso_desde = ahora
            if ahora - self._exceso_desde >= self.pausa_s:
                self.pausa = True
        else:
            self._exceso_desde = None
            if necesaria < self.margen * self.maximo:
                if self._calma_desde is None:
                    self._calma_desde = ahora
                if self.pausa and ahora - self._calma_desde >= self.reanuda_s:
                    self.pausa = False
            else:
                self._calma_desde = None
        return self.zona, self.pausa


class VigiaCara:
    """La pausa SIN CARA (P7.7, decidida por el operador el 2026-11-07): si la
    malla pierde la cara sin manos encima durante ``pausa_s``, el robot se
    pausa; reanuda cuando la cara lleva ``reanuda_s`` de vuelta.

    Existe porque con la luz apagada del todo la malla no tiembla: pierde la
    cara. En p76 la perdió a los ~20 s de apagar y ya no la recuperó, y el
    índice de ruido de ``VigiaRuido`` no se enteró, porque sin cara no hay nada
    que medir. Con las manos en la cara no cuenta: es lo que se tapa a propósito,
    y lo lleva la guarda.
    """

    def __init__(self, pausa_s: float = 3.0, reanuda_s: float = 1.0) -> None:
        self.pausa_s, self.reanuda_s = float(pausa_s), float(reanuda_s)
        self.pausa = False
        self._perdida_desde: float | None = None
        self._vuelta_desde: float | None = None

    @classmethod
    def desde_config(cls, cfg: AppConfig) -> "VigiaCara":
        return cls(cfg.head.pausa_sin_cara_s, cfg.head.reanuda_cara_s)

    def paso(self, ahora: float, hay_cara: bool, manos_recientes: bool) -> bool:
        if hay_cara:
            self._perdida_desde = None
            if self._vuelta_desde is None:
                self._vuelta_desde = ahora
            if self.pausa and ahora - self._vuelta_desde >= self.reanuda_s:
                self.pausa = False
            return self.pausa
        self._vuelta_desde = None
        if manos_recientes:
            self._perdida_desde = None
            return self.pausa
        if self._perdida_desde is None:
            self._perdida_desde = ahora
        if ahora - self._perdida_desde >= self.pausa_s:
            self.pausa = True
        return self.pausa


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
# La emoción, en su propio hilo (P7.1)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EstadoEmocion:
    """Lo que el bucle necesita de la emoción, leído de una vez tras cada paso
    del clasificador. El bucle lo lee sin tocar el clasificador, que puede
    estar a mitad de una inferencia en el otro hilo."""

    etiqueta: str
    confianza: float | None = None
    cruda: str | None = None
    confianza_cruda: float | None = None
    manual: str | None = None
    manos_en_cara: bool = False
    calibrando: bool = False
    latencia_ms: float | None = None
    rostro: tuple[int, int, int, int] | None = None
    inferencias: int = 0
    calibraciones: int = 0


class HiloEmocion:
    """El ``EmotionClassifier`` fuera del bucle de visión (P7.1).

    Lo pidió el criterio fijado antes de medir: en la sesión del 2026-11-02 los
    frames con inferencia duraban 92 ms (> 66) y el bucle procesaba el 88 % de
    los fps (< 90 %). Aquí el bucle entrega cada frame y sigue; el hilo pasa el
    más reciente por ``process``, que infiere a 5 Hz por su fecha límite como
    siempre. Si llega un frame nuevo mientras infiere, el anterior se descarta:
    interesa la cara de ahora, no una cola. Se lleva también el parón de
    ~0.3 s al terminar cada calibración de la cara.

    Las teclas (forzar expresión, calibrar) se encolan y las aplica el hilo
    antes de su siguiente paso, para no tocar el clasificador a mitad de una
    inferencia: tardan como mucho un frame en verse.

    Con ``en_hilo=False`` hace lo mismo en el bucle, como antes del P7.1
    (``--emocion-en-bucle``): sirve para medir la diferencia.

    ``al_inferir(t, etiqueta, frame)`` se llama tras cada inferencia, en el
    hilo que la hizo; es donde se graba la fila del CSV de emoción.
    """

    def __init__(self, clasificador: EmotionClassifier, *, en_hilo: bool = True,
                 al_inferir=None) -> None:
        self.clasificador = clasificador
        self.en_hilo = en_hilo
        self._al_inferir = al_inferir
        self._cond = threading.Condition()
        self._pendiente = None
        self._ordenes: deque = deque()
        self._parar = False
        self._hilo: threading.Thread | None = None
        self._inferencias = clasificador.inferencias
        self._estado = self._leer()
        self.errores = 0
        self.ultimo_error: str = ""

    @property
    def estado(self) -> EstadoEmocion:
        return self._estado

    def _leer(self) -> EstadoEmocion:
        c = self.clasificador
        emocion = c.emocion
        cruda = c.ultima
        return EstadoEmocion(
            etiqueta=emocion.etiqueta, confianza=emocion.confianza,
            cruda=cruda.etiqueta if cruda is not None else None,
            confianza_cruda=cruda.confianza if cruda is not None else None,
            manual=c.manual, manos_en_cara=c.manos_en_cara,
            calibrando=c.calibrando or c.calibracion_pendiente,
            latencia_ms=c.latencia_ms, rostro=c.rostro,
            inferencias=c.inferencias, calibraciones=c.calibraciones,
        )

    def entregar(self, frame, ahora: float, t: float, etiqueta: int, manos,
                 cabeceo: float | None = None, giro: float | None = None) -> None:
        """El frame (BGR volteado, sin dibujar), los puntos de las manos y el
        cabeceo del head tracker en ese frame (P7.6)."""
        trabajo = (frame, ahora, t, etiqueta, manos, cabeceo, giro)
        if not self.en_hilo:
            self._paso(trabajo)
            return
        with self._cond:
            self._pendiente = trabajo
            self._cond.notify()

    def ordenar(self, metodo: str, *argumentos) -> None:
        """``toggle_manual``, ``clear_manual`` o ``calibrar_neutro``."""
        if not self.en_hilo:
            getattr(self.clasificador, metodo)(*argumentos)
            self._estado = self._leer()
            return
        with self._cond:
            self._ordenes.append((metodo, argumentos))
            self._cond.notify()

    def _paso(self, trabajo) -> None:
        while self._ordenes:
            metodo, argumentos = self._ordenes.popleft()
            getattr(self.clasificador, metodo)(*argumentos)
        if trabajo is not None:
            frame, ahora, t, etiqueta, manos, cabeceo, giro = trabajo
            self.clasificador.process(frame, ahora, manos=manos, cabeceo=cabeceo, giro=giro)
            if self.clasificador.inferencias != self._inferencias:
                self._inferencias = self.clasificador.inferencias
                if self._al_inferir is not None:
                    self._al_inferir(t, etiqueta, frame)
        self._estado = self._leer()

    def _bucle(self) -> None:
        while True:
            with self._cond:
                while self._pendiente is None and not self._ordenes and not self._parar:
                    self._cond.wait()
                if self._parar:
                    return
                trabajo, self._pendiente = self._pendiente, None
            try:
                self._paso(trabajo)
            except Exception as exc:  # noqa: BLE001 -- como en PublicadorPeriodico
                self.errores += 1
                self.ultimo_error = f"{type(exc).__name__}: {exc}"

    def arrancar(self) -> None:
        if self.en_hilo:
            self._parar = False
            self._hilo = threading.Thread(target=self._bucle, name="emocion", daemon=True)
            self._hilo.start()

    def parar(self) -> None:
        with self._cond:
            self._parar = True
            self._cond.notify()
        if self._hilo is not None:
            self._hilo.join(timeout=5.0)
            self._hilo = None


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
    cabeza_tapada: bool = False
    recorte_cabeza: object = None              # HeadClamp | None
    mando: ArmAngles | None = None
    recortes_brazos: dict = field(default_factory=dict)   # {Side: ArmClamp}
    estimado: dict = field(default_factory=dict)          # {Side: bool}
    saludo: dict = field(default_factory=dict)            # {Side: 0..1}
    mano_cara: dict = field(default_factory=dict)         # {Side: 0..1}
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
    errores_emocion: int = 0
    pausa: bool = False
    pausa_luz: bool = False
    pausa_cara: bool = False
    ruido_malla: float | None = None
    zona_muerta: float | None = None
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

    if estado.pausa_cara:
        _texto(lienzo, x, y, "SIN CARA: poca luz o fuera de cuadro", _ROJO, 0.45, 2)
    elif estado.pausa_luz:
        _texto(lienzo, x, y, "POCA LUZ: no se puede continuar", _ROJO, 0.5, 2)
    elif estado.pausa:
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
    elif estado.cabeza_tapada:
        # Lo publicado es la pose guardada, no la medida: sin barras, que son
        # de la medida, y con el aviso (H58).
        filas.append(("pan", estado.cabeza[0], None, True, ""))
        filas.append(("tilt", estado.cabeza[1], None, True, "CARA TAPADA"))
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
            _texto(lienzo, x, y, f"{nombre:<6}{marca}{valor:+7.1f}" + (f"  {nota}" if nota else ""),
                   _ROJO if recortado else (_AMBAR if nota else _BLANCO))
            if eje is not None:
                dibujar_barra(lienzo, x + 140, y, eje.fraction, recortado)
        y += 18
    gestos = []
    for nombre, pesos in (("SALUDO", estado.saludo), ("A LA CARA", estado.mano_cara)):
        activos = [CANALES[c][0] for c in (Side.L, Side.R) if pesos.get(c, 0.0) > 0.0]
        if activos:
            gestos.append(f"{nombre} {' '.join(activos)}")
    if gestos:
        _texto(lienzo, x, y, "  ".join(gestos), _CIAN)
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
        if estado.errores_emocion:
            _texto(lienzo, x, y, f"ERRORES de emocion {estado.errores_emocion}", _ROJO, 0.45, 2)
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
            # El brillo medio no avisa de la poca luz (la exposición automática lo
            # sube); el ruido de la malla sí, y con él la zona de la cabeza (P7.6).
            ruido = ("" if estado.ruido_malla is None
                     else f"   ruido {estado.ruido_malla:.1f}  zona {estado.zona_muerta:.1f}")
            _texto(lienzo, x, y, f"brillo {estado.brillo:.0f}{ruido}",
                   _AMBAR if estado.zona_muerta and estado.zona_muerta > 1.5 else _GRIS)
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
        "--emocion-en-bucle", action="store_true",
        help="La emocion en el bucle de vision, como antes del P7.1, en vez de en "
             "su hilo. Solo para medir la diferencia.",
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
    propios.add_argument(
        "--interfaz", metavar="PUERTO", type=int, nargs="?", const=8001, default=None,
        help="Sirve la interfaz de narración (P10) en http://localhost:PUERTO "
             "(8001 si no se dice): el visor con la cámara, el libro y los botones.",
    )
    propios.add_argument(
        "--abrir-navegador", action="store_true",
        help="Con --interfaz: abre el navegador en cuanto aparece la ventana de "
             "OpenCV, para que el navegador quede delante.",
    )
    propios.add_argument(
        "--guia-sesion", metavar="JSON", type=Path,
        help="Con --interfaz y --record: la instrucción de cada etiqueta, que la "
             "página enseña arriba (sesiones de verificación; la escribe "
             "tools/sesion_verificacion.py).",
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
    guia = None
    if args.guia_sesion is not None:
        if args.interfaz is None:
            parser.error("--guia-sesion necesita --interfaz: la guía la enseña la página")
        from .control_panel import leer_guia

        try:
            guia = leer_guia(args.guia_sesion)
        except ValueError as exc:
            # Antes de abrir la cámara: una sesión de verificación sin su guía no vale.
            print(exc)
            return 1
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
    hilo = None
    interfaz = None
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
        periodico = PublicadorPeriodico(publicador, cfg.rates.publish_period,
                                        rampa_s=cfg.rates.resume_ramp_s,
                                        topic_pagina=cfg.mqtt.topic_page)
        periodico.arrancar()

        if clasificador is not None:
            hilo = HiloEmocion(clasificador, en_hilo=not args.emocion_en_bucle,
                               al_inferir=_grabar_inferencia(clasificador, grabacion))
            hilo.arrancar()

        if args.interfaz is not None:
            interfaz = _crear_interfaz(cfg, args.interfaz, guia)
            if interfaz is None:
                return 1
            print(f"Interfaz de narracion en {interfaz.url} (cuentos de "
                  f"{cfg.paths.stories_dir}).\n")

        _bucle(cfg, cap, cabeza, brazos, hilo, periodico, publicador, grabacion,
               pendientes=interfaz.pendientes if interfaz is not None else None,
               interfaz=interfaz, abrir_navegador=args.abrir_navegador)
    except KeyboardInterrupt:
        print("\nInterrumpido.")
    finally:
        # Cierre limpio en cualquier caso (punto 7): q, Ctrl-C o excepción.
        if interfaz is not None:
            interfaz.parar()
        if periodico is not None:
            periodico.parar()
        if hilo is not None:
            # Antes de cerrar los CSV: el hilo escribe el de emoción.
            hilo.parar()
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


def _crear_interfaz(cfg: AppConfig, puerto: int, guia: dict | None = None):
    """El servidor de la interfaz de narración (P10), ya arrancado, o ``None``
    si el puerto está ocupado (otra sesión abierta)."""
    # Aquí y no arriba: sin --interfaz no se carga nada de esto.
    from .control_panel import CARPETA_VISOR, ServidorInterfaz

    try:
        interfaz = ServidorInterfaz(CARPETA_VISOR, Biblioteca(cfg.paths.stories_dir),
                                    puerto=puerto, guia=guia)
    except OSError as exc:
        print(f"No se pudo abrir la interfaz en el puerto {puerto} ({exc}). "
              f"Hay otra sesion abierta?")
        return None
    if not interfaz.biblioteca.existe:
        print(f"AVISO: no encuentro la carpeta de cuentos {cfg.paths.stories_dir}. "
              f"Usa --stories-dir o TELMEOW_STORIES_DIR.")
    interfaz.arrancar()
    return interfaz


def _grabar_inferencia(clasificador, grabacion):
    """La función que graba cada inferencia en el CSV de emoción (y su recorte)."""
    if grabacion is None or "emocion" not in grabacion.escritores:
        return None

    def grabar(t, etiqueta, frame):
        # El frame que recibió process: volteado y sin dibujar.
        registrar_inferencia(grabacion.escritores["emocion"], t, etiqueta, clasificador,
                             frame, brillo_medio(frame), grabacion.recortes)

    return grabar


def _bucle(cfg, cap, cabeza, brazos, hilo, periodico, publicador, grabacion,
           pendientes=None, interfaz=None, abrir_navegador=False) -> None:
    """El bucle de visión. ``pendientes``, si se da, devuelve en cada frame las
    órdenes que llegaron del navegador (P10); ``interfaz`` recibe el video y el
    estado de cada frame."""
    import mediapipe as mp

    dibujo = mp.solutions.drawing_utils
    conexiones = mp.solutions.pose.POSE_CONNECTIONS
    t0 = time.perf_counter()
    marcas: deque[float] = deque(maxlen=30)
    previo = None
    duplicados = 0
    sesion = Sesion(cabeza, hilo, periodico,
                    biblioteca=Biblioteca(cfg.paths.stories_dir),
                    topic_cuento=cfg.mqtt.topic_story)
    inferencias_vistas = 0
    calibraciones_vistas = 0
    t_anterior = None
    lienzo = None
    muestras_hz: deque[tuple[float, int]] = deque()
    ms_bucle_previo = None
    guarda = GuardaCabeza()
    malla = MallaInestable()
    ruido = RuidoMalla()
    vigia = VigiaRuido.desde_config(cfg)
    vigia_cara = VigiaCara.desde_config(cfg)
    ultima_mano_cara = None
    ultima_caja = None
    # Para el indicador de la interfaz: cuándo se perdió el último mensaje.
    fallos_vistos = (0, 0)
    ultimo_fallo = -math.inf
    navegador_abierto = False

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        ahora = time.perf_counter()
        t_captura_ms = time.time() * 1000.0
        if cfg.camera.flip:
            frame = cv2.flip(frame, 1)
        if interfaz is not None:
            # El BGR volteado y SIN dibujar: el operador se ve como en un espejo,
            # sin esqueleto. Nadie lo modifica después (el panel dibuja en una
            # copia), así que basta la referencia.
            interfaz.emisor.entregar(frame)
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

        t = ahora - t0
        etiqueta_frame = sesion.etiqueta
        manos = (manos_de_blazepose(brazos.landmarks_imagen, ancho, alto)
                 if brazos is not None else None)
        # La cara tapada por las manos (H58): con la caja de la malla de este
        # frame o, si Face Mesh no dio cara, la del último que la dio.
        if cabeza.caja is not None:
            ultima_caja = cabeza.caja
        encima = bool(manos) and ultima_caja is not None and mano_sobre_rostro(
            manos, ultima_caja, MARGEN_MANOS)
        crudo = cabeza.raw_angles if pose_cabeza is not None else None
        malla_falla = malla.paso(ahora, crudo)
        pose_publicada = (guarda.paso(ahora, pose_cabeza, encima, malla_falla, malla.sin_cara)
                          if brazos is not None else pose_cabeza)
        # P7.6: el ruido de la malla; la zona de la cabeza crece con él y, si no
        # basta, se pausa el robot entero. Solo cuenta lo que es temblor de la
        # luz (ver cuenta_para_ruido): de frente y sin manos en la cara.
        if encima:
            ultima_mano_cara = ahora
        if cuenta_para_ruido(crudo, ahora, ultima_mano_cara):
            ruido.actualizar(t, crudo)
        else:
            ruido.cortar()
        zona, pausa_luz = vigia.paso(ahora, ruido.sigma)
        cabeza.zona_muerta = zona
        if pausa_luz != periodico.pausado_por_luz:
            periodico.fijar_pausa_luz(pausa_luz)
            print("POCA LUZ: robot en pausa" if pausa_luz else "luz suficiente: se reanuda")
        manos_recientes = ultima_mano_cara is not None and ahora - ultima_mano_cara < SIN_MANOS_RUIDO_S
        pausa_cara = vigia_cara.paso(ahora, pose_cabeza is not None, manos_recientes)
        if pausa_cara != periodico.pausado_sin_cara:
            periodico.fijar_pausa("cara", pausa_cara)
            print("SIN CARA: robot en pausa" if pausa_cara else "cara de vuelta: se reanuda")

        emocion = None
        ms_emocion = None
        if hilo is not None:
            # El BGR volteado y SIN dibujar (punto 14): es el convenio del
            # entrenamiento, y el recorte que se graba tiene que ser lo que vio.
            hilo.entregar(frame, ahora, t, sesion.etiqueta, manos,
                          cabeceo=crudo[1] if crudo is not None else None,
                          giro=crudo[0] if crudo is not None else None)
            emocion = hilo.estado
            if emocion.inferencias != inferencias_vistas:
                inferencias_vistas = emocion.inferencias
                ms_emocion = emocion.latencia_ms
                if emocion.calibraciones != calibraciones_vistas:
                    calibraciones_vistas = emocion.calibraciones
                    # P7.7: la cabeza toma su neutro con la misma cara, la que
                    # el panel pidió («cara relajada, mirando la pantalla»), y
                    # no con el primer frame: en p76 eso la dejaba mirando ~15°
                    # abajo, y el cabeceo del enojo, 8° desplazado.
                    if cabeza.set_neutral():
                        periodico.suavizar()
                    print("cara neutra calibrada (cabeza y emocion)")

        pan, tilt = pose_publicada if pose_publicada is not None else (None, None)
        campos = {"pan": pan, "tilt": tilt, "t": t_captura_ms}
        if mando is not None:
            campos.update(armL=mando.armL, armLtw=mando.armLtw,
                          armR=mando.armR, armRtw=mando.armRtw)
        if emocion is not None:
            campos["emo"] = emocion.etiqueta
        periodico.actualizar(**campos)

        brillo = brillo_medio(muestra)
        if grabacion is not None:
            if pose_cabeza is not None:
                grabacion.escribir("cabeza", fila_cabeza(cabeza, t, sesion.etiqueta, pose_cabeza, ancho, alto))
            if brazos is not None:
                grabacion.escribir("brazos", fila_brazos(brazos, mando, t, sesion.etiqueta, ancho, alto))

        # -- dibujo: video limpio a la izquierda, panel a la derecha --------
        if lienzo is None or lienzo.shape[:2] != (alto, ancho + ANCHO_PANEL):
            lienzo = np.zeros((alto, ancho + ANCHO_PANEL, 3), dtype=np.uint8)
        video = frame.copy()
        if brazos is not None and brazos.landmarks_imagen is not None:
            dibujo.draw_landmarks(video, brazos.landmarks_imagen, conexiones)
        if emocion is not None and emocion.rostro is not None:
            x, y, w, h = emocion.rostro
            cv2.rectangle(video, (x, y), (x + w, y + h),
                          (0, 190, 255) if emocion.manos_en_cara else (230, 220, 90), 2)
        lienzo[:, :ancho] = video

        sesion.caducar_aviso()
        hz = _hz(muestras_hz, ahora, publicador.message_count)
        estado = EstadoPanel(
            cabeza=pose_publicada,
            cabeza_tapada=guarda.tapada,
            recorte_cabeza=cabeza.last_clamp,
            mando=mando,
            recortes_brazos=brazos.last_clamp if brazos is not None else {},
            estimado=brazos.estimado if brazos is not None else {},
            saludo=brazos.pesos_saludo if brazos is not None else {},
            mano_cara=brazos.pesos_mano_cara if brazos is not None else {},
            brazos_activos=brazos is not None,
            emocion_activa=emocion is not None,
            emocion=emocion.etiqueta if emocion is not None else None,
            confianza=emocion.confianza if emocion is not None else None,
            cruda=emocion.cruda if emocion is not None else None,
            confianza_cruda=emocion.confianza_cruda if emocion is not None else None,
            manual=emocion.manual if emocion is not None else None,
            manos_en_cara=bool(emocion and emocion.manos_en_cara),
            calibrando=bool(emocion and emocion.calibrando),
            latencia_emocion_ms=emocion.latencia_ms if emocion is not None else None,
            errores_emocion=hilo.errores if hilo is not None else 0,
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
            pausa_luz=periodico.pausado_por_luz,
            pausa_cara=periodico.pausado_sin_cara,
            ruido_malla=ruido.sigma,
            zona_muerta=zona,
            brillo=brillo,
            grabando=grabacion is not None,
            etiqueta=sesion.etiqueta,
            aviso=sesion.aviso,
        )
        dibujar_panel(lienzo, ancho, estado)
        # Todo lo del frame salvo esperar a la cámara y el imshow/waitKey:
        # estimadores, grabación y dibujo.
        ms_bucle = ms_bucle_previo = 1000.0 * (time.perf_counter() - ahora)

        cv2.imshow(VENTANA, lienzo)
        if abrir_navegador and interfaz is not None and not navegador_abierto:
            # Después del primer imshow, para que el navegador quede delante de
            # la ventana de OpenCV.
            import webbrowser

            webbrowser.open(interfaz.url)
            navegador_abierto = True
        tecla = cv2.waitKey(1) & 0xFF
        orden = ORDENES_DE_TECLA.get(tecla)
        if orden is not None:
            ejecutar_orden(sesion, orden)
        # Las del navegador (P10), por el mismo camino que las teclas.
        for orden in (pendientes() if pendientes is not None else ()):
            ejecutar_orden(sesion, orden)

        # Después de las órdenes: el gesto y la página quedan en la fila del
        # frame en que salieron.
        if grabacion is not None:
            grabacion.escribir("sesion", [
                f"{t:.4f}", f"{t_captura_ms:.1f}", etiqueta_frame, _f(dt_ms, ".2f"),
                int(duplicado), _f(ms_cabeza, ".2f"), _f(ms_brazos, ".2f"),
                _f(ms_emocion, ".2f"), _f(ms_bucle, ".2f"),
                _f(pan), _f(tilt),
                *(_f(getattr(mando, c)) if mando is not None else ""
                  for c in ("armL", "armLtw", "armR", "armRtw")),
                emocion.etiqueta if emocion is not None else "",
                sesion.gesto_registrado, int(periodico.pausado),
                publicador.message_count, publicador.dropped_count,
                publicador.failed_count, f"{hz:.1f}",
                f"{brillo:.1f}", ancho, alto,
                int(hilo.en_hilo) if hilo is not None else "",
                int(guarda.tapada),
                int(malla_falla), _f(ruido.sigma, ".3f"), f"{zona:.2f}",
                int(periodico.pausado_por_luz),
                int(periodico.pausado_sin_cara),
                sesion.lector.historia.nombre if sesion.lector.abierto else "",
                sesion.lector.pagina if sesion.lector.abierto else "",
                sesion.orden_registrada,
            ])
        if interfaz is not None:
            fallos = (publicador.failed_count, periodico.errores)
            if fallos != fallos_vistos:
                ultimo_fallo, fallos_vistos = ahora, fallos
            interfaz.publicar_estado(_estado_interfaz(
                cfg, publicador, periodico, emocion, sesion, grabacion,
                fallo_reciente=ahora - ultimo_fallo < FALLO_RECIENTE_S))
        sesion.gesto_registrado = ""
        sesion.orden_registrada = ""
        if sesion.salir:
            break


#: Tras perder un mensaje, cuántos segundos sigue en rojo el indicador de la
#: interfaz.
FALLO_RECIENTE_S = 3.0


def _estado_interfaz(cfg, publicador, periodico, emocion, sesion, grabacion, *,
                     fallo_reciente: bool) -> dict:
    """Lo que enseña la página en este frame (``control_panel.estado_interfaz``)."""
    from .control_panel import estado_interfaz

    return estado_interfaz(
        enlace=publicador.connected,
        fallo_reciente=fallo_reciente,
        pausa_tecla=periodico.pausado_por_tecla,
        pausa_luz=periodico.pausado_por_luz,
        pausa_cara=periodico.pausado_sin_cara,
        emocion_activa=emocion is not None,
        expresion=emocion.etiqueta if emocion is not None else None,
        manual=emocion.manual if emocion is not None else None,
        calibrando=bool(emocion and emocion.calibrando),
        lector=sesion.lector,
        grabando=grabacion is not None,
        etiqueta=sesion.etiqueta,
        aviso=sesion.aviso,
        ws_port=cfg.mqtt.ws_port,
        topic_pose=cfg.mqtt.topic_pose,
    )


if __name__ == "__main__":
    raise SystemExit(main())
