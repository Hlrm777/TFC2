"""La pantalla del cuento de la Raspberry (P11, ``raspberry/story_display.py``).

Sin pantalla ni broker: pygame corre con el controlador de video ``dummy`` (una
pantalla que no existe), el cliente MQTT es uno falso y los PDF se fabrican en
blanco con pypdf, con páginas de proporciones distintas para poder saber cuál
de ellas es la que quedó en pantalla.
"""

from __future__ import annotations

import json
import os
import time
import unicodedata
from pathlib import Path
from types import SimpleNamespace

import pytest

from raspberry import story_display as sd
from telmeow_teleop.config import MQTTConfig

TAMANO = (192, 108)  # una pantalla 16:9 en pequeño


def _pdf(ruta: Path, paginas: list[tuple[int, int]]) -> Path:
    from pypdf import PdfWriter

    escritor = PdfWriter()
    for ancho, alto in paginas:
        escritor.add_blank_page(width=ancho, height=alto)
    with open(ruta, "wb") as archivo:
        escritor.write(archivo)
    return ruta


def _esperar(condicion, segundos: float = 5.0) -> bool:
    limite = time.perf_counter() + segundos
    while time.perf_counter() < limite:
        if condicion():
            return True
        time.sleep(0.005)
    return False


# -- el contrato con la PC ---------------------------------------------------


def test_los_topics_son_los_de_la_pc():
    assert sd.TOPIC_CUENTO == MQTTConfig.topic_story
    assert sd.TOPIC_PAGINA == MQTTConfig.topic_page


def test_no_importa_nada_del_paquete_de_la_pc():
    fuente = Path(sd.__file__).read_text(encoding="utf-8")
    assert "import telmeow_teleop" not in fuente
    assert "from telmeow_teleop" not in fuente


# -- encajar -----------------------------------------------------------------


@pytest.mark.parametrize("pagina, esperado", [
    ((1920, 1080), (0, 0, 1920, 1080)),      # igual que la pantalla
    ((1000, 1000), (420, 0, 1080, 1080)),    # cuadrada: franjas a los lados
    ((4000, 1000), (0, 300, 1920, 480)),     # muy ancha: franjas arriba y abajo
    ((96, 54), (0, 0, 1920, 1080)),          # pequeña: se amplía
])
def test_encajar_mantiene_la_proporcion_y_centra(pagina, esperado):
    assert sd.encajar(*pagina, 1920, 1080) == esperado


def test_encajar_no_revienta_con_medidas_imposibles():
    assert sd.encajar(0, 0, 1920, 1080) == (0, 0, 1920, 1080)


# -- buscar el cuento ----------------------------------------------------------


def test_buscar_solo_acepta_nombres_exactos_de_la_carpeta(tmp_path):
    _pdf(tmp_path / "uno.pdf", [(100, 100)])
    (tmp_path / "dentro").mkdir()
    _pdf(tmp_path / "dentro" / "dos.pdf", [(100, 100)])
    assert sd.buscar_cuento(tmp_path, "uno.pdf") == tmp_path / "uno.pdf"
    for malo in ("dos.pdf", "dentro/dos.pdf", "../uno.pdf", str(tmp_path / "uno.pdf"),
                 "uno", "", None, 7):
        assert sd.buscar_cuento(tmp_path, malo) is None


def test_buscar_no_distingue_la_forma_de_las_tildes(tmp_path):
    compuesto = unicodedata.normalize("NFC", "El túnel.pdf")
    suelto = unicodedata.normalize("NFD", "El túnel.pdf")
    assert compuesto != suelto
    _pdf(tmp_path / suelto, [(100, 100)])
    assert sd.buscar_cuento(tmp_path, compuesto) is not None


def test_buscar_sin_carpeta_no_falla(tmp_path):
    assert sd.buscar_cuento(tmp_path / "no_existe", "uno.pdf") is None


# -- el guion ------------------------------------------------------------------


