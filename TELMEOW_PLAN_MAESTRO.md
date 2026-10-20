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
| **P5** — Arm tracker | ✅ **hecho, verificado y cerrado el 2026-10-19** · P5.3 (dirección), P5.4 (posición), P5.5 (híbrido, reposo, escorzo), P5.6 (validación: confianza en la mano, focal, alcance) y P5.7 (el robot saluda solo) · `model_complexity` medido: se queda el 1 (22 ms), el 0 de reserva para el P7 | `271034d`, `3c44883`, `10951e5` |
| **P6** — Clasificador de emoción | 🟡 **código hecho el 2026-10-19** · preprocesamiento idéntico al del entrenamiento bit a bit, orden de clases verificado contra las etiquetas del dataset · falta verlo con el operador en el visor (P6.1) | |
| P8 — Entrenamiento FER-2013 | ⬜ | |
| P7 — Integración | ⬜ | |
| P10 — Panel del operador | ⬜ | |
| P11 — Pantalla externa (RPi) | ⬜ | |
| P9 — Evaluación comparativa | ⬜ | |

**1053 pruebas en verde** (600 + 141 del P5.3 + 49 del P5.4 + 41 del P5.5 + 13 del P5.6 + 86 del P5.7 + 30 de las herramientas de reproducción y de medida + 93 del P6), más 2 del P6 que solo corren donde están DeepFace y HSEmotion (§4.0.8) y en `.venv` se saltan. Entorno: CPython 3.12.1 x64, `.venv` en el repositorio.

Entre ellas, tres de tipo distinto que conviene conocer porque atrapan lo que las
demás no ven (`tests/test_estatico.py`): una pasa **pyflakes** sobre todo el
código —el modo demo del head tracker no lo ejecuta ninguna prueba, y así se
cazó un `deque` usado sin importar que reventaba al arrancar—, otra prohíbe
**tildes en los identificadores** de funciones y variables, y la tercera
comprueba que pyflakes detecta de verdad ese fallo, para que la primera no pase
por no mirar nada. *(Hasta el 2026-10-19 este párrafo decía que la tercera
comprobaba el cascade Haar; esa prueba no existía. Las del cascade —que es el
de OpenCV, que pesa lo que pesaba al entrenar y que detecta una cara de
verdad— llegaron con el P6, en `tests/test_emotion_classifier.py`.)*

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
│   ├── emotion_classifier.py 🟡 Haar + CNN propia, DeepFace, HSEmotion · falta el visor
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
│   ├── medir_emocion.py      ✅ las cifras del P6: red, Haar, espejo, cara
│   ├── train_fer2013.py      ⬜ P8
│   └── eval_emotion.py       ⬜ P9
├── tests/                    ✅ 403 pruebas
├── mosquitto.conf            ✅ los dos listeners
├── emotion_model.meta.json   ✅ orden de clases del modelo propio (el .keras no va en git)
├── requirements.txt / requirements-dev.txt
├── requirements-benchmark.txt ✅ DeepFace y HSEmotion, en un entorno APARTE (P6)
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
barrido de agosto solo exploró dentro de él. La zona [−180, −95] **se barrió
el 2026-09-27** con los mismos detectores del visor (`tools/barrido_colisiones.mjs`,
ver P5.3) y está en `limits._TABLE_EXT_L/_R`; la unión del hombro y el motor del
brazo, que ningún detector revisaba, tampoco chocan (0 en ~820 poses por
brazo). `clamp_arm` conserva ±90 por defecto y el extendido se pide con
`flexion_range=FLEXION_RANGE_EXTENDIDO`.

**El cable del hombro no está modelado en el CAD**, así que no se verifica: se
**declara como supuesto de diseño**. El mapeo por dirección solo usa flexión en
[−180, 0], o sea 180° de recorrido, el mismo que el ±90° original desplazado
90°. Montado con el cable relajado en flexión −90° (brazo al frente), su
torsión queda en ±90°, igual que en el diseño original.

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
que es lo que hace el mapeo `direccion` del P5.3. Por debajo de −90,
`arm_abduccion_range_extendido` interpola la **tabla medida** de [−180, −95]
(no el reflejo `(a, f) ≡ (−a, f ± 180)`, que se probó y falla por un paso de 5°
en parte de las filas).

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
| 6 | **P6** | Clasificador de emoción con backends intercambiables | 🟡 código hecho · falta el visor |
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

## PROMPT 5 — Arm tracker ✅ **cerrado el 2026-10-19** · P5.1 y P5.3 a P5.7 hechos y verificados · `model_complexity` 1 medido y elegido

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
   **necesita red**, y para la Raspberry Pi del P11 hay que preverlo. En este
   `.venv` ya están los tres (comprobado el 2026-10-19).

   **La medida completa, el 2026-10-19** (`tools/medir_blazepose.py`: 8 s de
   cámara con el operador en postura de narración, y los mismos 120 frames por
   los tres modelos, en modo vídeo como el tracker):

   | `model_complexity` | ms por frame, mediana | p95 | frames con persona | visibilidad de muñecas |
   |---|---|---|---|---|
   | 0 (lite) | **15.3** | 16.7 | 100 % | 0.94 |
   | **1 (full, el que se usa)** | **22.0** | 24.4 | 100 % | 0.98 |
   | 2 (heavy) | **76.4** | 90.7 | 100 % | 0.97 |

   Junto a lo demás que correrá en el bucle del P7 —Face Mesh 5-7 ms (P4) y el
   bloque de emoción, ~30 ms cada 200 ms (P6)—:

   | | Frame normal | Frame con emoción (1 de cada 6 a 30 fps) | Media |
   |---|---|---|---|
   | con el nivel 1 | 27-29 ms | 57-59 ms | ~32-34 ms |
   | con el nivel 0 | 20-22 ms | 50-52 ms | ~25-27 ms |

   **Decisión: se queda el nivel 1.** El 2 queda descartado: sus 76 ms solos ya
   pasan del presupuesto entero (33 ms a 30 fps, 50 ms a 20 Hz de publicación).
   El 1 cabe en los 20 Hz de media, y es con el que se midió, se ajustó y se
   verificó todo el P5: cambiarlo obligaría a rehacer esa validación. El 0
   ahorra 7 ms por frame con una visibilidad de muñecas apenas menor (0.94
   frente a 0.98; la precisión de las posiciones no se midió) y queda como
   **plan de reserva para el P7**: si el bucle único no sostiene su tasa,
   `TELMEOW_ARM_MODEL_COMPLEXITY=0`, sin tocar código, y revalidar con
   `tools/reproducir_brazos.py` sobre una grabación nueva. A 15 fps —la cámara
   con poca luz, como en esta medida— hay 66 ms por frame y cabe todo.

#### Lo que salió de la sesión del 2026-09-26

Primera sesión con protocolo, con el mapeo `direccion` del P5.3 ya por defecto.
Dos grabaciones, renombradas con su fecha para que la siguiente no las aparte:

| Archivo | Contenido |
|---|---|
| `brazos_2026-09-26.csv` | 2783 frames, 187 s. Etiquetas 0-2 con `mano` (manos en la mesa, manos a la altura de la cara, narración) y 3-6 con `codo` |
| `mapeo_2026-09-26.csv` | 1356 frames, 93 s. Acercar y alejar las manos, subir y bajar por delante, cruzar por delante del cuerpo |

Lo que dejan establecido, y lo que no:

1. **Se grabó a 15 fps, y lo pone la cámara, no el código.** Los intervalos
   entre frames se concentran en 62-68 ms, el ritmo exacto de 15 fps; el
   2026-09-13, con prácticamente la misma cadena, la mediana era 39.6 ms (25
   fps). La causa casi segura es la poca luz de la tarde: la exposición
   automática alarga el tiempo de exposición y la cámara baja a la mitad de
   fps. Pendiente de confirmar con más luz.
2. **La vertical del tronco salió de la reserva el 85-100 % del tiempo.** Las
   caderas no entran nunca en cuadro. Queda para declarar (§3.3): la terna
   asume al operador sentado y erguido.
3. **Las manos apoyadas en la mesa no se ven: 0 % de frames estimados.** La
   mesa no entra en el encuadre. Fue un fallo del protocolo, y esa etiqueta no
   sirve para `mano`.
4. **Cuál de los dos puntos se ve depende de la postura**, y eso corrige la
   discusión de mano frente a codo por segunda vez:

   | Postura | `mano` estimada | `codo` estimado |
   |---|---|---|
   | Manos quietas a la altura de la cara | **91 %** | 19-54 % (codos en el borde inferior) |
   | Narración | 74-75 % | **86-96 %** |

5. **Temblor de la dirección en posturas quietas** —mediana, en ventanas de
   1 s, de la desviación angular; no depende del mapeo—: `mano` 3.1/2.9°,
   `codo` 1.0-2.1°. Las posturas no eran idénticas, así que es un indicio, no
   una cifra citable.
6. **La cifra del §3.3.1 no sale limpia de estas columnas.** La dirección está
   normalizada, y al normalizar la profundidad se mezcla con la vertical. Por
   eso el P5.4 graba además los vectores en metros (`p_*`, `c_*`): con ellos el
   ruido de la profundidad se mide en centímetros.

Y lo que vio el operador en vivo, con el visor delante:

- **El brazo ya no da la vuelta** al acercar las manos: el P5.3 funciona.
- **Al recoger la mano, el robot se queda adelante.** Es el **H38** y lo
  resuelve el P5.4.
- **Al cruzar un brazo, el otro, quieto, da un saltito.** Su mano cuelga en el
  borde inferior del cuadro y su visibilidad ronda 0.5: cuando se pierde más de
  `gracia_s` el canal va al reposo `(−55, 0)`, y al recuperarse el brazo
  colgando se mide `(−50, −12)`. **Pendiente**: rampa de vuelta al reposo más
  lenta, o un reposo que coincida con el brazo relajado del operador.
- **La línea de hombros se ve inclinada.** Es la estimación de BlazePose tal
  cual, sube del lado del brazo que se levanta (la escápula) y, con la vertical
  en reserva, la terna solo usa su parte horizontal: no afecta a los ángulos.
- **La vuelta suave al reposo funciona.**

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
   medidas y de la rama en uso (`alcance_robot`: ~30° arriba, ~70° al frente,
   ~55° abajo; continua sin suavizar, porque la zona medida casa con la de
   agosto en −95/−90). El operador con el brazo colgando pide exactamente el
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
| Correlación componente lateral | 0.35 / 0.29 | **0.66 / 0.56** |
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

#### Barrido de la zona extendida — 2026-09-27

La primera versión del P5.3 extendía la tabla **por simetría**, sin barrer. Se
barrió después, con `tools/barrido_colisiones.mjs`: una copia en memoria del
visor que expone su `probe()` —plano posterior + BVH brazo-tronco, **el mismo
criterio que opera en vivo**— y repite el procedimiento de agosto (paso 5°,
rango contiguo alrededor de abducción 0, demás DOF en home; 1D con refinado a
1°). Resultado en `barrido_colisiones_2026-09-27.json`.

| Comprobación | Resultado |
|---|---|
| Reproducción de la tabla de agosto en [−90, 90] | **37/37 filas idénticas** en los dos brazos: el arnés es fiel |
| 1D | abducción −55/+32 (izq) y −55/+33 (der), como en agosto; flexión libre de −180 a +90 con el brazo en cruz |
| Zona [−180, −95] contra el reflejo | iguales **12/18** (izq) y **9/18** (der); el resto difiere **un paso de 5°** (dos, una vez, en el máximo del derecho a −170) |
| En el límite que usa la rama −1 (el mínimo) | el reflejo prometía **5° de más** en 3 filas (izq) y 2 (der), más que el margen de 2° |
| Unión y motor del brazo contra el tronco (+ tapa y cajita) | **0 choques** en 822 (izq) y 818 (der) poses admisibles; libres también en home |

Consecuencia: **manda la tabla medida**, no el reflejo (`limits._TABLE_EXT_*`).
El reflejo solo se conserva para flexión > 90, que el rango extendido no
alcanza. La simetría era una buena aproximación —a un paso de barrido— pero no
lo bastante para usarla como límite.

Lo que el barrido **no** cubre, y se declara: combinaciones con los demás DOF
fuera de home (como en agosto, §3.3.8), contacto de la unión con el motor fijo
del hombro (van acoplados), y el cable, que no está en el CAD (ver §0.5).

**Cambios en el contrato**: ninguno en el JSON. El visor recibe `armLtw` en
[−180, 90] y ya lo aplicaba; solo cambió el rango de sus dos sliders de flexión.
El CSV gana `mapeo`, `flexion_min`, `h_lat/sup/ant_*` (la dirección cruda, que
permite reproducir OFFLINE cualquier mapeo futuro sobre la misma sesión) y
`psi_*`, `alfa_*`, `rama_*`.

**Lo que falta, con el operador delante:**

1. ~~Confirmar en Fusion la unión, el motor del brazo y el cable~~ → **hecho
   con los detectores del visor** el 2026-09-27 para unión y motor (0 choques).
   El cable no existe en el CAD: pasa a **supuesto de diseño declarado** (§0.5,
   §3.3.11), no a verificación.
