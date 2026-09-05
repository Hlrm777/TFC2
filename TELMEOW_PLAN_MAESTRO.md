# Telmeow — Plan maestro de implementación (v2)

Repositorio: `Hlrm777/TFC2` · Código: `D:\02_Tesis\01_Principales\02_Codigo`
Viewer: `D:\02_Tesis\01_Principales\04_TelmeowViewer\telmeow_viewer.html`

**Versión 2 · 2026-08-30.** Reemplaza a la v1 y absorbe los documentos de
hallazgos y de anexo que existieron entre medias: **este archivo es la única
fuente vigente**. Incorpora los 16 hallazgos de la ejecución de los PROMPT 1
al 3 — cierra decisiones que ya estaban resueltas en el código, corrige cifras
equivocadas, y añade a cada prompt pendiente lo que hay que tener en cuenta
ahora que el paquete existe.

Cómo leerlo:

- **Partes 0 a 5** — el plan propiamente dicho, con los hallazgos ya integrados
  donde toca actuar.
- **Parte 6** — la API ya construida, para escribir los prompts que faltan.
- **Parte 7** — el registro de hallazgos con su evidencia (archivo, línea,
  medición), por si hace falta citarlo o revisarlo.

Los bloques marcados `--- AÑADIDO EN LA v2 ---` dentro de cada prompt son lo que
cambia respecto al plan original.

---

# PARTE 0 — Estado

## 0.1 Progreso

| Prompt | Estado | Commit |
|---|---|---|
| **P1** — Estructura y parámetros | ✅ **hecho** | `66440a5` |
| **P2** — Capa MQTT | ✅ **hecho** · punto de control **superado** | `7481a96`, `83224b5`, `9e626d9` |
| **P3** — Filtros temporales | ✅ **hecho** | `fc7a6ec` |
| **P4** — Head tracker | ✅ **hecho** ⚠️ falta confirmar el sentido del pan en el visor | |
| P5 — Arm tracker | ⬜ **siguiente** | |
| P6 — Clasificador de emoción | ⬜ | |
| P8 — Entrenamiento FER-2013 | ⬜ | |
| P7 — Integración | ⬜ | |
| P10 — Panel del operador | ⬜ | |
| P11 — Pantalla externa (RPi) | ⬜ | |
| P9 — Evaluación comparativa | ⬜ | |

**339 pruebas en verde.** Entorno: CPython 3.12.1 x64, `.venv` en el repositorio.

## 0.2 Bugs del código legacy

Los 14 originales, más dos encontrados al ejecutar los prompts (marcados
**nuevo**):

| # | Hallazgo | Archivo | Gravedad |
|---|---|---|---|
| 1 | El bloque IZQUIERDO imprime `right_red_angle` / `right_green_angle`; los ángulos del brazo izquierdo se calculan y nunca se usan | `Hands Tracking.py` | **Bug** |
| 2 | Guardas anti-NaN inservibles: `float(x) == float('nan')` es siempre `False` | `Hands Tracking.py` | **Bug** |
| 3 | `arccos(arm_length / max_arm_length)` produce NaN cuando el cociente > 1 | `Hands Tracking.py` | **Bug** |
| 4 | `angle_green` mide la muñeca respecto a la **cadera**, no al hombro → no es un ángulo de articulación de hombro | `Hands Tracking.py` | Modelo incorrecto |
| 5 | QoS 1 + `wait_for_publish()` bloqueante por mensaje → no sostiene 20 Hz | `MQTT_Publisher.py` | **Bug de diseño** |
| 6 | Broker público `mqtt.eclipseprojects.io` por TCP 1883; el navegador no puede conectarse | `MQTT_Publisher.py` | Incompatible con viewer |
| 7 | Esquema de topics `telmeow/angles/head/1` con escalares; el viewer espera un JSON en `telmeow/teleop/pose` | `MQTT_Publisher.py` | Incompatible con viewer |
| 8 | Se inicializa MediaPipe FaceMesh y nunca se usa; todo el trabajo lo hace dlib | `Face Tracking.py` | Dependencia muerta |
| 9 | Cuantización a pasos de 5°/10° → movimiento a saltos | ambos trackers | Calidad |
| 10 | Cada script abre su propio `VideoCapture(0)` → no pueden correr a la vez | los 4 | **Bloqueante** |
| 11 | Sin suavizado temporal en emoción → parpadeo de clase entre frames | `Emotion Recognition.py` | Calidad |
| 12 | `model.predict()` por frame dentro del bucle → sobrecarga y *retracing* | `Emotion Recognition.py` | Rendimiento |
| 13 | Dataset de 2000 frames consecutivos + `train_test_split` aleatorio → **fuga de datos**, accuracy inflada | `Creation_of_dataset.py` + `Training…` | **Riesgo de defensa** |
| 14 | `train_test_split` sin `random_state` → resultados no reproducibles | `Training…` | Metodológico |
| **15** | **nuevo** · El `CascadeClassifier` se construye **dentro** de `detect_face`, o sea una vez por frame: ~900 KB de XML parseados 30 veces por segundo | `Emotion Recognition.py`, `Creation_of_dataset.py`, `Training…` | Rendimiento |
| **16** | **nuevo** · Ruta del dataset escrita a fuego y apuntando a **otra máquina** (`C:\Users\harol\...`); el script no corre tal cual aquí | `Training Emotion Recognition.py:9` | **Bloquea P9 A/B** |

## 0.3 Arquitectura

```
02_Codigo/
├── telmeow_teleop/
│   ├── __init__.py
│   ├── config.py             ✅ broker, topics, tasas, rutas · solo stdlib
│   ├── limits.py             ✅ límites articulares + clamp 2D · solo stdlib
│   ├── filters.py            ✅ One Euro, EMA, histéresis · solo stdlib
│   ├── publisher.py          ✅ MQTT, JSON único, QoS 0
│   ├── head_tracker.py       ✅ Face Mesh + solvePnP
│   ├── arm_tracker.py        ⬜ P5
│   ├── emotion_classifier.py ⬜ P6
│   ├── control_panel.py      ⬜ P10
│   └── main.py               ⬜ P7
├── raspberry/
│   └── story_display.py      ⬜ P11
├── legacy/                   ✅ los scripts originales, intactos
│   ├── MQTT/
│   └── haarcascade_frontalface_default.xml   (huérfano, ver §0.6)
├── tools/
│   ├── mqtt_monitor.py       ✅
│   ├── dev_broker.py         ✅ broker sin privilegios de administrador
│   ├── train_fer2013.py      ⬜ P8
│   └── eval_emotion.py       ⬜ P9
├── tests/                    ✅ 339 pruebas
├── mosquitto.conf            ✅ los dos listeners
├── requirements.txt / requirements-dev.txt
└── README.md
```

**`config.py`, `limits.py` y `filters.py` usan solo biblioteca estándar.** Es
deliberado: permite validar toda la lógica de límites, configuración y filtrado
con `pip install -r requirements-dev.txt && pytest`, sin descargar los ~600 MB
de TensorFlow. Mantener esa propiedad al añadir código.

## 0.4 Contrato MQTT (definitivo, verificado contra el visor)

- **Broker:** Mosquitto local, listener TCP `1883` (Python) + WebSocket `9001`
  (navegador). Ver §4.0 — **instalarlo no basta**.
- **Topics y sus consumidores** — están separados porque los lee hardware
  distinto y a ritmos muy distintos:

| Topic | Lo consume | Cadencia |
|---|---|---|
| `telmeow/teleop/pose` | El visor 3D | Continua, 20 Hz |
| `telmeow/teleop/page` | El servicio de pantalla externa (RPi) | Evento discreto |
| `telmeow/teleop/story` | El servicio de pantalla externa (RPi) | Evento discreto |

```json
{"pan": -12.4, "tilt": 8.1, "armL": -20.0, "armLtw": 15.0,
 "armR": 30.5, "armRtw": 50.2, "emo": "feliz",
 "gesture": "page_next", "page": 3}
```

Correcciones respecto a la v1, verificadas en `telmeow_viewer.html:987-1023`:

- **El visor sí consume `gesture` y `page`** dentro del payload de pose
  (líneas 1005-1009), además de los 6 ángulos y `emo`.
- **`emotion` funciona como alias de `emo`** (línea 1004).
- **Todos los campos son opcionales.** El visor comprueba cada uno por separado
  (`typeof o.X === 'number'`) y **conserva el último valor de los que falten**.
  Esto es lo que permite cumplir el requisito de «reutilizar el último valor
  válido» sin publicar ceros: basta con **omitir el campo**.
- `gesture` acepta `page_next` / `page_prev`.

Ángulos en grados (float), sin cuantizar. Tasa objetivo 20 Hz.

