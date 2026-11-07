"""Calcula la pose «mano a la cara» del robot con su geometría real (P7.5).

El brazo del robot es rígido y sin codo, así que su mano no puede llegar a la
cara: lo que se busca es, entre las poses que admite el robot, la que deja la
bolita de la mano más cerca de la pantalla de los ojos.

La geometría es la del visor (``telmeow_viewer.html``), copiada de ``W``,
``RIG`` y ``EYE``: las mismas mallas STL, las mismas matrices y la misma
cadena (flexión -> abducción -> brazo). Lo admisible son las tablas de
``limits.py``, con un margen. Pero esas tablas solo miran el brazo contra el
tronco y el plano posterior: **el visor no comprueba el brazo contra la
cabeza**, así que aquí se mide esa holgura con las mallas, también con la
cabeza girada.

Comprueba antes de calcular que esta cinemática es la de ``retarget.direccion``
(dos caminos independientes): si no coinciden, la pose no significaría aquí lo
mismo que en el robot.

Uso::

    python tools/pose_mano_cara.py
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from telmeow_teleop.limits import Side, arm_abduccion_range_extendido  # noqa: E402
from telmeow_teleop.retarget import direccion  # noqa: E402

VISOR = PROJECT_ROOT / "telmeow_viewer"

# -- geometría del visor (W, RIG, EYE de telmeow_viewer.html) ------------------
W = {
    "cabeza": [0, -1, 0, 0.085, 1, 0, 0.00001, 14.817, -0.00001, 0, 1, 312.060, 0, 0, 0, 1],
    "brazoL": [-0.98483, 0, -0.17353, -173.33558, 0, 1, 0.00002, -16.26032,
               0.17353, 0.00002, -0.98483, 155.41715, 0, 0, 0, 1],
    "brazoR": [0.98532, 0.00001, 0.17069, 173.50906, 0, -1, 0.00007, 16.26417,
               0.17069, -0.00007, -0.98532, 155.10854, 0, 0, 0, 1],
}
RIG = {
    "neckPan": ((0.1, -8.4, 262.0), (0, 0, -1)),
    "neckTilt": ((0.1, -10.9, 215.1), (1, 0, 0)),
    "armR_bal": ((109.4, -0.1, 142.3), (0, -1, 0)),
    "armR_twist": ((63.1, -0.1, 134.4), (-0.985, 0, -0.170)),
    "armL_bal": ((-109.3, 0.1, 142.5), (0, 1, 0)),
    "armL_twist": ((-63.0, 0.1, 134.3), (-0.985, 0, 0.174)),
}
#: Centro de la pantalla de los ojos, en mundo con la cabeza en home.
PANTALLA = (0.085, 75.124, 309.809)

#: Margen frente a los topes de las tablas, en grados: las tablas van en pasos
#: de 5° y el brazo solo es simétrico a ±2 mm.
MARGEN_TABLA = 3.0

#: Holgura mínima entre el brazo y la cabeza, en mm.
HOLGURA_MIN = 10.0

#: Poses, de la más cercana a la pantalla en adelante, a las que se les mide la
#: holgura: medirla en todas tarda minutos y solo importan las primeras.
CERCANAS = 60

#: ``(pan, tilt)`` de la cabeza en los que se mide la holgura: home, sus topes y
#: el pan de operación con el tilt abajo, que es donde más se acerca a los brazos.
POSES_CABEZA = (
    (0, 0), (50, 0), (-50, 0), (90, 0), (-90, 0), (0, 42), (0, -39), (50, -39), (-50, -39),
)


def leer_stl(ruta: Path) -> np.ndarray:
    """Los vértices (sin deduplicar) de un STL binario, Nx3."""
    datos = ruta.read_bytes()
    n = int.from_bytes(datos[80:84], "little")
    if len(datos) != 84 + 50 * n:
        raise ValueError(f"{ruta.name}: no es un STL binario")
    registro = np.dtype([("n", "<3f4"), ("v", "<9f4"), ("a", "<u2")])
    return np.frombuffer(datos, dtype=registro, count=n, offset=84)["v"].reshape(-1, 3).astype(float)


def a_mundo(v: np.ndarray, matriz) -> np.ndarray:
    m = np.array(matriz, dtype=float).reshape(4, 4)
    return v @ m[:3, :3].T + m[:3, 3]


def girar(v: np.ndarray, articulacion: str, grados: float) -> np.ndarray:
    """Gira ``v`` sobre el eje de la articulación del RIG (Rodrigues)."""
    pivote, eje = RIG[articulacion]
    pivote = np.array(pivote, dtype=float)
    k = np.array(eje, dtype=float)
    k = k / np.linalg.norm(k)
    a = math.radians(grados)
    p = v - pivote
    girado = p * math.cos(a) + np.cross(k, p) * math.sin(a) + np.outer(p @ k, k) * (1 - math.cos(a))
    return girado + pivote


def fk_brazo(v_home: np.ndarray, lado: str, abduccion: float, flexion: float) -> np.ndarray:
    """Vértices de la pieza del brazo con esa pose: primero la abducción, que
    cuelga de la flexión, como los grupos anidados del visor."""
    return girar(girar(v_home, f"arm{lado}_bal", abduccion), f"arm{lado}_twist", flexion)


def fk_cabeza(v_home: np.ndarray, pan: float, tilt: float) -> np.ndarray:
    return girar(girar(v_home, "neckPan", pan), "neckTilt", tilt)


def punta_home(v_home: np.ndarray) -> np.ndarray:
    """La bolita de la mano: el vértice más lateral, como ``initBackplane``."""
    return v_home[np.argmax(np.abs(v_home[:, 0]))]


def direccion_visor(lado: str, abduccion: float, flexion: float) -> np.ndarray:
    """La dirección ``(L, S, A)`` del brazo según la cadena del visor, en el
    marco que modela ``retarget.direccion``: el lateral es el EJE DE FLEXION,
    que en el robot sube 10° hacia afuera (``arm_tracker.INCLINACION_FLEXION``),
    y el anterior es el frente, +Y."""
    _, eje = RIG[f"arm{lado}_twist"]
    k = np.array(eje, dtype=float)
    k = k / np.linalg.norm(k)
    lateral = k if (k[0] > 0) == (lado == "R") else -k      # hacia afuera
    anterior = np.array([0.0, 1.0, 0.0])
    superior = np.cross(anterior, lateral)
    if superior[2] < 0:
        superior = -superior
    pivote = np.array(RIG[f"arm{lado}_bal"][0], dtype=float)
    puntos = np.vstack([pivote, pivote + 100.0 * lateral])
    a, b = fk_brazo(puntos, lado, abduccion, flexion)
    d = (b - a) / np.linalg.norm(b - a)
    return np.array([d @ lateral, d @ superior, d @ anterior])


def candidatos(lado: str, paso: float = 2.5, margen: float = MARGEN_TABLA):
    """``(abducción, flexión)`` admisibles de la rama −1 en [−180, 0]."""
    side = Side.parse(lado)
    for f in np.arange(-180.0, 0.0 + 1e-9, paso):
        minimo, _ = arm_abduccion_range_extendido(float(f), side)
        for a in np.arange(minimo + margen, 0.0 + 1e-9, paso):
            yield float(a), float(f)


def holgura_cabeza(brazo: np.ndarray, arbol: cKDTree) -> float:
    return float(arbol.query(brazo, k=1)[0].min())


def brazo_home(lado: str) -> np.ndarray:
    """Los vértices del brazo en mundo, con el robot en home."""
    malla = "Brazo_Der_W.stl" if lado == "R" else "Brazo_Izq_W.stl"
    return np.unique(np.round(a_mundo(leer_stl(VISOR / malla), W[f"brazo{lado}"]), 1), axis=0)


def cabeza_home() -> np.ndarray:
    """Los vértices de la cabeza en mundo, diezmados: la holgura va en mm y la
    malla tiene más de 40 000 puntos."""
    return _diezmar(
        np.unique(np.round(a_mundo(leer_stl(VISOR / "Cabeza_Full.stl"), W["cabeza"]), 1), axis=0),
        20000,
    )


def por_distancia(lado: str, brazo: np.ndarray | None = None) -> list[dict]:
    """Las poses admisibles, de la más cercana a la pantalla a la más lejana."""
    punta = punta_home(brazo_home(lado) if brazo is None else brazo)[None, :]
    pantalla = np.array(PANTALLA)
    filas = []
    for a, f in candidatos(lado):
        tip = fk_brazo(punta, lado, a, f)[0]
        filas.append({"abd": a, "flex": f, "dist": float(np.linalg.norm(tip - pantalla)),
                      "tip": tip})
    filas.sort(key=lambda d: d["dist"])
    return filas


def holguras(lado: str, abduccion: float, flexion: float, brazo: np.ndarray,
             arboles: list[cKDTree]) -> list[float]:
    """La holgura brazo-cabeza, en mm, con la cabeza en cada pose de ``arboles``."""
    v = fk_brazo(_diezmar(brazo, 1500), lado, abduccion, flexion)
    return [holgura_cabeza(v, ar) for ar in arboles]


def holgura_en_la_envolvente(lado: str, brazo: np.ndarray, arboles: list[cKDTree],
                             paso: float = 5.0) -> tuple[float, float, float]:
    """``(holgura, abducción, flexión)`` de la pose admisible que más se acerca a
    la cabeza: los dos topes de las tablas y su centro, en todo el rango de flexión.
    Es lo que dice si hace falta una capa más para este par, que nadie comprueba."""
    side = Side.parse(lado)
    peor = (float("inf"), 0.0, 0.0)
    for f in np.arange(-180.0, 90.0 + 1e-9, paso):
        minimo, maximo = arm_abduccion_range_extendido(float(f), side)
        for a in (minimo, (minimo + maximo) / 2.0, maximo):
            h = min(holguras(lado, float(a), float(f), brazo, arboles))
            if h < peor[0]:
                peor = (h, float(a), float(f))
    return peor


def arboles_cabeza(cabeza: np.ndarray, poses_cabeza=POSES_CABEZA) -> list[cKDTree]:
    return [cKDTree(fk_cabeza(cabeza, p, t)) for p, t in poses_cabeza]


def _diezmar(v: np.ndarray, maximo: int) -> np.ndarray:
    return v if len(v) <= maximo else v[:: len(v) // maximo]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.parse_args(argv)

    print("Cinemática del visor frente a retarget.direccion (grados entre las dos):")
    for lado in "RL":
        print(f"  brazo {lado}: peor caso {peor_diferencia_cinematica(lado):.2f}°")

    cabeza = cabeza_home()
    print(f"Cabeza: x {cabeza[:,0].min():.0f}..{cabeza[:,0].max():.0f}, "
          f"y {cabeza[:,1].min():.0f}..{cabeza[:,1].max():.0f}, "
          f"z {cabeza[:,2].min():.0f}..{cabeza[:,2].max():.0f} mm")

    arboles = arboles_cabeza(cabeza)
    for lado in "RL":
        brazo = brazo_home(lado)
        filas = por_distancia(lado, brazo)
        print(f"\nBrazo {lado}: {len(filas)} poses admisibles; las más cercanas a la pantalla")
        print("   abd   flex | punta (x, y, z) mm       | a pantalla | holgura cabeza min (home / todas) mm")
        elegida = None
        for i, d in enumerate(filas[:CERCANAS]):
            h = holguras(lado, d["abd"], d["flex"], brazo, arboles)
            if elegida is None and min(h) >= HOLGURA_MIN:
                elegida = (d, h)
            if i < 10:
                print(f"  {d['abd']:6.1f} {d['flex']:6.1f} | "
                      f"{d['tip'][0]:6.0f} {d['tip'][1]:6.0f} {d['tip'][2]:6.0f}    | "
                      f"{d['dist']:6.0f}     | {h[0]:.0f} / {min(h):.0f}")
        if elegida:
            d, h = elegida
            print(f"  ELEGIDA: abd {d['abd']:.1f}, flex {d['flex']:.1f}: {d['dist']:.0f} mm a la pantalla, "
                  f"holgura mínima {min(h):.0f} mm")
        h, a, f = holgura_en_la_envolvente(lado, brazo, arboles)
        print(f"  En toda la envolvente: {h:.0f} mm como mínimo (abd {a:.0f}, flex {f:.0f})")
    return 0


def peor_diferencia_cinematica(lado: str) -> float:
    """El mayor ángulo entre esta cadena y ``retarget.direccion``, en grados."""
    return max(
        math.degrees(math.acos(np.clip(np.dot(direccion_visor(lado, a, f), direccion(a, f)), -1, 1)))
        for a in (-60, -40, -20, 10) for f in (-170, -120, -90, -45, 0, 30)
    )


if __name__ == "__main__":
    raise SystemExit(main())