2. ~~Sesión con `--publish`~~ → **hecha el 2026-09-26**: el brazo ya no da la
   vuelta. Destapó el H38, que resuelve el P5.4.
3. ~~Grabar con protocolo y rehacer las cifras~~ → **hecho con
   `mapeo_2026-09-26.csv`**, y confirman las de la grabación descartada:

   | | articular L / R | dirección L / R |
   |---|---|---|
   | Saltos de rama | 92 / 93 | **0 / 0** |
   | Correlación componente anterior | 0.42 / 0.23 | **0.77 / 0.68** |
   | Correlación componente lateral | 0.28 / 0.18 | **0.63 / 0.67** |
   | Temblor de la punta p95 (°/frame, a 15 fps) | 16.9 / 16.7 | **11.3 / 11.2** |

   En `brazos_2026-09-26.csv`: 39/38 saltos de rama con el articular, 0/0 con
   el de dirección. **Estas son las cifras que se citan.**
4. **Profundidad**: con las columnas nuevas se puede medir cuánto comprime
   BlazePose la `z`. Si hiciera falta, la siguiente palanca es estimar la
   componente anterior por **escorzo** (longitud 2D del brazo contra su
   longitud calibrada, resolviendo la intersección rayo-esfera con la cámara) y
   fusionarla con la de BlazePose. **No se implementa hasta medir** que hace
   falta.

### P5.4 — Mapeo por posición ✅ **hecho el 2026-09-27** · falta verlo con el operador

**El síntoma** (el operador, con el visor, el 2026-09-26): llevar el brazo al
frente funciona, pero **al recogerlo —o llevarlo hacia atrás, buscando dejarlo
paralelo al tronco— el robot sigue adelante**. Y un saludo moviendo solo la
mano no se traduce.

**La causa es el H38.** El mapeo `direccion` usa hacia dónde apunta el vector
hombro→mano y **descarta su longitud**. Recoger la mano por la misma línea no
cambia la dirección, y con el codo doblado la mano queda delante del pecho, así
que el vector apunta adelante aunque el brazo esté recogido. Medido en
`mapeo_2026-09-26.csv`, etiqueta 0 (32 s acercando y alejando las manos): la
componente anterior de la dirección se quedó por encima de 0.78 (izq) y 0.85
(der) el 95 % del tiempo. Y en la narración con `mano`, el robot pasó casi todo
el tiempo con los brazos al frente, porque esa es la postura de reposo de quien
narra.

**Los caminos que se pusieron sobre la mesa**: seguir con `mano` y declararlo;
pasar a `codo` —el hombro del robot copia el del operador, pero se pierden los
gestos de antebrazo—; o un híbrido. **La solución la propuso el operador**:
usar también la profundidad, de modo que al recoger el brazo el robot lo lleve
hacia atrás, y como no puede doblarlo, **lo abra hacia la cruz**. Se afinó con
un dibujo interactivo en tres correcciones suyas: el movimiento lateral tiene
que seguir transmitiéndose; **al cruzar la muñeca por dentro del tronco el
brazo se tiene que cerrar**, no abrir; y se tiene que cerrar **poco a poco**,
«con un factor de conversión, para que llegue lenta al máximo del robot», sin
esperar a que lo pare el tope ni el visor.

**La regla** —`retarget.direccion_por_posicion`, mapeo `posicion`, por
defecto—: «lo que el codo esconde al doblarse, el robot lo despliega hacia el
costado». Con `q` = hombro→mano dividido por el largo del brazo en ese frame
(hombro→codo + codo→mano; `|q| = 1` estirado), la dirección de la mano **gira
hacia la cruz** una fracción

```
s = w(L) · (1 − |q|)
```

del ángulo que las separa. `w` vale 1 con la muñeca por fuera del borde del
tronco (lateral del hombro) y baja a 0 con un paso suave a lo largo de
`banda_tronco` hacia dentro: **0.1 del largo del brazo, unos 6 cm, que eligió el
operador** probándolo en el dibujo interactivo (0.3, unos 18 cm, sería la mitad
del cuerpo). Antes de girar, lo que la mano cruza hacia dentro se
**pliega** como profundidad, `(L, S, A) → (0, S, hypot(A, L))`: el robot no
puede cruzar —su tope interno a la altura del hombro son 70° desde la cruz,
medidos en el barrido de colisiones—, y sin el pliegue la dirección apuntaba a
través del cuerpo y lo saturaba de golpe: con la mano recogida, el robot pasaba
de 11° a su tope en los primeros 7 cm hacia dentro y ahí se quedaba.

| Gesto | Resultado |
|---|---|
| Brazo estirado | **Idéntico a `direccion`**: apuntar no cambia |
| Recoger la mano al frente | El brazo vuelve hacia la cruz, **lineal**: ψ = 90° × extensión |
| Cruzar hacia dentro con la mano recogida | Se **cierra poco a poco**: 11° en el borde, 21° a 1.5 cm, 44° a 3 cm, 65° a 4.5 cm y su tope (68°) a 6 cm |
| 18 cm de lado con el codo doblado | ~10° de robot: se transmite, atenuado |
| Mano a medio camino del costado | **La cruz**: la cámara no deja estirar los brazos en cruz, y no hace falta |

Tres decisiones de diseño que hay que poder defender:

1. **El largo del brazo se mide en cada frame**, con sus dos segmentos, y no se
   calibra. Así la extensión vale 1 con el brazo estirado aunque BlazePose
   escale mal el cuerpo o comprima la profundidad: los dos lados del cociente
   se comprimen igual. Con `target="codo"` la extensión es siempre 1 y
   `posicion` se reduce a `direccion`.
2. **Un giro y no una suma.** La primera versión sumaba el largo escondido como
   un vector lateral y normalizaba. Lejos del cuerpo da lo mismo, pero con la
   mano pegada al pecho, en la franja del borde, la derivada crece como `1/A`:
   3.5° de brazo por cada 0.6 mm de mano. El giro está acotado. Y el pliegue
   quita además el único punto singular que tenía la regla —la mano apuntando al
   lado contrario, donde el plano del giro no existe—, porque ya no hay
   dirección que apunte hacia dentro.
3. **Sin corte seco en el borde del tronco.** Justo ahí tiene el operador las
   manos casi siempre, y un corte sería otra costura como la del H35. La
   anchura de la franja es un compromiso: más estrecha, el robot se cierra
   antes pero el paso por el borde se vuelve brusco; la mitad del cuerpo es la
   referencia natural, y `ARM_BANDA_TRONCO` la cambia sin tocar código.

**Qué cambia en el contrato**: nada en el JSON. El CSV gana `p_*` y `c_*` —los
vectores hombro→destino y hombro→codo **en metros**— y `ext_*`. Con ellos
`tools/comparar_mapeo_brazos.py` reproduce el mapeo `posicion` sobre la misma
sesión y reporta `corr prof`, la correlación entre la profundidad de la mano en
metros y la de la punta del robot, que es la queja del H38 en un número; y
`tools/analizar_registro_brazos.py` da el temblor de dirección, la extensión y
el **ruido de la mano en centímetros por eje**, que es la cifra del §3.3.1.
`k` recorre posicion → direccion → articular, solo para comparar en el banco;
el programa final usa un único mapeo.

**Lo que falta, con el operador delante:**

1. Sesión con `--publish`: recoger el brazo por fuera y por dentro del tronco,
   el saludo, cruzar, las manos sosteniendo el libro.
2. Grabar con protocolo **con las columnas nuevas**: posturas quietas (para los
   centímetros del §3.3.1) y los gestos del punto 1 (para `corr prof`).
3. Ajustar `ARM_ALCANCE_HUMANO` a lo que cabe en cuadro —con 90° el robot solo
   llega a su tope si el operador apunta recto arriba, y eso sale de cuadro;
   probablemente 60-70°— y, si hiciera falta, `ARM_BANDA_TRONCO`.
4. El saltito del brazo en reposo (sesión del 2026-09-26).
5. **El saludo**: el robot sube el brazo como mucho ~30° sobre la horizontal y
   no tiene codo ni muñeca, así que un saludo moviendo solo la mano no puede
   salir bien con ningún mapeo. La salida realista es una animación
   pregrabada que dispare el operador (el visor ya tiene una de demostración):
   es del P7.

### P5.5 — Híbrido, zona de reposo y escorzo ✅ **hecho el 2026-09-27** · validado en el P5.6, con tres correcciones

**Lo que vio el operador**, con el visor mirando al robot desde atrás:

1. Con las manos levantadas junto a la cara y los codos abajo —«muevo mis
   brazos para atrás»— el robot dejaba los brazos al frente. Por lo mismo, el
   saludo (subir y bajar el brazo en horizontal) no se traducía.
2. Al mover un solo brazo, el otro, quieto y colgando, daba un saltito.
3. Pidió el camino 3, el híbrido: «el más robusto cuando no se vean mis manos
   pero sí los codos».
4. Eligió la franja del borde del tronco al 10 % (P5.4).

**1. El H39: el fallo era la profundidad de BlazePose, no el mapeo.** Con los
números del HUD, BlazePose ponía las manos **32-37 cm por delante** de los
hombros y 3-4 cm por encima. En la imagen, brazo y antebrazo se veían casi con
todo su largo —32 y 28 cm aparentes frente a ~30 y ~27 reales—: estaban casi en
el plano de la imagen. Con la mano «adelante», ningún mapeo podía dar el gesto.
Es el §3.3.1 en la práctica, y la razón de que el P5.3 dejara anotado el escorzo
como «no se implementa hasta medir que hace falta»: esta es esa medición.

**La corrección: escorzo** (`telmeow_teleop/escorzo.py`). La imagen dice por qué
rayo de la cámara se ve cada articulación, y el largo del hueso a qué distancia
de la anterior está. El codo queda en la intersección de su rayo con una esfera
centrada en el hombro, y la mano en la de su rayo con una centrada en el codo
—la reconstrucción de Taylor (2000)—, con la cámara como modelo de agujero y los
hombros anclados por su separación en la imagen. La perspectiva está incluida,
y a un metro importa. En la escena de la foto reconstruida con su geometría:
**sin escorzo, el robot a 32° de la cruz hacia delante y abajo; con escorzo, a
0.8°**, que es la cruz que el operador dibujó.

**La ambigüedad, que hay que declarar (§3.3.13).** El rayo corta la esfera en
dos puntos. Con perspectiva, hasta un hueso en el plano tiene una segunda
solución que apunta a la cámara: en la foto, el codo podía estar 2 cm detrás del
hombro o 22 cm hacia la cámara. Se elige con un coste de tres términos:
parecido a BlazePose, preferencia por el plano (`PESO_PLANO` = 0.4) y
continuidad con el frame anterior como desempate (`PESO_CONTINUIDAD` = 0.3). El
0.4 sale de un margen estrecho: el codo de la foto pide más de 0.39 para
quedarse en el plano, y una mano que se acerca 25 cm a la cámara pide menos de
0.49 para no quedarse atrás. Depende de cuánto se equivoque BlazePose, así que
**se valida con la grabación**. La mano de la foto no tiene ese problema: sus
dos soluciones quedan a −7 y +9 cm del plano del codo, así que vuelve al plano
diga BlazePose lo que diga.

**2. El híbrido** (`target="hibrido"`, por defecto). Cada punto pesa según su
visibilidad —de 0 a 1 entre 0.35 y 0.65— y se combinan como
`peso = w_mano / (w_mano + (1 − w_mano) · w_codo)`: la mano visible manda entera;
fuera de cuadro cede al codo, tomado como brazo estirado; entre medias, un paso
suave. Se mezclan posiciones y no direcciones, para que el mapeo `posicion` siga
viendo cuánto está recogida la mano.

**3. La zona de reposo** (`zona_reposo` = 25°, ajustable; **35° desde el
P5.6**). Con el brazo a menos de 25° de la vertical, el robot queda **exactamente** en reposo, se mida o no, y
se funde con el mapeo en los 15° siguientes. El saltito era la diferencia entre
el brazo colgando medido —`(−52.7, −12.7)` en la foto— y el reposo `(−55, 0)`
al perderlo. El precio: los movimientos pequeños con el brazo colgando no se
transmiten.

**Lo que cambia en el contrato**: nada en el JSON. `ArmConfig` gana `target`
(`hibrido`), `zona_reposo`, `escorzo` y `focal_relativa` (`TELMEOW_ARM_*`). El
CSV gana `peso_mano_*`, `reposo_*`, `pb_*`/`cb_*` (BlazePose antes del
escorzo), `img_*` (los píxeles) y `escorzo`/`focal_rel`: con ellos el escorzo se
puede rehacer offline con otra focal u otros pesos. `p_*` y `c_*` pasan a ser
las posiciones corregidas, y `p_*` es siempre la mano. El analizador dice cuánto
actuó cada pieza, y la herramienta de comparación reproduce el híbrido y la
zona de reposo. La tecla `t` recorre hibrido → mano → codo.

**Lo que faltaba** —la grabación de validación, ajustar `PESO_PLANO` y la focal,
calibrar `ARM_ALCANCE_HUMANO`— se hizo en el P5.6.

