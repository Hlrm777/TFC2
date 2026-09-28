"""Analiza un registro del arm tracker (``--record``).

Convierte una sesión de brazos en números. Es hermano de
``analizar_registro.py`` —el de la cabeza— pero herramienta aparte, porque lo
que hay que responder es distinto: allí la pregunta era «qué ganancia hace falta
para que el operador cubra el rango del robot», y aquí son tres:

1. **¿Cuánto tiembla cada canal, y tiembla más la flexión que la abducción?**
   Es la cifra que respalda la limitación que el documento declara. La
   abducción vive en el plano de imagen; la flexión depende de la coordenada de
   profundidad de BlazePose, que es su componente débil en monocular. Si la
   medición lo confirma, la limitación deja de ser una afirmación y pasa a ser
   un dato.

2. **¿Conviene apuntar el brazo con la mano o con el codo?** La comparación es
   relativa y sobre los mismos frames, así que es limpia: el bloque POR DESTINO
   los enfrenta igual que el de la cabeza enfrenta ``mesh`` con ``pnp``.

3. **¿Se ve lo que hace falta ver?** El resumen de visibilidad dice qué
   porcentaje del tiempo cada landmark superó el umbral. Es la evidencia de si
   el codo se ve, y de si las caderas sostienen la vertical del tronco o se
   estuvo tirando de la reserva.

De la herramienta de la cabeza se reutilizan las tres funciones de estadística,
que ya demostraron su valor y no tienen nada de específico de un eje:
``temblor`` (la repetibilidad citable), ``percentil`` (el H24: en una postura
sostenida el extremo es el instante de entrar y salir, la meseta la recoge el
percentil) y ``diagnostico_meseta`` (avisa si se volvió al centro dentro de una
etiqueta, que es lo que inutilizó ``limites.csv``).

CONVENCION DE LAS COLUMNAS, que conviene tener presente al leer la salida: los
sufijos ``_L`` y ``_R`` son SIEMPRE el canal del robot, también en las columnas
``crudo_*`` y ``vis_*``. O sea que ``crudo_abd_L`` es el ángulo del operador que
manda sobre ``armL``, no «el ángulo de su brazo izquierdo». Con el espejo
activado son el mismo brazo, pero la columna ``espejo`` deja constancia de con
qué política se grabó y hay que mirarla antes de sacar conclusiones.

Uso::

    python tools/analizar_registro_brazos.py brazos.csv
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from analizar_registro import (  # noqa: E402
    diagnostico_meseta,
    percentil,
    temblor,
)
from telmeow_teleop.limits import (  # noqa: E402
    ARM_ABDUCCION_1D,
    FLEXION_RANGE,
    Side,
)

#: Excursión mínima, en grados, para proponer una ganancia en ese sentido.
#:
#: Mismo criterio que en la herramienta de la cabeza: con el umbral bajo, cada
#: etiqueta propone ganancias en los cuatro sentidos, incluidos los ejes que el
#: operador ni movió, y esas cifras sin sentido compiten en pantalla con la que
#: sí importa.
EXCURSION_MINIMA = 5.0

#: Umbral de visibilidad con el que se cuenta un landmark como observado. Es el
#: mismo que ``ArmConfig.min_visibility`` usa por defecto.
UMBRAL_VISIBILIDAD = 0.5

#: (columna cruda, columna publicada, rótulo) por canal del robot.
CANALES = (
    ("crudo_abd_L", "armL", "armL  abduccion"),
    ("crudo_flex_L", "armLtw", "armLtw flexion "),
    ("crudo_abd_R", "armR", "armR  abduccion"),
    ("crudo_flex_R", "armRtw", "armRtw flexion "),
)

COLUMNAS_VISIBILIDAD = (
    "vis_hombro_L", "vis_destino_L", "vis_codo_L",
    "vis_hombro_R", "vis_destino_R", "vis_codo_R",
    "vis_cadera",
)


def _flotante(valor: str) -> float:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return float("nan")


def cargar(ruta: Path) -> list[dict]:
    with open(ruta, newline="", encoding="utf-8") as f:
        filas = list(csv.DictReader(f))

    numericas = ["t"]
    numericas += [crudo for crudo, _, _ in CANALES]
    numericas += [salida for _, salida, _ in CANALES]
    numericas += list(COLUMNAS_VISIBILIDAD)
    numericas += [
        "gain_abduccion", "gain_aduccion", "gain_flexion", "gain_extension",
        "puntos_destino_L", "puntos_destino_R",
    ]
    # P5.3 y P5.4: dirección unitaria, vectores en metros y extensión. Las
    # grabaciones anteriores no las tienen y quedan en NaN, que es lo que las
    # secciones que las usan comprueban antes de imprimir nada.
    for s in ("L", "R"):
        numericas += [f"h_{e}_{s}" for e in ("lat", "sup", "ant")]
        numericas += [f"p_{e}_{s}" for e in ("lat", "sup", "ant")]
        numericas += [f"c_{e}_{s}" for e in ("lat", "sup", "ant")]
        numericas.append(f"ext_{s}")
        # P5.5: lo que dio BlazePose antes del escorzo, el peso de la mano en el
        # híbrido y el de la zona de reposo.
        numericas += [f"pb_{e}_{s}" for e in ("lat", "sup", "ant")]
        numericas += [f"peso_mano_{s}", f"reposo_{s}"]

    for fila in filas:
        for clave in numericas:
            fila[clave] = _flotante(fila.get(clave, ""))
        for clave in ("etiqueta", "espejo", "recortado_L", "recortado_R",
                      "tronco_caderas"):
            try:
                fila[clave] = int(fila.get(clave, 0))
            except (TypeError, ValueError):
                fila[clave] = 0
        # Estas por separado, con default 1: una grabación anterior a que
        # existiera la etapa de salida no tiene la columna, y sus filas SI eran
        # todas estimadas. Con default 0 se filtrarían todas y el informe
        # saldría vacío sin decir por qué.
        for clave in ("estimado_L", "estimado_R"):
            try:
                fila[clave] = int(fila.get(clave, 1))
            except (TypeError, ValueError):
                fila[clave] = 1
    return filas


def _columna_estimado(clave_salida: str) -> str:
    """Columna ``estimado_*`` que corresponde a un canal publicado."""
    return "estimado_R" if clave_salida.startswith("armR") else "estimado_L"


def _validos(
    filas: list[dict], clave: str, solo_si: str | None = None
) -> tuple[list[float], list[float]]:
    """Valores no NaN de una columna, con sus tiempos.

    Hay que filtrar: un brazo fuera de cuadro deja su canal crudo vacío ese
    frame, y meter esos huecos en la desviación típica la contamina con el hueco
    en vez de con el temblor.

    ``solo_si`` restringe a las filas en que esa columna valga 1. Es
    imprescindible para las columnas publicadas: cuando el brazo no se ve, el
    comando sigue teniendo valor —vuelve al reposo por una rampa— y medir el
    temblor sobre la rampa daría una cifra del limitador de velocidad, no del
    estimador. Que es justo la clase de número que no se puede citar.
    """
    valores, tiempos = [], []
    for fila in filas:
        if solo_si is not None and not fila.get(solo_si, 1):
            continue
        valor = fila[clave]
        if valor == valor:  # descarta NaN
            valores.append(valor)
            tiempos.append(fila["t"])
    return valores, tiempos


def _fmt(valor: float | None) -> str:
    return f"{valor:5.3f}" if valor is not None else "  -- "


def temblor_direccion(
    vectores: list[tuple[float, float, float]],
    tiempos: list[float],
    ventana: float = 1.0,
) -> float | None:
    """Temblor de la DIRECCION del brazo, en grados: mediana, sobre ventanas de
    ``ventana`` segundos, de la desviación angular RMS respecto a la dirección
    media de la ventana.

    Es la medida que no depende del mapeo ni de la parametrización. El temblor
    de ``crudo_flex`` se infla cerca de la cruz por pura geometría —la flexión
    es un azimut, y cerca del polo un milímetro de mano la cambia mucho—, y el
    de los canales publicados mezcla el filtro con la escala de la envolvente.
    Este no tiene ninguno de los dos problemas.
    """
    if len(vectores) < 10:
        return None
    grupos: dict[int, list[tuple[float, float, float]]] = defaultdict(list)
    for v, t in zip(vectores, tiempos):
        grupos[int(t // ventana)].append(v)
    desviaciones = []
    for grupo in grupos.values():
        if len(grupo) < 5:
            continue
        media = [sum(c) / len(grupo) for c in zip(*grupo)]
        norma = math.sqrt(sum(c * c for c in media))
        if norma < 1e-9:
            continue
        media = [c / norma for c in media]
        angulos = []
        for v in grupo:
            nv = math.sqrt(sum(c * c for c in v))
            coseno = sum(a * b for a, b in zip(v, media)) / nv
            angulos.append(math.degrees(math.acos(max(-1.0, min(1.0, coseno)))))
        desviaciones.append(math.sqrt(sum(a * a for a in angulos) / len(angulos)))
    return statistics.median(desviaciones) if desviaciones else None


def _vectores(filas: list[dict], prefijo: str, lado: str):
    """Vectores ``(lat, sup, ant)`` de un prefijo (``h`` o ``p``) en las filas
    estimadas de ese canal, con sus tiempos."""
    vectores, tiempos = [], []
    for fila in filas:
        if not fila.get(f"estimado_{lado}", 1):
            continue
        v = tuple(
            fila.get(f"{prefijo}_{e}_{lado}", float("nan")) for e in ("lat", "sup", "ant")
        )
        if all(c == c for c in v):
            vectores.append(v)
            tiempos.append(fila["t"])
    return vectores, tiempos


def resumen_direccion(filas: list[dict]) -> None:
    """Temblor de dirección, extensión y ruido de profundidad, por canal.

    EL RUIDO DE PROFUNDIDAD es la cifra del §3.3.1 en su forma limpia: el
    temblor de la posición de la mano en CENTIMETROS en cada eje del tronco.
    Con la dirección normalizada no se podía separar —al normalizar, la
    profundidad se mezcla con la vertical—; con ``p_*``, en metros, sí. Solo
    significa algo en una etiqueta QUIETA: en movimiento mide el movimiento.
    """
    for lado in ("L", "R"):
        direcciones, t_dir = _vectores(filas, "h", lado)
        if len(direcciones) < 10:
            continue
        linea = (
            f"    arm{lado}  direccion: temblor "
            f"{_fmt(temblor_direccion(direcciones, t_dir))} grados"
        )
        extensiones = [
            f.get(f"ext_{lado}", float("nan"))
            for f in filas
            if f.get(f"estimado_{lado}", 1)
        ]
        extensiones = [e for e in extensiones if e == e]
        if len(extensiones) >= 10:
            linea += (
                f"   extension med {statistics.median(extensiones):.2f}"
                f" [p05 {percentil(extensiones, 5):.2f},"
                f" p95 {percentil(extensiones, 95):.2f}]"
            )
        print(linea)

        posiciones, t_pos = _vectores(filas, "p", lado)
        if len(posiciones) < 10:
            continue
        cm = [temblor([100.0 * p[k] for p in posiciones], t_pos) for k in range(3)]
        if None in cm:
            continue
        plano = (cm[0] + cm[1]) / 2.0
        razon = cm[2] / plano if plano > 0 else float("nan")
        veredicto = (
            "la PROFUNDIDAD tiembla mas: respalda el 3.3.1"
            if razon > 1.5
            else ("EMPATAN" if razon > 0.67 else "OJO: tiembla menos la profundidad")
        )
        print(
            f"           mano en cm: lateral {cm[0]:.2f}  vertical {cm[1]:.2f}"
            f"  PROFUNDIDAD {cm[2]:.2f}  -> x{razon:.2f} ({veredicto})"
        )


def resumen_bloque(nombre: str, filas: list[dict]) -> None:
    if len(filas) < 2:
        print(f"  {nombre}: solo {len(filas)} muestras, se omite")
        return

    duracion = filas[-1]["t"] - filas[0]["t"]
    fps = len(filas) / duracion if duracion > 0 else 0.0
    print(f"  {nombre}")
    print(f"    {len(filas):5d} muestras en {duracion:5.1f} s  ->  {fps:5.1f} fps")

    temblores: dict[str, float] = {}
    for clave_cruda, clave_salida, rotulo in CANALES:
        crudos, tiempos = _validos(filas, clave_cruda)
        if len(crudos) < 10:
            print(f"    {rotulo}  sin muestras suficientes")
            continue
        salidas, tiempos_salida = _validos(
            filas, clave_salida, solo_si=_columna_estimado(clave_salida)
        )

        temblor_crudo = temblor(crudos, tiempos)
        temblor_salida = temblor(salidas, tiempos_salida)
        if temblor_crudo is not None:
            temblores[rotulo.strip()] = temblor_crudo

        print(
            f"    {rotulo}  mediana {statistics.median(crudos):+7.2f}"
            f"   p05 {percentil(crudos, 5):+7.2f}"
            f"   p95 {percentil(crudos, 95):+7.2f}"
            f"   TEMBLOR {_fmt(temblor_crudo)} -> {_fmt(temblor_salida)}"
        )
        diagnostico = diagnostico_meseta(crudos)
        aviso = diagnostico[1] if diagnostico else ""
        print(
            f"                     extremos "
            f"[{min(crudos):+7.2f},{max(crudos):+7.2f}]"
            f"   n={len(crudos)}{aviso}"
        )

    # La comparación que el documento necesita: ¿tiembla más la flexión?
    abduccion = [v for k, v in temblores.items() if "abduccion" in k]
    flexion = [v for k, v in temblores.items() if "flexion" in k]
    if abduccion and flexion:
        media_abd = statistics.mean(abduccion)
        media_flex = statistics.mean(flexion)
        if media_abd > 0:
            razon = media_flex / media_abd
            veredicto = (
                "la flexion tiembla mas, como se esperaba"
                if razon > 1.15
                else (
                    "EMPATAN: la limitacion declarada no se sostiene con estos datos"
                    if razon > 0.87
                    else "OJO: tiembla mas la ABDUCCION, al reves de lo esperado"
                )
            )
            print(
                f"    abduccion {media_abd:.3f} vs flexion {media_flex:.3f} "
                f"-> x{razon:.2f}  ({veredicto})"
            )

    resumen_direccion(filas)

    for lado, clave in (("L", "recortado_L"), ("R", "recortado_R")):
        recortados = sum(f[clave] for f in filas)
        if recortados:
            print(
                f"    arm{lado} recortado por el tope del robot en {recortados} "
                f"de {len(filas)} muestras ({100 * recortados / len(filas):.0f} %)"
            )
    print()


def resumen_visibilidad(filas: list[dict]) -> None:
    print(f"{'=' * 74}\nVISIBILIDAD DE LOS LANDMARKS\n{'=' * 74}\n")
    print(
        f"  Porcentaje de frames por encima del umbral de {UMBRAL_VISIBILIDAD}.\n"
        "  Es la evidencia de si el codo se ve lo bastante como para apuntar con\n"
        "  el, y de si las caderas sostienen la vertical del tronco.\n"
    )
    for clave in COLUMNAS_VISIBILIDAD:
        valores = [f[clave] for f in filas if f[clave] == f[clave]]
        if not valores:
            print(f"  {clave:16s}  sin datos")
            continue
        por_encima = sum(1 for v in valores if v >= UMBRAL_VISIBILIDAD)
        fraccion = 100 * por_encima / len(valores)
        aviso = "   <-- por debajo la mayor parte del tiempo" if fraccion < 50 else ""
        print(
            f"  {clave:16s}  {fraccion:5.1f} %   mediana "
            f"{statistics.median(valores):.3f}{aviso}"
        )

    # ¿Se cumple la promesa de los cuatro puntos de mano, o el centroide
    # colapsa a la muñeca? Es una afirmación del plan, y conviene medirla en
    # vez de repetirla.
    for lado in ("L", "R"):
        valores, _ = _validos(filas, f"puntos_destino_{lado}")
        if valores:
            media = statistics.mean(valores)
            cuantos_cuatro = 100 * sum(1 for v in valores if v >= 4) / len(valores)
            print(
                f"\n  Punto de destino {lado}: {media:.2f} landmarks de media"
                f"   con los 4 en el {cuantos_cuatro:.1f} % de los frames"
            )
            if media < 1.5:
                print(
                    "  AVISO: el centroide colapsa casi siempre a la muneca, asi que\n"
                    "  'cuatro puntos son mas estables que uno' NO se sostiene aqui."
                )

    # Tres estados, porque una vertical RETENIDA no es una vertical observada.
    # La columna 'tronco_caderas' solo cuenta los frames en que las caderas se
    # vieron de verdad; 'tronco_origen' desglosa el resto.
    de_caderas = sum(f["tronco_caderas"] for f in filas)
    fraccion = 100 * de_caderas / len(filas)
    print(
        f"\n  Vertical del tronco OBSERVADA en las caderas en el {fraccion:.1f} % "
        f"de los frames."
    )
    origenes = defaultdict(int)
    for fila in filas:
        origenes[fila.get("tronco_origen", "?")] += 1
    if set(origenes) - {"?"}:
        desglose = "  ".join(
            f"{nombre} {100 * n / len(filas):.1f} %"
            for nombre, n in sorted(origenes.items(), key=lambda kv: -kv[1])
        )
        print(f"  Desglose: {desglose}")
    if fraccion < 90:
        print(
            "  AVISO: el resto se resolvio con una vertical retenida de un frame\n"
            "  anterior o con la vertical de imagen, que asume al operador sentado\n"
            "  y erguido. Hay que declararlo, o reencuadrar la camara para que las\n"
            "  caderas entren."
        )
    print()


def resumen_p5_5(filas: list[dict]) -> None:
    """Híbrido, zona de reposo y escorzo: cuánto actuó cada uno.

    La cifra del escorzo es la que valida el H39: cuánto movió la mano en
    profundidad respecto a lo que dijo BlazePose. Si en los gestos de manos
    arriba la mueve 20-30 cm hacia el plano y en los de acercar la mano a la
    cámara apenas la toca, está haciendo lo que debe.
    """
    # Grabaciones anteriores al P5.5: las columnas no existen y cargar() las
    # dejó en NaN en todas las filas.
    if all(math.isnan(f["peso_mano_L"]) and math.isnan(f["peso_mano_R"]) for f in filas):
        return
    print(f"{'=' * 74}\nHIBRIDO, REPOSO Y ESCORZO (P5.5)\n{'=' * 74}\n")
    for lado in ("L", "R"):
        medidos = [f for f in filas if f.get(f"estimado_{lado}", 1)]
        pesos = [f[f"peso_mano_{lado}"] for f in medidos if f[f"peso_mano_{lado}"] == f[f"peso_mano_{lado}"]]
        reposos = [f[f"reposo_{lado}"] for f in medidos if f[f"reposo_{lado}"] == f[f"reposo_{lado}"]]
        if not pesos:
            continue
        solo_codo = 100 * sum(1 for p in pesos if p < 0.05) / len(pesos)
        mezcla = 100 * sum(1 for p in pesos if 0.05 <= p < 0.95) / len(pesos)
        en_reposo = 100 * sum(1 for r in reposos if r > 0.95) / max(1, len(reposos))
        print(
            f"  arm{lado}: el codo llevo el brazo en el {solo_codo:.0f} % de los frames"
            f" medidos, mezcla en el {mezcla:.0f} %; reposo exacto en el {en_reposo:.0f} %"
        )
        cambios = [
            100.0 * (f[f"p_ant_{lado}"] - f[f"pb_ant_{lado}"])
            for f in medidos
            if f[f"p_ant_{lado}"] == f[f"p_ant_{lado}"] and f[f"pb_ant_{lado}"] == f[f"pb_ant_{lado}"]
        ]
        if len(cambios) >= 10:
            print(
                f"         escorzo: la mano cambio en profundidad mediana {statistics.median(cambios):+.1f} cm"
                f" [p05 {percentil(cambios, 5):+.1f}, p95 {percentil(cambios, 95):+.1f}]"
                "  (negativo = la llevo hacia el plano)"
            )
    print()


def resumen_ganancias(filas: list[dict]) -> None:
    """Ganancias que harían falta para cubrir el rango del robot.

    El objetivo de la abducción es ``ARM_ABDUCCION_1D`` y no el rango de la
    tabla 2D, porque ese depende de la flexión y daría un número distinto en
    cada frame. Es la misma separación que el P4.3 tuvo que introducir entre
    ``HEAD_PAN_TARGET_RANGE`` y el tope mecánico del pan, y por el mismo motivo:
    dividir por el rango equivocado propone ganancias infladas.
    """
    print(f"{'=' * 74}\nGANANCIAS SUGERIDAS\n{'=' * 74}\n")
    mapeos = {f.get("mapeo") or "articular" for f in filas}
    if "articular" not in mapeos:
        print(
            f"  No aplican: se grabo con el mapeo {', '.join(sorted(mapeos))},\n"
            "  que escala el gesto dentro de la envolvente del robot en vez de\n"
            "  usar ganancias (H36). Su papel lo cumple ARM_ALCANCE_HUMANO.\n"
        )
        return
    print(
        "  Desde el PERCENTIL, no desde el extremo (H24). Objetivo de la\n"
        f"  abduccion: el rango 1D a flexion 0 ({ARM_ABDUCCION_1D[Side.L]}).\n"
        f"  Objetivo de la flexion: su rango mecanico {FLEXION_RANGE}.\n"
    )
    propuestas: dict[str, list[float]] = defaultdict(list)
    for clave_cruda, _, rotulo in CANALES:
        crudos, _ = _validos(filas, clave_cruda)
        if len(crudos) < 10:
            continue
        lado = Side.L if clave_cruda.endswith("_L") else Side.R
        es_abduccion = "abd" in clave_cruda
        objetivo = ARM_ABDUCCION_1D[lado] if es_abduccion else FLEXION_RANGE

        p05, p95 = percentil(crudos, 5), percentil(crudos, 95)
        if p95 > EXCURSION_MINIMA:
            clave = "abduccion" if es_abduccion else "flexion"
            propuestas[clave].append(abs(objetivo[1] / p95))
        if p05 < -EXCURSION_MINIMA:
            clave = "aduccion" if es_abduccion else "extension"
            propuestas[clave].append(abs(objetivo[0] / p05))

    if not propuestas:
        print("  El operador apenas se movio: no hay ganancia que proponer.\n")
        return

    for clave in ("abduccion", "aduccion", "flexion", "extension"):
        valores = propuestas.get(clave)
        if not valores:
            print(f"  {clave:10s}  --")
            continue
        # La menor de las dos propuestas: la del brazo que mas lejos llego. Con
        # la mayor, ese brazo se pasaria de tope.
        print(
            f"  {clave:10s}  {min(valores):.2f}"
            + (f"   (el otro brazo pedia {max(valores):.2f})"
               if len(valores) > 1 and abs(max(valores) - min(valores)) > 0.05
               else "")
        )
    print(
        "\n  Si salen todas cerca de 1.00, el operador ya cubre el rango del\n"
        "  robot y NO hacen falta ganancias. Eso tambien es un resultado.\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="analizar_registro_brazos")
    parser.add_argument("csv", type=Path)
    args = parser.parse_args(argv)

    if not args.csv.is_file():
        print(f"No existe {args.csv}")
        return 1

    filas = cargar(args.csv)
    if not filas:
        print("El registro esta vacio.")
        return 1

    print(f"\n{'=' * 74}\n{args.csv.name}: {len(filas)} muestras\n{'=' * 74}\n")

    espejos = {f["espejo"] for f in filas}
    politica = ", ".join(
        "izq->izq" if e else "izq->der" for e in sorted(espejos, reverse=True)
    )
    print(f"Politica de espejo durante la grabacion: {politica}")
    mapeos = sorted({f.get("mapeo") or "articular" for f in filas})
    print(f"Mapeo(s) durante la grabacion: {', '.join(mapeos)}")
    if len(espejos) > 1:
        print(
            "AVISO: el espejo cambio a mitad de sesion. Los canales de antes y\n"
            "de despues corresponden a brazos distintos del operador: analiza\n"
            "los tramos por separado o vuelve a grabar."
        )
    print()

    print("POR ETIQUETA  (cada pulsacion de ESPACIO abre una nueva)\n")
    por_etiqueta: dict[int, list[dict]] = defaultdict(list)
    for fila in filas:
        por_etiqueta[fila["etiqueta"]].append(fila)
    for etiqueta in sorted(por_etiqueta):
        resumen_bloque(f"etiqueta {etiqueta}", por_etiqueta[etiqueta])

    destinos = {f.get("target", "") for f in filas}
    if len(destinos) > 1:
        print(f"{'=' * 74}\nPOR DESTINO  (mano frente a codo)\n{'=' * 74}\n")
        print(
            "  Comparacion RELATIVA sobre los mismos frames: lo que cambia entre\n"
            "  los dos bloques es solo el punto que orienta el brazo, asi que la\n"
            "  diferencia de temblor es atribuible a esa eleccion.\n"
        )
        por_destino: dict[str, list[dict]] = defaultdict(list)
        for fila in filas:
            por_destino[fila["target"]].append(fila)
        for destino in sorted(por_destino):
            resumen_bloque(destino, por_destino[destino])

    resumen_visibilidad(filas)
    resumen_p5_5(filas)
    resumen_ganancias(filas)

    resoluciones = {(f.get("ancho"), f.get("alto")) for f in filas}
    print(
        "Resolucion(es) de captura: "
        + ", ".join(f"{w}x{h}" for w, h in sorted(resoluciones))
    )
    print(
        "\nNota: la desviacion en un tramo de REPOSO es el temblor; en un tramo\n"
        "de movimiento no significa nada (mide el propio movimiento).\n"
        "Nota: los sufijos _L y _R son el CANAL DEL ROBOT, no el brazo del\n"
        "operador. Con el espejo en izq->izq coinciden."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
