"""Pruebas del pipeline integrado (P7), sin cámara, sin MediaPipe y sin broker.

Los tres estimadores son los de verdad —``HeadTracker``, ``ArmTracker`` y
``EmotionClassifier``—; lo simulado es lo que hay debajo: la cámara, las dos
redes de MediaPipe, el detector de caras y el backend de emoción. Así se
comprueba el cableado que el P7 añade (qué frame recibe cada uno, qué sale por
MQTT, qué se graba) sin reescribir los estimadores en las pruebas.

Y lo que se graba se comprueba con las herramientas de ``tools/``, no con una
copia de su lógica: si el CSV de brazos del pipeline no lo reproduce
``reproducir_brazos.py`` o el de emoción no lo reproduce
``analizar_registro_emocion.py``, el pipeline no graba lo que dice.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

import telmeow_teleop.main as principal
from telmeow_teleop.arm_tracker import ArmTracker, canal_del_robot
from telmeow_teleop.config import VIEWER_EMOTIONS, AppConfig
from telmeow_teleop.emotion_classifier import COLUMNAS_CSV as COLUMNAS_EMOCION
from telmeow_teleop.emotion_classifier import EmotionClassifier, mano_sobre_rostro
from telmeow_teleop.head_tracker import COLUMNAS_CSV as COLUMNAS_CABEZA
from telmeow_teleop.arm_tracker import COLUMNAS_CSV as COLUMNAS_BRAZOS
from telmeow_teleop.limits import AxisClamp, Side
from telmeow_teleop.publisher import PosePublisher
from test_confianza_mano import CODO, MANO_COLGANDO, MANO_DOBLADA, escena, tracker_con_escenas
from test_emotion_classifier import FRAME as FRAME_EMOCION
from test_emotion_classifier import _BackendFalso, _clasificador
from test_publisher import ClienteFalso, RelojFalso

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))

import analizar_registro as ar  # noqa: E402
import analizar_registro_emocion as are  # noqa: E402
import reproducir_brazos as rb  # noqa: E402

ANCHO, ALTO = 640, 480


# --------------------------------------------------------------------------
# Lo simulado
# --------------------------------------------------------------------------


class CamaraFalsa:
    """Frames distintos y deterministas, con los canales B y R distintos para
    que un RGB no pase por un BGR."""

    def __init__(self, n, pausa_s=0.0):
        self.n = n
        self.pausa_s = pausa_s
        self.leidos = []
        self.liberada = False

    def read(self):
        if len(self.leidos) >= self.n:
            return False, None
        if self.pausa_s:
            import time

            time.sleep(self.pausa_s)
        rng = np.random.default_rng(len(self.leidos))
        frame = rng.integers(0, 256, size=(ALTO, ANCHO, 3), dtype=np.uint8)
        frame[..., 0] //= 2          # azul más oscuro que el rojo
        self.leidos.append(frame.copy())
        return True, frame

    def get(self, _propiedad):
        return 30.0

    def release(self):
        self.liberada = True


class _Landmark:
    __slots__ = ("x", "y", "z")

    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z


_NUBE = np.random.default_rng(0).normal(scale=0.08, size=(478, 3))


def _cara(k):
    """Una nube de 478 puntos que gira despacio: lo que Face Mesh devolvería."""
    a = math.radians(20 * math.sin(k / 5.0))
    rot = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])
    puntos = _NUBE @ rot.T
    return [_Landmark(0.5 + x, 0.45 + y, z) for x, y, z in puntos]


class Registro:
    """Todo lo que las piezas simuladas ven y hacen, para comprobarlo después."""

    def __init__(self):
        self.malla = []          # frames que recibió Face Mesh
        self.pose = []           # frames que recibió BlazePose
        self.emocion = []        # (frame, manos) que recibió EmotionClassifier.process
        self.cabeceos = []       # el cabeceo que recibió con cada frame (P7.6)
        self.rgb = 0             # conversiones BGR -> RGB
        self.eventos = []        # orden de las llamadas al publicador
        self.publicados = []     # kwargs de cada publish
        self.eventos_mqtt = []   # (topic, datos, retain) de cada publicar_evento (P10)
        self.neutros = 0
        self.calibraciones = 0
        self.ventanas_cerradas = False
        self.fallar_en = None    # frame de Face Mesh en que lanzar
        self.backends_creados = []
        self.cfg = None          # la configuración con que se creó el publicador
        self.cfg_camara = None   # y con la que se abrió la cámara


class PublicadorFalso:
    def __init__(self, registro):
        self.r = registro
        self.message_count = self.dropped_count = self.failed_count = 0
        self.effective_hz = 0.0
        self.connected = False
        self.desconectado = False

    def connect(self):
        self.r.eventos.append("connect")
        return self

    def wait_connected(self, timeout=5.0):
        self.r.eventos.append("wait_connected")
        self.connected = True
        return True

    def publish(self, **campos):
        self.r.eventos.append("publish")
        self.r.publicados.append({k: v for k, v in campos.items() if v is not None})
        self.message_count += 1
        return True

    def publicar_evento(self, topic, datos, retain=False):
        self.r.eventos.append("evento")
        self.r.eventos_mqtt.append((topic, dict(datos), retain))
        return True

    def disconnect(self):
        self.desconectado = True


class PeriodicoSincrono(principal.PublicadorPeriodico):
    """El de verdad, pero sin hilo: un tick por cada pose que deja el bucle.
    Así lo publicado es determinista; la cadencia del hilo se prueba aparte."""

    def arrancar(self):
        pass

    def actualizar(self, **campos):
        super().actualizar(**campos)
        self.tick()


class EmocionSincrona(principal.HiloEmocion):
    """El de verdad, en el modo del bucle (``--emocion-en-bucle``): lo que se
    publica y se graba es determinista. El modo con hilo se prueba aparte."""

    def __init__(self, clasificador, **kw):
        kw["en_hilo"] = False
        super().__init__(clasificador, **kw)


class BackendRelativo(_BackendFalso):
    """Un backend que se calibra con la cara neutra, como el combinado."""

    relativo = True
    calibrado = True

    def calibrar(self, caras):
        self.calibrado = True


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    """``correr(argv, frames, teclas)`` ejecuta ``main`` con todo simulado."""
    import telmeow_teleop.arm_tracker as at
    import telmeow_teleop.head_tracker as ht

    for variable in [v for v in __import__("os").environ if v.startswith("TELMEOW_")]:
        monkeypatch.delenv(variable)
    monkeypatch.setenv("TELMEOW_EMOTION_HZ", "1000")   # una inferencia por frame
    r = Registro()
    estado = {}

    class Malla:
        def __init__(self, **_kw):
            self.k = 0

        def process(self, rgb):
            self.k += 1
            r.malla.append(rgb)
            if r.fallar_en is not None and self.k >= r.fallar_en:
                raise RuntimeError("fallo simulado de Face Mesh")
            return SimpleNamespace(multi_face_landmarks=[SimpleNamespace(landmark=_cara(self.k))])

        def close(self):
            pass

    escenas = [escena(CODO, MANO_DOBLADA), escena(CODO, MANO_COLGANDO)]

    class Pose:
        def __init__(self, **_kw):
            self.k = 0

        def process(self, rgb):
            self.k += 1
            r.pose.append(rgb)
            mundo, imagen = escenas[(self.k // 4) % 2]
            return SimpleNamespace(
                pose_world_landmarks=SimpleNamespace(landmark=mundo),
                pose_landmarks=SimpleNamespace(landmark=imagen),
            )

        def close(self):
            pass

    class Emocion(EmotionClassifier):
        def __init__(self, *a, **k):
            k["detector"] = lambda frame: (200, 120, 160, 160)
            super().__init__(*a, **k)

        def process(self, frame_bgr, timestamp=None, manos=None, cabeceo=None, giro=None):
            r.emocion.append((frame_bgr, manos))
            r.cabeceos.append(cabeceo)
            return super().process(frame_bgr, timestamp, manos=manos, cabeceo=cabeceo, giro=giro)

        def calibrar_neutro(self):
            r.calibraciones += 1
            return super().calibrar_neutro()

    original_neutro = ht.HeadTracker.set_neutral

    def set_neutral(self):
        r.neutros += 1
        return original_neutro(self)

    original_cvt = cv2.cvtColor

    def cvt(imagen, codigo, *a, **k):
        if codigo == cv2.COLOR_BGR2RGB:
            r.rgb += 1
        return original_cvt(imagen, codigo, *a, **k)

    def correr(argv=(), frames=8, teclas=(), backend=None, hilo_real=False, pausa_s=0.0,
               ordenes=None):
        """``ordenes``: una lista por frame con las órdenes que llegan del
        navegador en ese frame (P10)."""
        camara = CamaraFalsa(frames, pausa_s)
        estado["camara"] = camara
        pulsadas = iter(list(teclas))
        backend = backend or _BackendFalso([("feliz", 0.9), ("feliz", 0.9), ("triste", 0.8)])

        def abrir(cfg_camara):
            r.cfg_camara = cfg_camara
            return camara

        def crear_publicador(cfg):
            r.cfg = cfg
            return estado["publicador"]

        def crear(nombre, cfg):
            r.backends_creados.append(nombre)
            return backend

        monkeypatch.setattr(principal, "abrir_camara", abrir)
        monkeypatch.setattr(principal, "describir", lambda cap, cfg: "camara falsa")
        monkeypatch.setattr(principal, "crear_backend_o_reserva", crear)
        monkeypatch.setattr(principal, "EmotionClassifier", Emocion)
        monkeypatch.setattr(principal, "PublicadorPeriodico", PeriodicoSincrono)
        if not hilo_real:
            monkeypatch.setattr(principal, "HiloEmocion", EmocionSincrona)
        estado["publicador"] = PublicadorFalso(r)
        monkeypatch.setattr(principal, "_crear_publicador", crear_publicador)
        if ordenes is not None:
            por_frame = iter(list(ordenes))
            bucle_original = principal._bucle

            def bucle(*a, **k):
                k["pendientes"] = lambda: next(por_frame, [])
                return bucle_original(*a, **k)

            monkeypatch.setattr(principal, "_bucle", bucle)
        monkeypatch.setattr(ht.mp.solutions.face_mesh, "FaceMesh", Malla)
        monkeypatch.setattr(at.mp.solutions.pose, "Pose", Pose)
        monkeypatch.setattr(at.mp.solutions.drawing_utils, "draw_landmarks", lambda *a, **k: None)
        monkeypatch.setattr(ht.HeadTracker, "set_neutral", set_neutral)
        monkeypatch.setattr(cv2, "cvtColor", cvt)
        monkeypatch.setattr(cv2, "imshow", lambda *a: None)
        monkeypatch.setattr(cv2, "waitKey", lambda *a: next(pulsadas, -1))

        def cerrar():
            r.ventanas_cerradas = True

        monkeypatch.setattr(cv2, "destroyAllWindows", cerrar)
        return principal.main(list(argv))

    r.correr = correr
    r.estado = estado
    r.tmp = tmp_path
    return r


# --------------------------------------------------------------------------
# 1. Una cámara, un RGB, y a cada uno lo suyo
# --------------------------------------------------------------------------


def test_una_camara_un_rgb_por_frame_y_la_emocion_recibe_el_bgr_volteado(pipeline):
    assert pipeline.correr(frames=6) == 0
    leidos = pipeline.estado["camara"].leidos
    assert len(leidos) == 6
    # Un solo cvtColor BGR->RGB por frame, y es el MISMO objeto el que reciben
    # las dos redes de MediaPipe: no se convierte dos veces.
    assert pipeline.rgb == 6
    assert all(a is b for a, b in zip(pipeline.malla, pipeline.pose))
    for original, rgb, (bgr, _manos) in zip(leidos, pipeline.malla, pipeline.emocion):
        volteado = cv2.flip(original, 1)
        assert np.array_equal(rgb, volteado[..., ::-1])
        # El clasificador recibe el BGR volteado y SIN dibujar (punto 14).
        assert np.array_equal(bgr, volteado)
        assert not np.array_equal(bgr, rgb)


def test_espera_el_enlace_antes_de_publicar_y_cierra_al_salir(pipeline):
    assert pipeline.correr(frames=4) == 0
    eventos = pipeline.eventos
    assert eventos.index("wait_connected") < eventos.index("publish")
    assert eventos.count("publish") == 4
    assert pipeline.estado["publicador"].desconectado
    assert pipeline.estado["camara"].liberada
    assert pipeline.ventanas_cerradas


def test_publica_un_solo_mensaje_con_los_seis_angulos_y_la_emocion(pipeline):
    assert pipeline.correr(frames=5) == 0
    ultimo = pipeline.publicados[-1]
    assert set(ultimo) == {"pan", "tilt", "armL", "armLtw", "armR", "armRtw", "emo", "t"}
    assert ultimo["emo"] in VIEWER_EMOTIONS


def test_sin_rostro_la_cabeza_mira_al_frente(pipeline, monkeypatch):
    """Hasta el P7.7, sin cara se omitían pan y tilt y el robot se quedaba con la
    última pose: con la luz apagada, en p76, 20 s mirando a un lado. Desde el
    P7.7 la guarda la lleva al frente; sin haber visto nunca una cara, el frente
    es lo único que se puede mandar."""
    import telmeow_teleop.head_tracker as ht

    monkeypatch.setattr(ht.HeadTracker, "process", lambda self, rgb: None)
    assert pipeline.correr(frames=3) == 0
    for mensaje in pipeline.publicados:
        assert (mensaje["pan"], mensaje["tilt"]) == (0.0, 0.0)
        assert "armL" in mensaje           # los brazos nunca tienen huecos


# --------------------------------------------------------------------------
# 2. Teclas
# --------------------------------------------------------------------------


def test_c_calibra_la_cabeza_y_la_emocion_a_la_vez(pipeline):
    backend = BackendRelativo([("feliz", 0.9)])
    assert pipeline.correr(frames=6, teclas=[-1, -1, ord("c")], backend=backend) == 0
    assert pipeline.neutros == 1
    assert pipeline.calibraciones == 1


def test_al_acabar_la_calibracion_la_cabeza_toma_su_neutro(pipeline, monkeypatch):
    """P7.7. Sin pulsar c: la calibración automática del arranque, al acabar,
    toma también el neutro de la cabeza, con la misma cara relajada. Antes la
    cabeza tomaba el primer frame, y en p76 se quedaba mirando ~15° abajo."""
    monkeypatch.setenv("TELMEOW_EMOTION_CALIBRACION_S", "0")
    backend = BackendRelativo([("neutral", 0.9)])
    backend.calibrado = False                                  # como el combinado al arrancar
    assert pipeline.correr(frames=5, backend=backend) == 0
    assert backend.calibrado
    assert pipeline.neutros == 1


def test_con_un_backend_que_no_se_calibra_c_solo_calibra_la_cabeza(pipeline):
    assert pipeline.correr(frames=6, teclas=[-1, -1, ord("c")]) == 0
    assert pipeline.neutros == 1
    assert pipeline.calibraciones == 0


@pytest.mark.parametrize("indice", range(6))
def test_las_teclas_1_a_6_son_las_expresiones_del_visor(pipeline, indice):
    tecla = ord(str(indice + 1))
    assert pipeline.correr(frames=4, teclas=[tecla]) == 0
    assert pipeline.publicados[-1]["emo"] == VIEWER_EMOTIONS[indice]


def test_la_misma_tecla_suelta_la_expresion_y_0_vuelve_a_automatico(pipeline):
    backend = _BackendFalso([("feliz", 0.95)])
    assert pipeline.correr(frames=9, teclas=[ord("6"), -1, ord("6"), ord("2"), -1, ord("0")],
                           backend=backend) == 0
    emos = [m["emo"] for m in pipeline.publicados]
    assert emos[1] == "concentrado" and emos[2] == "concentrado"
    assert emos[3] != "concentrado"       # la misma tecla la suelta
    assert emos[4] == VIEWER_EMOTIONS[1]  # '2' fuerza feliz... que el modelo también ve
    assert emos[-1] == "feliz"            # automático


def test_k_pausa_la_publicacion_y_no_deja_salir_gestos(pipeline):
    teclas = [-1, ord("k"), ord("l"), -1, ord("k"), ord("l"), ord("j")]
    assert pipeline.correr(frames=10, teclas=teclas) == 0
    gestos = [m.get("gesture") for m in pipeline.publicados if m.get("gesture")]
    # El 'l' en pausa no sale; los de después, una vez cada uno y en orden.
    assert gestos == ["page_next", "page_prev"]
    # Frames 0-1 salen; del 2 al 4 en pausa no sale nada; desde el 5, todo.
    assert len(pipeline.publicados) == 2 + 5


def test_q_sale_antes_de_que_se_acaben_los_frames(pipeline):
    assert pipeline.correr(frames=50, teclas=[-1, -1, ord("q")]) == 0
    assert len(pipeline.estado["camara"].leidos) == 3


# --------------------------------------------------------------------------
# 2b. Las órdenes del navegador y los cuentos (P10.3)
# --------------------------------------------------------------------------


def _resultado(pipeline):
    return {
        "frames": len(pipeline.estado["camara"].leidos),
        "publicados": [(m.get("emo"), m.get("gesture"), m.get("page"))
                       for m in pipeline.publicados],
        "neutros": pipeline.neutros,
        "calibraciones": pipeline.calibraciones,
        "eventos": list(pipeline.eventos_mqtt),
    }


@pytest.mark.parametrize("tecla", sorted(principal.ORDENES_DE_TECLA))
def test_cada_orden_del_navegador_hace_lo_mismo_que_su_tecla(pipeline, monkeypatch, tecla):
    """Botón y tecla acaban en el mismo ejecutor. Se comprueba con lo que sale,
    no mirando el código: las mismas poses, los mismos gestos, las mismas
    calibraciones, los mismos frames leídos."""
    secuencia = [-1, tecla, -1, -1, tecla, -1, -1, -1]
    assert pipeline.correr(frames=10, teclas=secuencia,
                           backend=BackendRelativo([("feliz", 0.9), ("triste", 0.8)])) == 0
    con_teclas = _resultado(pipeline)

    pipeline.publicados.clear()
    pipeline.eventos_mqtt.clear()
    pipeline.neutros = pipeline.calibraciones = 0
    orden = principal.ORDENES_DE_TECLA[tecla]
    navegador = principal.Orden(orden.nombre, orden.valor, origen="navegador")
    ordenes = [[navegador] if t == tecla else [] for t in secuencia]
    assert pipeline.correr(frames=10, ordenes=ordenes,
                           backend=BackendRelativo([("feliz", 0.9), ("triste", 0.8)])) == 0
    assert _resultado(pipeline) == con_teclas
    assert con_teclas["frames"] == (2 if orden.nombre == "salir" else 10)


@pytest.fixture
def cuentos(tmp_path, monkeypatch):
    from test_historias import _pdf

    carpeta = tmp_path / "cuentos"
    carpeta.mkdir()
    _pdf(carpeta / "El túnel.pdf", 3)
    _pdf(carpeta / "Cambios.pdf", 2, ancho=612, alto=840)
    monkeypatch.setenv("TELMEOW_STORIES_DIR", str(carpeta))
    return carpeta


def _abrir(nombre="El túnel.pdf"):
    return principal.Orden("historia", nombre, origen="navegador")


def _filas_sesion(ruta):
    import csv

    with open(ruta, encoding="utf-8") as archivo:
        return list(csv.DictReader(archivo))


def test_abrir_un_cuento_lo_anuncia_retenido_con_su_pagina(pipeline, cuentos):
    assert pipeline.correr(frames=4, ordenes=[[], [_abrir()]]) == 0
    assert pipeline.eventos_mqtt == [
        ("telmeow/teleop/story", {"story": "El túnel.pdf", "page": 1}, True),
        ("telmeow/teleop/page", {"page": 1, "total": 3}, True),
    ]
    assert not any("gesture" in m for m in pipeline.publicados)   # abrir no hace gesto


def test_con_cuento_cada_pagina_sale_con_su_gesto_y_queda_en_la_fila(pipeline, cuentos):
    ruta = pipeline.tmp / "sesion.csv"
    teclas = [-1, -1, ord("l"), -1, ord("l"), -1, ord("j"), -1]
    assert pipeline.correr(argv=["--record", str(ruta)], frames=9, teclas=teclas,
                           ordenes=[[], [_abrir()]]) == 0
    con_gesto = [(m["gesture"], m["page"]) for m in pipeline.publicados if "gesture" in m]
    assert con_gesto == [("page_next", 2), ("page_next", 3), ("page_prev", 2)]
    paginas = [d for topic, d, _ in pipeline.eventos_mqtt if topic == "telmeow/teleop/page"]
    assert paginas == [{"page": n, "total": 3} for n in (1, 2, 3, 2)]
    filas = _filas_sesion(ruta)
    # La página cambia exactamente en las filas con gesto: en la del frame en
    # que salió, no en la siguiente.
    previa = ""
    for fila in filas:
        assert (fila["pagina"] != previa) == (fila["gesto"] != "" or fila["orden"].startswith(
            "historia")), fila
        previa = fila["pagina"]
    assert [f["gesto"] for f in filas if f["gesto"]] == ["page_next", "page_next", "page_prev"]
    assert filas[1]["historia"] == "El túnel.pdf" and filas[1]["pagina"] == "1"
    assert filas[1]["orden"] == "historia=El túnel.pdf@navegador"
    assert filas[2]["orden"] == "pagina_siguiente@tecla"


def test_en_pausa_no_se_pasa_de_pagina(pipeline, cuentos):
    """Decisión del operador (2026-11-08): página y gesto siempre juntos."""
    ruta = pipeline.tmp / "sesion.csv"
    teclas = [-1, -1, ord("k"), ord("l"), ord("k"), ord("l"), -1]
    assert pipeline.correr(argv=["--record", str(ruta)], frames=8, teclas=teclas,
                           ordenes=[[], [_abrir()]]) == 0
    filas = _filas_sesion(ruta)
    assert [f["pagina"] for f in filas] == ["", "1", "1", "1", "1", "2", "2", "2"]
    assert not any(f["pausa"] == "1" and f["gesto"] for f in filas)
    assert [(m["gesture"], m["page"]) for m in pipeline.publicados if "gesture" in m] == [
        ("page_next", 2)]


def test_en_los_bordes_del_libro_no_sale_nada(pipeline, cuentos):
    teclas = [-1, -1, ord("j"), ord("l"), ord("l"), -1]
    assert pipeline.correr(frames=7, teclas=teclas, ordenes=[[], [_abrir("Cambios.pdf")]]) == 0
    assert [(m["gesture"], m["page"]) for m in pipeline.publicados if "gesture" in m] == [
        ("page_next", 2)]


@pytest.mark.parametrize("nombre", ["../El túnel.pdf", "no_existe.pdf", None])
def test_un_cuento_que_no_esta_en_la_lista_no_abre_nada(pipeline, cuentos, nombre):
    teclas = [-1, -1, ord("l")]
    assert pipeline.correr(frames=4, teclas=teclas, ordenes=[[], [_abrir(nombre)]]) == 0
    assert pipeline.eventos_mqtt == []
    # Sin cuento, l sigue siendo solo el gesto, como en el P7.
    assert [m.get("page") for m in pipeline.publicados if "gesture" in m] == [None]


def test_otro_cuento_vuelve_a_la_pagina_1(pipeline, cuentos):
    teclas = [-1, -1, ord("l"), -1, -1]
    assert pipeline.correr(frames=6, teclas=teclas,
                           ordenes=[[], [_abrir()], [], [_abrir("Cambios.pdf")]]) == 0
    assert pipeline.eventos_mqtt[-2:] == [
        ("telmeow/teleop/story", {"story": "Cambios.pdf", "page": 1}, True),
        ("telmeow/teleop/page", {"page": 1, "total": 2}, True),
    ]


def test_salir_desde_el_navegador_cierra_limpio(pipeline):
    salir = principal.Orden("salir", origen="navegador")
    assert pipeline.correr(frames=50, ordenes=[[], [], [salir]]) == 0
    assert len(pipeline.estado["camara"].leidos) == 3
    assert pipeline.estado["publicador"].desconectado
    assert pipeline.estado["camara"].liberada


def test_varias_ordenes_en_un_frame_quedan_todas_en_la_fila(pipeline, cuentos):
    ruta = pipeline.tmp / "sesion.csv"
    varias = [_abrir(), principal.Orden("pagina_siguiente", origen="navegador")]
    assert pipeline.correr(argv=["--record", str(ruta)], frames=3, ordenes=[[], varias]) == 0
    fila = _filas_sesion(ruta)[1]
    assert fila["orden"] == "historia=El túnel.pdf@navegador|pagina_siguiente@navegador"
    assert (fila["pagina"], fila["gesto"]) == ("2", "page_next")


# --------------------------------------------------------------------------
# 3. La latencia: el campo t es la hora de CAPTURA
# --------------------------------------------------------------------------


def test_el_campo_t_es_la_hora_de_captura_del_frame(pipeline, monkeypatch):
    horas = iter([1_000.0 + i for i in range(100)])
    monkeypatch.setattr(principal.time, "time", lambda: next(horas))
    assert pipeline.correr(frames=3) == 0
    assert [m["t"] for m in pipeline.publicados] == [1_000_000.0, 1_001_000.0, 1_002_000.0]


def test_el_publisher_manda_t_tal_cual_y_sin_t_se_comporta_como_antes():
    cliente = ClienteFalso()
    reloj = RelojFalso()
    pub = PosePublisher(client_factory=lambda: cliente, clock=reloj)
    pub.publish(pan=1.0, t=1_234_567.8)
    reloj.avanzar(1.0)
    pub.publish(pan=2.0)
    primero, segundo = (json.loads(p["payload"]) for p in cliente.publicaciones)
    assert primero["t"] == 1_234_567
    assert "t" not in segundo


# --------------------------------------------------------------------------
# 4. --no-emotion y --no-arms
# --------------------------------------------------------------------------


def test_sin_emocion_no_se_construye_backend_ni_se_manda_emo(pipeline):
    assert pipeline.correr(argv=["--no-emotion"], frames=3) == 0
    assert pipeline.backends_creados == []
    assert pipeline.emocion == []
    assert all("emo" not in mensaje for mensaje in pipeline.publicados)


def test_con_emocion_se_construye_el_backend_de_la_configuracion(pipeline):
    assert pipeline.correr(frames=1) == 0
    assert pipeline.backends_creados == [AppConfig().emotion.backend]


def test_sin_brazos_no_corre_blazepose_y_la_emocion_no_recibe_manos(pipeline, monkeypatch):
    def prohibido(_cfg):
        raise AssertionError("con --no-arms no se construye el ArmTracker")

    monkeypatch.setattr(ArmTracker, "desde_config", classmethod(lambda cls, cfg: prohibido(cfg)))
    assert pipeline.correr(argv=["--no-arms"], frames=3) == 0
    assert pipeline.pose == []
    assert all(manos is None for _frame, manos in pipeline.emocion)
    assert all("armL" not in mensaje for mensaje in pipeline.publicados)


def test_con_brazos_la_emocion_recibe_las_manos_de_blazepose(pipeline):
    assert pipeline.correr(frames=3) == 0
    _frame, manos = pipeline.emocion[-1]
    assert len(manos) > 0


# --------------------------------------------------------------------------
# 5. Las manos de BlazePose
# --------------------------------------------------------------------------


def test_las_manos_salen_en_el_mismo_pixel_que_el_escorzo_del_arm_tracker():
    """Dos caminos de código independientes: el del pipeline lee los landmarks
    15-22 y los pasa a píxeles; el del ArmTracker hace lo mismo por su cuenta
    para el escorzo (``puntos_imagen``), y ese camino está verificado en el P5.
    Si uno tuviera el frame o el eje al revés, no coincidirían."""
    tracker, reloj, estado = tracker_con_escenas(zona_reposo=0.0)
    reloj["t"] = 0.0
    estado["escena"] = escena(CODO, MANO_DOBLADA)
    tracker.comando(tracker.process(np.zeros((ALTO, ANCHO, 3), dtype=np.uint8)), 0.0)
    puntos = principal.manos_de_blazepose(tracker.landmarks_imagen, ANCHO, ALTO)
    canal = canal_del_robot(Side.L, frame_espejado=False, mirror=True)
    esperado = tracker.puntos_imagen[canal]["mano"]
    # Los cuatro de la mano L (muñeca, meñique, índice, pulgar) van juntos en la
    # escena, en el centroide que usa el tracker.
    for u, v in puntos[:4]:
        assert (u, v) == pytest.approx(esperado, abs=1e-9)


def test_la_mano_fuera_de_cuadro_no_cuenta():
    """BlazePose inventa la mano que no ve (H40): por debajo del cuadro no se
    pasa a la regla de las manos."""
    _mundo, imagen = escena(CODO, MANO_COLGANDO)
    landmarks = SimpleNamespace(landmark=imagen)
    puntos = principal.manos_de_blazepose(landmarks, ANCHO, ALTO)
    assert all(0 <= u <= ANCHO and 0 <= v <= ALTO for u, v in puntos)
    # Los cuatro de la mano L estaban fuera: quedan los cuatro de la R.
    assert len(puntos) == 4


def test_una_mano_de_blazepose_sobre_la_cara_activa_la_regla():
    _mundo, imagen = escena(CODO, MANO_DOBLADA)
    puntos = principal.manos_de_blazepose(SimpleNamespace(landmark=imagen), ANCHO, ALTO)
    u, v = puntos[0]
    rostro = (int(u) - 40, int(v) - 40, 80, 80)
    assert mano_sobre_rostro(puntos, rostro, 0.5)
    assert principal.manos_de_blazepose(None, ANCHO, ALTO) == []


# --------------------------------------------------------------------------
# 6. --record: cuatro CSV que las herramientas leen sin cambios
# --------------------------------------------------------------------------


def _cabecera(ruta):
    return ruta.read_text(encoding="utf-8").splitlines()[0].split(",")


def test_record_escribe_los_cuatro_csv_con_las_columnas_de_los_demos(pipeline):
    ruta = pipeline.tmp / "sesion.csv"
    assert pipeline.correr(argv=["--record", str(ruta)], frames=12,
                           teclas=[-1] * 5 + [ord(" ")]) == 0
    rutas = principal.rutas_de_grabacion(ruta)
    assert _cabecera(rutas["sesion"]) == list(principal.COLUMNAS_SESION)
    assert _cabecera(rutas["cabeza"]) == list(COLUMNAS_CABEZA)
    assert _cabecera(rutas["brazos"]) == list(COLUMNAS_BRAZOS)
    assert _cabecera(rutas["emocion"]) == list(COLUMNAS_EMOCION)
    sesion = rutas["sesion"].read_text(encoding="utf-8").splitlines()[1:]
    assert len(sesion) == 12
    assert {fila.split(",")[2] for fila in sesion} == {"0", "1"}   # ESPACIO


def test_el_csv_de_cabeza_lo_lee_el_analizador(pipeline):
    ruta = pipeline.tmp / "sesion.csv"
    assert pipeline.correr(argv=["--record", str(ruta)], frames=10) == 0
    filas = ar.cargar(principal.rutas_de_grabacion(ruta)["cabeza"])
    assert len(filas) == 10
    assert any(abs(f["raw_pan"]) > 1.0 for f in filas)   # la cara giraba


def test_reproducir_brazos_reproduce_el_comando_que_grabo_el_pipeline(pipeline):
    ruta = pipeline.tmp / "sesion.csv"
    assert pipeline.correr(argv=["--record", str(ruta)], frames=24) == 0
    filas = rb.leer(principal.rutas_de_grabacion(ruta)["brazos"])
    salida = rb.reproducir(filas)
    for canal, diferencia in rb.comparar(filas, salida).items():
        assert diferencia["mediana"] < 0.05, canal
        assert diferencia["p95"] < 1.0, canal


def test_el_analizador_de_emocion_reproduce_exacta_la_histeresis_grabada(pipeline):
    ruta = pipeline.tmp / "sesion.csv"
    backend = _BackendFalso([("feliz", 0.9)] * 4 + [("triste", 0.7)] * 6 + [("feliz", 0.55)] * 5)
    assert pipeline.correr(argv=["--record", str(ruta)], frames=15, backend=backend) == 0
    filas = are.cargar(principal.rutas_de_grabacion(ruta)["emocion"])
    assert len(filas) == 15
    comparacion = are.comparar_con_grabado(filas)
    assert comparacion.comparables == 15
    assert comparacion.distintas == ()


def test_record_no_pisa_la_sesion_anterior(pipeline):
    ruta = pipeline.tmp / "sesion.csv"
    assert pipeline.correr(argv=["--record", str(ruta)], frames=2) == 0
    assert pipeline.correr(argv=["--record", str(ruta)], frames=3) == 0
    for clave, destino in principal.rutas_de_grabacion(ruta).items():
        anterior = destino.with_name(f"{destino.stem}_anterior{destino.suffix}")
        assert anterior.exists(), clave


def test_sin_emocion_ni_brazos_no_se_crean_sus_csv(pipeline):
    ruta = pipeline.tmp / "sesion.csv"
    assert pipeline.correr(argv=["--record", str(ruta), "--no-emotion", "--no-arms"],
                           frames=2) == 0
    rutas = principal.rutas_de_grabacion(ruta)
    assert rutas["sesion"].exists() and rutas["cabeza"].exists()
    assert not rutas["brazos"].exists() and not rutas["emocion"].exists()


def test_guardar_recortes_sin_record_es_un_error(pipeline):
    with pytest.raises(SystemExit):
        pipeline.correr(argv=["--guardar-recortes", str(pipeline.tmp / "caras")], frames=1)


# --------------------------------------------------------------------------
# 7. Cierre limpio ante una excepción
# --------------------------------------------------------------------------


def test_una_excepcion_en_un_tracker_cierra_todo_igual(pipeline):
    pipeline.fallar_en = 3
    ruta = pipeline.tmp / "sesion.csv"
    with pytest.raises(RuntimeError, match="fallo simulado"):
        pipeline.correr(argv=["--record", str(ruta)], frames=10)
    assert pipeline.estado["camara"].liberada
    assert pipeline.estado["publicador"].desconectado
    assert pipeline.ventanas_cerradas
    # En Windows un archivo abierto no se puede renombrar: si se renombra, se
    # cerró. Y lo escrito hasta el fallo está en disco.
    for destino in principal.rutas_de_grabacion(ruta).values():
        destino.rename(destino.with_suffix(".cerrado"))
    assert len((pipeline.tmp / "sesion.cerrado").read_text(encoding="utf-8").splitlines()) == 3


# --------------------------------------------------------------------------
# 8. El publicador a su propio ritmo (H54)
# --------------------------------------------------------------------------


class _PubContador:
    def __init__(self, reloj, coste=0.0):
        self.reloj = reloj
        self.coste = coste
        self.marcas = []
        self.campos = []

    def publish(self, **campos):
        self.marcas.append(self.reloj())
        self.campos.append(campos)
        self.reloj.avanzar(self.coste)
        return True


def _correr_periodico(periodico, reloj, ticks):
    llamadas = {"n": 0}

    def dormir(segundos):
        reloj.avanzar(segundos)
        llamadas["n"] += 1
        if llamadas["n"] >= ticks:
            periodico._parar.set()

    periodico._dormir = dormir
    periodico.bucle()


def test_el_hilo_publica_cada_50_ms():
    reloj = RelojFalso()
    pub = _PubContador(reloj)
    periodico = principal.PublicadorPeriodico(pub, 0.05, reloj=reloj)
    periodico.actualizar(pan=1.0)
    _correr_periodico(periodico, reloj, 20)
    intervalos = np.diff(pub.marcas)
    assert len(pub.marcas) == 20
    assert intervalos == pytest.approx(0.05)


def test_tras_un_paron_no_dispara_las_ranuras_perdidas_en_rafaga():
    """Punto 10: si un envío se atasca 0.3 s, el siguiente sale en cuanto acaba
    el atasco y los demás vuelven a ir cada 50 ms; no salen seis seguidos para
    recuperar las ranuras perdidas."""
    reloj = RelojFalso()
    pub = _PubContador(reloj)
    periodico = principal.PublicadorPeriodico(pub, 0.05, reloj=reloj)
    periodico.actualizar(pan=1.0)
    original = pub.publish

    def publish(**campos):
        resultado = original(**campos)
        if len(pub.marcas) == 5:
            reloj.avanzar(0.3)
        return resultado

    pub.publish = publish
    _correr_periodico(periodico, reloj, 12)
    intervalos = np.diff(pub.marcas)
    assert min(intervalos) == pytest.approx(0.05)
    assert max(intervalos) == pytest.approx(0.3)
    assert sorted(intervalos)[:-1] == pytest.approx([0.05] * (len(intervalos) - 1))


def test_en_pausa_no_sale_nada_y_un_gesto_sale_una_sola_vez():
    reloj = RelojFalso()
    pub = _PubContador(reloj)
    periodico = principal.PublicadorPeriodico(pub, 0.05, reloj=reloj)
    periodico.actualizar(pan=1.0)
    assert periodico.gesto("page_next")
    assert periodico.tick() and periodico.tick()
    assert [c["gesture"] for c in pub.campos] == ["page_next", None]
    assert periodico.alternar_pausa()
    assert not periodico.gesto("page_prev")
    assert not periodico.tick()
    assert not periodico.alternar_pausa()
    assert periodico.tick()
    assert [c["gesture"] for c in pub.campos] == ["page_next", None, None]


def _periodico_real(rc=None):
    """Un PublicadorPeriodico sobre el PosePublisher de verdad y su cliente falso."""
    cliente = ClienteFalso() if rc is None else ClienteFalso(rc=rc)
    reloj = RelojFalso()
    pub = PosePublisher(client_factory=lambda: cliente, clock=reloj)
    periodico = principal.PublicadorPeriodico(pub, 0.05, reloj=reloj,
                                              topic_pagina="telmeow/teleop/page")
    return periodico, pub, cliente, reloj


def test_una_pagina_sale_en_el_acto_con_su_gesto_y_por_su_topic():
    """P10.1: la pose vigente con gesture y page, y después la página retenida
    por su topic. Sin esperar al tick: desde quien llama."""
    periodico, pub, cliente, _ = _periodico_real()
    periodico.actualizar(pan=5.0, tilt=-2.0, emo="feliz")
    assert periodico.evento_pagina("page_next", 4, 32) is True
    assert [p["topic"] for p in cliente.publicaciones] == [
        "telmeow/teleop/pose", "telmeow/teleop/page"]
    pose = json.loads(cliente.publicaciones[0]["payload"])
    assert pose == {"pan": 5.0, "tilt": -2.0, "emo": "feliz", "gesture": "page_next", "page": 4}
    assert cliente.publicaciones[0]["retain"] is False
    assert json.loads(cliente.publicaciones[1]["payload"]) == {"page": 4, "total": 32}
    assert cliente.publicaciones[1]["retain"] is True
    assert (pub.message_count, pub.event_count) == (1, 1)


@pytest.mark.parametrize("motivo", ["tecla", "luz", "cara"])
def test_en_pausa_no_sale_ni_la_pagina_ni_el_gesto(motivo):
    """Decisión del operador (2026-11-08): en pausa no se pasa de página. Ni la
    pose con el gesto ni el mensaje de página."""
    periodico, _, cliente, _ = _periodico_real()
    periodico.actualizar(pan=1.0)
    if motivo == "tecla":
        periodico.alternar_pausa()
    else:
        periodico.fijar_pausa(motivo, True)
    assert periodico.evento_pagina("page_next", 2, 9) is False
    assert cliente.publicaciones == []


def test_si_la_pose_no_sale_tampoco_sale_la_pagina():
    import paho.mqtt.client as mqtt

    periodico, pub, cliente, _ = _periodico_real(rc=mqtt.MQTT_ERR_NO_CONN)
    periodico.actualizar(pan=1.0)
    assert periodico.evento_pagina("page_prev", 1, 9) is False
    assert [p["topic"] for p in cliente.publicaciones] == ["telmeow/teleop/pose"]
    assert pub.failed_count == 1 and pub.event_count == 0


def test_la_pagina_respeta_la_rampa_de_reanudar():
    """Recién salido de una pausa, la pose del gesto va mezclada como la del
    tick: si no, el mensaje de página daría el salto que la rampa evita."""
    periodico, _, cliente, reloj = _periodico_real()
    periodico.rampa_s = 1.0
    periodico.actualizar(pan=0.0)
    periodico.tick()
    periodico.alternar_pausa()
    periodico.actualizar(pan=40.0)
    periodico.alternar_pausa()
    reloj.avanzar(0.5)
    assert periodico.evento_pagina("page_next", 2, 9)
    pan = json.loads(cliente.publicaciones[-2]["payload"])["pan"]
    assert 0.0 < pan < 40.0


def test_la_pagina_espera_a_que_acabe_el_envio_del_hilo():
    """El publisher no tiene candado: si el tick del hilo de 20 Hz está a mitad
    de un envío, la página espera a que acabe en vez de pisarle los contadores."""
    import threading

    dentro = threading.Event()
    soltar = threading.Event()
    orden = []

    class PubLento:
        def publish(self, **campos):
            if campos.get("gesture") is None:
                dentro.set()
                soltar.wait(2.0)
            orden.append(campos.get("gesture") or "pose")
            return True

        def publicar_evento(self, topic, datos, retain=False):
            orden.append(topic)
            return True

    periodico = principal.PublicadorPeriodico(PubLento(), 0.05)
    periodico.actualizar(pan=1.0)
    hilo_tick = threading.Thread(target=periodico.tick)
    hilo_tick.start()
    assert dentro.wait(2.0)
    hilo_pagina = threading.Thread(target=periodico.evento_pagina, args=("page_next", 2, 5))
    hilo_pagina.start()
    hilo_pagina.join(0.2)
    assert hilo_pagina.is_alive()             # esperando el candado de envío
    assert orden == []
    soltar.set()
    hilo_tick.join(2.0)
    hilo_pagina.join(2.0)
    assert orden == ["pose", "page_next", "telmeow/teleop/page"]


def test_publicar_en_cada_frame_a_30_fps_da_15_hz_y_el_hilo_da_20():
    """H54. El publisher descarta lo que llega a menos de 45 ms del anterior: un
    bucle que publica en cada frame a 30 fps pasa uno sí y otro no. Es lo que
    hacían los demos, y lo que el hilo de publicación evita."""
    cliente = ClienteFalso()
    reloj = RelojFalso()
    pub = PosePublisher(client_factory=lambda: cliente, clock=reloj)
    for _frame in range(30):                 # un segundo a 30 fps
        pub.publish(pan=1.0)
        reloj.avanzar(1.0 / 30.0)
    assert pub.message_count == 15
    assert pub.dropped_count == 15

    cliente = ClienteFalso()
    reloj = RelojFalso()
    pub = PosePublisher(client_factory=lambda: cliente, clock=reloj)
    periodico = principal.PublicadorPeriodico(pub, AppConfig().rates.publish_period, reloj=reloj)
    periodico.actualizar(pan=1.0)
    _correr_periodico(periodico, reloj, 20)  # un segundo a 20 Hz
    assert pub.message_count == 20
    assert pub.dropped_count == 0


def test_un_error_al_publicar_no_mata_el_hilo_y_queda_contado():
    """Un hilo de publicación muerto en silencio dejaría al robot quieto con el
    resto del pipeline diciendo que todo va bien."""
    reloj = RelojFalso()
    pub = _PubContador(reloj)
    original = pub.publish

    def publish(**campos):
        if len(pub.marcas) == 2 and not getattr(publish, "fallo", False):
            publish.fallo = True
            raise RuntimeError("broker caído a mitad de envío")
        return original(**campos)

    pub.publish = publish
    periodico = principal.PublicadorPeriodico(pub, 0.05, reloj=reloj)
    periodico.actualizar(pan=1.0)
    _correr_periodico(periodico, reloj, 10)
    assert periodico.errores == 1
    assert "broker caído" in periodico.ultimo_error
    assert len(pub.marcas) == 9


def test_los_hz_salen_del_contador_del_ultimo_segundo():
    muestras = principal.deque()
    hz = 0.0
    for k in range(61):                      # 3 s a 20 frames por segundo
        hz = principal._hz(muestras, k / 20.0, enviados=k)
    assert hz == pytest.approx(20.0)
    assert principal._hz(principal.deque(), 5.0, 3) == 0.0


def test_el_hilo_de_verdad_arranca_y_para():
    pub = _PubContador(RelojFalso())
    periodico = principal.PublicadorPeriodico(pub, 0.01)
    periodico.actualizar(pan=1.0)
    periodico.arrancar()
    import time

    time.sleep(0.1)
    periodico.parar()
    enviados = len(pub.marcas)
    time.sleep(0.05)
    assert enviados >= 3
    assert len(pub.marcas) == enviados       # ya no publica


# --------------------------------------------------------------------------
# 9. El panel
# --------------------------------------------------------------------------


def _estado_con_recorte(recortado: bool) -> principal.EstadoPanel:
    eje = AxisClamp(value=40.0, raw=55.0 if recortado else 40.0, lower=-50.0, upper=40.0,
                    clamped=recortado)
    return principal.EstadoPanel(
        cabeza=(40.0, 0.0),
        recorte_cabeza=SimpleNamespace(pan=eje, tilt=eje),
        brazos_activos=False, emocion="feliz", confianza=0.8, cruda="feliz",
        confianza_cruda=0.7, enlace=True, fps=29.5, brillo=120.0,
    )


def test_el_panel_no_toca_el_video():
    rng = np.random.default_rng(1)
    lienzo = rng.integers(0, 256, size=(ALTO, ANCHO + principal.ANCHO_PANEL, 3), dtype=np.uint8)
    video = lienzo[:, :ANCHO].copy()
    principal.dibujar_panel(lienzo, ANCHO, _estado_con_recorte(False))
    assert np.array_equal(lienzo[:, :ANCHO], video)
    assert not np.array_equal(lienzo[:, ANCHO:], 0)


def test_el_angulo_recortado_se_pinta_en_rojo():
    def rojos(recortado):
        lienzo = np.zeros((ALTO, ANCHO + principal.ANCHO_PANEL, 3), dtype=np.uint8)
        principal.dibujar_panel(lienzo, ANCHO, _estado_con_recorte(recortado))
        panel = lienzo[:, ANCHO:].astype(int)
        return int(np.sum((panel[..., 2] > 200) & (panel[..., 1] < 80) & (panel[..., 0] < 80)))

    assert rojos(True) > rojos(False) + 20


def test_el_panel_pinta_todos_los_estados_sin_fallar():
    lienzo = np.zeros((ALTO, ANCHO + principal.ANCHO_PANEL, 3), dtype=np.uint8)
    for estado in (
        principal.EstadoPanel(),
        principal.EstadoPanel(pausa=True, calibrando=True, emocion_activa=False, brillo=2.0,
                              grabando=True, etiqueta=3, aviso="en pausa: gesto no enviado"),
        principal.EstadoPanel(enlace=True, emocion="triste", manual="triste", manos_en_cara=True,
                              brazos_activos=False, fallidos=4),
    ):
        principal.dibujar_panel(lienzo, ANCHO, estado)


# --------------------------------------------------------------------------
# 10. Línea de comandos
# --------------------------------------------------------------------------


def test_broker_con_y_sin_puerto():
    cfg = AppConfig()
    assert principal._aplicar_broker(cfg, "192.168.1.7:1884").mqtt.host == "192.168.1.7"
    assert principal._aplicar_broker(cfg, "192.168.1.7:1884").mqtt.port == 1884
    assert principal._aplicar_broker(cfg, "otra").mqtt.port == cfg.mqtt.port
    assert principal._aplicar_broker(cfg, None) is cfg


def test_los_argumentos_de_configuracion_llegan_a_la_configuracion(pipeline):
    """``--camera`` y ``--broker`` son del enunciado (punto 6); el primero lo da
    ya ``config.add_cli_args``, y la precedencia defaults < entorno < CLI se
    mantiene."""
    assert pipeline.correr(argv=["--camera", "3", "--broker", "10.0.0.5:1999"], frames=1) == 0
    assert pipeline.cfg_camara.index == 3
    assert (pipeline.cfg.mqtt.host, pipeline.cfg.mqtt.port) == ("10.0.0.5", 1999)


# --------------------------------------------------------------------------
# 11. Reanudar la pausa sin salto (pedido del operador, 2026-11-02)
# --------------------------------------------------------------------------


def _periodico_con_rampa(rampa_s=1.0):
    reloj = RelojFalso()
    pub = _PubContador(reloj)
    return principal.PublicadorPeriodico(pub, 0.05, rampa_s=rampa_s, reloj=reloj), pub, reloj


def test_al_reanudar_los_angulos_van_suaves_de_la_pose_congelada_a_la_actual():
    periodico, pub, reloj = _periodico_con_rampa(1.0)
    periodico.actualizar(pan=0.0, armL=-55.0, emo="neutral")
    periodico.tick()
    periodico.alternar_pausa()
    periodico.actualizar(pan=40.0, armL=-5.0, emo="feliz")   # el operador se movió
    reloj.avanzar(5.0)
    periodico.alternar_pausa()                               # reanuda
    pans = []
    for _ in range(25):
        periodico.tick()
        pans.append(pub.campos[-1]["pan"])
        reloj.avanzar(0.05)
    assert pans[0] == pytest.approx(0.0)                     # arranca donde se quedó
    assert all(b >= a for a, b in zip(pans, pans[1:]))       # sin retroceder
    assert pans[10] == pytest.approx(20.0, abs=1e-9)         # a mitad, la mitad (simétrico)
    assert pans[3] - pans[2] < pans[10] - pans[9]            # arranca despacio
    assert pans[-1] == 40.0                                  # y llega, pasado 1 s
    assert pub.campos[0]["emo"] == "neutral" and pub.campos[1]["emo"] == "feliz"  # la emoción no se mezcla


def test_si_el_operador_se_mueve_durante_la_rampa_la_rampa_lo_sigue():
    periodico, pub, reloj = _periodico_con_rampa(1.0)
    periodico.actualizar(pan=0.0)
    periodico.tick()
    periodico.alternar_pausa()
    periodico.alternar_pausa()
    for k in range(25):
        periodico.actualizar(pan=2.0 * k)                    # va girando
        periodico.tick()
        reloj.avanzar(0.05)
    assert pub.campos[-1]["pan"] == pytest.approx(48.0)      # al final, donde está él


def test_sin_rampa_salta_y_sin_nada_enviado_no_hay_rampa():
    periodico, pub, reloj = _periodico_con_rampa(0.0)
    periodico.actualizar(pan=0.0)
    periodico.tick()
    periodico.alternar_pausa()
    periodico.actualizar(pan=40.0)
    periodico.alternar_pausa()
    periodico.tick()
    assert pub.campos[-1]["pan"] == 40.0

    periodico, pub, reloj = _periodico_con_rampa(1.0)
    periodico.alternar_pausa()
    periodico.actualizar(pan=40.0)
    periodico.alternar_pausa()
    periodico.tick()
    assert pub.campos[-1]["pan"] == 40.0


def test_la_cabeza_sin_rostro_no_entra_en_la_rampa():
    periodico, pub, reloj = _periodico_con_rampa(1.0)
    periodico.actualizar(pan=10.0, armL=-55.0)
    periodico.tick()
    periodico.alternar_pausa()
    periodico.actualizar(pan=None, armL=-5.0)
    periodico.alternar_pausa()
    periodico.tick()
    assert pub.campos[-1]["pan"] is None                     # se omite, como siempre
    assert pub.campos[-1]["armL"] == pytest.approx(-55.0)


def test_paso_suave():
    assert principal.paso_suave(0.0) == 0.0 and principal.paso_suave(1.0) == 1.0
    assert principal.paso_suave(0.5) == pytest.approx(0.5)
    assert principal.paso_suave(-1.0) == 0.0 and principal.paso_suave(2.0) == 1.0


def test_la_rampa_sale_de_la_configuracion():
    assert AppConfig().rates.resume_ramp_s == 1.0
    assert AppConfig.from_env({"TELMEOW_RESUME_RAMP_S": "0.5"}).rates.resume_ramp_s == 0.5


# --------------------------------------------------------------------------
# 12. La emoción en su hilo (P7.1)
# --------------------------------------------------------------------------


def _esperar(condicion, segundos=5.0):
    import time

    limite = time.perf_counter() + segundos
    while time.perf_counter() < limite:
        if condicion():
            return True
        time.sleep(0.005)
    return False


def test_el_hilo_infiere_y_el_estado_se_lee_sin_tocar_el_clasificador():
    clasificador = _clasificador([("feliz", 0.9)], emotion_hz=1000.0)
    grabadas = []
    hilo = principal.HiloEmocion(clasificador, al_inferir=lambda t, e, f: grabadas.append((t, e)))
    hilo.arrancar()
    try:
        for k in range(5):
            hilo.entregar(FRAME_EMOCION, k * 0.2, k * 0.2, 3, None)
            assert _esperar(lambda: hilo.estado.inferencias > k)
        assert _esperar(lambda: hilo.estado.etiqueta == "feliz")
        assert hilo.estado.rostro == (10, 20, 30, 40)
        assert grabadas and all(e == 3 for _t, e in grabadas)
    finally:
        hilo.parar()
    assert hilo._hilo is None


def test_si_llegan_frames_mientras_infiere_se_queda_con_el_ultimo():
    import threading
    import time

    soltar = threading.Event()

    class Lento(_BackendFalso):
        def distribucion(self, rostro):
            soltar.wait(2.0)
            return super().distribucion(rostro)

    clasificador = principal.EmotionClassifier(
        Lento([("feliz", 0.9)]), detector=lambda f: (10, 20, 30, 40),
        frame_espejado=False, emotion_hz=1000.0)
    vistos = []
    original = clasificador.process

    def process(frame, ts=None, manos=None, cabeceo=None, giro=None):
        vistos.append(ts)
        return original(frame, ts, manos=manos, cabeceo=cabeceo, giro=giro)

    clasificador.process = process
    hilo = principal.HiloEmocion(clasificador)
    hilo.arrancar()
    try:
        hilo.entregar(FRAME_EMOCION, 0.0, 0.0, 0, None)
        assert _esperar(lambda: len(vistos) == 1)
        for k in range(1, 10):                               # llegan mientras infiere
            hilo.entregar(FRAME_EMOCION, float(k), float(k), 0, None)
        time.sleep(0.05)
        soltar.set()
        assert _esperar(lambda: vistos[-1] == 9.0)
    finally:
        hilo.parar()
    assert vistos == [0.0, 9.0]                              # los de en medio, descartados


def test_las_teclas_se_aplican_en_el_hilo():
    clasificador = _clasificador([("feliz", 0.9)], emotion_hz=1000.0)
    hilo = principal.HiloEmocion(clasificador)
    hilo.arrancar()
    try:
        hilo.ordenar("toggle_manual", "concentrado")
        assert _esperar(lambda: hilo.estado.manual == "concentrado")
        assert hilo.estado.etiqueta == "concentrado"
        hilo.ordenar("clear_manual")
        assert _esperar(lambda: hilo.estado.manual is None)
    finally:
        hilo.parar()


def test_un_error_en_el_hilo_se_cuenta_y_el_hilo_sigue():
    class Roto(_BackendFalso):
        fallos = 0

        def distribucion(self, rostro):
            if Roto.fallos == 0:
                Roto.fallos += 1
                raise RuntimeError("onnx se quejó")
            return super().distribucion(rostro)

    clasificador = principal.EmotionClassifier(
        Roto([("feliz", 0.9)]), detector=lambda f: (10, 20, 30, 40),
        frame_espejado=False, emotion_hz=1000.0)
    hilo = principal.HiloEmocion(clasificador)
    hilo.arrancar()
    try:
        hilo.entregar(FRAME_EMOCION, 0.0, 0.0, 0, None)
        assert _esperar(lambda: hilo.errores == 1)
        assert "onnx" in hilo.ultimo_error
        hilo.entregar(FRAME_EMOCION, 0.5, 0.5, 0, None)
        assert _esperar(lambda: hilo.estado.inferencias >= 2)
    finally:
        hilo.parar()


def test_el_pipeline_con_el_hilo_de_verdad_graba_y_cierra(pipeline):
    """Sin el modo síncrono del fixture: el hilo de verdad, de punta a punta. Lo
    publicado no es determinista (depende de cuándo acaba cada inferencia), así
    que se comprueba lo que sí: que infiere, que graba, que la sesión lo marca
    y que al salir no queda el hilo vivo."""
    import csv
    import threading

    ruta = pipeline.tmp / "sesion.csv"
    assert pipeline.correr(argv=["--record", str(ruta)], frames=20, hilo_real=True,
                           pausa_s=0.02) == 0
    assert not any(h.name == "emocion" for h in threading.enumerate())
    rutas = principal.rutas_de_grabacion(ruta)
    with open(rutas["emocion"], newline="", encoding="utf-8") as f:
        inferencias = list(csv.DictReader(f))
    with open(rutas["sesion"], newline="", encoding="utf-8") as f:
        sesion = list(csv.DictReader(f))
    assert len(inferencias) >= 3
    assert {fila["emocion_hilo"] for fila in sesion} == {"1"}
    assert sum(1 for fila in sesion if fila["ms_emocion"]) >= 3



# --------------------------------------------------------------------------
# 13. La cabeza con la cara tapada (H58, decidido por el operador el 2026-11-03)
# --------------------------------------------------------------------------


def _guardar(guarda, desde, hasta, paso, pose, encima):
    """Avanza la guarda de ``desde`` a ``hasta``; devuelve las salidas."""
    salidas, t = [], desde
    while t < hasta - 1e-9:
        salidas.append((round(t, 3), guarda.paso(t, pose(t) if callable(pose) else pose, encima(t) if callable(encima) else encima)))
        t += paso
    return salidas


def test_sin_manos_encima_publica_lo_medido():
    g = principal.GuardaCabeza()
    assert g.paso(0.0, (10.0, -5.0), False) == (10.0, -5.0)
    assert g.paso(0.1, None, False) is None             # sin cara: se omite, como siempre
    assert not g.tapada


def test_tapada_se_queda_en_la_pose_de_antes_de_que_face_mesh_empezara_a_inventar():
    """Las manos llegan a la cara un poco antes de que BlazePose las vea encima:
    las últimas poses antes de detectarlas ya son inventadas. Se guarda la de
    0.3 s antes."""
    g = principal.GuardaCabeza()
    _guardar(g, 0.0, 1.0, 0.05, (12.0, 3.0), False)       # quieto mirando así
    _guardar(g, 1.0, 1.2, 0.05, (35.0, 40.0), False)      # empieza a inventar
    salidas = _guardar(g, 1.2, 2.0, 0.05, (-20.0, 25.0), True)
    assert g.tapada
    assert {s for _t, s in salidas} == {(12.0, 3.0)}


def test_tapada_dos_segundos_va_al_frente_con_paso_suave():
    g = principal.GuardaCabeza()
    _guardar(g, 0.0, 1.0, 0.05, (20.0, 10.0), False)
    salidas = dict(_guardar(g, 1.0, 5.0, 0.05, (99.0, 99.0), True))
    assert salidas[2.9] == (20.0, 10.0)                  # hasta los 2 s, quieta
    assert salidas[3.5][0] == pytest.approx(10.0)        # a mitad de la rampa, la mitad
    assert salidas[4.0] == pytest.approx((0.0, 0.0))     # al frente
    pans = [s[0] for t, s in sorted(salidas.items()) if 3.0 <= t <= 4.0]
    assert all(b <= a for a, b in zip(pans, pans[1:]))


def test_al_destaparse_vuelve_a_la_pose_del_operador_en_un_segundo():
    g = principal.GuardaCabeza()
    _guardar(g, 0.0, 1.0, 0.05, (0.0, 0.0), False)
    _guardar(g, 1.0, 2.0, 0.05, None, True)               # tapada 1 s
    # Las manos se van en t = 2: siguen contando 0.5 s, y en 2.5 se destapa.
    salidas = dict(_guardar(g, 2.0, 4.0, 0.05, (30.0, 0.0), False))
    assert salidas[2.45] == (0.0, 0.0)                    # aún retenida
    assert salidas[2.5] == (0.0, 0.0)                     # empieza la vuelta
    assert salidas[3.0][0] == pytest.approx(15.0)         # a mitad, la mitad
    assert salidas[3.55] == (30.0, 0.0)                   # ya con el operador


def test_un_hueco_corto_de_las_manos_no_la_destapa():
    g = principal.GuardaCabeza()
    _guardar(g, 0.0, 1.0, 0.05, (5.0, 5.0), False)
    encima = lambda t: not (1.5 <= t < 1.8)               # BlazePose las pierde 0.3 s
    salidas = _guardar(g, 1.0, 2.5, 0.05, (60.0, 60.0), encima)
    assert all(s == (5.0, 5.0) for _t, s in salidas)


def test_tapada_desde_el_arranque_mira_al_frente():
    g = principal.GuardaCabeza()
    assert g.paso(0.0, (40.0, 40.0), True) == (0.0, 0.0)


def test_la_caja_de_la_cara_es_la_de_la_malla(monkeypatch):
    import telmeow_teleop.head_tracker as ht

    caras = iter([_cara(1), None])

    class Malla:
        def __init__(self, **_kw):
            pass

        def process(self, rgb):
            lms = next(caras)
            return SimpleNamespace(multi_face_landmarks=None if lms is None
                                   else [SimpleNamespace(landmark=lms)])

        def close(self):
            pass

    monkeypatch.setattr(ht.mp.solutions.face_mesh, "FaceMesh", Malla)
    tracker = ht.HeadTracker()
    tracker.process(np.zeros((ALTO, ANCHO, 3), dtype=np.uint8))
    x, y, w, h = tracker.caja
    xs = [lm.x * ANCHO for lm in _cara(1)]
    ys = [lm.y * ALTO for lm in _cara(1)]
    assert (x, y) == (int(min(xs)), int(min(ys)))
    assert w == int(max(xs) - min(xs)) and h == int(max(ys) - min(ys))
    tracker.process(np.zeros((ALTO, ANCHO, 3), dtype=np.uint8))
    assert tracker.caja is None


def test_en_el_pipeline_con_las_manos_en_la_cara_la_cabeza_no_sigue_a_face_mesh(pipeline, monkeypatch):
    """De punta a punta: a partir del frame 4, las manos de BlazePose caen en
    el centro de la caja de la cara. Lo publicado deja de seguir a la malla
    (que sigue girando) y la sesión lo marca."""
    import csv

    llamadas = {"n": 0}

    def manos(landmarks, ancho, alto):
        llamadas["n"] += 1
        return [(ancho * 0.5, alto * 0.45)] if llamadas["n"] > 4 else []

    monkeypatch.setattr(principal, "manos_de_blazepose", manos)
    ruta = pipeline.tmp / "sesion.csv"
    assert pipeline.correr(argv=["--record", str(ruta)], frames=14) == 0
    pans = [m["pan"] for m in pipeline.publicados]
    assert len(set(pans[5:])) == 1                       # quieta mientras está tapada
    assert len(set(pans[:5])) > 1                         # antes seguía a la malla
    with open(principal.rutas_de_grabacion(ruta)["sesion"], newline="", encoding="utf-8") as f:
        marcas = [fila["cabeza_tapada"] for fila in csv.DictReader(f)]
    assert marcas[:4] == ["0"] * 4 and set(marcas[5:]) == {"1"}


def test_el_informe_de_la_sesion_de_verificacion(pipeline, monkeypatch):
    """De punta a punta con el pipeline: se llega a la etiqueta 11 con ESPACIO,
    las manos tapan la cara, y el informe de tools/sesion_verificacion.py lo
    cuenta."""
    sys.path.insert(0, str(RAIZ / "tools"))
    import sesion_verificacion as sv

    llamadas = {"n": 0}

    def manos(landmarks, ancho, alto):
        llamadas["n"] += 1
        return [(ancho * 0.5, alto * 0.45)] if llamadas["n"] > 14 else []

    monkeypatch.setattr(principal, "manos_de_blazepose", manos)
    monkeypatch.setattr(sv, "PROJECT_ROOT", pipeline.tmp)    # sin las sesiones del P6.5
    base = pipeline.tmp / "p7_final"
    teclas = [ord(" ")] * 11 + [-1] * 8                       # del 0 al 11, y ahí se queda
    assert pipeline.correr(argv=["--record", str(base) + ".csv"], frames=24, teclas=teclas) == 0
    informe = sv.analizar(base)
    assert "Cara tapada (etiqueta 11)" in informe
    assert "la guarda actuo en" in informe
    assert "Tasas:" in informe