### P5.6 — La validación, y lo que enseñó ✅ **hecho el 2026-09-28 y verificado el 2026-10-11** · el saludo (H42) se resolvió en el P5.7

**La grabación**: `validacion_2026-09-27.csv`, 270 s con protocolo —0 brazos
colgando · 1 manos junto a la cara, codos abajo y atrás · 2 saludo con el
derecho y el izquierdo colgando · 3 al revés · 4 acercar y alejar las manos de
la cámara con los brazos estirados · 5 recoger los brazos con las muñecas por
fuera · 6 cruzar por delante · 7 narración libre—, más un intento anterior
cortado, `validacion_2026-09-27_intento.csv` (etiquetas 0-2), que sirvió de
contraste: **todo lo que sigue se ajustó sobre una grabación y se comprobó
sobre la otra**. Con más luz, **18-20 fps** en vez de 15; la etiqueta 0 se
queda en 14, probablemente porque con las manos fuera de cuadro BlazePose
vuelve a detectar más a menudo.

**Cómo se evaluó.** El escorzo se rehízo offline con otros parámetros desde
`pb_*`, `cb_*` e `img_*`, y el resto de la cadena con el `ArmTracker` real.
Faltaba un dato —la separación de hombros de BlazePose, que usa el ancla— y se
reconstruyó de la propia salida del escorzo: 31.5 cm, estable en todas las
etiquetas, con las dos ecuaciones de la reconstrucción de acuerdo en un 1-9 %.
La reproducción repite lo grabado a 1-4 cm, y la versión final se comprobó
pasando las dos grabaciones por el tracker real: da **exactamente** las cifras
de la simulación. Desde el P5.6 el CSV graba ese dato (`hombros_ancho_m`,
`hombros_dz_m`) y no hará falta reconstruirlo.

**1. El saltito seguía, y peor: BlazePose inventa la mano que no ve (H40).** Con
el derecho saludando, el brazo izquierdo colgando estaba en reposo solo el
**16 %** del tiempo, y el robot lo llevaba hasta 95° de su reposo. La mano del
brazo colgando queda por debajo del cuadro, y BlazePose salta de un frame al
siguiente entre esa mano —en `(127, 592)`, con el cuadro acabando en 480— y otra
doblada hacia arriba delante del pecho —`(175, 430)`—, con **visibilidad 0.7-0.96
en las dos**. El híbrido se fiaba de la visibilidad. Una mano de verdad no salta
160 px ida y vuelta cada 50 ms: en los gestos, la mano se mueve 0.04 anchos de
hombro por frame de mediana; la inventada, 0.21. Y el escorzo lo agravaba: el
codo del brazo colgando cae en el borde inferior (y = 466-485), donde su píxel
es una extrapolación, y lo hacía saltar entre sus dos soluciones, 5 cm detrás
del hombro y 17 cm delante.

Tres arreglos:

- **Confianza en la mano** (`RAMPA_MANO_S` = 0.5 s). La mano cuenta solo
  **dentro de la imagen**, y va ganando peso durante medio segundo desde que
  entra; si sale o se pierde, la cuenta vuelve a empezar. La que aparece y
  desaparece no llega a contar. Se probaron otras cuatro formas —solo exigir
  que esté dentro, detectar la ida y vuelta, cualquier salto grande,
  persistencia dura—, y la rampa es la única que arregla el brazo quieto sin
  tocar los gestos: en el 90 % de sus frames el comando no cambia ni una décima
  de grado. Los saltos grandes de los gestos resultaron ser casi siempre
  movimientos rápidos de verdad, así que un detector de saltos frenaba justo
  los gestos más expresivos.
- **El codo del borde no se corrige si la mano no es de fiar** (`MARGEN_BORDE`,
  3 % del alto). Con la mano a la vista sí se corrige: con las manos junto a la
  cara y los codos abajo, el codo está en el borde y bien visto, y sin
  corregirlo ese gesto volvía al frente (en el intento, de 10° a 38° de la
  cruz).
- **Zona de reposo 35°** (era 25°). Sentado a la mesa, el brazo colgando del
  operador se mide a 20-37° de la vertical; el derecho descansa con el codo
  afuera, y en la imagen se ve 37° inclinado. Con 25°, el brazo derecho quieto
  oscilaba entre el reposo y 18° fuera de él.

| Brazo quieto: frames a menos de 3° del reposo | grabado | P5.6 |
|---|---|---|
| izq., etiqueta 0 (los dos colgando) | 51 % | 94 % |
| der., etiqueta 0 | 76 % | 94 % |
| izq., etiqueta 2 (el derecho saluda) | 16 % | 71 % |
| der., etiqueta 3 (el izquierdo saluda) | 58 % | 100 % |
| intento: izq. y der. etiqueta 0, der. etiqueta 2 | 98 / 92 / 45 % | 100 / 100 / 99 % |

Lo que queda en la etiqueta 2 son tramos en que la mano inventada se sostiene
en cuadro más de medio segundo. En esa hipótesis BlazePose adelanta también el
codo, el brazo se lee a 38-44° de la vertical, y la zona lo deja a medias: el
robot se aparta 4-28° del reposo. Que es inventada lo confirmó el operador: esa
mano estaba apoyada en el asiento, colgando.

**2. El escorzo quedaba en una frontera, y la culpa era la focal (H41).** En la
etiqueta 1 el escorzo acercó las manos al plano solo 4-8 cm (de 27 a 19-23 cm),
y el robot quedó a 27-29° de la cruz en vez de cerca de ella. Rehaciendo el
escorzo con `PESO_PLANO` de 0.4 a 1.0 y focales de 0.5 a 1.2 veces el ancho:

- Con la focal 1.0 del head tracker, ese gesto cae **justo en la frontera**
  entre las dos soluciones del codo: con `PESO_PLANO` 0.4 el codo va 14.5 cm
  delante y el robot a 27° de la cruz; con 0.6, al plano y a 3°. Una elección
  biestable, y con 1.0 ningún `PESO_PLANO` acierta a la vez este gesto y el de
  los brazos estirados.
- Con focales de **0.6-0.8**, los dos gestos de referencia salen bien **a la vez
  y en las dos grabaciones**: las manos junto a la cara vuelven al plano y los
  brazos estirados siguen delante.
- Y cuadra con la geometría: 190 px entre hombros a 0.6-0.9 m de la cámara dan
  una focal de 0.5-0.85 veces el ancho; la integrada de un portátil ronda 65-75°
  de campo horizontal.

`FOCAL_RELATIVA` = **0.7** (71° de campo horizontal). `PESO_PLANO` se queda en
0.4: con la focal buena es el que mejor deja los brazos estirados delante.

**Medida después, el 2026-10-11, con el teléfono del operador** (POCO F5, 16.1
× 7.5 cm): en horizontal, paralelo a la cámara y a una distancia igual a su
largo, la focal en píxeles es su largo aparente. Dos capturas dan **0.76 y
0.68** (el alto, 0.75 y 0.69; la proporción, 2.19 y 2.13 frente a 2.15 real,
así que estaba paralelo). La media, 0.72, confirma el 0.7; la diferencia entre
las dos capturas es la de poner la distancia a mano.

**Una precisión sobre la etiqueta 1**, de boca del operador: hizo primero «el
grito» —las manos en las mejillas— y luego «yo no fui» —las manos a los lados
de la cabeza—, **con los codos al aire** en las dos. O sea que la verdad no es
«en el plano» sino entre el plano y unos 10 cm delante (la cara), y la
etiqueta mezcla dos posturas; eso explica parte de lo biestable del codo. Las
cifras de la tabla siguen ordenando igual las focales —1.0 deja las manos a
20-21 cm, BlazePose a 27—, pero la grabación de verificación las separa en dos
etiquetas.

| Robot desde la cruz (mediana) | etiqueta 1, izq. / der. | etiqueta 4, izq. / der. |
|---|---|---|
| sin escorzo | 41 / 38° | 53 / 59° |
| grabado (focal 1.0, alcance 90) | 27 / 29° | 52 / 47° |
| **P5.6** (focal 0.7, alcance 80) | **0.4 / 23°** | **62 / 55°** |
| intento, P5.6 | 10 / 16° | — |

El tope del robot al frente, a la altura del hombro, es 68°.

**3. `ALCANCE_HUMANO` = 80** (era 90). Con los brazos estirados hacia la cámara
el operador se aparta de la cruz 62-70° de mediana y ~80° en el p90: apuntar
recto al frente sale de cuadro. Con 90, el robot se quedaba en 52-56° de sus
68°; con 80 llega a 55-62°, sin más temblor. Con 80, la grabación del P5.3 da un
salto de flexión de 94.5° en un fallo de BlazePose de un frame —el brazo tirado
80° al costado, cerca del polo del cardán— que con 90 era de 86.7° y no llegaba
al umbral de la métrica. No es la costura ni un cambio de rama, y la prueba del
P5.3 reproduce sus cifras con el alcance con que se calcularon.

**4. La cifra del §3.3.1, por fin.** En postura quieta con las manos a la vista
(etiqueta 1), la profundidad de BlazePose tiembla **1.6-1.8 cm** y el plano de
la imagen **0.5-0.9 cm**: **entre 2.1 y 3.0 veces más**, en las dos grabaciones
y los dos brazos, con BlazePose sin corregir. El escorzo no la reduce
(×2.6-4.2): hace la profundidad coherente con la imagen, no menos ruidosa.

**5. El escorzo cambia mucho de solución, pero no se nota.** Salta de una
solución a otra 54-62 veces por minuto de brazo medido, frente a 13 saltos de la
propia profundidad de BlazePose, y la mayoría son el escorzo encendiéndose y
apagándose (la mano cruzando el umbral, el codo el borde). Se probó a
suavizarlo —histéresis, mezcla de las dos soluciones, peso continuo— y no
compensa: el temblor de la punta del robot es el mismo con y sin escorzo
(narración: 2.0-2.4° por frame de mediana y 9.0° en el p95, contra 1.9 y 8.5 sin
él). El One Euro y el limitador de velocidad lo absorben.

**Lo que cambia en el contrato**: nada en el JSON. Por defecto, `zona_reposo`
35, `focal_relativa` 0.7, `alcance_humano` 80 y `rampa_mano` 0.5, nuevo
(`TELMEOW_ARM_RAMPA_MANO`). El CSV gana `confianza_mano_*` y
`hombros_ancho_m`/`hombros_dz_m`. El analizador da la cifra del §3.3.1 con
BlazePose sin corregir —y la de después del escorzo al lado— y cuánto no se
fió de la mano. **13 pruebas nuevas**, 12 de ellas en
`tests/test_confianza_mano.py`, con la escena de la grabación: con la mano inventada el brazo quieto no se aparta del
reposo —sin la rampa, 9° en cada frame, el paso máximo del limitador—, y la
misma mano, sostenida, sí mueve el brazo.

**La verificación, el 2026-10-11** (`verificacion_2026-10-11.csv`, 241 s, con
el protocolo de abajo; y un intento cortado, `verificacion_2026-10-11_intento.csv`,
con las etiquetas 0 y 1). Es la primera grabación hecha CON los parámetros del
P5.6, así que no se usó para elegirlos: las cifras salen de los comandos que se
publicaron de verdad.

| Etiqueta | Qué hizo el robot | |
|---|---|---|
| 0 · brazos colgando | en reposo el 96 y el 100 % del tiempo | ✅ |
| 1 · «yo no fui», manos por fuera de los hombros | **brazos en cruz**: a 10-11° de ella, casi horizontales. La mano, a 5 cm del plano; BlazePose decía 30 | ✅ |
| 1 del intento, manos por dentro de los hombros | al frente y arriba (55°, +11/+21°): es el pliegue del P5.4 | ✅ por diseño |
| 2 · «el grito», manos en las mejillas | al frente y arriba, a +25-30° sobre la horizontal, su tope. La mano, a 5-15 cm | ✅ |
| 3 y 4 · saluda un brazo, el otro cuelga | **el que cuelga, en reposo el 100 % del tiempo** en las dos | ✅ |
| 3 y 4 · el brazo que saluda | se queda en cruz y se mueve ±5°: **el saludo no pasa** (H42) | ❌ |
| 5 · estirar hacia la cámara y recoger | al frente y arriba al estirar (55-58°, +28°), a la cruz al recoger | ✅ |

**El saludo no pasa al robot (H42).** El operador saludó como se saluda: el codo
abajo y afuera, 15-20 cm por debajo del hombro; la mano por encima del hombro,
con el antebrazo hacia arriba; y la mano yendo de lado a lado (6-25 cm del
hombro en lateral, solo 8 cm en vertical). Con la mano recogida y por fuera
del tronco, el mapeo `posicion` lleva el brazo del robot a la cruz —es lo que
hace bien el «yo no fui»—, y ahí el vaivén de la mano solo cambia la dirección
efectiva entre 15° y 19° de la cruz: el robot se mueve ±5°. Un saludo así es un
giro del antebrazo, y el robot no tiene antebrazo.

**A 15 fps fijos** en toda la sesión, con poca luz (H27); en septiembre, con
luz, 18-20.

