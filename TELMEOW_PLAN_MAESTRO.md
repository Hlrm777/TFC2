# Telmeow — Plan maestro de implementación (v2)

Repositorio: `Hlrm777/TFC2` · Código: `D:\02_Tesis\01_Principales\02_Codigo`
Viewer: `telmeow_viewer/telmeow_viewer.html`, dentro del repositorio desde el
2026-09-06 (antes vivía suelto en `..\04_TelmeowViewer`, sin control de versiones)

**Versión 2 · 2026-08-30, revisada el 2026-09-05.** Reemplaza a la v1 y absorbe
los documentos de hallazgos y de anexo que existieron entre medias: **este
archivo es la única fuente vigente**. Incorpora los 21 hallazgos de la ejecución
de los PROMPT 1 al 4 — cierra decisiones que ya estaban resueltas en el código,
corrige cifras equivocadas, y añade a cada prompt pendiente lo que hay que tener
en cuenta ahora que el paquete existe.

La revisión del 2026-09-05 añade lo que salió del PROMPT 4: las cinco
validaciones que le faltan (**P4.1 a P4.5**), los hallazgos **H17-H21**, y dos
limitaciones nuevas para el documento (§3.3.9 y §3.3.10) — el techo de captura de
~15 fps y el efecto de la ganancia sobre el temblor.

**La revisión del 2026-09-06 cierra el PROMPT 4 entero**, con el operador
delante: las cinco validaciones P4.1 a P4.5, las ganancias medidas y fijadas
(`1.49 / 1.59 / 1.80`), y los hallazgos **H22 a H27**.

Tres cosas de esa jornada cambian lo que este documento decía:

- **H22** — el método `mesh` devolvía el **tilt invertido**: el operador
  levantaba la cabeza y el robot la bajaba. Corregido en la raíz.
- **H26** — `limites.csv` queda **descartada entera**, no releída. Las ganancias
  provisionales que salían de ella se retiran.
- **H27** — **el techo de ~15 fps no existe**: lo que limita la captura es la
  luz, vía exposición. La misma cámara mide 30 fps con buena iluminación y 10
  con poca. La limitación de tasa se retira y pasa a ser una condición de
  operación (§4.0.4).

**La segunda sesión del 2026-09-06 escribe el PROMPT 5** y añade dos hallazgos
más, los dos sobre los brazos:

- **H28** — `giro` y `balanceo` eran nombres provisionales, y el enunciado del
  P5 los interpretó mal: **el robot no tiene rotación axial**. Sus dos GDL de
  hombro son **abducción/aducción** y **flexión/extensión**. Vocabulario
  renombrado en Python y en el visor, y **§3.3.1 reescrita** — declaraba una
  limitación sobre un grado de libertad inexistente.
- **H29** — ⚠️ **RETRACTADO el 2026-09-07.** Afirmé que el canal `tw` iba al
  revés de lo que dice el comentario del visor. La sesión con el visor delante
  lo desmontó y lo sustituye el **H30**.

**La sesión del 2026-09-07 (P5.1) corrige el propio P5**, y con él la geometría
que este documento daba por buena desde el principio:

- **H30** — **la postura de reposo del robot tiene los brazos EN CRUZ**, no
  colgando, y la dirección de reposo va **sobre el propio eje de la flexión**.
  De ahí salen tres cosas: los topes de `limits.py` son elevación respecto a la
  horizontal (el robot **no puede** bajar los brazos del todo), la
  **singularidad del cardán cae en la postura de reposo**, y «flexión positiva =
  adelante» **no es una propiedad del signo** sino que se invierte con la
  abducción. Obligó a reparametrizar el estimador entero.

**La sesión del 2026-09-13 arranca el P5.2** y, antes de grabar nada, el propio
HUD delató un fallo del instrumento:

- **H31** — el HUD afirmaba «vertical del tronco tomada de las caderas» con la
  visibilidad de cadera a **0.00**. La bandera se **enclavaba** en el primer
  frame bueno y no volvía a bajar, así que el HUD y la columna del CSV
  declaraban como observado un valor congelado de minutos antes. Medido: las
  caderas se vieron en el **9.8 %** de los frames y el instrumento declaró
  haberlas usado en el **86.5 %**.
- **H32** — la visibilidad de la mano se medía con el **mínimo de cuatro**
  landmarks y la del codo con **uno solo**, así que la comparación
  mano-frente-a-codo —la decisión que esa medición existe para resolver—
  estaba sesgada por el propio agregador. **Reabre la elección del punto de
  destino**: medido, el codo se ve *más* que la mano, al revés de lo previsto.
- **H33** — **las ganancias sí hacen falta, y solo en abducción.** El recorrido
  del hombro humano desborda el del robot y el recorte actuó en el 67-75 % de
  los frames. Es consecuencia del H30. En flexión el operador y el robot
  coinciden.

Los dos primeros son **fallos del instrumento, no del robot**, y los dos se
detectaron antes de grabar nada citable. Se añade además una decisión de diseño
que pidió el operador: **separar la estimación del comando**, para que perder un
brazo de cuadro no lo deje clavado a mitad de gesto. Ver el P5.

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
| **P4** — Head tracker | ✅ **hecho y calibrado** · P4.1 a P4.5 cerrados | `b3813a6`, `47ee616`, `784d144` |
| **P5** — Arm tracker | 🟡 **código hecho y verificado en el visor** · **P5.3 (mapeo por dirección) hecho** · falta la sesión con cámara | |
| P6 — Clasificador de emoción | ⬜ | |
| P8 — Entrenamiento FER-2013 | ⬜ | |
| P7 — Integración | ⬜ | |
| P10 — Panel del operador | ⬜ | |
| P11 — Pantalla externa (RPi) | ⬜ | |
| P9 — Evaluación comparativa | ⬜ | |

**734 pruebas en verde** (600 + 134 del P5.3). Entorno: CPython 3.12.1 x64, `.venv` en el repositorio.

Entre ellas, tres de tipo distinto que conviene conocer porque atrapan lo que las
demás no ven (`tests/test_estatico.py`): una pasa **pyflakes** sobre todo el
código —el modo demo del head tracker no lo ejecuta ninguna prueba, y así se
cazó un `deque` usado sin importar que reventaba al arrancar—, otra prohíbe
**tildes en los identificadores** de funciones y variables, y la tercera
comprueba que el cascade Haar configurado detecta un rostro de verdad.

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
│   ├── filters.py            ✅ One Euro, EMA, límite de vel., histéresis · stdlib
│   ├── camera.py             ✅ apertura de cámara · el orden y la exposición
│   ├── publisher.py          ✅ MQTT, JSON único, QoS 0
│   ├── head_tracker.py       ✅ Face Mesh + solvePnP
│   ├── arm_tracker.py        🟡 BlazePose · signos verificados · falta medir
│   ├── emotion_classifier.py ⬜ P6
│   ├── control_panel.py      ⬜ P10
│   └── main.py               ⬜ P7
├── raspberry/
│   └── story_display.py      ⬜ P11
├── telmeow_viewer/           ✅ el visor 3D y sus 40 STL · en git desde el 2026-09-06
│   ├── telmeow_viewer.html   la autoridad sobre el rig y los límites
│   └── *.stl, *.png          mallas de Fusion y texturas de los ojos
├── legacy/                   ✅ los scripts originales, intactos
│   ├── MQTT/
│   └── haarcascade_frontalface_default.xml   (huérfano, ver §0.6)
├── tools/
│   ├── mqtt_monitor.py       ✅
│   ├── dev_broker.py         ✅ broker sin privilegios de administrador
│   ├── list_cameras.py       ✅ qué cámara y qué backend usar, con fps REALES
│   ├── analizar_registro.py  ✅ CSV del head tracker, por percentil
│   ├── analizar_registro_brazos.py ✅ CSV del arm tracker: 4 canales,
│   │                            mano/codo, visibilidad, ganancias
│   ├── train_fer2013.py      ⬜ P8
│   └── eval_emotion.py       ⬜ P9
├── tests/                    ✅ 403 pruebas
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

Ángulos en grados (float), sin cuantizar.

**Sobre la tasa objetivo.** El plan declara 20 Hz, el publisher los sostiene
—verificado con el brazo oscilando en el visor— y **la cámara también da para
tanto**: la sesión de calibración del 2026-09-06 corrió a **30 fps sostenidos**
durante 142 s, sin un solo frame duplicado.

La limitación de «~15 Hz por hardware» que este plan declaró durante un mes
**queda retirada**: no era un techo del sensor sino una medición tomada con poca
luz. Lo que limita la captura es la **exposición**, y por tanto la iluminación de
la sala — la misma cámara da 30 fps o 10 según la luz (**H27**, §4.0.4).

Lo que sí hay que declarar en la tesis es la **condición**: la tasa se sostiene
con iluminación suficiente, y la cifra que se reporte debe ir acompañada de las
condiciones en que se midió.

**Sobre la latencia declarada.** El indicador `t-lat` del visor está
honestamente etiquetado como «proxy», pero mide el **intervalo entre mensajes**,
que está acotado por arriba por el periodo de publicación: no puede variar con
la latencia que dice aproximar. Medido, marca 49-51 ms, que es exactamente el
periodo de 20 Hz. **La latencia extremo a extremo no está medida.** Ver §3.2
para la decisión pendiente.

## 0.5 Límites articulares

**Nomenclatura corregida el 2026-09-06 (H28).** Lo que este documento llamaba
`balanceo` y `giro` son, con su nombre anatómico, **abducción/aducción** y
**flexión/extensión**. El código Python y los rótulos del visor ya usan los
nombres correctos; los campos del JSON (`armL`, `armLtw`, …) no se pueden
cambiar sin romper el contrato con el visor y siguen igual.

```
pan          −90° … +90°
tilt         −39° … +42°
flexión      −180° … +90°  (extendida en el P5.3; ±90 era el rango del slider) [campo armLtw/armRtw]
abducción    depende de la flexión                       [campo armL/armR]
```

**EL CERO ES EL BRAZO EN CRUZ, NO COLGANDO (H30).** Verificado en el visor el
2026-09-07. La abducción es la **elevación respecto a la horizontal**: 0° = brazo
extendido hacia el lado, −55° es lo que baja y +30° lo que sube antes de chocar
con la cabeza. **El robot no puede dejar los brazos caídos** — el operador en
reposo pide −90° y se queda en −55°—, y hay que declararlo. Además, la dirección
de reposo va sobre el propio eje de la flexión, así que **la singularidad del
cardán cae en la postura de reposo**: con el brazo en cruz, la flexión no lo
mueve.

**La flexión ya no se limita a ±90° (P5.3, H35).** ±90° era el rango de los
sliders del visor, no un tope del servo (XL430: 360° en modo posición), y el
barrido solo exploró dentro de él. Fuera de ±90 la tabla se extiende por
simetría: `(a, f)` y `(−a, f ± 180)` ponen el brazo en la misma dirección
(verificado contra el rig y renderizando el visor). `clamp_arm` conserva ±90
por defecto y el extendido se pide con `flexion_range=FLEXION_RANGE_EXTENDIDO`.
**Pendiente de confirmar en el CAD** que la unión y el cableado del hombro
admiten −180; si no, `TELMEOW_ARM_FLEXION_MIN=-90`.

**La abducción depende de la flexión.** Las tablas 2D del barrido BVH (paso 5°,
refinado 1°, demás DOF en home) son **la autoridad**; están en `limits.py`.

Los valores 1D de abducción (izq −55…+32, der −55…+33) **no se aplican** como
límite: son la rebanada de la tabla en flexión = 0° medida con refinado a 1°.
Aplicarlos colapsaría el rango y le quitaría al brazo el recorrido extra que
gana al flexionarse y despejarse del tronco. **Sí se usan como objetivo de
calibración** de la ganancia de abducción, igual que `HEAD_PAN_TARGET_RANGE`
para el pan: el rango de la tabla no sirve para eso porque cambia con la
flexión y daría una ganancia distinta en cada frame.

Orden de operaciones en `clamp_arm`: recortar la **flexión** a su rango primero
(es límite duro y además índice de la tabla), luego interpolar la tabla en esa
flexión, y con ese rango recortar la **abducción**. El rango de la flexión es
**±90° por defecto** —lo que cubrió el barrido, y lo que sigue usando el mapeo
`articular`— y **[−180, 90] cuando se pasa `flexion_range=FLEXION_RANGE_EXTENDIDO`**,
que es lo que hace el mapeo `direccion` del P5.3. Fuera de ±90 la tabla no se
interpola directamente: `arm_abduccion_range_extendido` la refleja con la
equivalencia `(a, f) ≡ (−a, f ± 180)`.

La prueba que distingue la implementación correcta de la errónea:
`clamp_arm(+70, flexion=+90, 'izq')` debe dar **+70**, no +32.

**El cardán del robot no es ideal.** El eje del servo de flexión está girado
**~9.9°** respecto al eje latero-lateral puro, alrededor del eje de abducción
(se lee del rig: `(-0.985, 0, ∓0.17)`). Como la dirección de reposo del brazo va
sobre ese mismo eje, no corregirlo no produce diafonía sino algo peor: un
**sesgo constante de 10°**, con el que la postura de reposo del robot leería
+10° de abducción en vez de 0. `arm_tracker.arm_angles` lo deshace con una
rotación previa en el plano frontal, y hay una prueba que fija esa magnitud.

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
| 4 | **P4** | Head tracker (Face Mesh + Kabsch/solvePnP, sin dlib) | ✅ **hecho y calibrado con el operador** |
| 5 | **P5** | Arm tracker corregido (bugs 1–4) | 🟡 signos verificados · falta medir con cámara |
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
como línea base, `SlewLimiter` para las discontinuidades del comando y
`ClassHysteresis`. Medido sobre escalón con ruido a 100 Hz:

| Filtro | Reducción de ruido | Retardo al 90 % |
|---|---|---|
| **OneEuro(mc=0.5, β=0.5)** | **11.2×** | **6 muestras (60 ms)** |
| EMA(α=0.06) | 9.7× | 36 muestras (360 ms) |
| EMA(α=0.2) | 3.8× | 10 muestras (100 ms) |

El One Euro gana **en los dos ejes a la vez**. Cifra citable en la tesis.

---

## PROMPT 4 — Head tracker ✅ HECHO

`head_tracker.py`: Face Mesh, sin dlib. Cadena: estimar la rotación → restar
neutro → ganancia → One Euro → `clamp_head`.

**Dos estimadores, conmutables en caliente con la tecla `t`:**

