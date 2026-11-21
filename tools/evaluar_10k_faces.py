"""Neutral contra feliz, en caras que los modelos no vieron nunca (sin entrenar nada).

    python tools/evaluar_10k_faces.py [--acuerdo 0.5] [--backend SPEC ...]

Sobre las fotos etiquetadas del «10k US Adult Faces Database» (Bainbridge,
Isola y Oliva, 2013; ``tools/analizar_10k_faces.py`` dice qué trae) que la
mayoría de sus 12 evaluadores vio neutrales o felices, con al menos
``--acuerdo`` de ellos de acuerdo. Las otras tres clases del robot tienen
20 fotos o menos: no dan para una cifra.

Cada foto pasa por lo mismo que la cara del narrador en operación: el detector
Haar de OpenCV (la cara mayor, ``minSize`` 60) y el recorte al modelo, tal
cual, sin espejo (el clasificador deshace el de la cámara, H43).

Los modelos, por defecto (``--backend`` elige otros; la sintaxis es la de
``tools/evaluar_backends.py``):

- ``keras``: el modelo propio, entrenado solo con las caras del operador.
- los dos de FER-2013 (P8): la red legacy y la VGG.
- ``onnx``: HSEmotion ``mbf_va_mtl`` (AffectNet), la mitad del de operación.
- ``blendshapes``: la regresión sobre los blendshapes FACS, en ABSOLUTO.
- la combinación de los dos últimos.

**El de operación no se puede medir aquí tal cual**: sus blendshapes son
RELATIVOS a la cara neutra del operador, que se calibra al empezar cada sesión,
y de un desconocido en una foto no hay cara neutra con que calibrar. Lo más
cercano es la combinación con los blendshapes absolutos (``--backend`` de
abajo). Es una diferencia de método que hay que decir junto a la cifra.

Qué se cuenta:
- de las neutrales, cuántas dice neutral (y qué dice cuando no);
- de las felices, cuántas dice feliz;
- la precisión equilibrada (la media de las dos, que no premia decir siempre
  «feliz», que es lo que más hay) y lo mismo en las caras que aparentan 60+;
- en cuántas fotos el Haar no encontró la cara.

No reentrena ni ajusta nada. No guarda ninguna foto ni ningún nombre: la
licencia prohíbe reproducirlas. Escribe ``reports/evaluacion_10k_faces.csv``
con las cifras agregadas.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(RAIZ), str(RAIZ / "tools")]

from analizar_10k_faces import COL, cargar  # noqa: E402

ONNX = "modelos_externos/mbf_va_mtl.onnx"
POR_DEFECTO = [
    "keras",
    "keras:emotion_model_fer2013.keras",
    "keras:emotion_model_fer2013_vgg.keras",
    f"onnx:{ONNX}",
    "blendshapes:emotion_blendshapes_facs.json",
    f"combinado:onnx:{ONNX}+blendshapes:emotion_blendshapes_facs.json",
]
NOMBRES = {
    "keras": "propio (solo el operador)",
    "keras:emotion_model_fer2013.keras": "FER-2013, red legacy",
    "keras:emotion_model_fer2013_vgg.keras": "FER-2013, red VGG",
    f"onnx:{ONNX}": "HSEmotion mbf_va_mtl",
    "blendshapes:emotion_blendshapes_facs.json": "blendshapes FACS (absolutos)",
    f"combinado:onnx:{ONNX}+blendshapes:emotion_blendshapes_facs.json":
        "HSEmotion + blendshapes absolutos",
}
CLASES = {0: "neutral", 1: "feliz"}       # código del dataset -> etiqueta del robot


def fotos(carpeta: Path, acuerdo: float) -> list[tuple[Path, str, bool]]:
    """(ruta, etiqueta, aparenta 60+) de las neutrales y felices con acuerdo."""
    finales, acuerdos, _ = cargar(carpeta)
    nombres = {}
    lista = carpeta / "2kattributes" / "Full Attribute Scores" / "target-filenames.txt"
    for linea in lista.read_text(encoding="utf-8", errors="replace").splitlines():
        numero, _, nombre = linea.strip().partition(",")
        if numero.isdigit():
            nombres[int(numero)] = nombre
    caras = carpeta / "10kfaces" / "10k US Adult Faces Database" / "Face Images"
    salida = []
    for i, (fila, acu) in enumerate(zip(finales, acuerdos), start=1):
        codigo = int(fila[COL["emocion"]])
        if codigo in CLASES and acu[COL["emocion"]] / 100.0 >= acuerdo and i in nombres:
            ruta = caras / nombres[i]
            if ruta.is_file():
                salida.append((ruta, CLASES[codigo], fila[COL["edad"]] >= 4.5))
    return salida


def leer(ruta: Path):
    import cv2

    return cv2.imdecode(np.fromfile(str(ruta), dtype=np.uint8), cv2.IMREAD_COLOR)


def evaluar(backend, lista, cascade) -> dict:
    from telmeow_teleop.emotion_classifier import detectar_rostro

    dicho = {"neutral": Counter(), "feliz": Counter()}
    mayores = {"neutral": Counter(), "feliz": Counter()}
    sin_cara = Counter()
    inicio = time.perf_counter()
    for ruta, clase, mayor in lista:
        img = leer(ruta)
        rect = None if img is None else detectar_rostro(img, cascade, min_lado=60)
        if rect is None:
            sin_cara[clase] += 1
            continue
        x, y, w, h = rect
        etiqueta = backend.predict(img[y:y + h, x:x + w]).etiqueta or "sin voto"
        dicho[clase][etiqueta] += 1
        if mayor:
            mayores[clase][etiqueta] += 1
    return {"dicho": dicho, "mayores": mayores, "sin_cara": sin_cara,
            "ms": 1000 * (time.perf_counter() - inicio) / max(1, len(lista))}


def _tasa(contador: Counter, clase: str) -> float:
    total = sum(contador.values())
    return contador[clase] / total if total else float("nan")


def resumen(r: dict) -> dict:
    n, f = r["dicho"]["neutral"], r["dicho"]["feliz"]
    mn, mf = r["mayores"]["neutral"], r["mayores"]["feliz"]
    tn, tf = _tasa(n, "neutral"), _tasa(f, "feliz")
    return {
        "neutral_bien": tn, "feliz_bien": tf, "equilibrada": (tn + tf) / 2,
        "neutral_n": sum(n.values()), "feliz_n": sum(f.values()),
        "neutral_dice": dict(n.most_common()),
        "mayores_equilibrada": (_tasa(mn, "neutral") + _tasa(mf, "feliz")) / 2,
        "mayores_n": sum(mn.values()) + sum(mf.values()),
        "sin_cara": sum(r["sin_cara"].values()), "ms": r["ms"],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--carpeta", type=Path, default=RAIZ / "otherDataset")
    p.add_argument("--acuerdo", type=float, nargs="+", default=[0.5, 0.75],
                   help="Fraccion minima de los 12 evaluadores de acuerdo (una o varias).")
    p.add_argument("--backend", action="append", help="Por defecto, los seis de arriba.")
    p.add_argument("--salida", type=Path, default=RAIZ / "reports" / "evaluacion_10k_faces.csv")
    args = p.parse_args(argv)

    import cv2

    from evaluar_backends import crear
    from telmeow_teleop.emotion_classifier import ruta_cascade

    cascade = cv2.CascadeClassifier(str(ruta_cascade()))
    filas = []
    for acuerdo in args.acuerdo:
        lista = fotos(args.carpeta, acuerdo)
        cuenta = Counter(c for _, c, _ in lista)
        print(f"\n== Acuerdo >= {acuerdo:.0%}: {cuenta['neutral']} neutrales, {cuenta['feliz']} felices "
              f"({sum(1 for *_, m in lista if m)} aparentan 60+) ==")
        print(f"{'modelo':36s} {'neutral':>8s} {'feliz':>7s} {'equilib.':>9s} {'60+':>6s} "
              f"{'sin cara':>9s} {'ms':>5s}   las neutrales, que dice")
        for spec in args.backend or POR_DEFECTO:
            try:
                backend = crear(spec)
            except Exception as exc:  # noqa: BLE001 -- un modelo que falta no para los demás
                print(f"{NOMBRES.get(spec, spec):36s} no disponible: {exc}")
                continue
            r = resumen(evaluar(backend, lista, cascade))
            dice = ", ".join(f"{k} {v}" for k, v in list(r["neutral_dice"].items())[:4])
            print(f"{NOMBRES.get(spec, spec):36s} {r['neutral_bien']:8.0%} {r['feliz_bien']:7.0%} "
                  f"{r['equilibrada']:9.0%} {r['mayores_equilibrada']:6.0%} {r['sin_cara']:9d} "
                  f"{r['ms']:5.0f}   {dice}")
            filas.append({"acuerdo_min": acuerdo, "modelo": NOMBRES.get(spec, spec), "spec": spec,
                          "neutrales": r["neutral_n"], "felices": r["feliz_n"],
                          "neutral_bien": round(r["neutral_bien"], 4),
                          "feliz_bien": round(r["feliz_bien"], 4),
                          "precision_equilibrada": round(r["equilibrada"], 4),
                          "equilibrada_60_mas": round(r["mayores_equilibrada"], 4),
                          "caras_60_mas": r["mayores_n"], "sin_cara": r["sin_cara"],
                          "ms_por_foto": round(r["ms"], 1)})
    if filas:
        args.salida.parent.mkdir(parents=True, exist_ok=True)
        with args.salida.open("w", newline="", encoding="utf-8") as f:
            escritor = csv.DictWriter(f, fieldnames=list(filas[0]), lineterminator="\n")
            escritor.writeheader()
            escritor.writerows(filas)
        print(f"\nCifras en {args.salida.relative_to(RAIZ)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
