"""Pruebas de la interfaz de narración (P10.4): el servidor, el video, el
estado en palabras, el modo demo y el comando de la sesión.

El servidor es el de verdad, en un puerto libre, y se le habla por HTTP como
le hablará el navegador. Los cuentos son PDF en blanco.
"""

from __future__ import annotations

import http.client
import json
import threading
import time
from urllib.parse import quote

import numpy as np
import pytest

from telmeow_teleop import control_panel as cp
from telmeow_teleop.historias import Biblioteca, Lector
from telmeow_teleop.ordenes import Orden
from test_historias import _pdf


# --------------------------------------------------------------------------
# El emisor de video
# --------------------------------------------------------------------------


def _frame(valor=100):
    return np.full((48, 64, 3), valor, dtype=np.uint8)


def test_sin_nadie_mirando_no_se_codifica_nada():
    codificados = []
    emisor = cp.EmisorVideo(codificar=lambda f: codificados.append(f) or b"x")
    emisor.entregar(_frame())
    assert not emisor.paso()
    assert codificados == []


def test_con_alguien_mirando_se_codifica_la_ultima_imagen_una_vez():
    codificados = []
    emisor = cp.EmisorVideo(codificar=lambda f: codificados.append(int(f[0, 0, 0])) or b"jpg")
    emisor.conectar()
    emisor.entregar(_frame(1))
    emisor.entregar(_frame(2))            # la 1 no se llega a codificar: va la última
    assert emisor.paso()
    assert not emisor.paso()              # la misma imagen no se vuelve a codificar
    assert codificados == [2]
    assert emisor.siguiente(0, timeout=0.01) == (1, b"jpg")
    assert emisor.siguiente(1, timeout=0.01) is None


def test_un_frame_que_no_se_codifica_se_cuenta_y_no_para_nada():
    def falla(_frame):
        raise RuntimeError("roto")

    emisor = cp.EmisorVideo(codificar=falla)
    emisor.conectar()
    emisor.entregar(_frame())
    assert not emisor.paso()
    assert emisor.errores == 1


def test_el_jpeg_de_verdad_empieza_por_su_marca():
    emisor = cp.EmisorVideo()
    emisor.conectar()
    emisor.entregar(_frame())
    assert emisor.paso()
    _, jpeg = emisor.siguiente(0, timeout=0.01)
    assert jpeg[:2] == b"\xff\xd8" and jpeg[-2:] == b"\xff\xd9"


def test_el_hilo_no_pasa_de_los_fps_maximos():
    """Con frames llegando a ~100 por segundo, el hilo codifica como mucho
    fps_max por segundo."""
    codificados = []
    emisor = cp.EmisorVideo(fps_max=10.0, codificar=lambda f: codificados.append(1) or b"x")
    emisor.conectar()
    emisor.arrancar()
    try:
        fin = time.perf_counter() + 0.6
        while time.perf_counter() < fin:
            emisor.entregar(_frame())
            time.sleep(0.01)
    finally:
        emisor.parar()
    assert 3 <= len(codificados) <= 8


# --------------------------------------------------------------------------
# El estado, en palabras
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kw, clave, texto", [
    ({"enlace": False, "pausa_tecla": True}, "sin_enlace", "Sin enlace con el robot"),
    ({"enlace": True, "fallo_reciente": True, "pausa_luz": True}, "fallos",
     "Se están perdiendo mensajes"),
    ({"enlace": True, "pausa_luz": True, "pausa_cara": True}, "pausa", "Pausa: poca luz"),
    ({"enlace": True, "pausa_cara": True, "pausa_tecla": True}, "pausa", "Pausa: no te veo"),
    ({"enlace": True, "pausa_tecla": True}, "pausa", "En pausa (k)"),
    ({"enlace": True}, "ok", "Robot conectado"),
])
def test_el_estado_del_robot_va_por_prioridad(kw, clave, texto):
    assert cp.estado_interfaz(**kw)["robot"] == {"clave": clave, "texto": texto}


