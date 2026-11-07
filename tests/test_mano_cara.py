"""Pruebas de la mano a la cara (P7.5).

Con una mano en la cara el brazo del robot saltaba 2.6-5.1° por frame, y el
operador eligió que el robot reconozca el gesto y lo haga a su manera, como el
saludo: cada brazo por su lado. Cuatro grupos, cada uno atado a algo externo al
detector:

1. La pose, con la geometría del visor (mallas, ``RIG``, tablas): es la más
   cercana a la pantalla que se admite, y la cadena del visor es la de
   ``retarget.direccion`` por dos caminos independientes.
2. El detector, con señales sintéticas.
3. Las grabaciones reales: «el grito» y la cara tapada se reconocen; «yo no fui»,
   el saludo y estirar los brazos, no.
4. El tracker entero, con una escena que se lleva la mano a la cara.
"""

from __future__ import annotations

import csv
import re
import statistics
import sys
from pathlib import Path

import numpy as np
import pytest

from telmeow_teleop.arm_tracker import COLUMNAS_CSV, ArmTracker, fila_csv
from telmeow_teleop.config import AppConfig
from telmeow_teleop.limits import FLEXION_RANGE_EXTENDIDO, Side, clamp_arm
from telmeow_teleop.mano_cara import (
    POSE,
    RAMPA,
    T_ENTRADA,
    T_SALIDA,
    ZONA_ENTRADA,
    ZONA_SALIDA,
    DetectorManoCara,
    senales_mano_cara,
)
from test_confianza_mano import (
    CAMARA,
    CODO,
    HOMBRO_D,
    HOMBRO_I,
    MANO_COLGANDO,
    PASO,
    escena,
    paso,
    tracker_con_escenas,
)

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))

import analizar_mano_cara as amc  # noqa: E402
import pose_mano_cara as pmc  # noqa: E402

VERIFICACION = RAIZ / "verificacion_2026-10-11.csv"
FINAL = RAIZ / "p7_final_brazos.csv"
SESION_SALUDO = RAIZ / "saludo_2026-10-19.csv"


