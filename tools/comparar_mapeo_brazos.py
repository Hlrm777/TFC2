"""Compara los mapeos de brazos reproduciendo una sesión grabada.

Pasa la MISMA sesión (un CSV de ``arm_tracker --record``) por el mapeo
``articular`` del P5 original, por el ``direccion`` del P5.3 y —si el CSV trae
los vectores en metros ``p_*`` y ``c_*``, grabaciones del P5.4 en adelante—
por el ``posicion`` del P5.4, con la misma cadena real de ``ArmTracker``
—filtros, recorte, gracia, reposo y límite de velocidad— y sin cámara. Así la
comparación es sobre los mismos frames y no depende de que el operador repita
el gesto.

De dónde sale la dirección del brazo de cada frame:

- Si el CSV tiene ``h_lat_*``/``h_sup_*``/``h_ant_*`` (grabaciones del P5.3 en
  adelante), de ahí.
- Si no, se reconstruye de ``crudo_abd_*``/``crudo_flex_*`` con la cinemática
  directa del cardán. Es exacto salvo en el polo, donde la flexión cruda era la
  última válida; ahí el error es de décimas de grado.

Qué mide, por canal del robot y por mapeo:

``saltos de rama``
    Frames en que el OBJETIVO (antes del límite de velocidad) cambia la flexión
    más de 90° de un frame medido al siguiente. Es la firma del «giro
    completo»: la dirección del brazo apenas cambia y el comando se va al otro
    extremo.
``desvio``
    Recorrido de la punta del robot dividido por el recorrido de la dirección
    del operador. Un mapeo que da vueltas para llegar a posiciones contiguas
    tiene un desvío alto. (Con escala, el robot recorre MENOS que el
    operador, así que el desvío sano es menor que 1.)
``correlacion``
    Entre cada componente de la dirección del operador (lateral, superior,
    anterior) y la de la punta del robot. La ANTERIOR es la que mide si «acercar
    las manos a la cámara» lleva el brazo del robot hacia adelante.
``temblor``
    Paso angular de la punta del robot por frame (p50 y p95), solo en frames
    medidos: en reposo o en rampa no es temblor.
``corr prof``
    Solo con ``p_*``: entre la PROFUNDIDAD de la mano en metros —cuánto está
    por delante del hombro, sin normalizar— y la componente anterior de la
    punta del robot. Es la queja del H38 en un número: «recojo la mano y el
    robot sigue adelante». La dirección normalizada no la ve; el mapeo
    ``posicion`` existe para verla.

Uso::

    python tools/comparar_mapeo_brazos.py brazos.csv [--figura comparacion.png]
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from telmeow_teleop.arm_tracker import CANALES, ArmAngles, ArmTracker  # noqa: E402
from telmeow_teleop.limits import FLEXION_RANGE, Side  # noqa: E402
from telmeow_teleop.retarget import direccion  # noqa: E402


class _SinPose:
    """Sustituto de BlazePose: aquí no se procesan imágenes."""

    def process(self, _frame):
        return None

    def close(self):
        pass


def _float(texto: str) -> float:
    try:
        return float(texto)
    except (TypeError, ValueError):
        return float("nan")


def leer_sesion(ruta: Path) -> dict:
    """Tiempos, y por canal la dirección del operador ``(N, 3)`` (NaN si no
    se midió) y sus ángulos crudos ``(N, 2)``."""
    with open(ruta, newline="", encoding="utf-8") as archivo:
        filas = list(csv.DictReader(archivo))
    if not filas:
        raise SystemExit(f"{ruta} está vacío")
    t = np.array([_float(f["t"]) for f in filas])
    sesion = {
        "t": t, "direccion": {}, "crudo": {}, "fuente": {},
        "vector": {}, "largo": {},
        "con_posicion": "p_lat_L" in filas[0] and "c_lat_L" in filas[0],
    }
    for lado in (Side.L, Side.R):
        s = lado.value
        crudo = np.array(
            [[_float(f.get(f"crudo_abd_{s}")), _float(f.get(f"crudo_flex_{s}"))] for f in filas]
        )
        if f"h_lat_{s}" in filas[0]:
            h = np.array(
                [
                    [_float(f.get(f"h_lat_{s}")), _float(f.get(f"h_sup_{s}")), _float(f.get(f"h_ant_{s}"))]
                    for f in filas
                ]
            )
            fuente = "columnas h_*"
        else:
            h = np.full((len(filas), 3), np.nan)
            ok = np.all(np.isfinite(crudo), axis=1)
            for i in np.flatnonzero(ok):
                h[i] = direccion(*crudo[i])
            fuente = "reconstruida de crudo_*"
        sesion["direccion"][lado] = h
        sesion["crudo"][lado] = crudo
        sesion["fuente"][lado] = fuente
        if sesion["con_posicion"]:
            # El largo del brazo se reconstruye igual que en el tracker:
            # |hombro→codo| + |codo→destino|, los dos en el mismo marco.
            p = np.array(
                [[_float(f.get(f"{e}_{s}")) for e in ("p_lat", "p_sup", "p_ant")] for f in filas]
            )
            c = np.array(
                [[_float(f.get(f"{e}_{s}")) for e in ("c_lat", "c_sup", "c_ant")] for f in filas]
            )
            sesion["vector"][lado] = p
            sesion["largo"][lado] = np.linalg.norm(c, axis=1) + np.linalg.norm(p - c, axis=1)
    return sesion


def reproducir(sesion: dict, mapeo: str, **kwargs) -> dict:
    """Pasa la sesión por un ``ArmTracker`` real. Devuelve, por canal, el
    objetivo (antes del límite de velocidad) y el comando (después)."""
    reloj = {"t": 0.0}
    tracker = ArmTracker(
        mapeo=mapeo, pose_estimator=_SinPose(), clock=lambda: reloj["t"], **kwargs
    )
    t = sesion["t"]
    objetivo = {lado: np.full((len(t), 2), np.nan) for lado in (Side.L, Side.R)}
    comando = {lado: np.full((len(t), 2), np.nan) for lado in (Side.L, Side.R)}
    for i, ti in enumerate(t):
        reloj["t"] = ti
        pose = ArmAngles()
        for lado in (Side.L, Side.R):
            campo_abd, campo_flex = CANALES[lado]
            if mapeo == "posicion":
                p = sesion["vector"][lado][i]
                largo = sesion["largo"][lado][i]
                salida = (
                    tracker.map_posicion(lado, p, largo, ti)
                    if np.all(np.isfinite(p)) and np.isfinite(largo)
                    else None
                )
            elif mapeo == "direccion":
                h = sesion["direccion"][lado][i]
                salida = (
                    tracker.map_direccion(lado, h, ti) if np.all(np.isfinite(h)) else None
                )
            else:
                a, f = sesion["crudo"][lado][i]
                salida = (
                    tracker.map_to_robot(lado, a, f, ti)
                    if math.isfinite(a) and math.isfinite(f)
                    else None
                )
            if salida is not None:
                setattr(pose, campo_abd, salida[0])
                setattr(pose, campo_flex, salida[1])
        mando = tracker.comando(pose, ti)
        for lado in (Side.L, Side.R):
            campo_abd, campo_flex = CANALES[lado]
            objetivo[lado][i] = tracker.objetivos[lado]
            comando[lado][i] = (getattr(mando, campo_abd), getattr(mando, campo_flex))
    return {
        "objetivo": objetivo,
        "comando": comando,
        "cambios_de_rama": tracker.cambios_de_rama,
        "flexion_range": tracker.flexion_range,
    }


def _puntas(angulos: np.ndarray) -> np.ndarray:
    return np.array([direccion(a, f) for a, f in angulos])


def _correlacion(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson, o NaN si alguna serie no varía: una punta de robot inmóvil no
    tiene correlación con nada, y ``np.corrcoef`` lo diría con un aviso."""
    if len(a) < 2 or np.std(a) < 1e-12 or np.std(b) < 1e-12:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def _angulo_entre(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    return np.degrees(np.arccos(np.clip(np.sum(u * v, axis=1), -1.0, 1.0)))


def metricas(sesion: dict, salida: dict, lado: Side) -> dict:
    h = sesion["direccion"][lado]
    medido = np.all(np.isfinite(h), axis=1)
    objetivo = salida["objetivo"][lado]
    comando = salida["comando"][lado]

    pares = medido[1:] & medido[:-1]
    salto_flex = np.abs(np.diff(objetivo[:, 1]))
    saltos = int(np.sum((salto_flex > 90.0) & pares))

    puntas = _puntas(comando)
    paso_robot = _angulo_entre(puntas[1:], puntas[:-1])
    hn = h / np.linalg.norm(h, axis=1, keepdims=True)
    paso_humano = _angulo_entre(hn[1:], hn[:-1])
    desvio = float(np.sum(paso_robot[pares]) / max(np.sum(paso_humano[pares]), 1e-9))

    correlacion = tuple(
        _correlacion(hn[medido, k], puntas[medido, k]) for k in range(3)
    )
    corr_profundidad = None
    if sesion.get("con_posicion"):
        p = sesion["vector"][lado]
        con_p = medido & np.all(np.isfinite(p), axis=1)
        if con_p.sum() > 10:
            corr_profundidad = _correlacion(p[con_p, 2], puntas[con_p, 2])
    return {
        "frames_medidos": int(medido.sum()),
        "saltos": saltos,
        "desvio": desvio,
        "correlacion": correlacion,
        "corr_profundidad": corr_profundidad,
        "temblor_p50": float(np.percentile(paso_robot[pares], 50)),
        "temblor_p95": float(np.percentile(paso_robot[pares], 95)),
    }


def _figura(sesion: dict, salidas: dict, ruta: Path, lado: Side) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    t = sesion["t"]
    h = sesion["direccion"][lado]
    # La ventana de 20 s con más saltos de rama del mapeo articular: es donde
    # se ve la diferencia.
    objetivo = salidas["articular"]["objetivo"][lado]
    saltos = np.flatnonzero(np.abs(np.diff(objetivo[:, 1])) > 90.0)
    centro = t[saltos[len(saltos) // 2]] if len(saltos) else t[len(t) // 2]
    ventana = (t >= centro - 10.0) & (t <= centro + 10.0)

    fig, ejes = plt.subplots(3, 1, figsize=(10, 7.5), sharex=True)
    colores = {"articular": "#c0392b", "direccion": "#2471a3", "posicion": "#1d9e75"}
    ejes[0].plot(t[ventana], h[ventana, 2], color="#555", lw=1.2, label="operador")
    for mapeo, salida in salidas.items():
        puntas = _puntas(salida["comando"][lado])
        ejes[0].plot(t[ventana], puntas[ventana, 2], color=colores[mapeo], lw=1.2, label=f"robot · {mapeo}")
        ejes[1].plot(t[ventana], salida["comando"][lado][ventana, 1], color=colores[mapeo], lw=1.2, label=mapeo)
        ejes[2].plot(t[ventana], salida["comando"][lado][ventana, 0], color=colores[mapeo], lw=1.2, label=mapeo)
    ejes[0].set_ylabel("componente anterior\n(hacia la cámara)")
    ejes[1].set_ylabel("flexión [°]")
    ejes[2].set_ylabel("abducción [°]")
    ejes[2].set_xlabel("t [s]")
    for eje in ejes:
        eje.grid(alpha=0.3)
        eje.legend(loc="upper right", fontsize=8)
    fig.suptitle(f"Canal {CANALES[lado][0]}: mismo tramo de la sesión por cada mapeo")
    fig.tight_layout()
    fig.savefig(ruta, dpi=150)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="comparar_mapeo_brazos")
    parser.add_argument("csv", type=Path)
    parser.add_argument("--figura", type=Path, help="PNG con un tramo comparado")
    parser.add_argument(
        "--flexion-min",
        type=float,
        default=None,
        help="Tope inferior de flexión del mapeo 'direccion' (por defecto -180)",
    )
    args = parser.parse_args(argv)

    sesion = leer_sesion(args.csv)
    extra = {} if args.flexion_min is None else {"flexion_min": args.flexion_min}
    salidas = {
        "articular": reproducir(sesion, "articular"),
        "direccion": reproducir(sesion, "direccion", **extra),
    }
    if sesion["con_posicion"]:
        salidas["posicion"] = reproducir(sesion, "posicion", **extra)
    else:
        print("(sin columnas p_*/c_*: el mapeo 'posicion' no se puede reproducir)")

    print(f"Sesión: {args.csv}  ({len(sesion['t'])} frames, {sesion['t'][-1]:.0f} s)")
    rango = salidas["direccion"]["flexion_range"]
    print(
        f"Flexión: articular [{FLEXION_RANGE[0]:g}, {FLEXION_RANGE[1]:g}]  ·  "
        f"direccion [{rango[0]:g}, {rango[1]:g}]\n"
    )
    for lado in (Side.L, Side.R):
        print(f"== {CANALES[lado][0]}  (dirección {sesion['fuente'][lado]})")
        print(
            f"   {'mapeo':10s} {'saltos':>7s} {'desvio':>7s} "
            f"{'corr lat/sup/ant':>18s} {'temblor p50/p95':>16s}"
            + ("   corr prof" if sesion["con_posicion"] else "")
        )
        for mapeo, salida in salidas.items():
            m = metricas(sesion, salida, lado)
            c = m["correlacion"]
            profundidad = (
                f"   {m['corr_profundidad']:.2f}"
                if m["corr_profundidad"] is not None
                else ""
            )
            print(
                f"   {mapeo:10s} {m['saltos']:7d} {m['desvio']:7.2f} "
                f"   {c[0]:.2f}/{c[1]:.2f}/{c[2]:.2f}     "
                f"{m['temblor_p50']:.2f}/{m['temblor_p95']:.2f} °/frame{profundidad}"
            )
        print(
            f"   cambios de rama del mapeo 'direccion': "
            f"{salidas['direccion']['cambios_de_rama'][lado]}\n"
        )

    if args.figura:
        _figura(sesion, salidas, args.figura, Side.R)
        print(f"Figura guardada en {args.figura}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
