"""Pruebas del script de la sesión de verificación (``tools/sesion_verificacion.py``).

La parte interactiva (lanzar el broker, el visor, el pipeline y la cámara) se
prueba a mano en la sesión. Aquí, lo que decide algo: cuándo hay trampa del
doble broker, el protocolo que se enseña, y las cifras del informe.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))

import sesion_verificacion as sv  # noqa: E402


@pytest.mark.parametrize("tcp, ws, estado", [
    (True, True, "listo"),
    (True, False, "servicio"),       # §4.1: el servicio, sin WebSocket
    (False, False, "arrancar"),
    (False, True, "arrancar"),
])
def test_el_estado_del_broker(tcp, ws, estado):
    assert sv.estado_broker(tcp, ws) == estado


def test_puerto_abierto():
    servidor = socket.socket()
    servidor.bind(("localhost", 0))
    servidor.listen(1)
    puerto = servidor.getsockname()[1]
    try:
        assert sv.puerto_abierto(puerto)
    finally:
        servidor.close()
    assert not sv.puerto_abierto(puerto)


def test_el_protocolo_sigue_al_del_p6_5_y_anade_la_cara_tapada():
    """Las etiquetas 0-10 son las del P6.5, para que el informe compare con
    aquellas sesiones con las mismas cuentas; la 11 y la 12 son nuevas."""
    import analizar_p65 as p

    etiquetas = [e for e, *_ in sv.PROTOCOLO]
    assert etiquetas == list(range(13))
    for e in range(11):
        assert e in p.ESPERADAS
    tabla = sv.tabla_protocolo()
    assert "NO pulses c" in tabla                # H59: la c a mitad de la calibración
    assert "ESPACIO" in tabla and "2 min 35 s" in tabla


def test_los_saltos_de_la_cabeza():
    assert sv.saltos([(0.0, 0.0), None, (3.0, -1.0), (3.5, 5.0)]) == [3.0, 6.0]
    assert sv.saltos([None, (1.0, 1.0)]) == []


def _fila(t, rostro=1, manos=0, retorno=0, etiqueta=11):
    return {"t": t, "etiqueta": etiqueta, "rostro": rostro, "manos": manos, "retorno": retorno}


def test_el_retorno_se_mide_desde_que_la_cara_deja_de_servir():
    filas = ([_fila(0.2 * k) for k in range(5)]
             + [_fila(1.0, manos=1), _fila(1.2, rostro=0), _fila(2.0, rostro=0), _fila(3.1, rostro=0, retorno=1)])
    assert sv.retorno_tras_taparse(filas) == pytest.approx(2.1)
    assert sv.retorno_tras_taparse([_fila(0.0)]) is None
    assert sv.retorno_tras_taparse([_fila(0.0, manos=1)]) is None


def test_la_latencia_mqtt_sin_el_arranque(tmp_path):
    ruta = tmp_path / "x_latencia.csv"
    filas = ["recibido_ms,t_captura_ms,latencia_ms", "1000,0,2500"]          # el arranque
    filas += [f"{1000 + 6000 + 50 * k},0,{90 + k % 3}" for k in range(20)]
    ruta.write_text("\n".join(filas) + "\n", encoding="utf-8")
    mediana, p95, n = sv.latencia_mqtt(ruta)
    assert n == 20 and mediana == 91 and p95 == 92
    assert sv.latencia_mqtt(tmp_path / "no_existe.csv") is None


def test_con_el_servicio_de_mosquitto_se_para_sin_lanzar_nada(monkeypatch, capsys):
    lanzados = []
    monkeypatch.setattr(sv, "puerto_abierto", lambda puerto, host="localhost": puerto == 1883)
    monkeypatch.setattr(sv.subprocess, "Popen", lambda *a, **k: lanzados.append(a))
    monkeypatch.setattr(sv.subprocess, "run", lambda *a, **k: lanzados.append(a))
    assert sv.main([]) == 1
    assert lanzados == []
    assert "Stop-Service mosquitto" in capsys.readouterr().out


def test_analizar_escribe_el_informe(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sv, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sv, "analizar", lambda base: f"INFORME DE {base.name}")
    assert sv.main(["--analizar", "--base", "prueba"]) == 0
    assert (tmp_path / "prueba_informe.txt").read_text(encoding="utf-8") == "INFORME DE prueba"
    assert "INFORME DE prueba" in capsys.readouterr().out