**Sobre la latencia declarada.** El indicador `t-lat` del visor está
honestamente etiquetado como «proxy», pero mide el **intervalo entre mensajes**,
que está acotado por arriba por el periodo de publicación: no puede variar con
la latencia que dice aproximar. Medido, marca 49-51 ms, que es exactamente el
periodo de 20 Hz. **La latencia extremo a extremo no está medida.** Ver §3.2
para la decisión pendiente.

## 0.5 Límites articulares

```
pan          −90° … +90°
tilt         −39° … +42°
giro         −90° … +90°   (ambos brazos, límite duro)
```

**El balanceo depende del giro.** Las tablas 2D del barrido BVH (paso 5°,
refinado 1°, demás DOF en home) son **la autoridad**; están en `limits.py`.

Los valores 1D de balanceo (izq −55…+32, der −55…+33) **no se aplican**: son la
rebanada de la tabla en giro = 0° medida con refinado a 1°, y se conservan solo
como metadato documentado. Aplicarlos colapsaría el rango y le quitaría al brazo
el recorrido extra que gana al girar y despejarse del tronco.

Orden de operaciones en `clamp_arm`: recortar el **giro** a ±90° primero (es
límite duro y además índice de la tabla), luego interpolar la tabla en ese giro,
y con ese rango recortar el **balanceo**.

La prueba que distingue la implementación correcta de la errónea:
`clamp_arm(+70, giro=+90, 'izq')` debe dar **+70**, no +32.

## 0.6 Decisiones cerradas (eran «abiertas» en la v1)

| Tema v1 | Resolución |
|---|---|
| ¿El visor debe clampar lo recibido por MQTT? | **Ya lo hace.** `clampPoseExternal()`, con bisección sobre el segmento completo entre la última pose válida y la pedida. Es doble capa deliberada. |
| ¿Quitar solo la UI de barridos o también el BVH? | **Ya resuelto.** Los barridos se retiraron; el BVH y el plano posterior siguen activos porque operan en vivo como filtro, no como instrumentación. |
| ¿Qué cascade Haar usar? | **El de OpenCV** (`cv2.data.haarcascades`), que es el que usaron los tres scripts legacy y con el que se entrenó el modelo. La copia que había en la raíz **no es el mismo archivo** (963.441 vs 930.127 bytes) y está en `legacy/`. |
| Tilt del gesto de página | **−30°**, no −35°. El valor implementado es `PAGE_GESTURE.tiltDown = -30` (`telmeow_viewer.html:849`). Margen real respecto al límite mecánico: **9°**, no 4°. |

## 0.7 Nota metodológica de medición — aplica a todo el proyecto

**Toda medida de tiempo debe usar `time.perf_counter()`, nunca
`time.monotonic()`.**

En Windows, `monotonic` tiene una resolución de **~15.625 ms**, comparable a las
magnitudes que este proyecto mide (periodo de 50 ms a 20 Hz, `dt` de 33 ms a
30 fps). Medido con el mismo bucle emisor:

| Reloj | Intervalo mín. | Mediana | Descartes | Tasa |
|---|---|---|---|---|
| `time.monotonic()` | 46.0 ms | 47.0 ms | 9/60 | 17.0 Hz |
| `time.perf_counter()` | 50.1 ms | 50.4 ms | 0/60 | 20.0 Hz |

Un error sistemático del orden del 15 %. Afecta al HUD del P7, al `dt` del filtro
One Euro del P3, y a cualquier cifra de tasa o latencia que se reporte en la
tesis.

---

# PARTE 1 — Orden de ejecución

| Orden | Prompt | Qué hace | Estado |
|---|---|---|---|
| 1 | **P1** | Estructura del paquete + límites articulares | ✅ |
| 2 | **P2** | Capa MQTT compatible con el viewer | ✅ · punto de control superado |
| 3 | **P3** | Filtros temporales (One Euro, histéresis) | ✅ |
| 4 | **P4** | Head tracker (Face Mesh + solvePnP, sin dlib) | ✅ |
| 5 | **P5** | Arm tracker corregido (bugs 1–4) | ⬜ **siguiente** |
| 6 | **P6** | Clasificador de emoción con backends intercambiables | ⬜ |
| 7 | **P8** | Entrenamiento con FER-2013 | ⬜ ⚠️ revisar accuracy |
| 8 | **P7** | Integración: captura única + HUD | ⬜ |
| 9 | **P10** | Panel de control del operador | ⬜ |
| 10 | **P11** | Servicio de pantalla externa en la Raspberry | ⬜ |
| 11 | **P9** | Evaluación comparativa | ⬜ |

**Punto de verificación restante:** después de P8, si la accuracy sobre FER-2013
sale entre 60–70 %, es correcto. Si sale > 90 %, hay un error de partición —
detenerse y revisar.

*(El punto de control de P2 ya se superó: el brazo oscila en el visor.
`armR` 18→42, `armRtw` 42→58, 21 Hz, sin recorte por colisión. Verificado con
Mosquitto 2.1.2 y con el broker en Python puro, con idéntico resultado.)*

---

# PARTE 2 — Prompts

## PROMPT 1 — ✅ HECHO

Movidos los scripts a `legacy/`. Creado `telmeow_teleop/` con `config.py`
(precedencia defaults < entorno < CLI, prefijo `TELMEOW_`) y `limits.py`
(tablas 2D del barrido BVH). `requirements.txt` sin dlib.

## PROMPT 2 — ✅ HECHO

`publisher.PosePublisher`: un solo JSON en `telmeow/teleop/pose`, QoS 0 sin
`wait_for_publish()`, reconexión automática, limitación de tasa por descarte con
excepción para los eventos discretos. `tools/mqtt_monitor.py`,
`tools/dev_broker.py`, `mosquitto.conf`, README.

## PROMPT 3 — ✅ HECHO

`filters.py`: `OneEuroFilter` (Casiez, Roussel & Vogel, CHI 2012), `EMAFilter`
como línea base, `ClassHysteresis`. Medido sobre escalón con ruido a 100 Hz:

| Filtro | Reducción de ruido | Retardo al 90 % |
|---|---|---|
| **OneEuro(mc=0.5, β=0.5)** | **11.2×** | **6 muestras (60 ms)** |
| EMA(α=0.06) | 9.7× | 36 muestras (360 ms) |
| EMA(α=0.2) | 3.8× | 10 muestras (100 ms) |

El One Euro gana **en los dos ejes a la vez**. Cifra citable en la tesis.

---

## PROMPT 4 — Head tracker ✅ HECHO

`head_tracker.py`: Face Mesh + `solvePnP` sobre un modelo 3D canónico de 6
puntos, sin dlib. Cadena: solvePnP → restar neutro → ganancia → One Euro →
`clamp_head`. Ganancias `gain_pan` / `gain_tilt` separadas, ambas 1.0 por
defecto, más `invert_pan` / `invert_tilt`.

**Verificado con datos sintéticos** (proyectar el modelo con una rotación
conocida y recuperarla): error **0.00° en ±40°**, sin contaminación entre ejes.
Signos medidos: `yaw+` = la cara mira a la derecha de la imagen; `pitch+` = mira
hacia arriba.

### Resultados medidos con el operador real (2026-09-05)

Sesión grabada con `--record` y analizada con `tools/analizar_registro.py`.
1962 muestras, cámara externa a 1280x720.

**Repetibilidad (temblor), medida como la mediana de la desviación en ventanas
de 1 s.** Es la cifra que sustituye a la exactitud absoluta, que no se puede
medir sin captura de movimiento (§3.3.2):

| Método | Temblor en pan | Temblor en tilt |
|---|---|---|
| **mesh** | **0.19°** | 0.31° |
| pnp | 1.47° | 0.30° |

**El método `mesh` tiene 7.7× menos temblor en el giro lateral**, y empatan en
cabeceo. La ventaja se concentra donde PnP con 6 puntos tiene sus soluciones
ambiguas. El filtro One Euro reduce además el temblor de 0.31° a 0.17°.

**Efecto de la distancia** — mucho menor de lo esperado:

| Cara (px entre ojos) | Temblor tilt |
|---|---|
| 236 (cerca) | 0.281° |
| 180 (normal) | 0.313° |
| 140 (lejos) | 0.319° |

Alejarse un 40 % solo empeora un 14 %. El operador no necesita trabajar
incómodamente cerca.

**Recorrido alcanzado:** pan ±55.5°, tilt +33.3° / −40.2°. Hacia abajo **ya
supera** el tope del robot (−39°), lo que descarta la queja inicial de que
bajar la cabeza no llegaba: aquello venía de la cámara integrada mirando desde
abajo, no del estimador. Cambiar a una cámara externa a la altura de los ojos lo
resolvió.

