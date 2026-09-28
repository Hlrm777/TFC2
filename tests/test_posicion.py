"""Pruebas del mapeo por posición (P5.4).

La regla la enunció el operador el 2026-09-27, con el visor y un dibujo
interactivo delante, en dos correcciones sucesivas. Estas pruebas la fijan tal
como la enunció, no como la calcula el código:

1. Con el brazo estirado el robot hace lo mismo que con ``direccion``.
2. Recoger la mano con la muñeca por fuera del tronco hace retroceder el brazo
   del robot hacia la cruz: «lo que el codo esconde, se despliega al costado».
3. Moverse de lado con el codo doblado también mueve al robot (primera
   corrección: en el primer borrador lo lateral era solo lo que sobraba).
4. Con la muñeca por DENTRO del tronco —cruzando por delante del cuerpo— el
   brazo se cierra; abrirlo sería el gesto contrario (segunda corrección).
5. Y se cierra POCO A POCO: llega a su tope interno con la muñeca en la mitad
   del cuerpo, sin esperar a que lo pare el tope ni el visor (tercera
   corrección: «una función con factor de conversión, para que llegue lenta al
   máximo del robot»).
6. Con la mano a medio camino del costado el robot ya llega a la cruz: la
   cámara no deja estirar los brazos en cruz.

Y la regla de la casa desde el H35: nada de costuras. Ninguna trayectoria
continua de la mano produce un salto del brazo.
"""

from __future__ import annotations

import math
import random
from types import SimpleNamespace

import numpy as np
import pytest

from telmeow_teleop.arm_tracker import (
    LANDMARKS,
    MAPEOS,
    PUNTOS_MANO,
    ArmAngles,
    ArmTracker,
    direccion_brazo,
    torso_basis,
    vector_brazo,
)
from telmeow_teleop.config import AppConfig
from telmeow_teleop.limits import Side
from telmeow_teleop.retarget import (
    BANDA_TRONCO,
    direccion,
    direccion_por_posicion,
    polares,
)
from test_arm_tracker import (
    HOMBRO_DER,
    HOMBRO_IZQ,
    SUPERIOR,
    LandmarkFalso,
    mundo_falso,
)

#: Hacia la cámara en ``pose_world_landmarks``: la z crece hacia el fondo.
ADELANTE = np.array([0.0, 0.0, -1.0])


def _psi(d) -> float:
    """Ángulo con la cruz (el lateral), en grados."""
    return polares(d)[0]


def _angulo(u, v) -> float:
    u = np.asarray(u, dtype=float) / np.linalg.norm(u)
    v = np.asarray(v, dtype=float) / np.linalg.norm(v)
    return math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(u, v))))))


class _SinPose:
    def process(self, _frame):
        return None

    def close(self):
        pass


def _tracker(**kwargs):
    reloj = {"t": 0.0}
    tracker = ArmTracker(pose_estimator=_SinPose(), clock=lambda: reloj["t"], **kwargs)
    return tracker, reloj


# --------------------------------------------------------------------------
# 1. La regla, tal como la enunció el operador
# --------------------------------------------------------------------------


@pytest.mark.parametrize("semilla", range(5))
def test_con_el_brazo_estirado_es_la_direccion_de_hoy(semilla):
    """Brazo estirado hacia fuera del tronco y por delante del hombro:
    ``|q| = 1``, nada que desplegar, y la dirección es exactamente la de la
    mano. (Por detrás del plano del hombro la regla proyecta al plano frontal,
    que es lo que el retargeting hacía de todos modos: ``al_frente``.)"""
    rnd = random.Random(semilla)
    for _ in range(200):
        v = [rnd.gauss(0.0, 1.0) for _ in range(3)]
        v[0] = abs(v[0])
        v[2] = abs(v[2])
        n = math.sqrt(sum(c * c for c in v))
        q = tuple(c / n for c in v)
        assert direccion_por_posicion(q) == pytest.approx(q, abs=1e-12)


@pytest.mark.parametrize("lado", [Side.L, Side.R])
def test_estirado_y_cruzado_el_robot_acaba_donde_con_direccion(lado):
    """Brazo estirado cruzando por delante: el pliegue lo cuenta como «al
    frente» y el robot acaba en su tope interno, igual que con 'direccion'
    —que lo satura—. No en el mismo punto: lo cruzado suma al frente, el
    azimut cambia algo, y el tope depende del azimut."""
    from telmeow_teleop.retarget import RetargetBrazo, alcance_robot

    for q in [(-0.5, 0.0, 0.866), (-0.3, 0.2, 0.93), (-0.6, -0.3, 0.74)]:
        n = math.sqrt(sum(c * c for c in q))
        q = tuple(c / n for c in q)
        for paso in (
            RetargetBrazo(lado)(direccion_por_posicion(q)),
            RetargetBrazo(lado)(q),
        ):
            margen = 2.0
            assert paso.psi_robot == pytest.approx(
                alcance_robot(paso.alfa, lado) - margen, abs=1e-6
            )


