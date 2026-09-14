"""Pruebas de la apertura de cámara.

No abren ninguna cámara: sustituyen ``cv2.VideoCapture`` por un doble que
**registra el orden de las llamadas**. Ese orden es justamente lo que falla en
silencio con una cámara real —el FOURCC pedido después de la resolución no se
aplica, y la exposición heredada del proceso anterior deja la imagen negra—, así
que es lo que hay que fijar.
"""

import pytest

from telmeow_teleop import camera
from telmeow_teleop.camera import (
    AUTO_EXPOSURE_OFF,
    AUTO_EXPOSURE_ON,
    BACKENDS,
    abrir_camara,
    brillo_medio,
    describir,
)
from telmeow_teleop.config import CameraConfig


class CapturaFalsa:
    """Registra en orden lo que se le pide, y contesta lo que se le diga."""

    def __init__(self, abierta=True, valores=None):
        self.abierta = abierta
        self.llamadas: list[tuple[int, float]] = []
        self.valores = valores or {}
        self.liberada = False

    def isOpened(self):
        return self.abierta

    def set(self, prop, valor):
        self.llamadas.append((prop, valor))
        return True

    def get(self, prop):
        return self.valores.get(prop, 0.0)

    def release(self):
        self.liberada = True

    # -- ayudas para las aserciones -----------------------------------------

    def propiedades(self):
        return [prop for prop, _ in self.llamadas]

    def valor_de(self, prop):
        for p, v in self.llamadas:
            if p == prop:
                return v
        return None


@pytest.fixture
def cv2_falso(monkeypatch):
    """Sustituye cv2.VideoCapture por el doble y devuelve la instancia."""
    creadas = []

    def fabrica(indice, backend):
        cap = CapturaFalsa()
        cap.indice, cap.backend = indice, backend
        creadas.append(cap)
        return cap

    monkeypatch.setattr(camera.cv2, "VideoCapture", fabrica)
    return creadas


def test_el_fourcc_se_pide_antes_que_la_resolucion(cv2_falso):
    """La trampa número uno, y no da ningún error cuando se incumple.

    Muchas webcams solo entregan 30 fps si comprimen en MJPG; si el formato se
    pide DESPUES de la resolución, la cámara se queda en el formato sin
    comprimir y baja a 10 fps sin avisar de nada (medido en este proyecto).
    """
    abrir_camara(CameraConfig(fourcc="MJPG"))
    props = cv2_falso[0].propiedades()
    assert props.index(camera.cv2.CAP_PROP_FOURCC) < props.index(
        camera.cv2.CAP_PROP_FRAME_WIDTH
    )


def test_sin_fourcc_no_se_pide_el_formato(cv2_falso):
    """Cadena vacía significa «deja el que elija el backend»."""
    abrir_camara(CameraConfig(fourcc=""))
    assert camera.cv2.CAP_PROP_FOURCC not in cv2_falso[0].propiedades()


def test_la_exposicion_se_pide_SIEMPRE(cv2_falso):
    """La trampa número dos, y es la que dejó la cámara negra el 2026-09-13.

    Los ajustes de DirectShow viven en el driver, no en el proceso: la
    exposición que deje puesta un programa sigue ahí en el siguiente. No
    tocarla hace el arranque dependiente de lo que pasó antes, y el síntoma
    —imagen negra a la tasa nominal, sin ningún error— no se parece a un fallo.
    """
    abrir_camara(CameraConfig(auto_exposure=True))
    cap = cv2_falso[0]
    assert camera.cv2.CAP_PROP_AUTO_EXPOSURE in cap.propiedades()
    assert cap.valor_de(camera.cv2.CAP_PROP_AUTO_EXPOSURE) == AUTO_EXPOSURE_ON


def test_en_manual_se_fija_el_valor_de_exposicion(cv2_falso):
    abrir_camara(CameraConfig(auto_exposure=False, exposure=-2.0))
    cap = cv2_falso[0]
    assert cap.valor_de(camera.cv2.CAP_PROP_AUTO_EXPOSURE) == AUTO_EXPOSURE_OFF
    assert cap.valor_de(camera.cv2.CAP_PROP_EXPOSURE) == -2.0


