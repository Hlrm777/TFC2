"""Reproduce sobre una sesión grabada la regla de los vistazos del P7.8: con la
cabeza fuera de la pantalla, la cara no vota y el robot conserva la expresión,
hasta 3 s seguidos.

Usa las predicciones grabadas, el cabeceo de cada fila (columna ``cabeceo``) y el
giro del CSV de cabeza, decide como ``EmotionClassifier._mirando_fuera`` qué
caras no votan, y rehace la histéresis. Da el % del robot por tramo con y sin la
regla. Los protocolos miran abajo 10-15 s seguidos, así que solo los primeros 3 s
de esos tramos cambian: lo que la regla protege —los vistazos cortos— no está en
estas sesiones, y se verá narrando.

Uso::

    python tools/simular_vistazos.py p78
"""

from __future__ import annotations

import argparse
import bisect
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from telmeow_teleop.config import PROJECT_ROOT, AppConfig  # noqa: E402


def marcar_vistazos(filas: list[dict], giros: list[float | None], abajo: float, arriba: float,
                    lado: float, hasta_s: float) -> list[dict]:
    """Copia de las filas con ``mirada_fuera`` decidido como en vivo."""
    salida, desde = [], None
    for f, giro in zip(filas, giros):
        g = dict(f)
        cab = f.get("cabeceo")
        fuera = cab is not None and (cab < abajo or cab > arriba or (giro is not None and abs(giro) > lado))
        if not fuera or not f["rostro"] or f.get("manos") or f.get("calibrando"):
            desde = None if not fuera else desde
            g["mirada_fuera"] = 0
        else:
            desde = f["t"] if desde is None else desde
            g["mirada_fuera"] = int(f["t"] - desde < hasta_s)
        salida.append(g)
    return salida


def giros_de(base: str, filas: list[dict]) -> list[float | None]:
    with open(PROJECT_ROOT / f"{base}_cabeza.csv", encoding="utf-8", newline="") as f:
        cabeza = list(csv.DictReader(f))
    tc = [float(x["t"]) for x in cabeza]
    salida = []
    for fila in filas:
        i = min(bisect.bisect_left(tc, fila["t"]), len(cabeza) - 1)
        salida.append(float(cabeza[i]["raw_pan"]) if abs(tc[i] - fila["t"]) < 0.25 else None)
    return salida


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("sesion", choices=("p77", "p78"))
    a = p.parse_args(argv)

    import analizar_registro_emocion as are
    import confirmar_p78 as cp
    import reentrenar_p77 as rp

    e = AppConfig.from_env().emotion
    filas = are.cargar(PROJECT_ROOT / f"{a.sesion}_emocion.csv")
    for f in filas:
        f["enojo_por_grado"] = e.enojo_por_grado                # la regla de ahora
    resumir = rp.resumir if a.sesion == "p77" else cp.resumir
    con = marcar_vistazos(filas, giros_de(a.sesion, filas), e.mirada_abajo, e.mirada_arriba,
                          e.mirada_lado, e.mirada_fuera_s)
    tablas = {}
    for nombre, f_ in (("sin vistazos", filas), ("con vistazos", con)):
        emos, _ = are.reproducir(f_, 5, 0.5)
        copia = [dict(x, emocion=x["manual"] or y) for x, y in zip(f_, emos)]
        tablas[nombre] = resumir(copia)
    print(f"{a.sesion}, regla del enojo {e.enojo_por_grado:g}; caras que no votan por mirar fuera: "
          f"{sum(f['mirada_fuera'] for f in con)} de {len(con)}")
    for tramo in tablas["sin vistazos"]:
        v = [tablas[n][tramo] for n in tablas]
        print(f"  {tramo:20s} " + "  ".join("   --" if x is None else f"{x:5.0f} %" for x in v))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