**Otras confirmaciones medidas:**

- El ladeo de cabeza «de perro» (roll), que el robot no puede reproducir, deja
  pan y tilt en 0.00 y **no degrada** su medida: con 45° de ladeo, un giro real
  de 20° se sigue midiendo como 20.00.
- MediaPipe arrastra **TensorFlow entero** (2289 módulos) al importarse. De ahí
  el arranque lento y los avisos de oneDNN. Es dependencia suya, no nuestra, pero
  hay que tenerlo en cuenta para la Raspberry Pi del PROMPT 11.

⚠️ **Pendiente de confirmar con el visor:** con la imagen espejada que usa el
pipeline, el operador girando a *su* izquierda produce `pan` **positivo**
(confirmado por el operador). Falta ver en el visor hacia qué lado debe girar el
robot y, si sale al revés, poner `invert_pan=True`. No requiere tocar código.

### Enunciado original



```
Crea `telmeow_teleop/head_tracker.py` reemplazando legacy/Face Tracking.py.

Cambio importante: elimina dlib por completo. El script legacy inicializa
MediaPipe Face Mesh y nunca lo usa — todo el trabajo lo hace dlib con
shape_predictor_68_face_landmarks.dat, un archivo de ~100 MB que además está en
.gitignore, lo que hace el repo no reproducible. Face Mesh ya es dependencia,
no requiere descargas externas y es lo que declaro en el documento de tesis.

Implementa `HeadTracker`:

1. `process(frame_rgb) -> (pan, tilt) | None`, usando FaceMesh con
   refine_landmarks=True y max_num_faces=1.

2. Estimación de pose de cabeza por **solvePnP**, no por arctan2 de dos puntos.
   Usa un modelo 3D canónico de rostro con 6 puntos (punta de nariz, mentón,
   comisuras externas de ojos, comisuras de boca) y los landmarks 2D
   correspondientes de Face Mesh. Obtén el vector de rotación con
   cv2.solvePnP (SOLVEPNP_ITERATIVE), conviértelo con cv2.Rodrigues y extrae
   los ángulos de Euler. Esto es geométricamente correcto y defendible ante un
   jurado, a diferencia del arctan2 sobre landmarks proyectados, que confunde
   rotación con traslación lateral de la cabeza.

3. Mapeo a los DOF del robot:
   - yaw del operador → `pan`, tilt del operador → `tilt`
   - Escalado configurable (ganancia por defecto 1.0) y recorte con
     `limits.clamp_head`
   - NO cuantices a pasos de 5° (el legacy lo hacía): salida continua en float.

4. Filtrado One Euro independiente por canal, usando `filters.OneEuroFilter`.

5. Calibración: método `set_neutral()` que captura la pose actual como origen y
   la resta en adelante. Sin esto, la postura natural del operador se traduce en
   un sesgo constante del robot.

6. Modo demo: `python -m telmeow_teleop.head_tracker` abre la cámara, dibuja los
   ejes de la pose sobre el rostro y muestra pan/tilt en pantalla.

--- AÑADIDO EN LA v2 ---

7. `limits.clamp_head` devuelve un objeto `HeadClamp`, no una tupla:
       h = clamp_head(pan, tilt)
       h.pan.value      # ángulo recortado
       h.as_tuple       # (pan, tilt)
       h.clamped        # si hubo recorte
       h.pan.fraction   # posición en el rango, para el HUD del P7

8. `clamp_head` lanza `ValueError` ante NaN o infinito, a propósito, para que un
   NaN no llegue al robot. solvePnP puede no converger: captura la excepción y
   **descarta el frame**, devolviendo None. No lo dejes para main.py.

9. Los timestamps del One Euro deben venir de `time.perf_counter()`. Con
   `monotonic` en Windows el `dt` de 33 ms de una cámara a 30 fps sale con hasta
   40 % de error, y a veces 0 — que en el filtro es una división por cero. El
   filtro ya lo sustituye y lo contabiliza en `degenerate_dt_count`; expón ese
   contador para poder verlo.

10. mediapipe 0.10.14 está verificado en el entorno: `mp.solutions.face_mesh`
    existe y `FaceMesh(refine_landmarks=True, max_num_faces=1)` se instancia sin
    problema. No hace falta comprobarlo otra vez.

11. GANANCIA DE MAPEO: decisión aún abierta (§3.2). Impleméntala configurable
    con valor por defecto 1.0, expuesta en `config.py`, y déjala anotada como
    parámetro a calibrar con una persona real. No la fijes en el código.

Commit: "refactor: head tracker con Face Mesh y solvePnP"
```

---

## PROMPT 5 — Arm tracker ⬜

```
Crea `telmeow_teleop/arm_tracker.py` reemplazando legacy/Hands Tracking.py.
Ese archivo tiene bugs concretos que hay que corregir, no heredar:

BUG 1: en el bloque de impresión IZQUIERDO se usan `right_red_angle` y
`right_green_angle`. Los ángulos del brazo izquierdo se calculan y se descartan.

BUG 2: los guardas `if float(x) == float('nan')` nunca se cumplen, porque NaN no
es igual a nada, ni a sí mismo. Hay que usar `math.isnan`.

BUG 3: `arccos(arm_length / max_arm_length)` devuelve NaN siempre que el
cociente supere 1, lo que ocurre cada vez que la muñeca se aleja del hombro más
que la distancia hombro-cadera. Hay que acotar el argumento a [-1, 1] o, mejor,
cambiar de formulación.

BUG 4 (de modelo, el más importante): `angle_green` mide la muñeca respecto a la
CADERA. Eso no es un ángulo de la articulación del hombro. El robot tiene dos
DOF por brazo: `balanceo` (abducción en el plano frontal) y `giro` (rotación
axial). Ambos son ángulos del hombro.

Implementa `ArmTracker` con BlazePose (mp.solutions.pose), usando las
coordenadas 3D de `pose_world_landmarks` (en metros, origen en la cadera), no
las 2D normalizadas de imagen:

1. `balanceo`: ángulo entre el vector hombro→codo y el vector vertical del
   tronco, proyectados sobre el plano frontal del operador. Signo positivo hacia
   afuera (abducción).

2. `giro`: rotación axial del brazo. Derívalo del vector codo→muñeca respecto al
   plano definido por tronco y húmero. IMPORTANTE: documenta en el docstring que
   esta estimación es la menos observable del sistema — la rotación axial del
   húmero no se determina de forma robusta desde landmarks de una sola cámara
   RGB, y su incertidumbre es mayor que la del balanceo. Es una limitación que
   voy a declarar explícitamente en la tesis; no la disimules con suavizado
   agresivo.

3. Ambos brazos, sin copiar-pegar: una sola función parametrizada por lado.

4. Manejo del espejo: la cámara se voltea horizontalmente (cv2.flip) para que el
   operador se vea como en un espejo. Define y documenta con claridad si el
   brazo izquierdo del operador comanda el brazo izquierdo del robot o el
   contrario, y hazlo configurable con `mirror=True/False`.

5. Recorte con `limits.clamp_arm(balanceo, giro, lado)`.

6. Filtrado One Euro por canal (4 canales).

7. Salida `(armL, armLtw, armR, armRtw)`, en grados, continuos, sin cuantizar.

8. Modo demo con esqueleto dibujado y los 4 valores en pantalla.

--- AÑADIDO EN LA v2 ---

9. `clamp_arm` devuelve un objeto `ArmClamp`, no una tupla:
       a = clamp_arm(balanceo, giro, "izq")
       a.balanceo.value    # ángulo recortado
       a.as_tuple          # (balanceo, giro)
       a.clamped           # si hubo recorte
       a.balanceo.lower / .upper / .fraction   # rango APLICABLE a ese giro

   `lado` acepta 'L'/'R', 'left'/'right' e 'izq'/'der', sin distinguir
   mayúsculas.

10. `clamp_arm` lanza `ValueError` ante NaN o infinito. BlazePose los produce:
    captura la excepción y **descarta el frame**, reutilizando el último valor
    válido. Es la respuesta correcta al BUG 2, y va aquí, no en main.py.

11. CONVENCIÓN DE SIGNOS, ya fijada por el visor y no negociable:
    **giro+ = adelante en AMBOS brazos**. El eje del brazo derecho está
    invertido en el rig respecto al original, y las tablas 2D de `limits.py`
    están en esa convención. La decisión sobre `mirror` tiene que producir
    salidas coherentes con esto, o los brazos del robot se moverán al revés que
    los del operador. Verifícalo mirando el visor en vivo: el transporte ya
    funciona, así que es barato.

12. Recuerda que el rango de balanceo **depende del giro** — `clamp_arm` ya lo
    interpola, no lo repliques.

Commit: "fix: calculo de angulos de brazo y mapeo a DOF del robot"
```

