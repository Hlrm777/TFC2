"""Pruebas del arranque del broker de la sesión (``telmeow_teleop/broker.py``).

Hasta el P10 vivían en ``tools/sesion_verificacion.py``; las usa también el
comando de la sesión de narración.
"""

from __future__ import annotations

import socket

import pytest

from telmeow_teleop import broker


@pytest.mark.parametrize("tcp, ws, estado", [
    (True, True, "listo"),
    (True, False, "servicio"),       # §4.1: el servicio, sin WebSocket
    (False, False, "arrancar"),
    (False, True, "arrancar"),
])
def test_el_estado_del_broker(tcp, ws, estado):
    assert broker.estado_broker(tcp, ws) == estado


def test_puerto_abierto():
    servidor = socket.socket()
    servidor.bind(("localhost", 0))
    servidor.listen(1)
    puerto = servidor.getsockname()[1]
    try:
        assert broker.puerto_abierto(puerto)
    finally:
        servidor.close()
    assert not broker.puerto_abierto(puerto)


def test_con_el_broker_listo_no_lanza_nada(capsys):
    lanzados = []
    assert broker.asegurar_broker(lanzados, puerto_abierto=lambda p: True,
                                  popen=lambda *a, **k: pytest.fail("no debia lanzar"))
    assert lanzados == []
    assert "listo" in capsys.readouterr().out


def test_con_el_servicio_de_mosquitto_explica_y_no_lanza(capsys):
    lanzados = []
    assert not broker.asegurar_broker(lanzados, puerto_abierto=lambda p: p == 1883,
                                      popen=lambda *a, **k: pytest.fail("no debia lanzar"))
    assert lanzados == []
    assert "Stop-Service mosquitto" in capsys.readouterr().out


def test_sin_broker_lo_lanza_con_el_conf_del_repositorio_y_lo_devuelve(tmp_path):
    exe = tmp_path / "mosquitto.exe"
    exe.write_bytes(b"")
    abiertos = {"arriba": False}
    lanzados = []

    def popen(argv, **_kw):
        abiertos["arriba"] = True
        return ("proceso", argv)

    assert broker.asegurar_broker(lanzados, puerto_abierto=lambda p: abiertos["arriba"],
                                  popen=popen, esperar=lambda s: None, mosquitto=exe)
    assert len(lanzados) == 1
    argv = lanzados[0][1]
    assert argv[0] == str(exe) and argv[1] == "-c" and argv[2].endswith("mosquitto.conf")


def test_si_el_broker_lanzado_no_abre_los_puertos_lo_dice(tmp_path, capsys):
    exe = tmp_path / "mosquitto.exe"
    exe.write_bytes(b"")
    lanzados = []
    assert not broker.asegurar_broker(lanzados, puerto_abierto=lambda p: False,
                                      popen=lambda *a, **k: "proceso",
                                      esperar=lambda s: None, mosquitto=exe)
    assert lanzados == ["proceso"]          # quien llama lo apaga igual
    assert "no abrio" in capsys.readouterr().out


def test_sin_mosquitto_instalado_no_lanza_nada(tmp_path, capsys):
    assert not broker.asegurar_broker([], puerto_abierto=lambda p: False,
                                      mosquitto=tmp_path / "no_esta.exe")
    assert "No encuentro" in capsys.readouterr().out