**El §3.3.1 en cuatro grabaciones.** En posturas quietas con las manos a la
vista, la profundidad de BlazePose tiembla **1.5-3.0 veces** más que el plano
de la imagen (0.8-1.8 cm frente a 0.4-0.9). La razón más baja, 1.5-1.6, es la
de «el grito»: con las manos apoyadas en la cara el movimiento real es nulo, y
lo que queda es el ruido del estimador.

**Lo que falta:**

1. ~~Decidir el saludo~~ — el operador eligió, de tres caminos, **que el robot
   salude solo** (los otros: saludar con el brazo entero, o amplificar el
   vaivén del antebrazo). Hecho en el P5.7.
2. ~~Rehacer en `tools/` la reproducción offline~~ — **hecho el 2026-10-19**:
   `tools/reproducir_brazos.py` reconstruye lo que BlazePose le dio al tracker
   —píxeles, visibilidades y el mundo desde `pb_*`/`cb_*` y el ancla de los
   hombros— y se lo vuelve a dar a un `ArmTracker` de verdad con los parámetros
   que se quiera. Reproducida con sus propios parámetros, la verificación del
   2026-10-11 da los comandos que se publicaron (mediana 0.0°, p95 0.1-0.5°; lo
   que difiere es el 3 % de frames en que usó las caderas, que el CSV no
   permite rehacer). Y **saca exactamente las cifras de esta sección**: brazo
   quieto 94/94/71/100 %, la punta del robot a 0.4/23° y 61.7/55°, y con la
   focal vieja 27/29°. Las pruebas lo fijan. Desde ese día el tracker graba
   `pb_*`/`cb_*` también en los frames que no mide, para que una variante más
   permisiva que la grabación tenga de dónde sacarlos.
3. Medir el coste de `model_complexity` y cerrar el P5.

**El protocolo de la verificación**, con `--publish` para verlo a la vez en el
visor (ESPACIO al cambiar de postura; cada postura separada, para que las
cifras no mezclen dos):

| Etiqueta | Postura | Tiempo | Qué debe hacer el robot |
|---|---|---|---|
| 0 | Brazos colgando, quietos, manos sobre el asiento | 20 s | quieto en reposo |
| 1 | «Yo no fui»: manos abiertas a los lados de la cabeza, palmas a la cámara, codos abajo al aire | 15 s | brazos hacia la cruz |
| 2 | «El grito»: manos en las mejillas, codos abajo al aire | 15 s | brazos al frente y arriba |
| 3 | Saludo con el derecho: brazo horizontal hacia el costado, subir y bajar; el izquierdo colgando | 20 s | el derecho sigue; el izquierdo, quieto |
| 4 | Lo mismo con el izquierdo; el derecho colgando | 20 s | al revés |
| 5 | Brazos estirados hacia la cámara, acercar y alejar despacio | 20 s | brazos al frente, cerca de su tope |
| 6 | Narración libre, como contando un cuento | 45 s | sigue los gestos |

Con la cámara integrada en el índice que diga `tools/list_cameras.py` (el 0
sin la externa conectada) y con buena luz (H27). En las etiquetas 3 y 4 el
operador hizo el saludo natural, con el antebrazo arriba, en vez del brazo
horizontal: de ahí sale el H42.

### P5.7 — El robot saluda solo ✅ **hecho el 2026-10-11 y verificado el 2026-10-19**

**Por qué no se copia el saludo (H42).** Un saludo humano es un giro del
antebrazo, y el robot no tiene antebrazo: copiado, el brazo se quedaba en cruz
moviéndose ±5°. De tres caminos, el operador eligió que el robot **reconozca**
el saludo y salude a su manera, con el saludo del visor (botón «Saludo demo»),
que es el que ya conoce. `telmeow_teleop/saludo.py`.

**El detector** mira la mano en la IMAGEN respecto a su hombro, en anchos de
hombro, y pide cuatro cosas a la vez:

1. la mano **de fiar**: dentro del cuadro y con su rampa completa (P5.6);
2. **por encima del hombro** (≥ 0.1 anchos) **pero no de la cabeza** (mediana
   ≤ 1.2 anchos, desde el 2026-10-19: ver la sesión de ese día);
3. **cerca del plano del hombro**: la mediana de la profundidad corregida por el
   escorzo en el último 1.5 s, ≤ 15 cm. Es lo que separa el saludo de estirar
   los brazos hacia la cámara y recogerlos, que en la imagen también lleva la
   mano de lado a lado, y con el mismo ritmo. La mano que saluda queda a
   −11..+12 cm del plano; la que se estira, a 10-40. La profundidad de BlazePose
   sin corregir no sirve: pone las dos a ~30;
4. **un vaivén con ritmo**: cinco cambios de sentido de al menos 0.15 anchos
   (~5 cm), **encadenados**, cada uno a menos de 0.7 s del anterior. Contarlos
   sin encadenar dejaba pasar un gesto de narración con dos barridos grandes y
   separados; con cuatro en vez de cinco, otro de 0.2 s.

Se ajustó con la grabación del 2026-10-11 y se contrastó con las del
2026-09-27:

| Grabación | Dónde reconoce un saludo |
|---|---|
| 2026-10-11 | etiqueta 3 (saluda el derecho), etiqueta 4 (el izquierdo) y **el saludo con que el operador abrió la narración** (186-189 s). En ningún otro sitio: brazos colgando, «yo no fui», «el grito», estirar los brazos, el resto de la narración |
| 2026-10-11, intento | ninguno |
| 2026-09-27 (sin la condición 3: su profundidad usa la focal vieja) | los dos saludos del protocolo, el saludo con que abrió también esa narración, y un estirar los brazos que la condición 3 quita |
| 2026-10-19 (la sesión del saludo, abajo) | etiqueta 1 (el derecho), etiqueta 2 (el izquierdo) y el saludo que abre la narración. Las dos manos juntándose sobre la cabeza, no: lo quita el techo de altura |

**El precio es la latencia**: cinco cambios de sentido son ~1.4 s de saludo
antes de que el robot empiece. El robot sigue mientras el operador saluda y
para 0.8 s después del último vaivén.

**El saludo del robot** es el del visor —`armR` 30 ± 12 a 0.7 Hz y `armRtw`
50 ± 8 a 2.3 Hz— llevado a la rama de la cinemática inversa del Python:
`(a, f)` y `(−a, f − 180)` ponen el brazo en la misma dirección, girado 180°
sobre su eje, que no se ve porque el brazo es simétrico. Así el robot pasa de
seguir al operador a saludar sin dar la vuelta. Entra y sale con el paso suave
de 0.9 s del visor, cabe en las tablas de colisión, y va a menos de 180°/s, así
que el limitador no lo deforma. La cabeza no se toca: la lleva el head tracker.

**Lo que cambia en el contrato: nada.** El saludo sale como ángulos de brazo,
igual que todo lo demás, así que el visor no cambia y el robot físico lo
reproducirá sin saber que es un saludo. `TELMEOW_ARM_SALUDO=0` lo apaga. El CSV
gana `saludo_L`/`saludo_R` (cuánto manda el saludo, 0..1), el HUD escribe
«SALUDO», y el analizador dice en qué etiquetas saludó el robot.

**83 pruebas nuevas** (`tests/test_saludo.py`), cada grupo atado a algo
externo: el patrón contra la fórmula del visor transcrita aparte; el detector
con lo que es y lo que no es un saludo; **la grabación del 2026-10-11, que tiene
que dar exactamente los tres saludos y nada más**; y el tracker entero con una
escena que saluda. Comprobado que fallan al quitar cada pieza: sin el saludo en
el tracker, sin la condición de profundidad, con cuatro vaivenes y sin
encadenarlos.

**La sesión del 2026-10-19** (`saludo_2026-10-19.csv`, 87 s: 0 brazos colgando,
1 saluda el derecho, 2 el izquierdo, 3 narración que empieza saludando). Con
buena luz, **29 fps**. El operador lo vio funcionar en el visor y lo da por
bueno: «se corta donde sea que haya quedado en el loop de saludo cuando mi mano
se baja», que es lo que debe pasar —el brazo que se deja de ver se sostiene
medio segundo y vuelve al reposo, como cualquier otro—. En la grabación el robot
saludó **donde tocaba**: con el derecho en la etiqueta 1, con el izquierdo en la
2 y con el derecho al abrir la narración. Y **una vez donde no**: a mitad de la
narración el operador juntó y separó las dos manos por encima de la cabeza, y el
robot saludó 1.6 s con el izquierdo. Los saludos de verdad llevan la mano a
0.3-0.55 anchos de hombro sobre el hombro, de mediana, y el que más sube toca
1.26 a ratos; ese gesto, a 1.49-1.97. Arreglado con un techo en mediana,
`ALTURA_MAX` = 1.2: con él, las cinco grabaciones reconocen exactamente sus
saludos —incluido el que abre cada narración— y nada más. La sesión es ahora
la tercera grabación que fijan las pruebas.

**Lo que falta:**

1. ~~Verlo en el visor~~ — hecho el 2026-10-19.
2. Subir la versión nueva de la skill del visor (preparada; decía que el saludo
   demo «no pasa por el Python», y tenía dos pendientes ya cumplidos).
3. ~~Rehacer en `tools/` la reproducción offline~~ — hecho:
   `tools/reproducir_brazos.py` (ver P5.6).
4. ~~Medir el coste de `model_complexity`~~ — hecho el 2026-10-19: 15 / 22 /
   76 ms por frame con los niveles 0 / 1 / 2; se queda el 1, con el 0 de
   reserva para el P7 (ver «Lo que falta» del P5.1, punto 4). **El P5 queda
   cerrado.**

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

## PROMPT 6 — Clasificador de emoción 🟡 **código hecho el 2026-10-19** · falta verlo con el operador (P6.1)

### Lo hecho el 2026-10-19

`telmeow_teleop/emotion_classifier.py`, con los catorce puntos del enunciado,
más `tools/medir_emocion.py` —de donde sale cada cifra de esta sección— y
`requirements-benchmark.txt`. El punto de partida fue comprobar el entorno del
punto 14, que se cumple tal cual: TensorFlow 2.16.1 carga el modelo (guardado el
2024-06-21 con Keras 3.3.3), entrada `(None, 48, 48, 1)`, salida `(None, 5)` con
softmax.

**Lo medido**, sobre el modelo propio y las 8000 imágenes del dataset propio:

| Qué | Medido |
|---|---|
| `model(x, training=False)` frente a `model.predict(x)` (bug 12) | **10 ms frente a 66** (mediana; p95 14 frente a 82), con probabilidades **idénticas** (diferencia 0.0) |
| Primera llamada a la red | entre 74 y 640 ms según el proceso (la primera vez, TensorFlow aún carga sus bibliotecas): se paga al construir el backend, no en el primer frame |
| Detector Haar a 640×480, con los parámetros del legacy | **42 ms**: **cuatro veces lo que cuesta la red** |
| El mismo con `minSize` = 60 px | 20 ms. **No cambia el recorte de ninguna cara**: cambia 7 detecciones de 7459, y las 7 son falsos positivos de 57-83 px sobre una mejilla o la barbilla, en fotos con la cabeza girada cuya cara (~300 px) el Haar no encontró |
| Imágenes en que el Haar no encuentra cara | 541 de 8000 (6.8 %): enojado 13 %, sorprendido 10 %, el resto 3-4 %. El entrenamiento las descartó sin decirlo |
| Imágenes con más de una detección | 168; en **93-102**, según la ejecución, la primera —la que usó el entrenamiento— no es la mayor. Varía porque **el orden de las detecciones no es determinista** (H47) |
| Lado del rostro en el dataset | mediana **302 px** (p5 235, p95 338): se grabó muy cerca de la cámara |
| Preprocesamiento nuevo frente al legacy | **idéntico bit a bit** |
| Modelo propio sobre su dataset, la cara como se grabó | **7454 de 7459 = 99.9 %** |
| Lo mismo con la cara **volteada** | **4205 de 7129 = 59.0 %** (feliz 50 %, triste 32 %, sorprendido 37 %) — **H43** |

Lo último **no es la accuracy del modelo**: es el dataset con el que se entrenó,
con fuga de datos (bug 13), así que mide si el pipeline reproduce el
entrenamiento, no si el modelo generaliza. Eso es el P9. Pero el 59 % sí dice
algo citable: el modelo no reconoce **la cara de su propio operador** en espejo,
que es la forma más pequeña de generalización que se le puede pedir. Es
evidencia directa de memorización para §3.3.4 y §3.3.5.

**Las decisiones, y por qué:**

1. **Se deshace el espejo antes de detectar (H43).** El dataset se grabó con la
   cámara tal cual y el pipeline voltea el frame para que el operador se vea
   como en un espejo. Sin deshacerlo, el modelo vería caras que nunca vio y
   acertaría el 59 % en vez del 99.9 %, **sin ningún error**. Es
   `frame_espejado`, igual que en el arm tracker: un hecho de la captura, no una
   preferencia. Una prueba exige, con el Haar y la red de verdad, que el frame
   volteado dé **exactamente** la misma predicción que la imagen como se grabó.
