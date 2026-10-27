"""Analiza una sesión del pipeline integrado (``python -m telmeow_teleop.main --record``).

Lee ``sesion.csv`` —una fila por frame— y da, por etiqueta, lo que hace falta
para decir a qué tasa va el sistema y qué cuesta cada parte:

- **fps** de la cámara (mediana del intervalo entre frames) y cuántos
  intervalos pasan de 1.5 periodos, que son frames perdidos;
- **frames repetidos** (H19: contar lecturas no mide fps si la cámara repite);
- **ms por etapa** —cabeza, brazos, emoción y el bucle entero—, separando los
  frames en que se infirió la emoción de los demás;
- **publicación**: Hz efectivos y mensajes perdidos (sin enlace).

Con ``--comparar`` aplica el criterio del P7.1, fijado antes de medir: la
emoción pasa a un hilo aparte si con ella el bucle procesa menos del 90 % de
los fps que sin ella, o si la mediana de los frames con inferencia pasa de
66 ms (dos periodos a 30 fps: un frame perdido seguro).

Los CSV de cabeza, brazos y emoción de la misma sesión se analizan con sus
herramientas de siempre (``analizar_registro.py``,
``analizar_registro_brazos.py``, ``analizar_registro_emocion.py``).

Uso::

    python tools/analizar_sesion.py p7_movimiento.csv
    python tools/analizar_sesion.py p7_movimiento.csv --comparar p7_sin_emocion.csv --etiqueta 5
"""

from __future__ import annotations

import argparse
import csv
import statistics
from dataclasses import dataclass
from pathlib import Path

#: Un intervalo entre frames mayor que esto, en periodos de cámara, es un frame
#: perdido.
UMBRAL_PERDIDO = 1.5

#: Criterio del P7.1 (2026-10-26): por debajo de esta fracción de los fps sin
#: emoción, la emoción frena el bucle.
FRACCION_FPS_MINIMA = 0.9

#: Criterio del P7.1: mediana máxima de los frames con inferencia de emoción.
MS_INFERENCIA_MAXIMO = 66.0

#: Por debajo de esto, ni sin emoción cabe el bucle: primero
#: ``TELMEOW_ARM_MODEL_COMPLEXITY=0`` (la reserva del P5).
FPS_MINIMO_SIN_EMOCION = 25.0

_NUMERICAS = (
    "t", "t_captura_ms", "dt_ms", "ms_cabeza", "ms_brazos", "ms_emocion", "ms_bucle",
    "mqtt_hz", "brillo",
)
_ENTERAS = ("etiqueta", "duplicado", "pausa", "mqtt_enviados", "mqtt_descartados",
            "mqtt_fallidos", "ancho", "alto")


def cargar(ruta: Path) -> list[dict]:
    """Las filas de ``sesion.csv``, con los números ya convertidos (vacío -> None)."""
    with open(ruta, newline="", encoding="utf-8") as f:
        lector = csv.DictReader(f)
        faltan = [c for c in ("t", "etiqueta", "dt_ms", "ms_bucle") if c not in (lector.fieldnames or ())]
        if faltan:
            raise ValueError(
                f"{Path(ruta).name} no tiene las columnas {faltan}: no es la sesión de "
                f"'python -m telmeow_teleop.main --record' (los _cabeza/_brazos/_emocion "
                f"se analizan con sus herramientas)"
            )
        filas = list(lector)
    for fila in filas:
        for clave in _NUMERICAS:
            valor = fila.get(clave, "")
            fila[clave] = float(valor) if valor not in ("", None) else None
        for clave in _ENTERAS:
            valor = fila.get(clave, "")
            fila[clave] = int(valor) if valor not in ("", None) else 0
    return filas


def _percentil(valores: list[float], q: float) -> float | None:
    if not valores:
        return None
    ordenados = sorted(valores)
    posicion = (len(ordenados) - 1) * q
    abajo = int(posicion)
    arriba = min(abajo + 1, len(ordenados) - 1)
    return ordenados[abajo] + (ordenados[arriba] - ordenados[abajo]) * (posicion - abajo)


def _mediana(valores: list[float]) -> float | None:
    return statistics.median(valores) if valores else None


