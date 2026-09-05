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
                      "gain_pan", "gain_tilt_up", "gain_tilt_down",
                      "escala_cara"):
            fila[clave] = float(fila.get(clave, "nan"))
        fila["etiqueta"] = int(fila["etiqueta"])
        fila["recortado"] = int(fila["recortado"])
    return filas


def temblor(valores: list[float], tiempos: list[float], ventana=1.0) -> float | None:
    """Temblor de alta frecuencia: mediana de la desviación en ventanas cortas.

    La desviación sobre un tramo entero solo mide el temblor si el operador se
    quedó PERFECTAMENTE quieto, y eso no ocurre: hay deriva lenta, se respira,
    se cambia de postura. Esa desviación global mezcla temblor con movimiento
    real y sale inflada.

    Trocear en ventanas de un segundo y quedarse con la mediana de sus
    desviaciones separa las dos cosas: dentro de un segundo el movimiento
    voluntario apenas avanza, así que lo que queda es el ruido del estimador.
    La mediana descarta además las ventanas en las que sí hubo movimiento.

    Es la cifra que sirve como REPETIBILIDAD para la tesis.
    """
    if len(valores) < 10:
        return None
    desviaciones = []
    inicio = 0
    for i, t in enumerate(tiempos):
        if t - tiempos[inicio] >= ventana:
            trozo = valores[inicio:i]
            if len(trozo) >= 5:
                desviaciones.append(statistics.stdev(trozo))
            inicio = i
    return statistics.median(desviaciones) if desviaciones else None


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

    tiempos = [f["t"] for f in filas]
    temblor_pan = temblor(crudo_pan, tiempos)
    temblor_tilt = temblor(crudo_tilt, tiempos)
    temblor_pan_f = temblor(sal_pan, tiempos)
    temblor_tilt_f = temblor(sal_tilt, tiempos)

    def fmt(v):
        return f"{v:5.3f}" if v is not None else "  -- "

    print(f"  {nombre}")
    print(f"    {len(filas):5d} muestras en {duracion:5.1f} s  ->  {fps:5.1f} fps")
    print(f"    pan  recorrido [{min(crudo_pan):+7.2f},{max(crudo_pan):+7.2f}]"
          f"   TEMBLOR crudo {fmt(temblor_pan)}  filtrado {fmt(temblor_pan_f)}")
    print(f"    tilt recorrido [{min(crudo_tilt):+7.2f},{max(crudo_tilt):+7.2f}]"
          f"   TEMBLOR crudo {fmt(temblor_tilt)}  filtrado {fmt(temblor_tilt_f)}")
    escalas = [f["escala_cara"] for f in filas if f["escala_cara"] == f["escala_cara"]]
    if escalas:
        dispersion = max(escalas) - min(escalas)
        aviso = ""
        if dispersion > 0.15 * statistics.mean(escalas):
            aviso = "  <-- la distancia varió: el 'temblor' incluye ese movimiento"
        print(f"    cara  {statistics.mean(escalas):6.1f} px entre ojos "
              f"(min {min(escalas):.0f}, max {max(escalas):.0f}){aviso}")

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