def test_recoger_la_mano_al_frente_la_lleva_hacia_la_cruz():
    """El caso que motivó todo (H38): la mano se recoge por la misma línea.
    Con ``direccion`` el robot no se movería; aquí retrocede hasta la cruz."""
    anterior = None
    for t in np.linspace(1.0, 0.05, 40):
        psi = _psi(direccion_por_posicion((0.0, 0.0, t)))
        if anterior is not None:
            assert psi < anterior
        anterior = psi
    assert _psi(direccion_por_posicion((0.0, 0.0, 1.0))) == pytest.approx(90.0)
    assert anterior < 5.0


def test_recoger_la_mano_conserva_si_estaba_arriba_o_abajo():
    arriba = direccion_por_posicion((0.1, 0.3, 0.2))
    abajo = direccion_por_posicion((0.1, -0.3, 0.2))
    assert arriba[1] > 0.1
    assert abajo[1] < -0.1


def test_moverse_de_lado_con_el_codo_doblado_tambien_mueve_al_robot():
    """Primera corrección del operador: lo lateral no puede perderse."""
    laterales = [
        direccion_por_posicion((l, 0.1, 0.4))[0] for l in np.linspace(0.0, 0.3, 31)
    ]
    assert all(b > a for a, b in zip(laterales, laterales[1:]))
    barrido = _angulo(
        direccion_por_posicion((0.0, 0.1, 0.4)),
        direccion_por_posicion((0.3, 0.1, 0.4)),
    )
    assert barrido > 5.0


def test_cruzar_hacia_dentro_cierra_el_brazo_en_vez_de_abrirlo():
    """Segunda corrección del operador, dibujada sobre el propio widget: al
    pasar la muñeca por delante del tronco el robot abría el brazo."""
    anterior = None
    for l in np.linspace(0.0, -0.5, 60):
        psi = _psi(direccion_por_posicion((l, 0.0, 0.3)))
        if anterior is not None:
            assert psi >= anterior - 1e-9
        anterior = psi
    # Con el pliegue la regla nunca pasa del frente: el tope interno lo pone
    # después el retargeting.
    assert anterior == pytest.approx(90.0)


@pytest.mark.parametrize(
    "q", [(-BANDA_TRONCO, 0.1, 0.3), (-0.35, -0.2, 0.25), (-0.5, 0.0, 0.1)]
)
def test_pasada_la_mitad_del_cuerpo_se_sigue_a_la_mano_plegada(q):
    """Muñeca más allá de la mitad del cuerpo: nada que desplegar, y lo que
    cruza cuenta como profundidad."""
    L, S, A = q
    plegada = (0.0, S, math.hypot(A, L))
    n = math.sqrt(sum(c * c for c in plegada))
    assert direccion_por_posicion(q) == pytest.approx(
        tuple(c / n for c in plegada), abs=1e-12
    )


@pytest.mark.parametrize("lado", [Side.L, Side.R])
def test_cruzar_hacia_dentro_llega_al_tope_poco_a_poco(lado):
    """La tercera corrección, con el robot real detrás: con la mano recogida
    —como en las capturas que marcó el operador—, el brazo se cierra de forma
    monótona desde la línea del hombro, llega a su tope interno con la muñeca en
    la mitad del cuerpo y no antes, y ningún centímetro de mano lo mueve de
    golpe. Sin el pliegue llegaba al tope con la muñeca a 7 cm."""
    from telmeow_teleop.retarget import RetargetBrazo

    tope = RetargetBrazo(lado)(direccion_por_posicion((-0.6, 0.0, 0.2))).psi_robot
    # La pendiente máxima de la regla es 90° · max(w') · (1 − |q|), con
    # max(w') = 1.5 / banda (el paso suave), y la escala del robot la estira como
    # mucho un 11 %: el límite por paso sale de ahí, no de un número a ojo. Con
    # la franja del operador (0.1) son unos 16° por cada 0.6 cm de mano.
    paso = 0.01
    limite = paso * 90.0 * (1.5 / BANDA_TRONCO) * 1.2
    anterior = None
    for L in np.arange(0.0, -0.4, -paso):         # pasos de 0.6 cm
        psi = RetargetBrazo(lado)(direccion_por_posicion((L, 0.0, 0.145))).psi_robot
        if anterior is not None:
            assert psi >= anterior - 1e-9
            assert psi - anterior < limite
        if L > -0.6 * BANDA_TRONCO:
            assert psi < tope - 5.0               # todavía no ha llegado
        anterior = psi
    assert anterior == pytest.approx(tope)