def test_el_estado_de_la_expresion_y_del_cuento():
    lector = Lector()
    from telmeow_teleop.historias import Historia
    from pathlib import Path

    lector.elegir(Historia("El túnel.pdf", Path("El túnel.pdf"), 32))
    lector.ir(4)
    estado = cp.estado_interfaz(enlace=True, expresion="feliz", manual="concentrado",
                                lector=lector, grabando=True, etiqueta=3, aviso="hola")
    assert estado["expresion"]["texto"] == "Expresión fijada: concentrado"
    assert estado["cuento"] == {"nombre": "El túnel.pdf", "titulo": "El túnel",
                                "pagina": 4, "total": 32}
    assert estado["grabacion"]["texto"] == "Grabando · etiqueta 3"
    assert estado["pausa"] is False and estado["aviso"] == "hola"
    assert estado["mqtt"] == {"ws_port": 9001, "topic_pose": "telmeow/teleop/pose"}
    assert cp.estado_interfaz(enlace=True, calibrando=True)["expresion"]["texto"].startswith(
        "Calibrando")
    assert cp.estado_interfaz(enlace=True, expresion="triste")["expresion"]["texto"] == (
        "Expresión automática: triste")
    assert cp.estado_interfaz(enlace=True, emocion_activa=False)["expresion"]["texto"] == (
        "Expresión: sin reconocimiento")


def test_el_estado_no_lleva_cifras_de_diagnostico():
    """Decisión del operador (2026-11-02): nada de porcentajes, ms ni ángulos."""
    texto = json.dumps(cp.estado_interfaz(enlace=True, expresion="feliz"), ensure_ascii=False)
    for prohibido in ("ms", "%", "Hz", "fps", "pan", "tilt"):
        assert f'"{prohibido}' not in texto


# --------------------------------------------------------------------------
# El servidor
# --------------------------------------------------------------------------


@pytest.fixture
def servidor(tmp_path):
    visor = tmp_path / "visor"
    visor.mkdir()
    (visor / "telmeow_viewer.html").write_text("<html>visor</html>", encoding="utf-8")
    cuentos = tmp_path / "cuentos"
    cuentos.mkdir()
    _pdf(cuentos / "El túnel - Anthony Browne.pdf", 4)
    (tmp_path / "secreto.pdf").write_bytes(b"%PDF fuera de la carpeta")
    s = cp.ServidorInterfaz(visor, Biblioteca(cuentos), puerto=0)
    s.arrancar()
    yield s
    s.parar()


def _pedir(servidor, metodo, ruta, cuerpo=None, cabeceras=None, host=None):
    conexion = http.client.HTTPConnection("127.0.0.1", servidor.puerto, timeout=5)
    cabeceras = dict(cabeceras or {})
    if host is not None:
        conexion.putrequest(metodo, ruta, skip_host=True)
        conexion.putheader("Host", host)
        for k, v in cabeceras.items():
            conexion.putheader(k, v)
        datos = cuerpo.encode("utf-8") if isinstance(cuerpo, str) else cuerpo
        if datos is not None:
            conexion.putheader("Content-Length", str(len(datos)))
        conexion.endheaders(datos)
    else:
        conexion.request(metodo, ruta, body=cuerpo, headers=cabeceras)
    respuesta = conexion.getresponse()
    return respuesta, respuesta.read(), conexion


def _orden(servidor, datos, **kw):
    cabeceras = {"Content-Type": "application/json"}
    cabeceras.update(kw.pop("cabeceras", {}))
    return _pedir(servidor, "POST", "/api/orden", json.dumps(datos), cabeceras, **kw)


def test_la_raiz_lleva_al_modo_narracion(servidor):
    respuesta, _, _ = _pedir(servidor, "GET", "/")
    assert respuesta.status == 302
    assert respuesta.getheader("Location") == "/telmeow_viewer.html?modo=narracion"


def test_sirve_la_carpeta_del_visor(servidor):
    respuesta, cuerpo, _ = _pedir(servidor, "GET", "/telmeow_viewer.html?modo=narracion")
    assert respuesta.status == 200 and cuerpo == b"<html>visor</html>"


def test_el_estado_es_el_ultimo_que_dejo_el_bucle(servidor):
    _, cuerpo, _ = _pedir(servidor, "GET", "/api/estado")
    assert json.loads(cuerpo) == {"listo": False}
    servidor.publicar_estado(cp.estado_interfaz(enlace=True, expresion="feliz"))
    respuesta, cuerpo, _ = _pedir(servidor, "GET", "/api/estado")
    assert respuesta.getheader("Cache-Control") == "no-store"
    assert json.loads(cuerpo)["expresion"]["texto"] == "Expresión automática: feliz"


@pytest.mark.parametrize("datos", [
    {"orden": "pagina_siguiente"},
    {"orden": "expresion", "valor": "concentrado"},
    {"orden": "historia", "valor": "El túnel - Anthony Browne.pdf"},
    {"orden": "salir"},
])
def test_una_orden_valida_entra_a_la_cola_como_del_navegador(servidor, datos):
    respuesta, cuerpo, _ = _orden(servidor, datos)
    assert respuesta.status == 202 and json.loads(cuerpo) == {"ok": True}
    assert servidor.pendientes() == [Orden(datos["orden"], datos.get("valor"), "navegador")]
    assert servidor.pendientes() == []                    # la cola se vacía al leerla