- **`mesh`** (por defecto) — alinea la nube completa de landmarks contra la
  canónica por descomposición SVD (algoritmo de Kabsch). No estaba en el plan
  original; lo sugirió el operador al notar el temblor, y **mide 7.7× mejor**.
- **`pnp`** — `solvePnP` sobre un modelo 3D canónico de 6 puntos. Es el que
  describe el marco teórico del documento; se conserva como comparación.

Ganancias separadas `gain_pan`, `gain_tilt_up` y `gain_tilt_down` —el tilt se
diferencia por sentido porque las asimetrías del robot (+42°/−39°) y del
estimador se suman— más `invert_pan` / `invert_tilt`. Todas 1.0 por defecto,
**pendientes de calibrar: ver P4.2**.

**Verificado con datos sintéticos** (proyectar el modelo con una rotación
conocida y recuperarla): error **0.00° en ±40°**, sin contaminación entre ejes.

⚠️ **Pero esa verificación sintética resultó no ser suficiente**, y conviene no
olvidarlo: pasaba en verde con el eje de referencia a 180° del sitio, porque las
propias pruebas usaban esa referencia equivocada (H17, §7.7). El error solo
apareció con el operador delante.

Convenio de signos, ya corregido y **medido con el operador, no deducido**:
`yaw+` = la cara mira hacia la **izquierda de la imagen**; `pitch+` = mira hacia
arriba. Como la imagen va espejada, el operador girando a **su** izquierda
produce `yaw` **positivo**.

⚠️ **Ese convenio se cumplía en `pnp` pero NO en `mesh`, que es el método por
defecto** — ver **H22**. El fallo estuvo vivo hasta el 2026-09-06 y afecta a
todo el tilt medido antes de esa fecha. Corregido en `47ee616`.

**Del ángulo del operador al ángulo del robot: `invert_pan` va activado.** Son
dos convenios distintos y conviene no confundirlos. El estimador habla de la
**imagen** (espejada); el `pan` que se publica es el **ángulo articular del
robot**. Con `invert_pan=True`, el operador girando a **su** derecha hace que el
robot gire a **su propia** derecha: el robot reproduce el gesto como lo haría
una persona puesta en su lugar, que es la convención que corresponde a los
servos y la que se declara en el documento. Ver **P4.1** para por qué eso se ve
«al revés» en el visor y por qué no es un problema.

### Resultados medidos con el operador real (2026-09-05)

Sesión grabada con `--record` y analizada con `tools/analizar_registro.py`.
1962 muestras, cámara externa a 1280x720.

⚠️ **De esta sesión solo sobrevive la comparación entre métodos.** Las cifras de
temblor quedan sustituidas por las del **P4.5**, tomadas en la configuración de
operación; el recorrido de tilt no se cita, por el signo (**H22**). Lo que sí se
mantiene es la comparación `mesh` frente a `pnp`, porque es una comparación
**relativa** entre dos métodos sobre los mismos frames: el signo y la
configuración afectan igual a los dos y se cancelan.

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

**Recorrido alcanzado:** pan ±55.5°, y en tilt se registraron +33.3° / −40.2°.

⚠️ **El recorrido de tilt de esta sesión no se cita.** Se grabó con el signo
invertido (**H22**) y además forzando el cuello, no con el criterio de pantalla.
La cifra buena es la del **P4.2**: 26.5° arriba y 21.6° abajo, sostenidos.

Lo que sí queda establecido, y con dos sesiones que coinciden: **el operador
llega menos hacia abajo que hacia arriba**. Su asimetría va en el mismo sentido
que la del robot (−39° frente a +42°), así que **las dos se suman** — de ahí que
el tilt necesite dos ganancias separadas y que `gain_tilt_down` sea la mayor de
las tres. Es también la respuesta a la queja inicial del operador de que bajar
la cabeza no llegaba: era cierta, y se corrige con la ganancia, no con la cámara.

**Otras confirmaciones medidas:**

- El ladeo de cabeza «de perro» (roll), que el robot no puede reproducir, deja
  pan y tilt en 0.00 y **no degrada** su medida: con 45° de ladeo, un giro real
  de 20° se sigue midiendo como 20.00.
- MediaPipe arrastra **TensorFlow entero** (2289 módulos) al importarse. De ahí
  el arranque lento y los avisos de oneDNN. Es dependencia suya, no nuestra, pero
  hay que tenerlo en cuenta para la Raspberry Pi del PROMPT 11.

### Las cinco validaciones — todas cerradas el 2026-09-06

El P4 estaba «hecho» desde que compiló, pero no **calibrado**: le faltaban cinco
comprobaciones que solo se podían hacer con el operador delante. Las cinco están
cerradas, y de ellas salieron dos cosas que ninguna prueba automática habría
encontrado: el tilt invertido (**H22**) y las ganancias reales.

Merece quedar dicho, porque se repite en los prompts que vienen: **el código
pasaba 400 pruebas en verde con el cabeceo al revés.** Lo que lo destapó fue
sentar a una persona delante y mirar el robot.

**P4.1 — Sentido del pan en el visor.** ✅ **cerrado el 2026-09-06.**
Comprobado con `python -m telmeow_teleop.head_tracker --publish`, que es la
bandera que se añadió justamente para poder hacerlo (antes no había forma de
llevar la cabeza al visor: `main.py` es del P7).

**Decisión: `invert_pan=True`, ya fijado por defecto en `HeadConfig`.** El robot
gira a **su** derecha cuando el operador gira a la suya. Es la convención del
robot físico, y es la que se envía a los servos y la que valida el visor.

El precio, y hay que decirlo en el documento porque salta a la vista: mirando al
robot **de frente** en el visor, su cabeza parece moverse al revés que la del
operador. No es un fallo, es lo que pasa al mirar a cualquiera de frente. Desde
atrás —o en primera persona, si algún día hay cámara en la cabeza— se ve
natural, y el visor ya permite orbitar hasta esa vista.

Se consideró y **se descartó** espejar solo la visualización, dejando el dato
intacto. Dos motivos: un render espejado invierte la quiralidad, así que el
brazo izquierdo del robot aparecería donde está el derecho —una trampa seria
justo antes del P5—; y el visor tendría dos verdades en el mismo grafo de
escena, una para dibujar y otra para colisionar, que es de las cosas que se
desincronizan calladas. Mover la cámara es una transformación rígida y consigue
el mismo efecto sin tocar nada.

Redacción para la tesis: *el mapeo del pan reproduce el gesto en el marco del
robot; que se vea especular en el banco virtual es consecuencia de observarlo de
frente, y el sentido es un parámetro de configuración (`invert_pan`), no un
cambio de diseño.*

**P4.2 — El ángulo de pérdida de pantalla.** ✅ **medido el 2026-09-06.**
El criterio (§3.2.1) era que el recorrido útil del operador **lo acota su campo
de visión sobre el monitor**, no su cuello. Faltaba el número, y ya está.

Protocolo ejecutado: once etiquetas alternando **tránsito** y **postura
sostenida** —las impares son el dato, las pares el camino—, con la mirada fija
en un texto centrado en el monitor y girando solo la cabeza. La regla de no
volver al centro dentro de una etiqueta, que fue lo que inutilizó `limites.csv`,
se respeta por construcción: el regreso cae siempre en una etiqueta par.

**Recorrido útil sostenido del operador**, por percentil:

| Sentido | Integrada (operación) | Externa (repetición) |
|---|---|---|
| pan izquierda | 33.5° | 21.9° |
| pan derecha | 29.5° | 29.6° |
| tilt arriba | 26.5° | 31.0° |
| tilt abajo | 21.6° | 19.2° |

La grabación está versionada en `calibracion_20260906_integrada.csv`, que es la
evidencia de las cifras de abajo. La de la cámara externa **se perdió**: se grabó
encima con el mismo nombre, y de ella solo queda la salida del analizador. Es lo
que motivó que `--record` ya no pise la grabación anterior.

**Ganancias resultantes, ya fijadas en `HeadConfig`:**

```
gain_pan       = 1.49      (50 / 33.5)
gain_tilt_up   = 1.59      (42 / 26.5)
gain_tilt_down = 1.80      (39 / 21.6)
```

Se adoptan las de la **configuración de operación**, sin mezclar sesiones ni
promediar: son las que propone `tools/analizar_registro.py` sobre esa grabación.
La repetición con la otra cámara da 1.69 / 1.35 / 2.03, y esa dispersión de
±15 % es **la repetibilidad de la calibración**, que es una cifra citable por sí
misma.

Las tres son mayores que 1, como anticipaba §3.2.1: el operador no puede cubrir
el rango del robot con un gesto que le deje ver la pantalla, y la ganancia es el
mecanismo que lo compensa.

⚠️ **Son de ESTE operador y de ESTE puesto.** Dependen de la distancia al
monitor y de cuánto ocupa este en su campo visual —no de la cámara ni del
robot—, así que otro operador, u otra mesa, exige repetir el protocolo. Conviene
declararlo así en el documento: es un parámetro de puesto, no una constante del
sistema.

**La asimetría del pan NO se declara.** Venía arrastrándose como posible
hallazgo desde `limites.csv` (29.5° a un lado, 38.3° al otro), pero las tres
grabaciones no coinciden ni en el sentido ni en la magnitud: 29.5/38.3, luego
21.9/29.6, luego 33.5/29.5. El lado derecho sí resulta notablemente reproducible
—29.6° y 29.5° en las dos sesiones nuevas—, pero el izquierdo varía 12° entre
sesiones. En la grabación de la externa, el propio tramo de tránsito llegó a 32°
antes de que el operador se acomodara en 21.5°, lo que apunta a **cómo se
asiente, no a un límite anatómico**. Se atribuye a la postura y se deja de citar.

**P4.3 — `suggested_gains()` apuntaba a ±90°, no a ±50°.** ✅ **hecho** (`b3813a6`).
El método dividía entre `HEAD_PAN_RANGE`, o sea ±90°, y proponía una ganancia de
pan **1.8× más alta de la que corresponde**: para un recorrido cómodo de 29.5°
proponía 3.05 cuando el número bueno es 1.69.

Se resolvió con una constante nueva, **`HEAD_PAN_TARGET_RANGE = (−50, +50)`**,
deliberadamente separada de `HEAD_PAN_RANGE`: la primera es **objetivo de
calibración** y la segunda sigue siendo el **tope mecánico** que aplica
`clamp_head`. Hay pruebas de las dos cosas para que nadie las confunda. El tilt
no se toca, porque ahí el objetivo **sí** es el rango mecánico completo.

El mismo error estaba en `tools/analizar_registro.py`, que es la herramienta con
la que se leen las grabaciones — o sea que habría reaparecido justo al analizar
la sesión del P4.2. También corregido.

**P4.4 — La grabación `limites.csv`.** ✅ **cerrado: superada y descartada.**
Queda sustituida por la sesión del P4.2 y **se deja de citar**. No sirve ni
releyendo: tenía el protocolo equivocado —se midió la comodidad del cuello, no
la pérdida de pantalla—, el tilt con el signo invertido (**H22**) y las
etiquetas asignadas de memoria después de grabar.

Intentar rescatarla tuvo un coste concreto, y por eso queda escrito como
**H26**: al releerla con el signo corregido se dedujo que al operador le costaba
más levantar la cabeza que bajarla, y esa afirmación falsa estuvo un día en este
documento y en tres docstrings. Las dos sesiones nuevas dicen lo contrario y
coinciden entre sí. **Una grabación con el protocolo equivocado no se recupera
reinterpretándola.**

**P4.5 — Repetibilidad en la configuración de operación.** ✅ **medida.**
Salió de la etiqueta de reposo que abre el protocolo del P4.2, sin necesidad de
una sesión aparte. **Cifras citables para la tesis:**

| | Crudo | Tras el One Euro |
|---|---|---|
| pan | 0.159° | **0.058°** |
| tilt | 0.296° | **0.138°** |

Y lo que de verdad recibe el robot, que es el temblor filtrado por la ganancia:
**0.09° en pan y 0.25° en tilt**, sobre rangos de ±50° y −39/+42°.

Las cifras viejas (0.19°/0.31°, cámara externa a 1280x720) quedan sustituidas.
La configuración de operación resulta **mejor**, no peor, que aquella.

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

## PROMPT 5 — Arm tracker 🟡 **código hecho, falta P5.1**

### Lo hecho el 2026-09-06

`arm_tracker.py`, con BlazePose y `pose_world_landmarks`. Cadena por canal:
dirección del brazo → descomposición en el cardán del robot → ganancia por
sentido → inversión → One Euro → `clamp_arm`. Los cuatro bugs del legacy
corregidos, cada uno con su prueba de regresión. **120 pruebas nuevas, 534 en
total, todas en verde.**

Cuatro decisiones que cambian lo que este documento decía:

**1. `giro` no es rotación axial: es flexión (H28).** El enunciado de abajo
pedía estimar la rotación axial del húmero desde el vector codo→muñeca y
mandarla al canal `tw`. Los ejes del rig dicen que ese canal es un vaivén
sagital, no una torsión, y que el robot no tiene ningún GDL de rotación axial.
Nomenclatura corregida en `limits.py`, `arm_tracker.py`, las pruebas y los
rótulos del visor; el contrato MQTT intacto. Ver §0.5 y §7.12.

**2. El brazo se apunta con la MANO, no con el codo.** ⚠️ **El segundo argumento
de esta decisión resultó falso el 2026-09-13** (ver P5.2): medido sobre 13.271
frames, el **codo se ve más que la mano**, no menos. El primer argumento sigue
en pie y es el que importa —el brazo del robot es un eslabón rígido sin codo,
así que lo que hay que conservar es hacia dónde apunta el brazo entero—, pero la
elección vuelve a depender de la medición mano-frente-a-codo, que está
pendiente. Lo que se escribió aquí era: que sentado a la mesa con la integrada a
640x480 el codo se sale de cuadro antes que la mano. No es así.

Se usa el **centroide de los landmarks de mano** que BlazePose ya entrega
(muñeca, meñique, índice y pulgar), que es gratis y en principio más estable que
la muñeca sola — con la **muñeca como puerta de visibilidad**, corregido el
2026-09-13: gatear con el mínimo de los cuatro sesgaba la comparación contra la
mano. `target="codo"` queda conmutable con una tecla para poder comparar los dos
con datos, como `mesh`/`pnp` en el P4.

*(Y una corrección a lo que yo mismo escribí al planificar: hombro→muñeca **no**
es el BUG 4 en versión suave. El pecado del legacy era el pivote —medía respecto
a la **cadera**—, no el punto de destino.)*