def test_con_la_mano_a_medio_camino_del_costado_ya_esta_en_cruz():
    assert direccion_por_posicion((0.5, 0.0, 0.0)) == pytest.approx((1.0, 0.0, 0.0))
    assert _psi(direccion_por_posicion((0.5, 0.0, 0.1))) < 12.0


def test_el_brazo_colgando_pide_el_reposo():
    assert direccion_por_posicion((0.0, -1.0, 0.0)) == pytest.approx((0.0, -1.0, 0.0))


def test_la_mano_en_el_hombro_da_la_cruz_sin_dividir_por_cero():
    assert direccion_por_posicion((0.0, 0.0, 0.0)) == (1.0, 0.0, 0.0)


# --------------------------------------------------------------------------
# 2. Sin costuras
# --------------------------------------------------------------------------


@pytest.mark.parametrize("A", [0.05, 0.1, 0.2, 0.3, 0.5])
@pytest.mark.parametrize("S", [-0.3, 0.0, 0.3])
def test_cruzar_el_borde_del_tronco_no_da_saltos(S, A):
    """Barrido a pasos de 0.001 del largo del brazo (0.6 mm) a través de toda
    la franja. Un corte seco en el borde daría aquí decenas de grados de golpe;
    la suma de la primera versión llegaba a 3.5° por paso pegada al pecho."""
    previo = None
    for l in np.arange(0.3, -0.6, -0.001):
        if l * l + S * S + A * A > 1.0:
            previo = None
            continue
        d = direccion_por_posicion((l, S, A))
        assert math.sqrt(sum(c * c for c in d)) == pytest.approx(1.0)
        if previo is not None:
            assert _angulo(previo, d) < 1.5
        previo = d


def test_un_paseo_aleatorio_de_la_mano_no_da_saltos():
    rnd = random.Random(11)
    q = np.array([0.2, 0.0, 0.5])
    previo = direccion_por_posicion(q)
    for _ in range(20000):
        paso = np.array([rnd.uniform(-1.0, 1.0) for _ in range(3)]) * 0.002
        nuevo = q + paso
        # Por delante del hombro y dentro del alcance: donde puede estar la mano.
        if not (0.05 <= nuevo[2] and np.linalg.norm(nuevo) <= 1.0):
            continue
        q = nuevo
        d = direccion_por_posicion(q)
        assert _angulo(previo, d) < 3.0
        previo = d


# --------------------------------------------------------------------------
# 3. Validación
# --------------------------------------------------------------------------


@pytest.mark.parametrize("banda", [0.0, -0.1])
def test_la_banda_tiene_que_ser_positiva(banda):
    with pytest.raises(ValueError):
        direccion_por_posicion((0.0, 0.0, 0.5), banda)
    with pytest.raises(ValueError):
        _tracker(banda_tronco=banda)


def test_una_posicion_no_finita_se_rechaza():
    with pytest.raises(ValueError):
        direccion_por_posicion((float("nan"), 0.0, 0.5))


def test_map_posicion_descarta_lo_no_finito():
    tracker, _ = _tracker()
    assert tracker.map_posicion(Side.L, (float("nan"), 0.0, 0.3), 0.6, 0.0) is None
    assert tracker.map_posicion(Side.L, (0.0, 0.0, 0.3), 0.0, 0.0) is None
    assert tracker.map_posicion(Side.L, (0.0, 0.3), 0.6, 0.0) is None


# --------------------------------------------------------------------------
# 4. Dentro del tracker, desde landmarks
# --------------------------------------------------------------------------


def test_normalizar_el_vector_da_la_direccion_de_siempre():
    """Ancla: ``vector_brazo`` no puede cambiar lo que ``direccion`` ya hacía."""
    rnd = random.Random(3)
    base = torso_basis(HOMBRO_IZQ, HOMBRO_DER, SUPERIOR)
    for _ in range(100):
        destino = HOMBRO_IZQ + np.array([rnd.uniform(-0.6, 0.6) for _ in range(3)])
        v = vector_brazo(HOMBRO_IZQ, destino, base, 9.9)
        h = direccion_brazo(HOMBRO_IZQ, destino, base, 9.9)
        n = math.sqrt(sum(c * c for c in v))
        assert n == pytest.approx(float(np.linalg.norm(destino - HOMBRO_IZQ)))
        assert tuple(c / n for c in v) == pytest.approx(h, abs=1e-12)


