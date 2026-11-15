"""La pantalla de los ojos y la cámara del robot (``raspberry/eye_display.py`` y
``raspberry/robot_camera.py``), sin pantalla, sin broker y sin cámara.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from raspberry import eye_display as ojos
from raspberry import robot_camera as cam
from telmeow_teleop.config import VIEWER_EMOTIONS, MQTTConfig

RAIZ = Path(__file__).resolve().parent.parent
VISOR = RAIZ / "telmeow_viewer"


def _esperar(condicion, segundos: float = 5.0) -> bool:
    limite = time.perf_counter() + segundos
    while time.perf_counter() < limite:
        if condicion():
            return True
        time.sleep(0.005)
    return False


# ==========================================================================
# Los ojos
# ==========================================================================


def test_el_topic_y_las_expresiones_son_los_de_la_pc():
    assert ojos.TOPIC_POSE == MQTTConfig.topic_pose
    assert set(ojos.ARCHIVOS) == set(VIEWER_EMOTIONS)


def test_las_imagenes_y_los_alias_son_los_del_visor():
    html = (VISOR / "telmeow_viewer.html").read_text(encoding="utf-8")
    archivos = re.search(r"const EMO_FILES = \{(.*?)\};", html, re.S).group(1)
    assert dict(re.findall(r"(\w+):'([^']+)'", archivos)) == ojos.ARCHIVOS
    alias = re.search(r"const EMO_ALIAS = \{(.*?)\};", html, re.S).group(1)
    assert dict(re.findall(r"(\w+):'([^']+)'", alias)) == ojos.ALIAS
    assert "350" in re.search(r"crossfade de\s+(\d+) ms", html).group(0)
    assert ojos.FUNDIDO_S == 0.35
    for archivo in ojos.ARCHIVOS.values():
        assert (VISOR / archivo).is_file()


def test_no_importan_nada_de_la_pc_ni_entre_ellos():
    for modulo in (ojos, cam):
        fuente = Path(modulo.__file__).read_text(encoding="utf-8")
        assert "import telmeow_teleop" not in fuente and "from telmeow_teleop" not in fuente
        assert "story_display" not in fuente


@pytest.mark.parametrize("valor, esperado", [
    ("feliz", "feliz"), ("FELIZ ", "feliz"), ("happy", "feliz"), ("anger", "enojado"),
    ("surprised", "sorprendido"), ("focused", "concentrado"),
    ("contento", None), ("", None), (None, None), (3, None), (["feliz"], None),
])
def test_nombre_de_la_expresion(valor, esperado):
    assert ojos.nombre_de(valor) == esperado


def test_una_expresion_desconocida_deja_la_que_habia():
    expresion = ojos.Expresion()
    assert expresion.leer() == "neutral"
    assert expresion.pedir("triste")
    assert not expresion.pedir("aburrido")
    assert expresion.leer() == "triste"


class ClienteFalso:
    def __init__(self):
        self.llamadas = []

    def reconnect_delay_set(self, **kw):
        self.llamadas.append(("reconnect_delay_set", kw))

    def connect_async(self, *a):
        self.llamadas.append(("connect_async", a))

    def loop_start(self):
        self.llamadas.append(("loop_start",))

    def subscribe(self, *a):
        self.llamadas.append(("subscribe", a))

    def disconnect(self):
        raise OSError("ya estaba caido")

    def loop_stop(self):
        pass


def _pose(carga) -> SimpleNamespace:
    crudo = carga if isinstance(carga, bytes) else json.dumps(carga).encode()
    return SimpleNamespace(topic=ojos.TOPIC_POSE, payload=crudo)


def test_el_enlace_de_los_ojos_lee_emo_y_su_alias_y_aguanta_la_basura():
    cliente = ClienteFalso()
    expresion = ojos.Expresion()
    enlace = ojos.EnlaceOjos(expresion, "192.168.1.50", 1884, cliente=cliente)
    enlace.conectar()
    assert ("reconnect_delay_set", {"min_delay": 1, "max_delay": 30}) in cliente.llamadas
    assert ("connect_async", ("192.168.1.50", 1884, 30)) in cliente.llamadas
    for _ in range(2):      # se suscribe en cada conexión
        cliente.on_connect(cliente, None, None, SimpleNamespace(is_failure=False))
        cliente.on_disconnect(cliente, None)
    assert cliente.llamadas.count(("subscribe", (ojos.TOPIC_POSE, 0))) == 2

    cliente.on_message(cliente, None, _pose({"pan": 3.0, "emo": "feliz"}))
    assert expresion.leer() == "feliz"
    cliente.on_message(cliente, None, _pose({"pan": 3.0}))            # sin emo: no cambia
    cliente.on_message(cliente, None, _pose({"emo": "rarisima"}))
    for basura in (b"\xff", b"no es json", b"[1]", b"", b"null"):
        cliente.on_message(cliente, None, _pose(basura))
    assert expresion.leer() == "feliz"
    cliente.on_message(cliente, None, _pose({"emotion": "sad"}))
    assert expresion.leer() == "triste"
    enlace.cerrar()         # el cliente falla al desconectar: no debe lanzar


@pytest.fixture
def pantalla_ojos(monkeypatch):
    pytest.importorskip("pygame")
    monkeypatch.setitem(os.environ, "SDL_VIDEODRIVER", "dummy")
    reloj = SimpleNamespace(t=100.0)
    p = ojos.PantallaOjos(ojos.Expresion(), VISOR, ventana=True, reloj=lambda: reloj.t)
    p.reloj_falso = reloj
    yield p
    p.cerrar()


def _centro(pantalla) -> tuple:
    return tuple(pantalla.lienzo.get_at((400, 240)))[:3]


def _foto(pantalla) -> bytes:
    return pantalla.pg.image.tostring(pantalla.lienzo, "RGB")


def test_carga_las_seis_imagenes_del_visor_y_arranca_en_neutral(pantalla_ojos):
    assert set(pantalla_ojos.imagenes) == set(ojos.ARCHIVOS)
    pantalla_ojos.paso()
    assert pantalla_ojos.actual == "neutral"
    neutral = _foto(pantalla_ojos)
    assert len(set(neutral)) > 1                      # no es una pantalla en negro


def test_cada_expresion_pinta_una_imagen_distinta(pantalla_ojos):
    fotos = {}
    for nombre in ojos.ARCHIVOS:
        pantalla_ojos.expresion.pedir(nombre)
        pantalla_ojos.paso()                          # empieza el fundido
        pantalla_ojos.reloj_falso.t += 1.0            # ...y ya pasó
        pantalla_ojos.paso()
        fotos[nombre] = _foto(pantalla_ojos)
    assert len(set(fotos.values())) == 6


def test_el_fundido_dura_350_ms_y_acaba_en_la_imagen_exacta(pantalla_ojos):
    p = pantalla_ojos
    p.paso()
    neutral = _foto(p)
    p.expresion.pedir("feliz")
    p.paso()                                           # empieza el fundido
    assert p.actual == "feliz"
    p.reloj_falso.t += 0.175
    p.paso()
    a_medias = _foto(p)
    p.reloj_falso.t += 0.2
    p.paso()
    feliz = _foto(p)
    assert a_medias != neutral and a_medias != feliz and feliz != neutral
    p.reloj_falso.t += 5.0
    p.paso()
    assert _foto(p) == feliz                           # ya no cambia


def test_sin_la_imagen_de_una_expresion_se_queda_la_que_habia(pantalla_ojos):
    p = pantalla_ojos
    p.paso()
    del p.imagenes["triste"]
    p.expresion.pedir("triste")
    p.reloj_falso.t += 1.0
    p.paso()
    assert p.actual == "neutral"


def test_sin_ninguna_imagen_no_falla(monkeypatch, tmp_path):
    pytest.importorskip("pygame")
    monkeypatch.setitem(os.environ, "SDL_VIDEODRIVER", "dummy")
    p = ojos.PantallaOjos(ojos.Expresion(), tmp_path, ventana=True)
    try:
        p.paso()
        assert p.imagenes == {} and p.actual is None and p.seguir
    finally:
        p.cerrar()


def test_las_teclas_1_a_6_solo_en_el_modo_de_prueba(pantalla_ojos):
    p, pg = pantalla_ojos, pantalla_ojos.pg
    pg.event.post(pg.event.Event(pg.KEYDOWN, key=pg.K_3))
    p.paso()
    assert p.expresion.leer() == "neutral"
    p.teclas = True
    pg.event.post(pg.event.Event(pg.KEYDOWN, key=pg.K_3))
    p.paso()
    assert p.expresion.leer() == "feliz"
    pg.event.post(pg.event.Event(pg.KEYDOWN, key=pg.K_ESCAPE))
    p.paso()
    assert not p.seguir


# ==========================================================================
# La cámara
# ==========================================================================


class FuenteFalsa:
    nombre = "falsa"

    def __init__(self, fotos: int | None = None):
        self.n = 0
        self.fotos = fotos
        self.cerrada = False

    def leer(self):
        time.sleep(0.005)
        if self.fotos is not None and self.n >= self.fotos:
            raise OSError("se desconecto")
        self.n += 1
        return f"foto-{self.n}"

    def cerrar(self):
        self.cerrada = True


def _texto_a_jpeg(imagen) -> bytes:
    return imagen.encode()


def test_ultima_entrega_solo_fotos_nuevas():
    ultima = cam.Ultima()
    assert ultima.edad() is None
    assert ultima.esperar_nueva(0, espera=0.01) == (0, None, 0)
    ultima.poner(b"a")
    numero, jpeg, captura_ms = ultima.esperar_nueva(0)
    assert (numero, jpeg) == (1, b"a") and captura_ms > 0
    assert ultima.esperar_nueva(1, espera=0.01)[1] is None
    threading.Timer(0.02, ultima.poner, [b"b"]).start()
    assert ultima.esperar_nueva(1, espera=2.0)[:2] == (2, b"b")
    assert 0 <= ultima.edad() < 1.0


def test_la_captura_reintenta_sola_si_la_camara_se_cae():
    ultima = cam.Ultima()
    abiertas = []

    def abrir():
        if len(abiertas) == 1:
            abiertas.append(None)
            raise OSError("no hay camara")           # el segundo intento falla al abrir
        fuente = FuenteFalsa(fotos=3 if not abiertas else None)
        abiertas.append(fuente)
        return fuente

    captura = cam.Captura(ultima, abrir, a_jpeg=_texto_a_jpeg, reintento=0.01).empezar()
    try:
        assert _esperar(lambda: len(abiertas) >= 3 and ultima.cuadros > 6)
        assert captura.fallos == 2
        assert abiertas[0].cerrada
        assert captura.fuente == "falsa"
    finally:
        captura.cerrar()
    assert abiertas[2].cerrada


@pytest.fixture
def servidor():
    ultima = cam.Ultima()
    captura = cam.Captura(ultima, FuenteFalsa, a_jpeg=_texto_a_jpeg).empezar()
    s = cam.ServidorCamara(ultima, captura, host="127.0.0.1", puerto=0).empezar()
    assert _esperar(lambda: ultima.cuadros > 0)
    yield f"http://127.0.0.1:{s.puerto}"
    s.cerrar()
    captura.cerrar()


def test_el_estado_dice_si_la_imagen_es_reciente(servidor):
    with urllib.request.urlopen(servidor + "/estado", timeout=3) as r:
        assert r.headers["Access-Control-Allow-Origin"] == "*"
        estado = json.loads(r.read())
    assert estado["cuadros"] > 0 and estado["edad_ms"] < 1000
    assert estado["fuente"] == "falsa" and estado["fallos"] == 0


def test_la_foto_suelta_y_la_ruta_que_no_existe(servidor):
    with urllib.request.urlopen(servidor + "/foto.jpg", timeout=3) as r:
        assert r.headers["Content-Type"] == "image/jpeg"
        assert r.read().startswith(b"foto-")
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(servidor + "/otra", timeout=3)
    assert error.value.code == 404


def test_el_video_manda_fotos_seguidas_y_siempre_nuevas(servidor):
    with urllib.request.urlopen(servidor + "/video.mjpg", timeout=3) as r:
        assert r.headers["Content-Type"].startswith("multipart/x-mixed-replace")
        crudo = b""
        while crudo.count(b"--frame") < 6:
            crudo += r.read(64)
    numeros = [int(n) for n in re.findall(rb"\r\n\r\nfoto-(\d+)", crudo)]
    assert len(numeros) >= 5
    assert numeros == sorted(set(numeros))            # ninguna repetida ni hacia atrás
    assert re.search(rb"X-Captura-Ms: \d{13}", crudo)


def test_sin_imagen_todavia_la_foto_responde_503():
    s = cam.ServidorCamara(cam.Ultima(), host="127.0.0.1", puerto=0).empezar()
    try:
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"http://127.0.0.1:{s.puerto}/foto.jpg", timeout=3)
        assert error.value.code == 503
        with urllib.request.urlopen(f"http://127.0.0.1:{s.puerto}/estado", timeout=3) as r:
            assert json.loads(r.read())["edad_ms"] is None
    finally:
        s.cerrar()


def test_la_camara_de_prueba_da_fotos_jpeg_de_verdad():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    fuente = cam.abrir_fuente("prueba", 320, 240, 30)
    primera, segunda = fuente.leer(), fuente.leer()
    assert primera.shape == (240, 320, 3) and (primera != segunda).any()
    jpeg = cam._a_jpeg(primera, 80)
    assert jpeg[:2] == b"\xff\xd8"
    assert cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR).shape == (240, 320, 3)


def test_un_video_grabado_hace_de_camara_y_se_repite(tmp_path):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    ruta = tmp_path / "ninos.avi"
    escritor = cv2.VideoWriter(str(ruta), cv2.VideoWriter_fourcc(*"MJPG"), 200, (64, 48))
    if not escritor.isOpened():
        pytest.skip("este OpenCV no escribe MJPG")
    for i in range(3):
        escritor.write(np.full((48, 64, 3), 60 * i, np.uint8))
    escritor.release()
    fuente = cam.abrir_fuente(str(ruta), 64, 48, 200)
    try:
        assert fuente.es_archivo
        brillos = [int(fuente.leer().mean()) for _ in range(7)]    # 3 fotos: da la vuelta
    finally:
        fuente.cerrar()
    assert max(brillos[:3]) > 100 and abs(brillos[0] - brillos[3]) < 6


def test_una_camara_que_no_existe_falla_al_abrir_sin_colgarse(tmp_path):
    pytest.importorskip("cv2")
    with pytest.raises(OSError):
        cam.abrir_fuente(str(tmp_path / "no_existe.mp4"), 64, 48, 15)