2. **Los backends reciben el recorte, no el 48×48 gris.** El enunciado escribía
   `predict(face_gray_48x48)`. Pero HSEmotion se entrenó con caras en color a
   224×224, y darle un 48×48 gris es medirlo con una entrada que no es la suya.
   Lo común a los tres es el detector y el recorte —que es lo que hace que
   clasifiquen las mismas caras—, y cada uno lleva el recorte a la entrada de
   **su** red. Para el modelo propio no cambia nada: su `preprocesar_48` es el
   del entrenamiento, línea a línea. **Medido** (tabla de abajo): a DeepFace le
   da igual —él mismo reduce a 48×48 gris—, y a HSEmotion le cuesta poco en
   total (75 % → 72 %, dentro del ruido de 183 caras) **pero le cambia qué
   acierta**: sorprendido del 89 al 60 %, triste del 84 al 66 %, neutral del 21
   al 53 %. Como el P9 reporta por clase, la elección se notaría allí.
   **Pendiente de confirmar por el operador** (P6.2).
3. **El preprocesamiento del modelo propio, intacto** (puntos 4, 9 y 11): el
   cascade de OpenCV, `scaleFactor` 1.1 y `minNeighbors` 5, y el orden
   `resize` sobre BGR → gris → `/255`, que se fija **contra el código legacy
   copiado tal cual**. El orden habitual (gris primero) da ±1 nivel de gris en
   uno de cada cuatro píxeles: poco, pero distinto, y la prueba lo distingue.
4. **Se elige el rostro mayor, no el primero.** Con una sola cara dan lo mismo;
   con un falso positivo, la cara del operador es la grande. Y es
   **determinista**, que «la primera» no es: OpenCV reparte la detección en
   varios hilos y devuelve el mismo conjunto de rectángulos en distinto orden
   entre llamadas idénticas (H47). `criterio="primera"` se acerca a los
   recortes del entrenamiento para el P9, pero no puede reproducirlos.
5. **`minSize` = 60 px** (`TELMEOW_EMOTION_MIN_LADO_ROSTRO`; 0 lo apaga). Parte
   el coste del Haar por la mitad, y la cara de operación le queda al doble: la
   caja del Haar mide **2.0 veces** la distancia entre las comisuras externas
   de los ojos (mediana de 74 imágenes del dataset, con Face Mesh), y esa
   distancia es la `escala_cara` que registró el head tracker en la
   calibración del 2026-09-06 —**61 px** de mediana (p5 56, p95 66) con la
   cámara integrada a 640×480—. O sea, una caja de **~120 px** en operación,
   frente a los ~300 del dataset: el operador grabó el dataset dos veces y media
   más cerca de la cámara de lo que se sienta para narrar.
6. **El orden de clases se lee de `emotion_model.meta.json`** (punto 1). El
   modelo se guardó sin metadatos, así que el JSON se **reconstruyó** desde los
   tres scripts legacy, lo dice dentro, y está en git aunque el `.keras` no. Su
   orden lo fija una prueba contra **las etiquetas que el operador puso a sus
   fotos al grabarlas**, que no salen de ningún código: con el orden del JSON
   acierta lo de arriba; con las clases rotadas una posición, se hunde. Sin
   JSON se supone el orden legacy y se avisa con `OrdenDeClasesSupuesto`
   —**forzado a mostrarse**, porque DeepFace silencia todos los warnings del
   proceso al importarse (H45)—; con un número de salidas distinto de 5, no se
   supone nada. Una red que no acabe en softmax se rechaza: su «confianza» no
   sería una probabilidad y el umbral de la histéresis no significaría nada.
7. **Estabilización** (puntos 6 y 13): `ClassHysteresis` con `n_frames` = 5 y
   `min_confidence` = 0.6. A 5 Hz es una ventana de 1 s, y cambiar de expresión
   exige tres votos seguidos: 0.4-0.6 s desde que cambia la cara, más los
   350 ms del fundido del visor. 0.6 está por encima de la duda entre dos
   clases (~0.5), que es lo que frena la alternancia que la mayoría sola no
   frena. **Ninguno de los dos está medido**: se calibran con `--record`.
8. **La confianza que devuelve `process` vale `None` cuando no hay una medida
   que la respalde ahora** —en manual, sin cara, o si ganó una clase sin
   equivalente—, en vez de repetir la última. Un número congelado que parece
   vivo es el H31.
9. **Sin cara, la expresión se mantiene**: no vuelve a neutral, igual que la
   cabeza del robot se queda donde estaba cuando el head tracker pierde el
   rostro. Si el robot debe relajarse cuando el operador se levanta es una
   decisión del P7, que ve los tres estimadores a la vez.
10. **CONCENTRADO es manual** (punto 7): `toggle_manual(e)` acepta las seis
    expresiones del visor, la misma otra vez la suelta, y `clear_manual()`
    vuelve a automático. **La inferencia sigue corriendo en manual**, para que
    al soltar la expresión sea la de la cara de ese momento. El orden de las
    seis es el de los botones del visor (`config.VIEWER_EMOTIONS`, fijado
    contra el HTML), que es el de las teclas 1..6 del P7 y el del panel del P10;
    **no** es el orden de clases del modelo.
11. **Qué hacer con asco, miedo y desprecio** (punto 2; HSEmotion añade
    desprecio). `colapso="cercana"`, por defecto, suma su probabilidad a la
    expresión más parecida —asco y desprecio a enojado, por la «tríada hostil»
    de Izard (1977); miedo a sorprendido, porque comparten las unidades de
    acción 1, 2, 5 y 26 y es la confusión clásica de los estudios de Ekman—.
    `"ninguna"` las deja fuera: si gana una de ellas no hay voto. Se mapea
    **por nombre**, nunca por posición, y una clase desconocida se rechaza en
    vez de adivinarla. ⚠️ Verificar las dos referencias antes de citarlas.

**Las dos librerías, probadas de verdad** en un entorno aparte (§4.0.8, H45),
con `tools/medir_emocion.py --backend B --paso 40`: 183 caras del dataset
propio, cada una con cuatro entradas. **No es la accuracy de nadie** —una sola
persona, expresiones posadas—: es para ver qué convenios respeta cada backend.

| Entrada | Modelo propio | DeepFace 0.0.102 | HSEmotion 0.3.0 |
|---|---|---|---|
| El recorte tal cual (cada uno lo prepara a su modo) | 100 % | 27.9 % | **74.9 %** |
| La foto volteada | **57.5 %** (H43) | **47.1 %** (H46) | 74.1 % |
| Colores al revés (RGB por BGR) | 100 % | 27.9 % | 72.7 % |
| 48×48 gris, la entrada literal del enunciado | 100 % | 28.4 % | 71.6 % |
| Coste por cara, en CPU | 11 ms | 18 ms | **119 ms** |

Lo que esto verifica y lo que no:

- **El mapeo por nombre funciona con las clases reales** de las dos librerías
  —ninguna etiqueta desconocida, y las probabilidades colapsadas suman 1—, y
  con DeepFace importado el modelo propio sigue acertando 183 de 183 y el aviso
  de orden supuesto **sale** (H45). Dos pruebas nuevas lo atan con las
  librerías de verdad; se saltan en `.venv` y corren en el entorno de
  benchmark.
- **El convenio de color no se puede verificar con aciertos**: al modelo propio
  y a DeepFace les da igual —los dos pasan a gris, y el gris de un RGB tomado
  por BGR es otro, pero no tanto como para cambiar una predicción— y a
  HSEmotion le cambia 4 caras de 183. Se fija por el código fuente de cada
  uno: el entrenamiento legacy lee con `cv2.imread` (BGR), DeepFace documenta
  BGR, y HSEmotion hace `PIL.Image.fromarray` y normaliza con las medias de
  ImageNet en orden RGB. Por eso el punto 14 del P7 lo trata como convenio y
  no como riesgo: yo esperaba que pasarle el RGB de MediaPipe degradara el
  modelo propio, y medido no le cuesta nada. **Lo que sí lo degrada es el
  espejo.**
- **HSEmotion es demasiado lento para el bucle en vivo** (119 ms por cara, más
  el Haar, cada 200 ms). Sirve para el P9, que no corre en vivo; en el demo se
  puede elegir, pero se notará.
- **Adelanto del P9, con cautela**: DeepFace queda cerca del azar con este
  operador (20 % con cinco clases), y HSEmotion lee su cara neutra como triste
  (27 de 38).

Pesos con que se midió, para poder decirlo en la tesis:

| Archivo | Bytes | SHA-256 |
|---|---|---|
| `facial_expression_model_weights.h5` (DeepFace) | 5 977 392 | `e8e8851d3fa05c001b1c27fd8841dfe08d7f82bb786a53ad8776725b7a1e824c` |
| `enet_b0_8_best_afew.pt` (HSEmotion) | 16 419 305 | `47c1423f3e6f50e3750bf7b0eda7db947c9ce0c2637e1766bf2187eddc652b17` |

El `.pt` de HSEmotion es **una red serializada entera con pickle**, y
`torch.load` la ejecuta al cargarla —PyTorch avisa de ello—: por eso
`torch < 2.6`, y por eso la huella, que permite comprobar que el archivo
descargado es el mismo antes de cargarlo.

**Para el P7, tres cosas que este prompt deja dichas:**

- **`process` recibe el frame en BGR**, no el RGB que el P7 comparte con
  MediaPipe (punto 1 del P7). `BGR2GRAY` sobre un RGB pesa el rojo como azul y
  da otra cara gris, sin error. Medido, al modelo propio no le cambia ningún
  acierto, pero es el convenio del entrenamiento y no cuesta nada respetarlo:
  el BGR ya lo tiene, es lo que entrega la cámara.
- **La inferencia cuesta ~30 ms** (20 de Haar + 10 de red) **cada 200 ms**. En un
  bucle único a 30 fps (33 ms por frame) eso es un frame tardío de cada seis,
  sumado a Face Mesh y BlazePose. Si se nota, el bloque de emoción va en un
  hilo aparte, que es para lo que existe el desacople de tasas.
- `EmotionClassifier.desde_config(cfg)` lo construye con todo lo de la
  configuración, incluido `frame_espejado = cfg.camera.flip`.

**95 pruebas nuevas** (`tests/test_emotion_classifier.py` y
`tests/test_medir_emocion.py`), cada convenio atado a algo de fuera: el orden de
clases a las etiquetas del operador, el preprocesamiento al código legacy
copiado, las expresiones al HTML del visor, `model(x)` a `model.predict`, y el
espejo de punta a punta con el Haar y la red de verdad. Cada una de las que
fijan un convenio va con otra que comprueba que **detecta** el fallo —el orden
rotado hunde los aciertos, el orden habitual de gris y `resize` da otro tensor,
DeepFace silenciando los warnings de verdad los silencia—. Dos usan DeepFace y
HSEmotion reales: pasan en el entorno de benchmark y se saltan en `.venv`. Y
una corre **el modo demo entero** con cámara, teclas y backends simulados y sin
ventana: es la primera prueba del proyecto que ejecuta un demo, que hasta ahora
solo vigilaba pyflakes.

**El demo** (`python -m telmeow_teleop.emotion_classifier`): caja de rostro con
la clase nativa del backend encima, la expresión que se manda al robot con su
confianza, al lado **la predicción cruda** —la lección del H31—, barras de las
cinco probabilidades, la ventana de votos, Hz de inferencia, latencia, y aviso
de cámara negra. Teclas: `1..6` forzar expresión, `0` automático, `b` siguiente
backend —se salta los no instalados—, ESPACIO etiqueta, `q` salir. Con
`--publish` manda `emo` al visor, y con `--record` escribe una fila por
inferencia: predicción cruda, las cinco probabilidades, la expresión enviada,
su confianza, latencia y brillo.

### Lo que falta

1. **P6.1 — Verlo con el operador en el visor.** Es lo único que verifica el
   orden de clases **de punta a punta**, hasta los ojos del robot. Con el broker
   y el visor levantados (§4.2):

   ```
   python -m telmeow_teleop.emotion_classifier --publish --record emocion_2026-10-XX.csv
   ```

   | Etiqueta | Qué hacer | Tiempo | Qué debe hacer el robot |
   |---|---|---|---|
   | 0 | Cara relajada, mirando la pantalla | 10 s | Ojos NEUTRAL |
   | 1 | Sonrisa amplia, como al ver a un amigo | 10 s | FELIZ |
   | 2 | Comisuras abajo, como al oír una mala noticia | 10 s | TRISTE |
   | 3 | Cejas arriba y boca abierta, como en una sorpresa | 10 s | SORPRENDIDO |
   | 4 | Ceño fruncido, como al regañar | 10 s | ENOJADO |
   | 5 | Leer en voz alta un párrafo cualquiera, natural | 60 s | Sin parpadear entre expresiones |
   | 6 | Pulsar `6` (CONCENTRADO), luego `6` otra vez | 10 s | CONCENTRADO, y luego la de la cara |

   No hace falta mirar el visor mientras se sostiene la cara: el CSV guarda la
   expresión enviada en cada inferencia (`emocion`) junto a la predicción cruda
   (`prediccion`), y se comprueba después etiqueta a etiqueta. Basta un vistazo
   a los ojos del robot al final de cada etiqueta. Es la cara del operador
   **a la distancia de narrar**, no tan cerca como en el dataset (caras de
   ~120 px frente a ~300): si el modelo propio falla aquí y no en el dataset,
   eso también es un resultado.

   La etiqueta 5 es la que calibra `n_frames` y `min_confidence`: cuántos
   cambios de expresión hay narrando con naturalidad, y cuántos sobran.
   Calibrarlos es repasar las predicciones crudas grabadas por la
   `ClassHysteresis` con otros valores, sin volver a grabar.

   `python tools/analizar_registro_emocion.py emocion_2026-10-XX.csv` lo hace:
   por etiqueta, la expresión enviada frente a la esperada, caras perdidas,
   brillo y cambios por minuto; luego comprueba que con los parámetros grabados
   la reproducción da **exactamente** la columna `emocion` (si no, la rejilla no
   vale), y da la rejilla `n_frames` 3-9 × `min_confidence` 0.5-0.8: cambios
   por minuto en la etiqueta 5 y latencia de entrada en las 1-4.
