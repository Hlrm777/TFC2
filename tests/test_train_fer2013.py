"""Pruebas de ``tools/train_fer2013.py`` (P8).

Lo que se puede equivocar en silencio al entrenar son, otra vez, CONVENIOS: el
orden de las clases de FER-2013, el de los píxeles al pasar del CSV a la
imagen, el de las salidas de la red frente al JSON, y la entrada de la red
frente a la del clasificador en operación. Cada uno se ata a algo de FUERA del
script:

- el orden de las clases, a las cifras documentadas del dataset por clase;
- el de los píxeles, al detector Haar de OpenCV, que encuentra caras derechas
  y no traspuestas;
- la arquitectura, al ``emotion_model.keras`` del legacy;
- la entrada, a ``preprocesar_48`` del P6;
- el JSON y las salidas, al ``KerasBackend`` del P6.

Las que necesitan FER-2013 (``fer2013/``), el modelo legacy o el entrenado se
saltan si no están: viven fuera de git.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
import tarfile
import urllib.error
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import train_fer2013 as tr  # noqa: E402

from telmeow_teleop.config import EMOTION_LABELS, PROJECT_ROOT  # noqa: E402
from telmeow_teleop.emotion_classifier import (  # noqa: E402
    KerasBackend,
    cargar_cascade,
    leer_metadatos,
    preprocesar_48,
    ruta_metadatos,
)

FER2013 = PROJECT_ROOT / "fer2013" / tr.FUENTE.archivo
LEGACY = PROJECT_ROOT / "emotion_model.keras"
ENTRENADO = PROJECT_ROOT / "emotion_model_fer2013.keras"

requiere_fer2013 = pytest.mark.skipif(
    not FER2013.is_file(), reason="fer2013/ no está (se descarga con tools/train_fer2013.py)"
)


@pytest.fixture(scope="module")
def fer2013():
    return tr.leer_fer2013(FER2013)


def _csv(filas, cabecera=("emotion", "pixels", "Usage")) -> bytes:
    salida = io.StringIO()
    escritor = csv.writer(salida, lineterminator="\n")
    escritor.writerow(cabecera)
    for fila in filas:
        escritor.writerow(fila)
    return salida.getvalue().encode("ascii")


def _pixeles(valor_o_array) -> str:
    a = np.broadcast_to(np.asarray(valor_o_array, dtype=np.int64), (48, 48))
    return " ".join(str(v) for v in a.ravel())


# --------------------------------------------------------------------------
# Las clases
# --------------------------------------------------------------------------


def test_el_mapeo_cubre_las_cinco_del_robot_y_descarta_asco_y_miedo():
    assert set(tr.A_ROBOT.values()) == set(EMOTION_LABELS)
    assert set(tr.A_ROBOT) | set(tr.DESCARTADAS) == set(tr.CLASES_FER)
    assert not set(tr.A_ROBOT) & set(tr.DESCARTADAS)
    assert set(tr.DESCARTADAS) == {"Disgust", "Fear"}


def test_los_indices_pasan_por_los_nombres():
    """0=Angry ... 6=Neutral (FER-2013) → (feliz, triste, sorprendido,
    neutral, enojado) del robot. Escrito a mano, no con las tablas del script."""
    esperado = [
        EMOTION_LABELS.index("enojado"), -1, -1,
        EMOTION_LABELS.index("feliz"), EMOTION_LABELS.index("triste"),
        EMOTION_LABELS.index("sorprendido"), EMOTION_LABELS.index("neutral"),
    ]
    assert tr.a_indices_robot(np.arange(7)).tolist() == esperado


@requiere_fer2013
def test_las_cifras_canonicas_cuadran_con_el_archivo_real(fer2013):
    tr.comprobar_canonico(fer2013)


@requiere_fer2013
def test_con_las_clases_rotadas_las_cifras_no_cuadran(fer2013, monkeypatch):
    """Lo que ata el orden de CLASES_FER a los datos: las imágenes de cada clase
    son siete cifras documentadas, y rotando una posición no cuadra ninguna."""
    monkeypatch.setattr(tr, "CLASES_FER", tr.CLASES_FER[1:] + tr.CLASES_FER[:1])
    with pytest.raises(tr.DatasetNoCanonico, match="Training/"):
        tr.comprobar_canonico(fer2013)


def test_comprobar_canonico_dice_que_no_cuadra():
    datos = tr.Fer2013(
        pixeles=np.zeros((4, 48, 48), np.uint8),
        clases=np.array([3, 3, 0, 1]),
        usos=np.array(["Training", "Training", "Training", "PublicTest"]),
        sha256_csv="",
    )
    canonico = {"total": 4, "por_uso": {"Training": 3, "PublicTest": 1, "PrivateTest": 0},
                "training_por_clase": {"Happy": 2, "Angry": 1}}
    tr.comprobar_canonico(datos, canonico)
    canonico["training_por_clase"]["Happy"] = 3
    with pytest.raises(tr.DatasetNoCanonico, match="Training/Happy: 2 en vez de 3"):
        tr.comprobar_canonico(datos, canonico)


# --------------------------------------------------------------------------
# Lectura
# --------------------------------------------------------------------------


def test_lee_el_csv_y_su_huella_es_la_del_archivo(tmp_path):
    imagen = np.arange(48 * 48).reshape(48, 48) % 256
    crudo = _csv([(3, _pixeles(imagen), "Training"), (6, _pixeles(7), "PrivateTest")])
    ruta = tmp_path / "fer2013.csv"
    ruta.write_bytes(crudo)

    datos = tr.leer_fer2013(ruta)
    assert len(datos) == 2
    assert datos.pixeles.dtype == np.uint8 and datos.pixeles.shape == (2, 48, 48)
    assert np.array_equal(datos.pixeles[0], imagen)          # fila a fila, como en el CSV
    assert datos.clases.tolist() == [3, 6]
    assert datos.usos.tolist() == ["Training", "PrivateTest"]
    assert datos.sha256_csv == hashlib.sha256(crudo).hexdigest()


def test_lee_el_encabezado_de_icml_face_data_y_dentro_de_un_tar(tmp_path):
    """``icml_face_data.csv`` trae ``emotion, Usage, pixels``, con espacios; y
    el original va dentro de un .tar.gz, que se lee sin extraer."""
    crudo = _csv([(0, "Training", _pixeles(1))], cabecera=("emotion", " Usage", " pixels"))
    (tmp_path / "x").mkdir()
    suelto = tmp_path / "x" / "icml_face_data.csv"
    suelto.write_bytes(crudo)
    empaquetado = tmp_path / "fer2013.tar.gz"
    with tarfile.open(empaquetado, "w:gz") as tar:
        tar.add(suelto, arcname="fer2013/fer2013.csv")

    for ruta in (suelto, empaquetado):
        datos = tr.leer_fer2013(ruta)
        assert datos.clases.tolist() == [0] and datos.usos.tolist() == ["Training"]
        assert datos.pixeles.max() == 1
        assert datos.sha256_csv == hashlib.sha256(crudo).hexdigest()


@pytest.mark.parametrize("fila, mensaje", [
    ((3, "1 2 3", "Training"), "píxeles"),
    ((3, _pixeles(256), "Training"), "píxeles"),
    ((7, _pixeles(0), "Training"), "clase 7"),
    ((3, _pixeles(0), "Validation"), "partición desconocida"),
])
def test_una_fila_mala_se_rechaza_con_su_linea(tmp_path, fila, mensaje):
    ruta = tmp_path / "malo.csv"
    ruta.write_bytes(_csv([(0, _pixeles(0), "Training"), fila]))
    with pytest.raises(ValueError, match=f"línea 3: .*{mensaje}"):
        tr.leer_fer2013(ruta)


@requiere_fer2013
def test_los_pixeles_quedan_derechos_segun_el_haar(fer2013):
    """El orden de los píxeles, atado a un detector que no es de este proyecto.

    El CSV guarda cada imagen como 2304 números; leerlos por columnas en vez de
    por filas da la cara traspuesta, y nada fallaría: la red aprendería caras
    tumbadas. El Haar frontal de OpenCV se entrenó con caras derechas, así que
    encuentra muchas más en la lectura buena que en la traspuesta o boca abajo
    (medido el 2026-10-20: 184, 12 y 28 de 300).
    """
    cascade = cargar_cascade()
    muestra = fer2013.pixeles[:: len(fer2013) // 300][:300]

    def detectadas(imagenes):
        return sum(
            len(cascade.detectMultiScale(cv2.resize(img, (96, 96)), scaleFactor=1.1,
                                         minNeighbors=3)) > 0
            for img in imagenes
        )

    derechas = detectadas(muestra)
    assert derechas > 0.5 * len(muestra)
    assert derechas > 3 * detectadas(muestra.transpose(0, 2, 1))
    assert derechas > 3 * detectadas(muestra[:, ::-1, :])


# --------------------------------------------------------------------------
# Descarga
# --------------------------------------------------------------------------


class _Respuesta(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _fuente(contenido: bytes) -> tr.Fuente:
    return tr.Fuente("x/y", "abc", "datos.tar.gz", hashlib.sha256(contenido).hexdigest(),
                     len(contenido))


def test_descarga_y_verifica_la_huella(tmp_path):
    contenido = b"fer2013" * 1000
    pedidas = []

    def abrir(url, timeout):
        pedidas.append(url)
        return _Respuesta(contenido)

    fuente = _fuente(contenido)
    ruta = tr.descargar(tmp_path, fuente, abrir=abrir)
    assert ruta.read_bytes() == contenido
    assert pedidas == ["https://huggingface.co/datasets/x/y/resolve/abc/datos.tar.gz"]
    assert not list(tmp_path.glob("*.parcial"))

    # La segunda vez no se descarga: lo que hay ya tiene la huella buena.
    tr.descargar(tmp_path, fuente, abrir=abrir)
    assert len(pedidas) == 1


def test_una_descarga_con_otra_huella_no_deja_nada(tmp_path):
    fuente = _fuente(b"lo esperado")
    with pytest.raises(tr.DatasetNoCanonico):
        tr.descargar(tmp_path, fuente, abrir=lambda url, timeout: _Respuesta(b"lo recibido"))
    assert list(tmp_path.iterdir()) == []


def test_un_archivo_con_otra_huella_no_se_pisa(tmp_path):
    (tmp_path / "datos.tar.gz").write_bytes(b"otra copia")
    with pytest.raises(tr.DatasetNoCanonico, match="No se pisa"):
        tr.descargar(tmp_path, _fuente(b"la buena"), abrir=lambda url, timeout: 1 / 0)
    assert (tmp_path / "datos.tar.gz").read_bytes() == b"otra copia"


def test_la_descarga_se_reintenta_si_falla_la_conexion(tmp_path):
    contenido = b"x" * 100
    intentos = []

    def abrir(url, timeout):
        intentos.append(url)
        if len(intentos) < 3:
            raise urllib.error.URLError("timed out")
        return _Respuesta(contenido)

    tr.descargar(tmp_path, _fuente(contenido), abrir=abrir, espera_s=0)
    assert len(intentos) == 3

    intentos.clear()
    with pytest.raises(ConnectionError, match="--archivo"):
        tr.descargar(tmp_path / "otra", _fuente(contenido), intentos=2, espera_s=0,
                     abrir=lambda url, timeout: (_ for _ in ()).throw(TimeoutError()))


# --------------------------------------------------------------------------
# Partición y pesos
# --------------------------------------------------------------------------


def test_la_validacion_toma_la_fraccion_de_cada_clase_y_es_determinista():
    etiquetas = np.repeat(np.arange(5), [700, 450, 300, 500, 400])
    grupos = list(range(len(etiquetas)))
    a = tr.particion_validacion(etiquetas, grupos, 0.1, semilla=7)
    assert np.array_equal(a, tr.particion_validacion(etiquetas, grupos, 0.1, semilla=7))
    assert not np.array_equal(a, tr.particion_validacion(etiquetas, grupos, 0.1, semilla=8))
    assert np.bincount(etiquetas[a]).tolist() == [70, 45, 30, 50, 40]


def test_las_imagenes_identicas_van_juntas():
    """Dos copias de la misma imagen a los dos lados serían la fuga del bug 13."""
    rng = np.random.default_rng(0)
    etiquetas = rng.integers(0, 5, 2000)
    grupos = rng.integers(0, 600, 2000).tolist()       # muchas imágenes repetidas
    val = tr.particion_validacion(etiquetas, grupos, 0.2, semilla=1)
    lado = {}
    for g, v in zip(grupos, val):
        assert lado.setdefault(g, v) == v
    assert 0.15 < val.mean() < 0.25


def test_la_fraccion_tiene_que_ser_una_fraccion():
    for f in (0.0, 1.0, -0.1):
        with pytest.raises(ValueError):
            tr.particion_validacion(np.zeros(10), list(range(10)), f, 0)


def test_los_pesos_de_clase_igualan_lo_que_pesa_cada_clase():
    pesos = tr.pesos_de_clase(np.array([0, 0, 0, 1]), 2)
    assert pesos == pytest.approx({0: 4 / 6, 1: 2.0})
    etiquetas = np.repeat(np.arange(5), [7215, 4830, 3171, 4965, 3995])
    pesos = tr.pesos_de_clase(etiquetas, 5)
    cuenta = np.bincount(etiquetas)
    assert [pesos[c] * cuenta[c] for c in range(5)] == pytest.approx([len(etiquetas) / 5] * 5)
    with pytest.raises(ValueError):
        tr.pesos_de_clase(np.array([0, 0, 2]), 3)


def test_preparar_filtra_reparte_y_marca_las_repetidas():
    imgs = np.stack([np.full((48, 48), v, np.uint8) for v in range(12)])
    imgs[10] = imgs[0]                       # una de la prueba es copia de una de Training
    datos = tr.Fer2013(
        pixeles=imgs,
        # Angry, Disgust, Fear, Happy, Sad, Surprise, Neutral, Happy, Sad, Angry | prueba
        clases=np.array([0, 1, 2, 3, 4, 5, 6, 3, 4, 0, 3, 2]),
        usos=np.array(["Training"] * 10 + ["PublicTest", "PrivateTest"]),
        sha256_csv="",
    )
    p = tr.preparar(datos, fraccion=0.5, semilla=0)
    todos = np.concatenate([p.entrenamiento, p.validacion, p.prueba])
    assert len(set(todos.tolist())) == len(todos)                  # sin solapes
    assert not set(todos.tolist()) & {1, 2, 11}                     # asco y miedo fuera
    assert set(p.entrenamiento.tolist()) | set(p.validacion.tolist()) == {0, 3, 4, 5, 6, 7, 8, 9}
    assert p.prueba.tolist() == [10]
    assert p.repetida.tolist() == [True]
    assert p.huella() == tr.preparar(datos, fraccion=0.5, semilla=0).huella()


# --------------------------------------------------------------------------
# La entrada de la red
# --------------------------------------------------------------------------


def test_a_tensor_es_preprocesar_48_bit_a_bit():
    """La red se entrena con ``a_tensor`` y opera con ``preprocesar_48``: si no
    dieran lo mismo, se entrenaría con una entrada y se operaría con otra."""
    imgs = np.random.default_rng(3).integers(0, 256, (20, 48, 48), dtype=np.uint8)
    imgs[0] = 0
    imgs[1] = 255
    lote = tr.a_tensor(imgs)
    assert lote.shape == (20, 48, 48, 1) and lote.dtype == np.float32
    for i, img in enumerate(imgs):
        una = preprocesar_48(img)
        assert una.dtype == lote.dtype and np.array_equal(una[0], lote[i])


def test_a_tensor_rechaza_lo_que_ya_esta_normalizado():
    """Dividir dos veces entre 255 deja una cara casi negra, sin ningún error."""
    with pytest.raises(ValueError):
        tr.a_tensor(np.zeros((2, 48, 48), np.float32))


# --------------------------------------------------------------------------
# Métricas
# --------------------------------------------------------------------------


def test_las_metricas_contra_un_ejemplo_resuelto_a_mano():
    """Tres clases. Verdad A A A B B C; predicción A A B B B B.

    Confusión: A→(2, 1, 0), B→(0, 2, 0), C→(0, 1, 0). Precisión A 2/2, B 2/4,
    C 0 (nunca se predice); recall A 2/3, B 2/2, C 0/1. F1 A 0.8, B 2/3, C 0.
    """
    m = tr.metricas([0, 0, 0, 1, 1, 2], [0, 0, 1, 1, 1, 1], ("A", "B", "C"))
    assert m["confusion"] == [[2, 1, 0], [0, 2, 0], [0, 1, 0]]
    assert m["accuracy"] == pytest.approx(4 / 6)
    pc = m["por_clase"]
    assert [pc[c]["precision"] for c in "ABC"] == pytest.approx([1.0, 0.5, 0.0])
    assert [pc[c]["recall"] for c in "ABC"] == pytest.approx([2 / 3, 1.0, 0.0])
    assert [pc[c]["f1"] for c in "ABC"] == pytest.approx([0.8, 2 / 3, 0.0])
    assert [pc[c]["soporte"] for c in "ABC"] == [3, 2, 1]
    assert m["macro_f1"] == pytest.approx((0.8 + 2 / 3) / 3)
    assert m["accuracy_balanceada"] == pytest.approx((2 / 3 + 1.0) / 3)


# --------------------------------------------------------------------------
# La red
# --------------------------------------------------------------------------


def test_la_red_es_la_del_legacy_con_batchnorm():
    modelo = tr.construir_modelo()
    assert tuple(modelo.input_shape[1:]) == (48, 48, 1)
    assert tuple(modelo.output_shape[1:]) == (len(EMOTION_LABELS),)
    tipos = [type(c).__name__ for c in modelo.layers]
    assert tipos.count("BatchNormalization") == 3
    assert not [t for t in tipos if t.startswith("Random")]          # la aumentación va fuera
    assert modelo.layers[-1].get_config()["activation"] == "softmax"
    assert [c.rate for c in modelo.layers if type(c).__name__ == "Dropout"] == [0.5]


@pytest.mark.parametrize("arquitectura", ["vgg", "vgg_sin_dropout"])
def test_las_candidatas_del_p8_1_son_mayores_y_solo_difieren_en_el_dropout(arquitectura):
    modelo = tr.construir_modelo(arquitectura=arquitectura)
    assert tuple(modelo.input_shape[1:]) == (48, 48, 1)
    assert tuple(modelo.output_shape[1:]) == (len(EMOTION_LABELS),)
    assert modelo.layers[-1].get_config()["activation"] == "softmax"
    convs = [c for c in modelo.layers if type(c).__name__ == "Conv2D"]
    assert [c.filters for c in convs] == [32, 32, 64, 64, 128, 128, 256, 256]
    assert {c.get_config()["padding"] for c in convs} == {"same"}
    assert modelo.count_params() == 1_765_925               # 5 veces la legacy
    tipos = [type(c).__name__ for c in modelo.layers]
    assert not [t for t in tipos if t.startswith("Random")]
    abandono = [c.rate for c in modelo.layers if type(c).__name__ == "Dropout"]
    assert abandono == ([0.25] * 4 + [0.5] if arquitectura == "vgg" else [0.5])


def test_una_arquitectura_desconocida_se_rechaza():
    with pytest.raises(ValueError, match="arquitectura desconocida"):
        tr.construir_modelo(arquitectura="resnet")


def test_cada_arquitectura_se_guarda_en_su_archivo_y_ninguna_pisa_el_elegido():
    assert tr.salida_por_defecto("legacy") == ENTRENADO
    otras = {tr.salida_por_defecto(a) for a in tr.ARQUITECTURAS if a != "legacy"}
    assert ENTRENADO not in otras and len(otras) == len(tr.ARQUITECTURAS) - 1
    assert tr.salida_por_defecto("vgg").name == "emotion_model_fer2013_vgg.keras"


def test_una_red_vgg_guardada_la_carga_el_backend_del_p6(tmp_path):
    ruta = tmp_path / "vgg.keras"
    tr.construir_modelo(arquitectura="vgg").save(ruta)
    tr.escribir_metadatos(ruta, {"clases": list(EMOTION_LABELS)})
    backend = KerasBackend(ruta)                 # comprueba entrada 48x48x1 y softmax
    d = backend.distribucion(np.full((48, 48), 128, np.uint8))
    assert backend.clases == EMOTION_LABELS
    assert sum(d.probabilidades.values()) == pytest.approx(1.0, abs=1e-5)


@pytest.mark.skipif(not LEGACY.is_file(), reason="emotion_model.keras no está (vive fuera de git)")
def test_las_capas_con_pesos_tienen_las_formas_del_modelo_legacy():
    """Atado al ``.keras`` legacy, el que se entrenó en 2024: mismos núcleos de
    convolución y mismas capas densas (las convoluciones nuevas van sin sesgo,
    que la normalización hace inútil)."""
    import keras

    legacy = keras.saving.load_model(LEGACY, compile=False)
    nuevo = tr.construir_modelo()

    def nucleos(m):
        return [tuple(c.kernel.shape) for c in m.layers if type(c).__name__ in ("Conv2D", "Dense")]

    assert nucleos(nuevo) == nucleos(legacy)


# --------------------------------------------------------------------------
# Entrenar, guardar y cargar por el camino del P6
# --------------------------------------------------------------------------


def _sintetico(n_por_clase: int, semilla: int = 0):
    """Cinco clases fáciles: un cuadrado claro en un sitio distinto para cada una."""
    rng = np.random.default_rng(semilla)
    imgs, etiquetas = [], []
    for clase in range(5):
        for _ in range(n_por_clase):
            img = rng.integers(0, 60, (48, 48)).astype(np.uint8)
            fila, col = divmod(clase, 3)
            img[4 + 20 * fila: 20 + 20 * fila, 4 + 14 * col: 18 + 14 * col] = 230
            imgs.append(img)
            etiquetas.append(clase)
    return np.stack(imgs), np.array(etiquetas)


@pytest.fixture(scope="module")
def entrenado(tmp_path_factory):
    """Un modelo diminuto entrenado de verdad, guardado con sus metadatos."""
    carpeta = tmp_path_factory.mktemp("p8")
    imgs, etiquetas = _sintetico(40)
    x = tr.a_tensor(imgs)
    modelo = tr.construir_modelo()
    ruta = carpeta / "modelo.keras"
    resumen = tr.entrenar(modelo, x, etiquetas, x[::4], etiquetas[::4],
                          pesos=tr.pesos_de_clase(etiquetas, 5), epocas=2, lote=32,
                          historial=tr.ruta_historial(ruta), verbose=0)
    modelo.save(ruta)
    tr.escribir_metadatos(ruta, {"clases": list(EMOTION_LABELS), "accuracy": 0.5})
    return ruta, imgs, tr.predecir(modelo, x), resumen


def test_el_modelo_guardado_carga_y_reproduce_sus_predicciones(entrenado):
    ruta, imgs, prob, resumen = entrenado
    assert resumen["epocas"] == 2 and 1 <= resumen["mejor_epoca"] <= 2
    v = tr.verificar_guardado(ruta, imgs[::10], prob[::10])
    assert v["forma"] == [1, 5] and v["imagenes"] == 20
    assert ruta_metadatos(ruta).name == "modelo.meta.json"
    assert leer_metadatos(ruta_metadatos(ruta))["clases"] == list(EMOTION_LABELS)
    assert KerasBackend(ruta).clases == EMOTION_LABELS


def test_la_verificacion_detecta_un_json_con_otro_orden(entrenado, tmp_path):
    """Lo que ata el orden del JSON al de la red: con las clases rotadas, las
    probabilidades por expresión salen cambiadas y la verificación lo dice."""
    ruta, imgs, prob, _ = entrenado
    copia = tmp_path / "rotado.keras"
    copia.write_bytes(ruta.read_bytes())
    rotadas = list(EMOTION_LABELS[1:] + EMOTION_LABELS[:1])
    ruta_metadatos(copia).write_text(json.dumps({"clases": rotadas}), encoding="utf-8")
    with pytest.raises(tr.GuardadoInvalido):
        tr.verificar_guardado(copia, imgs[::10], prob[::10])


def test_el_historial_tiene_una_fila_por_epoca(entrenado):
    ruta = entrenado[0]
    with open(tr.ruta_historial(ruta), encoding="utf-8") as archivo:
        filas = list(csv.DictReader(archivo))
    assert len(filas) == 2
    assert {"loss", "val_loss", "accuracy", "val_accuracy", "learning_rate", "segundos"} <= set(filas[0])


class _RedFalsa:
    """Elige la clase por el brillo de la mitad IZQUIERDA de la cara: no es
    simétrica, y voltear la cara le cambia la predicción."""

    def predict(self, x, batch_size=None, verbose=0):
        brillo = x[:, :, :24, 0].mean(axis=(1, 2))
        clase = np.minimum((brillo * 5).astype(int), 4)
        return np.eye(5, dtype=np.float32)[clase]


def _datos_sinteticos(n_por_clase: int = 20) -> tr.Fer2013:
    """Las siete clases en las tres particiones; la mitad izquierda de cada
    imagen tiene el brillo de su clase del robot, la derecha es oscura."""
    rng = np.random.default_rng(5)
    pixeles, clases, usos = [], [], []
    for uso in tr.USOS:
        for clase in range(7):
            robot = tr.a_indices_robot(np.array([clase]))[0]
            for _ in range(n_por_clase):
                img = np.zeros((48, 48), np.uint8)
                img[:, :24] = int(255 * (max(robot, 0) + 0.5) / 5)
                img[0, 30] = rng.integers(0, 255)            # para que no haya repetidas
                img[1, 30] = rng.integers(0, 255)
                pixeles.append(img)
                clases.append(clase)
                usos.append(uso)
    return tr.Fer2013(np.stack(pixeles), np.array(clases), np.array(usos), "")


def test_la_simetria_ve_que_voltear_cambia_la_prediccion():
    datos = _datos_sinteticos()
    p = tr.preparar(datos, fraccion=0.1, semilla=0)
    x, y = tr.a_tensor(datos.pixeles[p.prueba]), p.etiquetas[p.prueba]
    s = tr.simetria(_RedFalsa(), x, y)
    assert s["accuracy"] == 1.0
    assert s["accuracy_volteada"] < 0.3 and s["misma_prediccion"] < 0.3


def test_reevaluar_exige_las_metricas_guardadas_y_anade_lo_que_falta(tmp_path, monkeypatch):
    import keras

    datos = _datos_sinteticos()
    p = tr.preparar(datos, fraccion=0.1, semilla=3)
    m, _ = tr.evaluar(_RedFalsa(), datos, p)
    guardadas = {n: v for n, v in tr._metricas_para_guardar(m).items() if n != "simetria"}
    ruta = tmp_path / "modelo.keras"
    meta = {"clases": list(EMOTION_LABELS), "entrenamiento": {"semilla": 3},
            "particion": {"huella": p.huella(), "fraccion_validacion": 0.1},
            "metricas": guardadas}
    tr.escribir_metadatos(ruta, meta)
    monkeypatch.setattr(keras.saving, "load_model", lambda ruta: _RedFalsa())

    assert tr.reevaluar(ruta, datos, 0.1) == 0
    escrito = leer_metadatos(ruta_metadatos(ruta))
    assert escrito["metricas"]["simetria"]["misma_prediccion"] < 0.3
    assert escrito["metricas"]["prueba"] == guardadas["prueba"]          # lo que había, intacto

    escrito["metricas"]["prueba"]["confusion"][0][0] += 1               # otro modelo
    tr.escribir_metadatos(ruta, escrito)
    assert tr.reevaluar(ruta, datos, 0.1) == 1

    meta["particion"]["huella"] = "otra"                                 # otra partición
    tr.escribir_metadatos(ruta, meta)
    assert tr.reevaluar(ruta, datos, 0.1) == 1


@pytest.mark.parametrize("arquitectura", sorted(tr.ARQUITECTURAS))
def test_un_ensayo_no_pisa_el_modelo_de_verdad(capsys, arquitectura):
    assert tr.main(["--limite", "100", "--arquitectura", arquitectura]) == 1
    assert "no pisa" in capsys.readouterr().out


# --------------------------------------------------------------------------
# El modelo entrenado (si está)
# --------------------------------------------------------------------------


requiere_entrenado = pytest.mark.skipif(
    not ENTRENADO.is_file(), reason="emotion_model_fer2013.keras no está (vive fuera de git)"
)
DESCARTADO = PROJECT_ROOT / "emotion_model_fer2013_paciencia4.meta.json"


def test_el_modelo_elegido_es_el_de_mejor_validacion():
    """Los metadatos están en git aunque el .keras no: la selección se puede
    comprobar sin el modelo. Se eligió por validación, nunca por la prueba."""
    elegido = leer_metadatos(ruta_metadatos(ENTRENADO))
    descartado = leer_metadatos(DESCARTADO)
    assert elegido["clases"] == list(EMOTION_LABELS)
    assert not elegido["ensayo"] and elegido["advertencia"] is None
    assert elegido["entrenamiento"]["val_loss"] < descartado["entrenamiento"]["val_loss"]
    assert elegido["seleccion"].startswith("ELEGIDO") and descartado["seleccion"].startswith("DESCARTADO")
    assert elegido["particion"]["huella"] == descartado["particion"]["huella"]
    assert elegido["dataset"]["sha256_csv"] == "3b8d9617d1017f34733c8f2474d7784c563ce86c40a86ac12c2d37cc968f871b"
    # Por debajo del 60-70 % que esperaba el plan, y lejos del 90 % que delataría
    # un error de partición (P8).
    assert 0.5 < elegido["accuracy"] < tr.UMBRAL_SOSPECHA
    # Los valores por defecto del script son los del elegido: reentrenar sin
    # opciones lo reproduce.
    e = elegido["entrenamiento"]
    assert (e["semilla"], e["early_stopping"]["paciencia"], e["reduce_lr"]["paciencia"],
            e["epocas_maximas"]) == (tr.SEMILLA, tr.PACIENCIA, tr.PACIENCIA_TASA, tr.EPOCAS)
    assert e["arquitectura"] == tr.ARQUITECTURAS["legacy"]


VGG = PROJECT_ROOT / "emotion_model_fer2013_vgg.keras"


def test_la_red_mayor_se_eligio_por_validacion_y_no_toca_el_modelo_del_p8():
    """P8.1: dos candidatas fijadas antes de entrenar, todo lo demás igual al
    modelo del P8 (semilla, partición, paciencias), y elegida la de mejor
    validación. Los metadatos están en git aunque los .keras no."""
    elegida = leer_metadatos(ruta_metadatos(VGG))
    descartada = leer_metadatos(PROJECT_ROOT / "emotion_model_fer2013_vgg_sin_dropout.meta.json")
    p8 = leer_metadatos(ruta_metadatos(ENTRENADO))
    assert elegida["entrenamiento"]["arquitectura_id"] == "vgg"
    assert descartada["entrenamiento"]["arquitectura_id"] == "vgg_sin_dropout"
    assert elegida["entrenamiento"]["val_loss"] < descartada["entrenamiento"]["val_loss"]
    assert elegida["seleccion"].startswith("ELEGIDO") and descartada["seleccion"].startswith("DESCARTADO")
    for m in (elegida, descartada):
        assert m["particion"]["huella"] == p8["particion"]["huella"]
        for clave in ("semilla", "lote", "tasa_inicial", "epocas_maximas", "early_stopping", "reduce_lr",
                      "aumentacion"):
            assert m["entrenamiento"][clave] == p8["entrenamiento"][clave], clave
    assert p8["entrenamiento"]["arquitectura"] == tr.ARQUITECTURAS["legacy"]
    assert 0.6 < elegida["accuracy"] < tr.UMBRAL_SOSPECHA


@pytest.mark.skipif(not VGG.is_file(), reason="emotion_model_fer2013_vgg.keras no está (vive fuera de git)")
@pytest.mark.skipif(not (PROJECT_ROOT / "emotions_dataset").is_dir(),
                    reason="emotions_dataset/ no está (vive fuera de git)")
def test_con_la_red_mayor_las_etiquetas_del_operador_fijan_el_orden_entero():
    """Con la red mayor el ancla del operador vuelve a servir entera (al revés
    que con la legacy, H50): de las 120 permutaciones del orden de clases, la
    del JSON es la que más cuadra con sus etiquetas. Medido el 2026-10-24 con
    1 de cada 20 fotos: 58.8 % frente a 51.5 % de la mejor alternativa."""
    import itertools

    from telmeow_teleop.emotion_classifier import detectar_rostro

    backend = KerasBackend(VGG)
    cascade = cargar_cascade()
    salidas, verdad = [], []
    for archivo in sorted(a for a in (PROJECT_ROOT / "emotions_dataset").glob("*.jpg")
                          if a.name.split("_")[0] in EMOTION_LABELS)[::20]:
        img = cv2.imread(str(archivo))
        rect = detectar_rostro(img, cascade)
        if rect is None:
            continue
        x, y, w, h = rect
        d = backend.distribucion(img[y:y + h, x:x + w])
        salidas.append(int(np.argmax([d.probabilidades[c] for c in backend.clases])))
        verdad.append(EMOTION_LABELS.index(archivo.name.split("_")[0]))
    salidas, verdad = np.array(salidas), np.array(verdad)
    bien = np.mean(salidas == verdad)
    otras = [np.mean(np.array(p)[salidas] == verdad)
             for p in itertools.permutations(range(5)) if p != tuple(range(5))]
    assert bien > max(otras) + 0.03


def test_feliz_y_sorprendido_son_las_que_mejor_reconoce_en_fer2013():
    """Un ancla del mapeo de FER-2013 que no sale de este código: en FER-2013
    alegría y sorpresa son las expresiones más fáciles, y así salen en las
    matrices de confusión publicadas. Si el mapeo por nombre estuviera
    cambiado, el recall alto aparecería en otras filas."""
    recall = {c: v["recall"] for c, v in
              leer_metadatos(ruta_metadatos(ENTRENADO))["metricas"]["prueba"]["por_clase"].items()}
    dos_mejores = sorted(recall, key=recall.get, reverse=True)[:2]
    assert set(dos_mejores) == {"feliz", "sorprendido"}


@requiere_entrenado
@pytest.mark.skipif(not (PROJECT_ROOT / "emotions_dataset").is_dir(),
                    reason="emotions_dataset/ no está (vive fuera de git)")
def test_las_etiquetas_del_operador_fijan_feliz_y_descartan_las_rotaciones():
    """El ancla del P6 —las etiquetas que puso el operador a sus fotos—, en lo
    que puede fijar con este modelo.

    No puede fijar el orden entero: el modelo de FER-2013 confunde en la cara
    del operador triste, neutral y enojado, y permutarlas entre sí apenas
    cambia los aciertos (medido el 2026-10-20: con 183 caras, una permutación
    cuadra mejor que la buena; con 746, la buena gana por 1.2 puntos — H50).
    Lo que sí fija, con margen: que ``feliz`` es feliz, y que el orden no está
    rotado (las cuatro rotaciones se hunden).
    """
    from telmeow_teleop.emotion_classifier import detectar_rostro

    backend = KerasBackend(ENTRENADO)
    cascade = cargar_cascade()
    archivos = sorted(a for a in (PROJECT_ROOT / "emotions_dataset").glob("*.jpg")
                      if a.name.split("_")[0] in EMOTION_LABELS)[::20]
    salidas, verdad = [], []
    for archivo in archivos:
        img = cv2.imread(str(archivo))
        rect = detectar_rostro(img, cascade)
        if rect is None:
            continue
        x, y, w, h = rect
        d = backend.distribucion(img[y:y + h, x:x + w])
        salidas.append(int(np.argmax([d.probabilidades[c] for c in backend.clases])))
        verdad.append(EMOTION_LABELS.index(archivo.name.split("_")[0]))
    salidas, verdad = np.array(salidas), np.array(verdad)
    assert len(salidas) >= 300

    bien = np.mean(salidas == verdad)
    rotadas = [np.mean(np.roll(np.arange(5), k)[salidas] == verdad) for k in range(1, 5)]
    assert bien > max(rotadas) + 0.1
    feliz = EMOTION_LABELS.index("feliz")
    assert np.mean(salidas[verdad == feliz] == feliz) > 0.7
