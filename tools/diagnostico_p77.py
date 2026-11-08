"""De dónde sale cada error de p77: el modelo o la regla del cabeceo (P7.7).

Evalúa los pesos de operación y los candidatos sobre p77 y da, por tramo, el %
de la predicción CRUDA que acierta (lo que dice el modelo) y el del robot con y
sin la regla del enojo con el cabeceo. Es diagnóstico, no un criterio: lo que se
decida mirando esto se ha visto en p77, así que no puede validarse con p77.

Uso::

    python tools/diagnostico_p77.py [--candidato modelos_externos/blendshapes_p77_candidato.json]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from telmeow_teleop.config import PROJECT_ROOT, AppConfig  # noqa: E402


def crudo_por_tramo(filas: list[dict]) -> dict[str, float | None]:
    """% de las caras que votan en que la predicción cruda es la pedida."""
    import reentrenar_p77 as rp
    from analizar_p65 import _vota

    salida = {}
    for nombre, etiquetas in rp.TRAMOS_P77:
        esperada = rp.ESPERADAS_P77[etiquetas[0]]
        votan = [f for f in filas if f["etiqueta"] in etiquetas and _vota(f)]
        salida[nombre] = 100.0 * sum(f["prediccion"] == esperada for f in votan) / len(votan) if votan else None
    return salida


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--candidato", type=Path,
                   default=PROJECT_ROOT / "modelos_externos" / "blendshapes_p77_candidato.json")
    a = p.parse_args(argv)

    import analizar_p65 as p65
    import analizar_registro_emocion as are
    import evaluar_backends as eb
    import reentrenar_p77 as rp

    cfg = AppConfig.from_env()
    filas, carpeta = p65._cargar(PROJECT_ROOT / "p77_emocion.csv")
    episodios = p65.episodios_de(filas, carpeta, cfg)
    columnas = {}
    for nombre, pesos in (("operacion", Path(cfg.paths.emotion_blendshapes)), ("reentrenado", a.candidato)):
        motor = eb.crear(f"combinado:onnx:{cfg.paths.emotion_onnx}+blendshapes:{pesos}"
                         f"+cejas:{cfg.emotion.umbral_cejas}", cfg)
        ev = eb.evaluar(filas, carpeta, motor, episodios=episodios)
        columnas[f"{nombre} crudo"] = crudo_por_tramo(ev.filas)
        columnas[f"{nombre} robot"] = rp.resumir(ev.filas)
        sin_regla = [dict(f, enojo_por_grado=0.0) for f in ev.filas]
        emos, _ = are.reproducir(sin_regla, 5, 0.5)
        for f, e in zip(sin_regla, emos):
            f["emocion"] = f["manual"] or e
        columnas[f"{nombre} sin regla"] = rp.resumir(sin_regla)
    nombres = list(columnas)
    print(f"{'tramo':20s} | " + " | ".join(f"{n:>21s}" for n in nombres))
    for tramo, _ in rp.TRAMOS_P77:
        print(f"{tramo:20s} | " + " | ".join(
            f"{'--' if columnas[n][tramo] is None else f'{columnas[n][tramo]:.0f} %':>21s}" for n in nombres))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
