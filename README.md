# Telmeow — pipeline de teleoperación

Estimación de pose y emoción del operador desde una sola cámara RGB, y
publicación por MQTT hacia el visor 3D del robot social **Telmeow**.

Código de la tesis de pregrado de Harold Rodríguez. Los scripts originales se
conservan sin modificar en [`legacy/`](legacy/) como referencia histórica citada
en el documento.

---

## Flujo de datos

```
             ┌────────────────────────────────────────────────────────────────────┐
             │ main.py (P7) · UNA captura, UN bucle                               │
 ┌──────────┐│                                                                    │
 │  cámara  ├► frame ─► flip ─┬─► BGR ──────────────────────┐                     │
 │  30 fps  ││  (hora t)      │                             │                     │
 └──────────┘│                └─► RGB (una vez)             │                     │
             │                     ├─► HeadTracker (P4)     │                     │
             │                     │    Face Mesh           │                     │
             │                     │    └─► pan, tilt       ▼                     │
             │                     └─► ArmTracker (P5)    EmotionClassifier (P6)  │
             │                          BlazePose ─ manos ─►combinado, 5 Hz       │
             │                          └─► armL, armLtw,   └─► emo               │
             │                              armR, armRtw                          │
             │                                                                    │
             │ instantánea {pan, tilt, arm*, emo, t}                              │
             │        │                                                           │
             │        ▼                                                           │
             │ PublicadorPeriodico: su propio hilo, 20 Hz                         │
             └─────────────────────────────────┬──────────────────────────────────┘
                                               │ publisher.PosePublisher
                                               ▼
                         ┌───────────────────────┐
                         │  Mosquitto (local)    │
                         │  1883 TCP · 9001 WS   │
                         └───┬───────────────┬───┘
             telmeow/teleop/pose      telmeow/teleop/page
                         │                   │  telmeow/teleop/story
                         ▼                   ▼
                ┌─────────────────┐   ┌──────────────────────┐
                │  visor 3D       │   │  Raspberry Pi (P11)  │
                │  (navegador)    │   │  pantalla externa    │
                │  clampa otra vez│   │  15.6" HDMI          │
                └─────────────────┘   └──────────────────────┘
```

Tres cadencias, cada una con su reloj: la visión corre a lo que dé la cámara,
la emoción a 5 Hz en su propio hilo (P7.1: dentro del bucle, cada inferencia
retrasaba un frame), y la publicación a 20 Hz en otro hilo que solo envía la
última pose. Publicar en cada frame no da 20 Hz: el publisher descarta lo que
llega a menos de 45 ms del anterior, y a 30 fps pasaría uno sí y otro no
(15 Hz).

El recorte de poses tiene **dos capas**: `limits.py` recorta en Python contra
las tablas del barrido BVH antes de publicar, y el visor vuelve a verificar la
pose completa contra la geometría real al recibirla. La segunda capa hace falta
porque las tablas se midieron con los demás grados de libertad en posición home
y no cubren combinaciones simultáneas.

---

## Instalación

**Intérprete: CPython 3.12 x64.** Es el máximo común denominador: TensorFlow
publica ruedas hasta cp313 y MediaPipe hasta cp312. En Python 3.13 y 3.14 la
instalación falla porque no existe rueda de MediaPipe.

```bash
py install 3.12
```

```bash
py -V:3.12 -m venv .venv
```

```bash
.venv\Scripts\activate
```

```bash
pip install -r requirements.txt
```

Para trabajar solo sobre la lógica de límites y configuración no hace falta nada
de lo anterior: `config.py`, `limits.py` y sus pruebas usan únicamente
biblioteca estándar.

```bash
pip install -r requirements-dev.txt
```

```bash
pytest
```

### Modelos del clasificador de emoción