def test_un_cuento_nuevo_empieza_en_su_pagina():
    guion = sd.Guion()
    assert guion.leer() == (None, 1)
    assert guion.al_cuento({"story": "a.pdf", "page": 1})
    assert guion.a_la_pagina({"page": 5, "total": 9})
    assert guion.leer() == ("a.pdf", 5)
    assert guion.al_cuento({"story": "b.pdf", "page": 1})
    assert guion.leer() == ("b.pdf", 1)


def test_al_arrancar_la_pagina_retenida_vale_aunque_llegue_antes_que_el_cuento():
    guion = sd.Guion()
    guion.a_la_pagina({"page": 7, "total": 9})
    assert guion.leer() == (None, 1)
    guion.al_cuento({"story": "a.pdf", "page": 1})
    assert guion.leer() == ("a.pdf", 7)


def test_el_mismo_cuento_repetido_al_reconectar_no_vuelve_a_la_pagina_1():
    guion = sd.Guion()
    guion.al_cuento({"story": "a.pdf", "page": 1})
    guion.a_la_pagina({"page": 7, "total": 9})
    guion.al_cuento({"story": "a.pdf", "page": 1})   # el retenido, otra vez
    assert guion.leer() == ("a.pdf", 7)


@pytest.mark.parametrize("malo", [None, [], "x", {}, {"story": ""}, {"story": 3}])
def test_un_mensaje_de_cuento_sin_sentido_se_ignora(malo):
    guion = sd.Guion()
    guion.al_cuento({"story": "a.pdf", "page": 1})
    assert not guion.al_cuento(malo)
    assert guion.leer() == ("a.pdf", 1)


@pytest.mark.parametrize("malo", [None, [], {}, {"page": 0}, {"page": -2}, {"page": "3"},
                                  {"page": 2.5}, {"page": True}])
def test_un_mensaje_de_pagina_sin_sentido_se_ignora(malo):
    guion = sd.Guion()
    guion.al_cuento({"story": "a.pdf", "page": 1})
    guion.a_la_pagina({"page": 4})
    assert not guion.a_la_pagina(malo)
    assert guion.leer() == ("a.pdf", 4)


def test_las_flechas_no_se_salen_del_libro():
    guion = sd.Guion()
    guion.al_cuento({"story": "a.pdf", "page": 1})
    guion.mover(-1, 3)
    assert guion.leer()[1] == 1
    for _ in range(5):
        guion.mover(+1, 3)
    assert guion.leer()[1] == 3


# -- los píxeles ---------------------------------------------------------------


def test_a_rgb_quita_el_relleno_de_fila_y_la_transparencia():
    # 2x2 con cuatro canales y dos bytes de relleno por fila
    fila1 = bytes([1, 2, 3, 255, 4, 5, 6, 255, 0, 0])
    fila2 = bytes([7, 8, 9, 255, 10, 11, 12, 255, 0, 0])
    assert sd._a_rgb(fila1 + fila2, 2, 2, 10, 4) == bytes(range(1, 13))
    assert sd._a_rgb(bytes([5, 9]), 2, 1, 2, 1) == bytes([5, 5, 5, 9, 9, 9])
    assert sd._a_rgb(bytes(range(6)), 2, 1, 6, 3) == bytes(range(6))


def test_dibujar_pdf_escala_cada_pagina_a_la_pantalla(tmp_path):
    pytest.importorskip("pypdfium2")
    ruta = _pdf(tmp_path / "a.pdf", [(400, 400), (1600, 400)])
    total, dibujar, cerrar = sd.dibujar_pdf(ruta, TAMANO)
    try:
        assert total == 2
        cuadrada, ancha = dibujar(0), dibujar(1)
        assert (cuadrada.ancho, cuadrada.alto) == (108, 108)
        assert (ancha.ancho, ancha.alto) == (192, 48)
        assert len(ancha.datos) == 192 * 48 * 3
        assert set(ancha.datos) == {255}          # una página en blanco es blanca
    finally:
        cerrar()


# -- el prerenderizado -----------------------------------------------------------