**3. El canal `tw` va al revés de lo que dice el visor (H29).** Tres anclas
independientes, dos ya verificadas en vivo, dicen que `+armRtw` lleva el brazo
hacia **atrás**. `ARM_INVERT_FLEXION` viene activado para compensarlo, y el
comentario del visor queda marcado como en revisión. **No se ha tocado ningún
eje ni ningún valor del rig.** Ver §7.12.

**4. Ganancias implementadas y a 1.0.** Cuatro, una por sentido, y el analizador
las propone.

⚠️ **Aquí decía «no hay motivo conocido para que hagan falta». Lo hay, y se
midió el 2026-09-13**: en **abducción sí hacen falta** (~0.33/0.51 provisional),
porque el recorrido del hombro humano desborda por completo el del robot —
consecuencia del **H30**, cuyo cero está en la horizontal, así que solo tiene
−55…+32 de recorrido. En **flexión no** (1.04/1.06): ahí el operador y el robot
coinciden. El valor definitivo sale de la sesión con protocolo.

### P5.1 — Verificación en el visor ✅ **cerrada el 2026-09-07**

Se hizo moviendo un slider cada vez, con el visor delante, y **desmontó la
geometría que este documento daba por buena**: el reposo del robot tiene los
brazos en cruz, no colgando (**H30**, §7.13). Eso obligó a reparametrizar el
estimador entero y a **retractar el H29**, que era una deducción de papel
impecable levantada sobre un supuesto falso.

Lo que quedó fijado, todo medido y no deducido:

| Comprobación | Resultado |
|---|---|
| Postura de reposo | **brazos en cruz**, horizontales |
| `armR` −50 / +30 | baja el brazo / lo sube ⇒ abducción, sentido global ✓ |
| `armRtw` con `armR`=0 | **no mueve el brazo**: singularidad en el reposo |
| `armRtw` −60 con `armR`=−50 | brazo hacia **adelante** |
| `armRtw` +60 con `armR`=+20 | brazo hacia **adelante** ⇒ el sentido depende de la abducción |
| `tilt` −39 | baja la cabeza ✓ (reconfirma H22) |
| Ida y vuelta contra el rig | cierra a **<0.2°** en los cuatro canales |

Las dos banderas de inversión quedan en `False`: el estimador reproduce la
cinemática inversa exacta del cardán, así que lo que sale de él ya son los
valores articulares del robot.

### Lo hecho el 2026-09-13 — la etapa de salida, y el tronco que mentía

Dos cosas, las dos salidas del primer arranque de la sesión P5.2 y ninguna
prevista en el plan.

**1. El HUD mentía sobre la vertical del tronco (H31).** Decía «tronco:
caderas» con `cadera 0.00` en la misma línea. La bandera `_tronco_de_caderas`
se ponía en `True` el primer frame en que las caderas superaban el umbral y
**no volvía a bajar nunca**; lo único que la reiniciaba era no haber tenido
nunca una vertical buena. Consecuencias:

- El HUD declaraba observado lo que era un valor **congelado** de un momento
  indeterminado.
- La columna `tronco_caderas` del CSV escribía `1` para siempre, así que el
  analizador habría reportado «la vertical salió de las caderas en el 100 % de
  los frames» sobre una grabación en la que no se vieron jamás. Una cifra falsa,
  citable, y sin nada que la delatara.

Arreglado con **tres estados** —`caderas` (vista en este frame), `retenido Xs`
(derivada de las caderas hace poco) y `RESERVA vertical`— y una **caducidad**
de 2 s (`RETENCION_TRONCO_S`). Pasado el plazo se olvida la vertical y se cae a
la reserva: **una vertical retenida un minuto es peor que la reserva**, porque
la reserva es un supuesto declarado —operador sentado y erguido— y la retenida
es un supuesto invisible. El CSV gana la columna `tronco_origen` y el analizador
imprime el desglose de los tres.

**2. Estimación y comando pasan a ser cosas distintas.** Lo pidió el operador:
al perder los brazos de cuadro el robot se quedaba **clavado a mitad de gesto**,
porque el canal se omitía del JSON y el visor conserva el último valor. Y al
recuperarlo, **saltaba**: el One Euro adapta su corte al `dt`, así que tras un
hueco largo su factor de suavizado se va a 1 y la primera muestra de vuelta pasa
entera.

Ahora `process()` devuelve **lo medido**, con `None` donde no se pudo medir, y
`comando()` devuelve **lo que se le manda al robot**, que nunca tiene huecos.
Tres reglas y un límite:

| | Regla |
|---|---|
| 1 | Si el brazo se estimó, el objetivo es lo estimado |
| 2 | Si no, y lleva menos de `gracia_s` (0.5 s), se mantiene el último objetivo — absorbe los parpadeos de visibilidad, que son la mayoría |
| 3 | Si no, el objetivo es la **pose de reposo** |
| + | Sobre las tres, un **límite de velocidad angular** de 180 °/s por canal (`filters.SlewLimiter`) |

**El reposo por defecto es −55° de abducción, no 0**, y es consecuencia directa
del H30: el cero del robot son los brazos en cruz, así que volver al cero sería
volver a una postura de espantapájaros. −55° es lo más bajo que llega su
mecanismo. Pasa por `clamp_arm`, así que una configuración descuidada no puede
pedir una postura imposible.

El límite de velocidad tiene un precio que hay que declarar porque **la latencia
es una métrica del proyecto**: un salto de `d` grados tarda `d / velocidad_max`
segundos (los 55° del reposo, ~0.3 s). Se elige **por encima de la velocidad del
gesto narrado** para que solo actúe en las discontinuidades; por debajo
introduciría retardo en el gesto normal. Y no es solo estético: un servo real
tiene su propia velocidad máxima, así que limitar el comando lo acerca a lo que
el actuador puede seguir.

Efecto lateral que mejora la instrumentación: el CSV gana `estimado_L` /
`estimado_R`, y el analizador **excluye los tramos de rampa** al calcular el
temblor. Sin esa columna, la cifra de §3.3.1 habría mezclado el temblor del
estimador con la pendiente del limitador — un número del filtro disfrazado de
número del estimador.

**+31 pruebas (581 en total).** 12 del `SlewLimiter` en `test_filters.py`, y en
`test_arm_tracker.py` dos grupos nuevos: la etapa de salida (incluidas
`test_el_reposo_no_es_el_cero_del_robot`, que fija el H30, y
`test_un_parpadeo_de_visibilidad_no_tira_el_brazo`) y los tres estados del
tronco con su caducidad.

De paso se retiró `SENTIDO_FLEXION_VISOR` de las pruebas: era la conclusión del
H29 retractado, ya no se usaba y su comentario afirmaba algo falso. `pyflakes`
no avisa de constantes de módulo sin usar, así que había sobrevivido.

### P5.2 — Sesión con cámara 🟡 **arrancada el 2026-09-13, falta el operador**

```
& "C:\Program Files\mosquitto\mosquitto.exe" -c mosquitto.conf -v
python -m http.server 8000 --directory telmeow_viewer
python -m telmeow_teleop.arm_tracker --publish --record brazos.csv
```

#### Lo que se sacó de `brazos_2026-09-13_descartada.csv`

**La grabación queda descartada como medición** y su nombre lo dice: 13.271
frames (563 s, 23.6 fps) en **una sola etiqueta**, solo con `target="mano"`, con
los signos aún sin verificar y con el tronco mintiendo (H31). No sirve para
ninguna de las cifras que la tesis necesita — el temblor sobre 563 s de
movimiento continuo mide el movimiento, no el temblor (H24), y sin una segunda
etiqueta con `codo` no hay comparación. Se **conserva** porque es la evidencia
del H31 y porque las columnas `crudo_*` y `vis_*` sí son medida honesta.

Tres cosas que sí quedan establecidas:

**1. El H31, demostrado con números en la propia grabación.** Las dos columnas
se contradicen de frente:

| Columna | Valor |
|---|---|
| `vis_cadera` por encima de 0.5 | **9.8 %** de los frames (mediana **0.03**) |
| `tronco_caderas` = 1 | **86.5 %** de los frames |

Las caderas se vieron en uno de cada diez frames y el instrumento declaró
haberlas usado en nueve de cada diez. Es la bandera enclavada, medida.
Consecuencia real para el P5.2: **la vertical del tronco va a salir de la
reserva buena parte del tiempo**, y eso hay que declararlo en la tesis —
sentado a la mesa, las caderas simplemente no entran en cuadro.

**2. El codo se ve MÁS que la mano, al contrario de lo previsto** — y parte de
la diferencia era un sesgo del instrumento:

| Landmark | Por encima de 0.5 | Mediana |
|---|---|---|
| hombros | 100 % | 1.00 |
| **codo** izq / der | **89.3 % / 91.8 %** | 0.88 / 0.93 |
| **destino (mano)** izq / der | **47.5 % / 44.5 %** | 0.39 / 0.38 |

El argumento del operador para hombro→mano era que el codo quedaría oculto y la
mano visible. **Con estos datos es al revés.** Pero la comparación no era
limpia: `vis_destino` era el **mínimo de los cuatro** landmarks de mano mientras
el codo se medía con **uno solo**, y BlazePose estima los dedos con bastante
menos confianza que la muñeca incluso con la mano a la vista. O sea que el
agregador sesgaba justo la decisión que la medición tenía que resolver.

**Corregido**: la puerta de `target="mano"` es ahora la **muñeca** —un landmark,
igual que el codo— y el centroide promedia los dedos que superen el umbral. El
CSV gana `puntos_destino_L`/`_R` con cuántos landmarks formaron el punto, para
poder comprobar si «cuatro puntos son más estables que uno» se sostiene o si el
centroide colapsa a la muñeca. **La decisión mano-frente-a-codo se reabre** y se
resuelve con la grabación nueva.

**3. Las ganancias sí hacen falta, y solo en la abducción.** Contra
`ARM_ABDUCCION_1D` y desde el percentil (H24):

| Sentido | Ganancia sugerida |
|---|---|
| abducción | **0.33** |
| aducción | **0.51** |
| flexión | 1.04 |
| extensión | 1.06 |

Y encaja con el resto: `armL` salió **recortado por el tope en el 75 %** de los
frames y `armR` en el 67 %. El recorrido del operador (±100° y más) desborda el
del robot (−55…+32), mientras que en flexión coinciden. Contradice lo que este
documento suponía —«no hay motivo conocido para que las ganancias hagan falta»—
y el motivo ahora se entiende: es el **H30**. El robot no tiene el recorrido de
abducción de un hombro humano porque su cero está en la horizontal.

Las cifras de arriba son provisionales, porque salen de una grabación sin
protocolo; el valor se fija con la sesión buena. Pero la **dirección** está
clara y no va a cambiar.

Truco de diagnóstico en vivo que conviene recordar: MediaPipe solo dibuja los
landmarks por encima de 0.5, **el mismo umbral que usa el tracker**. Si el
esqueleto del brazo no está pintado, el tracker tampoco lo está usando.

1. **Las cuatro afirmaciones con el operador delante.** Separar el brazo
   izquierdo físico → se separa el izquierdo del robot; levantarlo → sube; ídem
   con el derecho. Es lo único que ata el marco del OPERADOR al del robot: la
   verificación del visor ató el marco del robot, pero que `u_ant` sea de verdad
   el frente del operador —el signo `−z` de MediaPipe— solo se comprueba con una
   persona en cuadro. Las teclas `b` y `f` alternan los signos en caliente y `m`
   el espejo, para no relanzar.
2. **Grabar dos o tres minutos** en postura de narración, alternando `t` entre
   mano y codo, y pasar el CSV por
   `python tools/analizar_registro_brazos.py brazos.csv`. De ahí salen las tres
   cifras que faltan: el temblor por canal (respalda §3.3.1), la comparación
   mano/codo, y el porcentaje de frames en que se ven el codo y las caderas.
3. **Comprobar el reposo.** El operador con los brazos caídos pide −90° de
   abducción y el robot se queda en −55°: hay que ver cómo se siente eso y si
   conviene una ganancia menor que 1 en ese sentido, que es justo para lo que
   están las ganancias.
4. **Medir el coste de BlazePose** con `model_complexity` 0, 1 y 2, **con el
   operador delante**. Es lo que hay que aclarar antes del P7, donde BlazePose
   comparte bucle con Face Mesh y con el clasificador de emoción.

   Ya hay media medida, y hay que leerla con cuidado porque **es fácil creer
   que dice más de lo que dice**: sin persona en cuadro, los tres niveles dan
   **17.2 ms** idénticos. No significa que la complejidad no importe: significa
   que sin persona solo corre el **detector**, que es común a los tres, y el
   modelo de landmarks —que es lo que cambia— no llega a ejecutarse. Medirlo
   así y concluir «da igual el nivel» sería el **H19** otra vez.

   Lo que sí queda establecido: el detector solo cuesta **~17 ms/frame**, o sea
   **tres veces** lo que cuesta Face Mesh entero (5-7 ms), y es el camino que
   se toma en cada frame en que el operador no se detecta. Con 20 Hz de
   publicación (50 ms de presupuesto) cabe, pero deja de sobrar.

   ⚠️ **MediaPipe descarga los `.tflite` la primera vez que se usa un nivel**
   (`pose_landmark_lite.tflite`, `pose_landmark_heavy.tflite`, dentro del
   `.venv`). Con `model_complexity=1` no se nota porque el `full` ya viene en el
   paquete. Dos consecuencias: la primera ejecución con un nivel distinto
   **necesita red**, y para la Raspberry Pi del P11 hay que preverlo.

### P5.3 — Mapeo por dirección ✅ **hecho el 2026-09-27** · falta verlo con el operador

**El síntoma** (reportado por el operador con MQTT y el visor): mucha vibración y,
sobre todo, que al **acercar las manos a la cámara** el brazo del robot no iba
hacia adelante sino que se movía a los lados y, de pronto, **daba una vuelta
entera** para llegar a una posición contigua.

**La causa no era la profundidad** —la `z` de `pose_world_landmarks` sí se
usa—, **era el mapeo articular** (H35). Con la flexión en ±90°, «brazo al frente
horizontal» solo se alcanza con la flexión en el tope y tiene dos soluciones,
`(+ψ, +90)` y `(−ψ, −90)`. `arm_angles` elegía entre ellas con el **signo de la
componente vertical** del brazo, que al frente ronda el cero y cambia con el
ruido. Cada cambio mandaba el objetivo al otro extremo, y el One Euro y el
limitador —los dos en espacio articular— lo interpolaban **pasando por la pose
en cruz**. Medido en `brazos_2026-09-13_descartada.csv`:

