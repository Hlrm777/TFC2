"""Pruebas del P7.6: lo que salió de la sesión p75 (2026-11-07).

Tres cosas que vio el operador, cada una con lo que decidió:

1. Con las manos en las mejillas la cabeza se quedaba fija: la guarda solo entra
   si además la malla falla (``MallaInestable``), y mientras tanto la cabeza va
   con un límite de velocidad.
2. Con la cabeza baja el neutral se leía como enojo: el enojo pide más confianza
   cuanto más baja la cabeza (``voto_con_cabeceo``).
3. Con poca luz la cabeza volvía a vibrar: la zona muerta crece con el ruido de
   la malla (``RuidoMalla``, ``VigiaRuido``) y, si no basta, el robot se pausa.

Cada grupo atado a algo externo cuando se puede: las grabaciones, reproducidas.
"""

from __future__ import annotations

import csv
import math
import random
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

import telmeow_teleop.main as principal
from telmeow_teleop.config import AppConfig
from telmeow_teleop.emotion_classifier import COLUMNAS_CSV, EmotionClassifier, voto_con_cabeceo
from telmeow_teleop.head_tracker import RuidoMalla

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))


class RelojFalso:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def avanzar(self, s):
        self.t += s


# --------------------------------------------------------------------------
# 1. La guarda de la cabeza
# --------------------------------------------------------------------------


def _correr(guarda, desde, hasta, pose, encima, falla, paso=0.05):
    salidas, t = [], desde
    while t < hasta - 1e-9:
        p = pose(t) if callable(pose) else pose
        salidas.append((round(t, 3), guarda.paso(t, p, encima, falla(t) if callable(falla) else falla)))
        t += paso
    return salidas


def test_con_las_manos_en_las_mejillas_y_la_malla_bien_la_cabeza_sigue():
    """«El grito»: las manos sobre la caja de la cara, la malla viéndola bien. La
    cabeza sigue al operador, que gira despacio (20°/s, por debajo del límite)."""
    g = principal.GuardaCabeza()
    _correr(g, 0.0, 1.0, (0.0, 0.0), False, False)
    salidas = _correr(g, 1.0, 3.0, lambda t: (20.0 * (t - 1.0), 0.0), True, False)
    assert not g.tapada
    assert salidas[-1][1] == pytest.approx((20.0 * 1.95, 0.0))


def test_con_las_manos_encima_la_cabeza_va_con_limite_de_velocidad():
    """Las primeras poses inventadas llegan antes de que se note el fallo: con el
    límite, un salto de 40° tarda 40/60 s y no se ve como un latigazo."""
    g = principal.GuardaCabeza(limite_grados_s=60.0)
    _correr(g, 0.0, 1.0, (0.0, 0.0), True, False)
    salidas = _correr(g, 1.0, 1.5, (40.0, 0.0), True, False)
    pans = [s[0] for _t, s in salidas]
    assert pans[0] == pytest.approx(3.0)                       # 60°/s · 0.05 s
    assert all(b - a <= 3.0 + 1e-9 for a, b in zip(pans, pans[1:]))
    # Sin manos encima no hay límite: lo medido pasa tal cual.
    g = principal.GuardaCabeza()
    _correr(g, 0.0, 1.0, (0.0, 0.0), False, False)
    assert g.paso(1.0, (40.0, 0.0), False, False) == (40.0, 0.0)


def test_al_taparse_la_malla_falla_y_la_guarda_entra_sin_salto():
    """Las manos llevan un rato encima con la cabeza siguiendo al operador; al
    taparse, la malla empieza a fallar: la guarda se queda donde estaba la
    cabeza publicada, sin volver a una pose de antes."""
    g = principal.GuardaCabeza()
    _correr(g, 0.0, 2.0, (10.0, 5.0), True, False)
    salidas = _correr(g, 2.0, 3.0, (60.0, -30.0), True, True)
    assert g.tapada
    assert salidas[0][1] == pytest.approx((10.0, 5.0))
    assert {s for _t, s in salidas} == {(10.0, 5.0)}


def test_si_entra_a_la_vez_que_las_manos_es_la_guarda_del_h58():
    """Taparse de golpe: manos y fallo a la vez. Como antes, la pose de 0.3 s
    antes de ver las manos, porque las primeras inventadas llegan antes."""
    g = principal.GuardaCabeza()
    _correr(g, 0.0, 1.0, (12.0, 3.0), False, False)
    _correr(g, 1.0, 1.2, (35.0, 40.0), False, False)
    salidas = _correr(g, 1.2, 2.0, (-20.0, 25.0), True, True)
    assert {s for _t, s in salidas} == {(12.0, 3.0)}


