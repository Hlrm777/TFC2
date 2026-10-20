"""Pruebas de ``tools/analizar_registro_emocion.py`` con registros sintéticos.

Las que importan son las de la reproducción exacta: el CSV lo escribe el
clasificador DE VERDAD —``EmotionClassifier`` con la ``_fila_csv`` del demo, y
en otra el demo entero con cámara y teclas simuladas—, y la herramienta tiene
que sacar de él, por su cuenta, la misma expresión que mandó el clasificador
fila a fila. Van con otra que comprueba que la comparación DETECTA una
reproducción que no es la del clasificador, y con otra que distingue ese fallo
del redondeo de la confianza en el CSV.
"""

from __future__ import annotations

import csv
import random
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import analizar_registro_emocion as are  # noqa: E402

from telmeow_teleop.config import EMOTION_LABELS, VIEWER_EMOTIONS  # noqa: E402
from telmeow_teleop.emotion_classifier import (  # noqa: E402
    _COLUMNAS_CSV,
    BackendNoDisponible,
    Distribucion,
    EmotionBackend,
    EmotionClassifier,
    _fila_csv,
)

HZ = 5.0
RECTANGULO = (10, 20, 30, 40)
FRAME = np.zeros((60, 80, 3), dtype=np.uint8)

#: Inferencias de cada etiqueta a 5 Hz: los tiempos del protocolo del P6.1.
DURACIONES = {0: 50, 1: 50, 2: 50, 3: 50, 4: 50, 5: 300, 6: 50}


class _Guion(EmotionBackend):
    """Responde lo que diga ``fuente()``: ``(clase, confianza)``, con la clase a
    ``None`` para una sin equivalente."""

    colapso = "cercana"

    def __init__(self, nombre, fuente):
        self.nombre = nombre
        self._fuente = fuente

    def distribucion(self, rostro_bgr):
        etiqueta, confianza = self._fuente()
        probabilidades = dict.fromkeys(EMOTION_LABELS, 0.0)
        if etiqueta is not None:
            probabilidades[etiqueta] = confianza
        return Distribucion(etiqueta, confianza, probabilidades, etiqueta or "fear")


def _grabar(ruta, pasos, *, n_frames=5, min_confidence=0.6):
    """Graba como el demo: el clasificador de verdad y su ``_fila_csv``, una
    fila por inferencia a 5 Hz.

    Cada paso lleva ``etiqueta`` y ``pred``: ``(clase, confianza)``, ``(None,
    confianza)`` para una clase sin equivalente, o ``None`` sin rostro. Y,
    opcionales, aplicados ANTES de la inferencia como las teclas del demo:
    ``backend`` (cambiar a ese), ``manual`` (``toggle_manual``; vacío es la
    tecla 0) y ``brillo``.
    """
    actual = {}
    backends = {n: _Guion(n, lambda: actual["pred"]) for n in ("keras", "deepface")}
    clasificador = EmotionClassifier(
        backends["keras"], emotion_hz=HZ, n_frames=n_frames, min_confidence=min_confidence,
        frame_espejado=False,
        detector=lambda frame: None if actual["pred"] is None else RECTANGULO,
    )
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        escritor = csv.writer(f)
        escritor.writerow(_COLUMNAS_CSV)
        for i, paso in enumerate(pasos):
            if "backend" in paso:
                clasificador.cambiar_backend(backends[paso["backend"]])
            if paso.get("manual"):
                clasificador.toggle_manual(paso["manual"])
            elif "manual" in paso:
                clasificador.clear_manual()
            actual["pred"] = paso["pred"]
            t = i / HZ
            clasificador.process(FRAME, timestamp=t)
            assert clasificador.inferencias == i + 1
            escritor.writerow(_fila_csv(t, paso["etiqueta"], clasificador, paso.get("brillo", 100.0)))
    return ruta


