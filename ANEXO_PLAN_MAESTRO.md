# Anexo al plan maestro — consecuencias para los PROMPT 3 al 11

Este documento **no sustituye ni modifica** `TELMEOW_PLAN_MAESTRO.md`. Lo
complementa.

La diferencia con `HALLAZGOS_PLAN_MAESTRO.md` es de dirección:

- **HALLAZGOS** mira hacia atrás: qué discrepancias hay entre lo que el plan
  dice y lo que el código realmente hace.
- **Este anexo** mira hacia adelante: qué cosas descubiertas al ejecutar los
  PROMPT 1 y 2 **cambian el trabajo de los prompts que faltan**, y que no
  estaban contempladas ni en el código legacy ni en el plan.

Estado: escrito el 2026-08-30, tras cerrar el PROMPT 2.

---

## A. Lo que cambia para el PROMPT 3 (filtros temporales)

### A.1 El filtro One Euro depende del reloj, y el reloj tiene una trampa

Es la consecuencia más directa y la que más conviene tener presente **antes** de
escribir `filters.py`.

El One Euro no es un suavizado fijo: su razón de ser es **adaptar el corte a la
velocidad de la señal**, y esa velocidad se estima con `dt`, el tiempo entre
muestras. El plan lo describe correctamente («suaviza fuerte cuando la señal está
quieta y deja pasar los movimientos rápidos sin retardo»), pero eso solo es
cierto si `dt` es correcto.

Lo medido en el PROMPT 2 (hallazgo H11): en Windows, `time.monotonic()` tiene una
resolución de **~15.625 ms**. A 30 fps el `dt` real es de 33.3 ms, y con ese
reloj se leería como 31.25 ms o 46.875 ms — un error de hasta el 40 %.

Dos consecuencias concretas:

1. **El corte adaptativo se corrompe.** El filtro creería que la señal se mueve
   un 40 % más rápido o más lento de lo que se mueve, y ajustaría mal el
   suavizado. El síntoma sería «el One Euro no mejora nada respecto al EMA», y
   la causa no estaría en el filtro.
2. **`dt` puede salir exactamente 0** cuando dos muestras caen en el mismo tick
   del reloj. En la formulación del One Euro eso es una **división por cero**.

**Qué hacer en el PROMPT 3:**

- Usar `time.perf_counter()` para los timestamps, nunca `monotonic`.
- Que `OneEuroFilter.__call__(value, timestamp)` acepte el timestamp como
  argumento (como ya pide el plan) y **rechace o sustituya** `dt <= 0` de forma
  explícita, en vez de dejar que reviente.
- En la prueba que el plan pide (señal escalón con ruido), generar los timestamps
  de forma calculada (`i * dt`) y no acumulada, para que el error de punto
  flotante no se vuelva el sujeto de la prueba. Es la misma lección que dejó una
  prueba frágil en el PROMPT 2.

### A.2 La histéresis de clase ya tiene un consumidor definido

`ClassHysteresis` la usará `EmotionClassifier` (PROMPT 6), que corre a 5 Hz, no a
30. Con `n_frames` contado en **inferencias** y no en frames de vídeo, una
ventana de 5 significa un segundo entero de latencia para cambiar de expresión.
Conviene decidir el valor con esa tasa en mente, y documentarlo — el crossfade
del visor dura 350 ms, así que una histéresis demasiado larga se nota.

---

## B. Lo que cambia para los PROMPT 4 y 5 (trackers)

### B.1 `limits.py` devuelve objetos, no tuplas

El plan no especifica la firma de retorno. La implementada es:

```python
resultado = clamp_arm(balanceo, giro, "izq")
resultado.balanceo.value     # el ángulo recortado
resultado.as_tuple           # (balanceo, giro)
resultado.clamped            # si hubo recorte
resultado.balanceo.lower     # límite aplicable AHORA, para el HUD
resultado.balanceo.fraction  # posición en el rango, [0, 1]
```

Se diseñó así para que el HUD del PROMPT 7 pinte las barras sin recalcular nada.
`clamp_head` es análogo con `.pan` y `.tilt`.

### B.2 Los recortes lanzan `ValueError` ante NaN — hay que decidir qué hacer

El plan (PROMPT 5, bug 2) dice correctamente que hay que usar `math.isnan` en vez
de la guarda rota del legacy, pero **no dice qué hacer cuando aparece un NaN**.