2. **P6.2 — Confirmar la interfaz de los backends** (decisión 2).
3. **P6.3 — ¿Dónde están las otras 400 imágenes de cada clase?** (H44). Es para
   el P9, pero conviene saberlo antes de escribirlo.

### Enunciado original

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

--- AÑADIDO EN EL P6 ---

14. EL CLASIFICADOR DE EMOCIÓN RECIBE BGR, NO EL RGB COMPARTIDO. El punto 1
    convierte a RGB una vez para MediaPipe, pero `EmotionClassifier.process`
    espera el BGR de la cámara (ya volteado), que es el convenio del
    entrenamiento: BGR2GRAY sobre un RGB da otra cara gris, sin error (medido,
    al modelo propio no le cambia ningún acierto; a HSEmotion, algo). Lo que SÍ
    importa es el espejo. Constrúyelo con `EmotionClassifier.desde_config(cfg)`,
    que fija `frame_espejado = cfg.camera.flip`: el modelo propio se entrenó
    con caras SIN voltear, y con la cara volteada acierta el 59 % en vez del
    99.9 % (H43).

15. LAS TECLAS 1..6 SON `config.VIEWER_EMOTIONS`, EN ORDEN: neutral, feliz,
    triste, sorprendido, enojado, concentrado —el de los botones del visor—.
    La misma tecla otra vez suelta la expresión (`toggle_manual`), y `0` es
    `clear_manual()`. Es lo que ya hace el demo del P6.

16. EL BLOQUE DE EMOCIÓN CUESTA ~30 ms CADA 200 ms (20 de Haar, con minSize,
    y 10 de red). En el bucle único es un frame tardío de cada seis, sumado a
    Face Mesh y BlazePose; si se nota, va en un hilo aparte. Con `--no-emotion`
    no se construye el backend y nada carga el modelo.

17. EL HUD pone al lado de la expresión enviada la predicción cruda
    (`clasificador.ultima`), y la confianza vale `None` sin cara: no la
    rellenes con la última (H31). Sin cara la expresión se mantiene; si el
    robot debe volver a neutral cuando el operador se levanta, se decide aquí,
    junto con lo que hacen la cabeza (se queda) y los brazos (vuelven al
    reposo en 0.5 s).

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

--- AÑADIDO EN EL P6 ---

9. EL FORMATO DEL JSON ya está fijado: `"clases"` es lo único obligatorio
   —las etiquetas en el orden de las salidas, todas de `EMOTION_LABELS`, sin
   repetir—; el resto se conserva como documentación. `KerasBackend` rechaza
   un JSON que no cuadre con la red. Ejemplo: `emotion_model.meta.json`.

10. LA RED TIENE QUE ACABAR EN SOFTMAX. `KerasBackend` rechaza una salida que
    no sume 1: su «confianza» no sería una probabilidad y el umbral de la
    histéresis no significaría nada.

11. El volteo horizontal de la aumentación (punto 4) hace al modelo nuevo
    insensible al espejo, que el legacy no es (H43). El clasificador deshace el
    espejo igualmente, así que los dos ven la cara en la orientación de la
    cámara y la comparación es justa.

12. FER-2013 son caras ya recortadas, a 48×48 y en gris: `preprocesar_48` las
    acepta tal cual. En operación las caras salen del Haar, con su encuadre y
    a ~120 px antes de reducir (P6, decisión 5): esa diferencia es parte del
    domain gap que mide la configuración D del P9.

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

--- AÑADIDO EN EL P6 ---

10. PARA REPRODUCIR LOS RECORTES DEL ENTRENAMIENTO (A y B):
    `detectar_rostro(img, cascade, criterio="primera", min_lado=None)`. El
    legacy tomaba la primera detección y sin minSize; el clasificador de
    operación toma la mayor y usa minSize 60, y eso cambia ~100 y 6 recortes de
    7459 respectivamente. Y la «primera» no es reproducible del todo: con
    varios hilos OpenCV devuelve las detecciones en distinto orden (H47);
    `cv2.setNumThreads(1)` la hace fija, pero no tiene por qué ser la que vio
    el entrenamiento. Son ~100 imágenes de 7459: declararlo y seguir. El entrenamiento DESCARTÓ sin decirlo las 541
    imágenes sin cara (6.8 %; 13 % de enojado): el denominador de A y B tiene
    que decirlo.

11. EN DISCO HAY 1600 IMÁGENES POR CLASE, NO 2000 (H44). Faltan 400 de cada
    una, repartidas al azar por los índices 1-2000: parece un 20 % apartado
    como prueba. Preguntar al operador ANTES de definir B, que hace bloques
    por índice de frame, y los huecos están por todo el rango.

12. E y F corren en el entorno de `requirements-benchmark.txt`, no en `.venv`
    (ver su cabecera y §4.0.8). Cada backend recibe el recorte del Haar y hace
    su propio preprocesamiento (P6, decisión 2). Para comparar también con la
    entrada literal del enunciado del P6 —48×48 gris para todos— basta pasar
    `cv2.cvtColor(cv2.resize(r, (48, 48)), cv2.COLOR_BGR2GRAY)`; lo que le
    cuesta a cada librería está medido en el P6.

13. `colapso` ("cercana" o "ninguna") cambia E y F: reportar con cuál se midió,
    o las dos.

14. DeepFace silencia TODOS los warnings del proceso al importarse (H45). El
    aviso de orden de clases supuesto está forzado y sale igual, pero
    cualquier otro aviso del P9 se perderá si DeepFace ya se importó.
    Importarlo lo más tarde posible.

15. LA ORIENTACIÓN DE LA CARA CAMBIA EL RESULTADO (H43, H46): el modelo propio
    pasa del 99.9 % al 59 % con la foto volteada, y DeepFace del 28 % al 47 %.
    Evaluar todo con la cara como la ve la cámara —la del entrenamiento propio
    y la del clasificador en operación— y decirlo en la nota metodológica.
    `tools/medir_emocion.py --backend B` ya da las cuatro variantes de entrada.

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

**Ideas del operador (2026-09-27), para cuando llegue este prompt.** El narrador
**no sostiene un libro físico**: ve el libro digitalizado en la interfaz,
junto a sí mismo y al robot. Dos distribuciones posibles:

- **Tres columnas**: al centro, lo que ve el robot (sus ojos); a la izquierda,
  partida en dos, el robot tal como se ve y el operador a sí mismo (lo que capta
  la cámara); a la derecha, el texto.
- **Dos filas**: abajo, el libro digitalizado; arriba, partida en tres, el robot
  tal como se ve, lo que ve el robot y el operador a sí mismo.

Consecuencia ya aplicada al P5: la postura de «sostener el libro» no se evalúa.

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
     componente débil de BlazePose en monocular. **Medido el 2026-09-27 y el
     2026-10-11** (P5.6): en postura quieta con las manos a la vista, la
     posición de la mano que da BlazePose tiembla **0.8-1.8 cm en
     profundidad** frente a **0.4-0.9 cm en el plano de la imagen**, entre
     **1.5 y 3.0 veces más**, en cuatro grabaciones y con los dos brazos. La
     razón más baja (1.5-1.6) es con las manos apoyadas en la cara, donde el
     movimiento real es nulo y todo lo que queda es ruido del estimador. Es la cifra limpia: la razón de temblor
     entre los dos canales del robot mezcla la profundidad con la geometría del
     cardán. La da `tools/analizar_registro_brazos.py`, etiqueta a etiqueta.
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
   persona en sesiones controladas —en disco quedan **1600 por emoción**, ver
   H44—. Validación en condiciones de operación, no evidencia de
   generalización. Y el modelo entrenado con él **no generaliza ni a esa misma
   persona en espejo**: acierta el 99.9 % de las 7459 caras con la cara como
   se grabó y el 59 % con la cara volteada (P6, H43). Es la prueba más barata de
   que memorizó, y complementa la cifra de la fuga del P9.

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
   algoritmo.** El cuello de botella no es el método —Face Mesh cuesta 5-7 ms
   por frame, el 7 % del presupuesto— ni resultó ser el sensor: es la
   exposición. (Con los brazos el cómputo ya no es despreciable: BlazePose
   suma 22 ms por frame, y con la emoción el bucle del P7 queda en ~32-34 ms de
   media; ver P5.1, punto 4. Cabe en los 20 Hz de publicación, pero el margen
   se declara.) La misma cámara, con el mismo código, mide **30 fps con buena luz
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

11. **El cable del hombro no está modelado.** La flexión extendida (P5.3) se
    verificó contra colisiones de brazo, unión y motor, pero el cableado no
    existe en el CAD. Se declara como supuesto de diseño: 180° de recorrido
    usado, el mismo que el ±90° original, con el cable relajado en flexión −90°
    para que la torsión quede en ±90°.

12. **El robot no tiene codo, y eso tiene un precio que el mapeo solo reparte.**
    La flexión de codo del operador se traduce en un **giro del brazo del robot
    hacia la cruz** (P5.4), no en un acortamiento; el movimiento lateral con el
    codo doblado llega atenuado; y los gestos de mano y muñeca —saludar moviendo
    solo la mano— no tienen contrapartida. Con las manos recogidas por fuera
    del tronco el robot queda entre abierto y en cruz. Medido en la
    verificación del 2026-10-11 (H42): con el saludo natural —codo abajo,
    antebrazo arriba, la mano de lado a lado— el robot se queda en cruz y se
    mueve ±5°. El saludo se resuelve reconociéndolo, no copiándolo (P5.7): el
    robot hace el suyo ~1.4 s después de que el narrador empiece, que es la
    latencia que se declara.

13. **La profundidad del brazo es una reconstrucción con una ambigüedad
    resuelta por preferencia.** El escorzo (P5.5) hace la profundidad coherente
    con la imagen y con el largo de los huesos, pero cada hueso tiene dos
    posturas que dan los mismos píxeles, y se elige entre ellas con un criterio
    —parecido a BlazePose, preferencia por el plano del tronco, continuidad—, no
    con una medida. Y la elección depende de la focal, que no está calibrada
    con un patrón sino **estimada con la grabación de validación** (H41): con
    la convención del head tracker (`focal = ancho`), el gesto de las manos
    junto a la cara quedaba en la frontera entre las dos soluciones; con 0.7
    veces el ancho, él y el de los brazos estirados salen bien a la vez, en dos
    grabaciones. Y la medida independiente con un objeto de tamaño conocido —el
    teléfono del operador a una distancia igual a su largo— da 0.68-0.76.

14. **Una mano solo cuenta si se ve dentro del cuadro, y al cabo de medio
    segundo** (H40). BlazePose inventa la mano que queda fuera de la imagen, y
    con visibilidad alta, así que la visibilidad no basta. El precio: una mano
    que entra en cuadro tarda medio segundo en mandar entera —mientras tanto el
    brazo lo lleva el codo—, y los gestos en el mismo borde del cuadro los
    sigue el codo. Y el de la zona de reposo, ahora de 35°: los movimientos
    pequeños con el brazo colgando no se transmiten.

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

8. **DeepFace y HSEmotion van en un entorno APARTE** (P6, H45). Solo hacen
   falta para el P9. Instalados con sus dependencias declaradas, meterían en
   `.venv` otra distribución de `cv2` —la headless, sin `imshow`— y un
   onnxruntime que exige un protobuf que rompe MediaPipe. La receta, verificada
   en un entorno limpio el 2026-10-19, está en la cabecera de
   `requirements-benchmark.txt`: los dos paquetes **sin dependencias**, y
   después las que de verdad usan, con versión.

   ```
   py -V:3.12 -m venv .venv-benchmark
   .venv-benchmark\Scripts\activate
   pip install -r requirements.txt
   pip install --no-deps deepface==0.0.102 hsemotion==0.3.0
   pip install -r requirements-benchmark.txt
   ```

   Dos avisos para cuando se use:

   - **Con la salida redirigida a un archivo, DeepFace revienta en Windows** la
     primera vez: imprime emojis al descargar sus pesos, y una tubería en
     Windows codifica en cp1252. `$env:PYTHONUTF8="1"` antes de lanzarlo.
   - Cada librería descarga sus pesos de GitHub la primera vez, a `~/.deepface`
     y `~/.hsemotion`. Las huellas SHA-256 con que se verificó están en el P6.

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
  espectador interpreta. El encuadre no decide por sí solo, y conviene no
  decir que sí: cuál de los dos se ve depende de la postura —con las manos a la
  altura de la cara se ve la mano (91 %) y no el codo; narrando, al revés (codo
  86-96 %, mano 74-75 %)—. La alternativa (hombro→codo, el ángulo articular
  estricto) está implementada y conmutable, y la elección se hizo con datos.

