"""Pruebas del modo narración del visor (P10.5), leyendo el HTML.

La lógica de la página la prueba la sesión con el operador; aquí, lo que se
puede atar sin navegador: que las teclas de la página son las de Python (dos
caminos de código independientes), que lo que otras herramientas exigen del
HTML sigue en su sitio, y que todo lo que la página pide existe en el servidor.
"""

from __future__ import annotations

import http.client
import re

import pytest

from telmeow_teleop import control_panel as cp
from telmeow_teleop.config import PROJECT_ROOT, VIEWER_EMOTIONS
from telmeow_teleop.historias import Biblioteca
from telmeow_teleop.ordenes import ORDENES, ORDENES_DE_TECLA

HTML = (PROJECT_ROOT / "telmeow_viewer" / "telmeow_viewer.html").read_text(encoding="utf-8")


def _tabla_js() -> dict[str, tuple[str, str | None]]:
    bloque = re.search(r"const TECLAS_NARRACION = \{(.*?)\n  \};", HTML, re.S)
    assert bloque, "no encuentro TECLAS_NARRACION en el visor"
    tabla = {}
    for tecla, orden, valor in re.findall(r"'(.)':\s*\['(\w+)'(?:,\s*'(\w+)')?\]", bloque.group(1)):
        tabla[tecla] = (orden, valor or None)
    return tabla


def test_las_teclas_del_navegador_son_las_de_la_ventana_de_opencv():
    """La tabla de JavaScript y ORDENES_DE_TECLA se escriben por separado: si
    discrepan, una tecla haría otra cosa según dónde estuviera el foco."""
    python = {chr(codigo): (o.nombre, o.valor) for codigo, o in ORDENES_DE_TECLA.items()}
    assert _tabla_js() == python


def test_las_cifras_son_las_expresiones_en_el_orden_de_los_botones():
    tabla = _tabla_js()
    assert [tabla[str(i + 1)][1] for i in range(6)] == list(VIEWER_EMOTIONS)


def test_toda_orden_de_la_pagina_existe_en_python():
    usadas = set(re.findall(r"ordenar\('(\w+)'", HTML)) | {o for o, _ in _tabla_js().values()}
    assert usadas <= set(ORDENES)
    assert {"historia", "pagina_anterior", "pagina_siguiente", "expresion", "automatico",
            "calibrar", "pausa"} <= usadas


def test_el_script_de_modulo_sigue_siendo_el_ultimo_y_el_importmap_no_cambia():
    """tools/barrido_colisiones.mjs inyecta su código antes del ÚLTIMO
    </script> y reescribe el importmap por cadena exacta (skill del visor)."""
    etiquetas = re.findall(r"<script[^>]*>", HTML)
    assert etiquetas[-1] == '<script type="module">'
    for url in ('"three": "https://esm.sh/three@0.160.0"',
                '"three/addons/": "https://esm.sh/three@0.160.0/examples/jsm/"',
                '"three-mesh-bvh": "https://esm.sh/three-mesh-bvh@0.7.8?deps=three@0.160.0"'):
        assert url in HTML


def test_pdfjs_va_fijado_a_una_version_clasica():
    assert "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/" in HTML
    assert "'pdf.min.js'" in HTML and "'pdf.worker.min.js'" in HTML


def test_el_modo_narracion_solo_se_activa_con_su_parametro():
    assert ("const MODO_NARRACION = new URLSearchParams(location.search).get('modo') "
            "=== 'narracion';") in HTML
    assert "if(MODO_NARRACION) iniciarNarracion();" in HTML


def test_la_q_pide_confirmacion():
    """Una q suelta delante de los niños no corta la sesión."""
    assert "narr.confirmarHasta" in HTML and "ordenar('salir')" in HTML
    assert "ordenar(...entrada)" in HTML


def test_en_pausa_los_botones_de_pagina_se_desactivan():
    for boton in ("n-ant", "n-sig"):
        assert re.search(rf"getElementById\('{boton}'\)\.disabled = [^;]*e\.pausa", HTML)


@pytest.fixture
def servidor_real(tmp_path):
    from test_historias import _pdf

    _pdf(tmp_path / "Cuento.pdf", 2)
    s = cp.ServidorInterfaz(cp.CARPETA_VISOR, Biblioteca(tmp_path), puerto=0)
    s.arrancar()
    yield s
    s.parar()


def test_todo_lo_que_la_pagina_pide_lo_sirve_el_servidor(servidor_real):
    """Las rutas salen del propio HTML, no de una lista escrita en la prueba."""
    conexion = http.client.HTTPConnection("127.0.0.1", servidor_real.puerto, timeout=5)
    conexion.request("GET", "/telmeow_viewer.html?modo=narracion")
    respuesta = conexion.getresponse()
    assert respuesta.status == 200
    assert respuesta.read().decode("utf-8") == HTML
    pedidas = set(re.findall(r"fetch\('(/api/\w+)'", HTML))
    assert pedidas == {"/api/estado", "/api/historias", "/api/orden"}
    assert "'/historias/'+encodeURIComponent(nombre)" in HTML
    assert "video.src = '/video.mjpg';" in HTML
    # /api/orden solo admite POST: sus respuestas las prueban test_control_panel.
    for ruta in sorted(pedidas - {"/api/orden"}) + ["/historias/Cuento.pdf"]:
        conexion = http.client.HTTPConnection("127.0.0.1", servidor_real.puerto, timeout=5)
        conexion.request("GET", ruta)
        assert conexion.getresponse().status == 200, ruta
    # Los STL y los ojos, con rutas relativas al HTML: también desde el 8001.
    for archivo in ("Cabeza_Full.stl", "01_NEUTRAL.png"):
        conexion = http.client.HTTPConnection("127.0.0.1", servidor_real.puerto, timeout=5)
        conexion.request("HEAD", "/" + archivo)
        assert conexion.getresponse().status == 200, archivo