---

## PROMPT 6 — Clasificador de emoción ⬜

```
Crea `telmeow_teleop/emotion_classifier.py`, reemplazando
legacy/Emotion Recognition.py.

Requisito de diseño: BACKENDS INTERCAMBIABLES tras una interfaz común, porque
necesito comparar tres enfoques en el capítulo de resultados. Define una clase
base `EmotionBackend` con `predict(face_gray_48x48) -> (label, confidence)` y:

1. `KerasBackend`: carga un .keras propio. Debe soportar tanto el modelo actual
   (5 clases) como el que entrenaré con FER-2013. El orden de clases NO se
   infiere: se lee de un archivo JSON de metadatos junto al modelo. Si no
   existe, usa el orden legacy y emite un warning — el orden equivocado degrada
   las predicciones en silencio, que es el peor modo de fallo posible.

2. `DeepFaceBackend`: usa deepface. Mapea sus etiquetas inglesas a las mías y
   colapsa 'disgust' y 'fear' a la clase más cercana o a None (configurable).

3. `HSEmotionBackend`: usa hsemotion con enet_b0_8_best_afew en CPU.

Los backends 2 y 3 son OPCIONALES: si la librería no está instalada, el import
falla con un mensaje claro pero el programa sigue funcionando con Keras. Van en
`requirements-benchmark.txt`, no en requirements.txt.

Sobre `EmotionClassifier`:

4. Preprocesamiento IDÉNTICO al del entrenamiento: detectar rostro con cascade
   Haar, recortar, redimensionar a 48×48, convertir a gris, dividir entre 255.

5. Rendimiento: no uses `model.predict()` en el bucle. Usa `model(x,
   training=False)`. Ejecuta la inferencia a `config.emotion_hz` (5 Hz), no por
   frame.

6. Estabilización con `filters.ClassHysteresis`. Umbral de confianza
   configurable; por debajo, mantiene la clase anterior.

7. CONCENTRADO es un estado MANUAL del operador, no salida del modelo. El visor
   tiene 6 expresiones y el clasificador emite 5. Implementa `toggle_manual(e)`
   y `clear_manual()`. El estado por defecto al arrancar es NEUTRAL.

8. `process(frame_bgr) -> (emotion, confidence)` y modo demo con caja de rostro,
   clase y confianza, permitiendo alternar backend con una tecla.

--- AÑADIDO EN LA v2 ---

9. EL CASCADE SALE DE OpenCV, no del repositorio. `config.PathConfig.face_cascade`
   vale `None` por defecto, y eso significa:
       ruta = cfg.paths.face_cascade or (
           Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")

   Motivo: los tres scripts legacy usan `cv2.data.haarcascades`, y el modelo se
   entrenó sobre los rostros que recortaba ESE cascade. La copia que había en la
   raíz del repositorio **no es el mismo archivo** (963.441 vs 930.127 bytes) y
   ahora está en `legacy/`. Usarla cambiaría el preprocesamiento y con él las
   métricas que el P9 debe reproducir.

10. BUG 15 (nuevo, no estaba en la lista de la v1): el legacy construye el
    `CascadeClassifier` DENTRO de `detect_face`, o sea una vez por frame — son
    ~900 KB de XML parseados 30 veces por segundo. **Constrúyelo una sola vez**
    en el `__init__`. Es del mismo orden de gravedad que el bug 12.

11. ORDEN DE PREPROCESAMIENTO, verificado en los tres scripts legacy y que hay
    que conservar tal cual: recortar rostro → `resize(48,48)` **sobre BGR** →
    `cvtColor(BGR2GRAY)` → `/255`. Es poco habitual (normalmente se convierte a
    gris antes de redimensionar), pero al ser idéntico en entrenamiento e
    inferencia es correcto. No lo «arregles».

12. ORDEN DE CLASES: ya está en `config.EMOTION_LABELS`, verificado idéntico en
    los tres scripts legacy, con una prueba que lo fija:
        ("feliz", "triste", "sorprendido", "neutral", "enojado")

13. La `ClassHysteresis` corre a 5 Hz, no a 30: `n_frames` cuenta INFERENCIAS.
    Con n_frames=5 el cambio tarda hasta 600 ms, más los 350 ms del crossfade
    del visor. Elige el valor con esa tasa en mente y documéntalo.

    Y ten presente su límite documentado: la mayoría sola no frena una
    alternancia perfecta entre dos clases (la ventana queda 3-2). Lo que sí la
    frena es `min_confidence`, porque un clasificador que alterna es un
    clasificador dudando. Usa las dos condiciones.

14. Entorno verificado: TensorFlow 2.16.1 carga `emotion_model.keras` (guardado
    con Keras 3.3.3; TF arrastra Keras 3.15.1, que lee hacia adelante). Entrada
    (None,48,48,1), salida (None,5), y `model(x, training=False)` funciona.

Commit: "feat: clasificador de emocion con backends intercambiables"
```

---

## PROMPT 7 — Integración ⬜

```
Crea `telmeow_teleop/main.py`, el punto de entrada que integra todo.

Restricción estructural: los scripts legacy abrían cada uno su propio
`cv2.VideoCapture(0)`. En Windows dos procesos no pueden abrir la misma cámara,
así que nunca pudieron ejecutarse simultáneamente. Aquí debe haber UNA sola
captura y UN solo bucle que alimente a los tres estimadores.

1. Bucle principal: captura un frame, lo voltea horizontalmente (espejo), lo
   convierte a RGB una sola vez y lo comparte entre HeadTracker, ArmTracker y
   EmotionClassifier. No conviertas el mismo frame tres veces.

2. Desacople de tasas: visión a 30 fps, emoción a 5 Hz, publicación a 20 Hz.
   Cada bloque se ejecuta según su propio temporizador.

3. Publica un único mensaje con los 6 ángulos y la emoción.

4. HUD sobre la ventana de video, en una esquina:
   - los 6 ángulos con una barra que muestre su posición dentro del rango
     admisible, y en rojo cuando el valor esté siendo recortado
   - emoción actual y confianza
   - fps de captura, Hz de publicación, estado del enlace MQTT

5. Teclas: `q` salir · `c` calibrar neutral de cabeza · `m` alternar espejo ·
   `1..6` forzar emoción manual · `0` volver a automático · `p` pausar publicación.

6. Argumentos CLI: `--camera`, `--broker`, `--no-emotion`, `--no-arms`,
   `--record`. `--record` guarda un CSV con timestamp y los 6 ángulos + emoción.

7. Cierre limpio: libera cámara, cierra ventanas, desconecta MQTT, incluso ante
   Ctrl-C o excepción.

--- AÑADIDO EN LA v2 ---

8. ESPERA EL ENLACE ANTES DE EMITIR. `connect_async()` devuelve al instante pero
   el enlace tarda ~2 s (medido: 2058 ms), y con QoS 0 lo publicado mientras
   tanto se pierde sin dejar rastro. Llama a `PosePublisher.wait_connected()`
   antes de entrar en el bucle. Sin esto se pierden los primeros ~40 mensajes,
   justo cuando el operador mira si funciona.

9. EL HUD NECESITA TRES CONTADORES, no uno:
       message_count  -> salió a la red
       dropped_count  -> no quise enviarlo (excedía la tasa)
       failed_count   -> NO PUDE enviarlo (sin enlace)
   Sin el tercero, el HUD puede informar 20 Hz mientras al broker no llega nada.
   Ese bug ya ocurrió en el P2 y está corregido en el publisher.

10. CADENCIA DEL BUCLE por fecha límite, no por `sleep(periodo)`, y saltando las
    ranuras perdidas en vez de dispararlas seguidas para recuperar:
        next_tick = max(next_tick + periodo, time.perf_counter())
        time.sleep(max(0.0, next_tick - time.perf_counter()))
    Sin el `max()`, tras una pausa del sistema el bucle emite una ráfaga que el
    limitador descarta y la tasa efectiva baja sin motivo real. Patrón ya
    validado en `publisher._demo` (60/60 mensajes, 0 descartes, 20 Hz).

11. LAS BARRAS DEL HUD NO REQUIEREN CÁLCULO. `AxisClamp` ya expone `lower`,
    `upper`, `fraction` y `clamped`. En el brazo, `lower`/`upper` son los
    interpolados PARA EL GIRO ACTUAL, así que la barra se estrecha y ensancha en
    vivo — que es el comportamiento correcto.

12. «Reutilizar el último valor válido» se consigue OMITIENDO el campo del JSON:
    el visor conserva el último valor de los campos ausentes, y
    `PosePublisher.publish()` ya omite los `None`. main.py no necesita mantener
    estado para esto.

13. Mide los fps y los Hz con `perf_counter`, no con `monotonic` (§0.7).

Actualiza el README con: instalación, configuración de Mosquitto, cómo lanzar el
visor (servidor HTTP local — NUNCA con file://), cómo lanzar el pipeline, y un
diagrama ASCII del flujo de datos.

Commit: "feat: pipeline de teleoperacion integrado"
```