- **«¿Qué hace el robot cuando usted dobla el codo?»** *(nueva, P5.4)*
  Su brazo no se puede acortar, así que lo que el codo del operador «esconde»
  se despliega hacia el costado: al recoger la mano, el brazo del robot gira
  hacia la cruz en proporción a cuánto está recogido. Solo con la muñeca por
  fuera del tronco; si la mano cruza por delante del cuerpo, el brazo se va
  cerrando poco a poco hasta su tope, que alcanza a unos 6 cm del borde del
  tronco. La regla la propuso el propio operador tras ver que, con la dirección
  sola, recoger la mano dejaba el robot apuntando adelante (H38, medido).

- **«¿Cómo sabe a qué profundidad está el brazo, con una sola cámara?»** *(nueva, P5.5)*
  No se fía de la profundidad de BlazePose, que es su coordenada más débil: se
  midió que ponía las manos 35 cm por delante cuando estaban junto a la cara
  (H39). La imagen dice por qué rayo se ve cada articulación, y el largo del
  hueso a qué distancia de la anterior está: la articulación queda en la
  intersección de un rayo con una esfera (Taylor, 2000). Queda una ambigüedad
  de dos soluciones por hueso que se resuelve por preferencia, y se declara
  (§3.3.13). Si preguntan por la focal: se estimó con dos grabaciones de
  validación, como la única que acierta a la vez dos gestos de referencia
  opuestos —manos junto a la cara, brazos estirados hacia la cámara—, y se
  midió después con un objeto de tamaño conocido a distancia conocida: 0.7
  estimada, 0.68-0.76 medida (H41).

- **«¿El robot imita el saludo del narrador?»** *(nueva, P5.7)*
  No: lo reconoce. Un saludo es un giro del antebrazo y el robot no tiene
  antebrazo; copiado, el brazo apenas se movía ±5° (H42, medido). Así que el
  sistema reconoce el saludo —la mano por encima del hombro, cerca de su plano,
  cinco vaivenes con ritmo— y el robot hace su propio saludo. Medido en la
  verificación: reconoce los saludos del protocolo y el saludo espontáneo con
  que el narrador abrió el cuento, y no salta ni una vez en el resto de la
  narración. Cuesta ~1.4 s de latencia, y se declara.

- **«¿Qué pasa cuando BlazePose se equivoca de mano?»** *(nueva, P5.6)*
  Pasa, y con visibilidad alta: con el brazo colgando bajo la mesa, BlazePose
  inventa la mano y la hace saltar de un frame al siguiente entre dos sitios, con
  visibilidades de 0.7-0.96. Por eso la mano solo cuenta dentro del cuadro y al
  cabo de medio segundo sostenida (H40). Medido: el brazo quieto pasó de estar
  en reposo el 16 % del tiempo al 71 %, y en los gestos el comando no cambia en
  el 90 % de los frames.

- **«¿Cómo pasa de un brazo humano, con más grados de libertad, a uno de dos?»** *(nueva, P5.3)*
  No pasa ángulos: pasa **la dirección** en que apunta el brazo. Se filtra como
  vector, se escala dentro de la envolvente medida del robot (que depende de
  hacia dónde apunte) y los ángulos del cardán se calculan al final, con una
  sola rama de la cinemática inversa. Pasar ángulos articulares tenía una
  costura justo en «brazo al frente» que hacía dar la vuelta al brazo; está
  medido (H35) y se muestra con la figura de `tools/comparar_mapeo_brazos.py`.

- **«¿Por qué extendió la flexión a −180° si su barrido llegaba a ±90°?»** *(nueva)*
  Porque ±90 era el rango del slider, no del servo, y porque con ±90 la costura
  cae en el espacio de trabajo. Y la zona nueva **se barrió** con el mismo
  criterio que la de agosto: el arnés reprodujo la tabla original fila por fila
  antes de medir la nueva. La simetría `(a, f) ≡ (−a, f ± 180)` predecía la
  zona a un paso de 5°, pero no exacta, así que manda lo medido. La unión y el
  motor del brazo, que el detector del visor no revisa, se comprobaron aparte:
  sin choques.

- **«¿Y el cable del hombro con 180° de giro?»** *(nueva)*
  No está modelado en el CAD, así que se declara como supuesto de diseño, no se
  presenta como verificado. El argumento: el mapeo solo usa flexión en
  [−180, 0], el mismo recorrido de 180° que el ±90° original, desplazado. Con
  el cable relajado en −90° (brazo al frente) la torsión queda en ±90°, igual
  que en el diseño que ya se daba por bueno.

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
               target="hibrido",       # P5.5 · "hibrido" (defecto), "mano" o "codo"
               invert_abduccion=False, invert_flexion=False,  # verificado, H30
               reposo_abduccion=-55.0, reposo_flexion=0.0,    # NO es el cero
               gracia_s=0.5, velocidad_max=180.0,             # grados/s
               mapeo="posicion",       # P5.4 · "direccion" (P5.3) · "articular"
               banda_tronco=0.1,       # cierre al cruzar el tronco, x largo (6 cm)
               zona_reposo=35.0,       # P5.6 · grados desde la vertical; 0 la apaga
               escorzo=True,           # P5.5 · corregir la profundidad (H39)
               focal_relativa=0.7,     # P5.6 · focal / ancho, estimada (H41)
               rampa_mano=0.5,         # P5.6 · s hasta fiarse de una mano (H40)
               saludo=True)            # P5.7 · el robot saluda solo (H42)
t.process(frame_rgb) -> ArmAngles | None      # ESTIMACION · None = sin pose
t.comando(pose, timestamp=None) -> ArmAngles  # COMANDO · nunca tiene huecos
t.map_posicion(canal, vector_m, largo_m, timestamp=None)   # P5.4 · sin camara
t.map_direccion(canal, h, timestamp=None)                  # P5.3 · sin camara
t.map_to_robot(canal, abduccion, flexion, timestamp=None)  # articular
t.mapeo             # settable en caliente y sin salto
t.vectores ; t.vectores_codo  # {Side: (L, S, A)} en METROS · CSV p_* y c_*
t.extensiones       # {Side: |p| / (|c| + |p - c|)} · 1 = brazo estirado
t.direcciones ; t.retargetings  # dirección efectiva (CSV h_*) · psi, alfa, rama
t.pesos_mano        # {Side: 0..1} · cuánto mandó la mano frente al codo
t.pesos_reposo      # {Side: 0..1} · cuánto se llevó al reposo
t.vectores_bp ; t.vectores_codo_bp  # BlazePose ANTES del escorzo (CSV pb_*, cb_*)
t.puntos_imagen     # {Side: {hombro, codo, mano: (u, v)}} · píxeles (CSV img_*)
t.confianzas_mano   # {Side: 0..1} · P5.6 · 0 fuera de cuadro, 1 tras la rampa
t.pesos_saludo      # {Side: 0..1} · P5.7 · cuánto manda el saludo del robot
t.aplicar_saludo(canal, salida, timestamp)  # el saludo, sin cámara
t.hombros_mundo     # (ancho, dz) de BlazePose en m · el ancla del escorzo (CSV hombros_*)
t.aplicar_reposo(canal, direccion, salida)  # la zona de reposo, sin cámara
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
vector_brazo(hombro, destino, base, inclinacion=0.0) -> (L, S, A) en metros | None
direccion_brazo(hombro, destino, base, inclinacion=0.0) -> (L, S, A) unitario | None
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
y luego `python tools/analizar_registro_brazos.py brazos.csv`. Para rehacer la
grabación con otros parámetros —otra focal, otra zona de reposo, sin escorzo,
sin saludo— por el `ArmTracker` real: `python tools/reproducir_brazos.py
brazos.csv --focal 1.0 --comparar`. Para el coste de BlazePose por
`model_complexity`: `python tools/medir_blazepose.py`.
Teclas: `k` mapeo · `m` espejo · `t` mano/codo · `b` `f` invertir signos (solo
en `articular`) · `r` extremos · `z/Z x/X v/V n/N` ganancias (solo en
`articular`) · ESPACIO etiqueta · `q` salir.

El HUD marca con `~` los canales cuyo valor **no** viene de una medida de ese
frame, e imprime `tronco:` con el estado y, si está retenido, su edad. **Imprime
el dato crudo junto a su interpretación** (`cadera 0.00` al lado de `tronco: …`)
a propósito: es lo que permitió detectar el H31.

Columnas del CSV, y la distinción importa al leerlo: `crudo_*` es la **medida**
(vacío si no se pudo medir), `arm*` es el **comando publicado** (nunca vacío),
`estimado_L`/`estimado_R` dicen cuál de los dos casos es cada fila, y
`tronco_origen` de dónde salía la vertical. Los sufijos `_L`/`_R` son **siempre**
el canal del robot, también en `crudo_*` y `vis_*`.

## `retarget.py`

```python
from telmeow_teleop.retarget import (direccion_por_posicion, RetargetBrazo,
                                     polares, direccion, alcance_robot,
                                     escala_radial, ALCANCE_HUMANO, BANDA_TRONCO)

direccion_por_posicion(q, banda=BANDA_TRONCO) -> (L, S, A)  # P5.4, ver arriba
polares(h) -> (psi, alfa)          # psi: angulo con la cruz · alfa: 0 arriba, 90 frente
direccion(abduccion, flexion)      # cinematica directa del cardan
alcance_robot(alfa, lado, rama)    # envolvente de las tablas 2D
RetargetBrazo(lado)(h) -> Retargeting | None   # direccion -> angulos, una rama
```

Solo biblioteca estándar y `limits`. Todo lo que decide el mapeo del brazo vive
aquí y se prueba sin cámara (`tests/test_retarget.py`, `tests/test_posicion.py`).

## `escorzo.py`

```python
from telmeow_teleop.escorzo import (Camara, interseccion, anclar_hombros,
                                    reconstruir_brazo, PESO_PLANO, PESO_CONTINUIDAD,
                                    FOCAL_RELATIVA)

cam = Camara.desde_imagen(ancho, alto, focal_relativa=FOCAL_RELATIVA)  # 0.7 (H41)
cam.rayo(u, v) ; cam.proyectar(punto_3d)
anclar_hombros(cam, img_a, img_b, mundo_a, mundo_b) -> (hombro_a, hombro_b) | None
interseccion(rayo, centro, radio, dz_bp, dz_previa=None) -> (punto, exacto)
reconstruir_brazo(cam, hombro_3d, img_codo, img_mano, largo_brazo,
                  largo_antebrazo, dz_codo_bp, dz_mano_bp, previo=None)
    -> (codo, mano, (dz_codo, dz_mano))
```

Sin estado, solo numpy, probado con una cámara sintética
(`tests/test_escorzo.py`): el brazo verdadero se proyecta, se le da a BlazePose
una profundidad equivocada y se exige recuperar el verdadero.

## `saludo.py`

```python
from telmeow_teleop.saludo import (DetectorSaludo, senales_saludo, patron_saludo,
                                   AMPLITUD, VAIVENES, MEDIO_PERIODO_MAX,
                                   ALTURA_MIN, ALTURA_MAX, PROFUNDIDAD_MAX,
                                   VENTANA, FIN, RAMPA)

senales_saludo(px_mano, px_hombro, px_hombro_contrario, profundidad_m)
    -> (x, y, p) | None          # x, y en anchos de hombro; p corregida, en m
d = DetectorSaludo()             # uno por brazo
d.actualizar(t, muestra_o_None) -> bool   # None = mano no de fiar: se reinicia
d.activo ; d.reiniciar()
patron_saludo(t) -> (abduccion, flexion)  # el saludo del visor, rama del Python
```

Solo biblioteca estándar. El `ArmTracker` lo usa solo (`saludo=True`); el
detector se prueba sobre la grabación del 2026-10-11 (`tests/test_saludo.py`).

## `emotion_classifier.py`

