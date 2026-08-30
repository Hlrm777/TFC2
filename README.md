# Telmeow — pipeline de teleoperación

Estimación de pose y emoción del operador desde una sola cámara RGB, y
publicación por MQTT hacia el visor 3D del robot social **Telmeow**.

Código de la tesis de pregrado de Harold Rodríguez. Los scripts originales se
conservan sin modificar en [`legacy/`](legacy/) como referencia histórica citada
en el documento.

---

## Flujo de datos

```
                     ┌───────────────────────────────────────┐
                     │            main.py (P7)               │
   ┌──────────┐      │   UNA sola captura, UN solo bucle     │
   │  cámara  ├─────►│                                       │
   │  30 fps  │      │   frame ──┬──► HeadTracker    (P4)    │
   └──────────┘      │           │      Face Mesh + solvePnP │
                     │           │         └─► pan, tilt     │
                     │           │                           │
                     │           ├──► ArmTracker     (P5)    │
                     │           │      BlazePose 3D         │
                     │           │         └─► armL, armLtw, │
                     │           │             armR, armRtw  │
                     │           │                           │
                     │           └──► EmotionClassifier (P6) │
                     │                  CNN a 5 Hz           │
                     │                     └─► emo           │
                     │                                       │
                     │   los 6 ángulos ──► limits.clamp_*    │
                     └───────────────┬───────────────────────┘
                                     │ 20 Hz
                              publisher.PosePublisher
                                     │
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
cd D:\02_Tesis\01_Principales\04_TelmeowViewer && python -m http.server 8000
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
en [`HALLAZGOS_PLAN_MAESTRO.md`](HALLAZGOS_PLAN_MAESTRO.md).

El demo respeta las variables de entorno, útil para apuntar a otro broker:

```bash
TELMEOW_MQTT_HOST=192.168.1.50 python -m telmeow_teleop.publisher
```

> **Esperar el enlace antes de emitir.** `connect_async()` devuelve al instante,
> pero el enlace tarda ~2 s en establecerse, y con QoS 0 lo que se publique
> mientras tanto se pierde sin dejar rastro. Usar
> `PosePublisher.wait_connected()` antes de empezar a publicar.

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
- Los mensajes con `gesture` o `page` **nunca** se descartan por límite de tasa:
  son eventos discretos que tienen que llegar.

Los tres topics tienen consumidores distintos:

| Topic | Lo consume | Cadencia |
|---|---|---|
| `telmeow/teleop/pose` | El visor 3D | Continua, 20 Hz |
| `telmeow/teleop/page` | El servicio de pantalla externa (Raspberry) | Evento discreto |
| `telmeow/teleop/story` | El servicio de pantalla externa (Raspberry) | Evento discreto |

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
│   └── publisher.py    # MQTT, JSON único, QoS 0, limitación por descarte
├── tools/
│   └── mqtt_monitor.py # suscriptor de diagnóstico a telmeow/#
├── tests/              # pytest, sin broker ni cámara
├── legacy/             # los scripts originales, intactos
├── mosquitto.conf
└── requirements.txt / requirements-dev.txt
```

Dos documentos acompañan al plan de implementación:

- [`HALLAZGOS_PLAN_MAESTRO.md`](HALLAZGOS_PLAN_MAESTRO.md) — discrepancias entre
  lo que el plan dice y lo que el código realmente hace, con archivo y línea.
- [`ANEXO_PLAN_MAESTRO.md`](ANEXO_PLAN_MAESTRO.md) — qué implican esos hallazgos
  para los pasos que faltan, organizado por prompt.
