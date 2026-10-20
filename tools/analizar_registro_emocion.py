"""Analiza un registro del clasificador de emoción (``--record``): el P6.1.

Para qué
--------
El protocolo del P6.1 (plan maestro, PROMPT 6, «Lo que falta») se graba con::

    python -m telmeow_teleop.emotion_classifier --publish --record emocion.csv

que escribe una fila por inferencia. Esta herramienta saca de esa grabación las
cifras que el plan necesita:

1. **Por etiqueta del protocolo**, qué expresión envió el robot la mayor parte
   del tiempo frente a la que pedía el protocolo, y al lado qué dijo el modelo
   crudo, que es lo que separa un fallo del modelo de uno de la histéresis.
   Las inferencias van a ``emotion_hz`` fijo, así que contar inferencias es
   contar tiempo.
2. Qué fracción de inferencias no encontró cara, y el brillo medio: una
   etiqueta sin cara no dice nada del modelo.
3. Cuántas veces por minuto cambió la expresión enviada. En la etiqueta 5
   (leer en voz alta) es el parpadeo que la histéresis tiene que frenar.
4. **La reproducción offline de la histéresis**: las predicciones crudas
   grabadas pasan otra vez por ``filters.ClassHysteresis`` —la clase de verdad,
   no una copia— con una rejilla de ``n_frames`` y ``min_confidence``. Para cada
   par: cambios por minuto en la etiqueta 5 y latencia del cambio al entrar en
   las etiquetas 1-4. Es lo que calibra los dos parámetros sin volver a grabar.

La prueba de que el instrumento reproduce el clasificador
---------------------------------------------------------
Con los parámetros con que se grabó, la reproducción tiene que dar
**exactamente** la columna ``emocion``, que escribió el clasificador en vivo
(fuera de las filas en manual, donde manda la tecla). Se comprueba en cada
análisis, antes de la rejilla: si no coincide, la rejilla no vale.

Para coincidir, la reproducción hace lo mismo que ``EmotionClassifier``:

- las filas sin rostro, o con ``prediccion`` vacía (ganó una clase sin
  equivalente), **no votan**, como en ``_inferir``;
- las filas en manual **sí votan**: la inferencia sigue corriendo debajo;
- al cambiar de backend (tecla ``b``) la ventana se vacía y se conserva la
  expresión, como en ``cambiar_backend``. Dos pulsaciones de ``b`` entre dos
  inferencias que vuelvan al mismo backend no dejan rastro en el CSV.

Un límite que se detecta y se dice: el CSV guarda la confianza con 4
decimales. Si la media de los votos de la candidata cae a menos de ``FILO`` del
umbral, la grabación no alcanza para saber de qué lado quedó, y la
reproducción puede salir distinta en esa fila y en las siguientes hasta
resincronizarse. Una discrepancia que empieza en una decisión «al filo» es del
redondeo; una que no, es un fallo del instrumento.

Uso::

    python tools/analizar_registro_emocion.py emocion_2026-10-XX.csv
    python tools/analizar_registro_emocion.py emocion.csv --n-frames 3 5 --min-confidence 0.55 0.65
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telmeow_teleop.emotion_classifier import NEUTRAL  # noqa: E402
from telmeow_teleop.filters import ClassHysteresis  # noqa: E402

#: Lo que tiene que enseñar el robot en cada etiqueta del protocolo del P6.1
#: (la tabla del plan maestro). En la 5, leyendo en voz alta, no hay una
#: expresión correcta: lo que se mide es que no parpadee.
ESPERADAS: dict[int, str | None] = {
    0: "neutral",
    1: "feliz",
    2: "triste",
    3: "sorprendido",
    4: "enojado",
    5: None,
    6: "concentrado",
}

#: La etiqueta de lectura natural, la que mide el parpadeo.
LECTURA = 5
#: Las etiquetas en que se mide la latencia de entrada.
ENTRADAS = (1, 2, 3, 4)
#: La de la tecla 6 (concentrado), que es manual.
MANUAL = 6

#: La rejilla del barrido, alrededor de los valores razonados (5 y 0.6).
REJILLA_N_FRAMES = (3, 5, 7, 9)
REJILLA_MIN_CONFIDENCE = (0.5, 0.6, 0.7, 0.8)

#: Distancia al umbral por debajo de la cual una decisión no se puede
#: reproducir desde el CSV, que guarda la confianza con 4 decimales (±5e-5).
FILO = 1e-4

#: Las columnas que lee esta herramienta. Todas las escribe ``_fila_csv`` del
#: clasificador; una prueba lo comprueba.
COLUMNAS = (
    "t", "etiqueta", "backend", "rostro", "prediccion", "confianza_pred",
    "emocion", "manual", "brillo", "n_frames", "min_confidence",
)

#: Cómo se cuenta en el reparto del modelo una cara cuya clase ganadora no
#: tiene expresión en el robot (``prediccion`` vacía con rostro).
SIN_EQUIVALENTE = "(sin equivalente)"

#: Brillo por debajo del cual el demo avisa de cámara negra.
BRILLO_NEGRO = 8.0

#: Fracción de inferencias sin cara a partir de la cual se avisa.
SIN_ROSTRO_AVISO = 0.25


# --------------------------------------------------------------------------
# Lectura
# --------------------------------------------------------------------------


def cargar(ruta: Path) -> list[dict]:
    """Las filas del CSV, con los números ya convertidos.

    ``manual``, ``confianza_pred`` y ``latencia_ms`` vacíos pasan a ``None``.
    """
    ruta = Path(ruta)
    with open(ruta, newline="", encoding="utf-8") as f:
        lector = csv.DictReader(f)
        faltan = [c for c in COLUMNAS if c not in (lector.fieldnames or ())]
        if faltan:
            raise ValueError(
                f"{ruta.name} no tiene las columnas {faltan}: no es un registro de "
                f"'python -m telmeow_teleop.emotion_classifier --record'"
            )
        filas = list(lector)
    for fila in filas:
        fila["t"] = float(fila["t"])
        fila["etiqueta"] = int(fila["etiqueta"])
        fila["rostro"] = int(fila["rostro"])
        fila["confianza_pred"] = float(fila["confianza_pred"]) if fila["confianza_pred"] else None
        fila["manual"] = fila["manual"] or None
        fila["brillo"] = float(fila["brillo"])
        fila["n_frames"] = int(fila["n_frames"])
        fila["min_confidence"] = float(fila["min_confidence"])
        latencia = fila.get("latencia_ms")
        fila["latencia_ms"] = float(latencia) if latencia else None
    return filas


def parametros_grabados(filas: list[dict]) -> tuple[int, float]:
    """``(n_frames, min_confidence)`` con que se grabó.

    El demo no los cambia en marcha, así que una grabación con más de un par
    está corrupta o es la suma de dos.
    """
    pares = {(f["n_frames"], f["min_confidence"]) for f in filas}
    if len(pares) != 1:
        raise ValueError(
            f"el registro tiene mas de un par n_frames/min_confidence: {sorted(pares)}"
        )
    return pares.pop()


def indices_por_etiqueta(filas: list[dict]) -> dict[int, list[int]]:
    """Índices de las filas de cada etiqueta, en orden."""
    grupos: dict[int, list[int]] = defaultdict(list)
    for i, fila in enumerate(filas):
        grupos[fila["etiqueta"]].append(i)
    return dict(grupos)


# --------------------------------------------------------------------------
# La reproducción de la histéresis
# --------------------------------------------------------------------------


def voto(fila: dict) -> tuple[str, float] | None:
    """El voto que dio la fila a la histéresis, o ``None`` si no votó.

    Como en ``EmotionClassifier._inferir``: sin rostro no se llama al backend,
    y con rostro tampoco vota si ganó una clase sin equivalente (``prediccion``
    vacía).
    """
    if not fila["rostro"] or not fila["prediccion"] or fila["confianza_pred"] is None:
        return None
    return fila["prediccion"], fila["confianza_pred"]


def reproducir(
    filas: list[dict], n_frames: int, min_confidence: float
) -> tuple[list[str], list[float | None]]:
    """Pasa las predicciones crudas grabadas por una ``ClassHysteresis`` nueva.

    Devuelve, por fila:

    - la expresión estabilizada, que es lo que el clasificador manda fuera de
      manual;
    - el margen de la decisión de umbral tomada en esa fila —media de los votos
      de la candidata menos ``min_confidence``—, o ``None`` si en esa fila no se
      llegó a mirar el umbral (no votó, votó por la vigente o no tenía
      mayoría).
    """
    histeresis = ClassHysteresis(n_frames, min_confidence, initial=NEUTRAL)
    emociones: list[str] = []
    margenes: list[float | None] = []
    backend = None
    for fila in filas:
        if backend is not None and fila["backend"] != backend:
            # cambiar_backend: la ventana tenía votos de otra escala de
            # confianza; se vacía y se conserva la expresión.
            histeresis.reset(initial=histeresis.current)
        backend = fila["backend"]
        margen = None
        v = voto(fila)
        if v is not None:
            etiqueta, confianza = v
            anterior = histeresis.current
            histeresis.update(etiqueta, confianza)
            if etiqueta != anterior:
                votos = [c for e, c in histeresis.window if e == etiqueta]
                if len(votos) * 2 > len(histeresis.window):
                    margen = sum(votos) / len(votos) - min_confidence
        emociones.append(histeresis.current)
        margenes.append(margen)
    return emociones, margenes


@dataclass(frozen=True)
class Comparacion:
    """La reproducción con los parámetros grabados frente a la columna ``emocion``.

    Attributes:
        comparables: filas fuera de manual, que son las que se comparan.
        distintas: índices de las filas que no coinciden.
        tramos: ``(primera fila, empieza al filo)`` de cada tramo seguido de
            filas distintas.
        reproducidas: la expresión reproducida en cada fila.
    """

    n_frames: int
    min_confidence: float
    comparables: int
    distintas: tuple[int, ...]
    tramos: tuple[tuple[int, bool], ...]
    reproducidas: tuple[str, ...]

    @property
    def exacta(self) -> bool:
        return not self.distintas

    @property
    def explicada(self) -> bool:
        """Toda discrepancia empieza en una decisión al filo del umbral."""
        return all(filo for _, filo in self.tramos)


def comparar_con_grabado(filas: list[dict]) -> Comparacion:
    """Reproduce con los parámetros grabados y compara fila a fila.

    Un tramo de discrepancias se atribuye al redondeo si entre la última fila
    que coincidió y la primera que no hubo una decisión a menos de ``FILO`` del
    umbral. Hasta ahí las dos histéresis tenían la misma ventana y la misma
    expresión, así que la primera decisión en que difieren es una que tomaron
    con medias a uno y otro lado del umbral: la del CSV, redondeada, queda a
    menos de 5e-5 de él.
    """
    n_frames, min_confidence = parametros_grabados(filas)
    emociones, margenes = reproducir(filas, n_frames, min_confidence)
    comparables = 0
    distintas: list[int] = []
    tramos: list[tuple[int, bool]] = []
    filo = False
    en_tramo = False
    for i, (fila, emocion) in enumerate(zip(filas, emociones)):
        if margenes[i] is not None and abs(margenes[i]) < FILO:
            filo = True
        if fila["manual"]:
            continue
        comparables += 1
        if emocion == fila["emocion"]:
            en_tramo = filo = False
            continue
        distintas.append(i)
        if not en_tramo:
            en_tramo = True
            tramos.append((i, filo))
    return Comparacion(
        n_frames, min_confidence, comparables, tuple(distintas), tuple(tramos),
        tuple(emociones),
    )


# --------------------------------------------------------------------------
# Medidas
# --------------------------------------------------------------------------


def contar_cambios(valores: list[str]) -> int:
    """Cuántas veces cambia el valor entre una fila y la siguiente."""
    return sum(1 for a, b in zip(valores, valores[1:]) if a != b)


def por_minuto(cambios: int, tiempos: list[float]) -> float | None:
    """Cambios por minuto en el tramo de ``tiempos``; ``None`` si no dura nada."""
    duracion = tiempos[-1] - tiempos[0]
    return cambios * 60.0 / duracion if duracion > 0 else None


def reparto(valores: list[str]) -> list[tuple[str, float]]:
    """``[(valor, fracción), ...]`` de más a menos frecuente."""
    cuenta = Counter(valores)
    total = sum(cuenta.values())
    return [(valor, n / total) for valor, n in cuenta.most_common()]


def latencia_entrada(
    filas: list[dict], emociones: list[str], esperada: str
) -> tuple[float | None, float | None]:
    """Cuánto tarda el robot en poner la expresión esperada, en segundos.

    Devuelve ``(desde el inicio, desde el voto)``:

    - desde la primera inferencia de la etiqueta, que incluye lo que tarda el
      operador en poner la cara;
    - desde el primer voto del modelo por la esperada, que es el retraso propio
      de la histéresis (0 si el robot ya la tenía antes de ese voto, y ``None``
      si el modelo no la votó nunca).

    ``(None, None)`` si el robot no llega a ponerla dentro de la etiqueta.
    """
    cambio = next((f["t"] for f, e in zip(filas, emociones) if e == esperada), None)
    if cambio is None:
        return None, None
    votado = next((f["t"] for f in filas if (voto(f) or ("",))[0] == esperada), None)
    desde_voto = None if votado is None else max(0.0, cambio - votado)
    return cambio - filas[0]["t"], desde_voto


@dataclass(frozen=True)
class ResumenEtiqueta:
    """Lo que hizo el robot en una etiqueta del protocolo.

    ``enviadas`` y ``crudas`` son repartos ``[(valor, fracción)]``: lo que se
    mandó al robot (columna ``emocion``) y lo que dijo el modelo en las
    inferencias con cara. ``ok`` es ``None`` donde no hay expresión que juzgar
    (la 5 y las de fuera del protocolo).
    """

    etiqueta: int
    esperada: str | None
    n: int
    duracion: float
    enviadas: list[tuple[str, float]]
    crudas: list[tuple[str, float]]
    sin_rostro: float
    brillo: float
    cambios: int
    por_minuto: float | None
    manual: float
    al_soltar: str | None
    ok: bool | None


def resumir_etiqueta(etiqueta: int, filas: list[dict]) -> ResumenEtiqueta:
    esperada = ESPERADAS.get(etiqueta)
    enviadas = [f["emocion"] for f in filas]
    con_rostro = [f for f in filas if f["rostro"]]
    cambios = contar_cambios(enviadas)
    manuales = [i for i, f in enumerate(filas) if f["manual"]]

    # Lo que puso el robot tras soltar la última tecla manual: la de la cara.
    al_soltar = None
    if manuales and manuales[-1] + 1 < len(filas):
        al_soltar = reparto(enviadas[manuales[-1] + 1:])[0][0]

    reparto_enviadas = reparto(enviadas)
    if etiqueta == MANUAL:
        concentrado = any(f["manual"] == esperada and f["emocion"] == esperada for f in filas)
        ok = concentrado and al_soltar is not None
    elif esperada is None:
        ok = None
    else:
        ok = reparto_enviadas[0][0] == esperada

    return ResumenEtiqueta(
        etiqueta=etiqueta,
        esperada=esperada,
        n=len(filas),
        duracion=filas[-1]["t"] - filas[0]["t"],
        enviadas=reparto_enviadas,
        crudas=reparto([f["prediccion"] or SIN_EQUIVALENTE for f in con_rostro]),
        sin_rostro=1.0 - len(con_rostro) / len(filas),
        brillo=statistics.mean(f["brillo"] for f in filas),
        cambios=cambios,
        por_minuto=por_minuto(cambios, [f["t"] for f in filas]),
        manual=len(manuales) / len(filas),
        al_soltar=al_soltar,
        ok=ok,
    )


@dataclass(frozen=True)
class Par:
    """Lo que da la histéresis con un par de parámetros.

    ``latencias`` va por cada etiqueta de entrada (1-4) que haya en el
    registro: ``(desde el inicio, desde el voto)``, de ``latencia_entrada``.
    """

    n_frames: int
    min_confidence: float
    cambios_lectura: float | None
    latencias: dict[int, tuple[float | None, float | None]]

    @property
    def no_llegan(self) -> int:
        return sum(1 for inicio, _ in self.latencias.values() if inicio is None)

    @property
    def media_inicio(self) -> float | None:
        valores = [inicio for inicio, _ in self.latencias.values() if inicio is not None]
        return statistics.mean(valores) if valores else None

    @property
    def media_voto(self) -> float | None:
        valores = [v for _, v in self.latencias.values() if v is not None]
        return statistics.mean(valores) if valores else None


def evaluar(
    filas: list[dict], grupos: dict[int, list[int]], n_frames: int, min_confidence: float
) -> Par:
    """La etiqueta 5 y las 1-4 con un par de parámetros."""
    emociones, _ = reproducir(filas, n_frames, min_confidence)
    cambios_lectura = None
    if LECTURA in grupos:
        indices = grupos[LECTURA]
        cambios_lectura = por_minuto(
            contar_cambios([emociones[i] for i in indices]), [filas[i]["t"] for i in indices]
        )
    latencias = {}
    for etiqueta in ENTRADAS:
        if etiqueta in grupos:
            indices = grupos[etiqueta]
            latencias[etiqueta] = latencia_entrada(
                [filas[i] for i in indices], [emociones[i] for i in indices],
                ESPERADAS[etiqueta],
            )
    return Par(n_frames, min_confidence, cambios_lectura, latencias)


def barrido(
    filas: list[dict],
    rejilla_n: tuple[int, ...] = REJILLA_N_FRAMES,
    rejilla_c: tuple[float, ...] = REJILLA_MIN_CONFIDENCE,
    grabado: tuple[int, float] | None = None,
) -> list[Par]:
    """Todos los pares de la rejilla, y el grabado al final si no está en ella."""
    pares = [(n, c) for n in rejilla_n for c in rejilla_c]
    if grabado is not None and grabado not in pares:
        pares.append(grabado)
    grupos = indices_por_etiqueta(filas)
    return [evaluar(filas, grupos, n, c) for n, c in pares]


# --------------------------------------------------------------------------
# Informe
# --------------------------------------------------------------------------


def _texto_reparto(valores: list[tuple[str, float]], maximo: int = 3) -> str:
    return ", ".join(f"{v} {100 * f:.0f} %" for v, f in valores[:maximo]) or "--"


def _num(valor: float | None, ancho: int, decimales: int = 2) -> str:
    return f"{valor:{ancho}.{decimales}f}" if valor is not None else f"{'--':>{ancho}}"


def imprimir_etiqueta(r: ResumenEtiqueta) -> None:
    if r.etiqueta == LECTURA:
        esperada = "-- (lectura natural: lo que cuenta es que no parpadee)"
    elif r.etiqueta == MANUAL:
        esperada = "concentrado (manual), y al soltar la de la cara"
    elif r.esperada is None:
        esperada = "-- (fuera del protocolo)"
    else:
        esperada = r.esperada
    print(f"  etiqueta {r.etiqueta}   esperada: {esperada}")
    print(f"    {r.n} inferencias en {r.duracion:.1f} s")

    veredicto = ""
    if r.ok:
        veredicto = "   OK"
    elif r.ok is False and r.etiqueta == MANUAL:
        veredicto = "   <-- NO: falta concentrado en manual, o soltarlo despues"
    elif r.ok is False:
        veredicto = f"   <-- NO: el robot puso sobre todo {r.enviadas[0][0]}"
    print(f"    robot:  {_texto_reparto(r.enviadas)}{veredicto}")
    print(f"    modelo: {_texto_reparto(r.crudas)}   (en las inferencias con cara)")

    avisos = ""
    if r.brillo < BRILLO_NEGRO:
        avisos += "  <-- camara negra"
    if r.sin_rostro > SIN_ROSTRO_AVISO:
        avisos += "  <-- muchas sin cara: la etiqueta dice poco del modelo"
    print(
        f"    sin rostro {100 * r.sin_rostro:.0f} %   brillo medio {r.brillo:.1f}"
        f"   cambios del robot {r.cambios} ({_num(r.por_minuto, 1, 1)} por minuto){avisos}"
    )
    if r.manual:
        soltar = (
            f"; al soltar: {r.al_soltar}" if r.al_soltar
            else "; no se solto dentro de la etiqueta"
        )
        print(f"    en manual el {100 * r.manual:.0f} % de las inferencias{soltar}")
    print()


def imprimir_comparacion(comp: Comparacion, filas: list[dict]) -> None:
    print(f"{'=' * 74}\nREPRODUCCION con los parametros grabados "
          f"(n_frames={comp.n_frames}, min_confidence={comp.min_confidence:g})\n{'=' * 74}\n")
    if comp.exacta:
        print(f"  Coincide con la columna 'emocion' en las {comp.comparables} inferencias "
              f"fuera de manual: EXACTA.")
        print("  El instrumento reproduce el clasificador; la rejilla de abajo vale.\n")
        return
    print(f"  NO COINCIDE en {len(comp.distintas)} de {comp.comparables} inferencias fuera "
          f"de manual, en {len(comp.tramos)} tramo(s):")
    for inicio, filo in comp.tramos[:10]:
        fila = filas[inicio]
        causa = "al filo del umbral (redondeo del CSV)" if filo else "<-- SIN EXPLICAR"
        # +2: la cabecera es la línea 1 y las filas se cuentan desde 0.
        print(f"    linea {inicio + 2} (t={fila['t']:.1f} s, etiqueta {fila['etiqueta']}): "
              f"grabada {fila['emocion']}, reproducida {comp.reproducidas[inicio]}   {causa}")
    if len(comp.tramos) > 10:
        print(f"    ... y {len(comp.tramos) - 10} tramo(s) mas")
    if comp.explicada:
        print(f"  Todos empiezan en una decision a menos de {FILO:g} del umbral: el CSV guarda")
        print("  la confianza con 4 decimales y no alcanza para decidirla. La rejilla vale.\n")
    else:
        print("  Hay discrepancias que el redondeo no explica: la reproduccion NO es la")
        print("  del clasificador y la rejilla de abajo no es fiable. Revisar antes de calibrar.\n")


def imprimir_rejilla(pares: list[Par], grabado: tuple[int, float]) -> None:
    print(f"{'=' * 74}\nREJILLA  (las predicciones crudas grabadas, otra vez por "
          f"ClassHysteresis)\n{'=' * 74}\n")
    print(f"  {'n':>2} {'conf':>5} | {'E5/min':>6} | "
          f"{'E1':>5} {'E2':>5} {'E3':>5} {'E4':>5} | {'media':>5} {'voto':>5} | no llega")
    for par in pares:
        latencias = " ".join(
            _num(par.latencias[e][0] if e in par.latencias else None, 5) for e in ENTRADAS
        )
        marca = "   <- grabado" if (par.n_frames, par.min_confidence) == grabado else ""
        print(f"  {par.n_frames:>2} {par.min_confidence:5.2f} | "
              f"{_num(par.cambios_lectura, 6, 1)} | {latencias} | "
              f"{_num(par.media_inicio, 5)} {_num(par.media_voto, 5)} | "
              f"{par.no_llegan:>2} de {len(par.latencias)}{marca}")
    print()
    print("Como leerla:")
    print("  E5/min    cambios de expresion por minuto leyendo en voz alta (etiqueta 5):")
    print("            el parpadeo que la histeresis tiene que frenar.")
    print("  E1..E4    segundos desde la primera inferencia de la etiqueta hasta que el")
    print("            robot pone la esperada. Incluye lo que tarda el operador en poner")
    print("            la cara, que es igual en todas las filas: sirve para comparar.")
    print("  voto      media desde el primer voto del modelo por la esperada: el retraso")
    print("            propio de la histeresis. El fundido del visor suma 350 ms mas.")
    print("  no llega  etiquetas 1-4 en que el robot no llego a poner la esperada.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="analizar_registro_emocion")
    parser.add_argument("csv", type=Path)
    parser.add_argument(
        "--n-frames", type=int, nargs="+", default=list(REJILLA_N_FRAMES),
        help="Valores de n_frames de la rejilla.",
    )
    parser.add_argument(
        "--min-confidence", type=float, nargs="+", default=list(REJILLA_MIN_CONFIDENCE),
        help="Valores de min_confidence de la rejilla.",
    )
    args = parser.parse_args(argv)

    if not args.csv.is_file():
        print(f"No existe {args.csv}")
        return 1
    try:
        filas = cargar(args.csv)
        if not filas:
            print("El registro esta vacio.")
            return 1
        grabado = parametros_grabados(filas)
        comparacion = comparar_con_grabado(filas)
        pares = barrido(filas, tuple(args.n_frames), tuple(args.min_confidence), grabado)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1

    duracion = filas[-1]["t"] - filas[0]["t"]
    hz = (len(filas) - 1) / duracion if duracion > 0 else 0.0
    backends = list(dict.fromkeys(f["backend"] for f in filas))
    costes = [f["latencia_ms"] for f in filas if f["latencia_ms"] is not None]
    print(f"\n{'=' * 74}\n{args.csv.name}: {len(filas)} inferencias en {duracion:.1f} s "
          f"({hz:.1f} Hz)")
    print(f"backend {' -> '.join(backends)}; histeresis grabada n_frames={grabado[0]}, "
          f"min_confidence={grabado[1]:g}")
    if costes:
        print(f"coste de cada inferencia: mediana {statistics.median(costes):.0f} ms, "
              f"maximo {max(costes):.0f} ms")
    print(f"{'=' * 74}\n")

    print("POR ETIQUETA  (cada pulsacion de ESPACIO abre una nueva)\n")
    grupos = indices_por_etiqueta(filas)
    resumenes = [resumir_etiqueta(e, [filas[i] for i in grupos[e]]) for e in sorted(grupos)]
    for resumen in resumenes:
        imprimir_etiqueta(resumen)
    juzgadas = [r for r in resumenes if r.ok is not None]
    if juzgadas:
        print(f"  {sum(1 for r in juzgadas if r.ok)} de {len(juzgadas)} etiquetas con la "
              f"expresion esperada.\n")

    imprimir_comparacion(comparacion, filas)
    imprimir_rejilla(pares, grabado)
    return 0 if comparacion.explicada else 1


if __name__ == "__main__":
    raise SystemExit(main())
