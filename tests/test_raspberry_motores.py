"""Los motores del robot (``raspberry/motor_driver.py``) contra motores
DYNAMIXEL simulados a nivel de bytes (``tools/simulador_dynamixel.py``).

Entre el programa y el simulador va la biblioteca oficial de ROBOTIS
(``dynamixel_sdk``), sin tocar: lo que aquí se prueba es lo que saldría por el
cable. El reloj es falso, para no esperar a que los motores lleguen.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("dynamixel_sdk")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import simulador_dynamixel as sim  # noqa: E402
from raspberry import motor_driver as md  # noqa: E402
from telmeow_teleop import limits  # noqa: E402
from telmeow_teleop.config import MQTTConfig  # noqa: E402

POSE = {"pan": 20.0, "tilt": -10.0, "armR": -55.0, "armRtw": -90.0, "armL": -55.0, "armLtw": 0.0}


class Banco:
    """Seis motores simulados, el programa y un reloj que se mueve a mano."""

    def __init__(self, ids=(1, 2, 3, 4, 5, 6), **ajustes):
        from dynamixel_sdk import PacketHandler

        self.t = 100.0
        self.bus = sim.SimuladorDynamixel(ids, reloj=lambda: self.t)
        self.puerto = sim.PuertoSimulado(self.bus)
        self.protocolo = PacketHandler(2.0)
        self.pose = md.Pose((a.campo for a in md.ARTICULACIONES), reloj=lambda: self.t)
        self.avisos: list[dict] = []
        self.aperturas = 0
        self.ctl = md.Controlador(self.pose, self._abrir, ajustes=md.Ajustes(**ajustes),
                                  avisar=self.avisos.append, reloj=lambda: self.t)

    def _abrir(self):
        self.aperturas += 1
        self.puerto.openPort()
        return self.puerto, self.protocolo

    def correr(self, segundos: float, pose: dict | None = None, hz: float = 20.0) -> None:
        """Deja pasar el tiempo dando vueltas al programa, con poses a 20 Hz."""
        for _ in range(round(segundos * hz)):
            if pose is not None:
                self.pose.recibir(pose)
            self.ctl.paso()
            self.t += 1.0 / hz

    def grados(self) -> dict[str, float]:
        return {a.campo: md.a_grados(round(self.bus.motor(a.id).posicion), a)
                for a in md.ARTICULACIONES if self.bus.motor(a.id) is not None}


# -- el contrato y los números ------------------------------------------------


def test_el_topic_y_los_campos_son_los_del_contrato():
    assert md.TOPIC_POSE == MQTTConfig.topic_pose
    assert {a.campo for a in md.ARTICULACIONES} == {"pan", "tilt", "armL", "armLtw", "armR",
                                                    "armRtw"}
    assert sorted(a.id for a in md.ARTICULACIONES) == [1, 2, 3, 4, 5, 6]
    fuente = Path(md.__file__).read_text(encoding="utf-8")
    assert "import telmeow_teleop" not in fuente and "from telmeow_teleop" not in fuente


def test_los_recorridos_son_los_de_limits():
    por_campo = {a.campo: (a.minimo, a.maximo) for a in md.ARTICULACIONES}
    assert por_campo["pan"] == limits.HEAD_PAN_RANGE
    assert por_campo["tilt"] == limits.HEAD_TILT_RANGE
    assert por_campo["armRtw"] == por_campo["armLtw"] == limits.FLEXION_RANGE_EXTENDIDO
    # La abducción depende de la flexión (lo recorta la PC): aquí, su envolvente.
    for campo, lado in (("armR", limits.Side.R), ("armL", limits.Side.L)):
        filas = limits._ARM_TABLES[lado] + limits._ARM_TABLES_EXT[lado]
        assert por_campo[campo] == (min(f[1] for f in filas), max(f[2] for f in filas))


def test_las_direcciones_son_las_del_manual_del_xl430():
    # Las dos listas se escribieron por separado, cada una contra el manual.
    for nombre in ("D_ID", "D_MODO", "D_PAR", "D_ERROR_HW", "D_ACELERACION", "D_VELOCIDAD",
                   "D_META", "D_POSICION", "D_TEMPERATURA"):
        assert getattr(md, nombre) == getattr(sim, nombre), nombre
    assert (md.D_ID, md.D_MODO, md.D_PAR, md.D_ERROR_HW) == (7, 11, 64, 70)
    assert (md.D_ACELERACION, md.D_VELOCIDAD, md.D_META) == (108, 112, 116)
    assert (md.D_POSICION, md.D_TEMPERATURA) == (132, 146)


@pytest.mark.parametrize("grados, signo, cero, esperado", [
    (0.0, 1, 2048, 2048), (90.0, 1, 2048, 3072), (-90.0, 1, 2048, 1024),
    (90.0, -1, 2048, 1024), (-180.0, 1, 2048, 0), (45.0, 1, 2000, 2512),
    (-180.0, -1, 2048, 4095),            # 4096 no existe: se queda en el último pulso
    (500.0, 1, 2048, 3072), (-500.0, 1, 2048, 0),     # fuera del recorrido: recortado
])
def test_de_grados_a_pulsos(grados, signo, cero, esperado):
    flexion = md.Articulacion("armRtw", 3, -180.0, 90.0, signo, cero)
    assert md.a_pulsos(grados, flexion) == esperado


def test_ida_y_vuelta_de_grados_a_pulsos():
    for a in md.ARTICULACIONES:
        for grados in (a.minimo, 0.0, a.maximo, 12.3):
            assert abs(md.a_grados(md.a_pulsos(grados, a), a) - grados) < 0.05


def test_la_pose_guarda_solo_numeros_y_conserva_lo_que_falta():
    reloj = SimpleNamespace(t=0.0)
    pose = md.Pose(("pan", "tilt"), reloj=lambda: reloj.t)
    assert pose.leer() == ({}, None)
    assert pose.recibir({"pan": 10, "tilt": 2.5, "emo": "feliz"})
    reloj.t = 3.0
    assert pose.recibir({"pan": 11})
    for malo in ({"pan": "12"}, {"pan": float("nan")}, {"pan": float("inf")}, {"pan": True},
                 {"emo": "feliz"}, [1], None, "x"):
        assert not pose.recibir(malo)
    reloj.t = 3.5
    assert pose.leer() == ({"pan": 11.0, "tilt": 2.5}, 0.5)


# -- la configuración -----------------------------------------------------------


def test_la_configuracion_cambia_el_signo_el_cero_y_los_ajustes(tmp_path):
    ruta = tmp_path / "motores.json"
    ruta.write_text(json.dumps({"articulaciones": {"pan": {"signo": -1, "cero": 2100}},
                                "ajustes": {"velocidad": 100}}), encoding="utf-8")
    articulaciones, ajustes = md.cargar_config(ruta)
    pan = next(a for a in articulaciones if a.campo == "pan")
    assert (pan.signo, pan.cero, pan.id) == (-1, 2100, 2)
    assert ajustes.velocidad == 100 and ajustes.velocidad_lenta == md.Ajustes().velocidad_lenta
    assert md.cargar_config(None) == (md.ARTICULACIONES, md.Ajustes())


@pytest.mark.parametrize("malo", [
    {"articulaciones": {"codo": {"signo": 1}}},
    {"articulaciones": {"pan": {"signo": 2}}},
    {"articulaciones": {"pan": {"cero": 5000}}},
    {"articulaciones": {"pan": {"id": 1}}},           # el 1 ya es el tilt
    {"articulaciones": {"pan": {"sentido": -1}}},
    {"ajustes": {"rapidez": 3}},
])
def test_una_configuracion_imposible_no_arranca(tmp_path, malo):
    ruta = tmp_path / "motores.json"
    ruta.write_text(json.dumps(malo), encoding="utf-8")
    with pytest.raises(ValueError):
        md.cargar_config(ruta)


# -- la puesta en marcha ----------------------------------------------------------


def test_al_arrancar_pone_modo_posicion_perfil_y_par_y_no_mueve_nada():
    banco = Banco()
    banco.correr(1.0)                       # sin ninguna pose
    assert banco.ctl.motores.listos == {1, 2, 3, 4, 5, 6}
    for motor in banco.bus.motores:
        assert motor.par and motor.leer(sim.D_MODO, 1) == 3
        assert motor.leer(sim.D_ACELERACION, 4) == md.Ajustes().aceleracion
        assert motor.metas == []            # nadie le pidió moverse
        assert round(motor.posicion) == 2048
    assert banco.bus.corruptos == 0


def test_el_modo_se_escribe_sin_par_aunque_el_motor_lo_trajera_puesto():
    banco = Banco()
    for motor in banco.bus.motores:         # como si el programa se hubiera reiniciado
        motor.tabla[sim.D_PAR] = 1
    banco.correr(0.2)
    assert banco.ctl.motores.listos == {1, 2, 3, 4, 5, 6}


def test_lleva_los_motores_a_la_pose_y_cada_campo_va_a_su_motor():
    banco = Banco()
    banco.correr(8.0, POSE)
    for campo, grados in banco.grados().items():
        assert abs(grados - POSE[campo]) < 0.1, campo
    assert banco.bus.motor(2).metas[-1] == md.a_pulsos(20.0, md.ARTICULACIONES[1])   # pan
    assert banco.bus.motor(3).metas[-1] == 1024                                      # armRtw -90
    assert banco.bus.corruptos == 0


def test_las_seis_posiciones_salen_en_un_solo_paquete():
    banco = Banco()
    banco.correr(1.0, POSE)
    sincronizadas = [p for _, inst, p in banco.bus.paquetes if inst == sim.INST_ESCRITURA_SINC]
    assert sincronizadas
    assert all(len(p) == 4 + 6 * 5 for p in sincronizadas)        # 6 motores x (id + 4 bytes)
    assert all(p[:4] == bytes([116, 0, 4, 0]) for p in sincronizadas)


def test_una_pose_fuera_del_recorrido_llega_recortada_al_motor():
    banco = Banco()
    banco.correr(12.0, {**POSE, "pan": 400.0, "tilt": -400.0})
    grados = banco.grados()
    assert abs(grados["pan"] - 90.0) < 0.1 and abs(grados["tilt"] + 39.0) < 0.1
    assert all(0 <= meta <= 4095 for motor in banco.bus.motores for meta in motor.metas)


# -- la seguridad -----------------------------------------------------------------


def test_el_primer_movimiento_es_lento_y_luego_va_a_velocidad_normal():
    banco = Banco()
    banco.correr(0.5, POSE)
    lenta, normal = md.Ajustes().velocidad_lenta, md.Ajustes().velocidad
    assert all(m.leer(sim.D_VELOCIDAD, 4) == lenta for m in banco.bus.motores)
    # A los 0.5 s el brazo derecho (90 grados de camino) no puede haber llegado.
    recorrido = abs(banco.grados()["armRtw"])
    assert 5.0 < recorrido < lenta * md.GRADOS_S_POR_UNIDAD * 0.5 + 1.0
    banco.correr(4.0, POSE)                 # 90 grados a 41 grados/s: llega
    assert all(m.leer(sim.D_VELOCIDAD, 4) == normal for m in banco.bus.motores)
    assert abs(banco.grados()["armRtw"] + 90.0) < 0.1


def test_sin_poses_el_robot_se_queda_quieto_con_el_par_puesto():
    banco = Banco()
    banco.correr(8.0, POSE)
    banco.correr(1.5)                       # el broker se cayó: pasa el segundo de gracia
    metas = [len(m.metas) for m in banco.bus.motores]
    antes = banco.grados()
    banco.correr(5.0)                       # ...y ya no se le manda nada
    assert [len(m.metas) for m in banco.bus.motores] == metas
    assert banco.grados() == antes
    assert all(m.par for m in banco.bus.motores)
    assert not banco.ctl.moviendo


def test_al_volver_las_poses_tras_un_silencio_entra_despacio_otra_vez():
    banco = Banco()
    banco.correr(8.0, POSE)
    banco.correr(3.0)
    banco.correr(0.3, {**POSE, "pan": -60.0})
    assert banco.bus.motor(2).leer(sim.D_VELOCIDAD, 4) == md.Ajustes().velocidad_lenta
    banco.correr(4.0, {**POSE, "pan": -60.0})
    assert banco.bus.motor(2).leer(sim.D_VELOCIDAD, 4) == md.Ajustes().velocidad
    assert abs(banco.grados()["pan"] + 60.0) < 0.1


def test_un_motor_averiado_se_queda_apagado_y_los_demas_siguen():
    banco = Banco()
    banco.correr(8.0, POSE)
    banco.bus.motor(4).averiar()            # sobrecarga en el brazo derecho
    banco.correr(3.0, {**POSE, "pan": -30.0})
    assert 4 in banco.ctl.motores.averiados and 4 not in banco.ctl.motores.listos
    assert not banco.bus.motor(4).par       # nadie le volvió a poner el par
    assert abs(banco.grados()["pan"] + 30.0) < 0.1
    assert banco.avisos[-1]["averiados"] == {"4": 0x20}
    assert banco.avisos[-1]["listos"] == [1, 2, 3, 5, 6]


def test_un_motor_que_falta_no_impide_mover_los_otros_y_se_engancha_al_aparecer():
    banco = Banco(ids=(1, 2, 3, 4, 5))
    banco.correr(8.0, POSE)
    assert banco.ctl.motores.listos == {1, 2, 3, 4, 5}
    assert banco.avisos[-1]["faltan"] == [6]
    assert abs(banco.grados()["pan"] - 20.0) < 0.1
    banco.bus.motores.append(sim.Motor(6, reloj=lambda: banco.t))     # lo enchufan
    banco.correr(8.0, POSE)
    assert banco.ctl.motores.listos == {1, 2, 3, 4, 5, 6}
    assert abs(banco.grados()["armL"] + 55.0) < 0.1


def test_un_motor_que_se_desenchufa_deja_de_contar_y_no_tumba_el_programa():
    banco = Banco()
    banco.correr(8.0, POSE)
    banco.bus.desenchufar(5)
    banco.correr(3.0, {**POSE, "tilt": 15.0})
    assert banco.ctl.motores.listos == {1, 2, 3, 4, 6}
    assert abs(banco.grados()["tilt"] - 15.0) < 0.1


def test_si_el_usb_desaparece_reintenta_y_al_volver_sigue():
    banco = Banco()
    banco.correr(8.0, POSE)
    banco.puerto.roto = True                # alguien desenchufa la OpenRB
    banco.correr(5.0, POSE)
    assert banco.ctl.motores is None and banco.ctl.fallos_de_bus == 1
    assert 3 <= banco.aperturas <= 4        # un intento cada 2 s, no uno por vuelta
    banco.puerto.roto = False
    banco.correr(8.0, {**POSE, "pan": -40.0})
    assert banco.ctl.motores.listos == {1, 2, 3, 4, 5, 6}
    assert abs(banco.grados()["pan"] + 40.0) < 0.1


def test_cuenta_su_estado_una_vez_por_segundo():
    banco = Banco()
    banco.correr(5.0, POSE)
    assert 5 <= len(banco.avisos) <= 6
    ultimo = banco.avisos[-1]
    assert ultimo["listos"] == [1, 2, 3, 4, 5, 6] and ultimo["faltan"] == []
    assert ultimo["moviendo"] is True and ultimo["sin_pose_s"] == 0.0
    assert ultimo["temperatura"]["1"] == 30
    json.dumps(ultimo)


def test_soltar_quita_el_par_al_salir():
    banco = Banco()
    banco.correr(1.0, POSE)
    banco.ctl.cerrar(soltar=True)
    assert not any(m.par for m in banco.bus.motores)


# -- la puesta en marcha de los motores: escanear y dar ID --------------------------


def test_escanear_dice_quien_contesta():
    banco = Banco(ids=(1, 4))
    assert md.escanear(banco.puerto, banco.protocolo) == {1: 1060, 4: 1060}


def test_cambiar_el_id_de_un_motor_recien_sacado_de_la_caja():
    banco = Banco(ids=(1,))
    md.cambiar_id(banco.puerto, banco.protocolo, 1, 4)
    assert md.escanear(banco.puerto, banco.protocolo) == {4: 1060}


def test_no_cambia_el_id_si_no_esta_o_si_el_nuevo_ya_existe():
    banco = Banco(ids=(1, 4))
    with pytest.raises(ValueError, match="ya hay un motor"):
        md.cambiar_id(banco.puerto, banco.protocolo, 1, 4)
    with pytest.raises(ValueError, match="no contesta"):
        md.cambiar_id(banco.puerto, banco.protocolo, 9, 5)
    with pytest.raises(ValueError):
        md.cambiar_id(banco.puerto, banco.protocolo, 1, 300)
    assert md.escanear(banco.puerto, banco.protocolo) == {1: 1060, 4: 1060}


def test_la_orden_de_escanear_y_la_de_cambiar_id(capsys):
    banco = Banco(ids=(1,))

    def abrir(puerto, baudios):
        return banco.puerto, banco.protocolo

    assert md.main(["--cambiar-id", "1", "3"], abrir=abrir) == 0
    assert md.main(["--escanear"], abrir=abrir) == 0
    salida = capsys.readouterr().out
    assert "ID 3: modelo 1060 -> armRtw" in salida and "Faltan los ID [1, 2, 4, 5, 6]" in salida
    assert md.main(["--cambiar-id", "9", "2"], abrir=abrir) == 1


# -- el simulador, por sí mismo -----------------------------------------------------


def test_el_crc_del_simulador_es_el_de_la_biblioteca():
    from dynamixel_sdk import PacketHandler

    protocolo = PacketHandler(2.0)
    for datos in (b"", b"\xff\xff\xfd\x00\x01\x03\x00\x01", bytes(range(256))):
        assert sim.crc16(datos) == protocolo.updateCRC(0, list(datos), len(datos))


def test_el_simulador_rechaza_lo_que_rechaza_un_motor():
    banco = Banco()
    p, ph = banco.puerto, banco.protocolo
    assert ph.write1ByteTxRx(p, 1, sim.D_PAR, 1) == (0, 0)
    assert ph.write1ByteTxRx(p, 1, sim.D_MODO, 3)[1] == sim.ERR_ACCESO          # EEPROM con par
    assert ph.write4ByteTxRx(p, 1, sim.D_META, 5000)[1] == sim.ERR_LIMITE
    assert ph.write4ByteTxRx(p, 1, sim.D_POSICION, 100)[1] == sim.ERR_ACCESO    # solo lectura
    assert ph.ping(p, 9)[1] != 0                                                # no existe
    banco.bus.motor(1).averiar()
    assert ph.read1ByteTxRx(p, 1, sim.D_ERROR_HW)[::2] == (0x20, sim.ALERTA)
    assert ph.write1ByteTxRx(p, 1, sim.D_PAR, 1)[1] & 0x7F == sim.ERR_ACCESO
    assert ph.reboot(p, 1)[0] == 0
    assert ph.read1ByteTxRx(p, 1, sim.D_ERROR_HW)[::2] == (0, 0)


def test_un_paquete_corrupto_no_se_obedece():
    bus = sim.SimuladorDynamixel((1,))
    bueno = sim._paquete(1, sim.INST_ESCRIBIR, bytes([sim.D_PAR, 0, 1]))
    malo = bueno[:-1] + bytes([bueno[-1] ^ 0xFF])
    assert bus.recibir(malo) == b"" and bus.corruptos == 1 and not bus.motor(1).par
    assert bus.recibir(bueno[:5]) == b""                # a trozos, como llega por el cable
    assert bus.recibir(bueno[5:]) != b"" and bus.motor(1).par


def test_el_motor_simulado_se_mueve_a_la_velocidad_del_perfil():
    reloj = SimpleNamespace(t=0.0)
    motor = sim.Motor(1, reloj=lambda: reloj.t)
    motor.escribir(sim.D_VELOCIDAD, (30).to_bytes(4, "little"))
    motor.escribir(sim.D_PAR, b"\x01")
    motor.escribir(sim.D_META, (3072).to_bytes(4, "little"))
    reloj.t = 1.0
    # 30 unidades x 0.229 rev/min = 6.87 rev/min = 469 pulsos por segundo
    assert abs(motor.posicion - (2048 + 469)) < 1.0
    reloj.t = 10.0
    assert motor.posicion == 3072
