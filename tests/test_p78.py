"""Pruebas del P7.8, lo último antes de cerrar el P7 (2026-11-08).

1. La zona muerta de 5° en el comando de los brazos, como la de la cabeza: quita
   el temblor pequeño del brazo quieto y no toca el reposo, el saludo ni la mano
   a la cara.
2. Los vistazos fuera de la pantalla: con la cabeza más de 8° abajo o 20° a un
   lado la cara no vota y el robot conserva la expresión, hasta 3 s.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

from telmeow_teleop.config import AppConfig
from telmeow_teleop.emotion_classifier import COLUMNAS_CSV, Distribucion, EmotionClassifier
from telmeow_teleop.limits import Side
from test_confianza_mano import tracker_con_escenas

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))


# --------------------------------------------------------------------------
# 1. La zona muerta de los brazos
# --------------------------------------------------------------------------


def test_la_zona_quita_los_cambios_pequenos_y_deja_pasar_los_grandes():
    tracker, *_ = tracker_con_escenas(zona_muerta=5.0)
    objetivo = (-30.0, -100.0)
    salidas = [tracker._aplicar_zona(Side.L, -30.0 + d, -100.0 - d, objetivo)
               for d in (0.0, 2.0, -3.0, 4.0, -4.5)]
    assert all(s == (-30.0, -100.0) for s in salidas)          # todo dentro de 5°
    assert tracker._aplicar_zona(Side.L, -20.0, -100.0, objetivo) == (-25.0, -100.0)


def test_la_zona_no_toca_el_reposo_el_saludo_ni_la_mano_a_la_cara():
    tracker, *_ = tracker_con_escenas(zona_muerta=5.0)
    reposo = tracker.reposo[Side.L]
    tracker._aplicar_zona(Side.L, -40.0, -60.0, (-40.0, -60.0))
    assert tracker._aplicar_zona(Side.L, *reposo, reposo) == pytest.approx(reposo)   # exacto
    tracker._pesos_saludo[Side.L] = 0.5
    assert tracker._aplicar_zona(Side.L, -10.0, -170.0, (-10.0, -170.0)) == (-10.0, -170.0)
    tracker._pesos_saludo[Side.L] = 0.0
    tracker._pesos_cara[Side.L] = 1.0
    assert tracker._aplicar_zona(Side.L, -57.0, -125.0, (-57.0, -125.0)) == (-57.0, -125.0)


def test_al_soltar_la_zona_arranca_desde_donde_esta_el_brazo():
    tracker, *_ = tracker_con_escenas(zona_muerta=5.0)
    tracker._pesos_saludo[Side.L] = 1.0
    tracker._aplicar_zona(Side.L, -12.0, -160.0, (-12.0, -160.0))
    tracker._pesos_saludo[Side.L] = 0.0
    assert tracker._aplicar_zona(Side.L, -13.0, -159.0, (-13.0, -159.0)) == (-13.0, -159.0)


def test_sin_zona_no_hace_nada_y_la_configuracion_la_pone_en_5():
    tracker, *_ = tracker_con_escenas()
    assert tracker.zona_muerta == 0.0
    assert tracker._aplicar_zona(Side.L, -30.4, -100.2, (0.0, 0.0)) == (-30.4, -100.2)
    cfg = AppConfig.from_env({})
    assert cfg.arm.zona_muerta == 5.0
    assert AppConfig.from_env({"TELMEOW_ARM_ZONA_MUERTA": "0"}).arm.zona_muerta == 0.0


@pytest.mark.skipif(not (RAIZ / "p77_brazos.csv").exists(), reason="falta la sesión p77")
def test_en_p77_la_zona_quita_el_temblor_de_yo_no_fui():
    """El ArmTracker de verdad, reproducido sobre p77 con la zona, frente a lo que
    se publicó (sin zona): en «yo no fui» el brazo izquierdo cambiaba más de 1°
    unas 400 veces por minuto; con 5°, menos de 80. Y en reposo, exacto."""
    import reproducir_brazos as rb

    filas = rb.leer(RAIZ / "p77_brazos.csv")
    con = rb.reproducir(filas, mano_cara=True, zona_muerta=5.0)

    def por_minuto(comandos, t):
        s = [max(abs(a[0] - b[0]), abs(a[1] - b[1])) for a, b in zip(comandos, comandos[1:])]
        return sum(1 for x in s if x > 1.0) / ((t[-1] - t[0]) / 60.0)

    idx = [i for i, f in enumerate(filas) if f["etiqueta"] == "12"]
    t = [float(filas[i]["t"]) for i in idx]
    grabado = [(float(filas[i]["armL"]), float(filas[i]["armLtw"])) for i in idx]
    reproducido = [con["canales"][Side.L][i]["comando"] for i in idx]
    assert por_minuto(grabado, t) > 300 and por_minuto(reproducido, t) < 80
    reposo = [con["canales"][Side.L][i]["comando"] for i, f in enumerate(filas) if f["etiqueta"] == "0"]
    assert reposo[-1] == pytest.approx((-55.0, 0.0))


# --------------------------------------------------------------------------
# 2. Los vistazos
# --------------------------------------------------------------------------


class _Seq:
    """Backend que da una predicción cada vez, de una lista."""

    nombre = "seq"
    relativo = False

    def __init__(self, etiquetas):
        self._it = iter(etiquetas)

    def distribucion(self, rostro):
        e = next(self._it)
        probs = {k: 0.0 for k in ("feliz", "triste", "sorprendido", "neutral", "enojado")}
        probs[e] = 0.9
        return Distribucion(e, 0.9, probs, e)


def _clasificador(etiquetas, **kw):
    kw.setdefault("mirada_fuera_s", 3.0)
    return EmotionClassifier(_Seq(etiquetas), detector=lambda f: (10, 20, 60, 60),
                             frame_espejado=False, emotion_hz=1000.0, **kw)


FRAME = np.zeros((120, 160, 3), dtype=np.uint8)


def test_un_vistazo_abajo_corto_conserva_la_expresion():
    """Triste mirando la pantalla, y luego 2 s mirando abajo, donde el modelo ya
    dice neutral: el robot sigue triste."""
    c = _clasificador(["triste"] * 10 + ["neutral"] * 10)
    t = 0.0
    for _ in range(10):
        c.process(FRAME, t, cabeceo=0.0, giro=0.0)
        t += 0.2
    assert c.emocion.etiqueta == "triste"
    for _ in range(10):                                        # 2 s abajo
        c.process(FRAME, t, cabeceo=-20.0, giro=0.0)
        t += 0.2
    assert c.mirada_fuera and c.emocion.etiqueta == "triste"


def test_si_la_mirada_se_queda_fuera_vuelve_a_decidir_con_lo_que_ve():
    c = _clasificador(["triste"] * 10 + ["neutral"] * 30)
    t = 0.0
    for _ in range(10):
        c.process(FRAME, t, cabeceo=0.0)
        t += 0.2
    for _ in range(30):                                        # 6 s abajo
        c.process(FRAME, t, cabeceo=-20.0)
        t += 0.2
    assert not c.mirada_fuera and c.emocion.etiqueta == "neutral"


def test_tambien_a_los_lados_y_no_hacia_arriba():
    c = _clasificador(["triste"] * 3)
    c.process(FRAME, 0.0, cabeceo=0.0, giro=30.0)
    assert c.mirada_fuera
    c = _clasificador(["triste"] * 3, mirada_arriba=90.0)
    c.process(FRAME, 0.0, cabeceo=20.0, giro=0.0)
    assert not c.mirada_fuera


def test_mirar_fuera_no_cuenta_como_cara_perdida():
    """Es una cara utilizable: el retorno a neutral de los 2 s no corre."""
    c = _clasificador(["feliz"] * 5 + ["neutral"] * 20, retorno_neutral_s=2.0, mirada_fuera_s=10.0)
    t = 0.0
    for _ in range(5):
        c.process(FRAME, t, cabeceo=0.0)
        t += 0.2
    for _ in range(20):                                        # 4 s abajo
        c.process(FRAME, t, cabeceo=-30.0)
        t += 0.2
    assert c.emocion.etiqueta == "feliz" and not c.retorno_en_ultima


def test_apagado_por_defecto_en_el_clasificador_y_encendido_en_la_configuracion():
    c = EmotionClassifier(_Seq(["triste"]), detector=lambda f: (10, 20, 60, 60), frame_espejado=False)
    c.process(FRAME, 0.0, cabeceo=-30.0)
    assert not c.mirada_fuera
    e = AppConfig.from_env({}).emotion
    assert (e.mirada_abajo, e.mirada_arriba, e.mirada_lado, e.mirada_fuera_s) == (-8.0, 90.0, 20.0, 3.0)


def test_el_csv_lleva_el_giro_y_la_marca_y_la_reproduccion_no_la_hace_votar():
    import analizar_registro_emocion as are
    from telmeow_teleop.emotion_classifier import _fila_csv

    c = _clasificador(["triste"])
    c.process(FRAME, 0.0, cabeceo=-20.0, giro=5.5)
    fila = dict(zip(COLUMNAS_CSV, _fila_csv(0.0, 0, c, 100.0)))
    assert (fila["giro"], fila["mirada_fuera"]) == ("5.50", 1)
    assert are.voto({"rostro": 1, "prediccion": "triste", "confianza_pred": 0.9, "mirada_fuera": 1,
                     "min_confidence": 0.5}) is None


@pytest.mark.skipif(not (RAIZ / "p78_emocion.csv").exists(), reason="falta la sesión p78")
def test_en_p78_los_vistazos_no_empeoran_nada_y_ayudan_mirando_abajo():
    import analizar_registro_emocion as are
    import confirmar_p78 as cp
    import simular_vistazos as sv

    e = AppConfig.from_env({}).emotion
    filas = are.cargar(RAIZ / "p78_emocion.csv")
    for f in filas:
        f["enojo_por_grado"] = e.enojo_por_grado
    con = sv.marcar_vistazos(filas, sv.giros_de("p78", filas), e.mirada_abajo, e.mirada_arriba,
                             e.mirada_lado, e.mirada_fuera_s)

    def tabla(f_):
        emos, _ = are.reproducir(f_, 5, 0.5)
        return cp.resumir([dict(x, emocion=x["manual"] or y) for x, y in zip(f_, emos)])

    antes, despues = tabla(filas), tabla(con)
    assert all(despues[k] >= antes[k] for k in antes)
    assert despues["triste abajo"] > antes["triste abajo"]
    assert despues["enojado abajo"] > antes["enojado abajo"]
