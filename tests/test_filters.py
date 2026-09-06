"""Pruebas del filtrado temporal.

Los umbrales de las pruebas de señal no están puestos a ojo: salen de medir el
comportamiento real sobre un escalón con ruido a 100 Hz, y se dejan con holgura
respecto a lo medido para que la prueba detecte una regresión sin volverse
frágil. Los números medidos están en el docstring de cada prueba.

Todo con biblioteca estándar: no hace falta numpy para validar estos filtros.
"""

import math
import random
import statistics
import time

import pytest

from telmeow_teleop.filters import (
    _DEFAULT_CLOCK,
    ClassHysteresis,
    EMAFilter,
    OneEuroFilter,
)

FS = 100.0
DT = 1.0 / FS
N = 200
SIGMA = 0.05


def senal_escalon(semilla=42):
    """Escalón 0 -> 1 a mitad de recorrido, con ruido gaussiano."""
    rnd = random.Random(semilla)
    return [
        (i * DT, (0.0 if i < N else 1.0) + rnd.gauss(0.0, SIGMA))
        for i in range(2 * N)
    ]


def reduccion_y_retardo(filtro, datos):
    """Devuelve (cuántas veces se reduce la desviación, muestras hasta el 90 %)."""
    salida = [filtro(v, t) for t, v in datos]
    entrada_estable = [v for _, v in datos[N - 100 : N]]
    salida_estable = salida[N - 100 : N]
    reduccion = statistics.stdev(entrada_estable) / statistics.stdev(salida_estable)
    retardo = next((i for i, v in enumerate(salida[N:]) if v >= 0.9), None)
    return reduccion, retardo


# --------------------------------------------------------------------------
# One Euro: lo que el plan pide comprobar
# --------------------------------------------------------------------------


def test_one_euro_suaviza_mucho_y_responde_rapido():
    """El requisito del plan: desviación notablemente menor en el tramo estable,
    sin retardo mayor a unos pocos frames en el flanco.

    Medido con (min_cutoff=0.5, beta=0.5) sobre este escalón: reduce la
    desviación 11.2 veces y alcanza el 90 % del escalón en 6 muestras (60 ms a
    100 Hz). Los umbrales van con holgura sobre esas cifras.
    """
    reduccion, retardo = reduccion_y_retardo(
        OneEuroFilter(min_cutoff=0.5, beta=0.5), senal_escalon()
    )
    assert reduccion > 5.0, f"solo redujo el ruido {reduccion:.1f} veces"
    assert retardo is not None and retardo <= 10, f"tardó {retardo} muestras"


def test_one_euro_domina_al_ema_en_los_dos_ejes_a_la_vez():
    """La afirmación central del paper, comprobada.

    Un pasabajos fijo tiene que elegir entre suavizar y responder rápido. El One
    Euro no: con (0.5, 0.5) reduce el ruido MÁS que un EMA de alpha=0.06 (11.2x
    frente a 9.7x) y además responde SEIS veces antes (6 muestras frente a 36).

    Es el resultado que sostiene la elección del filtro en la tesis.
    """
    datos = senal_escalon()
    red_euro, ret_euro = reduccion_y_retardo(OneEuroFilter(0.5, 0.5), datos)
    red_ema, ret_ema = reduccion_y_retardo(EMAFilter(0.06), datos)

    assert red_euro > red_ema, "el One Euro debería suavizar al menos tanto"
    assert ret_euro < ret_ema / 2, "y además responder mucho antes"


def test_con_beta_cero_degenera_en_pasabajos_fijo():
    """Sin el término de velocidad, el One Euro pierde su ventaja.

    Medido: con beta=0 el retardo pasa de 6 a 36 muestras, igualando al del EMA
    equivalente. Esto fija que la adaptación viene de beta y no de otra cosa.
    """
    datos = senal_escalon()
    _, ret_fijo = reduccion_y_retardo(OneEuroFilter(1.0, 0.0), datos)
    _, ret_adaptativo = reduccion_y_retardo(OneEuroFilter(1.0, 0.5), datos)
    assert ret_fijo > 3 * ret_adaptativo


def test_subir_beta_reduce_el_retardo():
    datos = senal_escalon()
    retardos = [
        reduccion_y_retardo(OneEuroFilter(1.0, beta), datos)[1]
        for beta in (0.0, 0.05, 0.5)
    ]
    assert retardos == sorted(retardos, reverse=True), retardos


def test_una_senal_constante_sale_constante():
    filtro = OneEuroFilter(1.0, 0.5)
    salida = [filtro(7.0, i * DT) for i in range(50)]
    assert all(v == pytest.approx(7.0) for v in salida)


