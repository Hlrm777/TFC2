"""Pruebas del script de la sesión de verificación (``tools/sesion_verificacion.py``).

La parte interactiva (lanzar el broker, el visor, el pipeline y la cámara) se
prueba a mano en la sesión. Aquí, lo que decide algo: cuándo hay trampa del
doble broker, el protocolo que se enseña, y las cifras del informe.
"""

from __future__ import annotations

import csv
import math
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


def test_el_protocolo_de_los_gestos_es_una_postura_por_etiqueta():
    """Una postura por etiqueta y con referencia concreta: lo que el informe
    espera de cada una tiene que estar en la tabla que lee el operador."""
    etiquetas = [e for e, *_ in sv.PROTOCOLO_GESTOS]
    assert etiquetas == list(range(14))
    que = {e: t for e, t, *_ in sv.PROTOCOLO_GESTOS}
    assert "DERECHA" in que[3] and "IZQUIERDA" in que[5] and "grito" in que[7]
    assert "POR FUERA de los hombros" in que[11] and "Saluda" in que[12]
    assert set(sv.GESTO_ESPERADO) | set(sv.SIN_GESTO) <= set(etiquetas)
    assert not set(sv.GESTO_ESPERADO) & set(sv.SIN_GESTO)
    tabla = sv.tabla_protocolo(sv.PROTOCOLO_GESTOS)
    assert "2 min 50 s" in tabla and "NO pulses c" in tabla


def _sesion_de_gestos(tmp_path, *, derecha_en_3=1.0, izquierda_en_3=0.0, en_11=0.0, temblor=0.05):
    """Escribe los CSV de cabeza y brazos de una sesión de 2 s por etiqueta."""
    from telmeow_teleop.arm_tracker import COLUMNAS_CSV as BRAZOS
    from telmeow_teleop.head_tracker import COLUMNAS_CSV as CABEZA

    def escribir(ruta, columnas, filas):
        with open(ruta, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=columnas, restval="")
            w.writeheader()
            w.writerows(filas)

    cabeza, brazos = [], []
    t = 0.0
    for etiqueta in range(14):
        for k in range(40):
            t += 0.05
            cabeza.append({"t": t, "etiqueta": etiqueta, "pan": temblor * (k % 2), "tilt": 0.0,
                           "raw_pan": 0.0, "raw_tilt": 0.0})
            pesos = {3: (izquierda_en_3, derecha_en_3), 5: (1.0, 0.0), 7: (1.0, 1.0),
                     9: (1.0, 1.0), 11: (en_11, en_11)}.get(etiqueta, (0.0, 0.0))
            brazos.append({"t": t, "etiqueta": etiqueta,
                           "mano_cara_L": pesos[0], "mano_cara_R": pesos[1],
                           "armL": -57.0, "armLtw": -125.0, "armR": -57.0, "armRtw": -125.0,
                           "saludo_R": 1.0 if etiqueta == 12 else 0.0})
    escribir(tmp_path / "g_cabeza.csv", CABEZA, cabeza)
    escribir(tmp_path / "g_brazos.csv", BRAZOS, brazos)
    return tmp_path / "g"


def test_el_informe_de_gestos_da_por_buena_una_sesion_que_cumple(tmp_path):
    informe = sv.analizar_gestos(_sesion_de_gestos(tmp_path))
    assert "NO CUMPLE" not in informe
    assert informe.count("OK") == 2 + 4 + 5      # temblor, gestos esperados, sin gesto
    assert "el robot saludo en 100 %" in informe
    assert "salto mediano 0.00" in informe


@pytest.mark.parametrize("cambio, donde", [
    ({"derecha_en_3": 0.0}, "   3 "),               # el brazo no fue
    ({"izquierda_en_3": 1.0}, "   3 "),             # fue también el otro
    ({"en_11": 1.0}, "  11 "),                      # «yo no fui» dispara el gesto
    ({"temblor": 1.0}, "   0 quieto"),              # la cabeza sigue temblando
])
def test_el_informe_de_gestos_senala_lo_que_no_cumple(tmp_path, cambio, donde):
    informe = sv.analizar_gestos(_sesion_de_gestos(tmp_path, **cambio))
    assert any("NO CUMPLE" in linea and linea.startswith(donde) for linea in informe.splitlines())