| | armL | armR |
|---|---|---|
| Saltos de flexión >90° con cambio de signo de abducción | **174** | **129** |
| Cambio REAL de dirección del brazo en esos saltos (mediana) | 4.6° | 5.0° |
| Tiempo del operador a ±10° de la costura (con ψ > 30°) | **25 %** | **29 %** |

O sea: la zona donde el mapeo fallaba es donde el operador pasa un cuarto del
tiempo.

**Lo hecho** — `telmeow_teleop/retarget.py`, nuevo, y `mapeo="direccion"` por
defecto en `ArmTracker` (el articular queda conmutable con `k`):

1. Se trabaja con la **dirección** del brazo, `h = (L, S, A)`, y los ángulos solo
   se calculan al final. `direccion_brazo()` sale de `arm_angles` sin cambiarlo.
2. **One Euro vectorial** (`filters.OneEuroVector`): un corte común para las tres
   componentes, así que el filtro puede retrasar la dirección pero no girarla, y
   no tiene polo ni costura que promediar.
3. **Proyección al hemisferio frontal**: lo que queda por detrás del plano
   frontal cae en el punto admisible más cercano (plano posterior del visor).
4. **Polares respecto al polo del cardán** —ψ = ángulo con el lateral, α = 0
   arriba / 90 al frente / 180 abajo—, que es la parametrización natural de
   este mecanismo: abducción = ±ψ, flexión = α o α − 180.
5. **Escala de ψ dentro de la envolvente del robot**, que sale de las tablas 2D
   (`alcance_robot`: ~30° arriba, ~70° al frente, ~55° abajo) y se suaviza en la
   costura de la tabla. El operador con el brazo colgando pide exactamente el
   reposo; más allá de 90° satura con pendiente cero (curva C¹, sin esquina
   contra la que vibrar). **Esto sustituye a las cuatro ganancias**: las
   provisionales del P5.2 (0.33 / 0.51) son justo `30/90` y `≈55/90` (H36).
6. **Cinemática inversa de una sola rama** (abducción ≤ 0) con la flexión
   extendida a −180°: cubre todo el hemisferio frontal sin costura. Con
   `TELMEOW_ARM_FLEXION_MIN=-90` funciona igual pero con **histéresis** de 15°
   en la costura, y cada cruce cuesta un paso por la pose en cruz: es del
   mecanismo, no del software.
7. **Amortiguación de la flexión en el polo** (ψ < 5° congelada, plena a 20°).
8. **Límite de velocidad coordinado** (`filters.SlewLimiterND`): los saltos
   grandes recorren la recta entre poses en vez de una L.

**Resultado sobre la misma grabación**, con `tools/comparar_mapeo_brazos.py`
(reproduce el CSV por los dos mapeos con la cadena real del tracker):

| | articular L / R | dirección L / R |
|---|---|---|
| Saltos de rama | 174 / 129 | **0 / 0** |
| Correlación componente **anterior** operador→robot | 0.64 / 0.62 | **0.93 / 0.89** |
| Correlación componente lateral | 0.35 / 0.29 | **0.67 / 0.57** |
| Temblor de la punta p95 (°/frame) | 10.2 / 9.5 | **7.1 / 6.7** |
| Con flexión ±90 + histéresis: cambios de rama | — | 54 / 48 |

La fila de la componente anterior es la queja del operador en un número: «acerco
las manos y el robot no va hacia adelante». La última fila es el argumento para
la flexión extendida.

**Verificado en el visor renderizándolo** (Chromium sin pantalla, sliders): las
poses `(−40, −135)` y `(−28, −180)` dejan el brazo donde `(+40, +45)` y
`(+28, 0)`, y la bisección de colisiones no bloqueó ninguna. El brazo es
simétrico alrededor de su eje (medido en `Brazo_Der_W.stl`: radios iguales a
±2 mm), así que el giro de 180° sobre su eje que implica la otra rama no se ve.
Lo que SÍ gira distinto es la **unión** y el **motor que viaja con el brazo**.

**Cambios en el contrato**: ninguno en el JSON. El visor recibe `armLtw` en
[−180, 90] y ya lo aplicaba; solo cambió el rango de sus dos sliders de flexión.
El CSV gana `mapeo`, `flexion_min`, `h_lat/sup/ant_*` (la dirección cruda, que
permite reproducir OFFLINE cualquier mapeo futuro sobre la misma sesión) y
`psi_*`, `alfa_*`, `rama_*`.

**Lo que falta, con el operador delante:**

1. **Confirmar en Fusion** que la unión del hombro, el motor del brazo y el
   cable admiten flexión hasta −180°. Si no, `TELMEOW_ARM_FLEXION_MIN=-90`.
2. Sesión con `--publish`: acercar las manos a la cámara, subir y bajar el brazo
   por delante, cruzar por delante del cuerpo. Alternar `k` para ver los dos
   mapeos sobre el mismo gesto.
3. Grabar con protocolo y pasar el CSV por `tools/comparar_mapeo_brazos.py`: las
   cifras de la tabla de arriba salen de una grabación descartada y hay que
   rehacerlas con la buena antes de citarlas.
4. **Profundidad**: con las columnas nuevas se puede medir cuánto comprime
   BlazePose la `z`. Si hiciera falta, la siguiente palanca es estimar la
   componente anterior por **escorzo** (longitud 2D del brazo contra su
   longitud calibrada, resolviendo la intersección rayo-esfera con la cámara) y
   fusionarla con la de BlazePose. **No se implementa hasta medir** que hace
   falta.

### Lo que hereda del P4 — leer antes de escribir código

El P4 se cerró el 2026-09-06 con el operador delante, y de ahí salen cuatro
cosas que le ahorran tiempo al P5.

**1. La configuración de cámara ya está decidida, y se decidió POR EL P5.**
Cámara **integrada a 640x480**, y el motivo es el encuadre: la externa recorta
tanto que da una cara de 105 px entre los ojos frente a 60 px de la integrada, y
el arm tracker necesita **hombros, codos y muñecas en cuadro**. En precisión
efectiva empatan (**H25**). El índice de cámara cambia entre sesiones en
Windows: correr `tools/list_cameras.py` al empezar.

```
$env:TELMEOW_CAMERA_BACKEND="dshow"; $env:TELMEOW_CAMERA_INDEX="1"
$env:TELMEOW_CAMERA_WIDTH="640";     $env:TELMEOW_CAMERA_HEIGHT="480"
```

⚠️ **El `1` de ahí arriba no es universal, y el 2026-09-13 era falso**: con solo
la integrada conectada, el índice **1 no se abría** y la integrada era el **0**
—que además es el valor por defecto, así que sin variables de entorno funciona—.
Es exactamente el motivo por el que hay que correr `list_cameras.py`: el número
depende de qué esté enchufado, no del protocolo.

**2. El punto 11 de abajo —la convención de signos— es EL riesgo de este
prompt, y ya se materializó una vez.** En el P4, el método por defecto devolvía
el cabeceo invertido y **pasó 400 pruebas en verde**: el operador levantaba la
cabeza y el robot la bajaba (**H22**). La causa fue que las pruebas sintéticas
solo ataban el signo de un camino de código, y el otro quedó libre.

El P5 tiene **cuatro canales con signo, más una decisión de espejo**, o sea
cuatro veces la superficie del mismo fallo. Dos consecuencias prácticas:

- **Ninguna prueba sintética vale si el convenio de referencia sale de la propia
  prueba.** Hay que atar cada signo contra algo externo: el visor, o una
  afirmación geométrica independiente. Ver las tres pruebas de
  `test_los_dos_metodos_coinciden_en_el_signo_del_pitch` y siguientes como
  patrón — comparan **dos caminos distintos entre sí**, no cada uno consigo
  mismo.
- **Verificarlo en el visor es barato y obligatorio.** El head tracker tiene
  `--publish` (`head_tracker.py`), que publica la pose mientras corre el demo;
  el arm tracker debería tener lo mismo desde el principio. `main.py` es del P7,
  así que sin esa bandera no hay forma de ver los brazos moverse.

**3. Cómo verificar en el visor sin equivocarse.** El visor renderiza a muy
pocos fps cuando no está en primer plano y la cámara sigue deslizándose por
amortiguación durante muchos segundos tras un arrastre. Comparar dos capturas
tomadas en momentos distintos **no es fiable** — así se llegó a una conclusión
falsa sobre el sentido del tilt. Lo correcto es mover **un solo valor** con el
slider o por MQTT y comparar sin tocar la cámara entre medias.

**4. Herramientas que ya existen.** `--record` graba un CSV por frame (y ya no
pisa la grabación anterior). `tools/analizar_registro.py` analiza esos CSV con
percentiles y detección de etiquetas contaminadas, **pero es específico del head
tracker**: espera columnas `raw_pan`/`raw_tilt`. Si el P5 necesita calibrar algo
por medición, hay que extenderlo, no asumir que sirve tal cual.

Sobre ganancias: el arm tracker **no las lleva por ahora**. Los ángulos del
hombro se mapean directos a los DOF del robot; la ganancia del P4 existe porque
el operador no puede girar la cabeza sin perder de vista el monitor, y ese
problema no se replica en los brazos.

### Enunciado original

⚠️ **Se conserva como historia, y tres de sus puntos están SUPERADOS.** Vale
para los cuatro bugs, que se corrigieron tal cual, y no vale en esto:

| Dice | Lo que se hizo, y por qué |
|---|---|
| `giro` es la «rotación axial del brazo», derivada de codo→muñeca (punto 2) | **Es flexión sagital.** El robot no tiene rotación axial (**H28**). Estimarla y mandarla a ese canal movería el brazo adelante cuando el operador rota el antebrazo |
| «documenta que esta estimación es la menos observable» (punto 2) | La conclusión se mantiene pero **cambia de razón**: la flexión depende de la coordenada de profundidad de BlazePose. Reescrito en §3.3.1 |
| `balanceo`: ángulo del vector **hombro→codo** (punto 1) | **hombro→mano** por defecto, con el codo conmutable. Decisión del operador y es la buena: el brazo del robot es un eslabón rígido sin codo, y el codo se sale de cuadro antes que la mano |
| «Salida `(armL, armLtw, armR, armRtw)`» (punto 7) | Un `ArmAngles` con cada canal `float \| None`, para que **un brazo fuera de cuadro no tumbe al otro** |

Y el punto 11 —la convención de signos— resultó ser exactamente el riesgo que
anunciaba, solo que el que estaba equivocado era el comentario del visor
(**H29**).

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
   - ✅ **Medido y cerrado** (P4.2). El recorrido útil sostenido resultó ser
     33.5° / 29.5° en pan y 26.5° / 21.6° en tilt, de donde salen las ganancias
     **1.49 / 1.59 / 1.80**, ya fijadas en `HeadConfig`. Una repetición con otra
     cámara da 1.69 / 1.35 / 2.03: ±15 % de repetibilidad de la calibración.
   - ✅ `suggested_gains()` ya divide entre `HEAD_PAN_TARGET_RANGE` (±50°), y
     también lo hace `tools/analizar_registro.py`. La cifra del HUD es citable.
     Ver **P4.3**.
   - Las ganancias provisionales que salían de `limites.csv` **quedan retiradas**:
     esa grabación se descarta entera, no se relee. Ver **P4.4** y **H26**.

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

1. **El robot no reproduce la rotación del brazo, y la flexión es el canal
   menos fiable.** Son dos cosas y hasta el 2026-09-06 este punto las
   confundía — decía que «la rotación axial del húmero no es determinable de
   forma robusta desde landmarks monoculares», que describe un grado de
   libertad **que el robot no tiene** (H28). Redacción corregida:

   - **Rotación interna/externa: no se reproduce.** El hombro del robot es un
     cardán de dos GDL que orienta un brazo rígido sin codo. No hay eje de
     rotación axial, así que esa componente del gesto se pierde por diseño
     mecánico, no por limitación del estimador. Se declara como tal.
   - **La flexión es menos fiable que la abducción, y ahora por una razón
     medible.** La abducción vive en el **plano de imagen**, donde MediaPipe es
     preciso; la flexión depende de la **coordenada de profundidad**, que es la
     componente débil de BlazePose en monocular. La cifra que lo respalda es la
     razón de temblor entre los dos canales, que da
     `tools/analizar_registro_brazos.py` sobre una grabación de reposo. **Falta
     medirla** (P5.1).
   - **La flexión de codo del operador tampoco se reproduce**, porque el brazo
     del robot es un eslabón único. El mapeo usa la dirección hombro→mano, así
     que se conserva hacia dónde apunta el brazo pero no su forma. Es un
     retargeting deliberado, no un error de modelo.
   - **El robot no puede bajar los brazos, y en su reposo pierde un grado de
     libertad** (H30, verificado en el visor). Su cero es el brazo en cruz y
     solo baja 55° desde ahí, así que el gesto de «brazos caídos» del operador
     se reproduce recortado. Y como la dirección de reposo cae sobre el eje de
     la flexión, **con el brazo en horizontal la flexión no mueve nada**: para
     señalar al frente hay que subir o bajar el brazo primero. Es una limitación
     de diseño mecánico, cuantificada y fácil de enseñar en el banco virtual.

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

9. **La tasa de captura depende de la ILUMINACIÓN del puesto, no del
   algoritmo.** El cuello de botella no es el método —MediaPipe cuesta 5-7 ms
   por frame, el 7 % del presupuesto— ni resultó ser el sensor: es la
   exposición. La misma cámara, con el mismo código, mide **30 fps con buena luz
   y 10 con poca** (**H27**, §4.0.4). Con la iluminación de la sesión de
   calibración la captura sostuvo 30 fps durante 142 s, de sobra para los 20 Hz
   de publicación. **Lo que se declara no es un techo, es una condición de
   operación**, y la cifra reportada tiene que ir con las condiciones en que se
   midió.

10. **El mapeo operador → robot está amplificado, y eso amplifica el temblor.**
    La ganancia > 1 es necesaria porque el campo de visión del operador sobre su
    monitor acota su recorrido útil (§3.2.1). El precio es proporcional: con
    ganancia 2.5, el temblor de 0.19° pasa a 0.48°. Sigue siendo pequeño frente
    al rango de ±50°, pero hay que declararlo junto con la ganancia empleada, no
    reportar el temblor crudo del estimador como si fuera el del robot.

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