---

## PROMPT 8 — Entrenamiento con FER-2013 ⬜

```
Necesito reentrenar el clasificador con un dataset público, porque el actual
tiene un problema metodológico que quiero corregir y documentar, no ocultar.

PROBLEMA: legacy/Creation_of_dataset.py graba 2000 frames CONSECUTIVOS de la
misma persona sosteniendo una emoción en una sola sesión. Luego
legacy/Training Emotion Recognition.py hace train_test_split ALEATORIO sobre
esas imágenes casi idénticas, así que frames vecinos quedan a ambos lados de la
partición. Es fuga de datos: la accuracy resultante mide memorización, no
generalización. Además el split no fija random_state.

SOLUCIÓN: entrenar con FER-2013 y reservar el dataset propio como conjunto de
prueba en condiciones de operación.

Crea `tools/train_fer2013.py`:

1. Descarga/carga FER-2013 desde HuggingFace datasets. 35,887 imágenes de 48×48
   en escala de grises, 7 clases. Respeta la partición oficial train/test
   (28,709 / 7,178); no rehagas el split.

2. Filtra a las 5 clases del proyecto, descartando 'disgust' y 'fear'. Mapea a
   las etiquetas en español manteniendo este orden exacto y guárdalo en un JSON
   de metadatos junto al modelo:
   ['feliz', 'triste', 'sorprendido', 'neutral', 'enojado']

3. FER-2013 tiene fuerte desbalance de clases. Aplica class_weight en el fit y
   repórtalo. No hagas oversampling ciego.

4. Arquitectura: parte de la CNN existente (3 bloques Conv+MaxPool, Dense 128,
   Dropout 0.5). Añade aumentación (flip horizontal, rotación ±10°, zoom ±10 %)
   y BatchNormalization. EarlyStopping sobre val_loss y ReduceLROnPlateau.
   No cambies el tamaño de entrada: 48×48×1.

5. Fija semillas (numpy, tensorflow, random) y guarda el historial como CSV.

6. Guarda `emotion_model_fer2013.keras` + `emotion_model_fer2013.meta.json`
   con orden de clases, fecha, accuracy y hash del dataset.

IMPORTANTE — calibración de expectativas: FER-2013 tiene precisión a nivel
humano de 65±5 % y los mejores trabajos publicados llegan a 75±2 %. Si obtengo
60–70 % es un resultado normal y publicable. Si algo diera más de 90 %,
sospecha de un error en la partición y avísame; no lo reportes como éxito.

--- AÑADIDO EN LA v2 ---

7. El modelo nuevo debe cargar con Keras 3 igual que el actual. Verifica al
   guardarlo: `keras.saving.load_model(ruta)` y `model(x, training=False)` sobre
   `(1,48,48,1)` debe dar forma `(1,5)`.

8. El JSON de metadatos es el que leerá `KerasBackend` (P6, punto 1). Que el
   orden de clases coincida con `config.EMOTION_LABELS` o documenta por qué no.

Commit: "feat: entrenamiento con FER-2013 y particion oficial"
```

---

## PROMPT 9 — Evaluación comparativa ⬜

```
Crea `tools/eval_emotion.py`. Debe comparar, sobre las MISMAS métricas:

  A. Modelo legacy, split aleatorio de mi dataset propio (reproduce el número
     original, el que tiene fuga de datos)
  B. Modelo legacy, split por bloques temporales de mi dataset propio
     (ordena por índice de frame dentro de cada clase y asigna bloques
     contiguos — esta es la cifra honesta del modelo original)
  C. Modelo FER-2013, sobre el conjunto de prueba oficial de FER-2013
  D. Modelo FER-2013, sobre mi dataset propio COMPLETO — mide el domain gap
  E. DeepFace, sobre mi dataset propio (línea base de librería)
  F. HSEmotion, sobre mi dataset propio (línea base de librería)

Para cada una: accuracy, precision/recall/F1 por clase, macro-F1, y matriz de
confusión normalizada.

Salidas:
- `reports/matriz_confusion_{config}.png`, matplotlib, 300 dpi, etiquetas en
  español.
- `reports/comparativa_emocion.csv`.
- `reports/evaluacion_emocion.md` con la tabla y una nota metodológica. La
  diferencia entre A y B es exactamente la magnitud de la fuga de datos; quiero
  ese número explícito.

Deja preparada la estructura para una configuración G (dataset con adultos
mayores) pero NO la implementes: aún no tengo acceso.

E y F deben omitirse limpiamente si las librerías no están instaladas.

Fija semillas en todo. No reentrenes nada aquí.

--- AÑADIDO EN LA v2 ---

7. BUG 16 (nuevo): `legacy/Training Emotion Recognition.py:9` tiene la ruta del
   dataset escrita a fuego y apuntando a OTRA MÁQUINA
   (`C:\Users\harol\PycharmProjects\Tesis\emotions_dataset`; el usuario aquí es
   `hlrm`). **El script legacy no corre tal cual**, y las configuraciones A y B
   dependen de él. Parametriza la ruta antes de intentar esa comparación.

8. Usa el cascade de OpenCV, no el de `legacy/` — si no, A y B no reproducen el
   número original (ver P6 punto 9).

9. Toda medida de tiempo con `perf_counter` (§0.7).

Commit: "test: evaluacion comparativa de reconocimiento de emociones"
```

**Rangos esperados**

| Config | Rango esperado | Si sale muy distinto |
|---|---|---|
| A (legacy, aleatorio) | 90–99 % | Normal: es la cifra inflada por fuga |
| B (legacy, bloques) | 40–70 % | La caída respecto a A **es** el resultado a reportar |
| C (FER-2013 oficial) | 60–72 % | >90 % ⇒ error de partición, revisar |
| D (FER-2013 → propio) | 45–70 % | Mide domain gap; menor que C es esperable |
| E, F (librerías) | 50–75 % | Línea base de referencia |

---

## PROMPT 10 — Panel de control del operador ⬜

```
Crea `telmeow_teleop/control_panel.py`. Elige entre Tkinter o una página HTML
servida localmente con websockets; si eliges HTML, sírvela en un puerto distinto
al del visor 3D (p. ej. 8001) para tener ambas abiertas a la vez.

## 1. Panel de conexión MQTT
- Campos: host, puerto (1883), topic (`telmeow/teleop/pose`)
- Botón "Conectar" / "Desconectar" con indicador de estado, reutilizando
  `PosePublisher`
- Contador de mensajes publicados y tasa efectiva (Hz)

## 2. Panel de cámara / visión
- Botón "Iniciar captura" / "Detener captura"
- Vista previa en vivo con overlay de landmarks
- Selector de cámara si hay más de una
- Botón "Calibrar neutral"

## 3. Panel de historia (libro álbum)
- Selector de la historia: lista los PDFs de una carpeta configurable, con
  nombre y número de páginas (pypdf solo para metadata).
- Al seleccionar, publica en `telmeow/teleop/story` un JSON
  `{"story": "nombre.pdf", "page": 1}`.
- Campo de página actual y botones "◀ Anterior" / "Siguiente ▶".
- Al cambiar de página, publica DOS cosas desde el MISMO evento:
  a) en `telmeow/teleop/page`: `{"page": N, "total": M}`
  b) en `telmeow/teleop/pose`: el campo `gesture` con `page_next`/`page_prev`

## 4. Panel de emoción manual
- Los 6 botones: neutral, feliz, triste, sorprendido, enojado, concentrado.
- Botón "Volver a automático" → `clear_manual()`
- Indicador de modo activo

## 5. Barra de estado global
- Latencia MQTT, FPS de cámara, estado de conexión

## Requisitos no funcionales
- El panel es una capa fina sobre `telmeow_teleop`: cerrarlo NO debe impedir que
  `main.py` siga funcionando por CLI.
- Cierre limpio: desconectar MQTT y liberar cámara.

--- AÑADIDO EN LA v2 ---

- Espera el enlace con `wait_connected()` antes de emitir (ver P7 punto 8).
- Los mensajes con `gesture` o `page` NUNCA se descartan por límite de tasa: el
  publisher ya lo garantiza, así que la sincronía entre gesto y página está
  asegurada por construcción.
- Los tres topics ya están en `config.MQTTConfig` (`topic_pose`, `topic_page`,
  `topic_story`), con el consumidor de cada uno documentado.
- La barra de estado debe mostrar también `failed_count` (ver P7 punto 9).

Commit: "feat: panel de control del operador"
```

