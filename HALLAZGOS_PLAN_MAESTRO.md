# Hallazgos para actualizar `TELMEOW_PLAN_MAESTRO.md`

Discrepancias entre el plan de implementación y el estado real del código,
encontradas durante los PROMPT 1 y 2. Cada entrada indica **qué dice el plan**,
**qué se encontró** (con archivo y línea) y **qué cambio proponer**.

Todo se verificó leyendo el código directamente, no la documentación.

Última actualización: 2026-08-29 · tras el PROMPT 2.

---

## H1 — El visor sí consume `gesture` y `page` en el payload de pose

**Dice el plan** (§0.3): el contrato incluye `gesture`, pero la skill
`telmeow-viewer` — la fuente que el plan cita como definitiva — lista solo
`{pan, tilt, armL, armLtw, armR, armRtw, emo}`.

**Se encontró** (`telmeow_viewer.html:1005-1009`):

```js
if(o.gesture==='page_next' || o.gesture==='page_prev'){
  changePage(o.gesture);
  playPageGesture(o.gesture);
}
if(typeof o.page==='number'){ pageNum = o.page; drawPage(); }
```

Además, `emotion` funciona como alias de `emo` (línea 1004).

**Cambio propuesto:** añadir `gesture`, `page` y el alias `emotion` a la sección
«MQTT — contrato único» de la skill del visor. El plan maestro §0.3 ya está
bien; la que está desactualizada es la skill.

---

## H2 — Todos los campos del payload son opcionales

**Dice el plan** (§0.3): muestra un JSON completo, lo que se lee como si los
seis ángulos fueran obligatorios.

**Se encontró** (`telmeow_viewer.html:993-1009`): el visor comprueba cada campo
por separado con guardas `typeof o.X === 'number'` y conserva el último valor de
los que falten.

**Por qué importa:** es exactamente lo que permite cumplir el requisito 2 del
PROMPT 7 («si un estimador no produce resultado, reutiliza el último valor
válido en vez de publicar ceros»). Se puede lograr **omitiendo el campo**, sin
necesidad de que `main.py` mantenga estado.

**Cambio propuesto:** declararlo explícitamente en §0.3.

---

## H3 — El indicador `t-lat` del visor NO mide latencia

**Dice el plan** (§0.3): «Latencia objetivo declarada: **< 150 ms**». La Parte 5
anticipa preguntas del jurado sobre validación.

**Se encontró** (`telmeow_viewer.html:1017-1021`):

```js
const now=performance.now();
if(lastMsg){ document.getElementById('t-lat').textContent=(now-lastMsg).toFixed(0)+' ms';
```

`now - lastMsg` es el **intervalo entre mensajes consecutivos**, no el retardo
extremo a extremo. A 20 Hz mostrará ~50 ms de forma constante, aunque la
latencia real fuera de 800 ms. El indicador no puede subir por encima del
periodo de publicación, así que **nunca podría delatar un problema de latencia**.

**Riesgo de defensa:** es una cifra declarada en el documento que el indicador
visible no sustenta. Un jurado que pregunte «¿cómo midió esos 150 ms?» encuentra
que se midió otra cosa.

**Cambio propuesto:** una de dos.

1. Medirla de verdad: el publisher incluye un campo `t` (Unix ms) y el visor
   calcula `Date.now() - o.t`. Ya está implementado del lado de Python
   (`PosePublisher(include_timestamp=True)`), **desactivado por defecto** para no
   alterar el contrato sin tu decisión. Falta el cambio en el visor. Salvedad
   metodológica: publisher y navegador comparten reloj solo si corren en la
   misma máquina; entre máquinas distintas habría que declarar el desfase.
2. O bien reetiquetar `t-lat` como «periodo entre comandos» y declarar en el
   capítulo de limitaciones que la latencia extremo a extremo no se midió.

---

## H4 — Discrepancia en el tilt del gesto de página

**Dice el plan** (§3.3.6): «El centro de la pantalla externa requeriría ~46.5°
de inclinación; el límite mecánico es −39°. **El gesto usa −35°** y alcanza la
franja superior del panel.»

**Se encontró** (`telmeow_viewer.html:849`):

```js
const PAGE_GESTURE = { panSweep:35, tiltDown:-30, downMs:700, upMs:700 };
```

El valor implementado es **−30°**, no −35°. El margen respecto al límite
mecánico es de **9°**, no de 4°. Probablemente el «−35» del plan se confundió
con `panSweep: 35`, que sí vale 35.

**Cambio propuesto:** corregir §3.3.6 a −30° y rehacer la aritmética del margen.
Es un número que va en el documento de tesis, conviene que sea el real.