def _abridor_falso(orden: list, paginas: int = 6, rotas: tuple = (), demora: float = 0.0):
    def abrir(ruta, tamano):
        if ruta.name == "roto.pdf":
            raise ValueError("no es un PDF")

        def dibujar(indice):
            time.sleep(demora)
            orden.append((ruta.name, indice + 1))
            if indice + 1 in rotas:
                raise RuntimeError("pagina danada")
            return sd.Imagen(1, 1, bytes([indice + 1] * 3))

        return paginas, dibujar, lambda: None
    return abrir


def test_dibuja_todas_las_paginas_empezando_por_la_pedida_y_sus_vecinas():
    orden: list = []
    pre = sd.Prerenderizador(TAMANO, _abridor_falso(orden))
    try:
        pre.pedir(Path("a.pdf"), 4)
        assert _esperar(lambda: pre.progreso() == (6, 6))
        assert [p for _, p in orden] == [4, 5, 3, 6, 1, 2]
        assert pre.imagen(Path("a.pdf"), 4).datos == bytes([4] * 3)
        assert pre.imagen(Path("otro.pdf"), 4) is None
    finally:
        pre.cerrar()


def test_un_cuento_nuevo_tira_las_paginas_del_anterior():
    orden: list = []
    pre = sd.Prerenderizador(TAMANO, _abridor_falso(orden, demora=0.01))
    try:
        pre.pedir(Path("a.pdf"), 1)
        assert _esperar(lambda: pre.imagen(Path("a.pdf"), 1) is not None)
        pre.pedir(Path("b.pdf"), 2)
        assert pre.imagen(Path("a.pdf"), 1) is None
        assert _esperar(lambda: pre.progreso() == (6, 6))
        de_b = [p for nombre, p in orden if nombre == "b.pdf"]
        assert de_b[0] == 2 and sorted(de_b) == [1, 2, 3, 4, 5, 6]
    finally:
        pre.cerrar()


def test_una_pagina_que_no_se_dibuja_no_para_las_demas():
    pre = sd.Prerenderizador(TAMANO, _abridor_falso([], rotas=(2,)))
    try:
        pre.pedir(Path("a.pdf"), 1)
        assert _esperar(lambda: pre.progreso() == (6, 6))
        assert pre.imagen(Path("a.pdf"), 2) is None
        assert pre.imagen(Path("a.pdf"), 3) is not None
    finally:
        pre.cerrar()


def test_un_pdf_que_no_abre_no_mata_el_hilo():
    pre = sd.Prerenderizador(TAMANO, _abridor_falso([]))
    try:
        pre.pedir(Path("roto.pdf"), 1)
        time.sleep(0.05)
        assert pre.progreso() == (0, 0)
        pre.pedir(Path("a.pdf"), 1)
        assert _esperar(lambda: pre.progreso() == (6, 6))
    finally:
        pre.cerrar()


# -- el enlace -------------------------------------------------------------------


class ClienteFalso:
    def __init__(self):
        self.llamadas = []

    def reconnect_delay_set(self, **kw):
        self.llamadas.append(("reconnect_delay_set", kw))

    def connect_async(self, *a):
        self.llamadas.append(("connect_async", a))

    def loop_start(self):
        self.llamadas.append(("loop_start",))

    def subscribe(self, topics):
        self.llamadas.append(("subscribe", topics))

    def disconnect(self):
        raise OSError("ya estaba caido")

    def loop_stop(self):
        self.llamadas.append(("loop_stop",))


def _mensaje(topic: str, carga) -> SimpleNamespace:
    crudo = carga if isinstance(carga, bytes) else json.dumps(carga).encode()
    return SimpleNamespace(topic=topic, payload=crudo)


def test_el_enlace_conecta_sin_bloquear_y_reintenta_solo():
    cliente = ClienteFalso()
    enlace = sd.Enlace(sd.Guion(), "192.168.1.50", 1884, cliente=cliente)
    enlace.conectar()
    assert ("reconnect_delay_set", {"min_delay": 1, "max_delay": 30}) in cliente.llamadas
    assert ("connect_async", ("192.168.1.50", 1884, 30)) in cliente.llamadas
    assert ("loop_start",) in cliente.llamadas


