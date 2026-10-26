"""Descarga los modelos del clasificador de emoción combinado y comprueba su huella.

Para qué
--------
El backend de operación desde el P6.4 (``combinado``) usa dos modelos que no
se versionan —pesan 12 MB y tienen su propio origen—: el Face Landmarker de
MediaPipe y HSEmotion ``mbf_va_mtl`` en ONNX. Van en ``modelos_externos/``.
Esta herramienta los baja de su origen y comprueba el SHA-256 anotado en el
plan maestro (P6.4), para que en otra máquina se opere con los MISMOS pesos con
que se midió. Un archivo que ya está y coincide no se vuelve a bajar; uno que no
coincide se rechaza y se borra.

``enet_b0_8_best_vgaf`` no es del de operación: solo se baja con ``--todos``,
para repetir la tabla del P6.4.

Uso::

    python tools/descargar_modelos.py [--todos]
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telmeow_teleop.config import PROJECT_ROOT  # noqa: E402

CARPETA = PROJECT_ROOT / "modelos_externos"
_EMOTIEFFLIB = ("https://github.com/sb-ai-lab/EmotiEffLib/raw/"
                "520a051c64cd191521e5934655314e769a319684/models/affectnet_emotions/onnx/")


@dataclass(frozen=True)
class Modelo:
    archivo: str
    url: str
    sha256: str
    operacion: bool = True


MODELOS = (
    Modelo("face_landmarker.task",
           "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
           "face_landmarker/float16/1/face_landmarker.task",
           "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"),
    Modelo("mbf_va_mtl.onnx", _EMOTIEFFLIB + "mbf_va_mtl.onnx",
           "0323ace52ea6dbe9aa4a909d20933f7b8629c3fb41249e338f4316abf2a4828c"),
    Modelo("enet_b0_8_best_vgaf.onnx", _EMOTIEFFLIB + "enet_b0_8_best_vgaf.onnx",
           "fa07e841fd06c7a67ee651ea4e6e4a3a2bb5695f47b37a7da50492526f59c898",
           operacion=False),
)


def sha256(ruta: Path) -> str:
    return hashlib.sha256(Path(ruta).read_bytes()).hexdigest()


def comprobar(ruta: Path, esperado: str) -> bool:
    """Si ``ruta`` existe y su SHA-256 es el ``esperado``."""
    return Path(ruta).is_file() and sha256(ruta) == esperado


def descargar(modelo: Modelo, carpeta: Path = CARPETA, bajar=urllib.request.urlretrieve) -> str:
    """Baja ``modelo`` a ``carpeta`` si falta. Devuelve qué pasó.

    Raises:
        ValueError: si lo bajado no tiene la huella esperada (se borra).
    """
    destino = Path(carpeta) / modelo.archivo
    if comprobar(destino, modelo.sha256):
        return "ya estaba"
    Path(carpeta).mkdir(parents=True, exist_ok=True)
    temporal = destino.with_name(destino.name + ".parcial")
    bajar(modelo.url, temporal)
    if not comprobar(temporal, modelo.sha256):
        temporal.unlink(missing_ok=True)
        raise ValueError(
            f"{modelo.archivo}: el SHA-256 de lo descargado no es el del plan maestro. "
            f"El origen cambió; no se usa sin revisarlo.")
    temporal.replace(destino)
    return "descargado"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--todos", action="store_true",
                        help="también los que solo sirven para la tabla del P6.4")
    args = parser.parse_args(argv)
    fallos = 0
    for modelo in MODELOS:
        if not (modelo.operacion or args.todos):
            continue
        try:
            print(f"{modelo.archivo}: {descargar(modelo)}")
        except (OSError, ValueError) as exc:
            print(f"{modelo.archivo}: ERROR {exc}")
            fallos += 1
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
