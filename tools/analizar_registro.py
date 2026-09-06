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

from telmeow_teleop.limits import (  # noqa: E402
    HEAD_PAN_TARGET_RANGE,
    HEAD_TILT_RANGE,
)

#: Por debajo de esta fracción de la meseta, una muestra se considera trayecto.
#:
#: Sirve para detectar la etiqueta contaminada: si el operador vuelve al centro
#: dentro de una etiqueta, aparece un montón de muestras a medio camino que no
#: pertenecen a la postura que se quería medir.
FRACCION_MESETA = 0.5

#: Si más de este porcentaje del tiempo se pasó fuera de la meseta, se avisa.
TOLERANCIA_TRAYECTO = 0.25

#: Excursión mínima, en grados, para proponer una ganancia en ese eje.
#:
#: Con el umbral en 1° cada etiqueta proponía ganancias en los cuatro sentidos,
#: incluidos los ejes que el operador ni movió: 3° de cabeceo residual mientras
#: giraba la cabeza salían como «tilt 11.81», que no significa nada y compite en
#: pantalla con la cifra que sí importa.
EXCURSION_MINIMA = 5.0


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


def percentil(valores: list[float], p: float) -> float:
    """Percentil ``p`` (0-100) por interpolación lineal entre las dos muestras
    que lo rodean.

    Se usa en vez del mínimo y el máximo porque en una etiqueta de POSTURA
    SOSTENIDA los extremos recogen el instante de entrada y el de salida, que no
    son la postura: son el trayecto hacia ella. El percentil se queda con la
    meseta, que es donde el operador pasó el tiempo.
    """
    if not valores:
        raise ValueError("no hay valores")
    ordenados = sorted(valores)
    if len(ordenados) == 1:
        return ordenados[0]
    posicion = (p / 100.0) * (len(ordenados) - 1)
    bajo = int(posicion)
    alto = min(bajo + 1, len(ordenados) - 1)
    peso = posicion - bajo
    return ordenados[bajo] * (1 - peso) + ordenados[alto] * peso


def diagnostico_meseta(valores: list[float]) -> tuple[float, str] | None:
    """¿Esta etiqueta es una postura sostenida limpia, o se volvió al centro?

    Devuelve ``(meseta, aviso)``, donde la meseta es el p95 o el p05 según el
    sentido en que se movió el operador, y el aviso está vacío si la etiqueta
    está limpia.

    El defecto que busca es concreto y ya ocurrió una vez: en la grabación
    `limites.csv` el operador volvió al centro dentro de una etiqueta —le picó
    la nariz— y esa etiqueta quedó inservible, porque mezcla la postura que se
    quería medir con el camino de ida y vuelta. Si más de una cuarta parte del
    tiempo se pasó a menos de la mitad de la meseta, es esa firma.
    """
    if not valores:
        return None
    p05, p95 = percentil(valores, 5), percentil(valores, 95)
    meseta = p95 if abs(p95) >= abs(p05) else p05
    if abs(meseta) < 5.0:
        return None  # etiqueta de reposo o de centro: no hay meseta que juzgar

    umbral = FRACCION_MESETA * abs(meseta)
    fuera = sum(1 for v in valores if abs(v) < umbral)
    fraccion = fuera / len(valores)
    if fraccion <= TOLERANCIA_TRAYECTO:
        return meseta, ""
    return meseta, (
        f"  <-- el {100 * fraccion:.0f} % del tiempo por debajo de la mitad de "
        f"la meseta: revisar si se volvio al centro dentro de la etiqueta"
    )


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

    # La mediana es lo que vale en un tramo SOSTENIDO (mantener una postura):
    # resiste los instantes de entrada y salida de la posición, que el mínimo y
    # el máximo sí recogen.
    mediana_pan = statistics.median(crudo_pan)
    mediana_tilt = statistics.median(crudo_tilt)

    print(f"  {nombre}")
    print(f"    {len(filas):5d} muestras en {duracion:5.1f} s  ->  {fps:5.1f} fps")
    for eje, crudo, mediana in (
        ("pan ", crudo_pan, mediana_pan),
        ("tilt", crudo_tilt, mediana_tilt),
    ):
        temblor_eje = temblor_pan if eje == "pan " else temblor_tilt
        print(f"    {eje} mediana {mediana:+7.2f}   p05 {percentil(crudo, 5):+7.2f}"
              f"   p95 {percentil(crudo, 95):+7.2f}   TEMBLOR {fmt(temblor_eje)}")
        diagnostico = diagnostico_meseta(crudo)
        aviso = diagnostico[1] if diagnostico else ""
        print(f"         extremos [{min(crudo):+7.2f},{max(crudo):+7.2f}]{aviso}")
    if temblor_pan_f is not None or temblor_tilt_f is not None:
        print(f"    tras el filtro One Euro:  pan {fmt(temblor_pan_f)}"
              f"   tilt {fmt(temblor_tilt_f)}")
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

    # Ganancias que harían falta para que ESTE recorrido cubra el objetivo.
    #
    # Se calculan desde el PERCENTIL, no desde el extremo: el extremo incluye el
    # instante de entrada y el de salida de la postura, y usarlo propone una
    # ganancia más baja de la debida —el operador no sostiene ese ángulo—.
    #
    # El objetivo del pan es ±50°, no su rango mecánico de ±90°: al operador lo
    # acota su campo de visión sobre el monitor, no el cuello del robot
    # (§3.2.1). Dividir por HEAD_PAN_RANGE daba una ganancia 1.8× más alta.
    p05_pan, p95_pan = percentil(crudo_pan, 5), percentil(crudo_pan, 95)
    p05_tilt, p95_tilt = percentil(crudo_tilt, 5), percentil(crudo_tilt, 95)
    sugerencias = []
    if p05_pan < -EXCURSION_MINIMA:
        sugerencias.append(f"pan {abs(HEAD_PAN_TARGET_RANGE[0] / p05_pan):.2f}")
    if p95_pan > EXCURSION_MINIMA:
        sugerencias.append(f"pan {abs(HEAD_PAN_TARGET_RANGE[1] / p95_pan):.2f}")
    if p95_tilt > EXCURSION_MINIMA:
        sugerencias.append(f"tilt^ {abs(HEAD_TILT_RANGE[1] / p95_tilt):.2f}")
    if p05_tilt < -EXCURSION_MINIMA:
        sugerencias.append(f"tiltv {abs(HEAD_TILT_RANGE[0] / p05_tilt):.2f}")
    if sugerencias:
        print(f"    ganancias desde el percentil (pan -> {HEAD_PAN_TARGET_RANGE[1]:.0f},"
              f" tilt -> rango mecanico): {'  '.join(sugerencias)}")
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
    print("Nota: en una etiqueta de postura SOSTENIDA la cifra que vale es el")
    print("percentil, no el extremo: el extremo incluye entrar y salir de ella.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