def _mundo_brazo(codo, muneca):
    """Brazo izquierdo de BlazePose con codo y mano; dedos sobre la muñeca."""
    mundo = mundo_falso(0.9)
    mundo[LANDMARKS[Side.L]["codo"]] = LandmarkFalso(*codo, visibility=1.0)
    for nombre in PUNTOS_MANO:
        mundo[LANDMARKS[Side.L][nombre]] = LandmarkFalso(*muneca, visibility=1.0)
    return mundo


def _tracker_con_mundo(mundo, **kwargs):
    resultado = SimpleNamespace(
        pose_world_landmarks=SimpleNamespace(landmark=mundo), pose_landmarks=None
    )
    estimador = SimpleNamespace(process=lambda _frame: resultado, close=lambda: None)
    # Sin espejo en la entrada: la etiqueta L de BlazePose manda sobre armL.
    return ArmTracker(
        pose_estimator=estimador, frame_espejado=False, clock=lambda: 0.0, **kwargs
    )


#: Brazo recogido al frente a la altura del hombro: la mano a 25 cm por delante
#: del hombro, con el codo abajo y afuera, segmentos de 30 cm.
_CODO_RECOGIDO = HOMBRO_IZQ + np.array([0.1, math.sqrt(0.3**2 - 0.1**2 - 0.125**2), -0.125])
_MANO_RECOGIDA = HOMBRO_IZQ + 0.25 * ADELANTE


def test_con_el_brazo_estirado_posicion_y_direccion_coinciden():
    mundo = _mundo_brazo(HOMBRO_IZQ + 0.3 * ADELANTE, HOMBRO_IZQ + 0.6 * ADELANTE)
    salidas = {}
    for mapeo in ("posicion", "direccion"):
        tracker = _tracker_con_mundo(mundo, mapeo=mapeo)
        pose = tracker.process(None)
        salidas[mapeo] = (pose.armL, pose.armLtw)
        assert tracker.extensiones[Side.L] == pytest.approx(1.0)
    assert salidas["posicion"] == pytest.approx(salidas["direccion"], abs=1e-9)


def test_recoger_la_mano_hace_retroceder_el_brazo_y_la_direccion_no_lo_ve():
    """El H38 de punta a punta, desde landmarks: la mano recogida apunta al
    mismo sitio que estirada, así que 'direccion' da lo mismo; 'posicion'
    retrocede en proporción a la extensión."""
    mundo = _mundo_brazo(_CODO_RECOGIDO, _MANO_RECOGIDA)
    psi = {}
    for mapeo in ("posicion", "direccion"):
        tracker = _tracker_con_mundo(mundo, mapeo=mapeo)
        tracker.process(None)
        psi[mapeo] = tracker.retargetings[Side.L].psi_humano
        assert tracker.extensiones[Side.L] == pytest.approx(0.25 / 0.6, abs=1e-3)
        assert tracker.vectores[Side.L] == pytest.approx((0.0, 0.0, 0.25), abs=1e-9)
        assert np.linalg.norm(tracker.vectores_codo[Side.L]) == pytest.approx(0.3)
    assert psi["direccion"] == pytest.approx(90.0, abs=1e-6)
    assert psi["posicion"] == pytest.approx(90.0 * 0.25 / 0.6, abs=0.1)


def test_con_destino_codo_la_extension_es_siempre_uno():
    """Con ``target="codo"`` el destino ES el codo: 'posicion' se reduce a
    'direccion', sin casos especiales."""
    tracker = _tracker_con_mundo(
        _mundo_brazo(_CODO_RECOGIDO, _MANO_RECOGIDA), target="codo"
    )
    tracker.process(None)
    assert tracker.extensiones[Side.L] == pytest.approx(1.0)