def test_un_fallo_suelto_no_deja_la_cabeza_fija_todo_el_gesto():
    """En p75 un solo tropiezo de la malla dejaba la guarda puesta el 73 % de
    «el grito». Con la malla un segundo sin fallar, se suelta y la cabeza vuelve
    a seguir al operador con la mezcla de 1 s."""
    g = principal.GuardaCabeza(suelta_s=1.0)
    _correr(g, 0.0, 1.0, (0.0, 0.0), True, False)
    _correr(g, 1.0, 1.1, (0.0, 0.0), True, True)              # el tropiezo
    assert g.tapada
    _correr(g, 1.1, 2.05, (20.0, 0.0), True, False)
    assert g.tapada                                            # aún no ha pasado 1 s
    _correr(g, 2.05, 2.2, (20.0, 0.0), True, False)
    assert not g.tapada
    salidas = _correr(g, 2.2, 4.0, (20.0, 0.0), True, False)
    assert salidas[-1][1] == pytest.approx((20.0, 0.0))


def test_tapada_sigue_mientras_la_malla_siga_fallando_aunque_a_ratos_no():
    """Tapada, la malla falla casi siempre; un frame que parece bien no la suelta."""
    g = principal.GuardaCabeza()
    _correr(g, 0.0, 1.0, (5.0, 5.0), False, False)
    falla = lambda t: int(t * 20) % 7 != 0                     # noqa: E731
    salidas = _correr(g, 1.0, 2.5, (60.0, 60.0), True, falla)
    assert all(s == (5.0, 5.0) for _t, s in salidas)


def test_por_defecto_la_malla_cuenta_como_fallando():
    """Sin ese dato (las pruebas y las herramientas de antes) es la guarda del H58."""
    g = principal.GuardaCabeza()
    assert g.paso(0.0, (40.0, 40.0), True) == (0.0, 0.0)


def test_la_malla_inestable():
    m = principal.MallaInestable(n=5, umbral=3.0, sin_cara_s=0.3)
    rng = random.Random(1)
    assert not any(m.paso(0.05 * k, (rng.gauss(0, 0.8), rng.gauss(0, 0.8))) for k in range(40))
    # Una cara inventada: saltos de 6-13° por frame (H58).
    estados = [m.paso(2.0 + 0.05 * k, (rng.uniform(-30, 30), rng.uniform(-20, 40))) for k in range(10)]
    assert estados[-1]
    # Sin cara: falla a partir de 0.3 s; un salto suelto no basta.
    m = principal.MallaInestable()
    for k in range(10):
        m.paso(0.05 * k, (0.0, 0.0))
    assert not m.paso(0.5, (12.0, 0.0))
    assert not m.paso(0.55, (12.0, 0.0))
    assert not m.paso(0.6, None) and not m.paso(0.85, None)
    assert m.paso(0.9, None)


# --------------------------------------------------------------------------
# 2. El enojo con la cabeza baja
# --------------------------------------------------------------------------


@pytest.mark.parametrize("etiqueta, confianza, cabeceo, vota", [
    ("enojado", 0.55, 0.0, "enojado"),       # a nivel, como siempre
    ("enojado", 0.55, -2.0, "enojado"),      # hasta 2° abajo, no cambia
    ("enojado", 0.55, -6.0, "neutral"),      # pide 0.5 + 0.03·4 = 0.62
    ("enojado", 0.70, -6.0, "enojado"),      # un enojo claro sigue saliendo
    ("enojado", 0.55, 10.0, "enojado"),      # cabeza arriba: pide lo de siempre
    ("enojado", 0.40, 10.0, "neutral"),      # lo de siempre es min_confidence
    ("triste", 0.30, -20.0, "triste"),       # solo el enojo
    ("enojado", 0.30, None, "enojado"),      # sin head tracker no actúa
])
def test_el_voto_con_cabeceo(etiqueta, confianza, cabeceo, vota):
    """Con la regla puesta, un voto de enojo pide al menos ``min_confidence`` —es
    lo que ya pedía la histéresis a la media de los votos— y más con la cabeza
    baja. Así se reprodujo al decidirla."""
    assert voto_con_cabeceo(etiqueta, confianza, cabeceo, 0.5, 0.03, 2.0) == vota
    assert voto_con_cabeceo(etiqueta, confianza, cabeceo, 0.5, 0.0, 2.0) == etiqueta