@dataclass(frozen=True)
class Resumen:
    etiqueta: int | None
    frames: int
    segundos: float
    fps: float | None              # 1000 / mediana del intervalo entre frames
    fps_medios: float | None       # frames / duración
    perdidos: float | None         # fracción de intervalos > UMBRAL_PERDIDO periodos
    duplicados: int
    ms_cabeza: tuple[float | None, float | None]       # (mediana, p95)
    ms_brazos: tuple[float | None, float | None]
    ms_emocion: tuple[float | None, float | None]      # solo frames con inferencia
    ms_bucle_sin: tuple[float | None, float | None]    # frames sin inferencia
    ms_bucle_con: tuple[float | None, float | None]    # frames con inferencia
    inferencias: int
    hz: float | None               # mediana de mqtt_hz, fuera de pausa
    fallidos: int                  # mensajes perdidos durante el tramo
    en_pausa: int


def resumir(filas: list[dict], etiqueta: int | None = None) -> Resumen:
    """Las cifras de un tramo: una etiqueta, o toda la sesión con ``None``."""
    tramo = [f for f in filas if etiqueta is None or f["etiqueta"] == etiqueta]
    if not tramo:
        raise ValueError(f"no hay filas con la etiqueta {etiqueta}")
    intervalos = [f["dt_ms"] for f in tramo if f["dt_ms"] is not None]
    periodo = _mediana(intervalos)
    perdidos = (
        sum(1 for d in intervalos if d > UMBRAL_PERDIDO * periodo) / len(intervalos)
        if intervalos and periodo else None
    )
    segundos = tramo[-1]["t"] - tramo[0]["t"]
    con = [f for f in tramo if f["ms_emocion"] is not None]
    sin = [f for f in tramo if f["ms_emocion"] is None]

    def par(valores):
        return (_mediana(valores), _percentil(valores, 0.95))

    def columna(lista, clave):
        return [f[clave] for f in lista if f[clave] is not None]

    activas = [f for f in tramo if not f["pausa"]]
    return Resumen(
        etiqueta=etiqueta,
        frames=len(tramo),
        segundos=segundos,
        fps=1000.0 / periodo if periodo else None,
        fps_medios=(len(tramo) - 1) / segundos if segundos > 0 else None,
        perdidos=perdidos,
        duplicados=sum(f["duplicado"] for f in tramo),
        ms_cabeza=par(columna(tramo, "ms_cabeza")),
        ms_brazos=par(columna(tramo, "ms_brazos")),
        ms_emocion=par(columna(con, "ms_emocion")),
        ms_bucle_sin=par(columna(sin, "ms_bucle")),
        ms_bucle_con=par(columna(con, "ms_bucle")),
        inferencias=len(con),
        hz=_mediana(columna(activas, "mqtt_hz")),
        fallidos=tramo[-1]["mqtt_fallidos"] - tramo[0]["mqtt_fallidos"],
        en_pausa=len(tramo) - len(activas),
    )


@dataclass(frozen=True)
class CriterioP71:
    fps_con: float | None
    fps_sin: float | None
    fraccion: float | None
    ms_inferencia: float | None
    hilo: bool                     # el criterio pide el hilo de emoción
    reserva_brazos: bool           # ni sin emoción cabe: model_complexity 0 primero
    motivos: tuple[str, ...]