4. **El techo real de captura es ~15 fps, no 30.** Es una limitación del
   hardware disponible, no del software, y obliga a revisar los objetivos de
   tasa del plan.

   Medido con `tools/list_cameras.py --medir`, contando solo frames **distintos
   del anterior**:

   | Cámara | Backend | 1280x720 | 640x480 |
   |---|---|---|---|
   | 0 (externa) | dshow | 10.0 fps | 12.5 fps |
   | 0 (externa) | msmf | falla | 12.8 fps |
   | 1 (integrada) | dshow | 8.0 fps | **15.0 fps** |
   | 1 (integrada) | msmf | falla | **15.3 fps** |

   **Mejor combinación: cámara integrada a 640x480.**

   ⚠️ **La cifra de ~15 fps de esta tabla está EN DUDA desde el 2026-09-06**, y
   con ella la limitación que este plan declara en §0.4 y §3.3.9. Ver el aviso
   al final de este apartado.

   **Por qué la integrada y no la externa**, ya con datos del operador y no solo
   de la tabla:

   - **El encuadre manda, y es del PROMPT 5.** La externa recorta mucho más: a
     la misma distancia da una cara de 105 px entre los ojos frente a 60 px de
     la integrada. Para la cabeza eso es una ventaja, pero el arm tracker
     necesita **hombros, codos y muñecas en cuadro**, y ahí ese recorte es
     descalificante. La configuración tiene que servir al sistema completo, no
     al head tracker solo.
   - **En precisión efectiva empatan.** Es lo que dice **H25**: la externa gana
     en temblor crudo (0.146° frente a 0.159° en pan) y **pierde tras el filtro
     One Euro** (0.098° frente a 0.058°). La cifra que llega al robot es la
     filtrada, y ahí la integrada es igual o mejor. Comparar cámaras por el
     dato crudo lleva a la decisión contraria a la correcta.

   ```
   $env:TELMEOW_CAMERA_BACKEND="dshow"; $env:TELMEOW_CAMERA_INDEX="1"
   $env:TELMEOW_CAMERA_WIDTH="640";     $env:TELMEOW_CAMERA_HEIGHT="480"
   ```

   Tres cosas que contradicen lo que parecía:

   - **Más resolución da MENOS fps** (8-10 a 720p frente a 12-15 a 480p). La
     webcam externa tampoco es mejor que la integrada.
   - **Medir `read()` engaña.** Puede devolver el mismo buffer repetido: una
     combinación que aparentaba 156 fps entregaba 21 de cada 39 frames
     duplicados, y su tasa real era de 3.7 fps. Solo vale contar frames
     distintos.
   - **Ningún ajuste de software lo cambia.** Se probaron MJPG antes y después
     de la resolución, `CAP_PROP_BUFFERSIZE`, `CAP_PROP_FPS`,
     `CAP_PROP_CONVERT_RGB` y cuatro variantes de exposición: los siete dieron
     79.7-80.2 ms, idénticos. Un lector en hilo aparte confirmó 12.2 fps, así
     que no es latencia de `read()`: la cámara **produce** a esa tasa.

   Consecuencias que se dedujeron entonces: el objetivo de **30 fps de captura
   no sería alcanzable** con este hardware, y el de **20 Hz de publicación
   tampoco** — quedaría en ~15 Hz. MediaPipe no tiene culpa: cuesta 5-7 ms por
   frame, el 7 % del tiempo.

   ### ⚠️ CORREGIDO el 2026-09-06: no hay techo fijo, lo pone la LUZ

   **No es una limitación del sensor. Es la exposición, y por tanto la
   iluminación de la sala.** La misma cámara, con el mismo formato, la misma
   resolución y el mismo código, mide **30 fps o 10 fps según la luz que haya**.

   **Evidencia 1 — la sesión de calibración corrió a 30 fps sostenidos.** 4262
   frames en 142.1 s, con **cero frames duplicados** en los 4261 pares
   consecutivos. Y el reparto de periodos entre frames lo confirma por una vía
   independiente: mediana de **32.1 ms**, p95 de 48.4 ms y **ni un solo periodo
   por encima de 90 ms**. Si la cámara hubiera estado entregando 10 fps,
   `read()` habría bloqueado ~100 ms por frame y esos periodos aparecerían.

   **Evidencia 2 — la misma cámara, esa misma tarde, daba 10 fps.** Medido
   replicando la secuencia exacta del demo (formato antes que resolución) y con
   12 s de calentamiento: 10.0 fps en la integrada, 12.5 en la externa. Con
   brillo medio de 84/255 y 10/255 respectivamente.

   **Dos hipótesis descartadas por el camino**, que conviene no volver a probar:

   - **El formato NO es la causa** (esto refuta la **H27** tal como se formuló).
     Se le añadió a `list_cameras.py` la opción `--fourcc`, que ahora mide con
     MJPG por defecto igual que el pipeline, y **la cifra no cambió**: sigue
     dando 10-12.5 fps. El arreglo de la herramienta se conserva igualmente,
     porque medir en una configuración distinta de la de producción es un error
     de método aunque en este caso no fuera la causa.
   - **El calentamiento tampoco.** A los 12 s de rodar sigue en 10 fps.

   La exposición manual no se pudo forzar para cerrar el círculo: esa cámara no
   acepta `CAP_PROP_EXPOSURE` por DSHOW y devuelve imagen negra. El mecanismo
   —poca luz, exposición larga, menos frames por segundo— es el que documenta el
   propio `fps_reales()` de la herramienta, y es estándar en webcams USB.

   **Consecuencias, que son tres y ninguna mala:**

   1. **La limitación de «~15 Hz por hardware» se retira.** No existe tal techo:
      era una medida tomada con poca luz y elevada a propiedad del equipo.
   2. **Los 20 Hz de publicación son alcanzables**, y de hecho la sesión de
      calibración los superó con margen. Lo que hay que declarar en la tesis no
      es un techo sino una **condición de operación**.
   3. **La iluminación pasa a ser requisito del puesto**, junto a la distancia al
      monitor. Es defendible y hasta esperable: un sistema de visión que pide luz
      suficiente no sorprende a nadie, y el número que se reporte tiene que ir
      con las condiciones en que se midió.

   **Lo único que queda por medir**, y solo lo puede hacer el operador: repetir
   `list_cameras.py --medir` con la sala iluminada como en la sesión de
   calibración, y anotar los fps junto al brillo. Con eso se puede escribir «a
   partir de X de brillo medio, la captura sostiene 30 fps», que es una frase
   mucho más fuerte que cualquiera de las dos que este documento llegó a tener.

5. **MSMF se cuelga al abrir, y falla a 720p.** `cv2.CAP_MSMF` bloquea
   indefinidamente al abrir algunas webcams. El remedio es la variable
   `OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS=0`, **fijada antes de importar
   cv2** — el paquete lo hace en su `__init__.py` por eso. Con ella, ocho
   aperturas seguidas sin un solo bloqueo.

   Aun así, MSMF revienta al pedir 1280x720 en estas cámaras (aserción interna
   de OpenCV) y no aporta ventaja a 640x480. **Se mantiene `dshow` por
   defecto.**

6. **El índice de cámara CAMBIA entre sesiones.** Windows enumera por orden de
   aparición del dispositivo: al reconectar o reiniciar, la integrada y la
   externa se intercambian. Conviene ejecutar `tools/list_cameras.py` al
   empezar. El PROMPT 10 debería seleccionar por **nombre**, no por número.

7. **Avisos de arranque que NO hay que arreglar.** Todos vienen de dentro de
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
4. Servir el visor, desde la raíz del repositorio:

   ```
   python -m http.server 8000 --directory telmeow_viewer
   ```

   y abrir `http://localhost:8000/telmeow_viewer.html`. **NUNCA con `file://`**
   — los módulos ES se bloquean por CORS. `--directory` evita tener que moverse
   de carpeta, que es cómodo porque el tracker se lanza desde la raíz. Si sale
   un 404, casi siempre es que el servidor se lanzó desde otro sitio: sirve la
   carpeta desde la que se arrancó, y los STL se piden con rutas relativas al
   HTML.
5. Conectar el visor a `ws://localhost:9001`, topic `telmeow/teleop/pose`. El
   brazo debe oscilar. ✅ **verificado**
6. `python tools/mqtt_monitor.py` para ver el JSON crudo.
7. `python -m telmeow_teleop.head_tracker --publish` — la cabeza del operador
   mueve la del robot en el visor. Es lo que cierra el P4.1, y funciona sin
   esperar al P7. Admite `--record` a la vez: publica y graba en la misma pasada.
   Si el CSV de destino ya existe, **la grabación anterior se conserva** con el
   sufijo `_anterior`; no se pisa (se perdió una así el 2026-09-06).
8. `python -m telmeow_teleop.main` con la cámara. *(tras P7)*
9. `python -m telmeow_teleop.control_panel`. *(tras P10)*
10. En la Raspberry: `python raspberry/story_display.py --windowed`. *(tras P11)*

Sin Mosquitto disponible: `python tools/dev_broker.py` levanta un broker
equivalente en Python puro, sin privilegios de administrador.

---

# PARTE 5 — Preguntas probables del jurado

- **«¿Cómo valida que los ángulos estimados corresponden a los reales?»**
  No hay ground truth con una sola cámara. Reportar repetibilidad y declarar la
  ausencia de validación absoluta como limitación.

- **«¿Por qué la flexión es menos confiable que la abducción?»** *(reformulada)*
  Porque la abducción cae en el **plano de imagen**, donde MediaPipe es preciso,
  mientras que la flexión depende de la **coordenada de profundidad**, que es la
  componente débil de un estimador monocular. La respuesta anterior —«la
  rotación axial del húmero es casi no observable»— describía un grado de
  libertad que el robot no tiene, y se retira (H28).

- **«¿Por qué su robot no reproduce la rotación del brazo?»** *(nueva)*
  Porque no puede: el hombro tiene dos grados de libertad y el brazo es un
  eslabón rígido sin codo. No es una limitación del estimador sino del
  mecanismo, y se declara como tal. El sistema reproduce **hacia dónde apunta**
  el brazo, no su forma.

- **«¿Por qué mide el brazo entero y no el ángulo del hombro?»** *(nueva)*
  Es un retargeting deliberado. El brazo del robot es un eslabón único, así que
  lo que hay que conservar es la dirección de apuntado, que es lo que el
  espectador interpreta. Y en la práctica manda el encuadre: sentado a la
  distancia de trabajo, el codo del operador sale de cuadro mucho antes que la
  mano. La alternativa (hombro→codo, el ángulo articular estricto) está
  implementada y conmutable, y la elección se hizo con datos.

- **«¿Cómo pasa de un brazo humano, con más grados de libertad, a uno de dos?»** *(nueva, P5.3)*
  No pasa ángulos: pasa **la dirección** en que apunta el brazo. Se filtra como
  vector, se escala dentro de la envolvente medida del robot (que depende de
  hacia dónde apunte) y los ángulos del cardán se calculan al final, con una
  sola rama de la cinemática inversa. Pasar ángulos articulares tenía una
  costura justo en «brazo al frente» que hacía dar la vuelta al brazo; está
  medido (H35) y se muestra con la figura de `tools/comparar_mapeo_brazos.py`.

- **«¿Por qué extendió la flexión a −180° si su barrido llegaba a ±90°?»** *(nueva)*
  Porque ±90 era el rango del slider, no del servo, y porque con ±90 la costura
  cae en el espacio de trabajo. Fuera de ±90 no hace falta barrer: `(a, f)` y
  `(−a, f ± 180)` son la misma dirección del brazo, y el brazo es simétrico
  alrededor de su eje. Lo que se verificó aparte es la unión del hombro.

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

- **«¿A qué tasa va su sistema, y de qué depende?»**
  El transporte MQTT sostiene 20 Hz, verificado, y la captura sostuvo 30 fps
  durante los 142 s de la sesión de calibración, sin un solo frame duplicado. Lo
  interesante es de qué depende: **de la iluminación**. La misma cámara da 10 fps
  con poca luz, porque alarga la exposición. No es una limitación del método
  —MediaPipe consume 5-7 ms por frame, el 7 % del presupuesto— sino una
  condición de operación del puesto, igual que la distancia al monitor. Se
  reporta la tasa junto con las condiciones en que se midió.

- **«¿Por qué usa una ganancia mayor que 1? ¿No está exagerando el
  movimiento?»** *(nueva)*
  Sí, deliberadamente. Sin gafas de realidad virtual, el operador tiene que
  seguir viendo su monitor mientras teleopera: si girase la cabeza los 50° que
  necesita el robot, perdería de vista justo la imagen que necesita mirar. La
  ganancia traduce un gesto pequeño y cómodo al recorrido del robot. Es una
  decisión de **diseño de interacción**, y su precio está cuantificado: amplifica
  el temblor en la misma proporción (§3.3.10).

- **«Su método no es el solvePnP que describe el marco teórico.»** *(nueva)*
  Correcto: se implementaron los dos y se eligió el mejor con datos. La
  alineación de la malla completa por descomposición SVD (Kabsch) da 7.7× menos
  temblor en el giro lateral que solvePnP sobre 6 puntos, porque con solo 6
  correspondencias hay configuraciones casi degeneradas. Los dos siguen en el
  código, conmutables, y la comparación es reproducible (§7.7).

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
from telmeow_teleop.limits import (clamp_arm, clamp_head, Side,
                                   arm_abduccion_range,
                                   ARM_ABDUCCION_1D, FLEXION_RANGE)

a = clamp_arm(abduccion, flexion, "izq")   # ArmClamp
a.abduccion.value / .lower / .upper / .fraction / .clamped
a.flexion.value ; a.as_tuple ; a.clamped

h = clamp_head(pan, tilt)              # HeadClamp
h.pan.value ; h.tilt.value ; h.as_tuple ; h.clamped
```

Lanzan `ValueError` ante NaN/infinito. `Side.parse` acepta L/R, left/right,
izq/der; `Side.opposite` da el otro lado.

⚠️ **Renombrado el 2026-09-06 (H28).** `balanceo` → `abduccion`, `giro` →
`flexion`, `GIRO_RANGE` → `FLEXION_RANGE`, `ARM_BALANCEO_1D` →
`ARM_ABDUCCION_1D`, `arm_balanceo_range` → `arm_abduccion_range`,
`BalanceoPolicy` → `AbduccionPolicy`, y la variable de entorno
`TELMEOW_BALANCEO_POLICY` → `TELMEOW_ABDUCCION_POLICY`. **El contrato MQTT no
cambia.**

## `filters.py`

```python
from telmeow_teleop.filters import (OneEuroFilter, EMAFilter, SlewLimiter,
                                    ClassHysteresis)

f = OneEuroFilter(min_cutoff=0.5, beta=0.5)
f(valor, timestamp)          # timestamp de perf_counter; opcional
f.degenerate_dt_count        # cuenta los dt <= 0 sustituidos
f.reset()