@pytest.mark.parametrize("datos, codigo", [
    ({"orden": "borrar_todo"}, 400),
    ({"orden": "expresion", "valor": "asco"}, 400),
    ({"orden": "pausa", "valor": "x"}, 400),
    ({"orden": "historia"}, 400),
    ({"orden": "historia", "valor": "no_existe.pdf"}, 404),
    ({"orden": "historia", "valor": "../secreto.pdf"}, 404),
    (["pausa"], 400),
])
def test_una_orden_invalida_no_entra(servidor, datos, codigo):
    respuesta, cuerpo, _ = _orden(servidor, datos)
    assert respuesta.status == codigo
    assert json.loads(cuerpo)["ok"] is False
    assert servidor.pendientes() == []


def test_una_orden_que_no_es_json_no_entra(servidor):
    """Un formulario de otra web solo puede mandar text/plain o un form."""
    respuesta, _, _ = _pedir(servidor, "POST", "/api/orden", '{"orden": "salir"}',
                             {"Content-Type": "text/plain"})
    assert respuesta.status == 415
    assert servidor.pendientes() == []


def test_una_orden_de_otro_origen_no_entra(servidor):
    respuesta, _, _ = _orden(servidor, {"orden": "salir"},
                             cabeceras={"Origin": "http://otra-web.example"})
    assert respuesta.status == 403
    respuesta, _, _ = _orden(servidor, {"orden": "pausa"},
                             cabeceras={"Origin": f"http://localhost:{servidor.puerto}"})
    assert respuesta.status == 202
    assert [o.nombre for o in servidor.pendientes()] == ["pausa"]


def test_un_host_ajeno_no_recibe_nada(servidor):
    """Contra el truco de DNS: un nombre ajeno que apunte a 127.0.0.1."""
    respuesta, _, _ = _pedir(servidor, "GET", "/api/estado", host="atacante.example:80")
    assert respuesta.status == 403
    respuesta, _, _ = _orden(servidor, {"orden": "salir"}, host="atacante.example")
    assert respuesta.status == 403
    assert servidor.pendientes() == []


def test_la_lista_de_cuentos(servidor):
    _, cuerpo, _ = _pedir(servidor, "GET", "/api/historias")
    datos = json.loads(cuerpo)
    assert datos["existe"] is True
    assert datos["historias"] == [{"nombre": "El túnel - Anthony Browne.pdf",
                                   "titulo": "El túnel - Anthony Browne",
                                   "paginas": 4, "error": ""}]


def test_sirve_el_pdf_de_la_lista(servidor):
    ruta = servidor.biblioteca.buscar("El túnel - Anthony Browne.pdf").ruta
    respuesta, cuerpo, _ = _pedir(servidor, "GET",
                                  "/historias/" + quote("El túnel - Anthony Browne.pdf"))
    assert respuesta.status == 200
    assert respuesta.getheader("Content-Type") == "application/pdf"
    assert cuerpo == ruta.read_bytes()


@pytest.mark.parametrize("ruta", [
    "/historias/" + quote("../secreto.pdf", safe=""),
    "/historias/..%5Csecreto.pdf",
    "/historias/no_existe.pdf",
    "/historias/",
])
def test_no_sirve_nada_fuera_de_la_lista(servidor, ruta):
    respuesta, cuerpo, _ = _pedir(servidor, "GET", ruta)
    assert respuesta.status == 404
    assert b"PDF fuera" not in cuerpo


