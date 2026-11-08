"""Pruebas del script de la sesión de verificación (``tools/sesion_verificacion.py``).

La parte interactiva (lanzar el broker, el visor, el pipeline y la cámara) se
prueba a mano en la sesión. Aquí, lo que decide algo: cuándo hay trampa del
doble broker, el protocolo que se enseña, y las cifras del informe.
"""

from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))

import sesion_verificacion as sv  # noqa: E402


# El estado del broker y puerto_abierto: tests/test_broker.py (desde el P10).


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


# --------------------------------------------------------------------------
# La sesión del P10 (--narracion)
# --------------------------------------------------------------------------


def test_el_protocolo_del_p10_es_una_postura_por_etiqueta_y_acaba_con_q():
    sin, con = sv.PROTOCOLO_P10_SIN_INTERFAZ, sv.PROTOCOLO_P10_NARRACION
    assert [e for e, *_ in sin] == [0, 1]
    assert [e for e, *_ in con] == list(range(len(con)))
    # La etiqueta 1 es la misma, palabra por palabra: sus fps son el criterio.
    assert sin[1][1] == con[1][1]
    # La 0 es la calibración que empieza sola.
    assert all("NO pulses nada" in protocolo[0][1] for protocolo in (sin, con))
    assert "q dos veces" in con[-1][1]
    assert all(segundos > 0 for _, _, segundos, _ in sin + con)


@pytest.mark.parametrize("clave, en_el_protocolo", [
    ("c", "pulsa c"), ("l", "con l"), ("flecha_derecha", "flecha →"), ("j", "con j"),
    ("flecha_izquierda", "flecha ←"), ("k", "Pulsa k"), ("6", "Pulsa 6"), ("0", "pulsa 0"),
    ("q", "Pulsa q"),
])
def test_cada_tecla_que_se_pregunta_se_pulsa_en_alguna_etiqueta(clave, en_el_protocolo):
    assert clave in {c for c, _, _ in sv.PREGUNTAS_P10}
    assert any(en_el_protocolo in que for _, que, _, _ in sv.PROTOCOLO_P10_NARRACION)


def test_las_teclas_del_protocolo_son_las_del_navegador():
    """Dos caminos: el protocolo se escribió a mano y la tabla de teclas es la
    que comparten main.py y el visor."""
    from telmeow_teleop.ordenes import ORDENES_DE_TECLA

    for tecla, orden in (("c", "calibrar"), ("l", "pagina_siguiente"), ("j", "pagina_anterior"),
                         ("k", "pausa"), ("0", "automatico"), ("q", "salir"), (" ", "etiqueta")):
        assert ORDENES_DE_TECLA[ord(tecla)].nombre == orden
    assert ORDENES_DE_TECLA[ord("6")].valor == "concentrado"


def test_las_paginas_esperadas_son_las_del_protocolo():
    que = {e: texto for e, texto, _, _ in sv.PROTOCOLO_P10_NARRACION}
    assert "5 páginas" in que[6] and "2 páginas" in que[7] and "2 páginas" in que[12]
    assert "pasa con l" in que[8]
    assert sv.PAGINAS_P10 == {6: 5, 7: 2, 8: 1, 12: 2}


def test_la_guia_del_p10_cabe_en_la_terminal():
    guia = sv.guia_p10()
    assert max(len(linea) for linea in guia.splitlines()) <= sv.ANCHO_P10
    assert guia.index("PRIMERA GRABACIÓN") < guia.index("SEGUNDA GRABACIÓN")
    assert "Unos 1 min 10 s en total." in guia


def _escribir(ruta, columnas, filas):
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columnas, restval="")
        w.writeheader()
        w.writerows(filas)


RESPUESTAS_BUENAS = {"clic_python": "n", "clic_navegador": "n", "temblor": "n", "vistazos": "n", "vista": "f"}


def _respondedor(respuestas):
    """Un input() falso que contesta las preguntas del P10 por su texto."""
    textos = {pregunta: clave for clave, pregunta, _ in sv.PREGUNTAS_P10}

    def entrada(prompt):
        for pregunta, clave in textos.items():
            if pregunta in prompt:
                return respuestas.get(clave, "s")
        return ""
    return entrada


