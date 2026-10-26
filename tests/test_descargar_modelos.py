"""Pruebas de ``tools/descargar_modelos.py``, sin red: la descarga es falsa."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import descargar_modelos as dm  # noqa: E402

CONTENIDO = b"pesos del modelo"
MODELO = dm.Modelo("m.onnx", "https://ejemplo/m.onnx", hashlib.sha256(CONTENIDO).hexdigest())


def _bajar(contenido):
    llamadas = []

    def bajar(url, destino):
        llamadas.append(url)
        Path(destino).write_bytes(contenido)
    bajar.llamadas = llamadas
    return bajar


def test_baja_lo_que_falta_y_comprueba_la_huella(tmp_path):
    bajar = _bajar(CONTENIDO)
    assert dm.descargar(MODELO, tmp_path, bajar) == "descargado"
    assert (tmp_path / "m.onnx").read_bytes() == CONTENIDO
    assert dm.descargar(MODELO, tmp_path, bajar) == "ya estaba"
    assert bajar.llamadas == ["https://ejemplo/m.onnx"]


def test_rechaza_y_borra_lo_que_no_tiene_la_huella(tmp_path):
    with pytest.raises(ValueError, match="SHA-256"):
        dm.descargar(MODELO, tmp_path, _bajar(b"otro modelo"))
    assert list(tmp_path.iterdir()) == []


def test_las_huellas_son_las_del_plan_maestro():
    plan = (dm.PROJECT_ROOT / "TELMEOW_PLAN_MAESTRO.md").read_text(encoding="utf-8")
    for modelo in dm.MODELOS:
        assert modelo.sha256 in plan, modelo.archivo
    assert [m.archivo for m in dm.MODELOS if m.operacion] == [
        "face_landmarker.task", "mbf_va_mtl.onnx"]