def test_el_video_es_mjpeg_con_la_ultima_imagen(servidor):
    servidor.emisor.entregar(_frame(90))
    conexion = http.client.HTTPConnection("127.0.0.1", servidor.puerto, timeout=5)
    conexion.request("GET", "/video.mjpg")
    respuesta = conexion.getresponse()
    assert respuesta.status == 200
    assert respuesta.getheader("Content-Type") == "multipart/x-mixed-replace; boundary=frame"
    # El hilo codifica en cuanto hay alguien mirando; si llegara antes la
    # conexión que el frame, el siguiente frame lo destapa.
    hilo = threading.Thread(target=lambda: [servidor.emisor.entregar(_frame(91))
                                            or time.sleep(0.05) for _ in range(20)])
    hilo.start()
    leido = b""
    while b"\xff\xd9" not in leido:
        leido += respuesta.read1(4096)
    hilo.join()
    assert leido.startswith(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ")
    assert b"\r\n\r\n\xff\xd8" in leido
    conexion.close()


def test_parar_cierra_el_puerto(tmp_path):
    s = cp.ServidorInterfaz(tmp_path, Biblioteca(tmp_path), puerto=0)
    s.arrancar()
    puerto = s.puerto
    s.parar()
    with pytest.raises(OSError):
        http.client.HTTPConnection("127.0.0.1", puerto, timeout=1).request("GET", "/")


def test_un_puerto_ocupado_se_dice_al_construir(servidor, tmp_path):
    with pytest.raises(OSError):
        cp.ServidorInterfaz(tmp_path, Biblioteca(tmp_path), puerto=servidor.puerto)


# --------------------------------------------------------------------------
# El modo demo
# --------------------------------------------------------------------------


class _ServidorFalso:
    def __init__(self, biblioteca):
        self.biblioteca = biblioteca
        self.cola = []
        self.estados = []
        self.emisor = cp.EmisorVideo(codificar=lambda f: b"x")

    def pendientes(self):
        cola, self.cola = self.cola, []
        return cola

    def publicar_estado(self, estado):
        self.estados.append(estado)


def test_la_demo_obedece_las_ordenes_como_el_pipeline(tmp_path):
    _pdf(tmp_path / "a.pdf", 3)
    falso = _ServidorFalso(Biblioteca(tmp_path))
    demo = cp._PipelineDemo(falso)
    falso.cola = [Orden("historia", "a.pdf", "navegador"), Orden("pagina_siguiente")]
    demo.paso(0.0)
    assert falso.estados[-1]["cuento"]["pagina"] == 2
    falso.cola = [Orden("pausa"), Orden("pagina_siguiente")]
    demo.paso(0.1)
    estado = falso.estados[-1]
    assert estado["cuento"]["pagina"] == 2 and estado["pausa"]       # en pausa no pasa
    assert "pausa" in estado["aviso"]
    falso.cola = [Orden("pausa"), Orden("expresion", "triste"), Orden("pagina_siguiente"),
                  Orden("pagina_siguiente")]
    demo.paso(0.2)
    estado = falso.estados[-1]
    assert estado["cuento"]["pagina"] == 3 and "ultima" in estado["aviso"]
    assert estado["expresion"]["manual"] == "triste"
    assert estado["demo"] is True
    falso.cola = [Orden("salir")]
    demo.paso(0.3)
    assert demo.salir


def test_la_demo_arranca_sirve_y_termina(tmp_path):
    hecho = {}

    def correr():
        hecho["codigo"] = cp.ejecutar_demo(puerto=0, carpeta=tmp_path, abrir=False,
                                           duracion_s=0.3)

    hilo = threading.Thread(target=correr)
    hilo.start()
    hilo.join(5.0)
    assert hecho == {"codigo": 0}


# --------------------------------------------------------------------------
# El comando de la sesión
# --------------------------------------------------------------------------


@pytest.fixture
def comando(monkeypatch):
    import telmeow_teleop.broker as broker
    import telmeow_teleop.main as principal

    llamadas = {"pipeline": None, "terminados": 0}

    class Proceso:
        def terminate(self):
            llamadas["terminados"] += 1

    def asegurar(arrancados, **_kw):
        if llamadas.get("lanza"):
            arrancados.append(Proceso())
        return llamadas.get("broker", True)

    def pipeline(argv):
        llamadas["pipeline"] = list(argv)
        return 0

    monkeypatch.setattr(broker, "asegurar_broker", asegurar)
    monkeypatch.setattr(principal, "main", pipeline)
    return llamadas


def test_el_comando_lanza_el_pipeline_con_la_interfaz_y_el_navegador(comando):
    assert cp.main(["--record", "p10.csv", "--camera", "1"]) == 0
    assert comando["pipeline"] == ["--interfaz", "8001", "--abrir-navegador",
                                   "--record", "p10.csv", "--camera", "1"]


def test_el_comando_pasa_la_carpeta_de_cuentos_y_el_puerto(comando, tmp_path):
    assert cp.main(["--sin-navegador", "--puerto", "8123", "--stories-dir", str(tmp_path)]) == 0
    assert comando["pipeline"] == ["--interfaz", "8123", "--stories-dir", str(tmp_path)]


def test_sin_broker_el_comando_no_lanza_el_pipeline(comando):
    comando["broker"] = False
    assert cp.main([]) == 1
    assert comando["pipeline"] is None


def test_el_comando_apaga_el_broker_que_levanto(comando):
    comando["lanza"] = True
    assert cp.main([]) == 0
    assert comando["terminados"] == 1
