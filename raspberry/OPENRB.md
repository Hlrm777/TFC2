# Poner en marcha los motores: la OpenRB-150 y los seis DYNAMIXEL

Esta guía es para quien construya el robot. **No se ha seguido nunca con el
hardware delante**: el robot no se ha fabricado, y `motor_driver.py` está
probado solo contra motores simulados (`tools/simulador_dynamixel.py`). Lo que
sale del manual de ROBOTIS está marcado como tal; el resto es el diseño.

## 1. Cómo se conecta

```
Fuente 12 V ──► bornera de la OpenRB-150 ──► alimenta los motores
Raspberry Pi 5 ──USB-C (datos)──► OpenRB-150 ──cable DYNAMIXEL──► motores
```

- **El cable USB-C entre la Raspberry y la OpenRB es el de datos**: por él van
  las órdenes. Falta en el esquema de conexiones original, donde la OpenRB solo
  recibe los 12 V.
- En la OpenRB, el puente de alimentación de los motores va en la posición
  **VIN(DXL)**, para que los motores tomen los 12 V de la bornera (manual de la
  OpenRB-150). La bornera admite de 3.7 a 12.6 V: una fuente de 12 V está
  dentro, pero cerca del tope.
- Los motores se encadenan: la cabeza (2) en un puerto DYNAMIXEL de la OpenRB y
  los brazos (2 + 2) en otro, como en el esquema.

## 2. Cargar en la OpenRB el programa puente (una sola vez)

La OpenRB no lleva programa propio de Telmeow. Se le carga el ejemplo
**`usb_to_dynamixel`** de ROBOTIS, que la convierte en un adaptador: lo que le
llega por el USB lo pasa a los motores y al revés (manual de la OpenRB-150).

1. Instalar el Arduino IDE en un computador.
2. En *Preferencias → URLs adicionales de gestor de placas*, añadir
   `https://raw.githubusercontent.com/ROBOTIS-GIT/OpenRB-150/master/package_openrb_index.json`
3. En el gestor de placas, instalar **Arduino SAMD Boards** y **OpenRB-150**.
4. Elegir la placa OpenRB-150, abrir el ejemplo `usb_to_dynamixel` de los
   ejemplos de la placa y subirlo.

## 3. Dar a cada motor su número (ID)

Los seis motores salen de fábrica con el **mismo ID, el 1**, y a 57600 baudios.
Dos motores con el mismo ID en el cable no se pueden distinguir, así que se
numeran **de uno en uno, con un solo motor conectado cada vez**:

```bash
python motor_driver.py --escanear            # debe contestar solo el ID 1
python motor_driver.py --cambiar-id 1 6      # ese motor pasa a ser el 6
```

Conviene empezar por el 6 y acabar por el 1, que ya lo trae. El programa se
niega si el ID viejo no contesta o si el nuevo ya existe.

| ID | Campo de la pose | Motor (pieza del CAD) | Qué mueve |
|---|---|---|---|
| 1 | `tilt` | `M_Cuello_Base`, fijo al cuerpo | cabeceo de la cabeza |
| 2 | `pan` | `M_Cuello_Sup`, en la cabeza | giro de la cabeza |
| 3 | `armRtw` | `M_HombroDer_Sup`, fijo al cuerpo | flexión del brazo derecho |
| 4 | `armR` | `M_HombroDer_Base`, en el brazo | abducción del brazo derecho |
| 5 | `armLtw` | `M_HombroIzq_Sup`, fijo al cuerpo | flexión del brazo izquierdo |
| 6 | `armL` | `M_HombroIzq_Base`, en el brazo | abducción del brazo izquierdo |

Con los seis ya conectados, `--escanear` tiene que listar los seis.

## 4. Calibrar el cero y el sentido de cada articulación

`motor_driver.py` convierte así: `pulsos = cero + signo × grados × 4096 / 360`.
De partida supone que cada motor se montó con su centro (pulso 2048) en el cero
de la articulación (la pose en cruz de los brazos, la cabeza al frente) y que
gira en el sentido positivo. **Eso depende de cómo quede atornillado cada
motor** y hay que comprobarlo articulación por articulación, con los brazos
desmontados o el robot sujeto:

1. Con el par quitado, poner la articulación a mano en su cero y leer el pulso
   (DYNAMIXEL Wizard 2.0, o `--escanear` ampliado): ese es su `cero`.
2. Pedir un ángulo pequeño positivo y mirar hacia dónde va, comparándolo con el
   visor 3D, que es la referencia. Si va al revés, `signo` es −1.
3. Guardarlo en un archivo y arrancar con él:

```json
{"articulaciones": {"pan": {"signo": -1, "cero": 2100},
                    "armL": {"signo": -1}},
 "ajustes": {"velocidad": 130, "velocidad_lenta": 30}}
```

```bash
python motor_driver.py --broker IP_DE_LA_PC --config motores.json
```

La flexión de los brazos recorre de −180 a +90 grados: con el cero en 2048 va
del pulso 0 al 3072, dentro de la vuelta única del motor. Si el montaje obliga
a otro cero, hay que comprobar que todo el recorrido cabe entre 0 y 4095.

## 5. Lo que hace el programa para no romper nada

- Recorta cada ángulo al recorrido de su articulación antes de mandarlo.
- El primer movimiento, y el primero tras más de un segundo sin poses, es lento
  (unos 41 grados por segundo); después, hasta unos 180.
- Sin poses, el robot se queda quieto con el par puesto.
- Un motor que se apaga solo por sobrecarga o calor no se reinicia: se anota y
  se espera a que alguien lo revise.
- Cuenta su estado una vez por segundo en `telmeow/robot/motores`.

**Lo que NO hace:** evitar que un brazo choque con el tronco o con la cabeza.
Eso lo calcula la PC (`telmeow_teleop/limits.py`) antes de publicar la pose, y
en el robot virtual además lo comprueba el visor contra la geometría. El robot
físico no tendría esa segunda comprobación.
