"""Pruebas del analizador de sesiones del pipeline (``tools/analizar_sesion.py``).

La sesión sintética se escribe con las columnas de ``main.COLUMNAS_SESION``, no
con una lista copiada: si el pipeline cambia una columna, esto falla.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

from telmeow_teleop.main import COLUMNAS_SESION
from telmeow_teleop.arm_tracker import COLUMNAS_CSV as COLUMNAS_BRAZOS

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))

import analizar_sesion as asn  # noqa: E402


def _sesion(ruta, frames, *, periodo_ms=1000 / 30, cada=6, extra_ms=0.0, etiqueta=0, bucle_ms=29.0,
            fallidos=0, pausa=()):
    """Una sesión a ``periodo_ms`` en que cada ``cada`` frames se infiere la
    emoción, y ese frame tarda ``extra_ms`` más (y retrasa el siguiente)."""
    t = 0.0
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        escritor = csv.DictWriter(f, fieldnames=COLUMNAS_SESION)
        escritor.writeheader()
        retraso = 0.0
        for i in range(frames):
            infiere = i % cada == 0
            dt = None if i == 0 else periodo_ms + retraso
            t += 0 if dt is None else dt / 1000.0
            retraso = extra_ms if infiere else 0.0
            escritor.writerow({
                "t": f"{t:.4f}", "t_captura_ms": f"{1e6 + 1000 * t:.1f}", "etiqueta": etiqueta,
                "dt_ms": "" if dt is None else f"{dt:.2f}", "duplicado": int(i == 7),
                "ms_cabeza": "6.0", "ms_brazos": "22.0",
                "ms_emocion": f"{20 + extra_ms:.2f}" if infiere else "",
                "ms_bucle": f"{bucle_ms + (extra_ms if infiere else 0):.2f}",
                "pan": "1.0", "tilt": "2.0", "armL": "-55", "armLtw": "0", "armR": "-55",
                "armRtw": "0", "emo": "neutral", "gesto": "", "pausa": int(i in pausa),
                "mqtt_enviados": i, "mqtt_descartados": 0,
                "mqtt_fallidos": fallidos if i == frames - 1 else 0,
                "mqtt_hz": "20.0", "brillo": "120.0", "ancho": 640, "alto": 480,
            })
    return ruta


def test_resume_una_sesion_a_30_fps(tmp_path):
    filas = asn.cargar(_sesion(tmp_path / "s.csv", 61))
    r = asn.resumir(filas)
    assert r.frames == 61
    assert r.fps == pytest.approx(30.0, abs=0.01)
    assert r.fps_medios == pytest.approx(30.0, abs=0.01)
    assert r.perdidos == 0.0
    assert r.duplicados == 1
    assert r.inferencias == 11
    assert r.ms_cabeza[0] == 6.0 and r.ms_brazos[0] == 22.0
    assert r.ms_bucle_sin[0] == 29.0 and r.ms_bucle_con[0] == 29.0
    assert r.hz == 20.0


def test_los_frames_con_inferencia_largos_cuentan_como_perdidos(tmp_path):
    """Cada sexto frame tarda 50 ms más: el siguiente llega a 83 ms, más de 1.5
    periodos, y los fps medios bajan aunque la mediana del intervalo no."""
    filas = asn.cargar(_sesion(tmp_path / "s.csv", 61, extra_ms=50.0))
    r = asn.resumir(filas)
    assert r.fps == pytest.approx(30.0, abs=0.01)        # la mediana no lo ve
    assert r.fps_medios < 25.0                           # los medios sí
    assert r.perdidos == pytest.approx(10 / 60)
    assert r.ms_bucle_con[0] == pytest.approx(79.0)
    assert r.ms_emocion[0] == pytest.approx(70.0)


def test_las_pausas_y_los_perdidos_se_cuentan(tmp_path):
    filas = asn.cargar(_sesion(tmp_path / "s.csv", 20, fallidos=7, pausa=(3, 4, 5)))
    r = asn.resumir(filas)
    assert r.fallidos == 7
    assert r.en_pausa == 3


def test_por_etiqueta(tmp_path):
    ruta = _sesion(tmp_path / "s.csv", 10, etiqueta=2)
    filas = asn.cargar(ruta)
    assert asn.resumir(filas, 2).frames == 10
    with pytest.raises(ValueError):
        asn.resumir(filas, 5)


@pytest.mark.parametrize("extra_ms, hilo", [(0.0, False), (50.0, True)])
def test_el_criterio_del_p7_1(tmp_path, extra_ms, hilo):
    con = asn.resumir(asn.cargar(_sesion(tmp_path / "con.csv", 61, extra_ms=extra_ms)))
    sin = asn.resumir(asn.cargar(_sesion(tmp_path / "sin.csv", 61)))
    c = asn.criterio_p71(con, sin)
    assert c.hilo is hilo
    assert not c.reserva_brazos
    assert bool(c.motivos) is hilo


def test_si_ni_sin_emocion_cabe_el_bucle_se_prueba_antes_blazepose_ligero(tmp_path):
    """El bucle sin emoción tarda 45 ms: aunque la cámara diera más, no pasaría
    de 22 fps. Primero el BlazePose ligero."""
    con = asn.resumir(asn.cargar(_sesion(tmp_path / "con.csv", 41, periodo_ms=50.0, bucle_ms=45.0)))
    sin = asn.resumir(asn.cargar(_sesion(tmp_path / "sin.csv", 41, periodo_ms=50.0, bucle_ms=45.0)))
    c = asn.criterio_p71(con, sin)
    assert c.reserva_brazos
    assert not c.hilo


def test_una_camara_lenta_por_la_luz_no_es_un_bucle_lento(tmp_path):
    """La sesión del 2026-11-02: la cámara a 15 fps por la poca luz y el bucle
    sin emoción en 38 ms. La reserva de BlazePose no aplica: el límite es la
    exposición (H27), no el cómputo."""
    con = asn.resumir(asn.cargar(_sesion(tmp_path / "con.csv", 41, periodo_ms=64.0, bucle_ms=38.0)))
    sin = asn.resumir(asn.cargar(_sesion(tmp_path / "sin.csv", 41, periodo_ms=64.0, bucle_ms=38.0)))
    c = asn.criterio_p71(con, sin)
    assert c.fps_sin < asn.FPS_MINIMO_SIN_EMOCION
    assert c.capacidad_sin == pytest.approx(1000 / 38.0)
    assert not c.reserva_brazos

def test_un_csv_de_brazos_se_rechaza_con_un_mensaje_que_dice_que_hacer(tmp_path):
    ruta = tmp_path / "s_brazos.csv"
    ruta.write_text(",".join(COLUMNAS_BRAZOS) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="sus herramientas"):
        asn.cargar(ruta)


def test_la_linea_de_comandos(tmp_path, capsys):
    con = _sesion(tmp_path / "con.csv", 61, extra_ms=50.0, etiqueta=5)
    sin = _sesion(tmp_path / "sin.csv", 61)
    assert asn.main([str(con), "--comparar", str(sin), "--etiqueta", "5"]) == 0
    salida = capsys.readouterr().out
    assert "etiqueta 5" in salida
    assert "la emocion pasa a un hilo aparte" in salida