def test_el_comando_retrocede_al_recoger_la_mano_sin_saltos():
    """La mano recogida por la misma línea, a lo largo del tiempo y a través
    de ``comando`` —filtro, retargeting, límite de velocidad—: con 'posicion'
    la punta del robot se abre hacia la cruz; con 'direccion' no se mueve."""
    finales = {}
    for mapeo in ("posicion", "direccion"):
        tracker, reloj = _tracker(mapeo=mapeo)
        t = 0.0
        previo = None
        for i in range(120):
            t += 1 / 30
            reloj["t"] = t
            extension = 1.0 if i < 45 else max(0.3, 1.0 - (i - 45) / 45)
            if mapeo == "posicion":
                vector = (0.0, 0.0, 0.6 * extension)
                salida = tracker.map_posicion(Side.R, vector, 0.6, t)
            else:
                salida = tracker.map_direccion(Side.R, (0.0, 0.0, 1.0), t)
            mando = tracker.comando(ArmAngles(armR=salida[0], armRtw=salida[1]), t)
            actual = np.array([mando.armR, mando.armRtw])
            if previo is not None:
                assert np.max(np.abs(actual - previo)) < 6.5
            previo = actual
        finales[mapeo] = direccion(*previo)
    assert finales["direccion"][0] < 0.5                # sigue al frente
    assert finales["posicion"][0] > 0.8                 # casi en cruz
    assert finales["posicion"][0] > finales["direccion"][0] + 0.3


# --------------------------------------------------------------------------
# 5. Configuración y cambio de mapeo
# --------------------------------------------------------------------------


def test_posicion_es_el_mapeo_por_defecto_y_k_recorre_los_tres():
    tracker, _ = _tracker()
    assert tracker.mapeo == "posicion"
    assert MAPEOS == ("posicion", "direccion", "articular")
    assert AppConfig.from_env({}).arm.mapeo == "posicion"


def test_recorrer_los_tres_mapeos_en_caliente_no_salta():
    tracker, reloj = _tracker()
    t = 0.0
    for _ in range(60):
        t += 1 / 30
        reloj["t"] = t
        salida = tracker.map_posicion(Side.L, (0.1, -0.2, 0.3), 0.6, t)
        mando = tracker.comando(ArmAngles(armL=salida[0], armLtw=salida[1]), t)
    antes = np.array([mando.armL, mando.armLtw])
    for mapeo in ("direccion", "articular", "posicion"):
        tracker.mapeo = mapeo
        t += 1 / 30
        reloj["t"] = t
        despues = tracker.comando(ArmAngles(), t)       # dentro de la gracia
        actual = np.array([despues.armL, despues.armLtw])
        assert np.max(np.abs(actual - antes)) <= 6.0 + 1e-6
        antes = actual


def test_la_configuracion_lee_la_banda_del_tronco():
    cfg = AppConfig.from_env(
        {"TELMEOW_ARM_BANDA_TRONCO": "0.3", "TELMEOW_ARM_MAPEO": "direccion"}
    )
    assert cfg.arm.banda_tronco == 0.3
    assert cfg.arm.mapeo == "direccion"
    assert AppConfig.from_env({}).arm.banda_tronco == BANDA_TRONCO


# --------------------------------------------------------------------------
# 6. La herramienta de comparación reproduce el mapeo nuevo desde un CSV
# --------------------------------------------------------------------------


def test_comparar_mapeo_reproduce_posicion_y_mide_la_profundidad(tmp_path):
    """Una sesión sintética con las columnas del P5.4: la mano va y viene por
    la misma línea al frente. 'direccion' no ve el vaivén y 'posicion' sí, y
    la herramienta lo tiene que decir con ``corr prof``."""
    import csv
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    from comparar_mapeo_brazos import leer_sesion, metricas, reproducir

    columnas = ["t"]
    for s in ("L", "R"):
        columnas += [f"crudo_abd_{s}", f"crudo_flex_{s}"]
        columnas += [f"h_lat_{s}", f"h_sup_{s}", f"h_ant_{s}"]
        columnas += [f"p_lat_{s}", f"p_sup_{s}", f"p_ant_{s}"]
        columnas += [f"c_lat_{s}", f"c_sup_{s}", f"c_ant_{s}"]
    ruta = tmp_path / "vaiven.csv"
    with open(ruta, "w", newline="", encoding="utf-8") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=columnas)
        escritor.writeheader()
        for i in range(300):
            t = i / 30
            # Entre 25 y 55 cm por delante del hombro, ni arriba ni al lado.
            profundidad = 0.40 + 0.15 * math.sin(2 * math.pi * t / 4.0)
            codo_ant = profundidad / 2
            codo_sup = -math.sqrt(max(0.0, 0.3**2 - codo_ant**2))
            fila = {"t": f"{t:.4f}"}
            for s in ("L", "R"):
                fila.update({
                    f"crudo_abd_{s}": "-90", f"crudo_flex_{s}": "90",
                    f"h_lat_{s}": "0", f"h_sup_{s}": "0", f"h_ant_{s}": "1",
                    f"p_lat_{s}": "0", f"p_sup_{s}": "0", f"p_ant_{s}": f"{profundidad:.5f}",
                    f"c_lat_{s}": "0", f"c_sup_{s}": f"{codo_sup:.5f}",
                    f"c_ant_{s}": f"{codo_ant:.5f}",
                })
            escritor.writerow(fila)

    sesion = leer_sesion(ruta)
    assert sesion["con_posicion"]
    assert sesion["largo"][Side.L] == pytest.approx(0.6, abs=1e-4)
    posicion = metricas(sesion, reproducir(sesion, "posicion"), Side.L)
    direccion_m = metricas(sesion, reproducir(sesion, "direccion"), Side.L)
    # No llega a 1: el arranque desde el reposo, el filtro y la curva de la
    # envolvente no son lineales. Lo que importa es que sigue el vaivén.
    assert posicion["corr_profundidad"] > 0.8
    # 'direccion' deja la punta quieta en cuanto sale del reposo: la
    # profundidad de la mano no la mueve.
    assert not direccion_m["corr_profundidad"] > 0.5