def test_en_automatico_no_se_fija_ningun_valor_manual(cv2_falso):
    """Fijar los dos a la vez es contradictorio y el driver decide por su cuenta."""
    abrir_camara(CameraConfig(auto_exposure=True, exposure=-2.0))
    assert camera.cv2.CAP_PROP_EXPOSURE not in cv2_falso[0].propiedades()


def test_los_valores_de_autoexposicion_no_son_uno_y_cero():
    """No son 1/0 y confundirlos deja la cámara ciega sin ningún error.

    Salen del rango del filtro de DirectShow y no están en la documentación de
    OpenCV, así que viven en constantes con nombre. Si alguien los 'normaliza'
    a 1 y 0, esta prueba se pone roja.
    """
    assert AUTO_EXPOSURE_ON == 0.75
    assert AUTO_EXPOSURE_OFF == 0.25


def test_una_camara_que_no_abre_devuelve_None_y_se_libera(monkeypatch):
    """Se devuelve None en vez de lanzar: quien llama imprime algo útil y sale."""
    cerrada = CapturaFalsa(abierta=False)
    monkeypatch.setattr(camera.cv2, "VideoCapture", lambda *a: cerrada)
    assert abrir_camara(CameraConfig()) is None
    assert cerrada.liberada


def test_un_backend_desconocido_falla_pronto_y_dice_cuales_valen(cv2_falso):
    with pytest.raises(ValueError, match="dshow"):
        abrir_camara(CameraConfig(backend="inventado"))


def test_se_usa_el_backend_pedido(cv2_falso):
    abrir_camara(CameraConfig(backend="any"))
    assert cv2_falso[0].backend == BACKENDS["any"]


def test_msmf_esta_disponible_pero_no_es_el_defecto():
    """MSMF **se cuelga** en algunas máquinas con las protecciones de Windows
    activadas, así que el defecto es DirectShow. Se mantiene seleccionable."""
    assert CameraConfig.backend == "dshow"
    assert "msmf" in BACKENDS


def test_describir_lee_de_vuelta_lo_concedido(cv2_falso):
    """Lo que se pidió y lo que la cámara concedió no son lo mismo (H19)."""
    abrir_camara(CameraConfig(width=1280, height=720))
    cap = cv2_falso[0]
    cap.valores = {
        camera.cv2.CAP_PROP_FRAME_WIDTH: 640,
        camera.cv2.CAP_PROP_FRAME_HEIGHT: 480,
        camera.cv2.CAP_PROP_FPS: 30,
    }
    texto = describir(cap, CameraConfig(width=1280, height=720))
    assert "640x480" in texto          # lo concedido, no los 1280x720 pedidos
    assert "auto" in texto


def test_describir_dice_el_valor_cuando_la_exposicion_es_manual(cv2_falso):
    abrir_camara(CameraConfig(auto_exposure=False, exposure=-2.0))
    cap = cv2_falso[0]
    cap.valores = {camera.cv2.CAP_PROP_EXPOSURE: -2.0}
    texto = describir(cap, CameraConfig(auto_exposure=False, exposure=-2.0))
    assert "manual" in texto and "-2" in texto


def test_brillo_medio_distingue_negro_de_imagen():
    """El umbral separa «hay poca luz» de «no hay imagen».

    Medido el 2026-09-13 en la cámara integrada: 2.2 con la exposición clavada
    en manual −6, 117.9 con la automática.
    """
    np = pytest.importorskip("numpy")
    negro = np.zeros((48, 64, 3), dtype=np.uint8)
    gris = np.full((48, 64, 3), 118, dtype=np.uint8)
    assert brillo_medio(negro) == 0.0
    assert brillo_medio(gris) == pytest.approx(118.0)


def test_la_configuracion_de_camara_trae_exposicion_automatica():
    """El defecto tiene que ser la automática: es lo que hace el arranque
    reproducible en vez de heredar lo que dejó el programa anterior."""
    assert CameraConfig.auto_exposure is True


def test_la_exposicion_se_puede_fijar_por_entorno():
    cfg = CameraConfig.from_env(
        {
            "TELMEOW_CAMERA_AUTO_EXPOSURE": "0",
            "TELMEOW_CAMERA_EXPOSURE": "-2.5",
        }
    )
    assert cfg.auto_exposure is False
    assert cfg.exposure == -2.5