def _leer(ruta):
    with open(ruta, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# --------------------------------------------------------------------------
# 1. La pose
# --------------------------------------------------------------------------


@pytest.mark.parametrize("lado", ["L", "R"])
def test_la_cadena_del_visor_es_la_de_retarget(lado):
    """Dos caminos independientes: los ejes y pivotes del visor y la fórmula de
    ``retarget.direccion``. Si no coinciden, ``POSE`` no significaría en el
    robot lo que aquí."""
    assert pmc.peor_diferencia_cinematica(lado) < 0.5


def test_el_rig_copiado_es_el_del_visor():
    """``pose_mano_cara`` lleva una copia de la geometría: si cambia el visor,
    esta prueba avisa de que hay que recalcular la pose."""
    html = (RAIZ / "telmeow_viewer" / "telmeow_viewer.html").read_text(encoding="utf-8")

    def numeros(patron):
        m = re.search(patron, html)
        assert m, patron
        return [float(x) for x in m.group(1).split(",")]

    for nombre, (pivote, eje) in pmc.RIG.items():
        assert numeros(rf"{nombre}:\s*\{{\s*piv:V\(([^)]+)\)") == pytest.approx(list(pivote)), nombre
        assert numeros(rf"{nombre}:\s*\{{[^}}]*axis:V\(([^)]+)\)") == pytest.approx(list(eje)), nombre
    for nombre, matriz in pmc.W.items():
        assert numeros(rf"\b{nombre}:\s*\[([^\]]+)\]") == pytest.approx(matriz), nombre
    m = re.search(r"cx:([-\d.]+),\s*cy:([-\d.]+),\s*cz:([-\d.]+)", html)
    assert [float(x) for x in m.groups()] == pytest.approx(list(pmc.PANTALLA))


@pytest.mark.parametrize("lado", ["L", "R"])
def test_la_pose_cabe_en_las_tablas_con_su_margen(lado):
    recorte = clamp_arm(POSE[0], POSE[1], lado, flexion_range=FLEXION_RANGE_EXTENDIDO)
    assert recorte.clamped is False
    # Con el margen de la búsqueda también: 3° antes del tope no recorta.
    assert clamp_arm(POSE[0] - pmc.MARGEN_TABLA, POSE[1], lado,
                     flexion_range=FLEXION_RANGE_EXTENDIDO).clamped is False


@pytest.mark.parametrize("lado", ["L", "R"])
def test_la_pose_es_la_mas_cercana_a_la_pantalla(lado):
    mejor = pmc.por_distancia(lado)[0]
    assert (mejor["abd"], mejor["flex"]) == pytest.approx(POSE)
    assert mejor["dist"] < 205.0            # ~20 cm del centro de la pantalla


@pytest.mark.parametrize("lado", ["L", "R"])
def test_la_pose_deja_la_mano_al_frente_y_a_la_altura_de_la_barbilla(lado):
    """Atado a lo que se ve en el visor: el brazo apunta al frente y hacia
    arriba, y la bolita queda a la altura del bajo de la cabeza (z ≈ 227 mm)."""
    punta = pmc.por_distancia(lado)[0]["tip"]
    assert punta[1] > 80.0                  # +Y es el frente: la pantalla mira ahí
    assert 215.0 < punta[2] < 245.0
    assert (punta[0] > 0) == (lado == "R")  # cada brazo de su lado, sin cruzar


@pytest.mark.parametrize("lado", ["L", "R"])
def test_la_pose_no_toca_la_cabeza_en_todo_su_recorrido(lado):
    """El visor no comprueba el brazo contra la cabeza, y las tablas tampoco:
    solo miran el tronco. Con la cabeza en sus topes sigue habiendo holgura."""
    h = pmc.holguras(lado, *POSE, pmc.brazo_home(lado), pmc.arboles_cabeza(pmc.cabeza_home()))
    assert min(h) >= pmc.HOLGURA_MIN


@pytest.mark.parametrize("lado", ["L", "R"])
def test_ninguna_pose_admisible_toca_la_cabeza(lado):
    """H62: ninguna capa comprueba este par, y no hace falta añadirla mientras las
    tablas sean estas. Con paso grueso para no alargar la suite: el de 5° da 32 mm."""
    holgura, *_ = pmc.holgura_en_la_envolvente(
        lado, pmc.brazo_home(lado), pmc.arboles_cabeza(pmc.cabeza_home()), paso=30.0)
    assert holgura >= pmc.HOLGURA_MIN


# --------------------------------------------------------------------------
# 2. El detector
# --------------------------------------------------------------------------


def recorrer(detector, muestras, desde=0.0, de_fiar=True, paso_s=0.05):
    return [detector.actualizar(desde + i * paso_s, m, de_fiar) for i, m in enumerate(muestras)]


EN_LA_CARA = (0.15, 0.2)
ABAJO = (0.7, -1.1)


def test_hay_que_sostener_la_mano_para_entrar():
    d = DetectorManoCara()
    n = int(T_ENTRADA / 0.05)
    estados = recorrer(d, [EN_LA_CARA] * (n + 2))
    assert not any(estados[:n])
    assert estados[-1] is True


def test_pasar_la_mano_por_delante_no_cuenta():
    d = DetectorManoCara()
    muestras = ([EN_LA_CARA] * 4 + [ABAJO]) * 6        # 0.2 s cada vez
    assert not any(recorrer(d, muestras))


def test_una_mano_que_no_es_de_fiar_no_entra():
    """La que BlazePose inventa (H40): puede caer en la zona con visibilidad alta."""
    d = DetectorManoCara()
    assert not any(recorrer(d, [EN_LA_CARA] * 40, de_fiar=False))


def test_dentro_del_gesto_perder_la_mano_no_lo_corta_enseguida():
    """Con la mano delante de la cara BlazePose la pierde: ni ``None`` ni dejar
    de ser de fiar sacan al robot del gesto hasta ``T_SALIDA``."""
    d = DetectorManoCara()
    recorrer(d, [EN_LA_CARA] * 20)
    assert d.activo
    n = int(T_SALIDA / 0.05)
    assert all(recorrer(d, [None] * (n - 1), desde=1.0))
    assert all(recorrer(d, [EN_LA_CARA] * 3, desde=1.0 + (n - 1) * 0.05, de_fiar=False))
    assert d.activo


def test_salir_pide_estar_fuera_un_rato_y_cualquier_regreso_reinicia_la_cuenta():
    d = DetectorManoCara()
    recorrer(d, [EN_LA_CARA] * 20)
    n = int(T_SALIDA / 0.05)
    muestras = ([ABAJO] * (n - 2) + [EN_LA_CARA]) * 3
    assert all(recorrer(d, muestras, desde=1.0))
    estados = recorrer(d, [ABAJO] * (n + 2), desde=3.0)
    assert estados[0] is True and estados[-1] is False


def test_la_zona_de_salida_es_mas_ancha_que_la_de_entrada():
    """Sin histéresis, la mano en el borde haría parpadear el gesto."""
    d = DetectorManoCara()
    borde = (ZONA_ENTRADA[1] + 0.05, 0.2)              # fuera de una, dentro de la otra
    assert not any(recorrer(d, [borde] * 40))
    recorrer(d, [EN_LA_CARA] * 20, desde=2.0)
    assert all(recorrer(d, [borde] * 40, desde=3.0))
    assert ZONA_SALIDA[1] > ZONA_ENTRADA[1]


def test_zonas_que_no_caben_se_rechazan():
    with pytest.raises(ValueError):
        DetectorManoCara(zona_salida=(-0.1, 0.1, 0.0, 0.1))
    with pytest.raises(ValueError):
        DetectorManoCara(t_salida=-1.0)


def test_las_senales_en_anchos_de_hombro_y_hacia_el_lado_de_la_mano():
    """100 px de ancho: la mano 15 px por dentro de la mitad hacia su hombro y
    20 px por encima. Da igual de qué lado de la imagen esté su hombro."""
    assert senales_mano_cara((165, 280), (200, 300), (100, 300)) == pytest.approx((0.15, 0.2))
    assert senales_mano_cara((135, 280), (100, 300), (200, 300)) == pytest.approx((0.15, 0.2))
    # Cruzar al otro lado da x negativa.
    assert senales_mano_cara((135, 280), (200, 300), (100, 300)) == pytest.approx((-0.15, 0.2))
    assert senales_mano_cara((1, 2), (5, 5), (5, 5)) is None
    assert senales_mano_cara((float("nan"), 2), (5, 5), (9, 5)) is None


# --------------------------------------------------------------------------
# 3. Las grabaciones
# --------------------------------------------------------------------------


def _resumen(filas):
    return {lado: amc.por_etiqueta(filas, amc.reproducir(filas, lado)) for lado in "LR"}


@pytest.mark.skipif(not VERIFICACION.exists(), reason="falta la grabación del 2026-10-11")
def test_la_verificacion_reconoce_el_grito_y_nada_de_lo_demas():
    """Etiqueta 2, «el grito»: 70 % de la etiqueta, el tiempo con las manos
    arriba. Brazos colgando (0), «yo no fui» (1), saludos (3 y 4) y estirar los
    brazos (5): ni una vez."""
    r = _resumen(_leer(VERIFICACION))
    for lado in "LR":
        assert r[lado][2][0] > 65.0 and r[lado][2][1] == 1
        for etiqueta in (0, 1, 3, 4, 5):
            assert r[lado][etiqueta] == (0.0, 0)


@pytest.mark.skipif(not FINAL.exists(), reason="falta la sesión p7_final")
def test_taparse_la_cara_no_se_corta_aunque_blazepose_pierda_la_mano():
    """En p7_final el operador se tapa la cara de 307.4 a 331 s, y BlazePose
    pierde la derecha varias veces, con ella por debajo de los hombros. El gesto
    tiene que entrar una vez y salir una vez, en los dos brazos. Y con las manos
    abajo, de la etiqueta 0 a la 5 y de la 7 a la 10, no sale nunca."""
    filas = _leer(FINAL)
    for lado in "LR":
        activo = amc.reproducir(filas, lado)
        tramo = [a for f, a in zip(filas, activo) if 308.5 <= float(f["t"]) <= 331.0]
        assert all(tramo)
        assert not any(a for f, a in zip(filas, activo) if 300.0 <= float(f["t"]) <= 307.0)
        assert not any(a for f, a in zip(filas, activo) if 332.5 <= float(f["t"]) <= 335.0)
        for etiqueta, (porcentaje, _) in amc.por_etiqueta(filas, activo).items():
            if etiqueta <= 5 or 7 <= etiqueta <= 10:
                assert porcentaje == 0.0, etiqueta


@pytest.mark.skipif(not FINAL.exists(), reason="falta la sesión p7_final")
def test_una_sola_mano_lleva_solo_ese_brazo():
    """A los 335.6-337.9 s el operador solo sube la izquierda."""
    filas = _leer(FINAL)
    izquierda = amc.reproducir(filas, "L")
    derecha = amc.reproducir(filas, "R")
    tramo = [(i, d) for f, i, d in zip(filas, izquierda, derecha) if 336.0 <= float(f["t"]) <= 337.4]
    assert tramo and all(i and not d for i, d in tramo)


@pytest.mark.skipif(not SESION_SALUDO.exists(), reason="falta la sesión del 2026-10-19")
def test_saludar_no_es_llevarse_la_mano_a_la_cara():
    """Etiquetas 1 y 2: saluda un brazo. El otro colgando, tampoco."""
    r = _resumen(_leer(SESION_SALUDO))
    for lado in "LR":
        for etiqueta in (1, 2):
            assert r[lado][etiqueta] == (0.0, 0)


@pytest.mark.skipif(not FINAL.exists(), reason="falta la sesión p7_final")
def test_de_principio_a_fin_el_brazo_deja_de_saltar():
    """El ``ArmTracker`` de verdad, reproducido sobre p7_final. Sin el gesto el
    brazo derecho salta > 5°/frame de mediana con la cara tapada; con él no se
    mueve ni una décima, y está en la pose."""
    import reproducir_brazos as rb

    filas = rb.leer(FINAL)
    sin = rb.reproducir(filas)
    con = rb.reproducir(filas, mano_cara=True)

    def saltos(salida):
        comandos = [c["comando"] for t, c in zip(salida["t"], salida["canales"][Side.R])
                    if 310.0 <= t <= 331.0]
        return [max(abs(a[0] - b[0]), abs(a[1] - b[1])) for a, b in zip(comandos, comandos[1:])]

    assert statistics.median(saltos(sin)) > 5.0
    assert max(saltos(con)) < 0.1
    t = con["t"].index(next(x for x in con["t"] if x >= 320.0))
    assert con["canales"][Side.R][t]["comando"] == pytest.approx(POSE, abs=0.1)


# --------------------------------------------------------------------------
# 4. El tracker entero
# --------------------------------------------------------------------------

_MEDIO = (HOMBRO_I + HOMBRO_D) / 2
_ANCHO = float(np.linalg.norm(HOMBRO_I - HOMBRO_D))
# La mano izquierda en la mejilla: 0.15 anchos hacia su hombro, 0.2 por encima
# de la línea de los hombros y unos centímetros hacia la cámara.
MANO_EN_LA_CARA = _MEDIO + np.array([-0.15 * _ANCHO, -0.2 * _ANCHO, -0.08])
CODO_RECOGIDO = HOMBRO_I + np.array([-0.05, 0.25, -0.05])
ARRANQUE = 0.5 + T_ENTRADA + RAMPA + 0.5      # rampa de la mano, entrada, rampa, limitador


def _en_la_cara(**kwargs):
    return escena(CODO_RECOGIDO, MANO_EN_LA_CARA, **kwargs)


def test_la_escena_cae_en_la_zona_de_entrada():
    x, y = senales_mano_cara(
        CAMARA.proyectar(MANO_EN_LA_CARA), CAMARA.proyectar(HOMBRO_I), CAMARA.proyectar(HOMBRO_D))
    assert ZONA_ENTRADA[0] < x < ZONA_ENTRADA[1] and ZONA_ENTRADA[2] < y < ZONA_ENTRADA[3]


def test_una_mano_en_la_cara_lleva_ese_brazo_a_la_pose_y_el_otro_no_se_mueve():
    tracker, reloj, estado = tracker_con_escenas(mano_cara=True)
    reposo_r = tracker.reposo[Side.R]
    mando = None
    for i in range(int(ARRANQUE / PASO)):
        mando = paso(tracker, reloj, estado, i * PASO, _en_la_cara())
        assert tracker.pesos_mano_cara[Side.R] == 0.0
        assert (mando.armR, mando.armRtw) == pytest.approx(reposo_r)
    assert tracker.pesos_mano_cara[Side.L] == 1.0
    assert (mando.armL, mando.armLtw) == pytest.approx(POSE, abs=0.1)


def test_el_brazo_no_salta_al_entrar_ni_al_salir():
    """Entra y sale con la rampa: el peso nunca da un salto mayor que un paso
    de la rampa, y al bajar la mano el robot vuelve a su reposo."""
    tracker, reloj, estado = tracker_con_escenas(mano_cara=True)
    pesos = []
    for i in range(int(ARRANQUE / PASO)):
        paso(tracker, reloj, estado, i * PASO, _en_la_cara())
        pesos.append(tracker.pesos_mano_cara[Side.L])
    mando = None
    for i in range(int(4.0 / PASO)):
        mando = paso(tracker, reloj, estado, ARRANQUE + i * PASO, escena(CODO, MANO_COLGANDO))
        pesos.append(tracker.pesos_mano_cara[Side.L])
    maximo = PASO / RAMPA + 1e-9
    assert all(abs(b - a) <= maximo for a, b in zip(pesos, pesos[1:]))
    assert pesos[-1] == 0.0
    assert (mando.armL, mando.armLtw) == pytest.approx(tracker.reposo[Side.L], abs=1.0)


def test_el_saludo_manda_sobre_el_gesto():
    """Van encadenados con el saludo al final: si los dos están activos, el robot
    saluda. El saludo es un gesto deliberado."""
    tracker, reloj, estado = tracker_con_escenas(mano_cara=True)
    tracker._pesos_saludo[Side.L] = 1.0
    tracker._detectores_saludo[Side.L]._activo = True
    tracker._inicio_saludo[Side.L] = 0.0
    tracker._t_saludo[Side.L] = 0.0
    tracker._detectores_cara[Side.L]._activo = True
    tracker._pesos_cara[Side.L] = 1.0
    con_gesto = tracker.aplicar_mano_cara(Side.L, (-30.0, -90.0), 1.0)
    assert con_gesto == pytest.approx(POSE)
    final = tracker.aplicar_saludo(Side.L, con_gesto, 1.0)
    assert final != pytest.approx(POSE)


def test_si_blazepose_no_mide_el_brazo_el_robot_no_lo_baja_a_medio_gesto():
    """Sin mano ni codo de fiar el brazo no se estima, y la regla general lo
    mandaría a reposo pasada la gracia: justo lo que hay que evitar aquí. Y
    ``estimado`` sigue diciendo la verdad: no se midió."""
    tracker, reloj, estado = tracker_con_escenas(mano_cara=True)
    t = 0.0
    for _ in range(int(ARRANQUE / PASO)):
        paso(tracker, reloj, estado, t, _en_la_cara())
        t += PASO
    assert tracker.pesos_mano_cara[Side.L] == 1.0
    mando = None
    for _ in range(int(3.0 / PASO)):
        mando = paso(tracker, reloj, estado, t, _en_la_cara(vis_mano=0.0, vis_codo=0.0))
        t += PASO
    assert tracker.estimado[Side.L] is False
    assert (mando.armL, mando.armLtw) == pytest.approx(POSE, abs=0.1)


def test_perder_la_pose_un_momento_no_corta_y_perderla_del_todo_no_deja_el_peso_colgado():
    tracker, reloj, estado = tracker_con_escenas(mano_cara=True)
    t = 0.0
    for _ in range(int(ARRANQUE / PASO)):
        paso(tracker, reloj, estado, t, _en_la_cara())
        t += PASO
    for _ in range(int(0.5 * T_SALIDA / PASO)):
        mando = paso(tracker, reloj, estado, t, None)
        t += PASO
    assert tracker.pesos_mano_cara[Side.L] == 1.0
    assert (mando.armL, mando.armLtw) == pytest.approx(POSE, abs=0.1)
    for _ in range(int(2.0 * T_SALIDA / PASO)):
        paso(tracker, reloj, estado, t, None)
        t += PASO
    assert tracker.pesos_mano_cara[Side.L] == 0.0


def test_apagado_no_hace_nada():
    tracker, reloj, estado = tracker_con_escenas(mano_cara=False)
    for i in range(int(ARRANQUE / PASO)):
        paso(tracker, reloj, estado, i * PASO, _en_la_cara())
        assert tracker.pesos_mano_cara[Side.L] == 0.0


def test_por_defecto_apagado_y_la_configuracion_lo_enciende():
    """Apagado en el constructor para que reproducir una grabación antigua dé lo
    que se publicó; encendido en la configuración, que es lo que usa el pipeline."""
    tracker, *_ = tracker_con_escenas()
    assert tracker.mano_cara is False
    cfg = AppConfig.from_env({})
    assert cfg.arm.mano_cara is True
    assert AppConfig.from_env({"TELMEOW_ARM_MANO_CARA": "0"}).arm.mano_cara is False
    desde = ArmTracker.desde_config(cfg)
    try:
        assert desde.mano_cara is True
    finally:
        desde.close()


def test_el_peso_va_al_csv():
    tracker, reloj, estado = tracker_con_escenas(mano_cara=True)
    mando = None
    for i in range(int(ARRANQUE / PASO)):
        mando = paso(tracker, reloj, estado, i * PASO, _en_la_cara())
    fila = dict(zip(COLUMNAS_CSV, fila_csv(tracker, mando, 1.0, 0, 640, 480)))
    assert float(fila["mano_cara_L"]) == 1.0
    assert float(fila["mano_cara_R"]) == 0.0
    assert len(fila_csv(tracker, mando, 1.0, 0, 640, 480)) == len(COLUMNAS_CSV)