s = SlewLimiter(max_rate=180.0)   # grados/s · limita VELOCIDAD, no ruido
s(objetivo, timestamp) -> float   # avanza lo que el limite permita
s.reset(valor)               # fija la salida sin rampa (arrancar en reposo)
s.value ; s.initialized ; s.degenerate_dt_count

h = ClassHysteresis(n_frames=5, min_confidence=0.6, initial="neutral")
h.update(etiqueta, confianza) -> etiqueta estabilizada
h.current ; h.window
```

⚠️ **`SlewLimiter` no sustituye al One Euro y no filtra ruido**: no toca las
muestras que cambian despacio. Está para las **discontinuidades**, que son
justo lo que el One Euro no puede filtrar —tras un hueco largo su `dt` es
grande, su factor de suavizado se va a 1 y la muestra pasa entera—. Un salto de
`d` unidades tarda `d / max_rate` segundos, así que `max_rate` se elige **por
encima de la velocidad del gesto** para que no añada retardo al movimiento
normal. Lo usa `arm_tracker.comando`.

## `camera.py`

```python
from telmeow_teleop.camera import (abrir_camara, describir, brillo_medio,
                                   BACKENDS, AUTO_EXPOSURE_ON, AUTO_EXPOSURE_OFF)

cap = abrir_camara(cfg.camera)     # None si no se abre; NO lanza
if cap is None: ...
print(describir(cap, cfg.camera))  # lo CONCEDIDO, no lo pedido (H19)
brillo_medio(frame)                # 0-255 · por debajo de 8 no hay imagen (H34)
```

Existe porque abrir una webcam en Windows tiene cuatro trampas ya pagadas, y
estaban **duplicadas** en los dos trackers, así que arreglar una dejaba la otra
rota:

1. **MSMF se cuelga** en algunas máquinas con las protecciones de Windows
   activadas. El defecto es DirectShow.
2. **El FOURCC antes que la resolución.** Al revés no siempre se aplica, y sin
   MJPG muchas webcams bajan a 10 fps a 1280x720 sin avisar.
3. **La exposición sobrevive al proceso** (H34): DirectShow la guarda en el
   driver, así que se pide **siempre y explícitamente**. Por defecto la
   automática; `TELMEOW_CAMERA_AUTO_EXPOSURE=0` + `TELMEOW_CAMERA_EXPOSURE=-2`
   la fija a mano para una sesión que necesite tasa constante.
4. **Los valores de autoexposición son 0.75 y 0.25**, no 1 y 0, y no están en la
   documentación de OpenCV. Invertirlos deja la cámara ciega sin error.

## `head_tracker.py`

```python
from telmeow_teleop.head_tracker import (HeadTracker, estimate_head_pose,
                                         rotation_between_clouds)

t = HeadTracker(gain_pan=1.0, gain_tilt_up=1.0, gain_tilt_down=1.0,
                method="mesh",              # "mesh" (mejor) o "pnp"
                invert_pan=False, invert_tilt=False)
t.process(frame_rgb) -> (pan, tilt) | None   # None = sin rostro o pose inválida
t.map_to_robot(yaw, pitch, timestamp=None)   # la cadena, sin cámara: testeable
t.set_neutral() / t.clear_neutral()
t.last_clamp        # HeadClamp, con lower/upper/fraction/clamped para el HUD
t.raw_angles        # ángulos crudos del estimador, antes de todo
t.raw_extremes      # {"pan": (min,max), "tilt": (min,max)} · para calibrar
t.reset_extremes()
t.suggested_gains() # {"pan", "tilt_up", "tilt_down"} · el pan apunta a ±50 (§3.2.1)
t.face_scale        # px entre comisuras externas de los ojos ≈ distancia
t.frames_sin_rostro ; t.degenerate_dt_count
t.close()           # o usarlo como gestor de contexto

estimate_head_pose(puntos_2d, (ancho, alto)) -> (yaw, pitch, roll) | None
```

**Dos métodos, y `mesh` es el bueno.** `pnp` resuelve solvePnP sobre 6 puntos;
`mesh` alinea la nube completa de landmarks contra la canónica por Kabsch (SVD).
Medido: 7.7× menos temblor en el pan. `pnp` se conserva porque es el que describe
el documento de tesis y sirve de comparación.

⚠️ **En `mesh`, la referencia se autocaptura con el primer rostro que ve.** Es
deliberado —así funciona sin pedir nada— pero significa que si el operador no
está de frente al arrancar, **todo queda sesgado hasta que pulse `c`**. Cualquier
medición debe empezar por calibrar el neutro. En `mesh`, `set_neutral()` no
guarda un origen numérico: **recaptura la nube de referencia**, o sea el molde
del rostro concreto del operador. Por eso `raw_angles` en ese modo ya viene
referido al neutro, y los extremos de `raw_extremes` son directamente el
recorrido del operador desde su postura natural.

Modo demo con instrumentación: `python -m telmeow_teleop.head_tracker
--record archivo.csv`, y luego `python tools/analizar_registro.py archivo.csv`.
Teclas: `c` neutro · `n` borrarlo · `r` reiniciar extremos · `z/Z` `v/V` `a/A`
ganancias · `t` alternar método · ESPACIO cambiar de etiqueta · `q` salir.

## `arm_tracker.py`

```python
from telmeow_teleop.arm_tracker import (ArmTracker, ArmAngles, arm_angles,
                                        torso_basis, canal_del_robot,
                                        LANDMARKS, INCLINACION_FLEXION, CANALES)

t = ArmTracker(mirror=True,            # brazo izq. FISICO del operador -> armL
               frame_espejado=True,    # = cfg.camera.flip · hecho, no politica
               target="mano",          # "mano" (por defecto) o "codo"
               invert_abduccion=False, invert_flexion=False,  # verificado, H30
               reposo_abduccion=-55.0, reposo_flexion=0.0,    # NO es el cero
               gracia_s=0.5, velocidad_max=180.0)             # grados/s
t.process(frame_rgb) -> ArmAngles | None      # ESTIMACION · None = sin pose
t.comando(pose, timestamp=None) -> ArmAngles  # COMANDO · nunca tiene huecos
t.map_to_robot(canal, abduccion, flexion, timestamp=None)  # sin camara: testeable
t.last_clamp        # {Side: ArmClamp} · lower/upper/fraction/clamped para el HUD
t.raw_angles        # {Side: (abduccion, flexion)} crudos del operador
t.raw_extremes ; t.reset_extremes() ; t.suggested_gains()
t.estimado          # {Side: bool} · si ese canal se MIDIO en el ultimo frame
t.objetivos         # {Side: (abd, flex)} objetivo vigente, antes del limite
t.reposo            # {Side: (abd, flex)} ya recortado a lo que el robot admite
t.visibilidades     # {landmark: score} · el dato que decide mano-vs-codo
t.origen_tronco     # "caderas" | "retenido" | "reserva"  (H31)
t.tronco_de_caderas # True solo si se vieron EN ESTE FRAME
t.edad_tronco_s     # segundos desde la ultima actualizacion de caderas
t.landmarks_imagen  # para dibujar el esqueleto
t.frames_sin_pose ; t.degenerate_dt_count
t.close()           # o usarlo como gestor de contexto

# Funciones puras, probables sin camara ni MediaPipe:
torso_basis(hombro, hombro_contrario, superior) -> (u_lat, u_ant, u_sup) | None
arm_angles(hombro, destino, base, inclinacion=0.0) -> (abduccion, flexion) | None
canal_del_robot(etiqueta, frame_espejado=, mirror=) -> Side
```

`ArmAngles` lleva `armL`, `armLtw`, `armR`, `armRtw`, cada uno `float | None`,
más `.as_tuple` y `.vacio`. En la **estimación** un canal en `None` significa
«no se pudo medir»; en el **comando** no hay `None` nunca.

⚠️ **`process()` y `comando()` no son lo mismo, y la distinción es deliberada.**
`process()` devuelve lo **medido**, con huecos donde no se pudo medir.
`comando()` devuelve lo que se le **manda** al robot, sin huecos: mantiene el
último objetivo `gracia_s` segundos, luego vuelve a la pose de reposo, y todo
ello con un límite de velocidad angular. **Lo que se publica y lo que se graba
en las columnas `arm*` es el comando**; `crudo_*` es la medida, y `estimado_*`
dice cuál de los dos casos es cada fila. Mezclarlas dejaría un CSV en el que no
se puede distinguir un ángulo medido de una rampa, y el temblor calculado sobre
él no sería del estimador.

Lo que resuelve, y son dos cosas que se veían en el visor: un brazo perdido ya
no se queda **clavado a mitad de gesto**, y al recuperarlo ya no **salta** —el
One Euro no protege de eso, porque tras un hueco largo su `dt` es grande, su
factor de suavizado se va a 1 y la primera muestra pasa entera—.

Cinco cosas que conviene saber antes de tocarlo:

- **El cero de la abducción es el brazo EN CRUZ**, no colgando (H30). El
  operador con los brazos caídos pide −90°, que el recorte deja en −55°. Por eso
  la pose de reposo por defecto es −55° y **no** 0: volver al cero sería abrir
  los brazos en cruz cada vez que el operador baja las manos.
- **`origen_tronco` tiene tres valores, no dos** (H31). `tronco_de_caderas`
  significa «vistas **en este frame**», y una vertical retenida más de
  `RETENCION_TRONCO_S` (2 s) caduca y cae a la reserva. Antes la bandera se
  enclavaba y el HUD declaraba observado un valor congelado.
- **`arm_angles` puede devolver la flexión como `None`** aunque la abducción sea
  válida: es la singularidad del cardán, y **cae en la postura de reposo del
  robot** (brazo en cruz). Sin esa guarda, `atan2(0, -0.0)` devuelve **180°**
  —no un valor ruidoso: 180 exactos— y el robot pega un tirón. El tracker
  conserva la última flexión válida de ese canal.
- **`canal_del_robot` es el único sitio donde un lado del operador se ata a un
  canal del robot.** Todo lo demás —filtros, recorte, CSV, JSON— viaja ya en el
  marco del robot, así que la grabación y lo publicado no pueden discrepar.
- **`suggested_gains()` divide la abducción por `ARM_ABDUCCION_1D`**, no por el
  rango de la tabla, porque ese depende de la flexión. Es la misma separación
  que el P4.3 tuvo que introducir para el pan.

Modo demo: `python -m telmeow_teleop.arm_tracker --publish --record brazos.csv`,
y luego `python tools/analizar_registro_brazos.py brazos.csv`.
Teclas: `m` espejo · `t` mano/codo · `b` `f` invertir signos · `r` extremos ·
`z/Z x/X v/V n/N` ganancias · ESPACIO etiqueta · `q` salir.

El HUD marca con `~` los canales cuyo valor **no** viene de una medida de ese
frame, e imprime `tronco:` con el estado y, si está retenido, su edad. **Imprime
el dato crudo junto a su interpretación** (`cadera 0.00` al lado de `tronco: …`)
a propósito: es lo que permitió detectar el H31.

Columnas del CSV, y la distinción importa al leerlo: `crudo_*` es la **medida**
(vacío si no se pudo medir), `arm*` es el **comando publicado** (nunca vacío),
`estimado_L`/`estimado_R` dicen cuál de los dos casos es cada fila, y
`tronco_origen` de dónde salía la vertical. Los sufijos `_L`/`_R` son **siempre**
el canal del robot, también en `crudo_*` y `vis_*`.

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

Rastro de auditoría de lo que se encontró al ejecutar los PROMPT 1 al 4. Sus
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

**Cambio hecho en el visor el 2026-09-06 (H23).** El indicador `#t-col` pasa a
tener un solo escritor, `pintarInterferencia()`, llamado desde `checkCollisions()`
y desde el manejador de MQTT. Antes escribían los dos directamente y el campo
parpadeaba entre `libre` y `sin contacto`.

✅ **El visor ya está bajo control de versiones.** El 2026-09-06 se movió a
`telmeow_viewer/` dentro del repositorio, con sus 40 STL y las texturas de los
ojos. Antes vivía suelto en `..\04_TelmeowViewer` y cualquier edición era
irreversible.

## 7.7 Sobre el head tracker (H17, H18)

**H17 — El eje de referencia estaba a 180° del sitio.** Los ángulos de Euler se
extraían respecto a la rotación identidad, que en el sistema de coordenadas de la
cámara corresponde a **una cara mirando hacia atrás**: una postura que ningún
operador puede adoptar y que ninguna prueba sintética delató, porque las pruebas
usaban esa misma identidad como referencia. Con el operador real se manifestaba
como temblor, saltos de ±180° y el valor pegándose a los topes.

La causa es el **desbordamiento de ±180°** en la extracción de Euler: cerca de la
referencia todo va bien, pero a media vuelta de ella cualquier ruido hace saltar
el ángulo de un extremo al otro del círculo. Corregido componiendo con
`FRONTAL_ROTATION` (media vuelta en Y) antes de extraer los ángulos, lo que pone
la referencia en la cara de frente, donde el operador realmente está.

Lección metodológica: **una prueba sintética que fija su propia referencia no
puede detectar que la referencia sea la equivocada.** El error solo aparece con
datos reales.

**H18 — Kabsch sobre la malla completa bate a solvePnP sobre 6 puntos.** No
estaba en el plan; lo sugirió el operador al notar el temblor. Alinear la nube
entera de landmarks contra la canónica por descomposición SVD da **7.7× menos
temblor en el pan** (0.19° frente a 1.47°) y empata en tilt. La ventaja se
concentra justo donde PnP con 6 puntos tiene soluciones casi degeneradas.

Se conservan los dos métodos, conmutables con la tecla `t`: `pnp` es el que
describe el documento de tesis, y tenerlo al lado convierte el cambio en un
**resultado medido** en vez de en una decisión sin justificar.

## 7.8 Sobre la cámara (H19, H20, H21)

**H19 — Medir `read()` no mide fps.** Afirmé que MSMF daba 39.8 fps frente a los
10 de DSHOW, y que ahí estaba el arreglo. **Era falso: estaba contando frames
duplicados.** `cv2.VideoCapture.read()` puede devolver el mismo buffer una y otra
vez, así que contar lecturas produce cifras altísimas y sin sentido. Una
combinación que aparentaba **156 fps** entregaba 21 de cada 39 frames repetidos:
su tasa real era **3.7 fps**.

La única medida honesta es comparar cada frame con el anterior y contar solo los
distintos, que es lo que hace `tools/list_cameras.py --medir`
(`fps_reales()`, línea 50). **Aplicable a cualquier cifra de fps que se reporte
en la tesis.**