def _sesion_p10(tmp_path, *, fps_con=20.0, sin_gesto=False, en_pausa=False, vistazo=True,
                conserva=True, respuestas=None):
    """Las dos grabaciones del P10, fabricadas: 40 frames por etiqueta, el cuento
    y las páginas como en el protocolo."""
    from telmeow_teleop.emotion_classifier import COLUMNAS_CSV as EMOCION
    from telmeow_teleop.main import COLUMNAS_SESION

    def fila(t, etiqueta, dt, **extra):
        return {"t": f"{t:.3f}", "etiqueta": etiqueta, "dt_ms": dt * 1000, "ms_bucle": 20.0, "mqtt_hz": 19.9,
                "pausa": 0, "mqtt_fallidos": 0, "armL": -55.0, "armLtw": 0.0, "armR": -55.0, "armRtw": 0.0,
                **extra}

    t, sin = 0.0, []
    for etiqueta in (0, 1):
        for _ in range(40):
            t += 0.05
            sin.append(fila(t, etiqueta, 0.05))
    _escribir(tmp_path / "p10_sin_interfaz.csv", COLUMNAS_SESION, sin)

    t, con, emocion = 0.0, [], []
    historia, pagina = "", ""
    for etiqueta in range(len(sv.PROTOCOLO_P10_NARRACION)):
        for k in range(40):
            t += 1.0 / fps_con if etiqueta == 1 else 0.05
            extra = {}
            if etiqueta in (6, 12) and k == 0:
                historia, pagina = ("El túnel.pdf" if etiqueta == 6 else "Cambios.pdf"), 1
                extra["orden"] = f"historia={historia}@navegador"
            paso = 0
            if etiqueta in (6, 12) and k in (5, 10, 15, 20, 25)[:sv.PAGINAS_P10[etiqueta]]:
                paso = +1
            elif etiqueta == 7 and k in (5, 10):
                paso = -1
            elif etiqueta == 8 and k == 30:
                paso = +1
            if etiqueta == 8 and k < 20:
                extra["pausa"] = 1
                if k == 5:
                    extra["orden"] = "pagina_siguiente@navegador"
            if paso:
                pagina += paso
                extra["gesto"] = "page_next" if paso > 0 else "page_prev"
                extra["orden"] = ("pagina_siguiente" if paso > 0 else "pagina_anterior") + "@navegador"
                if sin_gesto and etiqueta == 7:
                    extra["gesto"] = ""
                if en_pausa and etiqueta == 12 and k == 5:
                    extra["pausa"] = 1
            if k == 39:
                extra["orden"] = "salir@navegador" if etiqueta == 13 else "etiqueta@navegador"
            con.append(fila(t, etiqueta, 0.05, historia=historia, pagina=pagina, **extra))
            if k % 2 == 0:
                emo = {9: "feliz", 10: "triste"}.get(etiqueta, "neutral")
                fuera = etiqueta in (9, 10) and vistazo and k % 12 in (6, 8)
                emocion.append({"t": f"{t:.3f}", "etiqueta": etiqueta, "rostro": 1,
                                "mirada_fuera": int(fuera),
                                "emocion": "neutral" if fuera and not conserva else emo})
    _escribir(tmp_path / "p10_narracion.csv", COLUMNAS_SESION, con)
    _escribir(tmp_path / "p10_narracion_emocion.csv", EMOCION, emocion)
    sv.preguntar_p10(tmp_path / "p10", _respondedor({**RESPUESTAS_BUENAS, **(respuestas or {})}))
    return tmp_path / "p10"


def test_el_informe_del_p10_da_por_buena_una_sesion_que_cumple(tmp_path, capsys):
    informe = sv.analizar_narracion(_sesion_p10(tmp_path))
    assert "NO CUMPLE" not in informe and "NO VALE" not in informe
    criterios = 2 + 2 + sum(1 for _, _, cumple in sv.PREGUNTAS_P10 if cumple) + 2
    assert informe.count(" OK") == criterios
    assert "cambios de pagina: 10 (esperados 10" in informe
    assert "por etiqueta, 6: 5, 7: 2, 8: 1, 12: 2" in informe
    assert "intentos de pasar de pagina en pausa: 1" in informe
    assert "ordenes desde la ventana de Python: 0" in informe
    assert "¿Qué vista del robot prefieres? (d = detrás, f = frente) frente" in informe
    assert "   9 feliz: 3 vistazos" in informe


@pytest.mark.parametrize("cambio, empieza", [
    ({"fps_con": 15.0}, "  fps medios"),
    ({"sin_gesto": True}, "  con su gesto"),
    ({"en_pausa": True}, "  en pausa:"),
    ({"conserva": False}, "   9 feliz"),
    ({"respuestas": {"q": "n"}}, "  ¿q dos veces"),
    ({"respuestas": {"clic_python": "s"}}, "  ¿Tuviste que hacer clic en la ventana de Python"),
])
def test_el_informe_del_p10_senala_lo_que_no_cumple(tmp_path, cambio, empieza):
    informe = sv.analizar_narracion(_sesion_p10(tmp_path, **cambio))
    assert any(linea.startswith(empieza) and "NO CUMPLE" in linea for linea in informe.splitlines()), informe


def test_hacer_clic_en_el_navegador_informa_pero_no_decide(tmp_path):
    informe = sv.analizar_narracion(_sesion_p10(tmp_path, respuestas={"clic_navegador": "s"}))
    assert "NO CUMPLE" not in informe
    assert "para que las teclas respondieran? si" in informe