`clamp_arm` y `clamp_head` lanzan `ValueError` a propósito, en vez de propagar el
NaN hasta el robot. Eso obliga a los trackers a tomar una decisión explícita:
**capturar la excepción y descartar el frame**, reutilizando el último valor
válido. Es coherente con el requisito 2 del PROMPT 7 y hay que implementarlo en
los trackers, no dejarlo para `main.py`.

### B.3 Ya no hace falta que `main.py` guarde el último valor válido

Descubierto leyendo el visor (hallazgo H2): **todos los campos del JSON son
opcionales**, y el visor conserva el último valor de los que falten.

El PROMPT 7 pide «si un estimador no produce resultado, reutiliza el último valor
válido en vez de publicar ceros». Eso se cumple simplemente **omitiendo el campo**
del mensaje. `PosePublisher.publish()` ya omite los `None`. Simplifica el diseño
de `main.py`: no necesita mantener estado para esto.

### B.4 La convención de signos del giro ya está fijada por el visor

La skill del visor lo dice y el rig lo implementa: **giro+ = adelante en AMBOS
brazos**, con el eje del brazo derecho invertido en el rig respecto al original.
Las tablas 2D de `limits.py` ya están en esa convención.

Consecuencia para el PROMPT 5: la decisión sobre el espejo (`mirror=True/False`)
no es libre — tiene que producir salidas coherentes con esa convención, o los
brazos del robot se moverán al revés que los del operador. Conviene verificarlo
mirando el visor con el pipeline en vivo, que es barato ahora que el transporte
está resuelto.

### B.5 Riesgo abierto: la versión de MediaPipe

`requirements.txt` fija `mediapipe==0.10.14` porque los PROMPT 4 y 5 asumen la API
clásica `mp.solutions.face_mesh` y `mp.solutions.pose`. La serie 1.0 reorganizó la
biblioteca en torno a MediaPipe Tasks y **podría no exponerla**.

**Antes de escribir una línea del PROMPT 4** hay que instalar y comprobar:

```python
import mediapipe as mp
mp.solutions.face_mesh, mp.solutions.pose
```

Si la API ya no existe en la versión que instale, los PROMPT 4 y 5 cambian de
forma sustancial (otra API, otro modelo de inicialización) y hay que replantearlos
antes, no a mitad.

---

## C. Lo que cambia para el PROMPT 6 (emoción)

### C.1 Dos bugs del legacy que el plan NO lista

El plan enumera 14 hallazgos. Estos dos no están, y afectan al PROMPT 6 y al 9:

**El cascade Haar se recarga en cada frame.** En `legacy/Emotion Recognition.py`
el `cv2.CascadeClassifier(...)` se construye **dentro** de `detect_face`, que se
llama una vez por frame. Son ~900 KB de XML parseados 30 veces por segundo. El
plan sí señala `model.predict()` por frame (bug 12), pero no esto, que es del
mismo orden de gravedad. El mismo patrón está en `Creation_of_dataset.py` y en el
script de entrenamiento.

**La ruta del dataset está escrita a fuego, y de otra máquina.**
`legacy/Training Emotion Recognition.py:9` tiene:

```python
dataset_path = r"C:\Users\harol\PycharmProjects\Tesis\emotions_dataset"
```

El usuario de esta máquina es `hlrm`, no `harol`. **El script legacy no corre tal
cual aquí**, lo que afecta directamente a las configuraciones A y B del PROMPT 9,
que necesitan reproducir el entrenamiento original. Hay que parametrizar la ruta
(o copiarla a `config.py`) antes de intentar esa comparación.

### C.2 Punto abierto: de dónde sale el cascade

Hay una divergencia que aún **no está resuelta**:

| Fuente | Usa |
|---|---|
| `legacy/Emotion Recognition.py:13` | `cv2.data.haarcascades + '...'` (el que trae OpenCV) |
| `telmeow_teleop/config.py` | la copia versionada en el repositorio |

Elegí la copia del repositorio porque está trackeada en git y hace el
preprocesamiento reproducible. **Pero no he podido comprobar si los dos archivos
son idénticos**, porque `cv2` todavía no está instalado. Si difieren, la detección
de rostros cambia y con ella las métricas del PROMPT 9.