def test_el_temblor_crudo_es_el_de_la_face_mesh_y_sin_el_arranque():
    filas = [{"t": 0.05 * k, "etiqueta": 0, "raw_pan": 9.0 * (k % 2), "raw_tilt": 0.0} for k in range(60)]
    filas += [{"t": 3.5 + 0.05 * k, "etiqueta": 0, "raw_pan": 0.5 * (k % 2), "raw_tilt": 0.0} for k in range(60)]
    filas += [{"t": 7.0 + 0.05 * k, "etiqueta": 1, "raw_pan": 4.0 * (k % 2), "raw_tilt": 0.0} for k in range(100)]
    assert sv.temblor_crudo(filas) == pytest.approx(0.5)
    assert sv.temblor_crudo(filas, etiqueta=1) == pytest.approx(4.0)
    assert sv.temblor_crudo(filas, etiqueta=5) is None


@pytest.mark.parametrize("pipeline, demo, dice", [
    (1.3, 0.36, "PIPELINE"),           # el demo solo sigue como en septiembre
    (1.3, 1.2, "NO es el pipeline"),   # los dos tiemblan: cambió otra cosa
    (0.4, 0.36, "mirar"),              # hoy no tiembla ninguno
    (1.3, None, "sin las dos"),
])
def test_de_donde_sale_el_temblor(pipeline, demo, dice):
    assert dice in sv.de_donde_sale_el_temblor(pipeline, demo)


