"""Reproduce sobre las sesiones grabadas la regla de la segunda opción (P10.1,
H70): cuando el cabeceo descarta un enojo, el voto va a la expresión que el
modelo tenía de segunda, con su probabilidad, en vez de a neutral.

En la sesión p10 el operador sonreía leyendo el libro de la pantalla, con la
cabeza 10 a 16° abajo. Desde ahí el modelo empata entre feliz (0.45 de media) y
enojado (0.47); gana el enojo por poco, la regla del cabeceo (P7.6) lo descarta
y el voto se iba a neutral: el robot puso feliz el 25 % del tramo. El modelo sí
ve la sonrisa —la tiene de segunda—, así que no es un problema de
entrenamiento sino de adónde va el voto descartado.

Usa las predicciones grabadas (``p_feliz``… de cada fila), la regla del enojo y
los vistazos de operación, y rehace la histéresis con y sin la regla nueva. En
``p7_final``, anterior a la columna ``cabeceo``, el cabeceo sale del CSV de
cabeza (``raw_tilt``), como cuando se decidió la regla del enojo.

**Criterios, fijados el 2026-11-10** (después de ver p10, que es la sesión que
destapó el problema; por eso la regla se confirma en una grabación nueva):

- la sonrisa leyendo de p10 (etiqueta 9) sube al menos ``SUBIDA_FELIZ`` puntos;
- ningún otro tramo de ninguna sesión cae más de ``CAIDA_MAXIMA`` puntos.

Uso::

    python tools/simular_segunda_opcion.py
"""

from __future__ import annotations

import argparse
import bisect
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from telmeow_teleop.config import PROJECT_ROOT, AppConfig  # noqa: E402

#: Criterios, fijados el 2026-11-10.
SUBIDA_FELIZ = 30.0
CAIDA_MAXIMA = 5.0

#: El tramo que la regla tiene que arreglar.
TRAMO_SONRISA = ("p10a", "feliz leyendo")

#: Por sesión: ``(tramo, etiquetas, expresión pedida)``. De p10, solo las
#: etiquetas con una expresión clara: fuera la calibración (0, 2), la pausa (8),
#: la expresión fijada a mano (11) y el cierre (13).
#: ``p10a`` es la narracion de la sesion p10 del 2026-11-10 (la que destapo el
#: problema); ``p10_narracion`` es desde el 2026-11-12 la de confirmacion.
TRAMOS: dict[str, tuple[tuple[str, tuple[int, ...], str], ...]] = {
    "p10a": (
        ("neutral hablando", (1,), "neutral"),
        ("neutral brazos quietos", (3,), "neutral"),
        ("neutral brazo estirado", (4,), "neutral"),
        ("neutral saludando", (5,), "neutral"),
        ("neutral leyendo", (6, 7, 12), "neutral"),
        ("feliz leyendo", (9,), "feliz"),
        ("triste leyendo", (10,), "triste"),
    ),
    "p78": (
        ("neutral al frente", (1, 10), "neutral"),
        ("neutral abajo", (2,), "neutral"),
        ("neutral arriba", (3,), "neutral"),
        ("neutral hablando", (4,), "neutral"),
        ("enojado al frente", (5,), "enojado"),
        ("enojado abajo", (6,), "enojado"),
        ("triste al frente", (8,), "triste"),
        ("triste abajo", (9,), "triste"),
    ),
    "p77": (
        ("neutral al frente", (1, 11), "neutral"),
        ("neutral abajo", (2,), "neutral"),
        ("neutral arriba", (3,), "neutral"),
        ("neutral hablando", (4,), "neutral"),
        ("enojado al frente", (5,), "enojado"),
        ("enojado abajo", (6,), "enojado"),
        ("triste sin ceño", (8,), "triste"),
        ("triste con ceño", (9,), "triste"),
        ("triste abajo", (10,), "triste"),
    ),
    "p7_final": (
        ("neutral", (0, 4), "neutral"),
        ("triste sin ceño", (1, 5), "triste"),
        ("triste con ceño", (2, 6), "triste"),
        ("enojado", (3, 7), "enojado"),
        ("feliz", (8,), "feliz"),
        ("sorprendido", (9,), "sorprendido"),
        ("lectura", (10,), "neutral"),
    ),
}


def columna_de_cabeza(base: str, filas: list[dict], columna: str) -> list[float | None]:
    """El valor de ``columna`` del CSV de cabeza más cercano a cada inferencia."""
    with open(PROJECT_ROOT / f"{base}_cabeza.csv", encoding="utf-8", newline="") as f:
        cabeza = list(csv.DictReader(f))
    tc = [float(x["t"]) for x in cabeza]
    salida: list[float | None] = []
    for fila in filas:
        i = min(bisect.bisect_left(tc, fila["t"]), len(cabeza) - 1)
        salida.append(float(cabeza[i][columna]) if abs(tc[i] - fila["t"]) < 0.25 else None)
    return salida


