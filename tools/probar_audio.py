"""La voz con el robot, de punta a punta, sin altavoz ni micrófono.

    python tools/probar_audio.py [--segundos 24]

Monta las dos mitades en esta máquina, unidas por UDP de verdad:

- **La PC** (``telmeow_teleop/audio_operador.py``): su micrófono «lee el
  cuento» con una voz sintética todo el rato.
- **El robot** (``raspberry/robot_audio.py``, el mismo núcleo que correría en
  la Raspberry): su tarjeta es ``SalaSimulada``, que devuelve por el micrófono
  el eco de lo que suena por el altavoz (60 ms de retraso, una cola de rebotes
  y casi la misma fuerza que lo sonado: el altavoz de la boca está cerca del
  micrófono del pecho) y, de los 10 a los 16 s, un niño que habla A LA VEZ que
  el narrador.

Dice:
- si el sonido llega en los dos sentidos y cuántos paquetes se pierden;
- cuánto tarda la voz del narrador en salir por el altavoz del robot (del
  micrófono de la PC al altavoz; aquí comparten reloj);
- cuánto eco quita el cancelador cuando solo habla el narrador, cuánto quita
  mientras habla también el niño, y cuánto del niño deja pasar intacto.

QUÉ NO DICE: cómo suena. El eco de aquí es lineal y sin ruido de sala; el de un
altavoz pequeño de verdad trae distorsión, que un cancelador lineal no quita
del todo. Tampoco mide una red wifi cargada: aquí la red es la propia máquina.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(RAIZ), str(RAIZ / "tools")]

from raspberry import robot_audio as ra  # noqa: E402
from simulador_sala import DispositivoSintetico, SalaSimulada, camino_eco, voz_sintetica  # noqa: E402
from telmeow_teleop.audio_operador import AudioOperador  # noqa: E402


def _db(a: np.ndarray, b: np.ndarray) -> float:
    return 10.0 * np.log10(max(float(np.sum(a ** 2)), 1e-20) / max(float(np.sum(b ** 2)), 1e-20))


def correr(segundos: float = 24.0, nino=(10.0, 16.0), tiempo_real: bool = True) -> dict:
    fs = ra.TASA
    narrador = voz_sintetica(segundos + 2, semilla=1, nivel=0.1)
    cerca = np.zeros(int((segundos + 2) * fs))
    a, b = int(nino[0] * fs), int(nino[1] * fs)
    cerca[a:b] = voz_sintetica(nino[1] - nino[0], semilla=2, nivel=0.05, pausas=False)

    pc_tarjeta = DispositivoSintetico(narrador, tiempo_real=tiempo_real)
    pc = AudioOperador(puerto=0, robot="127.0.0.1", dispositivo=pc_tarjeta, host_local="127.0.0.1")
    sala = SalaSimulada(camino_eco(retraso_s=0.06, ganancia=0.7), cerca=cerca,
                        tiempo_real=tiempo_real)
    limpios: list[np.ndarray] = []
    colchon = ra.Colchon()
    enlace = ra.EnlaceAudio(colchon, destino=("127.0.0.1", pc.puerto), host_local="127.0.0.1")
    nucleo = ra.Nucleo(enlace, ra.Cancelador(),
                       al_procesar=lambda mic, limpio, sonado: limpios.append(np.array(limpio)))
    robot = ra.Supervisor(nucleo, sala)
    pc.arrancar()
    enlace.empezar()
    robot.empezar()
    inicio = time.perf_counter()
    try:
        while time.perf_counter() - inicio < segundos:
            time.sleep(0.1)
    finally:
        robot.cerrar()
        enlace.cerrar()
        pc.parar()

    n = min(len(limpios), len(sala.ecos))
    eco = np.concatenate(sala.ecos[:n])
    nino_rec = np.concatenate(sala.ninos[:n])
    limpio = np.concatenate(limpios[:n])
    sonado = np.concatenate(sala.sonado[:n])
    oido_pc = np.concatenate(pc_tarjeta.oido) if pc_tarjeta.oido else np.zeros(1)

    def tramo(t0, t1):
        return slice(int(t0 * fs), min(int(t1 * fs), n * ra.MUESTRAS))

    solo = tramo(4.0, nino[0])
    doble = tramo(*nino)
    lat_ida = np.array(colchon.latencias_ms or [0])
    lat_vuelta = np.array(pc.colchon.latencias_ms or [0])
    return {
        "segundos": round(time.perf_counter() - inicio, 1),
        "bloques_robot": n,
        "paquetes_robot_a_pc": pc.colchon.recibidos,
        "paquetes_pc_a_robot": colchon.recibidos,
        "perdidos_o_tarde": (colchon.perdidos + colchon.tarde + colchon.tirados
                             + pc.colchon.perdidos + pc.colchon.tarde + pc.colchon.tirados),
        "huecos": colchon.huecos + pc.colchon.huecos,
        "suena_en_el_robot": bool(np.sqrt(np.mean(sonado ** 2)) > 0.01),
        "llega_a_la_pc": bool(np.sqrt(np.mean(oido_pc ** 2)) > 0.001),
        "latencia_pc_a_altavoz_ms": (float(np.median(lat_ida)), float(np.percentile(lat_ida, 95))),
        "latencia_robot_a_pc_ms": (float(np.median(lat_vuelta)), float(np.percentile(lat_vuelta, 95))),
        "eco_quitado_solo_narrador_db": round(_db(eco[solo], limpio[solo] - nino_rec[solo]), 1),
        "eco_quitado_hablando_los_dos_db": round(_db(eco[doble], limpio[doble] - nino_rec[doble]), 1),
        "nino_intacto_correlacion": round(float(np.corrcoef(limpio[doble], nino_rec[doble])[0, 1]), 3),
        "eco_frente_a_nino_sin_cancelar_db": round(_db(eco[doble], nino_rec[doble]), 1),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--segundos", type=float, default=24.0)
    args = p.parse_args(argv)
    r = correr(args.segundos)
    print("La voz con el robot, de punta a punta (PC y robot en esta maquina, por UDP):")
    print(f"  {r['segundos']} s, {r['bloques_robot']} bloques de 20 ms en el robot")
    print(f"  paquetes PC->robot {r['paquetes_pc_a_robot']}, robot->PC {r['paquetes_robot_a_pc']}, "
          f"perdidos o tarde {r['perdidos_o_tarde']}, huecos {r['huecos']}")
    print(f"  suena la voz del narrador en el robot: {r['suena_en_el_robot']}; "
          f"llega el sonido del robot a la PC: {r['llega_a_la_pc']}")
    ida, vuelta = r["latencia_pc_a_altavoz_ms"], r["latencia_robot_a_pc_ms"]
    print(f"  del microfono de la PC al altavoz del robot: mediana {ida[0]:.0f} ms, p95 {ida[1]:.0f} ms")
    print(f"  del microfono del robot a la PC:             mediana {vuelta[0]:.0f} ms, p95 {vuelta[1]:.0f} ms")
    print("    (sin contar lo que tarden las tarjetas de sonido de verdad, ~10-40 ms cada una)")
    print(f"  eco quitado, solo el narrador:            {r['eco_quitado_solo_narrador_db']} dB")
    print(f"  eco quitado, hablando el nino a la vez:   {r['eco_quitado_hablando_los_dos_db']} dB")
    print(f"  el nino, tras quitar el eco (1 = intacto): {r['nino_intacto_correlacion']}")
    print(f"    (sin cancelar, el eco iria {r['eco_frente_a_nino_sin_cancelar_db']} dB por encima del nino)")
    bien = (r["suena_en_el_robot"] and r["llega_a_la_pc"]
            and r["eco_quitado_solo_narrador_db"] >= 20 and r["nino_intacto_correlacion"] >= 0.95)
    print("RESULTADO:", "bien" if bien else "MAL")
    return 0 if bien else 1


if __name__ == "__main__":
    raise SystemExit(main())