---

## H5 — La decisión abierta §3.2.2 ya está resuelta

**Dice el plan** (§3.2, punto 2): «Actualmente el viewer solo clampa lo que se
mueve con los sliders; las poses que llegan por MQTT se aplican sin verificar.
Falta decidir si el viewer debe clampar también lo recibido.»

**Se encontró** (`telmeow_viewer.html:1010-1015`):

```js
// Segunda capa de filtro: el publisher Python ya recortó por tabla,
// pero aquí se verifica la pose COMPLETA contra la geometría real.
const clipped = clampPoseExternal();
```

Ya lo hace, y con un algoritmo distinto al de los sliders: bisección sobre el
**segmento completo** entre la última pose válida y la pedida, porque un mensaje
MQTT cambia los seis grados de libertad a la vez y la colisión puede nacer de la
combinación. El indicador `t-col` avisa cuando la segunda capa actúa — es decir,
cuando el `limits.py` de Python dejó pasar una pose inválida.

**Cambio propuesto:** mover §3.2.2 de «decisiones abiertas» a arquitectura, y
documentar `t-col` como el instrumento que valida la primera capa.

---

## H6 — La decisión abierta §3.2.1 ya está resuelta

**Dice el plan** (§3.2, punto 1): «Falta definir si se quita solo la UI de los
barridos o también el motor de colisiones BVH.»

**Se encontró:** los botones «Barrido límites» y «Barrido 2D brazos», el
checkbox del plano posterior y las funciones `sweepLimits()` / `sweep2D()` fueron
eliminados en ago-2026. El motor BVH y el plano posterior siguen activos, porque
no son instrumentación: operan en vivo como filtro (ver H5).

**Cambio propuesto:** cerrar §3.2.1.

---

## H7 — Mosquitto no está instalado

No hay `mosquitto.exe` ni `mosquitto_sub` en el PATH, ni carpeta en
`C:\Program Files\mosquitto`, ni servicio de Windows registrado.