def test_el_informe_de_gestos_compara_con_el_demo_si_lo_hay(tmp_path):
    base = _sesion_de_gestos(tmp_path)
    assert "demo solo --" in sv.analizar_gestos(base)
    with open(tmp_path / "g_demo_cabeza.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["t", "etiqueta", "raw_pan", "raw_tilt"])
        w.writeheader()
        w.writerows({"t": 0.05 * k, "etiqueta": 0, "raw_pan": 0.3 * (k % 2), "raw_tilt": 0.0} for k in range(200))
    assert "demo solo 0.30" in sv.analizar_gestos(base)


def test_el_protocolo_del_p7_6_prueba_lo_que_se_cambio():
    etiquetas = [e for e, *_ in sv.PROTOCOLO_AJUSTES]
    assert etiquetas == list(range(13))
    que = {e: t for e, t, *_ in sv.PROTOCOLO_AJUSTES}
    assert "teclado" in que[1] and "techo" in que[1]          # el cabeceo del H64
    assert "mejillas" in que[4] and "gira" in que[4]          # la cabeza tiene que seguir
    assert "codos" in que[8] and "codos a la vista" in que[9]  # la prueba del H65
    assert "APAGA LA LUZ" in que[11] and "Prende" in que[12]
    assert "NO pulses c" in sv.tabla_protocolo(sv.PROTOCOLO_AJUSTES)


def _sesion_de_ajustes(tmp_path, *, enojo_en_1=0.0, guarda_en_4=0.0, codo_y=400.0, oscila=False):
    """Los CSV de sesión, brazos y emoción de una sesión del P7.6 de 2 s por etiqueta."""
    from telmeow_teleop.arm_tracker import COLUMNAS_CSV as BRAZOS
    from telmeow_teleop.emotion_classifier import COLUMNAS_CSV as EMOCION
    from telmeow_teleop.main import COLUMNAS_SESION

    def escribir(ruta, columnas, filas):
        with open(ruta, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=columnas, restval="")
            w.writeheader()
            w.writerows(filas)

    sesion, brazos, emocion = [], [], []
    t = 0.0
    for etiqueta in range(13):
        for k in range(160 if etiqueta == 12 else 40):     # la 12 se mira pasados 6 s
            t += 0.05
            tapada = (etiqueta == 5) or (etiqueta == 4 and k < 40 * guarda_en_4)
            sesion.append({"t": t, "etiqueta": etiqueta, "pan": 15.0 * math.sin(k / 6) if etiqueta == 4 else 0.0,
                           "tilt": 0.0, "cabeza_tapada": int(tapada),
                           "ruido_malla": 2.4 if etiqueta == 11 else 0.9,
                           "zona_muerta": 2.4 if etiqueta == 11 else 1.0, "pausa_luz": 0})
            abd = -5.0 + (50.0 if oscila and etiqueta == 9 and k % 4 == 0 else 0.0)
            brazos.append({"t": t, "etiqueta": etiqueta, "alto": 480,
                           "armL": abd, "armLtw": -137.0, "armR": -5.0, "armRtw": -137.0,
                           "img_codo_y_L": codo_y, "img_codo_y_R": codo_y})
            if k % 4 == 0:
                emo = "enojado" if (etiqueta == 2 or (etiqueta == 1 and k < 40 * enojo_en_1)) else "neutral"
                emocion.append({"t": t, "etiqueta": etiqueta, "rostro": 1, "emocion": emo})
    escribir(tmp_path / "a.csv", COLUMNAS_SESION, sesion)
    escribir(tmp_path / "a_brazos.csv", BRAZOS, brazos)
    escribir(tmp_path / "a_emocion.csv", EMOCION, emocion)
    return tmp_path / "a"


def test_el_informe_del_p7_6_da_por_buena_una_sesion_que_cumple(tmp_path):
    informe = sv.analizar_ajustes(_sesion_de_ajustes(tmp_path))
    assert "NO CUMPLE" not in informe and "NO VALE" not in informe
    assert informe.count("OK") == 2 + 3 + 1 + 1               # emoción, cabeza, codos, luz


@pytest.mark.parametrize("cambio, dice", [
    ({"enojo_en_1": 0.5}, "   1 enojado"),
    ({"guarda_en_4": 0.5}, "   4 mejillas"),
    ({"oscila": True}, "   9 codos a la vista"),
])
def test_el_informe_del_p7_6_senala_lo_que_no_cumple(tmp_path, cambio, dice):
    informe = sv.analizar_ajustes(_sesion_de_ajustes(tmp_path, **cambio))
    assert any(linea.startswith(dice) and "NO CUMPLE" in linea for linea in informe.splitlines())


def test_si_los_codos_no_se_vieron_la_prueba_no_vale(tmp_path):
    informe = sv.analizar_ajustes(_sesion_de_ajustes(tmp_path, codo_y=560.0))
    assert "NO VALE: los codos no se vieron" in informe


def test_con_ajustes_el_informe_y_la_base_son_los_suyos(monkeypatch, tmp_path):
    monkeypatch.setattr(sv, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sv, "analizar_ajustes", lambda base: f"AJUSTES DE {base.name}")
    assert sv.main(["--analizar", "--ajustes"]) == 0
    assert (tmp_path / "p76_informe.txt").read_text(encoding="utf-8") == "AJUSTES DE p76"


def test_con_gestos_el_informe_y_la_base_son_los_suyos(monkeypatch, tmp_path):
    monkeypatch.setattr(sv, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sv, "analizar_gestos", lambda base: f"GESTOS DE {base.name}")
    assert sv.main(["--analizar", "--gestos"]) == 0
    assert (tmp_path / "p75_informe.txt").read_text(encoding="utf-8") == "GESTOS DE p75"


def test_analizar_escribe_el_informe(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sv, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sv, "analizar", lambda base: f"INFORME DE {base.name}")
    assert sv.main(["--analizar", "--base", "prueba"]) == 0
    assert (tmp_path / "prueba_informe.txt").read_text(encoding="utf-8") == "INFORME DE prueba"
    assert "INFORME DE prueba" in capsys.readouterr().out
