"""La voz con el robot (raspberry/robot_audio.py y telmeow_teleop/audio_operador.py).

Sin tarjeta de sonido: el eco y los niños los fabrica tools/simulador_sala.py.
"""

import sys
import time
from pathlib import Path

import numpy as np
import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(RAIZ / "tools")]

from raspberry import robot_audio as ra  # noqa: E402
from simulador_sala import (DispositivoSintetico, SalaSimulada, camino_eco,  # noqa: E402
                            voz_sintetica)

FS = ra.TASA
B = ra.MUESTRAS


def _bloque(valor=0.1):
    return np.full(B, valor, dtype=np.float32)


# -- los paquetes -------------------------------------------------------------


def test_un_paquete_ida_y_vuelta():
    muestras = np.sin(np.arange(B) / 5).astype(np.float32) * 0.5
    orden, t_ms, leidas = ra.decodificar(ra.codificar(41, 1234, muestras))
    assert (orden, t_ms) == (41, 1234)
    assert np.max(np.abs(leidas - muestras)) < 1e-4
    assert len(ra.codificar(0, 0, muestras)) == ra.CABECERA.size + 2 * B


def test_lo_que_se_pasa_de_fuerte_se_recorta_en_vez_de_dar_la_vuelta():
    _, _, leidas = ra.decodificar(ra.codificar(0, 0, np.full(B, 3.0)))
    assert np.allclose(leidas, 1.0, atol=1e-4)


@pytest.mark.parametrize("malo", [
    b"", b"TMAU", b"XXXX" + bytes(ra.CABECERA.size - 4 + 2 * B),
    ra.codificar(0, 0, _bloque())[:-2],
    ra.codificar(0, 0, _bloque()).replace(b"TMAU\x01", b"TMAU\x09", 1),
])
def test_lo_que_no_es_un_paquete_nuestro_se_ignora(malo):
    assert ra.decodificar(malo) is None


# -- el colchón -----------------------------------------------------------------


def test_el_colchon_espera_a_tener_tres_antes_de_sonar():
    c = ra.Colchon(objetivo=3)
    c.meter(0, 0, _bloque(0.1))
    c.meter(1, 0, _bloque(0.2))
    assert not c.sacar().any()
    c.meter(2, 0, _bloque(0.3))
    assert [round(float(c.sacar()[0]), 2) for _ in range(3)] == [0.1, 0.2, 0.3]


def test_el_colchon_ordena_lo_que_llega_desordenado():
    c = ra.Colchon(objetivo=3)
    for orden in (1, 0, 2):
        c.meter(orden, 0, _bloque(orden / 10))
    assert [round(float(c.sacar()[0]), 2) for _ in range(3)] == [0.0, 0.1, 0.2]


def test_un_paquete_perdido_es_silencio_y_se_sigue():
    c = ra.Colchon(objetivo=3)
    for orden in (0, 2, 3):
        c.meter(orden, 0, _bloque(0.5))
    salidas = [float(c.sacar()[0]) for _ in range(4)]
    assert salidas == [pytest.approx(0.5, abs=1e-6), 0.0, pytest.approx(0.5, abs=1e-6),
                       pytest.approx(0.5, abs=1e-6)]
    assert c.perdidos == 1


def test_uno_que_llega_tarde_se_tira():
    c = ra.Colchon(objetivo=1)
    c.meter(5, 0, _bloque())
    c.sacar()
    c.meter(4, 0, _bloque())
    assert c.tarde == 1


def test_si_se_acumulan_muchos_se_tiran_los_viejos_para_no_ir_retrasado():
    c = ra.Colchon(objetivo=3, maximo=10)
    for orden in range(25):
        c.meter(orden, 0, _bloque(orden / 100))
    assert c.tirados == 15
    assert round(float(c.sacar()[0]), 2) == 0.15


def test_si_el_otro_lado_se_reinicia_se_empieza_de_cero():
    c = ra.Colchon(objetivo=1)
    for orden in range(2000, 2003):
        c.meter(orden, 0, _bloque())
        c.sacar()
    c.meter(0, 0, _bloque(0.7))
    assert c.tarde == 0
    assert round(float(c.sacar()[0]), 2) == 0.7


def test_si_se_vacia_vuelve_a_llenar_antes_de_sonar():
    c = ra.Colchon(objetivo=2)
    c.meter(0, 0, _bloque())
    c.meter(1, 0, _bloque())
    c.sacar(), c.sacar()
    assert not c.sacar().any() and c.huecos == 1
    c.meter(2, 0, _bloque(0.3))
    assert not c.sacar().any()             # aún uno solo: espera
    c.meter(3, 0, _bloque(0.4))
    assert round(float(c.sacar()[0]), 2) == 0.3


# -- el cancelador de eco -----------------------------------------------------