# --------------------------------------------------------------------------
# One Euro: estado y casos límite
# --------------------------------------------------------------------------


def test_la_primera_muestra_pasa_tal_cual():
    filtro = OneEuroFilter()
    assert filtro.initialized is False
    assert filtro(3.5, 0.0) == 3.5
    assert filtro.initialized is True


def test_reset_olvida_el_estado():
    filtro = OneEuroFilter()
    filtro(0.0, 0.0)
    filtro(10.0, DT)
    filtro.reset()
    assert filtro.initialized is False
    assert filtro(99.0, 2.0) == 99.0


def test_dt_cero_no_revienta_y_se_contabiliza():
    """Con un reloj de baja resolución dos muestras caen en el mismo tick.

    En la formulación del One Euro eso es una división por cero. Se sustituye el
    dt y se lleva la cuenta, para que el problema sea visible en vez de
    silencioso — es el fallo del hallazgo H11 visto desde este módulo.
    """
    filtro = OneEuroFilter(1.0, 0.5)
    filtro(0.0, 0.0)
    filtro(1.0, DT)
    assert filtro.degenerate_dt_count == 0
    salida = filtro(2.0, DT)  # mismo timestamp que la anterior
    assert math.isfinite(salida)
    assert filtro.degenerate_dt_count == 1


def test_timestamp_hacia_atras_tampoco_revienta():
    filtro = OneEuroFilter()
    filtro(0.0, 10.0)
    assert math.isfinite(filtro(1.0, 9.0))
    assert filtro.degenerate_dt_count == 1


def test_dt_degenerado_en_la_segunda_muestra_usa_el_de_reserva():
    """Sin ningún dt válido previo hay que recurrir al de reserva."""
    filtro = OneEuroFilter()
    filtro(0.0, 5.0)
    assert math.isfinite(filtro(1.0, 5.0))
    assert filtro.degenerate_dt_count == 1


@pytest.mark.parametrize("malo", [float("nan"), float("inf"), float("-inf")])
def test_one_euro_rechaza_muestras_no_finitas(malo):
    """Un NaN contaminaría el estado de forma permanente: se propaga a todas las
    muestras siguientes."""
    filtro = OneEuroFilter()
    filtro(0.0, 0.0)
    with pytest.raises(ValueError):
        filtro(malo, DT)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_cutoff": 0.0}, {"min_cutoff": -1.0},
        {"d_cutoff": 0.0}, {"beta": -0.1},
    ],
)
def test_one_euro_valida_sus_parametros(kwargs):
    with pytest.raises(ValueError):
        OneEuroFilter(**kwargs)


def test_el_reloj_por_defecto_es_de_alta_resolucion():
    """perf_counter y no monotonic: en Windows este último tiene resolución de
    ~15.6 ms, comparable al dt de 33 ms de una cámara a 30 fps."""
    assert _DEFAULT_CLOCK is time.perf_counter
    assert time.get_clock_info("perf_counter").resolution < 1e-4


def test_sin_timestamp_usa_el_reloj_interno():
    filtro = OneEuroFilter()
    assert filtro(1.0) == 1.0
    assert math.isfinite(filtro(2.0))


def test_reloj_inyectable():
    t = [0.0]
    filtro = OneEuroFilter(1.0, 0.0, clock=lambda: t[0])
    filtro(0.0)
    t[0] = DT
    assert 0.0 < filtro(1.0) < 1.0


# --------------------------------------------------------------------------
# EMA
# --------------------------------------------------------------------------


def test_ema_primera_muestra_y_convergencia():
    ema = EMAFilter(0.5)
    assert ema(10.0) == 10.0
    assert ema(20.0) == pytest.approx(15.0)
    assert ema(20.0) == pytest.approx(17.5)


def test_ema_con_alpha_1_no_filtra():
    ema = EMAFilter(1.0)
    assert ema(1.0) == 1.0
    assert ema(2.0) == 2.0


def test_ema_ignora_el_timestamp():
    """Es intercambiable con el One Euro en el banco de comparación, pero su
    suavizado NO depende del tiempo entre muestras — esa es justamente su
    limitación."""
    a, b = EMAFilter(0.3), EMAFilter(0.3)
    a(0.0, 0.0), b(0.0, 0.0)
    assert a(1.0, 0.001) == b(1.0, 1000.0)


@pytest.mark.parametrize("alpha", [0.0, -0.1, 1.5])
def test_ema_valida_alpha(alpha):
    with pytest.raises(ValueError):
        EMAFilter(alpha)


