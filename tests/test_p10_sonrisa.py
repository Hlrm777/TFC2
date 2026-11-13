"""P10.1 (H70): la sonrisa con la cabeza baja.

En la sesión p10 el operador sonreía leyendo con la cabeza 10 a 16° abajo; el
modelo empataba entre feliz y enojado, la regla del cabeceo descartaba el enojo
y el voto se iba a neutral. Desde el 2026-11-10 el voto descartado va a la
segunda opción del modelo, con su probabilidad (``voto_del_modelo``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

from telmeow_teleop.config import EMOTION_LABELS, AppConfig
from telmeow_teleop.emotion_classifier import (
    COLUMNAS_CSV,
    Distribucion,
    EmotionClassifier,
    _fila_csv,
    voto_con_cabeceo,
    voto_del_modelo,
)

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))

FRAME = np.zeros((120, 160, 3), dtype=np.uint8)


def _probs(**kw):
    return {e: float(kw.get(e, 0.0)) for e in EMOTION_LABELS}


#: Lo que decía el modelo en la etiqueta 9 de p10: casi empate, gana el enojo.
SONRISA_ABAJO = _probs(enojado=0.52, feliz=0.46, neutral=0.02)
SONRISA_CLARA = _probs(feliz=0.62, enojado=0.33, neutral=0.05)
#: Y leyendo sin expresión con la cabeza baja (etiquetas 6 y 7): el enojo falso
#: de siempre, con neutral de segunda.
NEUTRAL_ABAJO = _probs(enojado=0.47, neutral=0.41, triste=0.08, feliz=0.04)


@pytest.mark.parametrize("probs, cabeceo, segunda, vota", [
    (SONRISA_ABAJO, -12.0, True, ("feliz", 0.46)),      # descartado: va a la segunda, con SU probabilidad
    (SONRISA_ABAJO, -12.0, False, ("neutral", 0.52)),   # como antes del P10.1
    (SONRISA_ABAJO, 0.0, True, ("enojado", 0.52)),      # a nivel el enojo no se descarta
    (NEUTRAL_ABAJO, -12.0, True, ("neutral", 0.41)),    # un neutral de verdad sigue en neutral
    (SONRISA_CLARA, -12.0, True, ("feliz", 0.62)),      # lo que no es enojo no se toca
    (SONRISA_ABAJO, None, True, ("enojado", 0.52)),     # sin head tracker la regla no actúa
])
def test_el_voto_del_modelo(probs, cabeceo, segunda, vota):
    etiqueta = max(probs, key=probs.get)
    voto = voto_del_modelo(etiqueta, probs[etiqueta], probs, cabeceo, 0.5, 0.015, 2.0, segunda)
    assert voto == (vota[0], pytest.approx(vota[1]))


def test_sin_la_regla_vota_lo_mismo_que_voto_con_cabeceo():
    for probs in (SONRISA_ABAJO, NEUTRAL_ABAJO, SONRISA_CLARA):
        for cabeceo in (None, 0.0, -6.0, -20.0):
            etiqueta = max(probs, key=probs.get)
            esperado = voto_con_cabeceo(etiqueta, probs[etiqueta], cabeceo, 0.5, 0.015, 2.0)
            assert voto_del_modelo(etiqueta, probs[etiqueta], probs, cabeceo, 0.5, 0.015, 2.0,
                                   False) == (esperado, probs[etiqueta])


def test_sin_probabilidades_vota_neutral_como_antes():
    assert voto_del_modelo("enojado", 0.52, None, -12.0, 0.5, 0.015, 2.0, True) == ("neutral", 0.52)


class _Secuencia:
    """Backend que repite una lista de distribuciones."""

    nombre = "secuencia"
    relativo = False

    def __init__(self, lista):
        self._lista = [Distribucion(max(p, key=p.get), max(p.values()), p, max(p, key=p.get))
                       for p in lista]
        self._i = 0

    def distribucion(self, rostro):
        d = self._lista[self._i % len(self._lista)]
        self._i += 1
        return d


def _clasificador(lista, **kw):
    return EmotionClassifier(_Secuencia(lista), detector=lambda f: (10, 20, 60, 60),
                             frame_espejado=False, emotion_hz=1000.0, enojo_por_grado=0.015,
                             enojo_desde=2.0, **kw)


def _expresiones(c, n=30, cabeceo=-12.0):
    salida = []
    for k in range(n):
        c.process(FRAME, 0.01 * k, cabeceo=cabeceo)
        salida.append(c.emocion.etiqueta)
    return salida


def test_la_sonrisa_con_la_cabeza_baja_sale_feliz():
    """Dos de cada tres caras dicen enojo por poco, como en p10: sin la regla la
    sonrisa nunca reúne mayoría; con ella, sí."""
    lista = [SONRISA_ABAJO, SONRISA_ABAJO, SONRISA_CLARA]
    assert "feliz" not in _expresiones(_clasificador(lista))
    con = _expresiones(_clasificador(lista, segunda_tras_enojo=True))
    assert con[-1] == "feliz" and "enojado" not in con


def test_el_neutral_con_la_cabeza_baja_sigue_en_neutral():
    con = _expresiones(_clasificador([NEUTRAL_ABAJO], segunda_tras_enojo=True))
    assert set(con) == {"neutral"}


def test_un_empate_debil_no_basta_para_poner_feliz():
    """Solo votos de segunda (0.46): la mayoría la tienen, pero la confianza
    media no llega a ``min_confidence``, así que el robot no cambia."""
    con = _expresiones(_clasificador([SONRISA_ABAJO], segunda_tras_enojo=True))
    assert set(con) == {"neutral"}


def test_la_configuracion_de_la_segunda_opcion():
    cfg = AppConfig.from_env({})
    assert cfg.emotion.segunda_tras_enojo is True
    apagada = AppConfig.from_env({"TELMEOW_EMOTION_SEGUNDA_TRAS_ENOJO": "false"})
    assert apagada.emotion.segunda_tras_enojo is False
    assert EmotionClassifier.desde_config(cfg, backend=_Secuencia([SONRISA_ABAJO])).segunda_tras_enojo
    assert not EmotionClassifier.desde_config(
        apagada, backend=_Secuencia([SONRISA_ABAJO])).segunda_tras_enojo


def test_el_csv_lleva_la_regla_y_la_reproduccion_vota_igual():
    import analizar_registro_emocion as are

    assert COLUMNAS_CSV[-1] == "segunda_tras_enojo"
    for segunda, vota in ((True, ("feliz", 0.46)), (False, ("neutral", 0.52))):
        c = _clasificador([SONRISA_ABAJO], segunda_tras_enojo=segunda)
        c.process(FRAME, 0.0, cabeceo=-12.0)
        fila = dict(zip(COLUMNAS_CSV, _fila_csv(0.0, 9, c, 100.0)))
        assert fila["segunda_tras_enojo"] == int(segunda)
        # Como la lee el analizador: los números de la fila, las p_* en texto.
        leida = dict(fila, rostro=1, confianza_pred=float(fila["confianza_pred"]),
                     cabeceo=float(fila["cabeceo"]), min_confidence=0.5,
                     enojo_por_grado=0.015, enojo_desde=2.0, manos=0, calibrando=0,
                     mirada_fuera=0)
        assert are.voto(leida) == (vota[0], pytest.approx(vota[1]))


def test_una_grabacion_anterior_se_reproduce_como_se_grabo(tmp_path):
    """Sin la columna, el enojo descartado vota neutral, como entonces."""
    import csv

    import analizar_registro_emocion as are

    c = _clasificador([SONRISA_ABAJO])
    c.process(FRAME, 0.0, cabeceo=-12.0)
    ruta = tmp_path / "vieja_emocion.csv"
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        escritor = csv.writer(f)
        escritor.writerow(COLUMNAS_CSV[:-1])
        escritor.writerow(_fila_csv(0.0, 9, c, 100.0)[:-1])
    (fila,) = are.cargar(ruta)
    assert fila["segunda_tras_enojo"] == 0
    assert are.voto(fila) == ("neutral", pytest.approx(0.52))


# --------------------------------------------------------------------------
# La simulación con que se decidió
# --------------------------------------------------------------------------


def test_el_criterio_de_la_simulacion():
    import simular_segunda_opcion as sso

    sin = {"feliz leyendo": 25.0, "neutral leyendo": 95.0}
    assert sso.criterio({"p10a": (sin, {"feliz leyendo": 81.0, "neutral leyendo": 94.0})}).cumple
    poco = sso.criterio({"p10a": (sin, {"feliz leyendo": 40.0, "neutral leyendo": 95.0})})
    assert not poco.cumple and poco.subida_feliz == pytest.approx(15.0)
    rompe = sso.criterio({"p10a": (sin, {"feliz leyendo": 81.0, "neutral leyendo": 80.0})})
    assert not rompe.cumple and rompe.caidas == (("p10a", "neutral leyendo", 15.0),)
    # Sin la sesión de la sonrisa no se puede decidir.
    assert not sso.criterio({"p78": ({"neutral abajo": 100.0}, {"neutral abajo": 100.0})}).cumple


def test_la_regla_cumple_sobre_las_sesiones_grabadas():
    """Las cifras del plan (2026-11-10): la sonrisa leyendo de p10 pasa del 25 al
    81 % y ningún tramo de p10, p78, p77 ni p7_final cae más de 5 puntos (el
    enojo mirando abajo de p78 sube de 55 a 85 %)."""
    import simular_segunda_opcion as sso

    bases = [b for b in sso.TRAMOS if (RAIZ / f"{b}_emocion.csv").exists()
             and (RAIZ / f"{b}_cabeza.csv").exists()]
    if "p10a" not in bases:
        pytest.skip("falta p10a_emocion.csv")
    tablas = {}
    for base in bases:
        filas = sso.preparar(base)
        tablas[base] = (sso.resumir(base, filas, False), sso.resumir(base, filas, True))
    v = sso.criterio(tablas)
    assert v.cumple, v
    sin, con = tablas["p10a"]
    assert sin["feliz leyendo"] < 30.0 and con["feliz leyendo"] > 75.0
    if "p78" in tablas:
        assert tablas["p78"][1]["enojado abajo"] > tablas["p78"][0]["enojado abajo"] + 20.0
