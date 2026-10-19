"""Pasa una grabación de brazos por el ``ArmTracker`` REAL, con otros parámetros.

Para qué
--------
Los parámetros del P5.6 —la focal, la rampa de la mano, la zona de reposo, el
alcance— se eligieron rehaciendo la cadena offline sobre las grabaciones con
otros valores y comparando. Esta herramienta es esa reproducción, dentro del
repositorio, para que las cifras que cita el plan se puedan volver a sacar: la
primera versión vivía en la carpeta temporal y se perdió. Sirve también para
cualquier «¿y si...?» sobre una grabación: otra focal, sin escorzo, otra zona de
reposo, con o sin el saludo.

Cómo
----
Reconstruye, frame a frame, lo que BlazePose le dio al tracker, y se lo vuelve a
dar a un ``ArmTracker`` de verdad:

- **Imagen**: los píxeles grabados (``img_*``) de hombro, codo y mano de cada
  canal.
- **Mundo**: los hombros a la separación grabada (``hombros_ancho_m`` y
  ``hombros_dz_m``; las grabaciones anteriores no los tienen y se usan 0.315 m,
  reconstruidos de la salida del escorzo del 2026-09-27, y el tronco sin girar),
  y el codo y la mano desde ``cb_*``/``pb_*``, deshaciendo la terna del tronco y
  la inclinación del eje de flexión con las mismas funciones del tracker.
- **Visibilidades**: las grabadas. Las caderas, invisibles: la reserva (se avisa
  si la grabación las usó).

Reproducida con los parámetros con que se grabó, una grabación del P5.6 en
adelante da los mismos comandos que se publicaron (``--comparar`` lo mide).

Lo que no puede: un brazo sin ``pb_*``/``cb_*`` en un frame no se puede situar en
el mundo. Hasta el 2026-10-19 solo se grababan en los frames medidos, así que
con criterios MÁS PERMISIVOS que los de la grabación esos frames se pierden; se
cuentan y se avisa. Las grabaciones desde esa fecha los traen siempre.

Uso::

    python tools/reproducir_brazos.py grabacion.csv
    python tools/reproducir_brazos.py grabacion.csv --focal 1.0 --zona-reposo 25
    python tools/reproducir_brazos.py grabacion.csv --sin-saludo --comparar
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from telmeow_teleop.arm_tracker import (  # noqa: E402
    CANALES,
    EJE_SUPERIOR_RESERVA,
    INCLINACION_FLEXION,
    LANDMARKS,
    PUNTOS_MANO,
    ArmTracker,
    canal_del_robot,
    torso_basis,
)
from telmeow_teleop.limits import Side  # noqa: E402
from telmeow_teleop.retarget import direccion  # noqa: E402

#: Separación de hombros de BlazePose para las grabaciones que no la traen, en
#: metros: la que se reconstruyó de la salida del escorzo en la grabación del
#: 2026-09-27 (estable en todas sus etiquetas, 31.5 cm).
ANCHO_HOMBROS_POR_DEFECTO: float = 0.315

#: A cuántos grados del reposo cuenta un brazo como «en reposo» en el resumen.
TOLERANCIA_REPOSO: float = 3.0

#: Codo y mano de un brazo colgando, en el marco del robot y en metros: el
#: sitio provisional de un brazo que la grabación todavía no había situado.
_COLGANDO = (np.array([0.0, -0.28, 0.0]), np.array([0.0, -0.55, 0.0]))


def _f(texto) -> float:
    try:
        return float(texto)
    except (TypeError, ValueError):
        return float("nan")


@dataclass
class _Landmark:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    visibility: float = 0.0


def leer(ruta: Path) -> list[dict]:
    with open(ruta, newline="", encoding="utf-8") as archivo:
        filas = list(csv.DictReader(archivo))
    if not filas:
        raise SystemExit(f"{ruta} está vacío")
    for nombre in ("pb_lat_L", "cb_lat_L", "img_hombro_x_L"):
        if nombre not in filas[0]:
            raise SystemExit(
                f"{ruta} no trae '{nombre}': hace falta una grabación del P5.5 en adelante"
            )
    return filas


def a_mundo(vector, base, inclinacion: float) -> np.ndarray:
    """La inversa de ``arm_tracker.vector_brazo``: de ``(L, S, A)`` en el marco
    del robot al vector en el mundo de BlazePose."""
    lateral, superior, anterior = (float(v) for v in vector)
    radianes = math.radians(inclinacion)
    coseno, seno = math.cos(radianes), math.sin(radianes)
    lateral, superior = (
        lateral * coseno - superior * seno,
        lateral * seno + superior * coseno,
    )
    u_lat, u_ant, u_sup = base
    return lateral * u_lat + superior * u_sup + anterior * u_ant


class Reconstruccion:
    """Los landmarks de BlazePose de cada frame, rehechos desde el CSV."""

    def __init__(self, filas: list[dict], invert_abduccion: bool = False) -> None:
        primera = filas[0]
        self.frame_espejado = _f(primera.get("frame_espejado", 1)) != 0
        self.mirror = _f(primera.get("espejo", 1)) != 0
        self.invert_abduccion = invert_abduccion
        self.con_ancla = "hombros_ancho_m" in primera
        self._hombros: dict[str, tuple[float, float]] = {}
        # Último codo y mano (marco del robot) que se pudieron situar, por canal.
        self._ultimo_brazo: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        # Canales de este frame que no se pudieron situar en el mundo.
        self.sin_situar: set[str] = set()

    def frame(self, fila: dict):
        """``(mundo, imagen, forma)`` para el estimador falso, o ``None`` si
        BlazePose no dio pose en ese frame.

        Un brazo sin ``pb_*``/``cb_*`` se pone donde estaba la última vez que se
        pudo situar, CON sus visibilidades grabadas: así la rampa de la mano y la
        decisión de medirlo evolucionan igual que en la grabación, que tampoco lo
        midió. Queda en ``sin_situar`` por si una variante sí llega a medirlo."""
        self.sin_situar = set()
        if not any(math.isfinite(_f(fila.get(f"vis_hombro_{s}"))) for s in ("L", "R")):
            return None
        ancho, alto = _f(fila["ancho"]), _f(fila["alto"])
        for s in ("L", "R"):
            pixel = (_f(fila[f"img_hombro_x_{s}"]), _f(fila[f"img_hombro_y_{s}"]))
            if all(map(math.isfinite, pixel)):
                self._hombros[s] = pixel
        if len(self._hombros) < 2:
            return None

        separacion = _f(fila.get("hombros_ancho_m"))
        if not math.isfinite(separacion):
            separacion = ANCHO_HOMBROS_POR_DEFECTO
        dz = _f(fila.get("hombros_dz_m"))
        if not math.isfinite(dz):
            dz = 0.0
        # BlazePose pone la x del mundo hacia la derecha de la imagen: el hombro
        # que se ve a la izquierda tiene la x menor.
        izquierda = "L" if self._hombros["L"][0] < self._hombros["R"][0] else "R"
        hombro = {}
        for s in ("L", "R"):
            x = -separacion / 2 if s == izquierda else separacion / 2
            z = dz / 2 if s == "L" else -dz / 2
            hombro[s] = np.array([x, 0.0, z])

        mundo = [_Landmark() for _ in range(33)]
        imagen = [_Landmark() for _ in range(33)]
        for etiqueta in (Side.L, Side.R):
            canal = canal_del_robot(
                etiqueta, frame_espejado=self.frame_espejado, mirror=self.mirror
            )
            s, otro = canal.value, canal.opposite.value
            nombres = LANDMARKS[etiqueta]

            vis_hombro = _f(fila.get(f"vis_hombro_{s}"))
            vis_hombro = vis_hombro if math.isfinite(vis_hombro) else 1.0
            u, v = self._hombros[s]
            mundo[nombres["hombro"]] = _Landmark(*hombro[s], visibility=vis_hombro)
            imagen[nombres["hombro"]] = _Landmark(u / ancho, v / alto, 0.0, vis_hombro)

            base = torso_basis(hombro[s], hombro[otro], EJE_SUPERIOR_RESERVA)
            inclinacion = INCLINACION_FLEXION[canal]
            if self.invert_abduccion:
                inclinacion = -inclinacion
            codo = np.array([_f(fila[f"cb_{e}_{s}"]) for e in ("lat", "sup", "ant")])
            mano = np.array([_f(fila[f"pb_{e}_{s}"]) for e in ("lat", "sup", "ant")])
            if np.all(np.isfinite(codo)) and np.all(np.isfinite(mano)):
                self._ultimo_brazo[s] = (codo, mano)
            else:
                self.sin_situar.add(s)
                # Nunca situado todavía: colgando. Lo que cuenta aquí no es la
                # posición —la grabación no lo midió— sino que sus píxeles y su
                # visibilidad lleguen al tracker, porque con ellos arranca la
                # rampa de la mano igual que arrancó en la grabación.
                codo, mano = self._ultimo_brazo.get(s, _COLGANDO)
            if base is None:
                continue
            posiciones = {
                "codo": hombro[s] + a_mundo(codo, base, inclinacion),
                "mano": hombro[s] + a_mundo(mano, base, inclinacion),
            }

            for nombre, puntos, columna in (
                ("codo", ("codo",), "vis_codo"),
                ("mano", PUNTOS_MANO, "vis_destino"),
            ):
                pixel = (_f(fila[f"img_{nombre}_x_{s}"]), _f(fila[f"img_{nombre}_y_{s}"]))
                visibilidad = _f(fila.get(f"{columna}_{s}"))
                if not (all(map(math.isfinite, pixel)) and math.isfinite(visibilidad)):
                    continue
                posicion = posiciones[nombre]
                for punto in puntos:
                    mundo[nombres[punto]] = _Landmark(*posicion, visibility=visibilidad)
                    imagen[nombres[punto]] = _Landmark(
                        pixel[0] / ancho, pixel[1] / alto, 0.0, visibilidad
                    )
        return mundo, imagen, (int(alto), int(ancho), 3)


def reproducir(filas: list[dict], invert_abduccion: bool = False, **kwargs) -> dict:
    """La grabación por un ``ArmTracker`` real. ``kwargs`` van al tracker."""
    reconstruccion = Reconstruccion(filas, invert_abduccion=invert_abduccion)
    reloj = {"t": 0.0}
    actual = {"frame": None}

    def procesar(_frame):
        if actual["frame"] is None:
            return SimpleNamespace(pose_world_landmarks=None, pose_landmarks=None)
        mundo, imagen, _ = actual["frame"]
        return SimpleNamespace(
            pose_world_landmarks=SimpleNamespace(landmark=mundo),
            pose_landmarks=SimpleNamespace(landmark=imagen),
        )

    tracker = ArmTracker(
        pose_estimator=SimpleNamespace(process=procesar, close=lambda: None),
        frame_espejado=reconstruccion.frame_espejado,
        mirror=reconstruccion.mirror,
        invert_abduccion=invert_abduccion,
        clock=lambda: reloj["t"],
        **kwargs,
    )
    salida = {"t": [], "etiqueta": [], "canales": {Side.L: [], Side.R: []}}
    incompletos = 0
    for fila in filas:
        reloj["t"] = _f(fila["t"])
        actual["frame"] = reconstruccion.frame(fila)
        forma = (480, 640, 3) if actual["frame"] is None else actual["frame"][2]
        pose = tracker.process(np.zeros(forma, dtype=np.uint8))
        mando = tracker.comando(pose)
        salida["t"].append(reloj["t"])
        salida["etiqueta"].append(fila.get("etiqueta", "0"))
        for canal in (Side.L, Side.R):
            if tracker.estimado[canal] and canal.value in reconstruccion.sin_situar:
                # La variante midió un brazo que la grabación no situó: lo hizo
                # con su última posición conocida, no con la verdadera.
                incompletos += 1
            campo_abd, campo_flex = CANALES[canal]
            salida["canales"][canal].append({
                "estimado": tracker.estimado[canal],
                "comando": (getattr(mando, campo_abd), getattr(mando, campo_flex)),
                "peso_mano": tracker.pesos_mano.get(canal),
                "confianza": tracker.confianzas_mano.get(canal),
                "reposo": tracker.pesos_reposo.get(canal),
                "saludo": tracker.pesos_saludo.get(canal, 0.0),
                "mano": tracker.vectores.get(canal),
            })
    salida["incompletos"] = incompletos
    salida["con_ancla"] = reconstruccion.con_ancla
    salida["reposo"] = {canal: tracker.reposo[canal] for canal in (Side.L, Side.R)}
    return salida


def resumen(salida: dict) -> dict:
    """Por (etiqueta, canal): reposo, punta del robot, mano y saludo."""
    resultado = {}
    etiquetas = salida["etiqueta"]
    for canal in (Side.L, Side.R):
        reposo = np.array(salida["reposo"][canal])
        for etiqueta in dict.fromkeys(etiquetas):
            frames = [
                f for f, e in zip(salida["canales"][canal], etiquetas) if e == etiqueta
            ]
            comandos = np.array([f["comando"] for f in frames], dtype=float)
            medidos = [f for f in frames if f["estimado"]]
            puntas = np.array([direccion(a, b) for a, b in comandos])
            desde_la_cruz = np.degrees(np.arccos(np.clip(puntas[:, 0], -1.0, 1.0)))
            elevacion = np.degrees(np.arcsin(np.clip(puntas[:, 1], -1.0, 1.0)))
            en_medidos = np.array([f["estimado"] for f in frames])
            profundidad = [100 * f["mano"][2] for f in medidos if f["mano"] is not None]
            resultado[(etiqueta, canal)] = {
                "frames": len(frames),
                "medido": 100 * float(np.mean(en_medidos)),
                "en_reposo": 100 * float(
                    np.mean(np.hypot(*(comandos - reposo).T) < TOLERANCIA_REPOSO)
                ),
                "desde_la_cruz": float(np.median(desde_la_cruz[en_medidos])) if any(en_medidos) else float("nan"),
                "elevacion": float(np.median(elevacion[en_medidos])) if any(en_medidos) else float("nan"),
                "profundidad_mano": float(np.median(profundidad)) if profundidad else float("nan"),
                "saludo": 100 * float(np.mean([f["saludo"] > 0.0 for f in frames])),
            }
    return resultado


def comparar(filas: list[dict], salida: dict) -> dict:
    """Diferencia entre los comandos reproducidos y los grabados, por canal, en
    grados (sobre las dos articulaciones a la vez)."""
    resultado = {}
    for canal in (Side.L, Side.R):
        campo_abd, campo_flex = CANALES[canal]
        grabados = np.array([(_f(f[campo_abd]), _f(f[campo_flex])) for f in filas])
        reproducidos = np.array([f["comando"] for f in salida["canales"][canal]], dtype=float)
        diferencia = np.hypot(*(grabados - reproducidos).T)
        diferencia = diferencia[np.isfinite(diferencia)]
        resultado[canal] = {
            "mediana": float(np.median(diferencia)),
            "p95": float(np.percentile(diferencia, 95)),
            "max": float(np.max(diferencia)),
        }
    return resultado


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("csv", type=Path)
    parser.add_argument("--focal", type=float, help="focal_relativa (por defecto, la del tracker)")
    parser.add_argument("--zona-reposo", type=float)
    parser.add_argument("--rampa-mano", type=float)
    parser.add_argument("--alcance", type=float, help="alcance_humano")
    parser.add_argument("--mapeo", choices=("posicion", "direccion", "articular"))
    parser.add_argument("--sin-escorzo", action="store_true")
    parser.add_argument("--sin-saludo", action="store_true")
    parser.add_argument(
        "--comparar", action="store_true",
        help="medir la diferencia con los comandos grabados",
    )
    args = parser.parse_args(argv)

    kwargs = {}
    for clave, valor in (
        ("focal_relativa", args.focal),
        ("zona_reposo", args.zona_reposo),
        ("rampa_mano", args.rampa_mano),
        ("alcance_humano", args.alcance),
        ("mapeo", args.mapeo),
    ):
        if valor is not None:
            kwargs[clave] = valor
    if args.sin_escorzo:
        kwargs["escorzo"] = False
    if args.sin_saludo:
        kwargs["saludo"] = False

    # La salida va sin tildes, como la de los otros analizadores: la consola de
    # Windows no siempre las muestra.
    filas = leer(args.csv)
    salida = reproducir(filas, **kwargs)
    print(f"{args.csv}: {len(filas)} frames, parametros {kwargs or 'por defecto'}")
    if not salida["con_ancla"]:
        print(
            f"  Sin hombros_ancho_m: se usan {ANCHO_HOMBROS_POR_DEFECTO} m y el tronco sin"
            " girar (grabacion anterior al P5.6)."
        )
    origenes = {f.get("tronco_origen") for f in filas}
    if origenes - {"reserva", None}:
        print("  OJO: la grabacion uso las caderas en algunos frames; aqui, siempre la reserva.")
    if salida["incompletos"]:
        print(
            f"  OJO: {salida['incompletos']} veces se midio un brazo que la grabacion no"
            " situo (sin pb_*/cb_*); se uso su ultima posicion conocida."
        )
    print(
        "\n  etiqueta canal | medido | en reposo | punta desde la cruz | elevacion"
        " | profundidad de la mano | saludo"
    )
    for (etiqueta, canal), r in resumen(salida).items():
        print(
            f"  {etiqueta:>8} {canal.value:>5} | {r['medido']:5.0f}% | {r['en_reposo']:7.0f}%"
            f" | {r['desde_la_cruz']:13.1f} grd | {r['elevacion']:5.1f} grd"
            f" | {r['profundidad_mano']:19.1f} cm | {r['saludo']:4.0f}%"
        )
    if args.comparar:
        print("\n  Comandos reproducidos frente a los grabados (grados):")
        for canal, r in comparar(filas, salida).items():
            print(
                f"    arm{canal.value}: mediana {r['mediana']:.2f}  p95 {r['p95']:.2f}"
                f"  maximo {r['max']:.1f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