**Impacto:** el punto de verificación obligatorio posterior al PROMPT 2 («abrir
el viewer, conectar a `ws://localhost:9001`, confirmar que el brazo se mueve»)
no se puede ejecutar hasta instalarlo. Se descarga de
[mosquitto.org/download](https://mosquitto.org/download/), instalador x64.

**Cambio propuesto:** añadirlo como prerrequisito explícito en la Parte 4, antes
del paso 1. El repositorio ya incluye [`mosquitto.conf`](mosquitto.conf) con los
dos listeners.

---

## H8 — El intérprete objetivo es Python 3.12, no 3.9/3.10

**Dice el plan:** no fija versión. `legacy/Face Tracking.py:7` implica 3.9 (por
la rueda de dlib) y la configuración de PyCharm decía 3.10.

**Se encontró**, consultando PyPI:

| Paquete | Ruedas Windows | ¿3.14? |
|---|---|---|
| tensorflow 2.20/2.21 | cp39–cp313 `win_amd64` | No |
| mediapipe 1.0.1 | cp39–cp312 `win_amd64` | No |
| mediapipe 0.10.35 | `py3-none-win_amd64` | Instalaría, sin garantía |

La intersección que soporta TensorFlow **y** MediaPipe en Windows es 3.10, 3.11
o 3.12. **3.12 es la más nueva de las tres.** Ya está instalada
(`C:\Users\hlrm\AppData\Local\Programs\Python\Python312`).

**Cambio propuesto:** fijar «CPython 3.12 x64 en Windows» en el plan, y advertir
que 3.13 y 3.14 rompen la instalación. Al eliminar dlib (PROMPT 4) desaparece la
atadura a 3.9.

---

## H9 — Precisión sobre la advertencia de cdnjs

**Dice la skill del visor:** «cdnjs FALLA».

**Se encontró** (`telmeow_viewer.html:1033`): el visor carga `mqtt.min.js` desde
cdnjs y funciona correctamente. La advertencia aplica solo a los **addons
ES-module de three.js** (STLLoader, OrbitControls), que cdnjs sirve con MIME
incorrecto. `mqtt.min.js` es un script clásico, no un módulo.

**Cambio propuesto:** precisar el alcance de esa nota, para no inducir un cambio
innecesario en una línea que funciona.

---

## H10 — Los límites 1D de balanceo contradicen las tablas 2D

**Dice el plan** (§0.4): balanceo izq −55…+32, der −55…+33, y a continuación las
tablas 2D, que llegan a −75 y +75.

**Problema:** si `clamp_arm` aplicara ambos, todo el rango colapsaría a
(−55, +30) y el brazo perdería el recorrido extra que gana al girar y despejarse
del tronco — que es justamente lo que el barrido 2D midió.

**Resuelto** (decisión tomada durante el PROMPT 1): manda la tabla 2D. Los 1D
son la rebanada de la tabla en giro = 0° medida con refinado a 1°, y se
conservan como metadato documentado en `limits.py`. `clamp_arm` recorta primero
el giro a ±90° y luego interpola la tabla en ese giro.

**Cambio propuesto:** anotarlo en §0.4 para que no vuelva a leerse como
contradicción. La prueba que distingue la implementación correcta de la errónea
es `clamp_arm(+70, giro=+90, 'izq') == +70` (y no +32).

---

## H11 — `time.monotonic()` no sirve para medir periodos de 50 ms en Windows

Hallazgo del propio PROMPT 2. Es el más transferible de esta lista: afecta a
cualquier medida de tiempo del proyecto, no solo al publisher.

**Síntoma:** el limitador de tasa descartaba mensajes de un emisor que publicaba
a la tasa nominal. **Medido: 9 de 60 mensajes descartados publicando a 20 Hz**,
con el bucle emisor funcionando perfectamente. La tasa efectiva caía a 17 Hz.

**Causa:** el publisher usaba `time.monotonic()` como reloj. En Windows ese
reloj tiene una resolución de **~15.625 ms**, comparable al periodo de 50 ms que
hay que medir. Los intervalos salen cuantizados a múltiplos de tick (46.875 ms,
62.5 ms) y de vez en cuando se leen como 31.25 ms, por debajo de cualquier
umbral razonable.

Medido en esta máquina, con el mismo bucle emisor:

| Reloj | Intervalo mín. | Mediana | Descartes | Tasa |
|---|---|---|---|---|
| `time.monotonic()` | 46.0 ms | 47.0 ms | 9/60 | 17.0 Hz |
| `time.perf_counter()` | 50.1 ms | 50.4 ms | 0/60 | 20.0 Hz |

Conviene dejar escrito lo que **no** era la causa, porque son las dos hipótesis
plausibles que hubo que descartar midiendo: no era la precisión de
`time.sleep()` (en Python 3.12 usa temporizadores de alta resolución, y en esta
máquina acierta con ±0.5 ms), ni era el cliente MQTT (un cliente falso sin red
dio exactamente el mismo número de descartes).

**Corregido:** el reloj por defecto es ahora `time.perf_counter`, igualmente
monótono pero con resolución de sub-microsegundo. Cubierto por una prueba de
regresión. Se conserva una holgura del 10 % del periodo en el limitador, pero
como margen para hipos del sistema, no como parche del reloj.

**Relevante para los PROMPT 7 y 9:**

- El HUD del PROMPT 7 muestra «Hz de publicación» y «fps de captura». Medidos
  con `monotonic` habrían mostrado ~17 Hz con la configuración puesta en 20, y
  habría parecido un problema de rendimiento inexistente.
- Cualquier tasa o latencia que se reporte en la tesis debe medirse con
  `perf_counter`. Con `monotonic`, el error sistemático en magnitudes de esta
  escala es del orden del 15 %.

**Cambio propuesto:** anotarlo como nota metodológica de medición en el plan,
junto a H3 — es el mismo tema visto desde el lado del visor.

---

## Resumen de cambios propuestos al plan maestro

| # | Sección | Acción |
|---|---|---|
| H1 | §0.3 / skill del visor | Añadir `gesture`, `page`, alias `emotion` |
| H2 | §0.3 | Declarar que todos los campos son opcionales |
| H3 | §0.3, Parte 5 | Medir la latencia de verdad o reetiquetar el indicador |
| H4 | §3.3.6 | Corregir −35° → **−30°**, margen 9° |
| H5 | §3.2.2 | Cerrar: el visor ya clampa lo recibido |
| H6 | §3.2.1 | Cerrar: barridos retirados, BVH activo |
| H7 | Parte 4 | Añadir Mosquitto como prerrequisito |
| H8 | Parte 0 | Fijar CPython 3.12 x64 |
| H9 | skill del visor | Precisar el alcance de la nota sobre cdnjs |
| H10 | §0.4 | Anotar que manda la tabla 2D |
| H11 | Parte 0 / §0.3 | Nota metodológica: medir con `perf_counter`, no `monotonic` |

**Sigue abierta** la decisión §3.2.3: ganancia de mapeo operador → robot (¿1:1 o
amplificado?). Se necesita antes del PROMPT 4.