class _Fijo:
    """Backend que dice siempre lo mismo."""

    nombre = "fijo"
    relativo = False

    def __init__(self, etiqueta, confianza):
        from telmeow_teleop.emotion_classifier import Distribucion

        probs = {e: 0.0 for e in ("feliz", "triste", "sorprendido", "neutral", "enojado")}
        probs[etiqueta] = confianza
        self._d = Distribucion(etiqueta, confianza, probs, etiqueta)

    def distribucion(self, rostro):
        return self._d


def _clasificador(**kw):
    return EmotionClassifier(_Fijo("enojado", 0.55), detector=lambda f: (10, 20, 60, 60),
                             frame_espejado=False, emotion_hz=1000.0, **kw)


def test_el_clasificador_no_pone_enojado_con_la_cabeza_baja_si_no_es_claro():
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    c = _clasificador(enojo_por_grado=0.03, enojo_desde=2.0)
    for k in range(10):
        c.process(frame, 0.01 * k, cabeceo=-8.0)
    assert c.emocion.etiqueta == "neutral"
    assert c.ultima.etiqueta == "enojado"                      # la predicción cruda no cambia
    assert c.cabeceo == pytest.approx(-8.0)
    c = _clasificador(enojo_por_grado=0.03, enojo_desde=2.0)
    for k in range(10):
        c.process(frame, 0.01 * k, cabeceo=0.0)
    assert c.emocion.etiqueta == "enojado"


def test_el_csv_de_emocion_lleva_el_cabeceo_y_la_regla():
    from telmeow_teleop.emotion_classifier import _fila_csv

    c = _clasificador(enojo_por_grado=0.03, enojo_desde=2.0)
    c.process(np.zeros((120, 160, 3), dtype=np.uint8), 0.0, cabeceo=-4.25)
    fila = dict(zip(COLUMNAS_CSV, _fila_csv(0.0, 0, c, 100.0)))
    assert (fila["cabeceo"], fila["enojo_por_grado"], fila["enojo_desde"]) == ("-4.25", "0.03", "2")


def test_la_configuracion_del_enojo():
    """0.015 desde el 2026-11-08: la regla suave, confirmada en p78 (antes 0.03)."""
    cfg = AppConfig.from_env({})
    assert (cfg.emotion.enojo_por_grado, cfg.emotion.enojo_desde) == (0.015, 2.0)
    assert AppConfig.from_env({"TELMEOW_EMOTION_ENOJO_POR_GRADO": "0"}).emotion.enojo_por_grado == 0.0
    c = EmotionClassifier.desde_config(cfg, backend=_Fijo("enojado", 0.6))
    assert c.enojo_por_grado == 0.015


@pytest.mark.parametrize("base", ["p75", "p7_final"])
def test_la_regla_reproducida_sobre_las_sesiones(base):
    """Las cifras con que se decidió (2026-11-07), con las predicciones grabadas
    y la ClassHysteresis de verdad: el enojo falso del neutral de p75 baja de 23
    a 8 % y el enojo de verdad de p7_final se queda en el 84 %."""
    ruta = RAIZ / f"{base}_emocion.csv"
    if not ruta.exists():
        pytest.skip(f"falta {ruta.name}")
    import bisect

    import analizar_registro_emocion as are

    filas = are.cargar(ruta)
    with open(RAIZ / f"{base}_cabeza.csv", encoding="utf-8") as f:
        cabeza = list(csv.DictReader(f))
    tc = [float(x["t"]) for x in cabeza]
    for fila in filas:
        i = min(bisect.bisect_left(tc, fila["t"]), len(cabeza) - 1)
        fila["cabeceo"] = float(cabeza[i]["raw_tilt"]) if abs(tc[i] - fila["t"]) < 0.25 else None
        fila["enojo_por_grado"], fila["enojo_desde"] = 0.03, 2.0
    emos, _ = are.reproducir(filas, 5, 0.5)
    if base == "p75":
        idx = [i for i, f in enumerate(filas) if f["etiqueta"] in (0, 2, 4, 6, 10, 11, 12)
               and f["rostro"] and not f["calibrando"]]
        assert 100 * np.mean([emos[i] == "enojado" for i in idx]) < 10.0
    else:
        idx = [i for i, f in enumerate(filas) if f["etiqueta"] in (3, 7) and f["rostro"] and not f["calibrando"]]
        assert 100 * np.mean([emos[i] == "enojado" for i in idx]) > 80.0


