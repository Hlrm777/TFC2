"""La zona muerta de 5° en los brazos (P7.8), con el ArmTracker de verdad.

Reproduce p77 y p75 con ``reproducir_brazos`` sin zona y con ella, y da por
tramo: cambios de más de 1° por minuto, saltos de más de 5° por minuto y el p95
del salto; en el saludo, que el vaivén sigue entero, y en el reposo, que el
comando acaba exacto. Sostiene las cifras del P7.8 del plan.

Uso::

    python tools/zona_brazos.py
"""
import sys
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tools"))
import reproducir_brazos as rb  # noqa: E402
from telmeow_teleop.limits import Side  # noqa: E402


def tramo(salida, canal, etiqueta):
    return [x["comando"] for e, x in zip(salida["etiqueta"], salida["canales"][canal]) if str(e) == str(etiqueta)], \
        [t for e, t in zip(salida["etiqueta"], salida["t"]) if str(e) == str(etiqueta)]


def cifras(c, t):
    s = [max(abs(a[0] - b[0]), abs(a[1] - b[1])) for a, b in zip(c, c[1:])]
    m = (t[-1] - t[0]) / 60
    return f"{sum(1 for x in s if x > 1) / m:4.0f} {sum(1 for x in s if x > 5) / m:4.0f} {np.percentile(s, 95):4.1f}"


for base, etiquetas in (("p77", [("12", "yo no fui"), ("13", "estirar"), ("0", "reposo")]),
                        ("p75", [("12", "saludo"), ("13", "narrar"), ("0", "reposo")])):
    filas = rb.leer(RAIZ / f"{base}_brazos.csv")
    sin = rb.reproducir(filas, mano_cara=True)
    con = rb.reproducir(filas, mano_cara=True, zona_muerta=5.0)
    print(f"\n{base} (cambios >1/min, saltos >5/min, p95)")
    for etq, texto in etiquetas:
        for canal in (Side.L, Side.R):
            a, t = tramo(sin, canal, etq)
            b, _ = tramo(con, canal, etq)
            extra = ""
            if texto == "saludo":
                amp = lambda c: np.ptp([x[0] for x in c[len(c) // 3:]])  # noqa: E731
                extra = f" | recorrido abd sin {amp(a):.1f} con {amp(b):.1f}"
            if texto == "reposo":
                extra = f" | ultimo comando sin {a[-1]} con {b[-1]}"
            print(f"  {texto:10s} {canal.value}: sin {cifras(a, t)} | con {cifras(b, t)}{extra}")
