"""Pruebas de la lista de cuentos y del estado de lectura (P10.2).

Los PDF se fabrican en blanco con pypdf: los libros álbum de verdad tienen
derechos de autor y no van en git.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from telmeow_teleop.config import PROJECT_ROOT, AppConfig, PathConfig, load
from telmeow_teleop.historias import Biblioteca, Historia, Lector, contar_paginas


def _pdf(ruta: Path, paginas: int, ancho: float = 720, alto: float = 540) -> Path:
    from pypdf import PdfWriter

    escritor = PdfWriter()
    for _ in range(paginas):
        escritor.add_blank_page(width=ancho, height=alto)
    with open(ruta, "wb") as archivo:
        escritor.write(archivo)
    return ruta


@pytest.fixture
def carpeta(tmp_path):
    _pdf(tmp_path / "El túnel - Anthony Browne.pdf", 5)
    _pdf(tmp_path / "Cambios.PDF", 3, ancho=612, alto=840)       # vertical, extensión en mayúsculas
    _pdf(tmp_path / "Los tres cerditos - David Wiesner .pdf", 4)  # espacio antes de .pdf
    (tmp_path / "roto.pdf").write_bytes(b"esto no es un pdf")
    (tmp_path / "notas.txt").write_text("no es un cuento", encoding="utf-8")
    (tmp_path / "subcarpeta.pdf").mkdir()                          # una carpeta, no un archivo
    return tmp_path


def test_cuenta_las_paginas_con_pypdf(tmp_path):
    assert contar_paginas(_pdf(tmp_path / "a.pdf", 7)) == 7


def test_lista_solo_los_pdf_por_orden_alfabetico(carpeta):
    nombres = [h.nombre for h in Biblioteca(carpeta).listar()]
    assert nombres == ["Cambios.PDF", "El túnel - Anthony Browne.pdf",
                       "Los tres cerditos - David Wiesner .pdf", "roto.pdf"]


def test_cada_cuento_trae_sus_paginas_y_un_titulo_sin_extension(carpeta):
    por_nombre = {h.nombre: h for h in Biblioteca(carpeta).listar()}
    tunel = por_nombre["El túnel - Anthony Browne.pdf"]
    assert tunel.paginas == 5 and tunel.legible and tunel.error == ""
    assert tunel.titulo == "El túnel - Anthony Browne"
    assert por_nombre["Los tres cerditos - David Wiesner .pdf"].titulo == (
        "Los tres cerditos - David Wiesner")
    assert por_nombre["Cambios.PDF"].paginas == 3
    assert tunel.como_dict() == {"nombre": "El túnel - Anthony Browne.pdf",
                                 "titulo": "El túnel - Anthony Browne",
                                 "paginas": 5, "error": ""}


def test_un_pdf_roto_sale_con_su_error_y_no_tumba_la_lista(carpeta):
    roto = Biblioteca(carpeta).buscar("roto.pdf")
    assert roto is not None
    assert roto.paginas is None and not roto.legible
    assert roto.error


def test_la_cache_no_vuelve_a_abrir_un_pdf_que_no_cambio(carpeta):
    abiertos = []

    def contador(ruta):
        abiertos.append(ruta.name)
        return contar_paginas(ruta)

    biblioteca = Biblioteca(carpeta, contador=contador)
    biblioteca.listar()
    primera = len(abiertos)
    biblioteca.listar()
    assert len(abiertos) == primera
    # Al cambiar un archivo, se vuelve a abrir ese y solo ese.
    tunel = carpeta / "El túnel - Anthony Browne.pdf"
    _pdf(tunel, 6)
    os.utime(tunel, ns=(tunel.stat().st_atime_ns, tunel.stat().st_mtime_ns + 10**9))
    assert biblioteca.buscar("El túnel - Anthony Browne.pdf").paginas == 6
    assert abiertos[primera:] == ["El túnel - Anthony Browne.pdf"]


def test_un_cuento_borrado_desaparece_de_la_lista(carpeta):
    biblioteca = Biblioteca(carpeta)
    biblioteca.listar()
    (carpeta / "Cambios.PDF").unlink()
    assert biblioteca.buscar("Cambios.PDF") is None


@pytest.mark.parametrize("nombre", [
    "../roto.pdf", "..\\roto.pdf", "subcarpeta.pdf", "notas.txt", "", "cambios.pdf",
])
def test_buscar_solo_acepta_nombres_exactos_de_la_lista(carpeta, nombre):
    """Ningún nombre que llegue de la página puede salir de la carpeta."""
    assert Biblioteca(carpeta).buscar(nombre) is None


def test_sin_carpeta_la_lista_esta_vacia(tmp_path):
    biblioteca = Biblioteca(tmp_path / "no_existe")
    assert not biblioteca.existe
    assert biblioteca.listar() == []
    assert biblioteca.buscar("a.pdf") is None


# --------------------------------------------------------------------------
# El estado de lectura
# --------------------------------------------------------------------------


def _historia(paginas=3):
    return Historia("cuento.pdf", Path("cuento.pdf"), paginas)


def test_sin_cuento_no_hay_destino():
    lector = Lector()
    assert not lector.abierto
    assert lector.destino(+1) is None and lector.destino(-1) is None
    assert lector.total is None
    with pytest.raises(ValueError):
        lector.ir(1)


def test_elegir_abre_en_la_pagina_1():
    lector = Lector()
    lector.elegir(_historia(3))
    assert (lector.pagina, lector.total) == (1, 3)


def test_destino_no_mueve_la_pagina_y_respeta_los_bordes():
    lector = Lector()
    lector.elegir(_historia(3))
    assert lector.destino(-1) is None          # en la primera no hay anterior
    assert lector.destino(+1) == 2
    assert lector.pagina == 1                  # destino solo calcula
    lector.ir(2)
    lector.ir(3)
    assert lector.destino(+1) is None          # en la última no hay siguiente
    assert lector.destino(-1) == 2


def test_ir_fuera_del_libro_falla():
    lector = Lector()
    lector.elegir(_historia(3))
    for pagina in (0, 4):
        with pytest.raises(ValueError):
            lector.ir(pagina)


def test_un_cuento_sin_paginas_no_se_puede_elegir():
    lector = Lector()
    with pytest.raises(ValueError):
        lector.elegir(Historia("roto.pdf", Path("roto.pdf"), None, "PdfReadError"))
    assert not lector.abierto


def test_elegir_otro_cuento_vuelve_a_la_pagina_1():
    lector = Lector()
    lector.elegir(_historia(5))
    lector.ir(4)
    lector.elegir(Historia("otro.pdf", Path("otro.pdf"), 2))
    assert (lector.historia.nombre, lector.pagina, lector.total) == ("otro.pdf", 1, 2)


# --------------------------------------------------------------------------
# La carpeta en la configuración
# --------------------------------------------------------------------------


def test_la_carpeta_por_defecto_es_relativa_al_repositorio():
    """La de los libros álbum de la tesis, junto al repositorio: no una ruta
    escrita para una máquina (bug 16 del legacy)."""
    assert PathConfig().stories_dir == (
        PROJECT_ROOT.parent.parent / "02_Informacion" / "02_Libro Album" / "Libros Album")


def test_la_carpeta_se_cambia_por_entorno_y_por_linea_de_comandos(tmp_path):
    por_entorno = AppConfig.from_env({"TELMEOW_STORIES_DIR": str(tmp_path)})
    assert por_entorno.paths.stories_dir == tmp_path
    por_cli = load(["--stories-dir", str(tmp_path / "otra")], env={})
    assert por_cli.paths.stories_dir == tmp_path / "otra"


def test_una_carpeta_relativa_se_resuelve_contra_el_proyecto():
    assert PathConfig(stories_dir=Path("cuentos")).stories_dir == PROJECT_ROOT / "cuentos"