El clasificador de emoción usa por defecto el backend `combinado` (elegido en
el P6.4 del plan maestro): HSEmotion `mbf_va_mtl` en ONNX más los coeficientes
de la cara de MediaPipe Face Landmarker, relativos a la cara neutra del
operador. Sus dos modelos no van en git; se bajan, con su SHA-256 comprobado,
a `modelos_externos/`:

```bash
python tools/descargar_modelos.py
```

Al arrancar, el clasificador toma la cara neutra con los primeros 3 s de cara:
hay que mirar la pantalla con la cara relajada. En el modo demo, la tecla `c`
la vuelve a tomar. Si faltan los modelos, el demo avisa y arranca con el modelo
propio (`emotion_model.keras`, fuera de git).

### Backends opcionales del clasificador de emoción

DeepFace y HSEmotion (con torch) solo hacen falta para la evaluación
comparativa (P9), y **van en un entorno aparte**: las dependencias que declara
DeepFace instalarían otra copia de OpenCV y un protobuf que rompe MediaPipe. La
cabecera de [`requirements-benchmark.txt`](requirements-benchmark.txt) explica
por qué, y estos son los pasos:

```bash
py -V:3.12 -m venv .venv-benchmark
```

```bash
.venv-benchmark\Scripts\activate
```

```bash
pip install -r requirements.txt
```

```bash
pip install --no-deps deepface==0.0.102 hsemotion==0.3.0
```

```bash
pip install -r requirements-benchmark.txt
```

Sin ellos, el modo demo del clasificador (`python -m
telmeow_teleop.emotion_classifier`) sigue funcionando y la tecla `b` se salta
los backends que no están.

### Modelo de emoción entrenado con FER-2013

El modelo propio se entrenó con fuga de datos (frames consecutivos de una sola
persona a los dos lados de la partición). [`tools/train_fer2013.py`](tools/train_fer2013.py)
entrena la misma red con FER-2013 y su partición oficial: descarga el
`fer2013.tar.gz` original (96 MB, fijado por revisión y SHA-256) a `fer2013/`,
fuera de git, y escribe `emotion_model_fer2013.keras` con sus metadatos y su
historial. En CPU tarda alrededor de media hora, y con la misma semilla da los
mismos pesos bit a bit.

```bash
python tools/train_fer2013.py
```

Con `--arquitectura vgg` o `--arquitectura vgg_sin_dropout` entrena las redes
mayores del P8.1, cada una en su propio archivo (`emotion_model_fer2013_vgg.keras`…):
ninguna pisa el modelo elegido.

El clasificador lo usa en vez del propio con:

```bash
$env:TELMEOW_MODEL_PATH="emotion_model_fer2013.keras"
```

---

## Configuración del broker

Mosquitto necesita **DOS listeners**, y esta es la causa más frecuente de que
«el MQTT no funcione»:

| Puerto | Protocolo | Quién lo usa |
|---|---|---|
| 1883 | TCP crudo | El pipeline de Python (paho-mqtt) |
| 9001 | WebSocket | El visor 3D en el navegador |

**Un navegador no puede abrir una conexión TCP cruda.** Si solo se abre el 1883,
el publisher de Python funciona perfectamente y el visor nunca conecta — un
síntoma engañoso, porque el broker parece sano desde el lado de Python.

El archivo [`mosquitto.conf`](mosquitto.conf) de este repositorio ya lo declara:

```
listener 1883
listener 9001
protocol websockets
allow_anonymous true
```

