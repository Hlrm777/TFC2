"""La sesión corta de velocidad del P10 (``sesion_verificacion.py --velocidad``).

En la sesión que cerró el P10 (2026-11-13) la narración se comparó con una
referencia sin interfaz de la noche anterior, y eso no vale. Esta sesión graba
las dos seguidas, y la narración con las dos vistas del robot.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))

import sesion_verificacion as sv  # noqa: E402


def _grabar(ruta, tramos):
    """``tramos``: ``(etiqueta, segundos, fps)``; una fila por foto."""
    from telmeow_teleop.main import COLUMNAS_SESION

    t, filas = 0.0, []
    for etiqueta, segundos, fps in tramos:
        for _ in range(int(segundos * fps)):
            t += 1.0 / fps
            filas.append({"t": f"{t:.4f}", "etiqueta": etiqueta, "dt_ms": 1000.0 / fps,
                          "ms_bucle": 900.0 / fps, "mqtt_hz": 19.9, "pausa": 0, "mqtt_fallidos": 0})
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS_SESION, restval="")
        w.writeheader()
        w.writerows(filas)


def _sesion(tmp_path, frente=20.0, detras=20.0, segundos_detras=60):
    _grabar(tmp_path / "p10v_sin_interfaz.csv", [(0, 10, 21.0), (1, 60, 21.0)])
    _grabar(tmp_path / "p10v_narracion.csv",
            [(0, 15, 18.0), (1, 60, frente), (2, segundos_detras, detras), (3, 5, 18.0)])
    return tmp_path / "p10v"


def test_el_protocolo_habla_un_minuto_con_cada_vista():
    que = {e: (texto, segundos) for e, texto, segundos, _ in sv.PROTOCOLO_VELOCIDAD}
    assert "«Vista: frente»" in que[1][0] and que[1][1] == 60
    assert "«Vista»" in que[2][0] and "detrás" in que[2][0] and que[2][1] == 60
    assert "q dos veces" in que[3][0]
    assert que[1][1] >= sv.VELOCIDAD_MIN_S and que[2][1] >= sv.VELOCIDAD_MIN_S


def test_la_guia_cabe_en_la_terminal_y_no_habla_de_otra_sesion():
    guia = sv.guia_velocidad()
    assert max(len(linea) for linea in guia.splitlines()) <= sv.ANCHO_P10
    assert guia.index("PRIMERA GRABACIÓN") < guia.index("SEGUNDA GRABACIÓN")
    assert "etiquetas 2 a 13" not in guia


def test_una_sesion_que_cumple_con_las_dos_vistas(tmp_path):
    informe = sv.analizar_velocidad(_sesion(tmp_path))
    assert "robot de frente (etiqueta 1): 20.0 fps en 60 s (45 ms por vuelta): 95 % OK" in informe
    assert "robot desde detras (etiqueta 2): 20.0 fps" in informe
    assert "NO CUMPLE" not in informe and "NO VALE" not in informe
    assert "Lectura: la interfaz cumple con las dos vistas." in informe


@pytest.mark.parametrize("frente, detras, lectura", [
    (17.0, 20.0, "lo que cuesta es dibujar el robot de frente"),
    (17.0, 17.2, "no es la vista, es la pagina entera"),
    (15.0, 17.0, "de frente va peor que desde detras"),
])
def test_la_lectura_dice_de_donde_viene_lo_que_no_cumple(tmp_path, frente, detras, lectura):
    informe = sv.analizar_velocidad(_sesion(tmp_path, frente=frente, detras=detras))
    assert "NO CUMPLE" in informe and lectura in informe


def test_un_tramo_corto_no_vale(tmp_path):
    informe = sv.analizar_velocidad(_sesion(tmp_path, segundos_detras=10))
    assert "robot desde detras (etiqueta 2): NO VALE: hacen falta 30 s, y hay 10" in informe
    assert "Lectura" not in informe


def test_sin_las_dos_grabaciones_no_hay_nada_que_comparar(tmp_path):
    _grabar(tmp_path / "p10v_narracion.csv", [(1, 60, 20.0)])
    assert "Falta p10v_sin_interfaz.csv" in sv.analizar_velocidad(tmp_path / "p10v")


def test_la_sesion_graba_siempre_las_dos_seguidas_y_con_su_guia(monkeypatch, tmp_path, capsys):
    """Aunque ya existan: lo que se mide es una frente a otra, la misma noche."""
    monkeypatch.setattr(sv, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sv, "asegurar_broker", lambda arrancados, **k: True)
    monkeypatch.setattr(sv.subprocess, "Popen", lambda *a, **k: pytest.fail("ni visor ni monitor"))
    (tmp_path / "p10v_sin_interfaz.csv").write_text("ya", encoding="utf-8")
    lanzados, guias = [], []

    def correr(args, **k):
        lanzados.append(args)
        if "--guia-sesion" in args:
            guias.append(json.loads(Path(args[args.index("--guia-sesion") + 1]).read_text(encoding="utf-8")))
    monkeypatch.setattr(sv.subprocess, "run", correr)
    preguntas = []
    monkeypatch.setattr("builtins.input", lambda prompt="": preguntas.append(prompt) or "")
    monkeypatch.setattr(sv, "analizar_velocidad", lambda base: f"VELOCIDAD DE {base.name}")

    assert sv.main(["--velocidad"]) == 0
    assert lanzados[0][1:] == ["-m", "telmeow_teleop.main", "--record", str(tmp_path / "p10v_sin_interfaz.csv")]
    assert lanzados[1][1:5] == ["-m", "telmeow_teleop.control_panel", "--record",
                                str(tmp_path / "p10v_narracion.csv")]
    assert not any("Ya existe" in p for p in preguntas)
    (guia,) = guias
    assert guia["titulo"] == sv.TITULO_GUIA_VELOCIDAD
    assert [e["etiqueta"] for e in guia["etiquetas"]] == [0, 1, 2, 3]
    assert (tmp_path / "p10v_informe.txt").read_text(encoding="utf-8") == "VELOCIDAD DE p10v"


def test_la_guia_de_velocidad_la_lee_el_servidor():
    """Dos caminos: la herramienta la escribe y control_panel la valida."""
    from telmeow_teleop.control_panel import guia_de, guia_desde_datos

    guia = guia_desde_datos(sv.guia_p10_datos(sv.PROTOCOLO_VELOCIDAD, sv.TITULO_GUIA_VELOCIDAD))
    assert "detrás" in guia_de(guia, 2)["que"]


def test_con_velocidad_y_guia_solo_ensena_las_tablas(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sv, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sv, "asegurar_broker", lambda *a, **k: pytest.fail("no debe arrancar nada"))
    assert sv.main(["--velocidad", "--guia"]) == 0
    assert "LA SESIÓN DE VELOCIDAD" in capsys.readouterr().out