def _protocolo_ruidoso(semilla):
    """Un P6.1 con todo lo que complica reproducirlo: caras perdidas, clases sin
    equivalente, un modelo que duda, teclas manuales y dos cambios de backend.

    Las confianzas van redondeadas a 4 decimales, como las guarda el CSV: así
    ninguna decisión queda al filo del umbral y la reproducción TIENE que ser
    exacta.
    """
    azar = random.Random(semilla)
    pasos = []
    for etiqueta, duracion in DURACIONES.items():
        esperada = are.ESPERADAS[etiqueta]
        for _ in range(duracion):
            r = azar.random()
            if r < 0.08:
                pred = None
            elif r < 0.12:
                pred = (None, round(azar.uniform(0.3, 0.9), 4))
            elif esperada in EMOTION_LABELS and azar.random() < 0.6:
                pred = (esperada, round(azar.uniform(0.7, 1.0), 4))
            else:
                pred = (azar.choice(EMOTION_LABELS), round(azar.uniform(0.35, 0.9), 4))
            pasos.append({"etiqueta": etiqueta, "pred": pred, "brillo": round(azar.uniform(60, 140), 1)})
    inicio = {e: sum(list(DURACIONES.values())[:e]) for e in DURACIONES}
    pasos[inicio[2] + 20]["manual"] = "feliz"           # una tecla de más, y la 0
    pasos[inicio[2] + 30]["manual"] = ""
    pasos[inicio[3] + 25]["backend"] = "deepface"
    pasos[inicio[5] + 150]["backend"] = "keras"
    pasos[inicio[6] + 10]["manual"] = "concentrado"     # el protocolo: 6 y otra vez 6
    pasos[inicio[6] + 35]["manual"] = "concentrado"
    return pasos