Comprobación pendiente, en cuanto se instale `requirements.txt`:

```python
import cv2, hashlib, pathlib
a = pathlib.Path("haarcascade_frontalface_default.xml").read_bytes()
b = pathlib.Path(cv2.data.haarcascades + "haarcascade_frontalface_default.xml").read_bytes()
print("IDENTICOS" if a == b else "DIFERENTES")
```

### C.3 Confirmado: el orden de preprocesamiento y de clases es coherente

Verificado leyendo los tres scripts legacy:

- **Orden de clases** idéntico en `Creation_of_dataset.py:16`,
  `Training Emotion Recognition.py:12` y `Emotion Recognition.py:9`:
  `['feliz', 'triste', 'sorprendido', 'neutral', 'enojado']`. Ya está en
  `config.EMOTION_LABELS` con una prueba que lo fija.
- **Orden de preprocesamiento** idéntico entre entrenamiento e inferencia:
  recortar rostro → `resize(48,48)` **sobre BGR** → `cvtColor(BGR2GRAY)` → `/255`.
  Es un orden poco habitual (normalmente se convierte a gris antes de
  redimensionar), pero al ser el mismo en ambos lados es correcto. El PROMPT 6
  pide conservarlo tal cual: confirmado que hay que hacerlo.

### C.4 La versión de TensorFlow la fija el modelo, no la preferencia

`emotion_model.keras` se guardó con **Keras 3.3.3**. TensorFlow 2.15 y anteriores
traen Keras 2 y **no podrían cargarlo**. De ahí el `tensorflow==2.16.1` de
`requirements.txt`. No es una elección estética; cambiarla rompe el modelo
existente.

---

## D. Lo que cambia para el PROMPT 7 (integración)

### D.1 Esperar el enlace MQTT antes de emitir

Hallazgo H13. `connect_async()` devuelve al instante pero el enlace tarda ~2 s en
levantarse, y con QoS 0 lo publicado mientras tanto **se pierde sin dejar rastro**.
`main.py` debe llamar a `PosePublisher.wait_connected()` antes de entrar en el
bucle. Si no, los primeros dos segundos de cada sesión se pierden — justo cuando
el operador está mirando si funciona.

### D.2 El HUD necesita tres contadores, no uno

Hallazgo H12. El plan pide mostrar «Hz de publicación». No basta:

| Contador | Significa |
|---|---|
| `message_count` | salió a la red |
| `dropped_count` | no quise enviarlo (excedía la tasa) |
| `failed_count` | **no pude** enviarlo (sin enlace) |

Sin el tercero, el HUD puede informar 20 Hz mientras al broker no llega nada.
Ese fue exactamente el bug que se corrigió en el PROMPT 2.

### D.3 Patrón de cadencia del bucle, ya validado

El bucle de `main.py` debe usar fechas límite, no `sleep(periodo)`, y **saltar las
ranuras perdidas** en vez de dispararlas seguidas para recuperar:

```python
next_tick = max(next_tick + periodo, time.perf_counter())
time.sleep(max(0.0, next_tick - time.perf_counter()))
```

Sin el `max()`, tras una pausa del sistema el bucle emite una ráfaga que el
limitador de tasa descarta, y la tasa efectiva baja sin motivo real. Está
implementado y medido en `publisher._demo` (60/60 mensajes, 0 descartes, 20 Hz).

### D.4 Las barras del HUD ya no requieren cálculo

`AxisClamp` expone `lower`, `upper`, `fraction` y `clamped`. En el brazo,
`lower`/`upper` son los **interpolados para el giro actual**, así que la barra se
estrecha y ensancha en vivo, que es el comportamiento correcto y lo que el plan
pide («una barra que muestre su posición dentro del rango admisible, y en rojo
cuando el valor esté siendo recortado»).

---

## E. Lo que cambia para el PROMPT 9 (evaluación) y el documento de tesis

### E.1 Un instrumento de validación que el plan no contempla

Es, creo, el hallazgo más aprovechable para el capítulo de resultados.

El visor **vuelve a recortar** toda pose que recibe por MQTT
(`clampPoseExternal()`) y enciende el indicador `t-col` cuando tiene que hacerlo.
Es decir: **`t-col` se enciende exactamente cuando el `limits.py` de Python dejó
pasar una pose inválida**.