# --------------------------------------------------------------------------
# 3. La luz: el ruido de la malla, la zona y la pausa
# --------------------------------------------------------------------------


def test_el_ruido_de_la_malla_mide_el_temblor_y_no_el_movimiento():
    rng = random.Random(2)
    quieto, girando = RuidoMalla(), RuidoMalla()
    for k in range(120):
        t = k / 20.0
        quieto.actualizar(t, (rng.gauss(0, 1.0), rng.gauss(0, 1.0)))
        # Un giro suave de ±40° con muy poco ruido: casi no hay segunda diferencia.
        girando.actualizar(t, (40 * math.sin(t), rng.gauss(0, 0.2)))
    # El índice toma, frame a frame, el peor de los dos ejes: con el mismo ruido σ
    # en los dos da 1.56·σ. Los umbrales se fijaron con este mismo índice.
    assert quieto.sigma == pytest.approx(1.56, rel=0.25)
    assert girando.sigma < 0.6


def test_el_ruido_necesita_muestras_y_olvida_lo_viejo():
    r = RuidoMalla(ventana_s=3.0, minimo=10)
    assert r.sigma is None
    rng = random.Random(3)
    for k in range(80):
        r.actualizar(k / 20.0, (rng.gauss(0, 3.0), 0.0))
    alto = r.sigma
    for k in range(80, 200):
        r.actualizar(k / 20.0, (rng.gauss(0, 0.5), 0.0))
    assert alto > 2.0 and r.sigma < 1.0
    r.cortar()
    r.actualizar(10.0, (50.0, 0.0))                            # tras un corte no salta
    assert r.sigma < 1.0


def test_la_zona_crece_con_el_ruido_entre_sus_limites():
    v = principal.VigiaRuido(1.0, 3.0, 1.0)
    assert v.paso(0.0, 0.5) == (1.0, False)
    assert v.paso(0.1, 2.3) == (pytest.approx(2.3), False)
    assert v.paso(0.2, 4.0)[0] == 3.0
    assert v.paso(0.3, None)[0] == 3.0                        # sin medida, se queda


def test_la_pausa_por_poca_luz_entra_y_sale_con_histeresis():
    v = principal.VigiaRuido(1.0, 3.0, 1.0, pausa_s=1.0, reanuda_s=3.0, margen=0.85)
    assert not any(v.paso(0.1 * k, 3.5)[1] for k in range(10))   # 0.9 s por encima
    assert v.paso(1.0, 3.5)[1]                                   # 1 s: pausa
    assert all(v.paso(1.0 + 0.1 * k, 2.9)[1] for k in range(1, 40))  # 2.9 > 0.85·3: no
    assert all(v.paso(5.0 + 0.1 * k, 2.0)[1] for k in range(0, 30))  # 2.9 s en calma
    assert not v.paso(8.0, 2.0)[1]                               # 3 s: reanuda


def test_el_ruido_solo_cuenta_de_frente_y_sin_manos_en_la_cara():
    assert principal.cuenta_para_ruido((5.0, -3.0), 10.0, None)
    assert not principal.cuenta_para_ruido(None, 10.0, None)
    assert not principal.cuenta_para_ruido((45.0, 0.0), 10.0, None)   # de perfil
    assert not principal.cuenta_para_ruido((0.0, 25.0), 10.0, None)
    assert not principal.cuenta_para_ruido((0.0, 0.0), 10.0, 8.0)     # manos hace 2 s
    assert principal.cuenta_para_ruido((0.0, 0.0), 10.0, 6.5)


def test_la_pausa_por_luz_no_publica_ni_deja_gestos_y_reanuda_con_rampa():
    reloj = RelojFalso()

    class Pub:
        def __init__(self):
            self.campos = []

        def publish(self, **c):
            self.campos.append(c)
            return True

    pub = Pub()
    p = principal.PublicadorPeriodico(pub, 0.05, rampa_s=1.0, reloj=reloj)
    p.actualizar(pan=0.0)
    assert p.tick()
    p.fijar_pausa_luz(True)
    assert p.pausado and p.pausado_por_luz
    assert not p.tick() and not p.gesto("page_next")
    # La tecla no la quita: siguen pausados por la luz.
    p.alternar_pausa()
    p.alternar_pausa()
    assert p.pausado
    p.actualizar(pan=40.0)
    p.fijar_pausa_luz(False)
    assert not p.pausado
    pans = []
    for _ in range(25):
        p.tick()
        pans.append(pub.campos[-1]["pan"])
        reloj.avanzar(0.05)
    assert pans[0] == pytest.approx(0.0) and pans[-1] == 40.0


