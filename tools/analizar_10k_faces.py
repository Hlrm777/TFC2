"""Qué trae el «10k US Adult Faces Database» para entrenar emociones.

    python tools/analizar_10k_faces.py [--carpeta otherDataset]

El dataset (Bainbridge, Isola y Oliva, 2013; MIT) son 10 168 fotos de caras
de adultos de EE. UU. bajadas de Google Images. Solo 2 222 («target») tienen
etiquetas: 12 personas contestaron 20 preguntas por foto, entre ellas QUÉ
emoción ve (neutral, felicidad, tristeza, enojo, miedo, sorpresa, asco), CUÁNTA
emoción hay (1-5), la edad aparente y hacia dónde mira la cara.

Este script cuenta, sobre esas 2 222: cuántas hay de cada emoción según la
mayoría de los 12, con qué acuerdo, cuántas quedan en las cinco clases del
robot, y cuántas son de personas mayores. No copia ni enseña ninguna foto: la
licencia solo permite uso de investigación y prohíbe reproducirlas.

La carpeta ``otherDataset/`` no va en git (fotos de personas, con licencia).
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parent.parent
EMOCIONES = {0: "neutral", 1: "felicidad", 2: "tristeza", 3: "enojo", 4: "miedo",
             5: "sorpresa", 6: "asco"}
#: Las cinco del clasificador del robot (concentrado es manual).
DEL_ROBOT = {0: "neutral", 1: "feliz", 2: "triste", 3: "enojado", 5: "sorprendido"}
EDADES = {1: "<20", 2: "20-30", 3: "30-45", 4: "45-60", 5: "60+"}
COL = {"edad": 0, "magnitud": 4, "emocion": 5, "cara": 7, "calidad": 15}


def cargar(carpeta: Path):
    from scipy.io import loadmat

    ruta = (carpeta / "2kattributes" / "Full Attribute Scores" / "demographic & others labels"
            / "demographic-others-labels.mat")
    m = loadmat(str(ruta))
    finales = np.vstack([c.ravel() for c in m["finalVals"].ravel()])
    acuerdo = np.vstack([c.ravel() for c in m["stdevVals"].ravel()])
    crudos = [c for c in m["allData"].ravel()]
    return finales, acuerdo, crudos


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--carpeta", type=Path, default=RAIZ / "otherDataset")
    args = p.parse_args(argv)
    finales, acuerdo, crudos = cargar(args.carpeta)
    n = len(finales)
    caras = args.carpeta / "10kfaces" / "10k US Adult Faces Database" / "Face Images"
    print(f"Fotos en total: {sum(1 for _ in caras.glob('*.jpg')) if caras.exists() else '?'}; "
          f"con etiquetas: {n}")

    emo = finales[:, COL["emocion"]].astype(int)
    acu = acuerdo[:, COL["emocion"]] / 100.0     # viene en porcentaje
    print("\nEmocion segun la mayoria de los 12 (y cuantas con acuerdo >= 50 % y >= 75 %):")
    for k, nombre in EMOCIONES.items():
        sel = emo == k
        print(f"  {nombre:10s} {sel.sum():5d}   >=50 %: {(sel & (acu >= 0.5)).sum():5d}"
              f"   >=75 %: {(sel & (acu >= 0.75)).sum():5d}")
    print(f"  acuerdo medio sobre la emocion: {acu.mean():.0%}")

    # Los votos uno por uno: cuántas fotos tienen al menos 6 de 12 votos a una emoción.
    votos = Counter()
    for c in crudos:
        col = c[:, COL["emocion"]]
        col = col[~np.isnan(col)].astype(int)
        if len(col):
            k, veces = Counter(col).most_common(1)[0]
            if veces >= 6:
                votos[int(k)] += 1
    print("\nCon al menos 6 de 12 votos a la misma emocion:",
          ", ".join(f"{EMOCIONES[k]} {v}" for k, v in sorted(votos.items())))

    mag = finales[:, COL["magnitud"]]
    print(f"\nCuanta emocion hay (1 poca - 5 mucha): media {np.nanmean(mag):.2f}; "
          f"con 3 o mas: {(mag >= 3).sum()}")

    robot = np.isin(emo, list(DEL_ROBOT))
    bueno = robot & (acu >= 0.5)
    print(f"\nEn las cinco clases del robot: {robot.sum()} (con acuerdo >= 50 %: {bueno.sum()})")
    for k, nombre in DEL_ROBOT.items():
        print(f"  {nombre:12s} {(bueno & (emo == k)).sum():5d}")

    edad = finales[:, COL["edad"]]
    mayores = edad >= 4.5
    print(f"\nEdad aparente (media de los 12): 60+ -> {mayores.sum()} fotos; 45-60 -> "
          f"{((edad >= 3.5) & (edad < 4.5)).sum()}")
    print("  emociones de las 60+ (acuerdo >= 50 %):",
          ", ".join(f"{EMOCIONES[k]} {int((mayores & (emo == k) & (acu >= 0.5)).sum())}"
                    for k in EMOCIONES))
    cara = finales[:, COL["cara"]].astype(int)
    print(f"\nCara de frente (mayoria): {(cara == 1).sum()} de {n}; calidad media "
          f"{np.nanmean(finales[:, COL['calidad']]):.1f} de 5")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