**H20 — Más resolución da menos fps, y la webcam externa no es mejor.** También
lo afirmé al revés, recomendando 1280x720 «para ganar definición». Medido:

| Cámara | Backend | 1280x720 | 640x480 |
|---|---|---|---|
| 0 (externa) | dshow | 10.0 | 12.5 |
| 0 (externa) | msmf | falla | 12.8 |
| 1 (integrada) | dshow | 8.0 | **15.0** |
| 1 (integrada) | msmf | falla | **15.3** |

Y el techo es del sensor, no del software: se probaron **MJPG antes y después de
fijar la resolución**, `CAP_PROP_BUFFERSIZE`, `CAP_PROP_FPS`,
`CAP_PROP_CONVERT_RGB` y cuatro variantes de exposición — los siete dieron
**79.7-80.2 ms, idénticos**. Un lector en hilo aparte confirmó 12.2 fps, lo que
descarta que sea latencia de `read()`: la cámara **produce** a esa tasa. El
commit `4fc8b5b`, que pedía MJPG como arreglo, no arregló nada: la cámara lo
ignora y sigue en YUY2.

Consecuencias en §3.3.9 y §4.0.4.

**H21 — MSMF se cuelga al abrir la cámara.** Bloqueo indefinido, sin imprimir
nada, tanto en un banco de pruebas como en la aplicación real. El remedio es
`OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS=0` **fijada antes de importar cv2** —
OpenCV lee la variable al cargar su módulo de vídeo, así que ponerla después no
sirve de nada. Por eso vive en `configure_video_backend()`, que se ejecuta al
importar el paquete (`telmeow_teleop/__init__.py:24`).

Con ella, ocho aperturas seguidas sin un solo bloqueo. Aun así MSMF revienta a
1280x720 con una aserción interna, y no aporta ventaja a 640x480: **se mantiene
`dshow` por defecto.**

## 7.9 La sesión del 2026-09-06: el tilt iba al revés

**H22 — El método `mesh` invertía el cabeceo.** El operador levantaba la cabeza
y el robot la bajaba. La causa no estaba en el visor ni en los límites, sino en
que los dos estimadores descomponían la rotación en sistemas de coordenadas
**cuya `y` apunta al revés**: `landmarks_to_cloud` construía la nube con la
convención de la imagen (`y` hacia abajo, que es como MediaPipe entrega los
landmarks), mientras que `MODEL_POINTS_3D`, el modelo que resuelve `solvePnP`,
tiene la `y` hacia arriba (mentón en −330, ojos en +170).

Invertir un eje de esa manera **cambia el signo del pitch y deja el yaw
intacto**, que es exactamente el síntoma observado: el pan se comportaba bien y
solo fallaba el tilt. Comprobado numéricamente antes de tocar nada — la misma
rotación de 20° da `pitch +20.00` en un sistema y `pitch −20.00` en el otro,
mientras que un giro lateral da `yaw +20.00` en los dos.

Se corrigió en la raíz, negando la `y` al construir la nube. **No hubo que tocar
ni `HEAD_TILT_RANGE` ni el visor**: se verificó pidiéndole al visor ángulos
fuera de rango y leyendo dónde recorta por colisión geométrica —**−39.7° y
+42.5°**, que son los límites que ya declaraba `limits.py`— y comprobando de
perfil que el negativo baja la cabeza.

Lo importante para la tesis no es el fallo, es **por qué las 400 pruebas no lo
vieron**: solo fijaban el signo del camino `pnp`, y el método por defecto es
`mesh`. Es el mismo modo de fallo del **H17**, repetido: *una prueba sintética
solo vale si algo externo ata su convenio al del mundo real.* Ahora hay tres
pruebas que atan los dos métodos entre sí, de modo que si alguien vuelve a tocar
los ejes de la nube, la prueba dice además **cuál de los dos canales** se rompió.

**H23 — Un indicador con dos dueños parpadea.** El campo «Interferencia» del
visor alternaba a ojo entre `libre` y `sin contacto`. No era un fallo de
detección: lo escribían **dos sitios distintos** —el bucle de render cada 120 ms
y el manejador de MQTT a 20 Hz— con dos vocabularios para el mismo estado. Son
dos hechos distintos («ahora hay contacto» y «la pose que llegó se recortó»), así
que la solución no era borrar uno, sino **un solo pintor por prioridad** llamado
desde los dos eventos.

Y hay un detalle que solo apareció al medirlo: dejar el indicador colgando del
bucle de render lo **congela** cuando la pestaña deja de estar visible, porque
el navegador para `requestAnimationFrame`. El aviso de recorte es información de
seguridad y no puede depender de que alguien esté mirando; por eso se pinta
también desde el mensaje de MQTT, que llega por WebSocket y no se throttlea.

**H24 — En una postura sostenida, el extremo es ruido.** `tools/analizar_registro.py`
proponía las ganancias dividiendo entre el mínimo y el máximo de cada etiqueta.
En una etiqueta de postura mantenida, esos dos valores son **el instante de
entrar y el de salir**, no la postura. El percentil sí recoge la meseta, que es
donde se pasó el tiempo. La herramienta usa ahora p05/p95, y además **avisa
cuando una etiqueta pasó demasiado tiempo lejos de la meseta**: pasada sobre
`limites.csv` marca sola la etiqueta 5, que era justo la que se había descartado
a mano por inservible.

## 7.10 La calibración del 2026-09-06 (H25, H26, H27)

Dos sesiones de grabación con el protocolo del P4.2, una por cámara. De ahí
salen las ganancias y tres cosas que conviene no olvidar.

**H25 — El temblor crudo y el filtrado ordenan las cámaras al revés.** La cámara
externa gana en crudo (0.146° frente a 0.159° en pan) y **pierde tras el One
Euro** (0.098° frente a 0.058°). Si la comparación se hubiera hecho con el dato
crudo —que es el reflejo natural— se habría elegido la cámara equivocada. **La
cifra que decide es la que llega al robot**, no la que sale del estimador. Vale
para cualquier comparación de hardware que quede en el proyecto.

La decisión final, además, no la ganó ninguna de las dos por precisión sino por
**encuadre**: la externa recorta demasiado para meter los brazos del PROMPT 5. La
configuración se elige para el sistema completo, no para el módulo que se está
midiendo ese día.

**H26 — Una grabación con el protocolo equivocado no se rescata releyéndola.**
`limites.csv` se intentó reinterpretar dos veces: primero rescatando la meseta
por percentiles, después corrigiendo el signo del tilt. La segunda relectura
produjo una afirmación **falsa** —que al operador le costaba más levantar la
cabeza que bajarla— que estuvo un día en este documento y en tres docstrings,
hasta que dos sesiones nuevas la contradijeron. El dato bueno costó diez minutos
de grabación; la relectura costó más que eso y dejó una cifra equivocada por
medio. **Cuando el protocolo fue el equivocado, se vuelve a medir.**

**H27 — El techo de captura lo pone la luz, no el sensor.** Detallado en §4.0.4
con las dos mediciones. El resumen: la misma cámara, con el mismo código, da
**30 fps con buena iluminación y 10 con poca**, porque la exposición larga es lo
que limita. La «limitación de ~15 Hz por hardware» que este plan declaró durante
un mes **nunca existió**: era una medición tomada con poca luz y ascendida a
propiedad del equipo.

Dos lecciones, y la segunda es la que más va a doler si se olvida:

- **Una medición no es una propiedad.** «El sensor produce a 15 fps» era una
  inferencia, no un dato; el dato era «esta tarde, con esta luz, salieron 15».
  La diferencia no se ve al escribirla y sí al citarla en la tesis.
- **Una herramienta de diagnóstico que no replica la configuración de producción
  induce a error con toda la autoridad de una medición.** `list_cameras.py`
  medía sin fijar el formato mientras el pipeline fija MJPG. Resultó **no** ser
  la causa aquí —se comprobó, y la cifra no cambia—, pero el defecto de método
  era real y está corregido: la herramienta ahora mide con el formato del
  pipeline por defecto y acepta `--fourcc` para comparar.

## 7.13 La sesión con el visor del 2026-09-07 (H30, y el H29 retractado)

Es el caso más limpio que ha dado este proyecto de por qué la verificación en el
visor no es opcional, y conviene leerlo entero antes del PROMPT 7.

**H30 — El reposo del robot tiene los brazos en cruz, y eso lo cambia todo.**
Todo el módulo se escribió suponiendo que el brazo en reposo **cuelga**. Se
verificó en el visor y **no**: con los seis canales a cero, el robot está de
brazos abiertos, en horizontal. Tres consecuencias, cada una medida:

1. **`armL`/`armR` son elevación respecto a la horizontal**, no respecto a la
   vertical. Con eso, los topes de `limits.py` se leen solos: −55° es lo que el
   brazo **baja** desde la horizontal y +30° lo que **sube** antes de chocar con
   la cabeza. Comprobado con −50 (baja) y +30 (sube). **El robot no puede dejar
   los brazos caídos**, y el operador con los brazos en reposo pide −90°, que el
   recorte deja en −55°. Es una limitación del mecanismo y hay que declararla.
2. **La dirección de reposo va sobre el propio eje de la flexión.** Verificado:
   con `armR = 0`, barrer `armRtw` de 0 a 90° **no mueve el brazo ni un píxel**,
   solo gira la pieza del hombro sobre sí misma. O sea que **la singularidad del
   cardán cae exactamente en la postura de reposo del robot**: ahí el brazo
   tiene un solo grado de libertad útil. Es lo que obliga a que `arm_angles`
   devuelva la flexión como `None` en esa franja, en vez de un ±180 que daría
   un tirón.
3. **«Flexión positiva = adelante» no es cierto ni falso: depende.** La
   componente antero-posterior vale `sin(abducción)·sin(flexión)`, así que se
   invierte según el brazo esté por encima o por debajo de la horizontal. Con el
   brazo bajado 50°, la flexión **negativa** va hacia adelante; con el brazo
   subido 20°, la que va adelante es la **positiva**. Medidas las dos, y la
   transcripción del rig las reproduce.

**El H29 queda retractado, y merece quedar escrito por qué.** Deduje sobre el
papel que `+armRtw` llevaba el brazo hacia atrás, con tres anclas —dos de ellas
verificadas en vivo— y llegué a fijar `ARM_INVERT_FLEXION = True`. El
razonamiento no tenía ningún error de aritmética: partía de un supuesto tácito
que nunca comprobé, el del brazo colgando. **Con anclas correctas y un supuesto
falso se llega a una conclusión falsa con toda la apariencia de rigor.** Es la
tercera vez en este proyecto (H17, H22, H29) y siempre el mismo patrón.

Lo que sí funcionó fue el diseño defensivo: el sentido era un **parámetro**
(`invert_flexion`), no una constante, así que retractarlo costó cambiar un
`True` por un `False` y no reescribir el módulo. Eso conviene conservarlo.

**Y la prueba contra el rig pasaba en verde con la geometría equivocada**, por
la misma razón de siempre: su modelo directo usaba el mismo reposo falso que el
estimador, así que cerraba consigo misma. Ahora el reposo sale de los ejes del
rig (`REPOSO_VISOR`) y hay una prueba que fija «el cero es el brazo en cruz».

Dos cosas más que salieron de la sesión y que valen para cualquiera que vuelva a
abrir el visor:

- **La amortiguación de la cámara dura decenas de segundos**, porque el visor
  renderiza a pocos fps cuando el panel no tiene el foco: la inercia decae por
  frame, no por tiempo. Comparar dos capturas separadas por segundos **no es
  fiable**. Lo que sí funciona es capturar los dos estados **seguidos**, sin
  esperas, y volver al estado inicial para comprobar que la escena reproduce el
  primer fotograma.
- **Un valor pedido puede no ser el aplicado.** Con el cabeceo a −39°, pedir
  `pan = 75` dejó el slider en 1.5°: el recorte por colisión del propio visor.
  Hay que leer el valor de vuelta, no darlo por puesto.

## 7.12 El PROMPT 5: los brazos no hacen lo que el plan decía (H28, H29)

**H28 — El robot no tiene rotación axial, y `giro` es flexión.** El enunciado
del PROMPT 5 pedía estimar la «rotación axial del húmero» desde el vector
codo→muñeca y mandarla al canal `tw`. Los ejes del rig
(`telmeow_viewer.html:270-277`) dicen otra cosa:

| Canal | Eje del servo | Plano | Movimiento |
|---|---|---|---|
| `armL`/`armR` | antero-posterior `V(0,∓1,0)` | frontal | **abducción / aducción** |
| `armLtw`/`armRtw` | latero-lateral `V(-0.985,0,±0.17)` | sagital | **flexión / extensión** |

Un eje latero-lateral en el hombro no puede producir rotación axial: produce
vaivén. El hombro es un **cardán de 2 GDL** que orienta un brazo rígido de un
solo eslabón, y no hay tercer eje. Tres confirmaciones independientes:

1. §0.5 ya decía que el brazo «gana recorrido al **despejarse del tronco**». Una
   torsión no despeja nada; un vaivén sí.
2. Las tablas 2D lo cuantifican: con `tw`=0 el otro canal llega a +30, con
   `tw`=±90 llega a +70/75.
3. El saludo del visor combina `armRtw`=50 con `armR`=30, que como flexión +
   abducción es un saludo y como torsión de 50° no lo sería.

Consecuencias: se renombró el vocabulario en Python y en los rótulos del visor
(§6, `limits.py`), y se **reescribió la limitación §3.3.1**, que describía un
grado de libertad inexistente. La conclusión práctica sobrevive —la flexión es
el canal menos fiable— pero por una razón que además es **medible**: la
abducción vive en el plano de imagen y la flexión en la coordenada de
profundidad, que es la componente débil de BlazePose.

El sufijo `tw` (*twist*) queda como vocabulario heredado en el JSON, porque
cambiarlo rompería el contrato con el visor. Está documentado en los tres sitios
donde alguien podría tropezar con él.

**H29 — El canal `tw` va al revés de lo que dice el propio visor.** El
comentario del rig afirma «invertido: +θ = adelante en ambos brazos». La
geometría dice que `+armRtw` lleva el brazo hacia **atrás**, por tres vías:

1. `neckTilt` gira sobre `+x`, y está verificado **en vivo** que un tilt
   negativo baja la cabeza (H22) ⇒ el robot mira hacia `+y`.
