"""Prueba de punta a punta de los motores del robot, con motores simulados.

Hace de PC del narrador contra un broker de verdad: publica poses a 20 por
segundo, como el pipeline, y comprueba que ``raspberry/motor_driver.py`` las
lleva a los seis motores DYNAMIXEL simulados (``tools/simulador_dynamixel.py``),
pasando por la biblioteca oficial de ROBOTIS. Mide cuánto pasa entre publicar
una pose y que la orden llegue al motor, corta las poses para ver que el robot
se queda quieto, avería un motor y lee el estado que el programa publica.

En Linux (la Raspberry, la prueba en ARM) el programa abre un puerto serie
falso del sistema con el ``PortHandler`` de verdad, igual que abriría
``/dev/ttyACM0``; en Windows usa el puerto simulado dentro del mismo proceso.

    python tools/probar_motores.py --broker localhost:1883

No hay motores de verdad: no mide par, carga ni el tiempo real del cable.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tools"))

import simulador_dynamixel as sim  # noqa: E402
from raspberry import motor_driver as md  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--broker", default="localhost:1883")
    p.add_argument("--cambios", type=int, default=20)
    args = p.parse_args(argv)
    host, puerto = md._partir_broker(args.broker)

    import paho.mqtt.client as mqtt
    from dynamixel_sdk import PacketHandler

    bus = sim.SimuladorDynamixel()
    parar_pty = None
    if sys.platform.startswith("linux"):
        ruta, parar_pty = sim.servir_en_pty(bus)
        via = f"puerto serie del sistema {ruta}, con el PortHandler de ROBOTIS"

        def abrir():
            return md.abrir_bus(ruta, 57600)
    else:
        via = "puerto simulado en el mismo proceso"
        simulado = sim.PuertoSimulado(bus)

        def abrir():
            simulado.openPort()
            return simulado, PacketHandler(2.0)

    pose = md.Pose(a.campo for a in md.ARTICULACIONES)
    enlace = md.EnlaceMotores(pose, host, puerto)
    controlador = md.Controlador(pose, abrir, avisar=enlace.avisar)
    estados: list[dict] = []
    narrador = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="probar-motores")
    narrador.on_message = lambda c, u, m: estados.append(json.loads(m.payload))
    narrador.connect(host, puerto, 30)
    narrador.subscribe(md.TOPIC_ESTADO)
    narrador.loop_start()
    enlace.conectar()
    hilo = threading.Thread(target=controlador.bucle, daemon=True)
    hilo.start()

    def esperar(condicion, segundos: float) -> bool:
        limite = time.perf_counter() + segundos
        while time.perf_counter() < limite and not condicion():
            time.sleep(0.001)
        return condicion()

    def publicar(valores: dict) -> float:
        narrador.publish(md.TOPIC_POSE, json.dumps({**valores, "emo": "neutral"}), qos=0)
        return time.perf_counter()

    informe: dict = {"via": via}
    pan = md.ARTICULACIONES[1]
    base = {"tilt": -10.0, "armR": -55.0, "armRtw": -90.0, "armL": -55.0, "armLtw": 0.0}
    try:
        informe["listos"] = esperar(
            lambda: controlador.motores is not None and len(controlador.motores.listos) == 6, 15)
        esperar(lambda: enlace.conectado, 10)
        if not (informe["listos"] and enlace.conectado):
            raise RuntimeError("los motores o el broker no arrancaron")

        # 1. Llevar el robot a una pose y comprobar que cada motor llega a la suya.
        destino = {**base, "pan": 20.0}
        fin = time.perf_counter() + 6.0
        while time.perf_counter() < fin:
            publicar(destino)
            time.sleep(0.05)
        errores = {a.campo: abs(md.a_grados(round(bus.motor(a.id).posicion), a)
                                - destino[a.campo]) for a in md.ARTICULACIONES}
        informe["error_max_grados"] = max(errores.values())

        # 2. Retardo de publicar una pose a que la orden llegue al motor.
        retardos = []
        for i in range(args.cambios):
            grados = 20.0 + (i % 2 + 1) * 3.0 + i * 0.01
            meta = md.a_pulsos(grados, pan)
            t = publicar({**base, "pan": grados})
            if esperar(lambda: bus.motor(pan.id).metas[-1] == meta, 2):
                retardos.append((time.perf_counter() - t) * 1000)
            for _ in range(3):
                time.sleep(0.05)
                publicar({**base, "pan": grados})
        informe["retardos_ms"] = retardos

        # 3. Sin poses, quieto y con el par puesto.
        time.sleep(1.6)
        ordenes = len(bus.motor(pan.id).metas)
        time.sleep(1.5)
        informe["quieto_sin_poses"] = (len(bus.motor(pan.id).metas) == ordenes
                                       and all(m.par for m in bus.motores))

        # 4. Un motor averiado: se queda apagado, los demás siguen.
        bus.motor(4).averiar()
        fin = time.perf_counter() + 4.0
        while time.perf_counter() < fin:
            publicar({**base, "pan": -30.0})
            time.sleep(0.05)
        informe["averia_aislada"] = (
            not bus.motor(4).par
            and abs(md.a_grados(round(bus.motor(pan.id).posicion), pan) + 30.0) < 0.5)
        informe["estado"] = estados[-1] if estados else None
        informe["corruptos"] = bus.corruptos
    except Exception as exc:  # noqa: BLE001
        informe["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        controlador.seguir = False
        hilo.join(timeout=3)
        narrador.loop_stop()
        enlace.cerrar()
        controlador.cerrar()
        if parar_pty is not None:
            parar_pty()

    print("MOTORES")
    print(f"  por: {informe['via']}")
    if "error" in informe:
        print("  FALLO:", informe["error"])
        return 1
    r = informe["retardos_ms"]
    estado = informe["estado"] or {}
    print("  los 6 motores contestan y quedan con el par puesto: SI")
    print(f"  llegan a la pose pedida: error maximo {informe['error_max_grados']:.2f} grados")
    if r:
        print(f"  de publicar la pose a que la orden llega al motor, {len(r)} de "
              f"{args.cambios}: mediana {statistics.median(r):.0f} ms, maximo {max(r):.0f} ms")
    print(f"  sin poses se queda quieto, con el par puesto: "
          f"{'SI' if informe['quieto_sin_poses'] else 'NO'}")
    print(f"  un motor averiado se queda apagado y los demas siguen: "
          f"{'SI' if informe['averia_aislada'] else 'NO'}")
    print(f"  estado publicado: listos {estado.get('listos')}, averiados "
          f"{estado.get('averiados')}")
    print(f"  paquetes corruptos en el cable simulado: {informe['corruptos']}")
    bien = (informe["error_max_grados"] < 0.5 and len(r) == args.cambios
            and informe["quieto_sin_poses"] and informe["averia_aislada"]
            and estado.get("listos") == [1, 2, 3, 5, 6] and estado.get("averiados") == {"4": 32}
            and informe["corruptos"] == 0)
    return 0 if bien else 1


if __name__ == "__main__":
    raise SystemExit(main())