def test_se_suscribe_en_cada_conexion_para_recibir_los_retenidos():
    cliente = ClienteFalso()
    enlace = sd.Enlace(sd.Guion(), "x", cliente=cliente)
    for _ in range(2):   # la primera y una reconexión
        cliente.on_connect(cliente, None, None, SimpleNamespace(is_failure=False))
        cliente.on_disconnect(cliente, None, None, None, None)
    suscripciones = [c for c in cliente.llamadas if c[0] == "subscribe"]
    assert suscripciones == [("subscribe", [(sd.TOPIC_CUENTO, 0), (sd.TOPIC_PAGINA, 0)])] * 2
    assert not enlace.conectado


def test_los_mensajes_llegan_al_guion_y_la_basura_no_rompe_nada():
    cliente = ClienteFalso()
    guion = sd.Guion()
    sd.Enlace(guion, "x", cliente=cliente)
    cliente.on_message(cliente, None, _mensaje(sd.TOPIC_CUENTO, {"story": "a.pdf", "page": 1}))
    cliente.on_message(cliente, None, _mensaje(sd.TOPIC_PAGINA, {"page": 3, "total": 9}))
    for basura in (b"\xff\xfe", b"no es json", b"[1,2]", b""):
        cliente.on_message(cliente, None, _mensaje(sd.TOPIC_PAGINA, basura))
        cliente.on_message(cliente, None, _mensaje(sd.TOPIC_CUENTO, basura))
    cliente.on_message(cliente, None, _mensaje("otro/topic", {"page": 9}))
    assert guion.leer() == ("a.pdf", 3)


def test_cerrar_el_enlace_no_falla_aunque_el_cliente_falle():
    sd.Enlace(sd.Guion(), "x", cliente=ClienteFalso()).cerrar()


def test_partir_el_broker():
    assert sd._partir_broker("192.168.1.50") == ("192.168.1.50", 1883)
    assert sd._partir_broker("pc-narrador.local:1884") == ("pc-narrador.local", 1884)


# -- la pantalla, con un video que no existe ---------------------------------------


@pytest.fixture
def pantalla(tmp_path, monkeypatch):
    pytest.importorskip("pygame")
    pytest.importorskip("pypdfium2")
    monkeypatch.setitem(os.environ, "SDL_VIDEODRIVER", "dummy")
    # Cada página con su proporción: (ancho, alto) dibujado en una pantalla 192x108.
    _pdf(tmp_path / "a.pdf", [(400, 400), (1600, 400), (200, 400)])   # 108x108, 192x48, 54x108
    _pdf(tmp_path / "b.pdf", [(1600, 900)])                            # 192x108
    (tmp_path / "roto.pdf").write_bytes(b"esto no es un PDF")
    guion = sd.Guion()
    p = sd.Pantalla(guion, tmp_path, ventana=True)
    p.tamano = TAMANO
    p.pre.tamano = TAMANO
    yield p
    p.cerrar()


def _hasta_mostrar(pantalla, cuento, pagina) -> bool:
    def listo():
        pantalla.paso()
        return pantalla.mostrada == (cuento, pagina)
    return _esperar(listo)


def _tamano_en_pantalla(pantalla) -> tuple[int, int]:
    return pantalla._pagina_en_pantalla[0].get_size()


def test_sin_cuento_no_muestra_nada(pantalla):
    pantalla.paso()
    assert pantalla.mostrada is None


def test_muestra_la_pagina_que_dice_el_guion(pantalla):
    pantalla.guion.al_cuento({"story": "a.pdf", "page": 1})
    assert _hasta_mostrar(pantalla, "a.pdf", 1)
    assert _tamano_en_pantalla(pantalla) == (108, 108)
    pantalla.guion.a_la_pagina({"page": 2, "total": 3})
    assert _hasta_mostrar(pantalla, "a.pdf", 2)
    assert _tamano_en_pantalla(pantalla) == (192, 48)


