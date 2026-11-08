"""Los cuentos del P10: la lista de PDF de una carpeta y el estado de lectura.

El narrador no sostiene un libro físico: lee el libro digitalizado en la
interfaz (ideas del operador, 2026-09-27). Los cuentos son PDF —los libros
álbum de la tesis, en ``02_Informacion/02_Libro Album/Libros Album``— y este
módulo solo hace dos cosas con ellos:

- **Listarlos con su número de páginas** (``Biblioteca``). El PDF se dibuja en
  el navegador con pdf.js; aquí basta con saber cuántas páginas tiene, porque
  el mensaje de página lleva el total (``{"page": N, "total": M}``) y el
  servicio de la Raspberry (P11) lo necesita. Lo da **pypdf**, como pedía el
  enunciado, y solo para eso. Contar con una expresión regular sobre el
  archivo no vale: dos de los veinte libros llevan su índice comprimido y
  salían con cero páginas.
- **Llevar la página** (``Lector``): elegir un cuento abre la página 1, y
  ``destino`` dice a qué página llevaría «siguiente» o «anterior» sin moverla.
  Quien llama la mueve con ``ir`` solo si el mensaje salió: en pausa no se pasa
  de página (decisión del operador, 2026-11-08), y así el estado no puede
  adelantarse a lo publicado.

Los PDF no van en git: tienen derechos de autor. Las pruebas fabrican PDF en
blanco con pypdf.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Historia", "Biblioteca", "Lector", "contar_paginas"]


@dataclass(frozen=True)
class Historia:
    """Un cuento de la carpeta.

    ``nombre`` es el nombre del archivo, con su ``.pdf``: es la clave con que
    viaja por MQTT (``{"story": nombre}``) y con que lo pide la página, y el
    P11 la buscará en su propia carpeta. Nunca una ruta.
    """

    nombre: str
    ruta: Path
    paginas: int | None
    error: str = ""

    @property
    def titulo(self) -> str:
        """El nombre sin la extensión, para enseñarlo."""
        return Path(self.nombre).stem.strip()

    @property
    def legible(self) -> bool:
        return self.paginas is not None and self.paginas > 0

    def como_dict(self) -> dict:
        return {"nombre": self.nombre, "titulo": self.titulo,
                "paginas": self.paginas, "error": self.error}


def contar_paginas(ruta: Path) -> int:
    """Las páginas de un PDF, con pypdf. Lanza si el archivo no se puede leer.

    pypdf avisa por ``logging`` de cada irregularidad que tolera (los PDF
    escaneados traen muchas); no son errores, así que se callan aquí.
    """
    from pypdf import PdfReader

    registro = logging.getLogger("pypdf")
    nivel = registro.level
    registro.setLevel(logging.ERROR)
    try:
        return len(PdfReader(str(ruta)).pages)
    finally:
        registro.setLevel(nivel)


class Biblioteca:
    """Los PDF de una carpeta, con caché por fecha y tamaño de cada archivo.

    Listar los veinte libros cuesta ~0.2 s; con la caché, volver a listar no
    vuelve a abrir ninguno que no haya cambiado. Un PDF que no se puede leer
    sale en la lista con su error y sin páginas: no tumba la lista ni la
    sesión.
    """

    def __init__(self, carpeta: Path, contador=contar_paginas) -> None:
        self.carpeta = Path(carpeta)
        self._contar = contador
        self._cache: dict[str, tuple[tuple[int, int], Historia]] = {}

    @property
    def existe(self) -> bool:
        return self.carpeta.is_dir()

    def listar(self) -> list[Historia]:
        """Los PDF de la carpeta (sin subcarpetas), por orden alfabético sin
        distinguir mayúsculas. Lista vacía si la carpeta no existe."""
        if not self.existe:
            return []
        historias = []
        vistos = set()
        for ruta in self.carpeta.iterdir():
            if not ruta.is_file() or ruta.suffix.lower() != ".pdf":
                continue
            estado = ruta.stat()
            firma = (estado.st_mtime_ns, estado.st_size)
            vistos.add(ruta.name)
            guardada = self._cache.get(ruta.name)
            if guardada is not None and guardada[0] == firma:
                historias.append(guardada[1])
                continue
            try:
                historia = Historia(ruta.name, ruta, self._contar(ruta))
            except Exception as exc:  # noqa: BLE001 -- un PDF roto no tumba la lista
                historia = Historia(ruta.name, ruta, None, f"{type(exc).__name__}: {exc}")
            self._cache[ruta.name] = (firma, historia)
            historias.append(historia)
        for nombre in set(self._cache) - vistos:
            del self._cache[nombre]
        return sorted(historias, key=lambda h: h.nombre.casefold())

    def buscar(self, nombre: str) -> Historia | None:
        """El cuento con ese nombre EXACTO de la lista, o ``None``.

        Solo se aceptan nombres que estén en la lista: así ningún nombre que
        llegue de la página puede apuntar fuera de la carpeta (``..``, rutas
        absolutas, barras).
        """
        for historia in self.listar():
            if historia.nombre == nombre:
                return historia
        return None


class Lector:
    """En qué cuento y en qué página va la lectura.

    No publica nada: ``destino`` calcula y ``ir`` mueve. Entre las dos, quien
    llama manda el mensaje, y si no sale (pausa, sin enlace) no llama a ``ir``.
    """

    def __init__(self) -> None:
        self.historia: Historia | None = None
        self.pagina: int | None = None

    @property
    def total(self) -> int | None:
        return self.historia.paginas if self.historia is not None else None

    @property
    def abierto(self) -> bool:
        return self.historia is not None

    def elegir(self, historia: Historia) -> None:
        """Abre el cuento en la página 1. ``ValueError`` si no tiene páginas."""
        if not historia.legible:
            raise ValueError(f"el cuento {historia.nombre!r} no tiene páginas legibles")
        self.historia = historia
        self.pagina = 1

    def destino(self, paso: int) -> int | None:
        """La página a la que llevaría avanzar ``paso`` (+1 siguiente, −1
        anterior), o ``None`` si no hay cuento o se saldría del libro."""
        if self.historia is None or self.pagina is None:
            return None
        nueva = self.pagina + paso
        if not 1 <= nueva <= self.total:
            return None
        return nueva

    def ir(self, pagina: int) -> None:
        if self.historia is None:
            raise ValueError("no hay ningún cuento abierto")
        if not 1 <= pagina <= self.total:
            raise ValueError(f"página {pagina} fuera de 1..{self.total}")
        self.pagina = int(pagina)