# --------------------------------------------------------------------------
# 7. El CSV del demo, de punta a punta
# --------------------------------------------------------------------------


def test_el_demo_graba_tantas_columnas_como_anuncia(monkeypatch, tmp_path):
    """El modo demo no lo ejecuta ninguna otra prueba, y el CSV es lo que se
    analiza después: una columna de más o de menos desplaza todas las
    siguientes sin que nada falle al grabar. Se corre entero con una cámara y
    un BlazePose falsos, sin ventanas."""
    import csv

    import cv2

    import telmeow_teleop.arm_tracker as modulo

    class CamaraFalsa:
        def __init__(self, frames):
            self.frames = frames

        def read(self):
            if self.frames <= 0:
                return False, None
            self.frames -= 1
            return True, np.zeros((480, 640, 3), dtype=np.uint8)

        def get(self, propiedad):
            return {cv2.CAP_PROP_FRAME_WIDTH: 640.0, cv2.CAP_PROP_FRAME_HEIGHT: 480.0}.get(
                propiedad, 30.0
            )

        def release(self):
            pass

    mundo = _mundo_brazo(_CODO_RECOGIDO, _MANO_RECOGIDA)
    resultado = SimpleNamespace(
        pose_world_landmarks=SimpleNamespace(landmark=mundo), pose_landmarks=None
    )
    estimador = SimpleNamespace(process=lambda _frame: resultado, close=lambda: None)
    monkeypatch.setattr(modulo, "abrir_camara", lambda _cfg: CamaraFalsa(6))
    monkeypatch.setattr(modulo, "describir", lambda _cap, _cfg: "camara falsa")
    monkeypatch.setattr(modulo.mp.solutions.pose, "Pose", lambda **_kw: estimador)
    monkeypatch.setattr(modulo.cv2, "imshow", lambda *_a: None)
    monkeypatch.setattr(modulo.cv2, "waitKey", lambda *_a: -1)
    monkeypatch.setattr(modulo.cv2, "destroyAllWindows", lambda: None)
    for variable in [v for v in __import__("os").environ if v.startswith("TELMEOW_")]:
        monkeypatch.delenv(variable)

    ruta = tmp_path / "demo.csv"
    assert modulo._demo(["--record", str(ruta)]) == 0

    with open(ruta, newline="", encoding="utf-8") as archivo:
        filas = list(csv.reader(archivo))
    cabecera, datos = filas[0], filas[1:]
    assert len(datos) == 6
    assert all(len(fila) == len(cabecera) for fila in datos)
    for nombre in (
        "p_ant_R", "c_sup_R", "ext_R", "mapeo",
        "peso_mano_R", "reposo_R", "pb_ant_R", "cb_sup_R", "img_mano_x_R", "escorzo",
    ):
        assert nombre in cabecera
    fila = dict(zip(cabecera, datos[-1]))
    # Con el frame espejado y el espejo en izq->izq, la etiqueta L de BlazePose
    # manda sobre armR.
    assert fila["mapeo"] == "posicion"
    assert float(fila["p_ant_R"]) == pytest.approx(0.25, abs=1e-4)
    assert float(fila["ext_R"]) == pytest.approx(0.25 / 0.6, abs=1e-3)