def test_pasar_de_pagina_con_todo_dibujado_es_inmediato(pantalla):
    pantalla.guion.al_cuento({"story": "a.pdf", "page": 1})
    assert _hasta_mostrar(pantalla, "a.pdf", 1)
    assert _esperar(lambda: pantalla.pre.progreso() == (3, 3))
    pantalla.guion.a_la_pagina({"page": 3, "total": 3})
    pantalla.paso()                       # una sola vuelta, sin esperar
    assert pantalla.mostrada == ("a.pdf", 3)
    assert _tamano_en_pantalla(pantalla) == (54, 108)


@pytest.mark.parametrize("cuento", ["no_esta.pdf", "roto.pdf", "../a.pdf"])
def test_un_cuento_que_falta_o_esta_roto_deja_la_pagina_que_habia(pantalla, cuento):
    pantalla.guion.al_cuento({"story": "a.pdf", "page": 2})
    assert _hasta_mostrar(pantalla, "a.pdf", 2)
    pantalla.guion.al_cuento({"story": cuento, "page": 1})
    for _ in range(20):
        pantalla.paso()
        time.sleep(0.005)
    assert pantalla.mostrada == ("a.pdf", 2)
    assert _tamano_en_pantalla(pantalla) == (192, 48)
    assert pantalla.seguir
    # ...y un cuento bueno después sigue funcionando
    pantalla.guion.al_cuento({"story": "b.pdf", "page": 1})
    assert _hasta_mostrar(pantalla, "b.pdf", 1)


def test_una_pagina_fuera_del_libro_deja_la_que_habia(pantalla):
    pantalla.guion.al_cuento({"story": "a.pdf", "page": 1})
    assert _hasta_mostrar(pantalla, "a.pdf", 1)
    pantalla.guion.a_la_pagina({"page": 99, "total": 3})
    for _ in range(20):
        pantalla.paso()
        time.sleep(0.005)
    assert pantalla.mostrada == ("a.pdf", 1)


def test_el_cuento_que_faltaba_aparece_al_copiarlo_sin_reiniciar(pantalla, monkeypatch):
    pantalla.guion.al_cuento({"story": "nuevo.pdf", "page": 1})
    pantalla.paso()
    assert pantalla.mostrada is None
    _pdf(pantalla.carpeta / "nuevo.pdf", [(1600, 900)])
    reloj = time.monotonic()
    monkeypatch.setattr(sd.time, "monotonic", lambda: reloj + 3.0)   # pasan los 2 s
    assert _hasta_mostrar(pantalla, "nuevo.pdf", 1)


def test_un_fallo_dentro_de_una_vuelta_no_cierra_la_pantalla(pantalla, monkeypatch):
    vueltas = []

    def paso_roto():
        vueltas.append(1)
        if len(vueltas) >= 3:
            pantalla.seguir = False
        raise RuntimeError("algo inesperado")

    monkeypatch.setattr(pantalla, "paso", paso_roto)
    pantalla.bucle()                      # no lanza
    assert len(vueltas) == 3


def test_las_flechas_solo_pasan_de_pagina_en_el_modo_de_prueba(pantalla):
    pg = pantalla.pg
    pantalla.guion.al_cuento({"story": "a.pdf", "page": 1})
    assert _hasta_mostrar(pantalla, "a.pdf", 1)
    pg.event.post(pg.event.Event(pg.KEYDOWN, key=pg.K_RIGHT))
    pantalla.paso()
    assert pantalla.guion.leer()[1] == 1          # con MQTT manda el broker
    pantalla.flechas = True
    pg.event.post(pg.event.Event(pg.KEYDOWN, key=pg.K_RIGHT))
    assert _hasta_mostrar(pantalla, "a.pdf", 2)
    pg.event.post(pg.event.Event(pg.KEYDOWN, key=pg.K_ESCAPE))
    pantalla.paso()
    assert not pantalla.seguir