def test_ema_rechaza_no_finitos():
    with pytest.raises(ValueError):
        EMAFilter(0.5)(float("nan"))


def test_ema_reset():
    ema = EMAFilter(0.5)
    ema(10.0)
    ema.reset()
    assert ema(20.0) == 20.0


# --------------------------------------------------------------------------
# Histéresis de clase
# --------------------------------------------------------------------------


def test_la_primera_prediccion_se_adopta_de_inmediato():
    h = ClassHysteresis(n_frames=5)
    assert h.update("feliz", 1.0) == "feliz"


def test_una_prediccion_suelta_no_cambia_la_clase():
    h = ClassHysteresis(n_frames=5)
    for _ in range(5):
        h.update("feliz", 1.0)
    assert h.update("triste", 1.0) == "feliz"
    assert h.update("feliz", 1.0) == "feliz"


def test_hacen_falta_tres_de_cinco_para_cambiar():
    h = ClassHysteresis(n_frames=5)
    for _ in range(5):
        h.update("feliz", 1.0)
    assert h.update("triste", 1.0) == "feliz"
    assert h.update("triste", 1.0) == "feliz"
    assert h.update("triste", 1.0) == "triste"


def test_la_confianza_baja_bloquea_el_cambio():
    h = ClassHysteresis(n_frames=3, min_confidence=0.7)
    for _ in range(3):
        h.update("feliz", 0.9)
    for _ in range(5):
        assert h.update("triste", 0.4) == "feliz"


def test_con_confianza_alta_el_cambio_si_ocurre():
    h = ClassHysteresis(n_frames=3, min_confidence=0.7)
    for _ in range(3):
        h.update("feliz", 0.9)
    h.update("triste", 0.95)
    assert h.update("triste", 0.95) == "triste"


def test_la_confianza_bloquea_la_alternancia_sostenida():
    """La mayoría sola NO basta contra una alternancia perfecta: la ventana
    queda 3-2 y cambia de bando cada dos pasos. Lo que la detiene es la
    confianza, y por eso las dos condiciones son complementarias.

    Un clasificador que alterna es un clasificador dudando, y sus confianzas
    rondan el 0.5.
    """
    h = ClassHysteresis(n_frames=5, min_confidence=0.7, initial="neutral")
    for i in range(20):
        h.update("feliz" if i % 2 else "triste", 0.5)
    assert h.current == "neutral"


def test_sin_umbral_de_confianza_la_alternancia_si_atraviesa():
    """El reverso del anterior, para que el límite quede fijado y documentado
    en vez de descubrirse en una sesión con niños delante."""
    h = ClassHysteresis(n_frames=5, min_confidence=0.0, initial="neutral")
    vistos = set()
    for i in range(20):
        vistos.add(h.update("feliz" if i % 2 else "triste", 1.0))
    assert len(vistos) > 1


def test_clase_inicial():
    """El proyecto arranca en NEUTRAL por decisión de diseño documentada."""
    h = ClassHysteresis(n_frames=5, initial="neutral")
    assert h.current == "neutral"


def test_current_y_window_para_el_hud():
    h = ClassHysteresis(n_frames=3)
    h.update("feliz", 0.8)
    assert h.current == "feliz"
    assert h.window == (("feliz", 0.8),)


def test_la_ventana_no_crece_mas_alla_de_n_frames():
    h = ClassHysteresis(n_frames=3)
    for _ in range(10):
        h.update("feliz", 1.0)
    assert len(h.window) == 3


def test_reset_de_la_histeresis():
    h = ClassHysteresis(n_frames=3)
    h.update("feliz", 1.0)
    h.reset(initial="neutral")
    assert h.current == "neutral"
    assert h.window == ()


@pytest.mark.parametrize("n", [0, -1])
def test_histeresis_valida_n_frames(n):
    with pytest.raises(ValueError):
        ClassHysteresis(n_frames=n)


@pytest.mark.parametrize("c", [-0.1, 1.5])
def test_histeresis_valida_min_confidence(c):
    with pytest.raises(ValueError):
        ClassHysteresis(min_confidence=c)


def test_histeresis_rechaza_confianza_no_finita():
    with pytest.raises(ValueError):
        ClassHysteresis().update("feliz", float("nan"))


def test_n_frames_1_no_filtra_nada():
    h = ClassHysteresis(n_frames=1)
    assert h.update("feliz", 1.0) == "feliz"
    assert h.update("triste", 1.0) == "triste"
