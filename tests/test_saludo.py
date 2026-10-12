"""Pruebas del saludo del robot (P5.7, H42).

El operador saluda como se saluda —el codo abajo, la mano por encima del hombro
y de lado a lado— y el robot, que no tiene antebrazo, no lo puede copiar: lo
reconoce y hace el saludo del visor con ese brazo. Lo eligió el operador.

Cuatro grupos, cada uno atado a algo externo al detector:

1. El patrón ES el saludo del visor: la fórmula de ``telmeow_viewer.html`` está
   transcrita aquí por separado y se compara la dirección del brazo.
2. El detector, con señales sintéticas: lo que es un saludo y lo que no.
3. La grabación de verificación del 2026-10-11: los saludos del protocolo y el
   de la narración se reconocen, y nada más.
4. El tracker entero, con una escena que saluda.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

import numpy as np
import pytest

from telmeow_teleop.config import AppConfig
from telmeow_teleop.limits import FLEXION_RANGE_EXTENDIDO, Side, clamp_arm
from telmeow_teleop.retarget import direccion
from telmeow_teleop.saludo import (
    AMPLITUD,
    FIN,
    DetectorSaludo,
    patron_saludo,
    senales_saludo,
)
from test_confianza_mano import (
    CAMARA,
    HOMBRO_D,
    HOMBRO_I,
    PASO,
    escena,
    paso,
    tracker_con_escenas,
)

RAIZ = Path(__file__).resolve().parent.parent
VERIFICACION = RAIZ / "verificacion_2026-10-11.csv"
INTENTO = RAIZ / "verificacion_2026-10-11_intento.csv"


# --------------------------------------------------------------------------
# 1. El patrón
# --------------------------------------------------------------------------


def saludo_del_visor(t):
    """``telmeow_viewer.html``, botón b-wave, transcrito: armR y armRtw."""
    s = t * 3.64
    return 30 + math.sin(s * 1.2) * 12, 50 + math.sin(s * 4) * 8


@pytest.mark.parametrize("t", np.linspace(0.0, 6.0, 61))
def test_el_patron_apunta_el_brazo_como_el_saludo_del_visor(t):
    """El Python lo hace en su rama de la cinemática inversa: misma dirección,
    girado 180° sobre el eje del brazo, que es simétrico y no se ve."""
    assert direccion(*patron_saludo(t)) == pytest.approx(direccion(*saludo_del_visor(t)), abs=1e-12)


@pytest.mark.parametrize("lado", [Side.L, Side.R])
def test_el_patron_cabe_en_el_robot(lado):
    for t in np.arange(0.0, 10.0, 0.01):
        abd, flex = patron_saludo(t)
        assert abd <= 0.0 and -180.0 <= flex <= 0.0          # la rama del Python
        recorte = clamp_arm(abd, flex, lado, flexion_range=FLEXION_RANGE_EXTENDIDO)
        assert not recorte.clamped


def test_el_patron_va_mas_despacio_que_el_limitador():
    """Por debajo de los 180°/s del limitador de velocidad, que así no lo deforma."""
    ts = np.arange(0.0, 10.0, 0.001)
    angulos = np.array([patron_saludo(t) for t in ts])
    velocidad = np.hypot(*np.diff(angulos, axis=0).T) / 0.001
    assert velocidad.max() < 180.0


# --------------------------------------------------------------------------
# 2. El detector
# --------------------------------------------------------------------------


def recorrer(detector, muestras, desde=0.0):
    """Pasa ``(x, y, p)`` (o None) a 20 Hz y devuelve, por instante, si saluda."""
    return [(desde + i * PASO, detector.actualizar(desde + i * PASO, m)) for i, m in enumerate(muestras)]


def vaiven(segundos, frecuencia=1.5, amplitud=0.3, y=0.4, p=0.05, centro=-0.3):
    n = int(round(segundos / PASO))
    return [
        (centro + amplitud * math.sin(2 * math.pi * frecuencia * i * PASO), y, p) for i in range(n)
    ]


def primer_activo(estados):
    return next((t for t, activo in estados if activo), None)


def test_un_saludo_se_reconoce_en_segundo_y_medio():
    """Cinco cambios de sentido: a 1.5 Hz, unos 1.6 s."""
    estados = recorrer(DetectorSaludo(), vaiven(3.0))
    assert 1.3 <= primer_activo(estados) <= 1.8
    assert all(activo for t, activo in estados if t >= 1.8)


def test_el_saludo_termina_al_parar():
    det = DetectorSaludo()
    estados = recorrer(det, vaiven(3.0))
    assert estados[-1][1]
    quieto = [(-0.3, 0.4, 0.05)] * 40
    despues = recorrer(det, quieto, desde=3.0)
    fin = next(t for t, activo in despues if not activo)
    assert fin - 3.0 <= FIN + 0.3


def test_un_saludo_lento_tambien_cuenta():
    """El del protocolo del 2026-10-11 empezó a ~0.9 Hz (medio periodo 0.55 s)."""
    assert primer_activo(recorrer(DetectorSaludo(), vaiven(5.0, frecuencia=0.9))) is not None


@pytest.mark.parametrize(
    "nombre, muestras",
    [
        # «yo no fui»: las manos arriba, quietas, con temblor de BlazePose.
        ("mano quieta", [(-0.3 + 0.02 * math.sin(i), 0.4, 0.05) for i in range(100)]),
        # el vaivén con la mano por debajo del hombro: no es un saludo.
        ("por debajo del hombro", vaiven(5.0, y=-0.1)),
        # estirar los brazos hacia la cámara y recogerlos: en la imagen la mano
        # también va de lado a lado, pero está 25 cm por delante.
        ("hacia la camara", vaiven(5.0, p=0.25)),
        # un temblor por debajo de la amplitud mínima.
        ("temblor", vaiven(5.0, amplitud=AMPLITUD / 3)),
        # demasiado lento para ser un saludo: medio periodo de 1 s.
        ("lento", vaiven(8.0, frecuencia=0.5)),
    ],
)
def test_lo_que_no_es_un_saludo(nombre, muestras):
    assert primer_activo(recorrer(DetectorSaludo(), muestras)) is None, nombre


def test_dos_barridos_sueltos_no_son_un_saludo():
    """El gesto de cierre de la narración del 2026-10-11: barridos grandes,
    separados por más de lo que dura un vaivén."""
    muestras = []
    for _ in range(4):
        muestras += [(0.3, 0.3, 0.05)] * 20             # quieto 1 s
        muestras += [(0.3 - 0.9 * k / 4, 0.3, 0.05) for k in range(5)]
        muestras += [(-0.6 + 0.9 * k / 4, 0.3, 0.05) for k in range(5)]
    assert primer_activo(recorrer(DetectorSaludo(), muestras)) is None


def test_si_la_mano_deja_de_ser_de_fiar_el_saludo_se_corta():
    det = DetectorSaludo()
    recorrer(det, vaiven(3.0))
    assert det.activo
    det.actualizar(3.05, None)
    assert not det.activo


def test_las_senales_en_anchos_de_hombro():
    x, y, p = senales_saludo((150.0, 300.0), (190.0, 360.0), (390.0, 360.0), 0.04)
    assert (x, y, p) == pytest.approx((-0.2, 0.3, 0.04))       # 40 px de 200, 60 px arriba
    assert senales_saludo((1.0, 1.0), (5.0, 5.0), (5.0, 5.0), 0.0) is None
    assert senales_saludo((float("nan"), 1.0), (5.0, 5.0), (9.0, 5.0), 0.0) is None


# --------------------------------------------------------------------------
# 3. La grabación de verificación
# --------------------------------------------------------------------------


def _f(texto):
    try:
        return float(texto)
    except (TypeError, ValueError):
        return float("nan")


def reconocer(ruta):
    """Los tramos de saludo de una grabación, con las señales calculadas como
    las calcula el tracker: píxeles de la mano y de los dos hombros, y la
    profundidad YA corregida (``p_ant``), con la mano de fiar."""
    with open(ruta, newline="", encoding="utf-8") as archivo:
        filas = list(csv.DictReader(archivo))
    tramos = {}
    for lado, otro in (("L", "R"), ("R", "L")):
        detector = DetectorSaludo()
        antes = False
        for fila in filas:
            muestra = None
            confianza = _f(fila[f"confianza_mano_{lado}"])
            visibilidad = _f(fila[f"vis_destino_{lado}"]) * confianza
            if fila[f"estimado_{lado}"] == "1" and confianza >= 0.999 and visibilidad >= 0.5:
                muestra = senales_saludo(
                    (_f(fila[f"img_mano_x_{lado}"]), _f(fila[f"img_mano_y_{lado}"])),
                    (_f(fila[f"img_hombro_x_{lado}"]), _f(fila[f"img_hombro_y_{lado}"])),
                    (_f(fila[f"img_hombro_x_{otro}"]), _f(fila[f"img_hombro_y_{otro}"])),
                    _f(fila[f"p_ant_{lado}"]),
                )
            t = _f(fila["t"])
            activo = detector.actualizar(t, muestra)
            if activo and not antes:
                tramos.setdefault((int(fila["etiqueta"]), lado), []).append([t, t])
            if activo:
                tramos[(int(fila["etiqueta"]), lado)][-1][1] = t
            antes = activo
    return tramos


@pytest.mark.skipif(not VERIFICACION.exists(), reason="falta la grabación del 2026-10-11")
def test_la_verificacion_reconoce_los_saludos_y_nada_mas():
    """Etiqueta 3: saluda el derecho. Etiqueta 4: el izquierdo. Etiqueta 6,
    narración libre: el operador abrió el cuento saludando con la derecha (186-189
    s). En ninguna otra parte —brazos colgando, «yo no fui», «el grito», estirar
    los brazos hacia la cámara, el resto de la narración— hay saludo."""
    tramos = reconocer(VERIFICACION)
    assert set(tramos) == {(3, "R"), (4, "L"), (6, "R")}
    duracion = {clave: sum(b - a for a, b in ts) for clave, ts in tramos.items()}
    assert duracion[(3, "R")] > 8.0 and duracion[(4, "L")] > 15.0
    for a, b in tramos[(6, "R")]:
        assert 186.0 <= a and b <= 189.5


@pytest.mark.skipif(not INTENTO.exists(), reason="falta el intento del 2026-10-11")
def test_en_el_intento_no_hay_saludos():
    assert reconocer(INTENTO) == {}


# --------------------------------------------------------------------------
# 4. El tracker entero
# --------------------------------------------------------------------------

# Codo abajo y afuera; la mano 12 cm por encima del hombro, de lado a lado.
CODO_SALUDO = HOMBRO_I + np.array([-0.15, 0.18, 0.0])


def mano_saludando(t, hz=1.5):
    return CODO_SALUDO + np.array([0.08 * math.sin(2 * math.pi * hz * t), -0.30, 0.0])


def test_el_robot_saluda_cuando_el_operador_saluda():
    tracker, reloj, estado = tracker_con_escenas()
    reposo_r = tracker.reposo[Side.R]
    mando = None
    for i in range(int(4.5 / PASO)):
        t = i * PASO
        mando = paso(tracker, reloj, estado, t, escena(CODO_SALUDO, mano_saludando(t)))
        assert tracker.pesos_saludo[Side.R] == 0.0
        assert (mando.armR, mando.armRtw) == pytest.approx(reposo_r)
    assert tracker.pesos_saludo[Side.L] == pytest.approx(1.0)
    inicio = 4.5 - PASO - tracker._inicio_saludo[Side.L]
    esperado = np.array(patron_saludo(inicio))
    assert np.hypot(*(np.array([mando.armL, mando.armLtw]) - esperado)) < 10.0


def test_al_dejar_de_saludar_el_robot_vuelve():
    tracker, reloj, estado = tracker_con_escenas()
    for i in range(int(4.0 / PASO)):
        paso(tracker, reloj, estado, i * PASO, escena(CODO_SALUDO, mano_saludando(i * PASO)))
    assert tracker.pesos_saludo[Side.L] == pytest.approx(1.0)
    quieta = escena(CODO_SALUDO, mano_saludando(0.0))
    pesos = []
    for i in range(int(4.0 / PASO)):
        paso(tracker, reloj, estado, 4.0 + i * PASO, quieta)
        pesos.append(tracker.pesos_saludo[Side.L])
    assert pesos[-1] == 0.0
    assert all(b <= a for a, b in zip(pesos, pesos[1:]))      # sale con rampa, sin saltos


def test_con_el_saludo_apagado_el_robot_no_saluda():
    tracker, reloj, estado = tracker_con_escenas(saludo=False)
    for i in range(int(4.5 / PASO)):
        paso(tracker, reloj, estado, i * PASO, escena(CODO_SALUDO, mano_saludando(i * PASO)))
        assert tracker.pesos_saludo[Side.L] == 0.0


def test_la_mano_quieta_arriba_no_hace_saludar():
    """«Yo no fui»: la mano arriba y quieta lleva el brazo a la cruz, no al saludo."""
    tracker, reloj, estado = tracker_con_escenas()
    quieta = escena(CODO_SALUDO, mano_saludando(0.0))
    for i in range(int(4.0 / PASO)):
        paso(tracker, reloj, estado, i * PASO, quieta)
        assert tracker.pesos_saludo[Side.L] == 0.0


def test_la_configuracion_del_saludo():
    assert AppConfig.from_env({}).arm.saludo is True
    assert AppConfig.from_env({"TELMEOW_ARM_SALUDO": "0"}).arm.saludo is False


def test_la_escena_de_prueba_saluda_como_el_operador():
    """La escena del grupo 4 tiene que parecerse a lo medido el 2026-10-11: el
    codo por debajo del hombro, la mano 0.2-0.65 anchos de hombro por encima y
    ~0.5 de recorrido lateral."""
    assert CODO_SALUDO[1] > HOMBRO_I[1]                         # y hacia abajo
    hombro, contrario = CAMARA.proyectar(HOMBRO_I), CAMARA.proyectar(HOMBRO_D)
    muestras = [
        senales_saludo(CAMARA.proyectar(mano_saludando(t)), hombro, contrario, 0.0)
        for t in np.arange(0.0, 1.0, PASO)
    ]
    xs = [m[0] for m in muestras]
    ys = [m[1] for m in muestras]
    assert 0.2 <= min(ys) and max(ys) <= 0.65
    assert 0.35 <= max(xs) - min(xs) <= 0.7