def criterio_p71(con_emocion: Resumen, sin_emocion: Resumen) -> CriterioP71:
    """Aplica el criterio del P7.1 a dos tramos comparables (misma luz, narrando).

    Usa los fps medios —frames procesados por segundo—, que son los que bajan
    cuando el bucle se alarga: la mediana del intervalo no ve los frames tardíos
    si son minoría.
    """
    motivos = []
    fps_con, fps_sin = con_emocion.fps_medios, sin_emocion.fps_medios
    fraccion = fps_con / fps_sin if fps_con and fps_sin else None
    if fraccion is not None and fraccion < FRACCION_FPS_MINIMA:
        motivos.append(f"con emocion procesa el {100 * fraccion:.0f} % de los fps sin ella "
                       f"(< {100 * FRACCION_FPS_MINIMA:.0f} %)")
    ms_inferencia = con_emocion.ms_bucle_con[0]
    if ms_inferencia is not None and ms_inferencia > MS_INFERENCIA_MAXIMO:
        motivos.append(f"los frames con inferencia duran {ms_inferencia:.0f} ms de mediana "
                       f"(> {MS_INFERENCIA_MAXIMO:.0f})")
    reserva = fps_sin is not None and fps_sin < FPS_MINIMO_SIN_EMOCION
    if reserva:
        motivos.append(f"sin emocion solo llega a {fps_sin:.1f} fps (< {FPS_MINIMO_SIN_EMOCION:.0f}): "
                       f"primero TELMEOW_ARM_MODEL_COMPLEXITY=0")
    return CriterioP71(fps_con, fps_sin, fraccion, ms_inferencia,
                       hilo=bool(motivos) and not reserva, reserva_brazos=reserva,
                       motivos=tuple(motivos))


def _ms(par) -> str:
    mediana, p95 = par
    if mediana is None:
        return "    --"
    return f"{mediana:5.1f} / {p95:5.1f}"


def _num(valor, formato=".1f", ancho=5) -> str:
    return f"{'--':>{ancho}}" if valor is None else f"{valor:{ancho}{formato}}"


def imprimir(r: Resumen) -> None:
    nombre = "sesion entera" if r.etiqueta is None else f"etiqueta {r.etiqueta}"
    perdidos = "--" if r.perdidos is None else f"{100 * r.perdidos:.1f} %"
    print(f"\n{nombre}: {r.frames} frames en {r.segundos:.1f} s")
    print(f"  camara    fps {_num(r.fps)} (mediana)  {_num(r.fps_medios)} (medios)  "
          f"perdidos {perdidos}  repetidos {r.duplicados}")
    print(f"  ms (mediana / p95)   cabeza {_ms(r.ms_cabeza)}   brazos {_ms(r.ms_brazos)}")
    print(f"                       emocion {_ms(r.ms_emocion)}  ({r.inferencias} inferencias)")
    print(f"  bucle entero         sin inferencia {_ms(r.ms_bucle_sin)}   "
          f"con inferencia {_ms(r.ms_bucle_con)}")
    print(f"  MQTT  {_num(r.hz)} Hz   perdidos {r.fallidos}   frames en pausa {r.en_pausa}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="analizar_sesion",
        description="Tasas y costes de una sesion del pipeline integrado (P7).",
    )
    parser.add_argument("csv", type=Path, help="sesion.csv de main --record")
    parser.add_argument("--comparar", type=Path, metavar="SIN_EMOCION",
                        help="Sesion grabada con --no-emotion: aplica el criterio del P7.1.")
    parser.add_argument("--etiqueta", type=int,
                        help="Tramo con que comparar (p. ej. el de narrar). Sin ella, la sesion entera.")
    args = parser.parse_args(argv)

    filas = cargar(args.csv)
    imprimir(resumir(filas))
    for etiqueta in sorted({f["etiqueta"] for f in filas}):
        imprimir(resumir(filas, etiqueta))

    if args.comparar is not None:
        otra = cargar(args.comparar)
        con = resumir(filas, args.etiqueta)
        etiqueta_sin = args.etiqueta if any(f["etiqueta"] == args.etiqueta for f in otra) else None
        sin = resumir(otra, etiqueta_sin)
        c = criterio_p71(con, sin)
        print("\nCriterio del P7.1 (fijado el 2026-10-26, antes de medir):")
        print(f"  fps medios con emocion {_num(c.fps_con)}   sin emocion {_num(c.fps_sin)}   "
              f"fraccion {_num(c.fraccion, '.2f')}")
        print(f"  frames con inferencia: mediana {_num(c.ms_inferencia)} ms")
        for motivo in c.motivos:
            print(f"  - {motivo}")
        if c.reserva_brazos:
            print("  => probar antes TELMEOW_ARM_MODEL_COMPLEXITY=0 y volver a medir")
        elif c.hilo:
            print("  => la emocion pasa a un hilo aparte (P7.1)")
        else:
            print("  => la emocion se queda en el bucle")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