---

## PROMPT 11 — Servicio de pantalla externa (Raspberry Pi) ⬜

```
Crea `raspberry/story_display.py`, un servicio que corre EN LA RASPBERRY PI y
controla la pantalla externa de 15.6" conectada por HDMI.

Reemplaza el flujo actual en el que el operador usaba Firefox por escritorio
remoto y cambiaba de página con las flechas. El motivo: si el robot debe hacer
un gesto de cabeza sincronizado con el cambio de página, ese gesto tiene que
dispararse desde el mismo evento que cambia la página.

1. Suscripción MQTT a `telmeow/teleop/story` y `telmeow/teleop/page`.

2. Al recibir una historia nueva: cargar el PDF con pdf2image y PRE-RENDERIZAR
   todas sus páginas a imágenes en memoria (o caché en disco), a 1920×1080.
   Renderizar bajo demanda introduciría un retardo visible justo cuando el robot
   hace el gesto. Muestra progreso mientras pre-renderiza.

3. Al recibir un cambio de página: mostrar la imagen ya cacheada.

4. Pantalla completa, sin decoraciones, fondo negro. pygame o Tkinter, lo que
   resulte más ligero en la RPi 5. Escala manteniendo proporción y centrada.

5. Tolerancia a fallos: si el broker se cae, mantener la última página en
   pantalla y reconectar en segundo plano. NUNCA mostrar una pantalla de error
   ni cerrar la aplicación durante una sesión con niños presentes — es el
   requisito de robustez más importante de este componente.

6. CLI: `--broker`, `--stories-dir`, `--display`, `--windowed`.

7. Modo de prueba local: carga un PDF de ejemplo y permite cambiar de página con
   las flechas, sin MQTT.

Documenta en el README cómo lanzarlo al arranque (systemd) y las dependencias
del sistema (poppler-utils).

--- AÑADIDO EN LA v2 ---

- El gesto de cabeza NO lo dispara este servicio: lo dispara el visor al recibir
  `gesture` en el topic de pose. Este servicio solo pinta la página. La
  sincronía la garantiza el panel (P10) publicando ambos desde el mismo evento.
- El reintento de conexión de paho ya hace lo del punto 5 si se usa
  `connect_async` + `reconnect_delay_set`, como en `publisher.py`.

Commit: "feat: servicio de pantalla externa con prerenderizado"
```

---

# PARTE 3 — Limitaciones y decisiones

## 3.1 Pendientes que dependen de terceros

| Tema | Estado | Impacto si no llega |
|---|---|---|
| **FaceDatabase** (Minear & Park, UT Dallas) | Correo enviado, sin respuesta. La Dra. Park se retiró en mayo 2025. | Sin validación por grupo etario. Se declara como limitación. El pipeline funciona con FER-2013. |
| **FACES** (Ebner, Max Planck) | No solicitado. Requiere registro + Platform Release Agreement. | Alternativa. Cubre solo 4 de las 5 clases (no tiene sorpresa). |
| **TFED / Tsinghua** | No solicitado. | Alternativa que sí cubre las 5 clases y aporta diversidad étnica. |

**Decisión vigente:** se trabaja con FER-2013, pública y libre pero **no
especializada en rostros de adultos mayores**. Debe declararse (§3.3).

## 3.2 Decisiones abiertas

Quedan **dos**. Las otras dos de la v1 se cerraron (§0.6).

1. **Ganancia de mapeo operador → robot.** Implementada y configurable
   (`HeadConfig`), con dos valores para el tilt porque las asimetrías del robot
   y del estimador se suman. **Falta fijar los números definitivos**, y el
   criterio no es el que parecía:

   **El límite real no es el cuello del operador, es la PANTALLA.** El
   teleoperador tiene que seguir viendo lo que ve el robot en su monitor. Si
   para girar el robot tuviera que girar mucho la cabeza, dejaría de ver la
   pantalla — justo cuando más necesita mirarla. Sin gafas de realidad virtual,
   **el recorrido útil del operador lo acota su campo de visión sobre el
   monitor**, no su anatomía ni el rango mecánico del robot.

   Consecuencias:
   - La ganancia **tiene que ser mayor que 1**, y probablemente bastante:
     amplifica un gesto pequeño y cómodo hasta el recorrido del robot.
   - No hace falta cubrir los ±90° mecánicos del pan. El operador estima que con
     **±50° de robot** basta para la mediación de lectura.
   - Falta medir a qué ángulo de cabeza pierde la pantalla de vista. Con ese
     dato: `ganancia = 50 / ese ángulo`.

   Es una limitación de **diseño de interacción**, no de visión, y merece
   declararse como tal en el documento: el ancho del campo visual del operador
   sobre su monitor acota el mapeo, y la ganancia es el mecanismo que lo
   compensa. Precio: amplificar multiplica también el temblor (0.19° × 2.5 =
   0.48°, todavía pequeño) y hace el control más sensible.

2. **Cómo tratar la latencia declarada.** El «< 150 ms» del documento no está
   medido: el indicador del visor mide el intervalo entre mensajes, que no puede
   variar con la latencia (§0.4). Dos salidas:
   - **Medirla:** `PosePublisher(include_timestamp=True)` ya añade un campo `t`
     con el tiempo Unix en ms; falta que el visor calcule `Date.now() - o.t`.
     Salvedad: publisher y navegador solo comparten reloj en la misma máquina.
   - **O reetiquetar** el indicador como «periodo entre comandos» y declarar en
     limitaciones que la latencia extremo a extremo no se midió.

3. **Qué hacer si el gesto de página coincide con movimiento del operador.**
   Implementado un handoff (`liveTiltPanBuffer`: el gesto toma control temporal
   de pan/tilt y devuelve suavemente a la última posición en vivo, no a home),
   pero **no validado con una persona real**.

## 3.3 Limitaciones a declarar en el documento

1. **Observabilidad del giro del brazo.** La rotación axial del húmero no es
   determinable de forma robusta desde landmarks de una sola cámara RGB. Su
   incertidumbre es mayor que la del balanceo.

2. **Ausencia de ground truth angular.** No hay forma de validar que los ángulos
   estimados correspondan a los reales sin captura de movimiento. Lo defendible
   es reportar repetibilidad, no exactitud absoluta.

3. **Sesgo etario del dataset de emociones.** FER-2013 subrepresenta a personas
   mayores de 54 años, y la literatura documenta disparidades especialmente en
   neutral, tristeza y enojo — tres de las cinco clases. Referencias:
   Goodfellow et al. (2013); Khaireddin & Chen (2021); revisión sistemática
   arXiv:2502.02618 (2025).

4. **Dataset propio de un solo sujeto.** 2000 imágenes por emoción de una sola
   persona en sesiones controladas. Validación en condiciones de operación, no
   evidencia de generalización.

5. **Fuga de datos en el entrenamiento original.** La diferencia entre A y B del
   P9 cuantifica cuánto de la accuracy original era memorización.

6. **Gesto de página estilizado.** El centro de la pantalla externa requeriría
   ~46.5° de inclinación; el límite mecánico es −39°. **El gesto usa −30°**, con
   9° de margen, y alcanza la franja superior del panel. Se describe como «gesto
   de atención dirigido hacia la pantalla», no como «el robot enfoca el
   contenido».

7. **Validación sin usuarios finales.** No hay pruebas con niños ni con adultos
   mayores reales. La validación es técnica, no de experiencia de usuario.

8. **Las tablas de límites no cubren combinaciones simultáneas.** El barrido BVH
   se hizo con los demás DOF en home. Por eso existe la segunda capa de recorte
   en el visor — ver §5, que lo convierte en un resultado en vez de un problema.

---

# PARTE 4 — Verificación end-to-end

## 4.0 Prerrequisitos (no estaban en la v1)

1. **CPython 3.12 x64.** TensorFlow publica ruedas hasta cp313 y MediaPipe hasta
   cp312: 3.13 y 3.14 **rompen la instalación**. ✅ resuelto.

   ```
   py -V:3.12 -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
   ```

   Entorno verificado el 2026-08-30 — los pines no son elecciones estéticas:

   | Paquete | Versión | Comprobado |
   |---|---|---|
   | opencv-contrib-python | 4.10.0 | importa; expone `cv2.data.haarcascades` |
   | mediapipe | 0.10.14 | `mp.solutions.face_mesh` / `.pose` existen y se instancian |
   | tensorflow | 2.16.1 | carga el `.keras` y ejecuta `model(x, training=False)` |
   | keras | 3.15.1 | lo resuelve TensorFlow; lee el modelo guardado con 3.3.3 |
   | numpy | 1.26.4 | exigido por TensorFlow 2.16 (`>=1.26,<2.0`) |
   | paho-mqtt | 2.1.0 | verificado contra Mosquitto |

   `opencv-contrib-python` y no `opencv-python`: MediaPipe ya depende del
   primero, y declarar ambos instala dos distribuciones del mismo módulo `cv2`
   con sombreado según el orden de instalación.

