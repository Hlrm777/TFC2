"""Pruebas del P7.7: lo que salió de la sesión p76 (2026-11-07).

1. La cabeza toma su neutro al acabar la calibración de la cara, no en el primer
   frame (en p76 eso la dejaba mirando ~15° abajo y desplazaba el cabeceo del
   enojo 8°).
2. Sin cara y sin manos encima, la cabeza se queda y va al frente, y si la cara
   no vuelve en 3 s el robot se pausa (SIN CARA).
3. En «el grito», una mano que BlazePose pierde aguanta 1.5 s si la otra sigue.
4. La sesión del reentrenamiento y su criterio, fijado antes de grabar.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np
import pytest

import telmeow_teleop.main as principal
from telmeow_teleop.config import AppConfig
from telmeow_teleop.mano_cara import T_SALIDA, T_SALIDA_PERDIDA, DetectorManoCara

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))

import reentrenar_p77 as rp  # noqa: E402
import sesion_verificacion as sv  # noqa: E402


# --------------------------------------------------------------------------
# 1. El neutro de la cabeza
# --------------------------------------------------------------------------


def test_suavizar_mezcla_desde_lo_ultimo_que_salio():
    reloj = {"t": 0.0}

    class Pub:
        campos = []

        def publish(self, **c):
            self.campos.append(c)
            return True

    pub = Pub()
    p = principal.PublicadorPeriodico(pub, 0.05, rampa_s=1.0, reloj=lambda: reloj["t"])
    p.actualizar(tilt=-15.0)
    p.tick()
    p.actualizar(tilt=0.0)                                     # el neutro nuevo
    p.suavizar()
    tilts = []
    for _ in range(25):
        p.tick()
        tilts.append(pub.campos[-1]["tilt"])
        reloj["t"] += 0.05
    assert tilts[0] == pytest.approx(-15.0) and tilts[-1] == 0.0
    assert all(b >= a for a, b in zip(tilts, tilts[1:]))


# --------------------------------------------------------------------------
# 2. Sin cara
# --------------------------------------------------------------------------


def _correr(guarda, desde, hasta, pose, encima, falla, sin_cara, paso=0.05):
    salidas, t = [], desde
    while t < hasta - 1e-9:
        p = pose(t) if callable(pose) else pose
        sc = sin_cara(t) if callable(sin_cara) else sin_cara
        f = falla(t) if callable(falla) else falla
        salidas.append((round(t, 3), guarda.paso(t, p, encima, f, sc)))
        t += paso
    return salidas


def test_sin_cara_y_sin_manos_la_cabeza_se_queda_y_va_al_frente():
    """La luz apagada de p76: la malla pierde la cara y no la recupera. Antes la
    cabeza se quedaba 20 s mirando a un lado; ahora, quieta 2 s y al frente."""
    g = principal.GuardaCabeza()
    _correr(g, 0.0, 1.0, (40.0, -20.0), False, False, False)
    salidas = dict(_correr(g, 1.0, 5.0, None, False, True, True))
    assert g.tapada
    assert salidas[2.9] == (40.0, -20.0)
    assert salidas[4.0] == pytest.approx((0.0, 0.0))


def test_cuando_vuelve_la_cara_estable_la_cabeza_vuelve_al_operador():
    g = principal.GuardaCabeza(suelta_s=1.0)
    _correr(g, 0.0, 1.0, (10.0, 0.0), False, False, False)
    _correr(g, 1.0, 4.0, None, False, True, True)
    _correr(g, 4.0, 4.9, (20.0, 0.0), False, False, False)      # vuelve, < 1 s estable
    assert g.tapada
    _correr(g, 4.9, 5.2, (20.0, 0.0), False, False, False)
    assert not g.tapada
    salidas = _correr(g, 5.2, 7.0, (20.0, 0.0), False, False, False)
    assert salidas[-1][1] == pytest.approx((20.0, 0.0))


def test_sin_el_dato_de_cara_perdida_es_como_antes():
    g = principal.GuardaCabeza()
    assert g.paso(0.0, (10.0, 0.0), False) == (10.0, 0.0)
    assert g.paso(0.1, None, False) is None


def test_la_malla_dice_si_lleva_la_cara_perdida():
    m = principal.MallaInestable(sin_cara_s=0.3)
    m.paso(0.0, (0.0, 0.0))
    assert not m.sin_cara
    m.paso(0.2, None)
    assert not m.sin_cara
    m.paso(0.35, None)
    assert m.sin_cara
    m.paso(0.4, (0.0, 0.0))
    assert not m.sin_cara


def test_la_pausa_sin_cara():
    v = principal.VigiaCara(pausa_s=3.0, reanuda_s=1.0)
    assert not any(v.paso(0.1 * k, False, False) for k in range(30))   # 2.9 s
    assert v.paso(3.0, False, False)
    assert v.paso(3.5, True, False)                                     # vuelve: 1 s más
    assert not v.paso(4.6, True, False)
    # Con las manos en la cara no cuenta: se tapa a propósito.
    v = principal.VigiaCara()
    assert not any(v.paso(0.1 * k, False, True) for k in range(100))


def test_la_pausa_sin_cara_convive_con_las_demas():
    p = principal.PublicadorPeriodico(type("P", (), {"publish": lambda s, **c: True})(), 0.05)
    p.actualizar(pan=0.0)
    p.fijar_pausa("cara", True)
    p.fijar_pausa_luz(True)
    assert p.pausado and p.pausado_sin_cara and p.pausado_por_luz
    p.fijar_pausa("cara", False)
    assert p.pausado and not p.tick()                          # sigue la de la luz
    p.fijar_pausa_luz(False)
    assert not p.pausado and p.tick()


def test_la_configuracion_sin_cara():
    h = AppConfig.from_env({}).head
    assert (h.pausa_sin_cara_s, h.reanuda_cara_s) == (3.0, 1.0)
    assert principal.VigiaCara.desde_config(AppConfig()).pausa_s == 3.0


def test_el_panel_dice_sin_cara():
    lienzo = np.zeros((480, 640 + principal.ANCHO_PANEL, 3), dtype=np.uint8)
    principal.dibujar_panel(lienzo, 640, principal.EstadoPanel(pausa=True, pausa_cara=True, brillo=120.0))
    assert lienzo[:, 640:].any()


@pytest.mark.skipif(not (RAIZ / "p76.csv").exists(), reason="falta la sesión p76")
def test_en_p76_con_la_luz_apagada_la_cabeza_va_al_frente_y_se_pausa():
    import simular_guarda_luz as sgl

    filas = sgl.simular(RAIZ / "p76")
    apagada = [f for f in filas if f["etiqueta"] == 11]
    assert any(f["pausa_cara"] for f in apagada)
    final = [f["ahora"] for f in apagada if f["t"] > apagada[-1]["t"] - 3.0]
    assert all(p == pytest.approx((0.0, 0.0)) for p in final)
    # Y en las etiquetas con luz, ninguna pausa sin cara.
    assert not any(f["pausa_cara"] for f in filas if f["etiqueta"] <= 10)


@pytest.mark.parametrize("base", ["p75", "p7_final"])
def test_sin_pausas_sin_cara_en_las_sesiones_con_luz(base):
    if not (RAIZ / f"{base}.csv").exists():
        pytest.skip(f"falta {base}")
    import simular_guarda_luz as sgl

    assert not any(f["pausa_cara"] for f in sgl.simular(RAIZ / base))


# --------------------------------------------------------------------------
# 3. La mano perdida en «el grito»
# --------------------------------------------------------------------------


EN_LA_CARA, ABAJO = (0.15, 0.2), (0.6, -1.1)


def _activo(d, desde=0.0):
    for k in range(20):
        d.actualizar(desde + 0.05 * k, EN_LA_CARA, True)
    assert d.activo


def test_una_mano_perdida_aguanta_si_la_otra_sigue_en_la_cara():
    d = DetectorManoCara()
    _activo(d)
    n = int(T_SALIDA_PERDIDA / 0.05)
    estados = [d.actualizar(1.0 + 0.05 * k, ABAJO, False, otra_en_la_cara=True) for k in range(n + 2)]
    assert all(estados[:n - 1]) and not estados[-1]
    assert T_SALIDA_PERDIDA == 1.5


def test_sin_la_otra_mano_se_suelta_como_siempre():
    d = DetectorManoCara()
    _activo(d)
    n = int(T_SALIDA / 0.05)
    estados = [d.actualizar(1.0 + 0.05 * k, ABAJO, False, otra_en_la_cara=False) for k in range(n + 2)]
    assert not estados[-1]


def test_una_mano_que_se_ve_fuera_sale_aunque_la_otra_siga():
    """Bajar UNA mano, viéndola: no hay pérdida que aguantar."""
    d = DetectorManoCara()
    _activo(d)
    n = int(T_SALIDA / 0.05)
    estados = [d.actualizar(1.0 + 0.05 * k, ABAJO, True, otra_en_la_cara=True) for k in range(n + 2)]
    assert not estados[-1]


@pytest.mark.skipif(not (RAIZ / "p76_brazos.csv").exists(), reason="falta la sesión p76")
def test_en_p76_la_mano_izquierda_se_perdio_1_6_s():
    """El corte que vio el operador: 1.62 s fuera, una décima más que los 1.5 s
    que eligió. Se fija aquí para que la cifra del plan tenga de dónde salir."""
    import analizar_mano_cara as amc
    from telmeow_teleop.mano_cara import ZONA_SALIDA

    filas = [f for f in csv.DictReader(open(RAIZ / "p76_brazos.csv", encoding="utf-8"))
             if 158.0 <= float(f["t"]) <= 163.0]
    fuera = []
    for f in filas:
        m, _ = amc.muestra_de_fila(f, "L")
        fuera.append(m is None or not (ZONA_SALIDA[0] <= m[0] <= ZONA_SALIDA[1]
                                       and ZONA_SALIDA[2] <= m[1] <= ZONA_SALIDA[3]))
    ts = [float(f["t"]) for f, x in zip(filas, fuera) if x]
    assert 1.5 < ts[-1] - ts[0] < 1.75


def test_el_tamano_de_la_mano_va_al_csv():
    from telmeow_teleop.arm_tracker import COLUMNAS_CSV

    assert {"tam_mano_L", "tam_mano_R"} <= set(COLUMNAS_CSV)


# --------------------------------------------------------------------------
# 4. La sesión del reentrenamiento
# --------------------------------------------------------------------------


def test_el_protocolo_del_reentrenamiento_y_lo_que_pide_cada_etiqueta_casan():
    etiquetas = [e for e, *_ in sv.PROTOCOLO_ENTRENAR]
    assert etiquetas == list(range(16))
    que = {e: (t, r) for e, t, _s, r in sv.PROTOCOLO_ENTRENAR}
    for e, esperada in rp.ESPERADAS_P77.items():
        assert esperada.upper()[:5] in que[e][1].upper() or e == 0
    assert "teclado" in que[2][0] and "arriba" in que[3][0] and "teclado" in que[6][0]
    assert "teclado" in que[10][0] and "Estira" in que[13][0]


def test_cada_sesion_de_entrenamiento_dice_lo_que_pide_y_existe():
    for nombre, esperadas in rp.ESPERADAS_POR_SESION.items():
        assert set(esperadas.values()) <= {"feliz", "triste", "sorprendido", "neutral", "enojado"}
        ruta = RAIZ / f"{nombre}_emocion.csv"
        if ruta.exists():
            assert (RAIZ / f"{nombre}_recortes").is_dir(), nombre
    assert "p77" not in rp.ESPERADAS_POR_SESION                # la de evaluar no entrena


def _resumen(**cambios):
    base = {"neutral al frente": 90.0, "neutral abajo": 60.0, "neutral arriba": 70.0,
            "neutral hablando": 80.0, "enojado al frente": 80.0, "enojado abajo": 60.0,
            "triste sin ceño": 90.0, "triste con ceño": 85.0, "triste abajo": 70.0}
    base.update(cambios)
    return base


def test_el_criterio_del_reentrenamiento():
    base = _resumen()
    assert rp.criterio(base, _resumen(**{"neutral abajo": 85.0, "neutral arriba": 80.0})).vale
    # El neutral no mejora lo bastante.
    v = rp.criterio(base, _resumen(**{"neutral abajo": 64.0}))
    assert not v.vale and "neutral" in v.motivos[0]
    # Mejora el neutral pero cae el enojo.
    v = rp.criterio(base, _resumen(**{"neutral abajo": 95.0, "enojado abajo": 40.0}))
    assert not v.vale and any("enojo" in m for m in v.motivos)
    # Todos los neutrales al 90 % valen aunque suban poco.
    todos = {n: 92.0 for n in rp.NEUTRALES}
    assert rp.criterio(_resumen(**{n: 89.0 for n in rp.NEUTRALES}), _resumen(**todos)).vale


def test_el_resumen_por_tramo():
    filas = []
    for etiqueta, emocion, n in ((1, "neutral", 8), (1, "enojado", 2), (5, "enojado", 3), (5, "neutral", 1)):
        filas += [{"etiqueta": etiqueta, "emocion": emocion, "calibrando": False}] * n
    filas.append({"etiqueta": 1, "emocion": "enojado", "calibrando": True})   # no cuenta
    r = rp.resumir(filas)
    assert r["neutral al frente"] == 80.0 and r["enojado al frente"] == 75.0
    assert r["triste abajo"] is None


def test_las_muestras_por_sesion_usan_su_mapa():
    filas = [{"etiqueta": e, "rostro": 1, "calibrando": 0, "manos": 0, "manual": None}
             for e in (0, 0, 2, 9)]
    vectores = {i: np.full(3, float(i)) for i in range(4)}
    episodios = [([0, 1], 1)]                                   # calibra con las dos primeras
    X, y = rp.muestras(filas, vectores, episodios, {0: "neutral", 2: "enojado"})
    clases = ["feliz", "triste", "sorprendido", "neutral", "enojado"]
    assert [clases[c] for c in y] == ["neutral", "enojado"]    # la 9 no está en el mapa
    assert X[1] == pytest.approx(np.full(3, 2.0) - 0.5)


def test_el_informe_del_entrenamiento(tmp_path):
    from telmeow_teleop.arm_tracker import COLUMNAS_CSV as BRAZOS
    from telmeow_teleop.emotion_classifier import COLUMNAS_CSV as EMOCION

    def escribir(ruta, columnas, filas):
        with open(ruta, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=columnas, restval="")
            w.writeheader()
            w.writerows(filas)

    emocion = [{"t": 0.1 * k, "etiqueta": e, "rostro": 1, "emocion": rp.ESPERADAS_P77[e], "calibrando": 0}
               for e in range(12) for k in range(5)]
    brazos = [{"t": 0.1 * k, "etiqueta": e, "tam_mano_L": 40.0 if e in (12, 14) else 60.0,
               "tam_mano_R": 42.0 if e in (12, 14) else 63.0} for e in range(12, 16) for k in range(5)]
    escribir(tmp_path / "e_emocion.csv", EMOCION, emocion)
    escribir(tmp_path / "e_brazos.csv", BRAZOS, brazos)
    informe = sv.analizar_entrenamiento(tmp_path / "e")
    assert "neutral abajo        100 %" in informe
    assert "estirar / yo no fui = 1.50" in informe


def test_con_entrenar_el_informe_y_la_base_son_los_suyos(monkeypatch, tmp_path):
    monkeypatch.setattr(sv, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sv, "analizar_entrenamiento", lambda base: f"ENTRENAR {base.name}")
    assert sv.main(["--analizar", "--entrenar"]) == 0
    assert (tmp_path / "p77_informe.txt").read_text(encoding="utf-8") == "ENTRENAR p77"