def test_si_no_se_bajo_la_cabeza_los_vistazos_no_valen(tmp_path):
    informe = sv.analizar_narracion(_sesion_p10(tmp_path, vistazo=False))
    assert "   9 feliz: NO VALE: no se detecto ningun vistazo" in informe


def test_sin_la_segunda_grabacion_no_hay_informe(tmp_path):
    assert "Falta p10_narracion.csv" in sv.analizar_narracion(tmp_path / "p10")


def test_los_vistazos_cuentan_solo_sus_primeros_3_s():
    filas = [{"etiqueta": "9", "rostro": "1", "t": "0.0", "mirada_fuera": "0", "emocion": "feliz"}]
    filas += [{"etiqueta": "9", "rostro": "1", "t": str(0.5 * k), "mirada_fuera": "1",
               "emocion": "feliz" if k <= 6 else "neutral"} for k in range(1, 11)]
    # Diez filas mirando fuera; las de más de 3 s desde la primera no cuentan.
    assert sv.vistazos(filas, 9) == (1, 7, 6)


def test_las_preguntas_insisten_hasta_una_respuesta_valida(tmp_path, capsys):
    dadas = iter(["x", "", "sí"] + ["s"] * 20)
    respuestas = sv.preguntar_p10(tmp_path / "p10", lambda prompt: "f" if "d = detrás" in prompt else next(dadas))
    assert respuestas["clic_python"] == "s" and respuestas["vista"] == "f"
    assert capsys.readouterr().out.count("Responde s o n.") == 2
    assert sv.leer_respuestas(tmp_path / "p10") == respuestas


def test_con_narracion_y_guia_solo_ensena_las_tablas(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sv, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sv, "asegurar_broker", lambda *a, **k: pytest.fail("no debe arrancar nada"))
    assert sv.main(["--narracion", "--guia"]) == 0
    assert "SEGUNDA GRABACIÓN" in capsys.readouterr().out


def test_con_narracion_el_informe_y_la_base_son_los_suyos(monkeypatch, tmp_path):
    monkeypatch.setattr(sv, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sv, "analizar_narracion", lambda base: f"P10 DE {base.name}")
    assert sv.main(["--analizar", "--narracion"]) == 0
    assert (tmp_path / "p10_informe.txt").read_text(encoding="utf-8") == "P10 DE p10"


def test_la_sesion_del_p10_ensena_cada_tabla_antes_de_su_grabacion(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sv, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sv, "asegurar_broker", lambda arrancados, **k: True)
    eventos = []
    monkeypatch.setattr(sv.subprocess, "Popen", lambda *a, **k: pytest.fail("ni visor ni monitor"))
    monkeypatch.setattr(sv.subprocess, "run", lambda args, **k: eventos.append(("run", args)))
    responder = _respondedor(RESPUESTAS_BUENAS)

    def entrada(prompt=""):
        eventos.append(("tabla", capsys.readouterr().out))
        return responder(prompt)
    monkeypatch.setattr("builtins.input", entrada)

    assert sv.main(["--narracion"]) == 0
    lanzados = [args for tipo, args in eventos if tipo == "run"]
    assert lanzados[0][1:] == ["-m", "telmeow_teleop.main", "--record", str(tmp_path / "p10_sin_interfaz.csv")]
    assert lanzados[1][1:] == ["-m", "telmeow_teleop.control_panel", "--record",
                               str(tmp_path / "p10_narracion.csv")]
    # Lo impreso antes de cada grabación es su tabla.
    antes = [i for i, (tipo, _) in enumerate(eventos) if tipo == "run"]
    impreso_antes = ["".join(texto for tipo, texto in eventos[:i] if tipo == "tabla") for i in antes]
    assert "PRIMERA GRABACIÓN" in impreso_antes[0] and "SEGUNDA GRABACIÓN" not in impreso_antes[0]
    assert "SEGUNDA GRABACIÓN" in impreso_antes[1]
    assert sv.leer_respuestas(tmp_path / "p10")["vista"] == "f"
    assert (tmp_path / "p10_informe.txt").is_file()


def test_una_grabacion_que_ya_existe_solo_se_repite_si_se_pide(monkeypatch, tmp_path):
    monkeypatch.setattr(sv, "PROJECT_ROOT", tmp_path)
    (tmp_path / "p10_sin_interfaz.csv").write_text("ya", encoding="utf-8")
    lanzados = []
    monkeypatch.setattr(sv.subprocess, "run", lambda args, **k: lanzados.append(args))
    responder = _respondedor(RESPUESTAS_BUENAS)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n" if "Ya existe" in prompt else responder(prompt))
    sv._sesion_narracion(tmp_path / "p10", "python")
    assert [args[2] for args in lanzados] == ["telmeow_teleop.control_panel"]