2. **Mosquitto instalado** — ✅ resuelto (2.1.2). Dos avisos:
   - **No queda en el PATH.** Invocar por ruta completa:
     `& "C:\Program Files\mosquitto\mosquitto.exe" -c mosquitto.conf -v`
   - **Instalarlo no basta:** ver 4.1.

3. **UN SOLO BROKER en el 1883.** ⬜ pendiente, requiere administrador.

4. **La cámara debe entregar MJPG.** Por USB, muchas webcams solo dan 30 fps a
   1280x720 si comprimen. Sin comprimir (YUY2, el formato por defecto de
   DirectShow) la cámara baja a **10 fps sin avisar de nada** — medido
   exactamente eso en la primera sesión de prueba. Ya se pide MJPG antes de
   fijar la resolución (`TELMEOW_CAMERA_FOURCC`).

   Importa porque el pipeline publica a 20 Hz: a 10 fps la mitad de los mensajes
   serían repeticiones del mismo dato, y la teleoperación se vería a tirones sin
   que ningún indicador lo delatara.

5. **El índice de cámara CAMBIA entre sesiones.** Windows enumera por orden de
   aparición del dispositivo: al reconectar o reiniciar, la integrada y la
   externa se intercambian. Conviene ejecutar `tools/list_cameras.py` al
   empezar. El PROMPT 10 debería seleccionar por **nombre**, no por número.

6. **Avisos de arranque que NO hay que arreglar.** Todos vienen de dentro de
   MediaPipe o de protobuf, no del código del proyecto. MediaPipe además
   arrastra TensorFlow entero (2289 módulos) al importarse, de ahí el arranque
   lento; tenerlo en cuenta para la Raspberry Pi del PROMPT 11.

   | Aviso | Qué es | Acción |
   |---|---|---|
   | `oneDNN custom operations are on` | Informa de que las optimizaciones **ya están activas** | ninguna |
   | `XNNPACK delegate for CPU` | MediaPipe usando la ruta rápida de CPU | ninguna, es buena señal |
   | `Feedback manager requires…` | Interno del grafo de MediaPipe | no es corregible desde fuera |
   | `SymbolDatabase.GetPrototype() is deprecated` | Código generado de MediaPipe contra protobuf | **no tocar**: subir a protobuf 5.x rompería MediaPipe, y el pin `protobuf==4.25.3` ya protege |

## 4.1 La trampa del doble broker

El instalador registra un **servicio con arranque automático** que escucha solo
en `127.0.0.1:1883` y **no abre listener WebSocket**. Si además se lanza la
instancia manual del repositorio, Windows permite que convivan —el servicio
ocupa la dirección de loopback y la manual la comodín— y quedan **dos brokers
sin conexión entre ellos**: Python habla con uno y el visor con el otro.

Es el fallo más difícil de diagnosticar del proyecto porque **todo informa de
éxito**: Mosquitto abre los dos puertos, el visor dice «Conectado», el publisher
informa de 500 mensajes enviados y 0 sin entregar… y el visor marca 0.0 Hz.

```
netstat -ano | findstr ":1883"
```

**Dos PID distintos = dos brokers.** Solución, en una terminal **como
administrador**:

```
Stop-Service mosquitto; Set-Service mosquitto -StartupType Manual
```

Alternativa sin administrador: dejar el servicio en el 1883 y levantar la
instancia del repositorio en otro puerto TCP, apuntando el pipeline con
`TELMEOW_MQTT_PORT`.

## 4.2 Secuencia

1. Comprobar que hay un solo broker (4.1).
2. `& "C:\Program Files\mosquitto\mosquitto.exe" -c mosquitto.conf -v` — deben
   aparecer **cuatro** líneas `Opening ... listen socket`, dos por puerto.
3. `python -m telmeow_teleop.publisher` — pose oscilante, sin cámara.
4. Servir el visor: `python -m http.server 8000` desde `04_TelmeowViewer`, abrir
   `http://localhost:8000/telmeow_viewer.html`. **NUNCA con `file://`** — los
   módulos ES se bloquean por CORS.
5. Conectar el visor a `ws://localhost:9001`, topic `telmeow/teleop/pose`. El
   brazo debe oscilar. ✅ **verificado**
6. `python tools/mqtt_monitor.py` para ver el JSON crudo.
7. `python -m telmeow_teleop.main` con la cámara. *(tras P7)*
8. `python -m telmeow_teleop.control_panel`. *(tras P10)*
9. En la Raspberry: `python raspberry/story_display.py --windowed`. *(tras P11)*

Sin Mosquitto disponible: `python tools/dev_broker.py` levanta un broker
equivalente en Python puro, sin privilegios de administrador.

---

# PARTE 5 — Preguntas probables del jurado

- **«¿Cómo valida que los ángulos estimados corresponden a los reales?»**
  No hay ground truth con una sola cámara. Reportar repetibilidad y declarar la
  ausencia de validación absoluta como limitación.

- **«¿Por qué el giro es menos confiable que el balanceo?»**
  Porque la rotación axial del húmero es casi no observable desde landmarks
  monoculares. Anticiparlo en el texto.

- **«¿Su accuracy de emoción generaliza a otras personas?»**
  El modelo se entrena con FER-2013 (multi-sujeto), no con el dataset propio.
  Pero FER-2013 subrepresenta adultos mayores, que son el operador destacado.
  Declararlo.

- **«¿Por qué 6 expresiones si el clasificador emite 5?»**
  CONCENTRADO es un estado manual del operador. El estado por defecto es
  NEUTRAL. Decisión de diseño documentada.

- **«¿Por qué el robot no mira al centro de la pantalla al cambiar de página?»**
  Límite mecánico de tilt (−39°) vs. ángulo requerido (~46.5°). El gesto usa
  −30°, es estilizado y alcanza la franja superior del panel.

- **«¿Cómo sabe que su recorte de poses funciona?»** *(nueva)*
  El visor vuelve a recortar toda pose recibida y enciende el indicador `t-col`
  cuando tiene que hacerlo. Es decir: **`t-col` se enciende exactamente cuando el
  `limits.py` de Python dejó pasar una pose inválida.** Eso da una métrica
  medible sin instrumentación adicional — el porcentaje de poses que la segunda
  capa tuvo que corregir. Y su interpretación honesta: como las tablas se
  midieron con los demás DOF en home, un `t-col` ocasional **es** la limitación
  declarada hecha visible, no un fallo.

- **«¿Por qué eligió el filtro One Euro y no una media móvil?»** *(nueva)*
  Porque un pasabajos fijo tiene que elegir entre suavizar el jitter e
  introducir retardo. Medido sobre este proyecto, con un escalón con ruido a
  100 Hz: el One Euro reduce el ruido 11.2× con 6 muestras de retardo, mientras
  que un EMA con reducción comparable (9.7×) tarda 36. Gana en los dos ejes a la
  vez.

- **«¿Cómo midió esos 150 ms de latencia?»** *(nueva — hay que resolverla antes)*
  Hoy la respuesta honesta es que no se midieron: el indicador mide el intervalo
  entre mensajes. Ver §3.2 punto 2.

---

# PARTE 6 — API ya construida, para los prompts pendientes

## `config.py`

```python
from telmeow_teleop.config import AppConfig, EMOTION_LABELS

cfg = AppConfig.from_env()          # o config.load(argv) para defaults<env<CLI
cfg.mqtt.host / .port / .ws_port / .topic_pose / .topic_page / .topic_story
cfg.rates.capture_fps / .publish_hz / .emotion_hz   (+ .*_period)
cfg.camera.index / .width / .height / .flip / .backend
cfg.paths.emotion_model                # absoluta
cfg.paths.face_cascade                 # None = el de cv2.data.haarcascades
EMOTION_LABELS  # ("feliz","triste","sorprendido","neutral","enojado")
```

Prefijo de entorno `TELMEOW_`. Todo sobrescribible por entorno y CLI.

## `limits.py`

```python
from telmeow_teleop.limits import clamp_arm, clamp_head, Side, arm_balanceo_range

a = clamp_arm(balanceo, giro, "izq")   # ArmClamp
a.balanceo.value / .lower / .upper / .fraction / .clamped
a.giro.value ; a.as_tuple ; a.clamped

h = clamp_head(pan, tilt)              # HeadClamp
h.pan.value ; h.tilt.value ; h.as_tuple ; h.clamped
```

