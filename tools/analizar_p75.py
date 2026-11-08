"""Los análisis de la sesión p75 (2026-11-07) que decidieron el P7.6.

Tres, uno por cosa que vio el operador:

- ``cabeceo``: el enojo del neutral según el cabeceo de la cabeza (H64), y la
  regla elegida reproducida con las predicciones grabadas frente a otras.
- ``mejillas``: con las manos en la cara, si la malla ve la cara y cuánto salta
  (lo que separa «el grito» de la cara tapada).
- ``yo-no-fui``: por qué oscila el brazo izquierdo (H65): los codos fuera del
  cuadro y la profundidad corregida que salta entre dos soluciones; con
  ``--variante`` reproduce además la de no corregir el codo fuera de cuadro.
- ``referencia`` (tras p76, H66): la regla del enojo con el cabeceo medido
  desde el primer frame, como se grabó p76, o desde la calibración de la cara.

Uso::

    python tools/analizar_p75.py cabeceo
    python tools/analizar_p75.py mejillas
    python tools/analizar_p75.py yo-no-fui [--variante]
    python tools/analizar_p75.py referencia
"""

from __future__ import annotations

import argparse
import bisect
import csv
import statistics
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _leer(nombre: str) -> list[dict]:
    with open(PROJECT_ROOT / nombre, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def cabeceos(base: str, filas: list[dict]) -> list[float | None]:
    """El cabeceo crudo del head tracker más cercano a cada fila (± 0.25 s)."""
    cabeza = _leer(f"{base}_cabeza.csv")
    tc = [float(f["t"]) for f in cabeza]
    salida = []
    for f in filas:
        t = float(f["t"])
        i = min(bisect.bisect_left(tc, t), len(cabeza) - 1)
        salida.append(float(cabeza[i]["raw_tilt"]) if abs(tc[i] - t) < 0.25 else None)
    return salida


# -- cabeceo ---------------------------------------------------------------------

TRAMOS_CABECEO = ((-40, -12), (-12, -6), (-6, -2), (-2, 2), (2, 6), (6, 12), (12, 40))


def enojo_por_cabeceo(base: str, etiquetas: set[int]) -> list[tuple[str, int, float, float]]:
    """``(tramo, n, % predicción enojado, % robot enojado)`` en esas etiquetas,
    sin manos en la cara ni calibración."""
    emo = [f for f in _leer(f"{base}_emocion.csv") if f["prediccion"] and int(f["etiqueta"]) in etiquetas
           and f.get("calibrando") in ("0", "", None) and not int(f.get("manos") or 0)]
    pitch = cabeceos(base, emo)
    salida = []
    for a, b in TRAMOS_CABECEO:
        g = [f for f, p in zip(emo, pitch) if p is not None and a <= p < b]
        if len(g) >= 5:
            salida.append((f"{a:+d}..{b:+d}", len(g),
                           100 * sum(f["prediccion"] == "enojado" for f in g) / len(g),
                           100 * sum(f["emocion"] == "enojado" for f in g) / len(g)))
    return salida


REGLAS = {
    "como se grabo": (0.0, 2.0),
    "0.02 por grado desde 2": (0.02, 2.0),
    "0.03 por grado desde 2 (elegida)": (0.03, 2.0),
}


def reglas_reproducidas() -> list[tuple[str, float, float, float]]:
    """``(regla, % enojo falso en el neutral de p75, % enojo de verdad en
    p7_final, % neutral de verdad en p7_final)``, con la ClassHysteresis de verdad."""
    import analizar_registro_emocion as are

    datos = {}
    for base in ("p75", "p7_final"):
        filas = are.cargar(PROJECT_ROOT / f"{base}_emocion.csv")
        datos[base] = (filas, cabeceos(base, filas))
    salida = []
    for nombre, (por_grado, desde) in REGLAS.items():
        cifras = []
        for base, etiquetas, objetivo in (("p75", {0, 2, 4, 6, 10, 11, 12}, "enojado"),
                                          ("p7_final", {3, 7}, "enojado"),
                                          ("p7_final", {0, 4, 10}, "neutral")):
            filas, pitch = datos[base]
            copia = []
            for f, p in zip(filas, pitch):
                g = dict(f)
                g["cabeceo"], g["enojo_por_grado"], g["enojo_desde"] = p, por_grado, desde
                copia.append(g)
            emos, _ = are.reproducir(copia, 5, 0.5)
            idx = [i for i, f in enumerate(filas) if f["etiqueta"] in etiquetas and f["rostro"]
                   and not f["calibrando"]]
            cifras.append(100 * float(np.mean([emos[i] == objetivo for i in idx])))
        salida.append((nombre, *cifras))
    return salida


def _cabeceo() -> None:
    for base, etiquetas in (("p75", {0, 2, 4, 6, 11, 12}), ("p7_final", {0, 4, 10})):
        print(f"\n{base}, neutral pedido (etiquetas {sorted(etiquetas)}), sin manos:")
        print("  cabeceo crudo |   n  | prediccion enojado | robot enojado")
        for tramo, n, pred, robot in enojo_por_cabeceo(base, etiquetas):
            print(f"  {tramo:>12s}  | {n:4d} | {pred:17.0f} % | {robot:11.0f} %")
    print("\nReglas, reproducidas con las predicciones grabadas:")
    print("  regla                              | enojo falso p75 | enojo real p7_final | neutral p7_final")
    for nombre, falso, real, neutral in reglas_reproducidas():
        print(f"  {nombre:34s} | {falso:14.0f} % | {real:18.0f} % | {neutral:14.0f} %")


# -- mejillas --------------------------------------------------------------------


def malla_por_etiqueta(base: str = "p75") -> list[tuple[int, float, float, float]]:
    """``(etiqueta, % frames con cara en la malla, salto crudo p50, p90)``."""
    sesion = _leer(f"{base}.csv")
    cabeza = _leer(f"{base}_cabeza.csv")
    salida = []
    for e in sorted({int(f["etiqueta"]) for f in sesion}):
        n = sum(1 for f in sesion if int(f["etiqueta"]) == e)
        c = [f for f in cabeza if int(f["etiqueta"]) == e]
        s = [max(abs(float(a["raw_pan"]) - float(b["raw_pan"])), abs(float(a["raw_tilt"]) - float(b["raw_tilt"])))
             for a, b in zip(c, c[1:])]
        if s:
            salida.append((e, 100 * len(c) / n, float(np.percentile(s, 50)), float(np.percentile(s, 90))))
    return salida


def _mejillas() -> None:
    print("p75: con la cara a la vista (3, 5, 7: manos en las mejillas) frente a tapada (9)")
    print("  etiqueta | malla con cara | salto crudo p50 | p90")
    for e, cara, p50, p90 in malla_por_etiqueta():
        print(f"  {e:8d} | {cara:12.0f} % | {p50:15.2f} | {p90:5.2f}")


# -- yo no fui --------------------------------------------------------------------


def yo_no_fui(nombre: str = "p75_brazos.csv", desde: float = 322.0, hasta: float = 382.0) -> dict:
    """Por brazo: saltos > 5° por minuto, codo fuera del cuadro y la profundidad
    de la mano según BlazePose y corregida por el escorzo (p10, p90)."""
    filas = [f for f in _leer(nombre) if desde <= float(f["t"]) <= hasta]
    minutos = (hasta - desde) / 60.0
    salida = {}
    for lado, abd, flex in (("L", "armL", "armLtw"), ("R", "armR", "armRtw")):
        c = [(_num(f[abd]), _num(f[flex])) for f in filas]
        s = [max(abs(a[0] - b[0]), abs(a[1] - b[1])) for a, b in zip(c, c[1:])]
        alto = _num(filas[0]["alto"])
        salida[lado] = {
            "saltos_min": sum(1 for x in s if x > 5.0) / minutos,
            "codo_fuera": 100 * float(np.mean([_num(f[f"img_codo_y_{lado}"]) > alto for f in filas])),
            "ant_blazepose": tuple(np.nanpercentile([_num(f[f"pb_ant_{lado}"]) for f in filas], (10, 90))),
            "ant_corregida": tuple(np.nanpercentile([_num(f[f"p_ant_{lado}"]) for f in filas], (10, 90))),
        }
    return salida


def _yo_no_fui(variante: bool) -> None:
    for lado, d in yo_no_fui().items():
        print(f"p75, «yo no fui», brazo {lado}: {d['saltos_min']:.0f} saltos > 5 grados por minuto, "
              f"codo fuera del cuadro el {d['codo_fuera']:.0f} %, profundidad de la mano BlazePose "
              f"{d['ant_blazepose'][0]:.2f}..{d['ant_blazepose'][1]:.2f} m, corregida "
              f"{d['ant_corregida'][0]:.2f}..{d['ant_corregida'][1]:.2f} m")
    if variante:
        _variante_codo_fuera()


def _variante_codo_fuera() -> None:
    """Reproduce p75 y la verificación del 2026-10-11 con el escorzo sin corregir
    el codo cuyo píxel cae fuera de la imagen."""
    import reproducir_brazos as rb
    import telmeow_teleop.arm_tracker as at
    from telmeow_teleop.limits import Side
    from telmeow_teleop.retarget import direccion

    original = at.ArmTracker._escorzo_brazo
    estado = {"activa": False}

    def variante(self, mundo, etiqueta, canal, hombro, codo, mano, pixeles, vis_codo, vis_mano):
        if estado["activa"] and pixeles is not None and self._imagen is not None:
            _, ancho, alto = self._imagen
            u, v = pixeles["codo"]
            if not (0 <= u <= ancho and 0 <= v <= alto):
                vis_codo = 0.0
        return original(self, mundo, etiqueta, canal, hombro, codo, mano, pixeles, vis_codo, vis_mano)

    at.ArmTracker._escorzo_brazo = variante

    def medir(salida, canal, a, b):
        c = [x["comando"] for t, x in zip(salida["t"], salida["canales"][canal]) if a <= t <= b]
        s = [max(abs(u[0] - v[0]), abs(u[1] - v[1])) for u, v in zip(c, c[1:])]
        cruz = statistics.median(float(np.degrees(np.arccos(np.clip(direccion(*x)[0], -1, 1)))) for x in c)
        return f"p95 {np.percentile(s, 95):.1f}, a {cruz:.0f} grados de la cruz"

    v = rb.leer(PROJECT_ROOT / "verificacion_2026-10-11.csv")
    tramos = {"p75_brazos.csv": [("p75 yo no fui", 322, 382)], "verificacion_2026-10-11.csv": []}
    for e, nombre in (("1", "yo no fui"), ("5", "estirar")):
        ts = [float(f["t"]) for f in v if f["etiqueta"] == e]
        tramos["verificacion_2026-10-11.csv"].append((f"10-11 {nombre}", min(ts) + 3, max(ts)))
    try:
        for activa in (False, True):
            estado["activa"] = activa
            print(f"\n{'sin corregir el codo fuera de cuadro' if activa else 'como se grabo'}:")
            for nombre, ts in tramos.items():
                salida = rb.reproducir(rb.leer(PROJECT_ROOT / nombre), mano_cara=True)
                for texto, a, b in ts:
                    print(f"  {texto}: L {medir(salida, Side.L, a, b)} | R {medir(salida, Side.R, a, b)}")
    finally:
        at.ArmTracker._escorzo_brazo = original


def regla_con_referencia(base: str, desde_calibracion: bool, por_grado: float = 0.03,
                         desde: float = 2.0) -> tuple[list[dict], list[str]]:
    """Las filas de emoción de una sesión y lo que haría el robot con la regla
    del enojo, con el cabeceo desde el primer frame (la referencia de la malla)
    o desde la mediana del cabeceo mientras se calibraba la cara."""
    import analizar_registro_emocion as are

    filas = are.cargar(PROJECT_ROOT / f"{base}_emocion.csv")
    pitch = cabeceos(base, filas)
    cal = [p for f, p in zip(filas, pitch) if f["calibrando"] and p is not None]
    ref = statistics.median(cal) if (desde_calibracion and cal) else 0.0
    copia = []
    for f, p in zip(filas, pitch):
        g = dict(f)
        g["cabeceo"] = None if p is None else p - ref
        g["enojo_por_grado"], g["enojo_desde"] = por_grado, desde
        copia.append(g)
    emos, _ = are.reproducir(copia, 5, 0.5)
    return filas, emos


def _pct_tramo(filas, emos, etiquetas, objetivo) -> float:
    idx = [i for i, f in enumerate(filas) if f["etiqueta"] in etiquetas and f["rostro"] and not f["calibrando"]]
    return 100 * float(np.mean([emos[i] == objetivo for i in idx]))


def _referencia() -> None:
    print("La regla del enojo (0.03 por grado desde 2), con el cabeceo desde:")
    print("  referencia     | p76 neutral (1) enojo | p76 enojo real (2) | p75 neutral enojo | p7_final enojo real")
    for texto, cal in (("el primer frame", False), ("la calibracion", True)):
        f76, e76 = regla_con_referencia("p76", cal)
        f75, e75 = regla_con_referencia("p75", cal)
        f7, e7 = regla_con_referencia("p7_final", cal)
        print(f"  {texto:14s} | {_pct_tramo(f76, e76, {1}, 'enojado'):20.0f} % | "
              f"{_pct_tramo(f76, e76, {2}, 'enojado'):17.0f} % | "
              f"{_pct_tramo(f75, e75, {0, 2, 4, 6, 10, 11, 12}, 'enojado'):16.0f} % | "
              f"{_pct_tramo(f7, e7, {3, 7}, 'enojado'):18.0f} %")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("analisis", choices=("cabeceo", "mejillas", "yo-no-fui", "referencia"))
    p.add_argument("--variante", action="store_true", help="con yo-no-fui: reproduce la variante (minutos)")
    a = p.parse_args(argv)
    {"cabeceo": _cabeceo, "mejillas": _mejillas, "referencia": _referencia}.get(
        a.analisis, lambda: _yo_no_fui(a.variante))()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
