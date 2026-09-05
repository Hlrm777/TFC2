"""Analiza un registro del head tracker (``--record``).

Convierte una sesión de prueba en números: temblor en reposo, recorrido
alcanzado, ganancias necesarias y comparación entre métodos. Sirve para
sustituir el «lo sentí más preciso» por una cifra.

El temblor en reposo es, además, exactamente la **repetibilidad** que el plan
maestro dice que hay que reportar en la tesis, ya que no hay ground truth
angular con una sola cámara.

Uso::

    python tools/analizar_registro.py sesion.csv
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telmeow_teleop.limits import HEAD_PAN_RANGE, HEAD_TILT_RANGE  # noqa: E402


def cargar(ruta: Path) -> list[dict]:
    with open(ruta, newline="", encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    for fila in filas:
        for clave in ("t", "raw_pan", "raw_tilt", "pan", "tilt",
                      "gain_pan", "gain_tilt_up", "gain_tilt_down"):
            fila[clave] = float(fila[clave])
        fila["etiqueta"] = int(fila["etiqueta"])
        fila["recortado"] = int(fila["recortado"])
    return filas


def resumen_bloque(nombre: str, filas: list[dict]) -> None:
    if len(filas) < 2:
        print(f"  {nombre}: solo {len(filas)} muestras, se omite")
        return

    duracion = filas[-1]["t"] - filas[0]["t"]
    fps = len(filas) / duracion if duracion > 0 else 0.0
    crudo_pan = [f["raw_pan"] for f in filas]
    crudo_tilt = [f["raw_tilt"] for f in filas]
    sal_pan = [f["pan"] for f in filas]
    sal_tilt = [f["tilt"] for f in filas]
    recortados = sum(f["recortado"] for f in filas)

    print(f"  {nombre}")
    print(f"    {len(filas):5d} muestras en {duracion:5.1f} s  ->  {fps:5.1f} fps")
    print(f"    pan  crudo [{min(crudo_pan):+7.2f},{max(crudo_pan):+7.2f}]  "
          f"desv {statistics.stdev(crudo_pan):6.3f}   "
          f"salida [{min(sal_pan):+7.2f},{max(sal_pan):+7.2f}]  "
          f"desv {statistics.stdev(sal_pan):6.3f}")
    print(f"    tilt crudo [{min(crudo_tilt):+7.2f},{max(crudo_tilt):+7.2f}]  "
          f"desv {statistics.stdev(crudo_tilt):6.3f}   "
          f"salida [{min(sal_tilt):+7.2f},{max(sal_tilt):+7.2f}]  "
          f"desv {statistics.stdev(sal_tilt):6.3f}")
    if recortados:
        print(f"    recortado por el tope del robot en {recortados} de "
              f"{len(filas)} muestras ({100 * recortados / len(filas):.0f} %)")

    # Ganancias que harían falta para que ESTE recorrido cubra el rango.
    sugerencias = []
    if min(crudo_pan) < -1:
        sugerencias.append(f"pan {abs(HEAD_PAN_RANGE[0] / min(crudo_pan)):.2f}")
    if max(crudo_pan) > 1:
        sugerencias.append(f"pan {abs(HEAD_PAN_RANGE[1] / max(crudo_pan)):.2f}")
    if max(crudo_tilt) > 1:
        sugerencias.append(f"tilt^ {abs(HEAD_TILT_RANGE[1] / max(crudo_tilt)):.2f}")
    if min(crudo_tilt) < -1:
        sugerencias.append(f"tiltv {abs(HEAD_TILT_RANGE[0] / min(crudo_tilt)):.2f}")
    if sugerencias:
        print(f"    ganancias para cubrir el rango: {'  '.join(sugerencias)}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="analizar_registro")
    parser.add_argument("csv", type=Path)
    args = parser.parse_args(argv)

    if not args.csv.is_file():
        print(f"No existe {args.csv}")
        return 1

    filas = cargar(args.csv)
    if not filas:
        print("El registro está vacío.")
        return 1

    print(f"\n{'=' * 74}\n{args.csv.name}: {len(filas)} muestras\n{'=' * 74}\n")

    print("POR ETIQUETA  (cada pulsación de ESPACIO abre una nueva)\n")
    por_etiqueta: dict[int, list[dict]] = defaultdict(list)
    for fila in filas:
        por_etiqueta[fila["etiqueta"]].append(fila)
    for etiqueta in sorted(por_etiqueta):
        resumen_bloque(f"etiqueta {etiqueta}", por_etiqueta[etiqueta])

    metodos = {f["metodo"] for f in filas}
    if len(metodos) > 1:
        print(f"{'=' * 74}\nPOR METODO\n{'=' * 74}\n")
        por_metodo: dict[str, list[dict]] = defaultdict(list)
        for fila in filas:
            por_metodo[fila["metodo"]].append(fila)
        for metodo in sorted(por_metodo):
            resumen_bloque(metodo, por_metodo[metodo])

    resoluciones = {(f["ancho"], f["alto"]) for f in filas}
    print(f"Resolucion(es) de captura: "
          f"{', '.join(f'{w}x{h}' for w, h in sorted(resoluciones))}")
    print("\nNota: la desviacion en un tramo de REPOSO es el temblor; en un tramo")
    print("de movimiento no significa nada (mide el propio movimiento).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