```python
from telmeow_teleop.emotion_classifier import (EmotionClassifier, KerasBackend,
                                               DeepFaceBackend, HSEmotionBackend,
                                               crear_backend, BackendNoDisponible,
                                               Emocion, Distribucion, BACKENDS)
from telmeow_teleop.config import VIEWER_EMOTIONS   # las 6 del visor, orden de sus botones

c = EmotionClassifier.desde_config(cfg)     # backend, tasa, histéresis, cascade, espejo
c.process(frame_bgr) -> Emocion(etiqueta, confianza)   # BGR, NO el RGB de MediaPipe
c.toggle_manual("concentrado") ; c.clear_manual() ; c.manual
c.cambiar_backend(crear_backend("hsemotion", cfg))     # conserva la expresión
c.ultima        # Distribucion CRUDA de la última inferencia (antes de la histéresis)
c.rostro        # (x, y, w, h) en el frame recibido, el volteado · None sin cara
c.ventana ; c.inferencias ; c.inferencias_sin_rostro ; c.latencia_ms ; c.t_ultima

b = KerasBackend(ruta)                      # orden de clases de <ruta>.meta.json
b.predict(rostro_bgr) -> Prediccion(etiqueta, confianza)   # la interfaz del P6
b.distribucion(rostro_bgr) -> Distribucion(etiqueta, confianza, probabilidades, nativa)
b.clases ; b.metadatos ; b.modelo

# Funciones puras, para el P9:
detectar_rostro(frame_bgr, cascade, criterio="mayor" | "primera", min_lado=None)
preprocesar_48(rostro) -> (1, 48, 48, 1) float32     # el del entrenamiento, bit a bit
colapsar(puntuaciones_por_nombre, "cercana" | "ninguna") -> Distribucion
resolver_clases(ruta_modelo, n_salidas) ; leer_metadatos(ruta)
```

`Emocion.etiqueta` nunca es `None` —es siempre una de las seis del visor— y
`Emocion.confianza` vale `None` cuando no hay una medida que la respalde ahora
(manual, sin cara, clase sin equivalente). `process` infiere a `emotion_hz` por
fecha límite y entre medias devuelve lo último decidido, así que se puede
llamar en cada frame.

Los backends reciben el **recorte del rostro** en BGR y lo llevan cada uno a la
entrada de su red; los opcionales lanzan `BackendNoDisponible` (una
`ImportError`) si su librería no está, con la instrucción de instalación.

El JSON de metadatos de un modelo propio (`<modelo>.meta.json`) solo exige
`"clases"`: las etiquetas en el orden de las salidas de la red, todas de
`EMOTION_LABELS` y sin repetir. Lo demás —fecha, accuracy, hash del dataset—
es documentación y se conserva en `KerasBackend.metadatos`. El P8 lo escribe
con ese formato.

Modo demo: `python -m telmeow_teleop.emotion_classifier [--backend B]
[--publish] [--record CSV]`. Las cifras del P6: `python tools/medir_emocion.py
[--paso 20] [--registro-cabeza calibracion_20260906_integrada.csv]`.

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

## 7.17 El P6: el espejo, el dataset, DeepFace y el Haar (H43 a H47)

**H43 — El modelo propio solo reconoce la cara sin voltear.** El dataset se
grabó con `cap.read()` tal cual —`Creation_of_dataset.py` no voltea— y el
pipeline voltea cada frame para que el operador se vea como en un espejo
(`cfg.camera.flip`). Medido con `tools/medir_emocion.py` sobre las 8000
imágenes del propio dataset (las que tienen cara; volteadas, el Haar encuentra
algunas menos):

| Clase | La cara como se grabó | Volteada |
|---|---|---|
| feliz | 1545/1546 | 764/1531 = 50 % |
| triste | 1537/1538 | 467/1437 = 32 % |
| sorprendido | 1444/1444 | 527/1432 = 37 % |
| neutral | 1543/1544 | 1316/1444 = 91 % |
| enojado | 1385/1387 | 1131/1285 = 88 % |
| **total** | **7454/7459 = 99.9 %** | **4205/7129 = 59.0 %** |

No es un fallo del código sino del modelo: una red que hubiera aprendido
expresiones, y no fotos, sería casi simétrica. Y habría pasado en silencio: con
el frame del P7 tal cual, el clasificador habría funcionado —daría expresiones,
con confianzas altas— y acertaría la mitad. Consecuencias: el clasificador
deshace el espejo antes de detectar (`frame_espejado`, con una prueba de punta a
punta con el Haar y la red de verdad); §3.3.4 gana una evidencia de
memorización; y el P8 lo evita por construcción con la aumentación por volteo.

**H44 — En disco hay 1600 imágenes por emoción, no 2000.** Los nombres van de 1
a 2000 en las cinco clases, pero faltan 400 de cada una, repartidas por todo el
rango —rachas de 1 a 5, entre 26 y 49 en cada bloque de 200—. No es el borrado
de un tramo malo: parece un 20 % apartado al azar, quizá como conjunto de prueba
en la otra máquina (la ruta del legacy apunta a `C:\Users\harol\...`). §3.3.4
decía 2000. Afecta al P9, no al P6. **Pendiente de preguntar al operador.**

Del mismo barrido: el Haar no encuentra cara en **541 de las 8000** (6.8 %;
enojado 13 %, sorprendido 10 %), que el entrenamiento descartó sin decirlo, y en
unas cien la primera detección —la que usó el entrenamiento— no es la mayor
(H47). Las 7
detecciones que cambia `minSize` = 60 son falsos positivos de 57-83 px sobre
una mejilla o la barbilla, en fotos con la cabeza girada cuya cara el Haar no
encontró (se miraron tres; las otras cuatro son fotos consecutivas de una de
ellas, con la misma caja): el modelo se entrenó también con esos recortes,
etiquetados con la emoción de la foto.

**H47 — El orden de las detecciones del Haar no es determinista.** Dos barridos
idénticos del dataset dieron 102 y 93 imágenes en que «la primera detección no
es la mayor». Con 8 hilos, 15 llamadas idénticas sobre cada una de 40 imágenes
con varias detecciones devolvieron el mismo **conjunto** de rectángulos siempre,
pero en **distinto orden** en 16 de las 40; con `cv2.setNumThreads(1)`, en
ninguna. Consecuencias: la regla del legacy —«la primera»— eligió al azar en
esas fotos, y nadie puede reproducir exactamente sus recortes; el clasificador
usa «la mayor», que solo depende del conjunto y desempata por posición (con una
prueba que lo exige para todas las permutaciones); y cualquier cifra que dependa
de «la primera» varía entre ejecuciones, cosa que la herramienta ya avisa.

**H45 — DeepFace no cabe en el entorno de operación, y al importarse cambia el
proceso entero.** Sus dependencias declaradas (0.0.102) traen
`opencv-contrib-python-headless` —una tercera distribución del módulo `cv2`, sin
`imshow`, que pisa los archivos de la que haya— y `onnxruntime`, cuya versión
actual exige `protobuf>=4.25.8`, cuando el pin 4.25.3 es el que mantiene viva a
MediaPipe. Y al importarse hace dos cosas globales, las dos en `DeepFace.py`:
fija `TF_USE_LEGACY_KERAS=1`, con lo que `tf.keras` pasa a ser Keras 2 y deja de
leer el modelo propio, y hace `warnings.filterwarnings("ignore")`, que silencia
**todos** los warnings del proceso —incluido el que avisa de un orden de clases
supuesto, que existe precisamente porque ese fallo no da error—. Soluciones,
las tres con prueba: un entorno aparte con DeepFace instalado sin sus
dependencias (`requirements-benchmark.txt`, §4.0.8), el backend propio con
`keras` directo en vez de `tf.keras`, y el aviso forzado a mostrarse. Y una
cuarta trampa, del mismo entorno: la primera vez imprime emojis al descargar
sus pesos, y con la salida redirigida en Windows (cp1252) eso es un
`UnicodeEncodeError` que aborta la descarga. `PYTHONUTF8=1`.

**H46 — DeepFace tampoco es simétrico, y al revés que el modelo propio.** Con
las mismas 183 caras, acierta el **27.9 %** con la cara como la ve la cámara y
el **47.1 %** con la foto volteada (feliz 35 → 57 %, triste 8 → 37 %,
sorprendido 37 → 65 %); HSEmotion, en cambio, da lo mismo (74.9 / 74.1 %). Con
una sola persona y una sola luz —la ventana queda a un lado de la imagen— no se
puede saber si es el modelo o la iluminación, y no le toca al P6 averiguarlo.
Lo que sí deja: **la orientación cambia el resultado de dos de los tres
backends**, así que el P9 tiene que fijarla y declararla. La del clasificador
es la de la cámara, que es la del entrenamiento del modelo propio.

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
| **H34** | **La exposición de la cámara sobrevive al proceso** (DirectShow la guarda en el driver) y dejó la imagen **negra a 30 fps sin ningún error**. Se pide explícitamente al abrir, y el HUD avisa del brillo | §4.0, P5, §7.16 |
| **H35** | **El brazo «daba la vuelta» por una costura del mapeo articular, no por la profundidad**: con flexión ±90, «al frente» tiene dos soluciones y se elegía por el signo de una componente que al frente es ruido (303 saltos en 9 min). Retargeting por dirección y flexión extendida a −180 | P5.3, §0.5 |
| **H36** | Las ganancias de abducción del H33 **no son ganancias**: son la envolvente del robot entre 90°. Se sustituyen por un escalado dependiente de la dirección que sale de la tabla | P5.3 |
| **H42** | **El saludo natural no pasa al robot**: con el codo abajo y el antebrazo arriba, la mano queda recogida por fuera del tronco y el mapeo `posicion` lleva el brazo a la cruz; el vaivén de la mano solo mueve el robot ±5°. Es un giro del antebrazo, que el robot no tiene. De tres caminos, el operador eligió que el robot **salude solo**: reconoce el saludo (mano por encima del hombro pero no de la cabeza, cerca de su plano, cinco vaivenes encadenados) y hace el del visor | P5.6, P5.7 |
| **H41** | **La focal del head tracker dejaba el escorzo en una frontera**: con `focal = ancho`, las manos junto a la cara caían justo entre las dos soluciones del codo (27° de la cruz con `PESO_PLANO` 0.4, 3° con 0.6), y ningún peso acertaba a la vez ese gesto y el de los brazos estirados. Con 0.7 veces el ancho aciertan los dos en dos grabaciones, y cuadra con la geometría del puesto. Medida después con el teléfono del operador a una distancia igual a su largo: 0.68-0.76 | P5.6, §3.3.13 |
| **H40** | **BlazePose inventa la mano que no ve, y con visibilidad alta**: con el brazo colgando, la mano bajo el cuadro saltaba cada frame a una doblada delante del pecho con visibilidad 0.7-0.96, y el híbrido la seguía; el brazo quieto estaba en reposo el 16 % del tiempo. La visibilidad no basta: la mano cuenta dentro del cuadro y tras medio segundo sostenida (71-100 %). Y el codo en el borde del cuadro no se corrige por escorzo sin mano fiable | P5.6, §3.3.14 |
| **H39** | **BlazePose ponía las manos 35 cm por delante cuando estaban junto a la cara**: con los codos abajo y las manos levantadas, su profundidad las dejaba 32-37 cm delante de los hombros mientras la imagen mostraba brazo y antebrazo casi con todo su largo. Ningún mapeo podía dar el gesto. Corrección por escorzo (rayo-esfera, Taylor 2000) con una ambigüedad resuelta por preferencia | P5.5, §3.3.13 |
| **H38** | **La dirección hombro→mano no ve la retracción**: al normalizar se tira la distancia, y con el codo doblado la mano queda delante del pecho, así que el robot apunta adelante con el brazo recogido. 32 s acercando y alejando las manos sin que la componente anterior bajara de 0.78 el 95 % del tiempo. Mapeo `posicion`, propuesto por el operador | P5.4, §3.3.12 |
| **H43** | **El modelo propio solo reconoce la cara sin voltear**: 99.9 % con la cara como se grabó y 59 % volteada, y el pipeline voltea cada frame. El clasificador deshace el espejo antes de detectar. Evidencia de memorización | P6, §3.3.4, §7.17 |
| **H44** | **En disco hay 1600 imágenes por emoción, no 2000**: faltan 400 de cada una, al azar por todo el rango. Y el Haar no ve cara en el 6.8 %, que el entrenamiento descartó sin decirlo | P6, P9, §3.3.4, §7.17 |
| **H45** | **DeepFace no cabe en el entorno de operación** (otra `cv2`, protobuf) y al importarse fija `TF_USE_LEGACY_KERAS=1` y **silencia todos los warnings**. Entorno aparte, `keras` directo y el aviso forzado | P6, P9, §4.0.8, §7.17 |
| **H47** | **El orden de las detecciones del Haar no es determinista** con varios hilos (mismo conjunto, otro orden, en 16 de 40 imágenes con varias caras): la «primera» del legacy eligió al azar. El clasificador usa la mayor, determinista | P6, P9, §7.17 |
| **H46** | **DeepFace tampoco es simétrico, y al revés que el modelo propio**: 28 % con la cara como la ve la cámara y 47 % volteada; HSEmotion da lo mismo. La orientación cambia el resultado de dos de tres backends: el P9 la fija y la declara | P6, P9, §7.17 |
| **H37** | **La simetría aproximaba la zona extendida, pero no la medía**: el reflejo `(a, f) ≡ (−a, f ± 180)` falló por un paso de 5° en 6-9 de 18 filas, y en el límite usado prometía 5° de más. Barrida con los detectores del visor (tras reproducir 37/37 filas de agosto); manda lo medido. Unión y motor: 0 choques. El cable, sin CAD, pasa a supuesto declarado | P5.3, §0.5, §3.3.11 |

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