def test_la_tecla_y_la_luz_conviven():
    p = principal.PublicadorPeriodico(type("P", (), {"publish": lambda s, **c: True})(), 0.05)
    p.actualizar(pan=0.0)
    p.alternar_pausa()                                         # pausa con la tecla
    p.fijar_pausa_luz(True)
    p.fijar_pausa_luz(False)                                   # vuelve la luz
    assert p.pausado and not p.tick()                          # sigue la de la tecla


def test_la_configuracion_de_la_luz():
    h = AppConfig.from_env({}).head
    assert (h.zona_muerta, h.zona_muerta_max, h.zona_por_ruido) == (1.0, 3.0, 1.0)
    assert (h.pausa_ruido_s, h.reanuda_ruido_s) == (1.0, 3.0)
    v = principal.VigiaRuido.desde_config(AppConfig.from_env({"TELMEOW_HEAD_ZONA_MUERTA_MAX": "2.5"}))
    assert v.maximo == 2.5


def test_la_zona_del_tracker_se_cambia_en_marcha():
    from telmeow_teleop.head_tracker import HeadTracker

    t = HeadTracker(min_cutoff=1000.0, beta=0.0, zona_muerta=1.0, clock=RelojFalso())
    try:
        t.map_to_robot(0.0, 0.0, 0.0)
        t.zona_muerta = 3.0
        assert t.zona_muerta == 3.0
        assert t.map_to_robot(1.5, 0.0, 0.1)[0] == pytest.approx(0.0, abs=0.01)   # 1.5·1 < 3
        with pytest.raises(ValueError):
            t.zona_muerta = -1.0
    finally:
        t.close()


def test_el_panel_dice_poca_luz():
    lienzo = np.zeros((480, 640 + principal.ANCHO_PANEL, 3), dtype=np.uint8)
    principal.dibujar_panel(lienzo, 640, principal.EstadoPanel(
        pausa=True, pausa_luz=True, ruido_malla=3.4, zona_muerta=3.0, brillo=120.0))
    assert lienzo[:, 640:].any()


# --------------------------------------------------------------------------
# 4. Reproducido sobre las sesiones
# --------------------------------------------------------------------------


def _simular(base):
    import simular_guarda_luz as sgl

    return sgl.simular(base)


@pytest.mark.skipif(not (RAIZ / "p75.csv").exists(), reason="falta la sesión p75")
def test_en_p75_la_cabeza_sigue_en_las_mejillas_y_se_guarda_tapada():
    filas = _simular(RAIZ / "p75")

    def guarda(e):
        g = [f for f in filas if f["etiqueta"] == e]
        return 100 * sum(f["tapada_ahora"] for f in g) / len(g)

    assert guarda(3) == 0.0 and guarda(5) == 0.0               # una mano en la mejilla
    assert guarda(7) < 10.0                                    # «el grito»
    assert guarda(9) > 60.0                                    # tapada
    assert not any(f["pausa"] for f in filas)                  # con la luz de trabajo


@pytest.mark.skipif(not (RAIZ / "p7_final.csv").exists(), reason="falta la sesión p7_final")
def test_mirar_de_lado_no_es_poca_luz():
    """En p7_final el operador se quedó 15 s con la cabeza girada 40-50°: de perfil
    la malla tiembla más (σ 3.4-4) y, contándolo, el robot se habría pausado."""
    assert not any(f["pausa"] for f in _simular(RAIZ / "p7_final"))


@pytest.mark.skipif(not (RAIZ / "p75_anterior.csv").exists(), reason="falta la toma sin luz")
def test_sin_luz_la_zona_sube_y_la_cabeza_se_calma(tmp_path):
    """La primera toma de p75, con la luz apagada: la cámara a 10 fps y la malla
    temblando el doble. La zona sube por encima de 2° y no hace falta pausar."""
    shutil.copy(RAIZ / "p75_anterior.csv", tmp_path / "oscuro.csv")
    shutil.copy(RAIZ / "p75_cabeza_anterior.csv", tmp_path / "oscuro_cabeza.csv")
    filas = _simular(tmp_path / "oscuro")
    zonas = [f["zona"] for f in filas if f["sigma"] is not None]
    assert np.median(zonas) > 2.0
    assert not any(f["pausa"] for f in filas)
