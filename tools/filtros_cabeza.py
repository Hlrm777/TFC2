"""Reproduce offline la cadena de la cabeza con otros filtros (P7.4).

La cadena del robot es ganancia -> One Euro -> zona muerta -> recorte, y entera
vive en ``HeadTracker.map_to_robot``, así que se puede repetir sobre los
ángulos crudos (``raw_pan``, ``raw_tilt``) de un CSV de cabeza ya grabado, sin
cámara, con un reloj inyectado con la ``t`` de cada fila. Mide, por candidato:

- **temblor**: salto entre frames consecutivos (el mayor de pan y tilt) en las
  etiquetas de reposo; mediana y p95. Es lo que se ve como vibración;
- **retraso** al girar: el desfase que mejor alinea la salida con la cadena
  sin filtro en las etiquetas de giro;
- **error**: mediana de la distancia a la cadena sin filtro en esos giros, que
  junta el retraso y lo que la zona muerta se queda corta.

La primera fila es el **ancla**: la cadena con el filtro con que se grabó la
sesión tiene que dar lo grabado. Si no, lo que se compara no es lo que vio el
robot. (No coincide exacto: el pipeline reinicia el filtro al calibrar y su
reloj es el del momento de procesar, no la ``t`` de la fila.)

Uso::

    python tools/filtros_cabeza.py
    python tools/filtros_cabeza.py --reposo p7_final_cabeza.csv --giros p7_movimiento_cabeza.csv
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

#: (nombre, min_cutoff, beta, zona muerta). El primero es el filtro con que se
#: grabaron las sesiones del P7 (el del P4) y sirve de ancla.
CANDIDATOS = (
    ("P4 (0.5, 0.5)", 0.5, 0.5, 0.0),
    ("beta 0.2", 0.5, 0.2, 0.0),
    ("zona 1.0", 0.5, 0.5, 1.0),
    ("zona 0.7 + beta 0.2", 0.5, 0.2, 0.7),
    ("zona 1.0 + beta 0.2", 0.5, 0.2, 1.0),
    ("zona 1.5", 0.5, 0.5, 1.5),
)


def leer(ruta: Path) -> list[dict]:
    with open(ruta, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def reproducir(filas: list[dict], min_cutoff: float, beta: float,
               zona: float = 0.0) -> list[tuple[float, float]]:
    """La salida de la cadena para cada fila, con las ganancias grabadas."""
    from telmeow_teleop.head_tracker import HeadTracker

    reloj = {"t": 0.0}
    f0 = filas[0]
    tr = HeadTracker(gain_pan=float(f0["gain_pan"]), gain_tilt_up=float(f0["gain_tilt_up"]),
                     gain_tilt_down=float(f0["gain_tilt_down"]), invert_pan=True,
                     min_cutoff=min_cutoff, beta=beta, zona_muerta=zona,
                     clock=lambda: reloj["t"])
    try:
        salida = []
        for f in filas:
            reloj["t"] = float(f["t"])
            salida.append(tr.map_to_robot(float(f["raw_pan"]), float(f["raw_tilt"]), reloj["t"]))
        return salida
    finally:
        tr.close()


def _indices(filas: list[dict], etiquetas) -> list[int]:
    return [i for i, f in enumerate(filas) if int(f["etiqueta"]) in etiquetas]


def temblor(filas, salida, etiquetas) -> tuple[float, float]:
    """Salto mediano y p95 entre frames consecutivos de esas etiquetas."""
    idx = _indices(filas, etiquetas)
    saltos = [max(abs(salida[i][0] - salida[j][0]), abs(salida[i][1] - salida[j][1]))
              for i, j in zip(idx, idx[1:]) if j == i + 1]
    return statistics.median(saltos), float(np.percentile(saltos, 95))


def retraso_ms(filas, salida, etiquetas, ref) -> int | None:
    """Desfase (múltiplo de 10 ms) que maximiza la correlación con ``ref`` en
    el eje que más se mueve; None si ninguno se mueve."""
    idx = _indices(filas, etiquetas)
    t = np.array([float(filas[i]["t"]) for i in idx])
    rejilla = np.arange(t[0], t[-1], 0.01)
    mejor = []
    for k in (0, 1):
        a = np.interp(rejilla, t, [ref[i][k] for i in idx])
        b = np.interp(rejilla, t, [salida[i][k] for i in idx])
        a, b = a - a.mean(), b - b.mean()
        if a.std() < 1.0:
            continue
        corr = [np.dot(a[: len(a) - d], b[d:]) for d in range(0, 60)]
        mejor.append(10 * int(np.argmax(corr)))
    return max(mejor) if mejor else None


def error_mediano(filas, salida, etiquetas, ref) -> float:
    idx = _indices(filas, etiquetas)
    return statistics.median(
        max(abs(salida[i][0] - ref[i][0]), abs(salida[i][1] - ref[i][1])) for i in idx)


def informe(reposo: list[dict], giros: list[dict], etq_reposo, etq_giros,
            candidatos=CANDIDATOS) -> str:
    grabado = [(float(f["pan"]), float(f["tilt"])) for f in reposo]
    ref = reproducir(giros, 1000.0, 0.0)          # sin filtro práctico
    lineas = [
        f"{'candidato':22s} | reposo {sorted(etq_reposo)}: salto mediano, p95 (°/frame) "
        f"| giros {sorted(etq_giros)}: retraso, error mediano",
        f"{'lo grabado':22s} |   {temblor(reposo, grabado, etq_reposo)[0]:.3f}  "
        f"{temblor(reposo, grabado, etq_reposo)[1]:.3f}",
    ]
    for nombre, mc, beta, zona in candidatos:
        s = reproducir(reposo, mc, beta, zona)
        m, p95 = temblor(reposo, s, etq_reposo)
        g = reproducir(giros, mc, beta, zona)
        lineas.append(
            f"{nombre:22s} |   {m:.3f}  {p95:.3f}"
            f"   | {retraso_ms(giros, g, etq_giros, ref)} ms, "
            f"{error_mediano(giros, g, etq_giros, ref):.2f}°")
    ancla = reproducir(reposo, *candidatos[0][1:])
    dif = statistics.median(max(abs(a[0] - b[0]), abs(a[1] - b[1])) for a, b in zip(ancla, grabado))
    lineas.append(f"\nancla: la cadena con «{candidatos[0][0]}» se separa de lo grabado "
                  f"{dif:.3f}° de mediana")
    return "\n".join(lineas)


def _etiquetas(texto: str) -> set[int]:
    return {int(x) for x in texto.split(",") if x.strip()}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--reposo", type=Path, default=PROJECT_ROOT / "p7_final_cabeza.csv")
    p.add_argument("--etiquetas-reposo", default="0,4,10",
                   help="neutral, triste sin ceño y lectura en la sesión de verificación")
    p.add_argument("--giros", type=Path, default=PROJECT_ROOT / "p7_movimiento_cabeza.csv")
    p.add_argument("--etiquetas-giro", default="1,2,3")
    a = p.parse_args(argv)
    print(informe(leer(a.reposo), leer(a.giros),
                  _etiquetas(a.etiquetas_reposo), _etiquetas(a.etiquetas_giro)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
