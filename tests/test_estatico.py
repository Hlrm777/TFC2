"""Análisis estático de todo el código, incluido el que las pruebas no ejecutan.

POR QUÉ EXISTE
--------------
Los modos demo (`python -m telmeow_teleop.head_tracker`, el `_demo` del
publisher, las herramientas de `tools/`) solo corren con una cámara o un broker
delante, así que **ninguna prueba los ejecuta**. Un fallo tan tonto como usar
`deque` sin importarlo pasó las 400 pruebas y reventó en la cara del operador al
arrancar el demo.

pyflakes recorre el árbol sintáctico y detecta esa clase de fallo —nombres no
definidos, imports rotos, variables muertas— sin ejecutar nada. Es la única red
que cubre el código de los demos.

No sustituye a las pruebas: no comprueba que nada funcione, solo que todos los
nombres existen. Pero eso es exactamente lo que faltaba.
"""

import io
import re
from pathlib import Path

import pytest

pyflakes_api = pytest.importorskip(
    "pyflakes.api", reason="pyflakes no instalado (está en requirements-dev.txt)"
)
from pyflakes.reporter import Reporter  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
OBJETIVOS = ("telmeow_teleop", "tools", "tests", "conftest.py")


def analizar() -> tuple[int, str]:
    salida, errores = io.StringIO(), io.StringIO()
    reporter = Reporter(salida, errores)
    total = 0
    for objetivo in OBJETIVOS:
        ruta = RAIZ / objetivo
        if ruta.is_dir():
            total += pyflakes_api.checkRecursive([str(ruta)], reporter)
        elif ruta.is_file():
            total += pyflakes_api.checkPath(str(ruta), reporter)
    return total, salida.getvalue() + errores.getvalue()


def test_no_hay_nombres_indefinidos_ni_imports_rotos():
    """Cubre el código de los demos, que ninguna prueba llega a ejecutar."""
    total, detalle = analizar()
    assert total == 0, f"\npyflakes encontró {total} problema(s):\n{detalle}"


def test_ningun_identificador_lleva_tildes_ni_enye():
    """Los NOMBRES van sin tildes; los comentarios y docstrings, con ellas.

    Python 3 admite identificadores con tildes, pero traen problemas que no
    compensan: dependen de la codificación del archivo, se rompen al copiar
    entre editores y herramientas, y obligan a escribirlos exactamente igual
    desde cualquier terminal. El texto para leer sí lleva sus tildes: ahí no hay
    ningún riesgo y se lee mejor.
    """
    patron = re.compile(r"^\s*(?:def|class)\s+(\w*[^\x00-\x7F]\w*)", re.MULTILINE)
    culpables = []
    for objetivo in OBJETIVOS:
        ruta = RAIZ / objetivo
        archivos = ruta.rglob("*.py") if ruta.is_dir() else [ruta]
        for archivo in archivos:
            for nombre in patron.findall(archivo.read_text(encoding="utf-8")):
                culpables.append(f"{archivo.relative_to(RAIZ)}: {nombre}")
    assert not culpables, "identificadores con caracteres no ASCII:\n" + "\n".join(culpables)


def test_la_red_de_seguridad_detecta_de_verdad_el_fallo(tmp_path):
    """Comprueba el detector con el fallo exacto que se coló.

    Una prueba que solo dice «no hay problemas» podría estar pasando porque el
    detector no mira nada. Esta reproduce el caso real —usar `deque` sin
    importarlo, dentro de una función que nadie ejecuta— y exige que se
    detecte.
    """
    archivo = tmp_path / "con_fallo.py"
    archivo.write_text(
        "import time\n\n\n"
        "def demo():\n"
        "    marcas = deque(maxlen=30)\n"
        "    return time.time(), marcas\n",
        encoding="utf-8",
    )
    salida, errores = io.StringIO(), io.StringIO()
    problemas = pyflakes_api.checkPath(str(archivo), Reporter(salida, errores))
    assert problemas == 1
    assert "undefined name 'deque'" in salida.getvalue()