def preparar(base: str, cfg: AppConfig | None = None) -> list[dict]:
    """Las filas de la sesión como votarían hoy sin la regla nueva: la regla del
    enojo de operación y los vistazos (P7.8) marcados si la sesión es anterior."""
    import analizar_registro_emocion as are
    from simular_vistazos import marcar_vistazos

    e = (cfg or AppConfig.from_env()).emotion
    filas = are.cargar(PROJECT_ROOT / f"{base}_emocion.csv")
    with open(PROJECT_ROOT / f"{base}_emocion.csv", encoding="utf-8", newline="") as f:
        columnas = csv.DictReader(f).fieldnames or ()
    if "cabeceo" not in columnas:
        for fila, cabeceo in zip(filas, columna_de_cabeza(base, filas, "raw_tilt")):
            fila["cabeceo"] = cabeceo
    for fila in filas:
        fila["enojo_por_grado"], fila["enojo_desde"] = e.enojo_por_grado, e.enojo_desde
    if "mirada_fuera" not in columnas:
        filas = marcar_vistazos(filas, columna_de_cabeza(base, filas, "raw_pan"), e.mirada_abajo,
                                e.mirada_arriba, e.mirada_lado, e.mirada_fuera_s)
    return filas


def resumir(base: str, filas: list[dict], segunda: bool) -> dict[str, float | None]:
    """Por tramo: % de sus inferencias (sin las que calibran ni las fijadas a
    mano) en que el robot pone la expresión pedida, con o sin la regla."""
    import analizar_registro_emocion as are

    copia = [dict(f, segunda_tras_enojo=int(segunda)) for f in filas]
    n, minimo = are.parametros_grabados(copia)
    emociones, _ = are.reproducir(copia, n, minimo)
    salida: dict[str, float | None] = {}
    for nombre, etiquetas, esperada in TRAMOS[base]:
        tramo = [e for f, e in zip(copia, emociones)
                 if f["etiqueta"] in etiquetas and not f["calibrando"] and not f["manual"]]
        salida[nombre] = 100.0 * sum(e == esperada for e in tramo) / len(tramo) if tramo else None
    return salida


@dataclass(frozen=True)
class Veredicto:
    cumple: bool
    subida_feliz: float | None
    caidas: tuple[tuple[str, str, float], ...]


def criterio(tablas: dict[str, tuple[dict, dict]]) -> Veredicto:
    """``tablas[sesion] = (sin la regla, con la regla)``."""
    subida = None
    caidas = []
    for base, (sin, con) in tablas.items():
        for tramo, antes in sin.items():
            despues = con[tramo]
            if antes is None or despues is None:
                continue
            if (base, tramo) == TRAMO_SONRISA:
                subida = despues - antes
            elif antes - despues > CAIDA_MAXIMA:
                caidas.append((base, tramo, antes - despues))
    cumple = subida is not None and subida >= SUBIDA_FELIZ and not caidas
    return Veredicto(cumple, subida, tuple(caidas))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("sesiones", nargs="*", help=f"por defecto, todas: {', '.join(TRAMOS)}")
    a = p.parse_args(argv)
    desconocidas = [s for s in a.sesiones if s not in TRAMOS]
    if desconocidas:
        p.error(f"sesiones desconocidas: {desconocidas}")
    a.sesiones = a.sesiones or list(TRAMOS)

    tablas = {}
    for base in a.sesiones:
        if not (PROJECT_ROOT / f"{base}_emocion.csv").exists():
            print(f"{base}: falta {base}_emocion.csv, se salta")
            continue
        filas = preparar(base)
        tablas[base] = (resumir(base, filas, False), resumir(base, filas, True))
        print(f"\n{base}: % del tramo con la expresión pedida        sin la regla   con la regla")
        for tramo, antes in tablas[base][0].items():
            despues = tablas[base][1][tramo]
            celdas = ["     --" if v is None else f"{v:5.0f} %" for v in (antes, despues)]
            print(f"  {tramo:42s} {celdas[0]:>12s}   {celdas[1]:>12s}")

    v = criterio(tablas)
    print("\nCriterios (2026-11-10):")
    if v.subida_feliz is None:
        print("  la sonrisa leyendo de p10 no está entre las sesiones: no se puede decidir")
    else:
        print(f"  la sonrisa leyendo sube {v.subida_feliz:+.0f} puntos (se piden {SUBIDA_FELIZ:.0f}): "
              + ("OK" if v.subida_feliz >= SUBIDA_FELIZ else "NO CUMPLE"))
    if v.caidas:
        for base, tramo, caida in v.caidas:
            print(f"  {base}, {tramo}: cae {caida:.0f} puntos (máximo {CAIDA_MAXIMA:.0f}) NO CUMPLE")
    else:
        print(f"  ningún otro tramo cae más de {CAIDA_MAXIMA:.0f} puntos: OK")
    print("  " + ("LA REGLA CUMPLE" if v.cumple else "LA REGLA NO CUMPLE"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
