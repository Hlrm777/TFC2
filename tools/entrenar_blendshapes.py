"""Entrena la regresión logística de ``BlendshapesBackend`` con el dataset propio.

Para qué
--------
Es el punto (a) del P6.4: clasificar la expresión con los 52 coeficientes de
la cara de MediaPipe Face Landmarker (los «blendshapes») en lugar de con los
píxeles. Cada foto del dataset (``emotions_dataset/<clase>_<n>.jpg``) pasa por
el mismo camino que una cara en operación —el Haar del clasificador, el recorte
de la caja y el Face Landmarker sobre ese recorte—, y con sus coeficientes se
entrena una regresión logística de 5 clases.

La regresión está escrita con numpy (descenso de gradiente, L2) para no
instalar scikit-learn en ``.venv``.

Validación
----------
Las fotos de cada clase salen de un vídeo, así que dos con número seguido son
casi la misma: un reparto al azar pondría gemelas a los dos lados y daría una
cifra inflada. Se valida con el **último 20 % de la numeración** de cada clase,
y aun así es la misma persona, el mismo día y el mismo cuarto: la cifra que vale
es la de la grabación del operador (``tools/evaluar_backends.py``). El JSON
final se entrena con todas.

Uso::

    python tools/entrenar_blendshapes.py --rasgos facs --salida emotion_blendshapes_facs.json
    python tools/entrenar_blendshapes.py --rasgos todos --relativo --salida ...
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from telmeow_teleop.config import EMOTION_LABELS, PROJECT_ROOT, AppConfig  # noqa: E402
from telmeow_teleop.emotion_backends import FACS, Landmarker, rasgos_simetricos  # noqa: E402

NEUTRAL = "neutral"
TASK = AppConfig().paths.face_landmarker
CACHE = PROJECT_ROOT / "modelos_externos" / "blendshapes_dataset.json"


def sha256(ruta: Path) -> str:
    return hashlib.sha256(Path(ruta).read_bytes()).hexdigest()


def clase_y_numero(ruta: Path) -> tuple[str, int] | None:
    clase, _, numero = ruta.stem.rpartition("_")
    if clase not in EMOTION_LABELS or not numero.isdigit():
        return None
    return clase, int(numero)


def extraer(dataset: Path, ruta_task: Path = TASK, cfg: AppConfig | None = None) -> list[dict]:
    """Por cada foto con cara: archivo, clase, número y blendshapes simétricos.

    El Haar y el recorte son los del clasificador (``detectar_rostro`` con el
    ``min_lado`` de la configuración y la cara mayor). Las fotos se guardaron
    sin espejo, como los recortes de las grabaciones (H43).
    """
    import cv2

    from telmeow_teleop.emotion_classifier import cargar_cascade, detectar_rostro

    cfg = cfg or AppConfig.from_env()
    cascade = cargar_cascade()
    landmarker = Landmarker(ruta_task)
    filas = []
    for ruta in sorted(Path(dataset).glob("*.jpg")):
        cn = clase_y_numero(ruta)
        if cn is None:
            continue
        imagen = cv2.imread(str(ruta))
        rect = detectar_rostro(imagen, cascade, min_lado=cfg.emotion.min_lado_rostro)
        if rect is None:
            filas.append({"archivo": ruta.name, "clase": cn[0], "numero": cn[1], "rasgos": None})
            continue
        x, y, w, h = rect
        b = landmarker.blendshapes(imagen[y:y + h, x:x + w])
        filas.append({"archivo": ruta.name, "clase": cn[0], "numero": cn[1],
                      "rasgos": None if b is None else rasgos_simetricos(b)})
    return filas


def ajustar(X: np.ndarray, y: np.ndarray, n_clases: int, *, l2: float = 1e-2,
            iteraciones: int = 3000, paso: float = 0.5) -> dict:
    """Regresión logística multinomial sobre ``X`` estandarizada."""
    media = X.mean(0)
    desv = X.std(0) + 1e-6
    Z = (X - media) / desv
    W = np.zeros((Z.shape[1], n_clases))
    b = np.zeros(n_clases)
    Y = np.eye(n_clases)[y]
    for _ in range(iteraciones):
        L = Z @ W + b
        P = np.exp(L - L.max(1, keepdims=True))
        P /= P.sum(1, keepdims=True)
        G = (P - Y) / len(y)
        W -= paso * (Z.T @ G + l2 * W)
        b -= paso * G.sum(0)
    return {"media": media, "desviacion": desv, "W": W, "b": b}


def predecir(modelo: dict, X: np.ndarray) -> np.ndarray:
    return (((X - modelo["media"]) / modelo["desviacion"]) @ modelo["W"] + modelo["b"]).argmax(1)


def particion(numeros: np.ndarray, clases: np.ndarray, fraccion: float = 0.2) -> np.ndarray:
    """``True`` en el último ``fraccion`` de la numeración de cada clase."""
    validacion = np.zeros(len(numeros), dtype=bool)
    for c in np.unique(clases):
        m = clases == c
        validacion |= m & (numeros > np.quantile(numeros[m], 1 - fraccion))
    return validacion


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT / "emotions_dataset")
    parser.add_argument("--task", type=Path, default=TASK)
    parser.add_argument("--rasgos", choices=("facs", "todos"), default="facs")
    parser.add_argument("--relativo", action="store_true",
                        help="rasgos menos la cara neutra (la del dataset al entrenar)")
    parser.add_argument("--salida", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=CACHE,
                        help="blendshapes del dataset ya extraídos (se crea si no existe)")
    args = parser.parse_args(argv)

    huella = sha256(args.task)
    cache = json.loads(args.cache.read_text(encoding="utf-8")) if args.cache.is_file() else None
    if cache is None or cache["task_sha256"] != huella:
        print(f"Extrayendo los blendshapes de {args.dataset} ...")
        cache = {"task_sha256": huella, "filas": extraer(args.dataset, args.task)}
        args.cache.parent.mkdir(parents=True, exist_ok=True)
        args.cache.write_text(json.dumps(cache), encoding="utf-8")
    filas = cache["filas"]
    con_cara = [f for f in filas if f["rasgos"]]
    print(f"{len(filas)} fotos, {len(con_cara)} con cara para el Haar y el Face Landmarker")

    rasgos = list(FACS) if args.rasgos == "facs" else sorted(con_cara[0]["rasgos"])
    X = np.array([[f["rasgos"][k] for k in rasgos] for f in con_cara])
    clases = list(EMOTION_LABELS)
    y = np.array([clases.index(f["clase"]) for f in con_cara])
    numeros = np.array([f["numero"] for f in con_cara])
    val = particion(numeros, y)
    if args.relativo:
        X = X - X[(y == clases.index(NEUTRAL)) & ~val].mean(0)

    m = ajustar(X[~val], y[~val], len(clases))
    pred = predecir(m, X[val])
    acierto = float((pred == y[val]).mean())
    print(f"validacion (ultimo 20 % de cada clase, {val.sum()} fotos): {100 * acierto:.1f} %")
    for i, c in enumerate(clases):
        cuenta = np.bincount(pred[y[val] == i], minlength=len(clases))
        print(f"  {c:12s} " + " ".join(f"{clases[k][:4]} {cuenta[k]:3d}" for k in range(len(clases))))

    if args.relativo:
        X_todo = np.array([[f["rasgos"][k] for k in rasgos] for f in con_cara])
        X = X_todo - X_todo[y == clases.index(NEUTRAL)].mean(0)
    final = ajustar(X, y, len(clases))
    salida = {
        "rasgos": rasgos,
        "clases": clases,
        "relativo": args.relativo,
        **{k: v.tolist() for k, v in final.items()},
        "origen": "tools/entrenar_blendshapes.py (P6.4)",
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "task_sha256": huella,
        "fotos": len(con_cara),
        "validacion_ultimo_20": round(acierto, 4),
    }
    args.salida.write_text(json.dumps(salida, indent=1), encoding="utf-8")
    print(f"pesos en {args.salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