Eso convierte una decisión de arquitectura en una **métrica medible y
reportable**: el porcentaje de poses publicadas que el visor tuvo que recortar.
Es evidencia cuantitativa de que la primera capa funciona, obtenida sin
instrumentación adicional.

Y tiene una interpretación honesta ya prevista: las tablas 2D se midieron con los
demás DOF en home, así que **no cubren combinaciones simultáneas** (cabeza
inclinada + brazo elevado). Un `t-col` que se encienda de vez en cuando no es un
fallo del `limits.py`, es exactamente la limitación declarada, hecha visible.
Vale la pena reportarlo así en vez de esconderlo.

Durante la verificación del PROMPT 2, con la pose oscilante del demo, `t-col`
marcó «libre» todo el tiempo.

### E.2 Toda medida de tiempo debe usar `perf_counter`

Hallazgo H11. Con `time.monotonic()` en Windows el error sistemático en
magnitudes de esta escala (decenas de ms) es de alrededor del 15 %. Aplica a los
fps, a los Hz y a cualquier latencia que se reporte.

### E.3 La latencia declarada sigue sin estar medida

Hallazgo H3, y es lo que más conviene resolver antes de la defensa. El indicador
`t-lat` del visor está honestamente etiquetado como «proxy», pero mide el
**intervalo entre mensajes**, que está acotado por arriba por el periodo de
publicación: no puede variar con la latencia que dice aproximar. Hoy marcó
49-51 ms, que es exactamente el periodo de 20 Hz.

`PosePublisher` ya tiene `include_timestamp=True`, que añade un campo `t` con el
tiempo Unix en milisegundos. Falta el lado del visor (`Date.now() - o.t`) y
declarar la salvedad de que publisher y navegador solo comparten reloj si corren
en la misma máquina.

---

## F. Lo que cambia para los PROMPT 10 y 11 (panel y Raspberry)

- **Tres topics, tres consumidores.** Ya declarados en `config.py` con el
  comentario de quién lee cada uno: `pose` → visor; `page` y `story` → servicio
  de pantalla externa.
- **Los eventos discretos no se descartan.** `PosePublisher` nunca descarta por
  tasa un mensaje que lleve `gesture` o `page`. El PROMPT 10 puede publicar el
  gesto y la página desde el mismo evento con la garantía de que ambos salen.
- **El panel también debe esperar el enlace** antes de emitir (D.1).
- **El gesto de página usa −30°, no −35°** (hallazgo H4). Es un número que va en
  el documento.

---

## G. Prerrequisitos de entorno, que el plan no lista

Los tres bloquean trabajo y ninguno estaba contemplado:

1. **CPython 3.12 x64.** TensorFlow publica ruedas hasta cp313 y MediaPipe hasta
   cp312. En 3.13 y 3.14 la instalación falla. *(Resuelto: instalado.)*
2. **Mosquitto no basta con instalarlo.** Su configuración por defecto escucha
   solo en loopback y **no abre listener WebSocket**, sin el cual el visor no
   puede conectarse. Y si conviven el servicio y una instancia manual, se
   reparten el puerto 1883 sin dar error y los mensajes no pasan (hallazgo H15).
   *(Pendiente: requiere una acción con privilegios de administrador.)*
3. **`mosquitto` no queda en el PATH.** Hay que invocarlo por ruta completa o
   añadir `C:\Program Files\mosquitto` al PATH.

---

## Resumen: qué mirar antes de cada prompt

| Prompt | Antes de empezar, revisar |
|---|---|
| **3** — filtros | A.1: `perf_counter` para los timestamps y guarda de `dt <= 0` |
| **4** — cabeza | B.5: **confirmar que `mp.solutions` existe** en la versión instalada |
| **5** — brazos | B.1 (objetos), B.2 (NaN), B.4 (convención de signos) |
| **6** — emoción | C.1 (cascade por frame), C.2 (qué cascade), C.4 (TF 2.16) |
| **7** — integración | D.1 (esperar enlace), D.2 (tres contadores), D.3 (cadencia) |
| **8** — FER-2013 | C.4: el modelo nuevo también debe cargar con Keras 3 |
| **9** — evaluación | C.1 (ruta a fuego), E.1 (`t-col` como métrica), E.2 (`perf_counter`) |
| **10 / 11** | F: topics, eventos discretos, esperar enlace, −30° |