Mosquitto se descarga de [mosquitto.org/download](https://mosquitto.org/download/)
(instalador x64 para Windows), o con el gestor de paquetes:

```bash
winget install --id EclipseFoundation.Mosquitto --exact
```

Requiere **privilegios de administrador**: escribe en `Program Files` y registra
un servicio de Windows.

El instalador **no añade Mosquitto al PATH**, así que hay que invocarlo por ruta
completa. Para lanzarlo en primer plano y ver la traza, desde esta carpeta:

```bash
& "C:\Program Files\mosquitto\mosquitto.exe" -c mosquitto.conf -v
```

Debe imprimir cuatro líneas `Opening ipv4/ipv6 listen socket on port ...`, dos por
cada puerto. Si solo aparecen las del 1883, el archivo de configuración no se
cargó.

Para no repetir la ruta en cada sesión, se puede añadir al PATH del usuario (no
requiere administrador; hay que reabrir la terminal después):

```bash
[Environment]::SetEnvironmentVariable("Path", $env:Path + ";C:\Program Files\mosquitto", "User")
```

> ### ⚠️ Antes de nada: comprobar que hay UN SOLO broker
>
> El instalador de Windows registra un **servicio con arranque automático** que
> usa su propia configuración: escucha en `127.0.0.1:1883` y **no abre ningún
> listener WebSocket**. Si además se lanza la instancia manual del repositorio,
> Windows permite que convivan —el servicio ocupa la dirección de loopback y la
> manual la comodín `0.0.0.0`— y quedan **dos brokers sin conexión entre ellos**:
> Python habla con uno y el visor con el otro.
>
> Es el fallo más difícil de diagnosticar del proyecto, porque **todo informa de
> éxito**: Mosquitto abre los dos puertos, el visor dice «Conectado» y el
> publisher informa de 500 mensajes enviados y 0 sin entregar… mientras el visor
> marca 0.0 Hz.
>
> ```bash
> netstat -ano | findstr ":1883"
> ```
>
> **Dos PID distintos en el 1883 = dos brokers.** La solución (una sola vez) es
> dejar solo la instancia manual, cuya configuración vive versionada en este
> repositorio. Requiere una terminal **abierta como administrador** — con una
> normal falla con «Access is denied»:
>
> ```bash
> Stop-Service mosquitto; Set-Service mosquitto -StartupType Manual
> ```
>
> Si se prefiere no tocar el servicio, la alternativa sin administrador es dejar
> que el servicio conserve el 1883 y levantar la instancia del repositorio en
> otro puerto TCP, apuntando el pipeline con `TELMEOW_MQTT_PORT`. El visor sigue
> usando el 9001 de la instancia manual, y ambos extremos quedan en el mismo
> broker.

### Alternativa sin privilegios de administrador

Si no se puede instalar Mosquitto, [`tools/dev_broker.py`](tools/dev_broker.py)
levanta un broker equivalente en Python puro (`amqtt`) con **los mismos dos
listeners**:

```bash
python tools/dev_broker.py
```

Sirve para desarrollar y para cerrar la verificación del enlace. **El broker
declarado del proyecto sigue siendo Mosquitto**, y es el que debe usarse para
las mediciones que se reporten en la tesis.

---

## Verificar el enlace sin cámara ni modelo

Este es el punto de control del plan: si el brazo se mueve en el visor, el
transporte está resuelto y cualquier fallo posterior es de visión.

**1.** Levantar el broker (ver arriba).

**2.** Publicar una pose oscilante durante 10 segundos:

```bash
python -m telmeow_teleop.publisher
```

**3.** Servir el visor. **Nunca abrirlo con `file://`**: los módulos ES se
bloquean por CORS y la escena se queda en «Inicializando escena…» para siempre.

```bash
python -m http.server 8000 --directory telmeow_viewer
```

Luego abrir `http://localhost:8000/telmeow_viewer.html`.

**4.** En el visor, conectar a `ws://localhost:9001` con el topic
`telmeow/teleop/pose`. El brazo derecho debe oscilar.

**5.** Para ver el JSON crudo en otra terminal:

```bash
python tools/mqtt_monitor.py
```

Verificado con Mosquitto 2.1.2 (2026-08-30) y con el broker en Python puro
(2026-08-29), con idéntico resultado: `armR` recorre 18°→42° y `armRtw` 42°→58°
en el visor, a 20-23 Hz, con el indicador de interferencia en «libre». Detalle
en [`TELMEOW_PLAN_MAESTRO.md`](TELMEOW_PLAN_MAESTRO.md).

El demo respeta las variables de entorno, útil para apuntar a otro broker:

```bash
TELMEOW_MQTT_HOST=192.168.1.50 python -m telmeow_teleop.publisher
```

> **Esperar el enlace antes de emitir.** `connect_async()` devuelve al instante,
> pero el enlace tarda ~2 s en establecerse, y con QoS 0 lo que se publique
> mientras tanto se pierde sin dejar rastro. Usar
> `PosePublisher.wait_connected()` antes de empezar a publicar.

---

## Lanzar el pipeline completo

Con el broker levantado (un solo broker, ver arriba) y el visor servido:

```bash
python -m telmeow_teleop.main
```

Abre UNA cámara y corre a la vez cabeza, brazos y emoción. La ventana tiene el
video a la izquierda, limpio salvo el esqueleto y la caja de la cara, y a la
derecha un panel con los seis ángulos (en rojo si el robot los recorta), la
expresión enviada junto a la predicción cruda, los fps y lo que cuesta cada
etapa, y el estado del enlace: enviados, descartados y **perdidos** (sin enlace).

**Al arrancar, mirar la pantalla con la cara relajada unos 3 s**: la cabeza toma
esa postura como neutra y la emoción, esa cara.

| Tecla | Qué hace |
|---|---|
| `q` | salir (también Ctrl-C; cierra cámara, ventana y MQTT en cualquier caso) |
| `c` | calibrar a la vez la postura neutra de la cabeza y la cara neutra |
| `1`..`6` | forzar neutral, feliz, triste, sorprendido, enojado, concentrado; la misma otra vez la suelta |
| `0` | expresión automática |
| `k` | pausar / reanudar la publicación; al reanudar, el robot va en 1 s de la pose en que se quedó a la tuya (`TELMEOW_RESUME_RAMP_S`) |
| `j` / `l` | gesto de página anterior / siguiente |
| ESPACIO | cambiar de etiqueta (con `--record`) |

Opciones:

```bash
python -m telmeow_teleop.main --record sesion.csv --guardar-recortes sesion_caras
python -m telmeow_teleop.main --no-emotion        # sin backend de emoción
python -m telmeow_teleop.main --no-arms           # sin BlazePose
python -m telmeow_teleop.main --emocion-en-bucle  # la emoción en el bucle, para comparar
python -m telmeow_teleop.main --camera 1 --broker 192.168.1.50:1883
```

`--record sesion.csv` escribe cuatro archivos: `sesion.csv` (una fila por
frame: lo publicado, fps y milisegundos por etapa) y `sesion_cabeza.csv`,
`sesion_brazos.csv` y `sesion_emocion.csv`, con las mismas columnas que los
demos de cada tracker, así que se analizan con las herramientas de siempre:

```bash
python tools/analizar_sesion.py sesion.csv
python tools/analizar_registro.py sesion_cabeza.csv
python tools/analizar_registro_brazos.py sesion_brazos.csv
python tools/analizar_registro_emocion.py sesion_emocion.csv
```

Nada se pisa: lo que ya existiera se conserva con el sufijo `_anterior`.

**Una sesión de verificación entera, guiada:**

```bash
.venv/Scripts/python.exe tools/sesion_verificacion.py
.venv/Scripts/python.exe tools/sesion_verificacion.py --gestos   # la cabeza y la mano a la cara, 3 min
.venv/Scripts/python.exe tools/sesion_verificacion.py --ajustes  # el P7.6: enojo, mejillas, codos y luz
.venv/Scripts/python.exe tools/sesion_verificacion.py --entrenar # el P7.7: caras para reentrenar la emocion
python tools/reentrenar_p77.py                                   # y el reentrenamiento, evaluado en p77
.venv/Scripts/python.exe tools/sesion_verificacion.py --confirmar # la confirmacion, en caras nuevas
python tools/confirmar_p78.py                                    # reentrenado y regla suave, evaluados en p78
python tools/simular_vistazos.py p78                             # los vistazos del P7.8 sobre una sesion
```

Comprueba el broker (y avisa si es el servicio de Mosquitto sin WebSocket),
levanta el visor si hace falta, enseña el protocolo en la consola, graba con el
monitor de latencia a la vez, mide la cámara y deja el informe en
`p7_final_informe.txt`.

**Latencia.** Cada mensaje lleva en `t` la hora de captura de su frame. El
visor muestra «Latencia» (llegada menos `t`) y «Periodo cmd» (el intervalo
entre mensajes, que no es la latencia). La cifra para citar sale de

```bash
python tools/mqtt_monitor.py --latencia latencia.csv
```

con el pipeline en la misma máquina, y lo que tarda la propia cámara, de
`python tools/medir_latencia_camara.py` (destellos de la pantalla).

---

## Sesión de narración (la interfaz final, P10)

Un solo comando, desde la raíz del repositorio:

```bash
.venv/Scripts/python.exe -m telmeow_teleop.control_panel --record sesion.csv
```

Comprueba el broker y lo levanta si falta (avisando si es el servicio de
Mosquitto sin WebSocket), lanza el pipeline con la interfaz y abre el navegador
en `http://localhost:8001`. Ahí está el **visor en modo narración**: el robot y
tu cámara (en espejo) a la izquierda, el libro a la derecha y los botones
debajo, sin cifras de diagnóstico. La ventana de OpenCV se abre aparte, por si
hace falta mirar qué falla; no hace falta tocarla.

**Todas las teclas funcionan con el navegador delante**: las mismas de la tabla
de arriba, más las flechas ←/→ para pasar de página. La `q` hay que pulsarla dos
veces seguidas (Esc cancela), para que una pulsación suelta no corte la sesión.
En pausa no se puede pasar de página: página y gesto van siempre juntos.

Los cuentos son los PDF de `02_Informacion/02_Libro Album/Libros Album`, al lado
del repositorio (no van en git: tienen derechos de autor). Otra carpeta:
`--stories-dir CARPETA` o `TELMEOW_STORIES_DIR`. Los demás argumentos van al
pipeline (`--camera 1`, `--no-arms`...).

Sin cámara ni broker, para ver la página:

```bash
.venv/Scripts/python.exe -m telmeow_teleop.control_panel --demo
```

El pipeline también la sirve por su cuenta con `--interfaz [PUERTO]`. El visor
de siempre, en el 8000 y sin `?modo=narracion`, no cambia.

---

## Contrato MQTT

Un único mensaje JSON en `telmeow/teleop/pose`:

```json
{"pan": -12.4, "tilt": 8.1, "armL": -20.0, "armLtw": 15.0,
 "armR": 30.5, "armRtw": 50.2, "emo": "feliz",
 "gesture": "page_next", "page": 3}
```

- Ángulos en **grados, float, sin cuantizar**. Tasa objetivo 20 Hz.
- **Todos los campos son opcionales.** El visor comprueba cada uno por separado
  y conserva su último valor si falta, así que se pueden emitir mensajes
  parciales sin que el robot dé un tirón hacia home.
- `emo` acepta español o alias en inglés (`happy`, `sad`, `surprise`, `angry`,
  `focused`). También se acepta la clave `emotion` como alias de `emo`.
- `gesture` acepta `page_next` o `page_prev` y dispara el gesto de cabeza.
- `t` (opcional): hora Unix en ms de la captura del frame del que sale la pose,
  para medir la latencia. El visor no la necesita para mover el robot.
- Los mensajes con `gesture` o `page` **nunca** se descartan por límite de tasa:
  son eventos discretos que tienen que llegar.

Los tres topics tienen consumidores distintos:

| Topic | Lo consume | Cadencia |
|---|---|---|
| `telmeow/teleop/pose` | El visor 3D | Continua, 20 Hz |
| `telmeow/teleop/page` | El servicio de pantalla externa (Raspberry) | Evento discreto |
| `telmeow/teleop/story` | El servicio de pantalla externa (Raspberry) | Evento discreto |

Desde el P10, al abrir un cuento el pipeline publica `{"story": "nombre.pdf",
"page": 1}` en `story` y `{"page": 1, "total": M}` en `page`, y en cada cambio
de página la pose con `gesture` y `page` y, en el mismo instante,
`{"page": N, "total": M}` en `page`. Los de `story` y `page` van **retenidos**:
quien se conecte después recibe el último.

Se mantienen separados para que la Raspberry no tenga que analizar 20 mensajes
por segundo cuando solo le interesa uno cada varios segundos.

---

## Estructura

```
02_Codigo/
├── telmeow_teleop/
│   ├── config.py       # broker, topics, tasas, rutas · defaults < entorno < CLI
│   ├── limits.py       # límites articulares + recorte 2D (solo stdlib)
│   ├── filters.py      # One Euro, EMA, histéresis de clase (solo stdlib)
│   ├── camera.py       # apertura de la cámara: backend, formato, exposición
│   ├── publisher.py    # MQTT, JSON único, QoS 0, limitación por descarte
│   ├── head_tracker.py # Face Mesh → pan, tilt (P4)
│   ├── arm_tracker.py  # BlazePose → abducción y flexión de los brazos (P5)
│   ├── retarget.py, escorzo.py, saludo.py   # piezas del arm tracker
│   ├── emotion_classifier.py  # Haar, histéresis, manos, calibración; CNN propia, DeepFace, HSEmotion (P6)
│   ├── emotion_backends.py    # el combinado: HSEmotion ONNX + blendshapes de MediaPipe (P6.4)
│   ├── main.py         # el pipeline integrado: una cámara, los tres, 20 Hz (P7)
│   ├── control_panel.py # la interfaz de narración: servidor, --demo y el comando de la sesión (P10)
│   ├── historias.py    # los cuentos en PDF y la página en que va la lectura (P10)
│   ├── ordenes.py      # teclas y órdenes, las mismas en OpenCV y en el navegador (P10)
│   └── broker.py       # comprobar y levantar el broker (P10)
├── tools/
│   ├── mqtt_monitor.py # suscriptor de diagnóstico a telmeow/# · --latencia (P7.3)
│   ├── analizar_sesion.py, medir_latencia_camara.py  # tasas, costes y latencia (P7)
│   ├── dev_broker.py   # broker MQTT sin privilegios de administrador
│   ├── list_cameras.py # qué cámaras hay y qué resoluciones admiten
│   ├── train_fer2013.py # entrenamiento con FER-2013 y su partición oficial (P8)
│   ├── descargar_modelos.py, entrenar_blendshapes.py, evaluar_backends.py  # P6.4
│   └── medir_emocion.py, analizar_registro*.py, ...  # las cifras del plan
├── tests/              # pytest, sin broker ni cámara
├── legacy/             # los scripts originales, intactos
├── telmeow_viewer/     # el visor 3D y sus STL
├── emotion_model.meta.json   # orden de clases del modelo propio
├── emotion_blendshapes_*.json  # pesos de la regresión de los blendshapes (P6.4)
├── modelos_externos/         # modelos descargados (fuera de git)
├── emotion_model_fer2013.meta.json, .historial.csv   # el de FER-2013 (el .keras no va en git)
├── mosquitto.conf
└── requirements.txt / requirements-dev.txt / requirements-benchmark.txt
```

El plan de implementación completo está en
[`TELMEOW_PLAN_MAESTRO.md`](TELMEOW_PLAN_MAESTRO.md): estado de cada paso, los
prompts pendientes con las consideraciones ya incorporadas, la API construida
hasta ahora, y el registro de hallazgos con su evidencia. Es la única fuente
vigente.
