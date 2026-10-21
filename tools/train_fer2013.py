"""Entrenamiento del clasificador de emoción con FER-2013 (PROMPT 8).

Por qué
-------
El modelo propio (``emotion_model.keras``) se entrenó con 2000 frames
CONSECUTIVOS por emoción de una sola persona, repartidos al azar entre
entrenamiento y prueba (``legacy/Training Emotion Recognition.py``): frames
casi idénticos a los dos lados de la partición —fuga de datos, bug 13— y sin
``random_state`` —bug 14—. Su accuracy mide memorización. Aquí se entrena la
misma red, con BatchNormalization y aumentación, sobre FER-2013, que tiene
miles de caras distintas y una partición oficial; el dataset propio queda para
probar en condiciones de operación (P9, configuración D).

Los datos
---------
El ``fer2013.tar.gz`` ORIGINAL de la competición de ICML 2013 («Challenges in
Representation Learning: Facial Expression Recognition», en Kaggle), de una
copia en el Hub de HuggingFace fijada por REVISIÓN y por SHA-256 (``FUENTE``).
Dentro va ``fer2013.csv``: una fila por imagen con su clase, los 2304 píxeles
en texto y la partición a la que pertenece (``Usage``). Este y no otro, por dos
razones:

- **Son los píxeles originales, sin pérdida.** Las copias en carpetas de
  imágenes (``train/angry/*.jpg``) los recomprimen en JPEG.
- **No añade ninguna dependencia.** La biblioteca ``datasets``, y las copias en
  parquet, traen pyarrow y pandas al entorno de operación, que es la trampa del
  H45: numpy y protobuf están fijados por TensorFlow y MediaPipe. Este CSV se
  lee con la biblioteca estándar y numpy, y el ``.tar.gz`` se lee sin extraer
  nada a disco.

Al cargarlo se comprueban sus cifras documentadas (``CANONICO``): 35 887
imágenes, 28 709 / 3 589 / 3 589 por partición y las siete clases de Training.
Una copia que no cuadre se rechaza, porque sus cifras no serían comparables con
las publicadas. Con ``--archivo`` se puede usar otra copia (la de Kaggle, el
``fer2013.csv`` suelto o ``icml_face_data.csv``) y pasa por la misma
comprobación.

La partición: la oficial, sin rehacerla
---------------------------------------
- **Prueba = PublicTest + PrivateTest**, 7 178 imágenes (6 043 con las cinco
  clases del robot), que no se miran hasta el final. Cada mitad se reporta
  además por separado: la literatura suele citar PrivateTest.
- **Validación = el 10 % de Training**, por clase, con semilla, y **agrupado
  por imagen idéntica**: FER-2013 tiene imágenes repetidas, y dos copias de la
  misma a los dos lados de una partición son la fuga del bug 13 en pequeño.
  La validación solo decide cuándo parar (EarlyStopping) y cuándo bajar la tasa
  de aprendizaje (ReduceLROnPlateau).
- Se cuentan las imágenes de la prueba que están repetidas en Training —la
  partición oficial no lo evita—, y se da también la accuracy sin ellas.

Las clases
----------
FER-2013 tiene siete; el robot, cinco. ``disgust`` y ``fear`` se DESCARTAN, no
se fusionan con otra (P8, punto 2), y el resto se mapea **por nombre**
(``A_ROBOT``), nunca por posición: el orden de las salidas de la red es el de
``config.EMOTION_LABELS``, y se escribe en el JSON de metadatos que lee el
``KerasBackend`` del P6.

Lo que no cambia respecto al modelo propio
------------------------------------------
La entrada: 48×48×1 en [0, 1], la de ``preprocesar_48``. Las caras de FER-2013
ya vienen recortadas, en gris y a 48×48, así que ``preprocesar_48`` las deja tal
cual salvo la división entre 255 (``a_tensor``, y una prueba exige que coincidan
bit a bit). En operación la cara sale del Haar con su encuadre y a ~120 px
antes de reducirse: esa diferencia es parte del domain gap que mide el P9.

La aumentación incluye el volteo horizontal, así que el entrenamiento no
favorece ninguna orientación. Pero eso no hace simétrica a la red aprendida:
medido sobre la prueba de FER-2013, acierta lo mismo con las caras volteadas
(55.8 frente a 56.3 %) y aun así **cambia de predicción en el 18 % de ellas**.
Con miles de personas se compensa; con una sola, no (P8). El clasificador
deshace el espejo del pipeline igualmente, y los dos modelos ven la cara en la
orientación de la cámara.

Lo que se espera
----------------
La precisión humana en FER-2013 es de 65±5 % y los mejores trabajos rondan el
75 %, ambas cifras con las SIETE clases. Con cinco se quitan las dos más
difíciles —miedo, que se confunde con sorpresa y tristeza, y asco, con 436
imágenes—, así que se espera algo más que con siete. **Más del 90 % indica un
error de partición**: el script lo dice, lo anota en los metadatos y termina
con código 2. No es un éxito.

Uso::

    python tools/train_fer2013.py                  # descarga si hace falta y entrena (CPU)
    python tools/train_fer2013.py --archivo fer2013.csv           # otra copia local
    python tools/train_fer2013.py --limite 3000 --epocas 2 --salida ensayo.keras
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import platform
import sys
import tarfile
import time
import urllib.error
import urllib.request
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telmeow_teleop.config import EMOTION_LABELS, PROJECT_ROOT  # noqa: E402

#: Lado de las imágenes de FER-2013, y de la entrada de la red.
LADO = 48


@dataclass(frozen=True)
class Fuente:
    """De dónde se descarga FER-2013, fijado para que siempre sea el mismo archivo."""

    repositorio: str
    revision: str
    archivo: str
    sha256: str
    bytes: int

    @property
    def url(self) -> str:
        return (
            f"https://huggingface.co/datasets/{self.repositorio}/resolve/"
            f"{self.revision}/{self.archivo}"
        )


#: La copia del ``fer2013.tar.gz`` original. La revisión es un commit del
#: repositorio del Hub, así que el archivo no puede cambiar bajo el mismo
#: enlace; el SHA-256 es el que publica el propio Hub (oid de LFS) y se
#: comprueba al descargar.
FUENTE = Fuente(
    repositorio="DerrickUnleashed/FER-2013",
    revision="5b1d92d29dea477a4c13af5f928b2baee3744951",
    archivo="fer2013.tar.gz",
    sha256="bdc255c109a1bbf994e6a7dd02a6f4566eb21beaece7643cd988166ee3527a44",
    bytes=96_433_867,
)

#: Las siete clases de FER-2013 en el orden de su columna ``emotion``, que es
#: el de la documentación del dataset: 0=Angry, 1=Disgust, 2=Fear, 3=Happy,
#: 4=Sad, 5=Surprise, 6=Neutral.
CLASES_FER: tuple[str, ...] = ("Angry", "Disgust", "Fear", "Happy", "Sad", "Surprise", "Neutral")

#: Clase de FER-2013 → expresión del robot. Por nombre: ver el docstring.
A_ROBOT: dict[str, str] = {
    "Happy": "feliz",
    "Sad": "triste",
    "Surprise": "sorprendido",
    "Neutral": "neutral",
    "Angry": "enojado",
}

#: Las que el robot no tiene. Se descartan, no se fusionan (P8, punto 2).
DESCARTADAS: tuple[str, ...] = ("Disgust", "Fear")

#: Valores de la columna ``Usage``. La prueba oficial son las dos últimas.
USOS: tuple[str, ...] = ("Training", "PublicTest", "PrivateTest")
PRUEBA: tuple[str, ...] = ("PublicTest", "PrivateTest")

#: Las cifras documentadas de FER-2013. Si una copia no las da, no es la misma.
CANONICO: dict = {
    "total": 35_887,
    "por_uso": {"Training": 28_709, "PublicTest": 3_589, "PrivateTest": 3_589},
    "training_por_clase": {
        "Angry": 3_995, "Disgust": 436, "Fear": 4_097, "Happy": 7_215,
        "Sad": 4_830, "Surprise": 3_171, "Neutral": 4_965,
    },
}

# -- Hiperparámetros por defecto: cada uno es una opción de la línea de comandos.

SEMILLA = 2013
FRACCION_VALIDACION = 0.10
EPOCAS = 150
LOTE = 64
TASA = 1e-3
#: EarlyStopping: épocas sin mejorar ``val_loss`` antes de parar. Restaura los
#: pesos de la mejor.
PACIENCIA = 20
#: ReduceLROnPlateau: épocas sin mejorar antes de multiplicar la tasa por
#: ``FACTOR_TASA``. Menor que ``PACIENCIA``, para que la tasa baje un par de
#: veces antes de rendirse.
#:
#: Con 4 (y 10 de parada), el primer entrenamiento recortó la tasa mientras la
#: pérdida de ENTRENAMIENTO aún bajaba: con BatchNormalization, ``val_loss``
#: oscila ±0.1 de una época a otra, y cuatro épocas sin un mínimo nuevo pasan
#: por azar. Con 8 (y 20) la validación mejoró —``val_loss`` 1.0456 frente a
#: 1.0551— y es el modelo elegido. Las dos ejecuciones están en el plan (P8).
PACIENCIA_TASA = 8
FACTOR_TASA = 0.5
TASA_MINIMA = 1e-5
#: Aumentación del enunciado: volteo horizontal, rotación ±10° y zoom ±10 %.
ROTACION_GRADOS = 10.0
ZOOM = 0.10
#: Por encima, la accuracy en FER-2013 delata un error de partición.
UMBRAL_SOSPECHA = 0.90


class DatasetNoCanonico(ValueError):
    """La copia de FER-2013 no tiene las cifras documentadas del dataset."""


class GuardadoInvalido(RuntimeError):
    """El modelo guardado no se comporta como el que se acaba de entrenar."""


# --------------------------------------------------------------------------
# Descarga y lectura
# --------------------------------------------------------------------------


def sha256_de(ruta: Path, bloque: int = 1 << 20) -> str:
    huella = hashlib.sha256()
    with open(ruta, "rb") as archivo:
        while datos := archivo.read(bloque):
            huella.update(datos)
    return huella.hexdigest()


def descargar(carpeta: Path, fuente: Fuente = FUENTE, *, intentos: int = 4,
              abrir=urllib.request.urlopen, espera_s: float = 5.0) -> Path:
    """El archivo de ``fuente`` dentro de ``carpeta``, descargándolo si no está.

    Lo que hay en disco se reutiliza solo si su SHA-256 es el esperado. Uno con
    otra huella no se pisa: se avisa, porque puede ser una copia que alguien
    puso a propósito. La descarga va a un ``.parcial`` y solo se renombra
    cuando el tamaño y la huella cuadran, así que un corte a medias nunca deja
    un archivo que parezca bueno.

    Se reintenta: el CDN del Hub a veces tarda más de 20 s en aceptar la
    conexión (pasó el 2026-10-20).
    """
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / fuente.archivo
    if destino.is_file():
        huella = sha256_de(destino)
        if huella != fuente.sha256:
            raise DatasetNoCanonico(
                f"{destino} existe pero su SHA-256 es {huella}, no {fuente.sha256}. "
                f"No se pisa: bórralo si es una descarga rota, o usa --archivo."
            )
        return destino

    parcial = destino.with_name(destino.name + ".parcial")
    ultimo_error: BaseException | None = None
    for intento in range(1, intentos + 1):
        try:
            print(f"Descargando {fuente.url} (intento {intento}/{intentos})...")
            huella = hashlib.sha256()
            total = 0
            with abrir(fuente.url, timeout=60) as respuesta, open(parcial, "wb") as salida:
                while datos := respuesta.read(1 << 20):
                    salida.write(datos)
                    huella.update(datos)
                    total += len(datos)
                    if total % (16 << 20) < len(datos):
                        print(f"  {total / 2**20:5.0f} / {fuente.bytes / 2**20:.0f} MiB")
            if total != fuente.bytes or huella.hexdigest() != fuente.sha256:
                parcial.unlink(missing_ok=True)
                raise DatasetNoCanonico(
                    f"Se descargaron {total} bytes con SHA-256 {huella.hexdigest()}; "
                    f"se esperaban {fuente.bytes} con {fuente.sha256}."
                )
            parcial.replace(destino)
            return destino
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            ultimo_error = exc
            parcial.unlink(missing_ok=True)
            if intento < intentos:
                time.sleep(espera_s * intento)
    raise ConnectionError(
        f"No se pudo descargar {fuente.url} tras {intentos} intentos ({ultimo_error}). "
        f"Se puede bajar a mano y pasar con --archivo."
    ) from ultimo_error


@dataclass(frozen=True)
class Fer2013:
    """FER-2013 en memoria, en el orden del CSV."""

    pixeles: np.ndarray      # (N, 48, 48) uint8
    clases: np.ndarray       # (N,) índice en CLASES_FER
    usos: np.ndarray         # (N,) uno de USOS
    sha256_csv: str          # del CSV, para comparar copias con distinto empaquetado

    def __len__(self) -> int:
        return len(self.clases)


class _ConHuella(io.RawIOBase):
    """Lee de ``origen`` y va calculando el SHA-256 de lo leído."""

    def __init__(self, origen) -> None:
        self._origen = origen
        self.huella = hashlib.sha256()

    def readable(self) -> bool:
        return True

    def readinto(self, destino) -> int:
        datos = self._origen.read(len(destino))
        destino[: len(datos)] = datos
        self.huella.update(datos)
        return len(datos)


@contextmanager
def _abrir_csv(ruta: Path):
    """El CSV en binario, suelto o dentro de un ``.tar.gz``, sin extraer nada."""
    ruta = Path(ruta)
    if ruta.name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(ruta, "r:gz") as tar:
            csvs = [m for m in tar.getmembers() if m.isfile() and m.name.endswith(".csv")]
            if len(csvs) != 1:
                raise ValueError(
                    f"{ruta.name} debería tener un solo .csv y tiene {[m.name for m in csvs]}"
                )
            yield tar.extractfile(csvs[0])
    else:
        with open(ruta, "rb") as archivo:
            yield archivo


def leer_fer2013(ruta: Path) -> Fer2013:
    """Lee el CSV de FER-2013 (``emotion``, ``pixels``, ``Usage``).

    Admite los dos encabezados que circulan: el de ``fer2013.csv``
    (``emotion,pixels,Usage``) y el de ``icml_face_data.csv``, con los nombres
    en otro orden y con espacios delante. Cada fila se valida —2304 valores
    entre 0 y 255, clase de 0 a 6, partición conocida— y la primera que no
    cuadre detiene la lectura con su número de línea.
    """
    pixeles, clases, usos = [], [], []
    with _abrir_csv(ruta) as binario:
        lector = _ConHuella(binario)
        texto = io.TextIOWrapper(io.BufferedReader(lector), encoding="ascii", newline="")
        filas = csv.reader(texto)
        cabecera = [c.strip() for c in next(filas)]
        faltan = {"emotion", "pixels", "Usage"} - set(cabecera)
        if faltan:
            raise ValueError(f"{Path(ruta).name}: faltan las columnas {sorted(faltan)}")
        i_emo, i_pix, i_uso = (cabecera.index(c) for c in ("emotion", "pixels", "Usage"))
        for linea, fila in enumerate(filas, start=2):
            clase, uso = int(fila[i_emo]), fila[i_uso].strip()
            valores = np.array(fila[i_pix].split(), dtype=np.int16)
            if valores.size != LADO * LADO or valores.min() < 0 or valores.max() > 255:
                raise ValueError(f"línea {linea}: los píxeles no son {LADO}×{LADO} valores de 0 a 255")
            if not 0 <= clase < len(CLASES_FER):
                raise ValueError(f"línea {linea}: clase {clase} fuera de 0..{len(CLASES_FER) - 1}")
            if uso not in USOS:
                raise ValueError(f"línea {linea}: partición desconocida {uso!r}")
            pixeles.append(valores.astype(np.uint8).reshape(LADO, LADO))
            clases.append(clase)
            usos.append(uso)
        huella = lector.huella.hexdigest()
    return Fer2013(
        pixeles=np.stack(pixeles) if pixeles else np.zeros((0, LADO, LADO), np.uint8),
        clases=np.array(clases, dtype=np.int64),
        usos=np.array(usos),
        sha256_csv=huella,
    )


def comprobar_canonico(datos: Fer2013, canonico: dict = CANONICO) -> None:
    """Exige las cifras documentadas de FER-2013; si no, ``DatasetNoCanonico``."""
    errores = []
    if len(datos) != canonico["total"]:
        errores.append(f"{len(datos)} imágenes en vez de {canonico['total']}")
    por_uso = Counter(datos.usos.tolist())
    for uso, esperado in canonico["por_uso"].items():
        if por_uso[uso] != esperado:
            errores.append(f"{uso}: {por_uso[uso]} imágenes en vez de {esperado}")
    por_clase = Counter(CLASES_FER[c] for c in datos.clases[datos.usos == "Training"])
    for clase, esperado in canonico["training_por_clase"].items():
        if por_clase[clase] != esperado:
            errores.append(f"Training/{clase}: {por_clase[clase]} en vez de {esperado}")
    if errores:
        raise DatasetNoCanonico(
            "La copia de FER-2013 no es la documentada:\n  " + "\n  ".join(errores)
        )


# --------------------------------------------------------------------------
# Clases, partición y pesos
# --------------------------------------------------------------------------


def a_indices_robot(clases_fer: np.ndarray) -> np.ndarray:
    """Índice de FER-2013 → índice en ``EMOTION_LABELS``; −1 si se descarta.

    Pasa por los NOMBRES (``CLASES_FER`` → ``A_ROBOT`` → ``EMOTION_LABELS``):
    ninguna posición de un convenio se toma por la de otro.
    """
    tabla = np.full(len(CLASES_FER), -1, dtype=np.int64)
    for i, nombre in enumerate(CLASES_FER):
        if nombre in A_ROBOT:
            tabla[i] = EMOTION_LABELS.index(A_ROBOT[nombre])
    return tabla[np.asarray(clases_fer, dtype=np.int64)]


def huellas(pixeles: np.ndarray) -> list[bytes]:
    """Una huella por imagen: dos imágenes con los mismos píxeles dan la misma."""
    return [hashlib.blake2b(np.ascontiguousarray(p).tobytes(), digest_size=16).digest()
            for p in pixeles]


def particion_validacion(etiquetas: np.ndarray, grupos: list, fraccion: float,
                         semilla: int) -> np.ndarray:
    """Máscara de validación: ``fraccion`` de cada clase, por grupos enteros.

    ``grupos`` dice qué imágenes son la misma (``huellas``); las de un grupo van
    juntas, todas a validación o ninguna, y así ninguna imagen está a los dos
    lados. Un grupo cuenta para la clase de su primera aparición: la misma
    imagen con dos etiquetas distintas pasa en FER-2013, y es raro.

    Determinista: misma semilla, misma partición. El cupo de cada clase es el
    redondeo de ``fraccion`` por sus imágenes; con grupos de más de una se
    puede pasar en lo que mida el último grupo.
    """
    if not 0.0 < fraccion < 1.0:
        raise ValueError(f"la fracción de validación debe estar en (0, 1): {fraccion}")
    etiquetas = np.asarray(etiquetas)
    if len(grupos) != len(etiquetas):
        raise ValueError("hace falta un grupo por imagen")
    miembros: dict = {}
    for i, grupo in enumerate(grupos):
        miembros.setdefault(grupo, []).append(i)
    claves = list(miembros)              # orden de primera aparición: no depende de hash()
    clase_de = [int(etiquetas[miembros[g][0]]) for g in claves]
    cupo = {c: round(fraccion * n) for c, n in Counter(etiquetas.tolist()).items()}
    asignadas: Counter = Counter()
    validacion = np.zeros(len(etiquetas), dtype=bool)
    for j in np.random.default_rng(semilla).permutation(len(claves)):
        clase = clase_de[j]
        if asignadas[clase] < cupo[clase]:
            indices = miembros[claves[j]]
            validacion[indices] = True
            asignadas[clase] += len(indices)
    return validacion


def pesos_de_clase(etiquetas: np.ndarray, n_clases: int) -> dict[int, float]:
    """``n / (k · n_c)``: el criterio «balanced» de scikit-learn.

    Cada clase pesa en la pérdida en proporción inversa a sus imágenes, así que
    en total todas pesan lo mismo, y una clase con la media de imágenes pesa
    1. No duplica ni inventa imágenes (P8, punto 3: nada de sobremuestreo).
    """
    cuenta = np.bincount(np.asarray(etiquetas, dtype=np.int64), minlength=n_clases)
    if len(cuenta) != n_clases or (cuenta == 0).any():
        raise ValueError(f"cada una de las {n_clases} clases necesita imágenes: {cuenta.tolist()}")
    return {c: float(len(etiquetas) / (n_clases * cuenta[c])) for c in range(n_clases)}


@dataclass(frozen=True)
class Particion:
    """Índices sobre ``Fer2013``, ya filtrados a las cinco clases del robot."""

    entrenamiento: np.ndarray
    validacion: np.ndarray
    prueba: np.ndarray
    etiquetas: np.ndarray        # índice en EMOTION_LABELS para CADA imagen de datos; −1 descartada
    repetida: np.ndarray         # por imagen de ``prueba``: sus píxeles están en Training
    repetidas_en_training: int   # imágenes de Training (5 clases) con otra idéntica en Training

    def huella(self) -> str:
        """SHA-256 de los tres conjuntos de índices: identifica la partición."""
        h = hashlib.sha256()
        for indices in (self.entrenamiento, self.validacion, self.prueba):
            h.update(np.asarray(indices, dtype=np.int64).tobytes())
            h.update(b"|")
        return h.hexdigest()


def preparar(datos: Fer2013, *, fraccion: float = FRACCION_VALIDACION,
             semilla: int = SEMILLA, limite: int | None = None) -> Particion:
    """Filtra a las cinco clases, aparta la validación y marca los repetidos.

    ``limite`` toma al azar (con la semilla) ese número de imágenes de
    entrenamiento, y una parte proporcional de validación y de prueba: es para
    ensayos rápidos, nunca para el modelo de verdad.
    """
    etiquetas = a_indices_robot(datos.clases)
    nuestras = etiquetas >= 0
    training = np.flatnonzero(nuestras & (datos.usos == "Training"))
    prueba = np.flatnonzero(nuestras & np.isin(datos.usos, PRUEBA))

    huellas_training = huellas(datos.pixeles[training])
    es_val = particion_validacion(etiquetas[training], huellas_training, fraccion, semilla)
    vistas = set(huellas_training)
    repetida = np.array([h in vistas for h in huellas(datos.pixeles[prueba])], dtype=bool)
    internas = len(huellas_training) - len(vistas)

    entrenamiento, validacion = training[~es_val], training[es_val]
    if limite is not None:
        rng = np.random.default_rng(semilla)
        proporcion = min(1.0, limite / len(entrenamiento))

        def submuestra(indices, mascara=None):
            n = max(len(EMOTION_LABELS), round(proporcion * len(indices)))
            elegidos = np.sort(rng.choice(len(indices), size=min(n, len(indices)), replace=False))
            return indices[elegidos], (None if mascara is None else mascara[elegidos])

        entrenamiento, _ = submuestra(entrenamiento)
        validacion, _ = submuestra(validacion)
        prueba, repetida = submuestra(prueba, repetida)
    return Particion(entrenamiento, validacion, prueba, etiquetas, repetida, internas)


def a_tensor(pixeles: np.ndarray) -> np.ndarray:
    """``(N, 48, 48)`` uint8 → ``(N, 48, 48, 1)`` float32 en [0, 1].

    Es lo que ``preprocesar_48`` le hace a una cara que ya está en gris y a
    48×48 —el ``resize`` no cambia nada y la división se hace en float64 antes
    de pasar a float32—, escrito para todas las imágenes a la vez. Una prueba
    exige que den el mismo tensor bit a bit: si no, el modelo se entrenaría con
    una entrada y operaría con otra.
    """
    pixeles = np.asarray(pixeles)
    if pixeles.dtype != np.uint8 or pixeles.ndim != 3 or pixeles.shape[1:] != (LADO, LADO):
        raise ValueError(f"se esperaban imágenes uint8 de {LADO}×{LADO}; llegó {pixeles.dtype} {pixeles.shape}")
    return (pixeles / 255.0).astype(np.float32)[..., np.newaxis]


# --------------------------------------------------------------------------
# La red
# --------------------------------------------------------------------------


def construir_modelo(n_clases: int = len(EMOTION_LABELS)):
    """La CNN del legacy con una BatchNormalization tras cada convolución.

    Legacy: tres bloques Conv(3×3, ReLU) + MaxPool(2×2) de 32, 64 y 128
    filtros, Flatten, Dense 128 ReLU, Dropout 0.5 y softmax. Se conserva entera
    —mismos filtros, mismo relleno ``valid``, misma cabeza— y solo se añade la
    normalización entre cada convolución y su ReLU. La convolución va sin sesgo
    porque la normalización lo resta: sería un parámetro que no hace nada.

    Termina en softmax, que el ``KerasBackend`` exige (P8, punto 10).
    """
    import keras
    from keras import layers

    capas = [keras.Input((LADO, LADO, 1))]
    for filtros in (32, 64, 128):
        capas += [
            layers.Conv2D(filtros, (3, 3), use_bias=False),
            layers.BatchNormalization(),
            layers.Activation("relu"),
            layers.MaxPooling2D((2, 2)),
        ]
    capas += [
        layers.Flatten(),
        layers.Dense(128, activation="relu"),
        layers.Dropout(0.5),
        layers.Dense(n_clases, activation="softmax"),
    ]
    return keras.Sequential(capas, name="emocion_fer2013")


def construir_aumentacion(semilla: int = SEMILLA):
    """Volteo horizontal, rotación ±10° y zoom ±10 %, solo al entrenar.

    Va FUERA del modelo que se guarda: se entrena con una envoltura
    ``aumentación → modelo`` y se guarda solo el modelo, que así no lleva capas
    aleatorias dentro. Sin entrenar, esas capas no hacen nada, pero el
    ``KerasBackend`` no tiene por qué cargarlas.

    ``RandomRotation`` mide el ángulo en fracciones de vuelta: ±10° son
    ±10/360.
    """
    import keras
    from keras import layers

    return keras.Sequential([
        layers.RandomFlip("horizontal", seed=semilla),
        layers.RandomRotation(ROTACION_GRADOS / 360.0, fill_mode="reflect", seed=semilla + 1),
        layers.RandomZoom(ZOOM, fill_mode="reflect", seed=semilla + 2),
    ], name="aumentacion")


def fijar_semillas(semilla: int) -> None:
    """Python, numpy y TensorFlow, y operaciones deterministas (P8, punto 5).

    ``set_random_seed`` fija las tres semillas y el generador de Keras; con
    ``enable_op_determinism`` TensorFlow además elige las implementaciones
    deterministas de cada operación. Es lo que permite que dos ejecuciones con
    la misma semilla den los mismos pesos, no solo parecidos.
    """
    import keras
    import tensorflow as tf

    keras.utils.set_random_seed(semilla)
    tf.config.experimental.enable_op_determinism()


def entrenar(modelo, x_ent, y_ent, x_val, y_val, *, pesos: dict[int, float] | None,
             semilla: int = SEMILLA, epocas: int = EPOCAS, lote: int = LOTE,
             tasa: float = TASA, paciencia: int = PACIENCIA,
             paciencia_tasa: int = PACIENCIA_TASA, historial: Path | None = None,
             verbose: int = 2) -> dict:
    """Entrena ``modelo`` en su sitio y devuelve el resumen del entrenamiento.

    Al terminar, ``modelo`` tiene los pesos de la época con menor ``val_loss``
    (``restore_best_weights``), pare antes o no. ``historial`` recibe una fila
    por época con las pérdidas, las accuracies, la tasa de aprendizaje y lo que
    tardó.

    La validación se evalúa SIN los pesos de clase: ``val_loss`` es la pérdida
    de la distribución de clases tal como viene, la misma que tendrá la prueba.
    """
    import keras

    class Cronometro(keras.callbacks.Callback):
        def on_epoch_begin(self, epoch, logs=None):
            self._t0 = time.perf_counter()

        def on_epoch_end(self, epoch, logs=None):
            if logs is not None:
                logs["segundos"] = time.perf_counter() - self._t0

    entrada = keras.Input((LADO, LADO, 1))
    envoltura = keras.Model(entrada, modelo(construir_aumentacion(semilla)(entrada)),
                            name="entrenamiento")
    envoltura.compile(optimizer=keras.optimizers.Adam(tasa),
                      loss="sparse_categorical_crossentropy", metrics=["accuracy"])
    parada = keras.callbacks.EarlyStopping(monitor="val_loss", patience=paciencia,
                                           restore_best_weights=True)
    # El orden importa: CSVLogger escribe lo que los anteriores dejaron en logs.
    callbacks = [
        Cronometro(),
        keras.callbacks.ReduceLROnPlateau(monitor="val_loss", factor=FACTOR_TASA,
                                          patience=paciencia_tasa, min_lr=TASA_MINIMA),
        parada,
    ]
    if historial is not None:
        callbacks.append(keras.callbacks.CSVLogger(str(historial)))

    inicio = time.perf_counter()
    resultado = envoltura.fit(
        x_ent, y_ent, validation_data=(x_val, y_val), epochs=epocas, batch_size=lote,
        class_weight=pesos, shuffle=True, callbacks=callbacks, verbose=verbose,
    )
    h = resultado.history
    mejor = int(np.argmin(h["val_loss"]))
    return {
        "epocas": len(h["loss"]),
        "mejor_epoca": mejor + 1,
        "paro_antes": parada.stopped_epoch > 0,
        "val_loss": float(h["val_loss"][mejor]),
        "val_accuracy": float(h["val_accuracy"][mejor]),
        "tasa_final": float(h["learning_rate"][-1]) if "learning_rate" in h else tasa,
        "segundos": time.perf_counter() - inicio,
    }


def predecir(modelo, x: np.ndarray, lote: int = 256) -> np.ndarray:
    """Probabilidades de un conjunto entero. ``predict`` en bloque está bien
    aquí: el bug 12 era llamarlo una vez por frame."""
    return np.asarray(modelo.predict(x, batch_size=lote, verbose=0))


def simetria(modelo, x: np.ndarray, y: np.ndarray) -> dict:
    """Lo que cambia al voltear las caras: accuracy de las dos maneras, y en
    cuántas la predicción es la misma.

    Entrenar con volteo hace que ninguna orientación salga favorecida, pero no
    obliga a la red a ser simétrica (H49): puede acertar lo mismo en conjunto y
    cambiar de opinión en una de cada cinco caras. Con una sola persona, lo
    segundo es lo que se nota.
    """
    tal_cual = predecir(modelo, x).argmax(axis=1)
    volteada = predecir(modelo, x[:, :, ::-1, :]).argmax(axis=1)
    return {"accuracy": float(np.mean(tal_cual == y)),
            "accuracy_volteada": float(np.mean(volteada == y)),
            "misma_prediccion": float(np.mean(tal_cual == volteada))}


# --------------------------------------------------------------------------
# Métricas
# --------------------------------------------------------------------------


def metricas(verdad: np.ndarray, prediccion: np.ndarray,
             clases: tuple[str, ...] = EMOTION_LABELS) -> dict:
    """Accuracy, precisión/recall/F1 por clase, macro-F1, accuracy balanceada
    y matriz de confusión (filas = la clase de verdad, columnas = la predicha).

    Sin scikit-learn, que no está en el entorno: son cuatro divisiones, y una
    prueba las fija contra un ejemplo resuelto a mano. Una clase que nunca se
    predice tiene precisión 0, no indefinida, como hace scikit-learn con
    ``zero_division=0``.
    """
    k = len(clases)
    verdad, prediccion = np.asarray(verdad, dtype=np.int64), np.asarray(prediccion, dtype=np.int64)
    if verdad.shape != prediccion.shape or verdad.size == 0:
        raise ValueError("verdad y predicción deben tener la misma forma y no estar vacías")
    confusion = np.zeros((k, k), dtype=np.int64)
    np.add.at(confusion, (verdad, prediccion), 1)
    aciertos = np.diag(confusion).astype(float)
    soporte = confusion.sum(axis=1).astype(float)
    predichas = confusion.sum(axis=0).astype(float)
    precision = np.divide(aciertos, predichas, out=np.zeros(k), where=predichas > 0)
    recall = np.divide(aciertos, soporte, out=np.zeros(k), where=soporte > 0)
    suma = precision + recall
    f1 = np.divide(2 * precision * recall, suma, out=np.zeros(k), where=suma > 0)
    con_soporte = soporte > 0
    return {
        "n": int(verdad.size),
        "accuracy": float(aciertos.sum() / verdad.size),
        "macro_f1": float(f1[con_soporte].mean()),
        "accuracy_balanceada": float(recall[con_soporte].mean()),
        "por_clase": {
            c: {"precision": float(precision[i]), "recall": float(recall[i]),
                "f1": float(f1[i]), "soporte": int(soporte[i])}
            for i, c in enumerate(clases)
        },
        "confusion": confusion.tolist(),
    }


# --------------------------------------------------------------------------
# Guardado y verificación
# --------------------------------------------------------------------------


def ruta_historial(ruta_modelo: Path) -> Path:
    """``emotion_model_fer2013.keras`` → ``emotion_model_fer2013.historial.csv``."""
    return Path(ruta_modelo).with_suffix(".historial.csv")


def escribir_metadatos(ruta_modelo: Path, meta: dict) -> Path:
    """El JSON que lee el ``KerasBackend``, con el nombre que él espera.

    ``"clases"`` es lo único que exige; lo demás es documentación (P8, punto
    9). El nombre sale de ``emotion_classifier.ruta_metadatos``, el mismo que
    usa el backend para buscarlo.
    """
    from telmeow_teleop.emotion_classifier import leer_metadatos, ruta_metadatos

    ruta = ruta_metadatos(ruta_modelo)
    ruta.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    leer_metadatos(ruta)        # que lo que se escribió lo acepte quien lo va a leer
    return ruta


def verificar_guardado(ruta_modelo: Path, imagenes: np.ndarray,
                       probabilidades: np.ndarray, tolerancia: float = 1e-5) -> dict:
    """Carga el modelo guardado por los dos caminos que lo van a usar.

    1. ``keras.saving.load_model(ruta)`` y ``model(x, training=False)`` sobre
       ``(1, 48, 48, 1)`` da ``(1, 5)`` (P8, punto 7).
    2. El ``KerasBackend`` del P6 —con su JSON, su comprobación de softmax y
       ``preprocesar_48`` sobre los píxeles crudos— da, para cada imagen de
       ``imagenes``, las mismas probabilidades que se calcularon aquí. Es lo que
       ata el orden de clases del JSON al de la red: con otro orden, la
       etiqueta saldría cambiada aunque las probabilidades coincidieran.
    """
    import keras

    from telmeow_teleop.emotion_classifier import KerasBackend

    cargado = keras.saving.load_model(ruta_modelo)
    forma = np.asarray(cargado(np.zeros((1, LADO, LADO, 1), np.float32), training=False)).shape
    if forma != (1, len(EMOTION_LABELS)):
        raise GuardadoInvalido(f"el modelo cargado da {forma}, no (1, {len(EMOTION_LABELS)})")

    backend = KerasBackend(ruta_modelo)
    if backend.clases != EMOTION_LABELS:
        raise GuardadoInvalido(f"el backend lee las clases {backend.clases}, no {EMOTION_LABELS}")
    max_dif, distintas = 0.0, 0
    for imagen, esperadas in zip(imagenes, probabilidades):
        d = backend.distribucion(imagen)
        leidas = np.array([d.probabilidades[e] for e in EMOTION_LABELS])
        max_dif = max(max_dif, float(np.abs(leidas - esperadas).max()))
        distintas += d.etiqueta != EMOTION_LABELS[int(np.argmax(esperadas))]
    if max_dif > tolerancia or distintas:
        raise GuardadoInvalido(
            f"el KerasBackend no reproduce las predicciones del entrenamiento: "
            f"{distintas} etiquetas distintas de {len(imagenes)}, diferencia máxima {max_dif:.2e}"
        )
    return {"forma": list(forma), "imagenes": len(imagenes), "max_dif": max_dif}


# --------------------------------------------------------------------------
# Salida por pantalla
# --------------------------------------------------------------------------


def _por_clase(etiquetas: np.ndarray) -> dict[str, int]:
    cuenta = np.bincount(etiquetas, minlength=len(EMOTION_LABELS))
    return {e: int(cuenta[i]) for i, e in enumerate(EMOTION_LABELS)}


def _imprimir_metricas(titulo: str, m: dict) -> None:
    print(f"\n{titulo}: accuracy {100 * m['accuracy']:.1f} %, macro-F1 {m['macro_f1']:.3f}, "
          f"accuracy balanceada {100 * m['accuracy_balanceada']:.1f} % ({m['n']} imagenes)")
    print(f"  {'clase':12s} {'precision':>9s} {'recall':>7s} {'F1':>6s} {'imagenes':>9s}")
    for clase, v in m["por_clase"].items():
        print(f"  {clase:12s} {v['precision']:9.3f} {v['recall']:7.3f} {v['f1']:6.3f} {v['soporte']:9d}")
    print("  confusion (filas = verdad, columnas = prediccion, % de la fila):")
    print("  " + " " * 12 + "".join(f"{e[:6]:>8s}" for e in EMOTION_LABELS))
    for clase, fila in zip(EMOTION_LABELS, m["confusion"]):
        total = sum(fila) or 1
        print(f"  {clase:12s}" + "".join(f"{100 * v / total:8.1f}" for v in fila))


def _versiones() -> dict:
    import cv2
    import keras
    import tensorflow as tf

    return {"python": platform.python_version(), "tensorflow": tf.__version__,
            "keras": keras.__version__, "numpy": np.__version__, "opencv": cv2.__version__,
            "procesador": platform.processor(), "sistema": platform.platform()}


def _resumen_metricas(m: dict) -> dict:
    return {k: m[k] for k in ("n", "accuracy", "macro_f1", "accuracy_balanceada")}


def _metricas_para_guardar(m: dict) -> dict:
    """La prueba entera, con su matriz; de lo demás, el resumen."""
    return {n: (v if n in ("prueba", "simetria") else _resumen_metricas(v)) for n, v in m.items()}


def evaluar(modelo, datos: Fer2013, p: Particion) -> tuple[dict, np.ndarray]:
    """Las métricas de un modelo sobre la partición, impresas, y las
    probabilidades de la prueba.

    La prueba oficial entera, cada mitad, la prueba sin las imágenes repetidas
    en Training (H48), la validación, la simetría frente al volteo (H49), y el
    propio conjunto de entrenamiento sin aumentación: si acierta poco más ahí
    que en la prueba, a la red le falta capacidad; si acierta mucho más,
    memoriza.
    """
    y = p.etiquetas
    x_prueba = a_tensor(datos.pixeles[p.prueba])
    prob = predecir(modelo, x_prueba)
    pred, verdad = prob.argmax(axis=1), y[p.prueba]
    usos = datos.usos[p.prueba]
    m = {
        "prueba": metricas(verdad, pred),
        "public_test": metricas(verdad[usos == "PublicTest"], pred[usos == "PublicTest"]),
        "private_test": metricas(verdad[usos == "PrivateTest"], pred[usos == "PrivateTest"]),
        "prueba_sin_repetidas": metricas(verdad[~p.repetida], pred[~p.repetida]),
        "validacion": metricas(y[p.validacion],
                               predecir(modelo, a_tensor(datos.pixeles[p.validacion])).argmax(axis=1)),
        "simetria": simetria(modelo, x_prueba, verdad),
        "entrenamiento": metricas(y[p.entrenamiento], predecir(
            modelo, a_tensor(datos.pixeles[p.entrenamiento])).argmax(axis=1)),
    }
    _imprimir_metricas("PRUEBA OFICIAL (PublicTest + PrivateTest)", m["prueba"])
    for nombre, titulo in (("public_test", "PublicTest"), ("private_test", "PrivateTest"),
                           ("prueba_sin_repetidas", "Prueba sin las repetidas en Training"),
                           ("validacion", "Validacion"),
                           ("entrenamiento", "Entrenamiento, sin aumentacion")):
        print(f"  {titulo}: accuracy {100 * m[nombre]['accuracy']:.1f} %, macro-F1 "
              f"{m[nombre]['macro_f1']:.3f} ({m[nombre]['n']} imagenes)")
    s = m["simetria"]
    print(f"  Con las caras volteadas: accuracy {100 * s['accuracy_volteada']:.1f} %; la misma "
          f"prediccion en el {100 * s['misma_prediccion']:.1f} % de las caras")
    return m, prob


def reevaluar(ruta_modelo: Path, datos: Fer2013, fraccion: float) -> int:
    """Vuelve a evaluar un modelo ya entrenado, sin tocarlo.

    Rehace la partición con la semilla de sus metadatos y exige que salga la
    misma (por su huella) y que la prueba dé **exactamente** las métricas que
    se guardaron al entrenar: la inferencia es determinista, así que cualquier
    diferencia es un error de uno de los dos lados. Añade a los metadatos lo
    que falte —la simetría se midió por primera vez después de entrenar el
    primer modelo— sin cambiar nada de lo que ya había.
    """
    import keras

    from telmeow_teleop.emotion_classifier import leer_metadatos, ruta_metadatos

    meta = leer_metadatos(ruta_metadatos(ruta_modelo))
    p = preparar(datos, fraccion=meta["particion"].get("fraccion_validacion", fraccion),
                 semilla=meta["entrenamiento"]["semilla"])
    if p.huella() != meta["particion"]["huella"]:
        print("ERROR: la particion rehecha no es la del entrenamiento (otra huella).")
        return 1
    m, _ = evaluar(keras.saving.load_model(ruta_modelo), datos, p)
    guardada = meta["metricas"]["prueba"]
    if (m["prueba"]["confusion"] != guardada["confusion"]
            or m["prueba"]["accuracy"] != guardada["accuracy"]):
        print("ERROR: la prueba no da las metricas guardadas al entrenar.")
        return 1
    nuevas = {n: v for n, v in _metricas_para_guardar(m).items() if n not in meta["metricas"]}
    meta["metricas"].update(nuevas)
    escribir_metadatos(ruta_modelo, meta)
    print(f"\nMetricas reproducidas exactamente. Añadido a {ruta_metadatos(ruta_modelo).name}: "
          f"{sorted(nuevas) or 'nada'}.")
    return 0


# --------------------------------------------------------------------------
# Programa
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--datos", type=Path, default=PROJECT_ROOT / "fer2013",
                        help="carpeta donde se descarga FER-2013 (fuera de git)")
    parser.add_argument("--archivo", type=Path,
                        help="usar esta copia (.csv o .tar.gz) en vez de descargar")
    parser.add_argument("--salida", type=Path, default=PROJECT_ROOT / "emotion_model_fer2013.keras")
    parser.add_argument("--semilla", type=int, default=SEMILLA)
    parser.add_argument("--fraccion-validacion", type=float, default=FRACCION_VALIDACION)
    parser.add_argument("--epocas", type=int, default=EPOCAS)
    parser.add_argument("--lote", type=int, default=LOTE)
    parser.add_argument("--tasa", type=float, default=TASA)
    parser.add_argument("--paciencia", type=int, default=PACIENCIA)
    parser.add_argument("--paciencia-tasa", type=int, default=PACIENCIA_TASA)
    parser.add_argument("--sin-pesos", action="store_true",
                        help="entrenar sin class_weight, para medir lo que aportan")
    parser.add_argument("--limite", type=int,
                        help="ensayo: solo N imágenes de entrenamiento (y parte de las demás)")
    parser.add_argument("--evaluar", type=Path, metavar="MODELO",
                        help="no entrenar: volver a evaluar MODELO, comprobar que reproduce "
                             "sus métricas guardadas y añadir a sus metadatos lo que falte")
    args = parser.parse_args(argv)

    salida = args.salida.resolve()
    if args.limite is not None and salida == (PROJECT_ROOT / "emotion_model_fer2013.keras").resolve():
        print("Un ensayo con --limite no pisa el modelo de verdad: indica otra --salida.")
        return 1

    # -- 1. Los datos ------------------------------------------------------
    if args.archivo is not None:
        ruta = args.archivo
        origen = {"archivo": str(ruta), "sha256_archivo": sha256_de(ruta)}
    else:
        ruta = descargar(args.datos)
        origen = {"fuente": f"https://huggingface.co/datasets/{FUENTE.repositorio}",
                  "revision": FUENTE.revision, "archivo": FUENTE.archivo,
                  "sha256_archivo": FUENTE.sha256}
    print(f"Leyendo {ruta}...")
    t0 = time.perf_counter()
    datos = leer_fer2013(ruta)
    comprobar_canonico(datos)
    uniformes = int((datos.pixeles.reshape(len(datos), -1).std(axis=1) == 0).sum())
    print(f"  {len(datos)} imagenes en {time.perf_counter() - t0:.0f} s; cifras canonicas OK; "
          f"SHA-256 del CSV {datos.sha256_csv[:16]}...; {uniformes} imagenes de un solo color")
    if args.evaluar is not None:
        return reevaluar(args.evaluar.resolve(), datos, args.fraccion_validacion)

    # -- 2. La partición ---------------------------------------------------
    p = preparar(datos, fraccion=args.fraccion_validacion, semilla=args.semilla,
                 limite=args.limite)
    y = p.etiquetas
    conjuntos = {"entrenamiento": p.entrenamiento, "validacion": p.validacion, "prueba": p.prueba}
    print(f"\nPARTICION ({'ENSAYO, ' if args.limite else ''}disgust y fear descartadas)")
    print(f"  {'clase':12s}" + "".join(f"{n:>15s}" for n in conjuntos))
    for i, clase in enumerate(EMOTION_LABELS):
        print(f"  {clase:12s}" + "".join(f"{int((y[ix] == i).sum()):15d}" for ix in conjuntos.values()))
    print(f"  {'TOTAL':12s}" + "".join(f"{len(ix):15d}" for ix in conjuntos.values()))
    print(f"  Training con otra imagen identica en Training: {p.repetidas_en_training}; "
          f"prueba repetida en Training: {int(p.repetida.sum())} de {len(p.prueba)}")

    pesos = None if args.sin_pesos else pesos_de_clase(y[p.entrenamiento], len(EMOTION_LABELS))
    if pesos is not None:
        print("\nPESOS DE CLASE (n / (k * n_c), sobre el conjunto de entrenamiento)")
        for i, clase in enumerate(EMOTION_LABELS):
            print(f"  {clase:12s} {pesos[i]:.3f}  ({int((y[p.entrenamiento] == i).sum())} imagenes)")

    # -- 3. El entrenamiento -----------------------------------------------
    fijar_semillas(args.semilla)
    # Solo entrenamiento y validación: la prueba no se toca hasta evaluar().
    x = {n: a_tensor(datos.pixeles[conjuntos[n]]) for n in ("entrenamiento", "validacion")}
    modelo = construir_modelo()
    # Un resumen propio y no modelo.summary(), que dibuja con caracteres de caja
    # que una salida redirigida en Windows (cp1252) no puede escribir (H45).
    print(f"\nRED ({modelo.count_params()} parametros)")
    for capa in modelo.layers:
        print(f"  {capa.name:24s} {str(capa.output.shape):22s} {capa.count_params():8d}")
    historial = ruta_historial(salida)
    print(f"\nEntrenando (hasta {args.epocas} epocas, lote {args.lote}; historial en {historial.name})...")
    resumen = entrenar(
        modelo, x["entrenamiento"], y[p.entrenamiento], x["validacion"], y[p.validacion],
        pesos=pesos, semilla=args.semilla, epocas=args.epocas, lote=args.lote, tasa=args.tasa,
        paciencia=args.paciencia, paciencia_tasa=args.paciencia_tasa, historial=historial,
    )
    print(f"  {resumen['epocas']} epocas en {resumen['segundos'] / 60:.1f} min; la mejor, la "
          f"{resumen['mejor_epoca']} (val_loss {resumen['val_loss']:.4f}, val_accuracy "
          f"{100 * resumen['val_accuracy']:.1f} %)")

    # -- 4. La prueba, que no se ha mirado hasta ahora ----------------------
    m, prob = evaluar(modelo, datos, p)
    accuracy = m["prueba"]["accuracy"]
    advertencia = None
    if accuracy > UMBRAL_SOSPECHA:
        advertencia = (
            f"accuracy {100 * accuracy:.1f} % en la prueba de FER-2013: por encima del "
            f"{100 * UMBRAL_SOSPECHA:.0f} % es casi seguro un error de particion. NO es un resultado."
        )
        print(f"\n{'!' * 72}\nSOSPECHOSO: {advertencia}\n{'!' * 72}")

    # -- 5. Guardar y verificar por el camino del P6 ------------------------
    meta = {
        "clases": list(EMOTION_LABELS),
        "entrada": [LADO, LADO, 1],
        "preprocesamiento": (
            "FER-2013: caras ya recortadas, 48x48 en gris, /255. En operacion: el de "
            "emotion_classifier.preprocesar_48 (Haar de OpenCV, recorte, resize 48x48 "
            "sobre BGR, BGR2GRAY, /255), que con una cara de 48x48 en gris da el mismo tensor."
        ),
        "origen": "tools/train_fer2013.py (PROMPT 8)",
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "accuracy": accuracy,
        "advertencia": advertencia,
        "ensayo": args.limite is not None,
        "dataset": {
            "nombre": "FER-2013 (ICML 2013, Challenges in Representation Learning)",
            **origen,
            "sha256_csv": datos.sha256_csv,
            "imagenes": len(datos),
            "imagenes_de_un_solo_color": uniformes,
        },
        "clases_fer2013": {c: A_ROBOT.get(c) for c in CLASES_FER},
        "particion": {
            "prueba": "oficial: PublicTest + PrivateTest, intacta",
            "validacion": (f"{args.fraccion_validacion:g} de Training por clase, semilla "
                           f"{args.semilla}, agrupada por imagen identica"),
            "fraccion_validacion": args.fraccion_validacion,
            "huella": p.huella(),
            "por_clase": {n: _por_clase(y[ix]) for n, ix in conjuntos.items()},
            "training_repetidas_en_training": p.repetidas_en_training,
            "prueba_repetidas_en_training": int(p.repetida.sum()),
        },
        "pesos_de_clase": None if pesos is None else {EMOTION_LABELS[i]: w for i, w in pesos.items()},
        "entrenamiento": {
            **resumen,
            "semilla": args.semilla, "lote": args.lote, "tasa_inicial": args.tasa,
            "epocas_maximas": args.epocas, "optimizador": "adam",
            "perdida": "sparse_categorical_crossentropy",
            "early_stopping": {"monitor": "val_loss", "paciencia": args.paciencia,
                               "restaura_la_mejor": True},
            "reduce_lr": {"monitor": "val_loss", "factor": FACTOR_TASA,
                          "paciencia": args.paciencia_tasa, "minima": TASA_MINIMA},
            "aumentacion": {"volteo": "horizontal", "rotacion_grados": ROTACION_GRADOS,
                            "zoom": ZOOM, "relleno": "reflect"},
            "arquitectura": ("legacy (Conv 32/64/128 3x3 + MaxPool 2x2, Dense 128, Dropout 0.5, "
                             "softmax) con BatchNormalization tras cada convolucion"),
            "parametros": int(modelo.count_params()),
            "historial": historial.name,
        },
        "metricas": _metricas_para_guardar(m),
        "versiones": _versiones(),
    }
    modelo.save(salida)
    ruta_meta = escribir_metadatos(salida, meta)
    muestra = np.random.default_rng(args.semilla).choice(len(p.prueba), size=min(200, len(p.prueba)),
                                                         replace=False)
    try:
        meta["verificacion"] = verificar_guardado(salida, datos.pixeles[p.prueba][muestra], prob[muestra])
    except GuardadoInvalido as exc:
        meta["verificacion"] = f"FALLIDA: {exc}"
        escribir_metadatos(salida, meta)
        print(f"\nERROR: {exc}")
        return 1
    escribir_metadatos(salida, meta)
    print(f"\nGuardado {salida.name} y {ruta_meta.name}. Verificado: carga con Keras, da "
          f"{tuple(meta['verificacion']['forma'])}, y el KerasBackend reproduce las "
          f"{meta['verificacion']['imagenes']} predicciones de muestra "
          f"(diferencia maxima {meta['verificacion']['max_dif']:.1e}).")
    return 2 if advertencia else 0


if __name__ == "__main__":
    raise SystemExit(main())