Lanzan `ValueError` ante NaN/infinito. `Side.parse` acepta L/R, left/right,
izq/der.

## `filters.py`

```python
from telmeow_teleop.filters import OneEuroFilter, EMAFilter, ClassHysteresis

f = OneEuroFilter(min_cutoff=0.5, beta=0.5)
f(valor, timestamp)          # timestamp de perf_counter; opcional
f.degenerate_dt_count        # cuenta los dt <= 0 sustituidos
f.reset()

h = ClassHysteresis(n_frames=5, min_confidence=0.6, initial="neutral")
h.update(etiqueta, confianza) -> etiqueta estabilizada
h.current ; h.window
```

## `head_tracker.py`

```python
from telmeow_teleop.head_tracker import HeadTracker, estimate_head_pose

t = HeadTracker(gain_pan=1.0, gain_tilt=1.0, invert_pan=False, invert_tilt=False)
t.process(frame_rgb) -> (pan, tilt) | None   # None = sin rostro o pose inválida
t.map_to_robot(yaw, pitch, timestamp=None)   # la cadena, sin cámara: testeable
t.set_neutral() / t.clear_neutral()
t.last_clamp        # HeadClamp, con lower/upper/fraction/clamped para el HUD
t.raw_angles        # ángulos crudos de solvePnP, antes de todo
t.frames_sin_rostro ; t.degenerate_dt_count
t.close()           # o usarlo como gestor de contexto

estimate_head_pose(puntos_2d, (ancho, alto)) -> (yaw, pitch, roll) | None
```

## `publisher.py`

```python
from telmeow_teleop.publisher import PosePublisher

with PosePublisher(cfg.mqtt, cfg.rates) as pub:
    pub.wait_connected(timeout=5.0)    # IMPRESCINDIBLE antes de emitir
    pub.publish(pan=..., tilt=..., armL=..., armLtw=..., armR=..., armRtw=...,
                emo="feliz", gesture=None, page=None)   # -> bool
pub.message_count / .dropped_count / .failed_count / .effective_hz / .connected
```

Los `None` se omiten del JSON. Los mensajes con `gesture` o `page` nunca se
descartan por tasa.

---

# PARTE 7 — Registro de hallazgos

Rastro de auditoría de lo que se encontró al ejecutar los PROMPT 1 al 3. Sus
consecuencias ya están repartidas por el resto del documento; esto conserva la
**evidencia** —archivo, línea, medición— para poder citarla o revisarla.

## 7.1 Sobre el contrato con el visor

| # | Hallazgo | Evidencia |
|---|---|---|
| H1 | El visor **sí** consume `gesture` y `page` dentro del payload de pose, y acepta `emotion` como alias de `emo` | `telmeow_viewer.html:1004-1009` |
| H2 | **Todos** los campos son opcionales; el visor conserva el último valor de los ausentes | `telmeow_viewer.html:993-1009`, guardas `typeof o.X === 'number'` |
| H5 | El visor **ya** clampa lo recibido por MQTT, con bisección sobre el segmento completo | `clampPoseExternal()`, línea 1012 |
| H4 | El gesto de página usa **−30°**, no −35° | `PAGE_GESTURE = {panSweep:35, tiltDown:-30, ...}`, línea 849 |

Probablemente el «−35» de la v1 se confundió con `panSweep: 35`, que sí vale 35.

## 7.2 Sobre la medición del tiempo (H11)

El publisher usaba `time.monotonic()`. En Windows su resolución es ~15.625 ms,
comparable al periodo de 50 ms que debía medir: los intervalos salían
cuantizados y a veces se leían como 31.25 ms, por debajo del umbral.

| Reloj | Intervalo mín. | Mediana | Descartes | Tasa |
|---|---|---|---|---|
| `time.monotonic()` | 46.0 ms | 47.0 ms | 9/60 | 17.0 Hz |
| `time.perf_counter()` | 50.1 ms | 50.4 ms | 0/60 | 20.0 Hz |

Conviene dejar escrito lo que **no** era la causa, porque son las dos hipótesis
plausibles que hubo que descartar midiendo: no era la precisión de
`time.sleep()` (Python 3.12 usa temporizadores de alta resolución; en esta
máquina acierta con ±0.5 ms), ni era el cliente MQTT (un cliente falso sin red
dio exactamente el mismo número de descartes).

## 7.3 Sobre el transporte (H12, H13)

**H12 — `publish()` informaba de envíos que nunca salieron.** El publisher
declaraba 41 mensajes enviados y al suscriptor llegaron 9, los últimos 9. Causa:
se ignoraba el código de retorno de paho, que devuelve `MQTT_ERR_NO_CONN` cuando
no hay enlace y con QoS 0 descarta el mensaje sin encolarlo. Un fallo que se
anuncia como éxito.

**H13 — el enlace tarda ~2 s.** Medido: 2058 ms con `connect_async()`. Con la
espera de `wait_connected()`, la verificación pasó de 9 recibidos de 41 a
**40 de 40**.

## 7.4 Sobre el broker (H7, H15)

**H15 — el servicio y la instancia manual se reparten el 1883.** Detalle
completo en §4.1. Medido: 500 mensajes publicados, cero recibidos, con todos los
indicadores en verde. Apuntando el publisher a la IP de red de la máquina —que
solo puede servir la instancia comodín— el brazo oscila de inmediato.

**H7 — Mosquitto no queda en el PATH.** `mosquitto -c mosquitto.conf -v` falla
con «is not recognized». Hay que usar la ruta completa.

## 7.5 Sobre el preprocesamiento de emoción (H16)

Había **dos** cascades Haar distintos:

| Archivo | Tamaño | Lo usa |
|---|---|---|
| Copia que estaba en la raíz del repositorio | 963.441 bytes | **nadie** |
| `cv2.data.haarcascades` (OpenCV 4.10) | 930.127 bytes | los tres scripts legacy |

En el P1 puse la copia del repositorio como predeterminada, razonando que al
estar versionada en git era lo reproducible. **Era el criterio equivocado:** la
reproducibilidad que importa aquí es respecto al entrenamiento del modelo, no
respecto al control de versiones. Corregido, y el archivo huérfano movido a
`legacy/`.

## 7.6 Correcciones pendientes en la skill `telmeow-viewer`

La skill es la fuente de verdad del visor, y tiene tres cosas desfasadas:

| # | Qué corregir |
|---|---|
| H1 | Su sección «MQTT — contrato único» lista solo los 6 ángulos y `emo`. Faltan `gesture`, `page` y el alias `emotion`. |
| H14 | Los IDs de los sliders son `s-arL`, `s-arLtw`, `s-arR`, `s-arRtw` — **sin la `m`**. La skill documenta `s-armL`, `s-armR`, etc. Los indicadores de valor son `v-arL`, `v-arR`, … |
| H9 | Dice «cdnjs FALLA». Aplica solo a los **addons ES-module de three.js** (STLLoader, OrbitControls), que cdnjs sirve con MIME incorrecto. El visor carga `mqtt.min.js` desde cdnjs (línea 1033) y funciona: es un script clásico, no un módulo. |

H14 no afecta al pipeline de Python —esos IDs son internos del visor— pero
cualquier automatización o prueba que los use fallará con los nombres
documentados.

## 7.7 Índice de hallazgos y dónde vive cada uno ahora

| # | Tema | Recogido en |
|---|---|---|
| H1 | `gesture` y `page` en el contrato | §0.4, §7.1 |
| H2 | Campos opcionales | §0.4, P7 punto 12 |
| H3 | El proxy de latencia no puede variar con la latencia | §0.4, §3.2, Parte 5 |
| H4 | Gesto de página a −30° | §0.6, §3.3.6 |
| H5 | El visor ya clampa lo recibido | §0.6, Parte 5 |
| H6 | Barridos retirados, BVH activo | §0.6 |
| H7 | Mosquitto: prerrequisito, y no queda en el PATH | §4.0, §7.4 |
| H8 | CPython 3.12 | §4.0 |
| H9 | Alcance de la nota sobre cdnjs | §7.6 |
| H10 | Manda la tabla 2D | §0.5 |
| H11 | `perf_counter`, no `monotonic` | §0.7, §7.2 |
| H12 | Tres contadores en el HUD | P7 punto 9, §7.3 |
| H13 | Esperar el enlace antes de emitir | P7 punto 8, §7.3 |
| H14 | IDs de sliders del visor | §7.6 |
| H15 | Doble broker en el 1883 | §4.1, §7.4 |
| H16 | El cascade sale de OpenCV | §0.6, P6 punto 9, §7.5 |
