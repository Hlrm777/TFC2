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
(instalador x64 para Windows). Para lanzarlo en primer plano y ver la traza:

```bash
mosquitto -c mosquitto.conf -v
```

Debe imprimir dos líneas `Opening ipv4 listen socket on port ...`, una por cada
puerto. Si solo aparece una, el archivo de configuración no se cargó.

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
│   └── publisher.py    # MQTT, JSON único, QoS 0, limitación por descarte
├── tools/
│   └── mqtt_monitor.py # suscriptor de diagnóstico a telmeow/#
├── tests/              # pytest, sin broker ni cámara
├── legacy/             # los scripts originales, intactos
├── mosquitto.conf
└── requirements.txt / requirements-dev.txt
```

Ver [`HALLAZGOS_PLAN_MAESTRO.md`](HALLAZGOS_PLAN_MAESTRO.md) para las
discrepancias encontradas entre el plan de implementación y el estado real del
visor.