2. `neckPan` gira sobre `−z`, y está verificado **en vivo** que un pan positivo
   gira el robot a su derecha (P4.1) ⇒ coincide: mira a `+y`, su derecha es
   `+x`, que es donde está `Brazo_Der`. Dos anclas distintas dando lo mismo.
3. Con esos ejes y `setJoint()`, que aplica el ángulo **sin negarlo**, un
   `armRtw` positivo mueve el brazo hacia `−y`.

Si es así, el saludo del demo está agitando el brazo por detrás del cuerpo.

**No se ha tocado ningún eje ni ningún valor.** Se hicieron tres cosas
reversibles: `ARM_INVERT_FLEXION` viene activado para compensarlo, el comentario
del visor queda marcado como en revisión, y la prueba que ata el estimador al
rig lo fija en una constante con nombre (`SENTIDO_FLEXION_VISOR`) que es lo
único que hay que cambiar si la comprobación dice lo contrario.

Falta esa comprobación, y es de un minuto: mover `s-arRtw` a +50 y mirar. Es
**P5.1**. Merece la pena decir por qué no se dio por cerrado sobre el papel: es
literalmente el modo de fallo del H17 y del H22, y las dos veces el papel decía
que todo estaba bien.

**Lo que sí se decidió sin esperar**, porque su ancla no está en duda: la
**abducción no se invierte**. `armR` positivo lleva el brazo hacia `+x`, que es
hacia afuera para el brazo derecho, y eso coincide con el «+θ eleva» del
comentario. De ahí que la corrección de la inclinación del eje (§0.5) se pueda
aplicar ya: su signo depende del sentido de la abducción, no del de la flexión.

## 7.14 El instrumento que mentía sobre sí mismo (H31)

**2026-09-13, primer arranque de la sesión P5.2.** El HUD del arm tracker
imprimía, en la misma línea:

```
tronco: caderas    cadera 0.00    codo L 0.50 R 0.39
```

Las dos cosas no pueden ser ciertas a la vez. `cadera 0.00` significa que las
caderas no se ven —estaban fuera de cuadro, con el operador sentado a la mesa—,
y aun así el HUD declaraba que la vertical del tronco salía de ellas.

**La causa**, en `arm_tracker._actualizar_tronco`: la bandera se ponía en `True`
el primer frame en que las caderas superaban el umbral, y **la única cosa que la
volvía a poner en `False` era no haber tenido nunca una vertical buena**. O sea
que tras el primer frame afortunado se quedaba puesta para siempre, mientras el
módulo seguía usando —correctamente— el último valor bueno.

**Por qué importa más de lo que parece.** El comportamiento *interno* era
razonable: retener la vertical es lo correcto, porque la orientación del tronco
es cuasi-estática y las caderas parpadean bajo el umbral con facilidad. Lo que
estaba roto era **lo que el instrumento decía de sí mismo**. Y ese error tiene
una vía directa a la tesis: la columna `tronco_caderas` del CSV escribía `1` en
todas las filas, así que `analizar_registro_brazos.py` habría informado de
«vertical del tronco tomada de las caderas en el 100 % de los frames» sobre una
grabación en la que no se vieron **ni una vez**. Una cifra falsa, concreta,
citable y sin nada en el informe que la contradijera.

**Corregido** con tres estados (`caderas` / `retenido Xs` / `RESERVA vertical`),
una caducidad de 2 s, la columna `tronco_origen` en el CSV y el desglose de los
tres en el analizador.

**La lección, que generaliza más allá de este caso:** una bandera que **se
enclava** —que puede pasar de `False` a `True` pero no al revés— convierte un
diagnóstico en una afirmación histórica sin avisar de que lo es. Cuando el HUD o
el CSV declaran el **origen** de un dato, ese indicador tiene que ser *fresco
en este frame* o decir explícitamente su edad. Es primo hermano del **H26**: una
grabación ambigua no se rescata releyéndola, así que el instrumento tiene que
grabar sin ambigüedad la primera vez.

Y una nota de método que sí funcionó: el fallo salió a la luz porque el HUD
imprime **el dato crudo junto a su interpretación** (`cadera 0.00` al lado de
`tronco: caderas`). Si solo hubiera impreso la conclusión, no habría habido
forma de notarlo. Merece la pena mantener ese patrón en los HUD que queden.

**La evidencia numérica**, de `brazos_2026-09-13_descartada.csv` (13.271
frames): `vis_cadera` superó el umbral en el **9.8 %** de los frames —mediana
0.03— mientras `tronco_caderas` valía 1 en el **86.5 %**. Las dos columnas del
mismo archivo se contradicen, y una de ellas es la que el informe citaba.

## 7.16 La cámara negra a 30 fps (H34)

**2026-09-13, en plena sesión.** La ventana del arm tracker salió **totalmente
negra**, pero el HUD marcaba **29.8 fps** y publicaba por MQTT sin perder
mensajes. `sin pose (464)`.

**Lo que se descartó primero**, y en este orden, porque el síntoma admite varias
causas:

| Comprobado | Resultado |
|---|---|
| Otro proceso con la cámara cogida | no había ninguno |
| Permiso de cámara de Windows (usuario y apps de escritorio) | concedido |
| Estado del dispositivo | `HP Wide Vision HD Camera`, OK, sin código de error |
| Índice de cámara | **solo existe el 0**; el 1 no se abre |
| ¿Algún filtro del propio código? | **no**: la medición se hizo con OpenCV a pelo, sin una línea del tracker |

**La causa era la exposición**, y el barrido lo deja sin ambigüedad:

| Ajuste | Brillo medio (0-255) |
|---|---|
| Tal cual lo encontró | **2.20** |
| Autoexposición forzada | **117.91** |
| Manual −4 | 3.93 |
| Manual −2 / 0 / +2 | ~106 |

La cámara estaba clavada en **exposición manual −6** —el obturador casi
cerrado— heredada de **otro proceso**. Los ajustes de DirectShow **viven en el
driver, no en el proceso**: lo que deja puesto una aplicación sigue ahí cuando
arranca la siguiente, y este código nunca tocaba la exposición, así que el
arranque dependía de lo último que hubiera usado la cámara.

**Por qué es peligroso más allá de la molestia:** el fallo **no se parece a un
fallo**. El dispositivo se abre, no hay excepción, los frames llegan a la tasa
nominal y el contador de MQTT sube. Lo único que se ve es que BlazePose no
detecta a nadie — o sea que apunta al detector, que es donde no está el
problema. Sin el barrido de exposición, la hipótesis natural habría sido «falla
el tracker».

**Es la otra cara del H27.** Allí se estableció que en esta cámara la luz manda
vía exposición, y que el techo de fps era una condición de operación y no una
limitación. Lo que faltaba es que **ese ajuste sobrevive al proceso**.

**Corregido** en un módulo nuevo, `telmeow_teleop/camera.py`, compartido por los
dos trackers —estaba duplicado en los dos, así que arreglar uno dejaba el otro
roto—:

- La exposición **se pide siempre y explícitamente**. Por defecto la automática,
  que es lo que hace el arranque reproducible.
- `TELMEOW_CAMERA_AUTO_EXPOSURE=0` + `TELMEOW_CAMERA_EXPOSURE=-2` la fija a
  mano, para una sesión que necesite **tasa constante**: la automática alarga el
  tiempo de integración cuando falta luz y con ello baja los fps (H27), así que
  fijarla cambia ruido por estabilidad.
- El HUD **avisa** con `CAMARA NEGRA (brillo X)` cuando lleva más de 30 frames
  sin pose y el brillo medio está por debajo de 8. Distingue «no te detecto» de
  «no veo nada», que son dos fallos distintos con la misma pinta.
- `describir()` **lee de vuelta** lo que la cámara concedió, no lo que se pidió.

Los valores de `CAP_PROP_AUTO_EXPOSURE` en DirectShow son **0.75 (automática) y
0.25 (manual)**, no 1 y 0, y no están en la documentación de OpenCV. Quedan
como constantes con nombre y con una prueba que se pone roja si alguien los
«normaliza», porque invertirlos deja la cámara ciega sin ningún error.

## 7.15 El agregador que sesgaba su propia comparación (H32)

**Misma sesión, mismo archivo.** El informe de visibilidad decía que el **codo
se ve el doble de bien que la mano** (89-92 % frente a 44-48 % de frames por
encima del umbral), lo contrario de lo que el operador había previsto y de lo
que este documento daba por bueno al elegir hombro→mano.

Antes de aceptarlo hubo que mirar **cómo se medía cada uno**, y ahí estaba el
problema: con `target="mano"` la visibilidad reportada era el **mínimo de los
cuatro** landmarks de mano (muñeca, meñique, índice, pulgar), mientras que
`target="codo"` reportaba la de **un solo** landmark. Y BlazePose estima los
dedos con bastante menos confianza que la muñeca incluso con la mano claramente
a la vista, así que el mínimo de cuatro es un estimador sistemáticamente
pesimista.

O sea: **el instrumento sesgaba justo la decisión que estaba puesto para
resolver**, y en la dirección de rechazar la opción que el plan había elegido.
No es que el resultado fuera falso —el codo puede verse mejor, está por medir—
es que la comparación no era una comparación.

**Corregido**: la puerta de `target="mano"` es la **muñeca** —un landmark, igual
que el codo—, y el centroide promedia los dedos que superen el umbral. Se añade
`puntos_destino_L`/`_R` al CSV con cuántos landmarks formaron el punto, porque
eso permite comprobar una afirmación que el plan hacía sin medir: que «cuatro
puntos de mano son más estables que la muñeca sola». Si el centroide colapsa
casi siempre a la muñeca, la afirmación no se puede defender, y el analizador
ahora lo avisa.

**La lección:** cuando dos alternativas se comparan con una métrica, **la
métrica tiene que medirse igual en las dos**. Un agregador distinto a cada lado
—mínimo de cuatro frente a valor único— produce una diferencia que parece del
mundo y es del instrumento. Es el pariente estadístico del H31: allí el
instrumento mentía sobre el origen de un dato, aquí sobre su comparabilidad.

## 7.11 Índice de hallazgos y dónde vive cada uno ahora

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
| **H17** | La referencia de Euler estaba a 180°, y las pruebas sintéticas no podían verlo | §7.7 |
| **H18** | Kabsch sobre la malla bate a solvePnP: 7.7× menos temblor en pan | P4, §7.7 |
| **H19** | Contar `read()` no mide fps; hay que contar frames distintos | §4.0.4, §7.8 |
| **H20** | Más resolución = menos fps; el techo es ~15 fps y es del sensor | §0.4, §3.3.9, §4.0.4, §7.8 |
| **H21** | MSMF se cuelga sin `MSMF_ENABLE_HW_TRANSFORMS=0` antes de importar cv2 | §4.0.5, §7.8 |
| **H22** | El método `mesh` devolvía el **tilt invertido**: la nube de MediaPipe tiene la `y` hacia abajo y el modelo del PnP hacia arriba | P4, §7.9 |
| **H23** | Un indicador escrito por dos sitios parpadea aunque los dos acierten | §7.9 |
| **H24** | En una postura sostenida, el percentil es el dato y el extremo es ruido | P4.4, §7.9 |
| **H25** | El temblor crudo y el filtrado **ordenan las cámaras al revés** | §4.0.4, §7.10 |
| **H26** | Una grabación con el protocolo equivocado no se rescata releyéndola | P4.4, §7.10 |
| **H27** | El techo de captura lo pone **la luz**, no el sensor: 30 fps con buena iluminación, 10 con poca | §0.4, §4.0.4, §7.10 |
| **H28** | El robot **no tiene rotación axial**: `giro` es flexión y `balanceo` es abducción. Vocabulario renombrado y §3.3.1 reescrita | §0.5, §3.3.1, P5, §6, §7.12 |
| **H29** | ~~El canal `tw` va al revés~~ · **RETRACTADO**: la deducción partía de un reposo falso. Lo sustituye el H30 | §7.13 |
| **H30** | El reposo del robot tiene los **brazos en cruz**, sobre el eje de la flexión: los topes son elevación, la **singularidad cae en el reposo**, y «flexión+ = adelante» depende de la abducción | §0.5, §3.3.1, P5, §7.13 |
| **H31** | Una bandera **enclavada** hacía que el HUD y el CSV declararan como observada una vertical de tronco **congelada** (9.8 % de caderas vistas frente a 86.5 % declarado). Un indicador de origen tiene que ser fresco o decir su edad | P5, §7.14 |
| **H32** | La visibilidad de la mano se medía con el **mínimo de cuatro** landmarks y la del codo con **uno**, así que la comparación mano-frente-a-codo estaba sesgada por el agregador. Puerta corregida a la muñeca | P5, §7.15 |
| **H33** | **Las ganancias sí hacen falta, y solo en abducción** (~0.33/0.51): el recorrido del hombro humano desborda el del robot, que se recortó en el 67-75 % de los frames. Consecuencia del H30. En flexión coinciden | P5, §0.5 |
| **H35** | **El brazo «daba la vuelta» por una costura del mapeo articular, no por la profundidad**: con flexión ±90, «al frente» tiene dos soluciones y se elegía por el signo de una componente que al frente es ruido (303 saltos en 9 min). Retargeting por dirección y flexión extendida a −180 | P5.3, §0.5 |
| **H36** | Las ganancias de abducción del H33 **no son ganancias**: son la envolvente del robot entre 90°. Se sustituyen por un escalado dependiente de la dirección que sale de la tabla | P5.3 |
| **H34** | **La exposición de la cámara sobrevive al proceso** (DirectShow la guarda en el driver) y dejó la imagen **negra a 30 fps sin ningún error**. Se pide explícitamente al abrir, y el HUD avisa del brillo | §4.0, P5, §7.16 |

Los hallazgos H17 a H21 son de la ejecución del **PROMPT 4** y de la
investigación de la cámara que salió de él. H19 y H20 son **correcciones a
afirmaciones mías anteriores**, y quedan escritas como tales a propósito: la
primera versión de esas cifras llegó a influir en la configuración recomendada.

H22 a H27 salen todos del mismo día, el 2026-09-06: la primera sesión con el
operador **delante del visor** y la calibración que vino después. Es el patrón a
retener de cara a los prompts que quedan — **el código llevaba 400 pruebas en
verde con el cabeceo invertido**, y lo destapó sentar a una persona delante.

Tres de esos hallazgos obligan a releer cifras que ya estaban escritas aquí: la
H22 (el signo del tilt), la H26 (`limites.csv` se descarta entera) y la H27 (la
tasa de captura queda en el aire). Esas relecturas están anotadas **donde
estaban las cifras**, no solo en esta tabla, porque el riesgo no es olvidar el
hallazgo sino citar el número viejo.