def _reescribir(ruta, **columnas):
    with open(ruta, newline="", encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    for fila in filas:
        fila.update(columnas)
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        escritor = csv.DictWriter(f, fieldnames=_COLUMNAS_CSV)
        escritor.writeheader()
        escritor.writerows(filas)


def _escribir(ruta, filas):
    """Un CSV escrito a mano, con las columnas del clasificador."""
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        escritor = csv.DictWriter(f, fieldnames=_COLUMNAS_CSV, restval="")
        escritor.writeheader()
        for fila in filas:
            escritor.writerow({"backend": "keras", "n_frames": 5, "min_confidence": "0.6",
                               "brillo": "100.0", **fila})
    return ruta


# --------------------------------------------------------------------------
# Atado al clasificador
# --------------------------------------------------------------------------


def test_lee_columnas_que_escribe_el_clasificador():
    assert set(are.COLUMNAS) <= set(_COLUMNAS_CSV)


def test_el_protocolo_pide_expresiones_que_el_robot_tiene():
    """0-4 las emite el modelo; la 6, concentrado, solo existe en manual."""
    for etiqueta in (0, *are.ENTRADAS):
        assert are.ESPERADAS[etiqueta] in EMOTION_LABELS
    assert are.ESPERADAS[are.LECTURA] is None
    assert are.ESPERADAS[are.MANUAL] in VIEWER_EMOTIONS
    assert are.ESPERADAS[are.MANUAL] not in EMOTION_LABELS


@pytest.mark.parametrize(
    "n_frames, min_confidence, semilla", [(5, 0.6, 1), (3, 0.5, 2), (7, 0.7, 3), (9, 0.8, 4)]
)
def test_con_los_parametros_grabados_reproduce_exactamente_al_clasificador(
    tmp_path, n_frames, min_confidence, semilla
):
    ruta = _grabar(tmp_path / "p61.csv", _protocolo_ruidoso(semilla),
                   n_frames=n_frames, min_confidence=min_confidence)
    filas = are.cargar(ruta)
    comparacion = are.comparar_con_grabado(filas)
    assert comparacion.exacta
    assert comparacion.comparables == sum(1 for f in filas if not f["manual"])
    # Y no por trivial: el robot cambió de expresión, y el registro tiene caras
    # perdidas, clases sin equivalente, manual y cambio de backend.
    assert are.contar_cambios([f["emocion"] for f in filas if not f["manual"]]) >= 4
    assert any(not f["rostro"] for f in filas)
    assert any(f["rostro"] and not f["prediccion"] for f in filas)
    assert {f["manual"] for f in filas} == {None, "feliz", "concentrado"}
    assert {f["backend"] for f in filas} == {"keras", "deepface"}


@pytest.mark.parametrize("columna, valor", [("n_frames", "9"), ("min_confidence", "0.75")])
def test_la_comparacion_detecta_una_reproduccion_que_no_es_la_del_clasificador(
    tmp_path, capsys, columna, valor
):
    """Con otros parámetros que los del clasificador la comprobación tiene que
    saltar, y sin achacarlo al redondeo."""
    ruta = _grabar(tmp_path / "p61.csv", _protocolo_ruidoso(1))
    _reescribir(ruta, **{columna: valor})
    comparacion = are.comparar_con_grabado(are.cargar(ruta))
    assert not comparacion.exacta
    assert not comparacion.explicada
    assert are.main([str(ruta)]) == 1
    salida = capsys.readouterr().out
    assert "NO COINCIDE" in salida and "SIN EXPLICAR" in salida


def test_una_discrepancia_al_filo_del_umbral_se_achaca_al_redondeo(tmp_path, capsys):
    """El CSV guarda la confianza con 4 decimales. Un voto de 0.59996 no llega
    al umbral 0.6 en vivo, pero en el CSV pone 0.6000, que sí llega: la
    reproducción difiere en esa fila, y la herramienta tiene que decir por qué
    en vez de dar el instrumento por roto."""
    pasos = [{"etiqueta": 0, "pred": ("feliz", 0.59996)}] + [{"etiqueta": 0, "pred": ("neutral", 0.9)}] * 3
    ruta = _grabar(tmp_path / "filo.csv", pasos, n_frames=1, min_confidence=0.6)
    filas = are.cargar(ruta)
    assert filas[0]["emocion"] == "neutral"            # en vivo no cambió...
    assert filas[0]["confianza_pred"] == 0.6           # ...y el CSV dice 0.6000
    comparacion = are.comparar_con_grabado(filas)
    assert comparacion.distintas == (0,)
    assert comparacion.tramos == ((0, True),)
    assert comparacion.explicada
    assert are.main([str(ruta)]) == 0
    assert "al filo del umbral" in capsys.readouterr().out


def test_reproduce_lo_que_grabo_el_demo_entero(monkeypatch, tmp_path, capsys):
    """El demo de verdad, con cámara, teclas y backends simulados: la fila se
    escribe después de inferir, las teclas cambian de etiqueta, fuerzan
    expresiones y cambian de backend. Lo que grabe, la herramienta lo
    reproduce."""
    import cv2

    import telmeow_teleop.camera as camara
    import telmeow_teleop.emotion_classifier as ec

    total = 70
    azar = random.Random(11)
    guion = []
    for _ in range(total):
        r = azar.random()
        if r < 0.1:
            guion.append(None)
        elif r < 0.15:
            guion.append((None, round(azar.uniform(0.3, 0.9), 4)))
        else:
            guion.append((azar.choice(EMOTION_LABELS[:3]), round(azar.uniform(0.4, 1.0), 4)))
    pasos = iter(guion)
    actual = {}

    def detector(frame, **_):
        actual["pred"] = next(pasos)
        return None if actual["pred"] is None else RECTANGULO

    def fabrica(nombre, cfg=None):
        if nombre == "hsemotion":
            raise BackendNoDisponible(nombre, ImportError("simulado"))
        return _Guion(nombre, lambda: actual["pred"])

    class _Cap:
        def __init__(self):
            self.n = 0

        def read(self):
            self.n += 1
            return self.n <= total, np.full((120, 160, 3), 90, np.uint8)

        def get(self, _propiedad):
            return 160.0

        def release(self):
            pass

    teclas = [255] * total
    for frame, tecla in {9: " ", 19: " ", 24: "b", 29: " ", 39: " ", 44: "6", 49: "6",
                         52: "2", 55: "0", 57: " ", total - 1: "q"}.items():
        teclas[frame] = ord(tecla)
    teclas = iter(teclas)

    monkeypatch.setattr(camara, "abrir_camara", lambda cfg: _Cap())
    monkeypatch.setattr(ec, "crear_backend", fabrica)
    monkeypatch.setattr(ec, "detectar_rostro", detector)
    monkeypatch.setattr(cv2, "imshow", lambda *a: None)
    monkeypatch.setattr(cv2, "waitKey", lambda *a: next(teclas))
    monkeypatch.setattr(cv2, "destroyAllWindows", lambda: None)
    # Una inferencia por frame, sea cual sea el reloj: así cada tecla cae en
    # una fila conocida.
    monkeypatch.setenv("TELMEOW_EMOTION_HZ", "1e9")
    monkeypatch.setenv("TELMEOW_EMOTION_N_FRAMES", "3")
    monkeypatch.setenv("TELMEOW_EMOTION_MIN_CONFIDENCE", "0.5")
    registro = tmp_path / "emocion.csv"

    assert ec._demo(["--record", str(registro)]) == 0
    filas = are.cargar(registro)
    assert len(filas) == total
    assert are.parametros_grabados(filas) == (3, 0.5)
    assert [f["etiqueta"] for f in filas][::10] == [0, 1, 2, 3, 4, 4, 5]
    comparacion = are.comparar_con_grabado(filas)
    assert comparacion.exacta
    assert comparacion.comparables == sum(1 for f in filas if not f["manual"])
    # Lo que tenía que pasar, pasó:
    assert [f["backend"] for f in filas][24:26] == ["keras", "deepface"]
    assert {f["manual"] for f in filas} == {None, "concentrado", "feliz"}   # 2 = feliz
    assert any(not f["rostro"] for f in filas)
    assert any(f["rostro"] and not f["prediccion"] for f in filas)
    assert are.contar_cambios([f["emocion"] for f in filas if not f["manual"]]) >= 3

    capsys.readouterr()
    assert are.main([str(registro)]) == 0
    assert "EXACTA" in capsys.readouterr().out


# --------------------------------------------------------------------------
# Por etiqueta
# --------------------------------------------------------------------------


def test_resumen_de_una_etiqueta(tmp_path):
    """10 inferencias a 5 Hz: el robot tarda 3 en poner feliz, dos sin cara y
    una cuyo modelo dudó por una clase sin equivalente."""
    filas = []
    for i in range(10):
        sin_cara = i in (4, 7)
        filas.append({
            "t": f"{0.2 * i:.4f}", "etiqueta": 1, "rostro": 0 if sin_cara else 1,
            "prediccion": "" if sin_cara or i == 9 else ("neutral" if i == 0 else "feliz"),
            "confianza_pred": "" if sin_cara else "0.9000",
            "emocion": "neutral" if i < 3 else "feliz",
            "brillo": "50.0" if i % 2 else "150.0",
        })
    r = are.resumir_etiqueta(1, are.cargar(_escribir(tmp_path / "r.csv", filas)))
    assert r.esperada == "feliz" and r.ok is True
    assert r.enviadas[0] == ("feliz", pytest.approx(0.7))
    assert r.crudas == [("feliz", pytest.approx(6 / 8)), ("neutral", pytest.approx(1 / 8)),
                        (are.SIN_EQUIVALENTE, pytest.approx(1 / 8))]
    assert r.sin_rostro == pytest.approx(0.2)
    assert r.brillo == pytest.approx(100.0)
    assert r.cambios == 1
    assert r.duracion == pytest.approx(1.8)
    assert r.por_minuto == pytest.approx(60.0 / 1.8)
    assert r.manual == 0.0 and r.al_soltar is None


def test_una_etiqueta_con_la_expresion_equivocada_no_pasa(tmp_path):
    filas = [{"t": f"{0.2 * i:.4f}", "etiqueta": 2, "rostro": 1, "prediccion": "feliz",
              "confianza_pred": "0.9", "emocion": "feliz" if i > 3 else "triste"}
             for i in range(10)]
    r = are.resumir_etiqueta(2, are.cargar(_escribir(tmp_path / "r.csv", filas)))
    assert r.ok is False
    assert r.enviadas[0][0] == "feliz"


def test_la_lectura_no_se_juzga_por_la_expresion(tmp_path):
    filas = [{"t": f"{0.2 * i:.4f}", "etiqueta": 5, "rostro": 1, "prediccion": "feliz",
              "confianza_pred": "0.9", "emocion": "feliz"} for i in range(5)]
    assert are.resumir_etiqueta(5, are.cargar(_escribir(tmp_path / "r.csv", filas))).ok is None


@pytest.mark.parametrize("suelta, ok", [(True, True), (False, False)])
def test_la_etiqueta_manual_pide_concentrado_y_soltarlo(tmp_path, suelta, ok):
    """3 inferencias en automático, 5 en concentrado y, si se suelta, 4 con la
    cara: triste."""
    emociones = ["feliz"] * 3 + ["concentrado"] * 5 + (["triste"] * 4 if suelta else [])
    filas = [{"t": f"{0.2 * i:.4f}", "etiqueta": 6, "rostro": 1, "prediccion": "triste",
              "confianza_pred": "0.9", "emocion": e,
              "manual": "concentrado" if e == "concentrado" else ""}
             for i, e in enumerate(emociones)]
    r = are.resumir_etiqueta(6, are.cargar(_escribir(tmp_path / "r.csv", filas)))
    assert r.ok is ok
    assert r.manual == pytest.approx(5 / len(emociones))
    assert r.al_soltar == ("triste" if suelta else None)


# --------------------------------------------------------------------------
# La rejilla
# --------------------------------------------------------------------------


def _sostenidas(por_etiqueta: int, etiquetas=(0, 1, 2, 3, 4), confianza=0.9):
    return [{"etiqueta": e, "pred": (are.ESPERADAS[e], confianza)}
            for e in etiquetas for _ in range(por_etiqueta)]


def test_la_latencia_de_entrada_es_media_ventana(tmp_path):
    """Con la cara sostenida y la ventana llena de la anterior, cambiar exige
    n_frames // 2 + 1 votos; el primero cae en t=0, así que la latencia es
    n_frames // 2 inferencias de 0.2 s. Es la cifra del plan: 0.4 s con 5."""
    filas = are.cargar(_grabar(tmp_path / "r.csv", _sostenidas(10)))
    for par in are.barrido(filas, (3, 5, 7, 9), (0.5, 0.8)):
        esperada = 0.2 * (par.n_frames // 2)
        assert set(par.latencias) == set(are.ENTRADAS)
        for inicio, desde_voto in par.latencias.values():
            assert inicio == pytest.approx(esperada)
            assert desde_voto == pytest.approx(esperada)
        assert par.no_llegan == 0
        assert par.cambios_lectura is None             # no hay etiqueta 5


def test_la_latencia_desde_el_voto_descuenta_lo_que_tarda_el_operador(tmp_path):
    """Tres inferencias más con la cara de antes al empezar la etiqueta 1: la
    latencia desde el inicio crece 0.6 s, la de la histéresis no."""
    pasos = (_sostenidas(10, (0,)) + [{"etiqueta": 1, "pred": ("neutral", 0.9)}] * 3
             + _sostenidas(10, (1,)))
    filas = are.cargar(_grabar(tmp_path / "r.csv", pasos))
    (par,) = are.barrido(filas, (5,), (0.6,))
    assert par.latencias[1] == (pytest.approx(0.6 + 0.4), pytest.approx(0.4))


def test_una_etiqueta_demasiado_corta_no_llega(tmp_path):
    """Dos votos: bastan con n_frames=3, no con 5."""
    pasos = _sostenidas(10, (0,)) + _sostenidas(2, (1,))
    filas = are.cargar(_grabar(tmp_path / "r.csv", pasos))
    corta, larga = are.barrido(filas, (3, 5), (0.6,))
    assert corta.no_llegan == 0
    assert larga.latencias[1] == (None, None) and larga.no_llegan == 1
    assert larga.media_inicio is None


def test_la_confianza_minima_frena_la_alternancia_que_la_mayoria_no_frena(tmp_path):
    """Leyendo, un modelo que alterna feliz/triste con 0.55: con 0.5 de umbral
    el robot cambia en cada inferencia desde la tercera; con 0.6 o más, nunca
    (filters.ClassHysteresis, «Límite conocido»)."""
    lectura = [{"etiqueta": 5, "pred": ("feliz" if i % 2 == 0 else "triste", 0.55)}
               for i in range(50)]
    filas = are.cargar(_grabar(tmp_path / "r.csv", _sostenidas(10, (0,)) + lectura))
    pares = {(p.n_frames, p.min_confidence): p for p in are.barrido(filas)}
    assert pares[(3, 0.5)].cambios_lectura == pytest.approx(48 * 60.0 / (49 * 0.2))
    for n in are.REJILLA_N_FRAMES:
        assert pares[(n, 0.5)].cambios_lectura > 0
        for c in (0.6, 0.7, 0.8):
            assert pares[(n, c)].cambios_lectura == 0.0


def test_el_par_grabado_se_anade_si_no_esta_en_la_rejilla(tmp_path):
    filas = are.cargar(_grabar(tmp_path / "r.csv", _sostenidas(10), n_frames=4,
                               min_confidence=0.65))
    grabado = are.parametros_grabados(filas)
    pares = are.barrido(filas, grabado=grabado)
    assert len(pares) == 17
    assert (pares[-1].n_frames, pares[-1].min_confidence) == (4, 0.65)
    assert len(are.barrido(filas, grabado=(5, 0.6))) == 16


# --------------------------------------------------------------------------
# El informe y los registros que no son
# --------------------------------------------------------------------------


def test_el_informe_entero(tmp_path, capsys):
    ruta = _grabar(tmp_path / "p61.csv", _protocolo_ruidoso(1))
    assert are.main([str(ruta)]) == 0
    salida = capsys.readouterr().out
    salida.encode("ascii")                               # los print, en ASCII
    for etiqueta in DURACIONES:
        assert f"etiqueta {etiqueta}" in salida
    assert "EXACTA" in salida
    assert salida.count("<- grabado") == 1
    assert "REJILLA" in salida


def test_no_es_un_registro_de_emocion(tmp_path, capsys):
    ruta = tmp_path / "cabeza.csv"
    ruta.write_text("t,etiqueta,raw_pan,raw_tilt\n0.0,0,1.0,2.0\n", encoding="utf-8")
    assert are.main([str(ruta)]) == 1
    assert "no tiene las columnas" in capsys.readouterr().out


def test_dos_grabaciones_pegadas_se_rechazan(tmp_path):
    filas = [{"t": "0.0", "etiqueta": 0, "rostro": 0, "emocion": "neutral", "n_frames": 5},
             {"t": "0.2", "etiqueta": 0, "rostro": 0, "emocion": "neutral", "n_frames": 3}]
    with pytest.raises(ValueError, match="mas de un par"):
        are.parametros_grabados(are.cargar(_escribir(tmp_path / "r.csv", filas)))


def test_un_archivo_que_no_existe(tmp_path, capsys):
    assert are.main([str(tmp_path / "no.csv")]) == 1
    assert "No existe" in capsys.readouterr().out