def _cancelar(segundos=20, nino=None, retraso=0.06, ganancia=0.7, cambio=None, ruido=1e-4):
    lejos = voz_sintetica(segundos, semilla=1)
    h = camino_eco(retraso_s=retraso, ganancia=ganancia)
    eco = np.convolve(lejos, h)[:len(lejos)]
    if cambio is not None:            # a los ``cambio`` s alguien mueve el robot
        h2 = camino_eco(retraso_s=retraso + 0.03, ganancia=ganancia, semilla=11)
        k = int(cambio * FS)
        eco[k:] = np.convolve(lejos, h2)[:len(lejos)][k:]
    cerca = np.zeros_like(lejos)
    if nino is not None:
        a, b = int(nino[0] * FS), int(nino[1] * FS)
        cerca[a:b] = voz_sintetica(nino[1] - nino[0], semilla=2, nivel=0.05, pausas=False)
    mic = eco + cerca + ruido * np.random.default_rng(0).standard_normal(len(lejos))
    c = ra.Cancelador()
    salida = np.zeros_like(lejos)
    inicio = time.perf_counter()
    for i in range(0, len(lejos) - B + 1, B):
        salida[i:i + B] = c.procesar(mic[i:i + B], lejos[i:i + B])
    ms = (time.perf_counter() - inicio) * 1000 / (len(lejos) // B)
    return eco, cerca, mic, salida, c, ms


def _db(a, b):
    return 10 * np.log10(np.sum(a ** 2) / np.sum(b ** 2))


def _t(a, b):
    return slice(int(a * FS), int(b * FS))


def test_quita_el_eco_cuando_solo_habla_el_narrador():
    eco, _, _, salida, _, ms = _cancelar(16)
    assert _db(eco[_t(6, 16)], salida[_t(6, 16)]) > 25
    assert ms < 5.0                    # de sobra: hay 20 ms por bloque


@pytest.mark.parametrize("retraso", [0.02, 0.15, 0.3])
def test_aprende_el_eco_con_el_retraso_que_sea_dentro_de_la_cola(retraso):
    eco, _, _, salida, _, _ = _cancelar(16, retraso=retraso)
    assert _db(eco[_t(8, 16)], salida[_t(8, 16)]) > 20


def test_con_el_nino_hablando_a_la_vez_quita_el_eco_y_deja_al_nino():
    eco, cerca, _, salida, _, _ = _cancelar(20, nino=(10, 16))
    doble = _t(10, 16)
    assert _db(eco[doble], salida[doble] - cerca[doble]) > 20
    assert np.corrcoef(salida[doble], cerca[doble])[0, 1] > 0.98


def test_si_alguien_mueve_el_robot_vuelve_a_aprender():
    eco, _, _, salida, c, _ = _cancelar(30, cambio=12)
    assert _db(eco[_t(22, 30)], salida[_t(22, 30)]) > 15


def test_sin_nada_sonando_deja_pasar_el_microfono_tal_cual():
    c = ra.Cancelador()
    voz = voz_sintetica(2, semilla=5, nivel=0.05)
    for i in range(0, len(voz) - B + 1, B):
        assert np.allclose(c.procesar(voz[i:i + B], np.zeros(B)), voz[i:i + B], atol=1e-6)


def test_nunca_sale_mas_fuerte_que_lo_grabado():
    _, _, mic, salida, _, _ = _cancelar(12, nino=(4, 8), ganancia=2.5)
    for i in range(0, len(mic) - B + 1, B):
        assert np.sum(salida[i:i + B] ** 2) <= np.sum(mic[i:i + B] ** 2) * 1.0001 + 1e-12


# -- la red --------------------------------------------------------------------


def _esperar(condicion, segundos=2.0):
    fin = time.monotonic() + segundos
    while time.monotonic() < fin:
        if condicion():
            return True
        time.sleep(0.01)
    return condicion()


def test_el_robot_manda_y_la_pc_le_responde_sin_saber_su_ip():
    pc = ra.EnlaceAudio(ra.Colchon(objetivo=1), host_local="127.0.0.1").empezar()
    robot = ra.EnlaceAudio(ra.Colchon(objetivo=1), destino=("127.0.0.1", pc.puerto),
                           host_local="127.0.0.1").empezar()
    try:
        robot.enviar(_bloque(0.25))
        assert _esperar(lambda: pc.colchon.recibidos == 1)
        assert pc.destino == ("127.0.0.1", robot.puerto)
        pc.enviar(_bloque(0.5))
        assert _esperar(lambda: robot.colchon.recibidos == 1)
        assert round(float(robot.colchon.sacar()[0]), 2) == 0.5
        assert robot.edad_s() < 1.0
    finally:
        robot.cerrar()
        pc.cerrar()


def test_con_la_ip_del_robot_la_pc_no_atiende_a_nadie_mas():
    pc = ra.EnlaceAudio(ra.Colchon(), host_local="127.0.0.1", solo_de="10.9.9.9").empezar()
    otro = ra.EnlaceAudio(ra.Colchon(), destino=("127.0.0.1", pc.puerto),
                          host_local="127.0.0.1").empezar()
    try:
        otro.enviar(_bloque())
        assert _esperar(lambda: pc.rechazados == 1)
        assert pc.colchon.recibidos == 0 and pc.destino is None
    finally:
        otro.cerrar()
        pc.cerrar()


def test_la_basura_que_llegue_al_puerto_no_rompe_nada():
    pc = ra.EnlaceAudio(ra.Colchon(), host_local="127.0.0.1").empezar()
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.sendto(b"hola", ("127.0.0.1", pc.puerto))
        assert _esperar(lambda: pc.rechazados == 1)
        assert pc.destino is None
    finally:
        s.close()
        pc.cerrar()


# -- el núcleo y la tarjeta ---------------------------------------------------------


class _EnlaceFalso:
    def __init__(self):
        self.colchon = ra.Colchon(objetivo=1)
        self.enviado = []

    def enviar(self, muestras):
        self.enviado.append(np.array(muestras))


def test_el_nucleo_suena_lo_que_llega_y_manda_lo_del_microfono():
    enlace = _EnlaceFalso()
    enlace.colchon.meter(0, 0, _bloque(0.4))
    nucleo = ra.Nucleo(enlace, None, volumen=0.5)
    sonar = nucleo.bloque(_bloque(0.1))
    assert np.allclose(sonar, 0.2) and np.allclose(enlace.enviado[0], 0.1)


class _TarjetaQueFalla:
    def __init__(self, fallos):
        self.fallos, self.abierta, self.intentos = fallos, False, 0

    def abrir(self, procesar):
        self.intentos += 1
        if self.intentos <= self.fallos:
            raise OSError("no hay tarjeta")
        self.abierta = True

    def vivo(self):
        return self.abierta

    def cerrar(self):
        self.abierta = False


def test_sin_tarjeta_reintenta_sin_cerrarse_y_la_abre_cuando_aparece():
    tarjeta = _TarjetaQueFalla(fallos=2)
    s = ra.Supervisor(ra.Nucleo(_EnlaceFalso()), tarjeta)
    s.paso()
    assert not s.abierto and "no hay tarjeta" in s.error
    s.paso()
    s.paso()
    assert s.abierto and tarjeta.intentos == 3
    tarjeta.abierta = False            # se desenchufa
    s.paso()
    assert s.abierto and tarjeta.intentos == 4


# -- las dos mitades juntas -----------------------------------------------------------


def test_la_pc_y_el_robot_hablan_a_la_vez_por_la_red():
    from telmeow_teleop.audio_operador import AudioOperador

    narrador = voz_sintetica(6, semilla=1, nivel=0.1)
    pc_tarjeta = DispositivoSintetico(narrador)
    pc = AudioOperador(puerto=0, robot="127.0.0.1", dispositivo=pc_tarjeta, host_local="127.0.0.1")
    cerca = np.zeros(6 * FS)
    cerca[2 * FS:4 * FS] = voz_sintetica(2, semilla=2, nivel=0.05, pausas=False)
    sala = SalaSimulada(camino_eco(), cerca=cerca)
    enlace = ra.EnlaceAudio(ra.Colchon(), destino=("127.0.0.1", pc.puerto), host_local="127.0.0.1")
    robot = ra.Supervisor(ra.Nucleo(enlace, ra.Cancelador()), sala)
    pc.arrancar()
    enlace.empezar()
    robot.empezar()
    try:
        time.sleep(3.5)
        assert pc.resumen()["clave"] == "ok"
    finally:
        robot.cerrar()
        enlace.cerrar()
        pc.parar()
    sonado = np.concatenate(sala.sonado)
    oido = np.concatenate(pc_tarjeta.oido)
    assert np.sqrt(np.mean(sonado ** 2)) > 0.02        # la voz del narrador sonó en el robot
    assert np.sqrt(np.mean(oido ** 2)) > 0.001          # y a la PC llegó lo del robot
    assert pc.colchon.perdidos + enlace.colchon.perdidos < 5
    assert np.median(enlace.colchon.latencias_ms) < 150


def test_el_resumen_del_audio_para_la_pagina():
    from telmeow_teleop.audio_operador import resumen_audio

    assert resumen_audio(abierto=False, error="x", edad_s=None)["clave"] == "error"
    assert "aún no llega" in resumen_audio(abierto=True, edad_s=None)["texto"]
    assert "hace 5 s" in resumen_audio(abierto=True, edad_s=5.0)["texto"]
    assert resumen_audio(abierto=True, edad_s=0.1)["clave"] == "ok"
