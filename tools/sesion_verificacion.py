"""La sesión de verificación tras el P6.5, de principio a fin.

Comprueba lo que se cambió después de las sesiones del 2026-11-03 —los pesos
reentrenados, la regla de las cejas y la cabeza con la cara tapada (H58)— y
mide la latencia de la cámara con la luz de trabajo, que faltaba. Hace, en
orden:

1. **Comprueba el broker**: los dos puertos (1883 para Python, 9001 para el
   visor). Si solo está el 1883, es el servicio de Mosquitto sin WebSocket —la
   trampa del doble broker (§4.1)— y se para con las instrucciones. Si no hay
   ninguno, levanta el del repositorio (``mosquitto.conf``).
2. **Sirve el visor** por HTTP (nunca ``file://``) si no lo está, y lo abre.
3. **Graba la sesión** con el pipeline y, a la vez, el monitor de latencia.
   Antes enseña en la consola la tabla del protocolo: qué hacer en cada
   etiqueta, cuánto, y qué debe hacer el robot.
4. **Mide la latencia de la cámara** con la luz de trabajo y, si no hay
   contraste, ofrece repetir con menos luz.
5. **Analiza todo** y deja el informe en ``<base>_informe.txt``.

Al terminar para lo que arrancó él (broker y servidor del visor), no lo que
ya estaba en marcha.

Con ``--gestos`` hace la sesión corta del P7.4 y el P7.5, sin la cámara de
latencia ni la tabla de emociones: la cabeza que ya no debe temblar y la mano a
la cara, con el protocolo ``PROTOCOLO_GESTOS`` y los criterios de abajo, fijados
antes de grabar. Acaba con 20 s del demo de la cabeza, solo, que es lo que separa
las dos causas posibles del H60.

Con ``--ajustes`` hace la del P7.6 (``PROTOCOLO_AJUSTES``): el neutral con la
cabeza baja, la cabeza que sigue con las manos en las mejillas, el «yo no fui»
con los codos dentro del cuadro (la prueba que decide el H65) y la luz apagada.

Con ``--entrenar`` graba la del P7.7 (``PROTOCOLO_ENTRENAR``): las caras del
operador, neutral, enojada y triste, mirando al frente, abajo y arriba, para
reentrenar la emoción con ``tools/reentrenar_p77.py``; y «yo no fui» y estirar
los brazos, para ver si el tamaño de la mano los separa (H65).

Con ``--confirmar`` graba la sesión corta que confirma, en caras que nadie ha
mirado, los pesos reentrenados y la regla suave del enojo
(``PROTOCOLO_CONFIRMAR``; la evaluación, ``tools/confirmar_p78.py``).

Con ``--narracion`` hace la sesión del P10, en dos grabaciones, y antes de cada
una enseña su tabla: ``p10_sin_interfaz`` (solo ``main.py``, la referencia de
los fps) y ``p10_narracion`` (la interfaz en el navegador, con todas las teclas
ahí). En la segunda, la página enseña arriba la instrucción de cada etiqueta
(``--guia-sesion``), para no tener que volver a la terminal. Al final pregunta
qué teclas funcionaron y qué vista prefiere el operador, y analiza con los
criterios del plan. Con ``--guia`` solo enseña las tablas, para leerlas con
calma antes; con ``--ensayo``, la segunda grabación sin cámara ni grabar nada.

Uso, desde la raíz del repositorio::

    .venv\\Scripts\\python.exe tools\\sesion_verificacion.py
    .venv\\Scripts\\python.exe tools\\sesion_verificacion.py --gestos
    .venv\\Scripts\\python.exe tools\\sesion_verificacion.py --ajustes
    .venv\\Scripts\\python.exe tools\\sesion_verificacion.py --entrenar
    .venv\\Scripts\\python.exe tools\\sesion_verificacion.py --confirmar
    .venv\\Scripts\\python.exe tools\\sesion_verificacion.py --narracion
    .venv\\Scripts\\python.exe tools\\sesion_verificacion.py --narracion --guia   # solo las tablas
    .venv\\Scripts\\python.exe tools\\sesion_verificacion.py --narracion --ensayo # sin camara
    .venv\\Scripts\\python.exe tools\\sesion_verificacion.py --analizar      # rehace el informe
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Desde el P10 viven en el paquete (telmeow_teleop/broker.py).
from telmeow_teleop.broker import asegurar_broker, puerto_abierto  # noqa: E402
from telmeow_teleop.config import PROJECT_ROOT  # noqa: E402

URL_VISOR = "http://localhost:8000/telmeow_viewer.html"

#: El protocolo: (etiqueta, qué hacer, segundos, qué debe hacer el robot). Las
#: etiquetas 0-10 son las del P6.5, para comparar con aquellas sesiones; la 11
#: y la 12 prueban la cabeza con la cara tapada.
PROTOCOLO: tuple[tuple[int, str, int, str], ...] = (
    (0, "Cara relajada mirando la pantalla, quieto. NO pulses c", 10,
     "calibra 3 s (el panel dice CALIBRANDO); neutral"),
    (1, "Triste con puchero, SIN fruncir el ceño", 10, "TRISTE"),
    (2, "Triste con puchero Y el ceño fruncido", 10, "TRISTE (antes salía enojado)"),
    (3, "Enojado: ceño fruncido, como al regañar", 10, "ENOJADO"),
    (4, "Cara relajada", 10, "NEUTRAL"),
    (5, "Otra vez la 1: triste sin fruncir", 10, "TRISTE"),
    (6, "Otra vez la 2: triste con el ceño fruncido", 10, "TRISTE"),
    (7, "Otra vez la 3: enojado", 10, "ENOJADO"),
    (8, "Sonrisa amplia", 10, "FELIZ"),
    (9, "Sorpresa: cejas arriba y boca abierta", 10, "SORPRENDIDO"),
    (10, "Leer en voz alta sin actuar (un texto sin emoción)", 30,
     "NEUTRAL: la boca abierta ya no cuenta como sorpresa"),
    (11, "Sonreír 5 s y, sin dejar de sonreír, taparse la cara con las dos manos, quieto", 15,
     "feliz y neutral ~2 s después de taparte; la cabeza NO tiembla y a los 2 s va al frente"),
    (12, "Destaparse con la cabeza girada a un lado, y quedarse así", 10,
     "la cabeza va a tu pose en ~1 s, sin salto"),
)

#: La sesión de los gestos (P7.4 y P7.5): (etiqueta, qué hacer, segundos, qué debe
#: hacer el robot). Una postura por etiqueta, y bajar las manos es una etiqueta
#: aparte para que las cifras del gesto no se mezclen con la salida.
PROTOCOLO_GESTOS: tuple[tuple[int, str, int, str], ...] = (
    (0, "Cara relajada mirando la pantalla, quieto, manos abajo. NO pulses c", 15,
     "calibra 3 s; la cabeza NO vibra, quieta del todo"),
    (1, "Sin girar la cabeza: habla en voz alta y sonríe a ratos, manos abajo", 15,
     "la cabeza casi no se mueve aunque cambies la expresión"),
    (2, "Gira despacio: a tu izquierda, centro, a tu derecha, centro, arriba, abajo, centro", 20,
     "la cabeza te sigue sin retraso que se note; al pararte puede quedar 1° corta"),
    (3, "Mano DERECHA en tu mejilla derecha, codo abajo; la izquierda abajo", 10,
     "SU brazo derecho a la cara en ~1 s y quieto, sin saltos; el izquierdo en reposo"),
    (4, "Baja la mano", 5, "el brazo vuelve a reposo en ~1 s, sin salto"),
    (5, "Mano IZQUIERDA en tu mejilla izquierda, codo abajo; la derecha abajo", 10,
     "SU brazo izquierdo a la cara y quieto; el derecho en reposo"),
    (6, "Baja la mano", 5, "el brazo vuelve a reposo en ~1 s, sin salto"),
    (7, "«El grito»: las dos manos en las mejillas, codos abajo", 10,
     "los dos brazos a la cara y quietos"),
    (8, "Baja las manos", 5, "los dos vuelven a reposo"),
    (9, "Tápate la cara con las dos manos, quieto", 10,
     "los dos brazos a la cara; la cabeza quieta y a los 2 s al frente"),
    (10, "Baja las manos", 5, "brazos a reposo y la cabeza vuelve a tu pose en ~1 s"),
    (11, "«Yo no fui»: manos abiertas a los lados de la cabeza, POR FUERA de los hombros, "
         "palmas a la cámara", 10,
     "NO es la mano a la cara: los brazos hacia la cruz"),
    (12, "Saluda con la derecha, la izquierda abajo", 10,
     "el saludo del robot con el derecho; NO la mano a la cara"),
    (13, "Narra como contando un cuento, con las manos", 40,
     "sigue tus gestos; A LA CARA solo si de verdad llevas la mano a la barbilla o la cara"),
)

#: Salto mediano de la cabeza con la persona quieta, en grados por frame: el de
#: p7_final fue 1.02, y la reproducción offline con el filtro nuevo da 0.07.
TEMBLOR_QUIETO_MAX = 0.2

#: Lo mismo hablando y sonriendo: 1.56 sonriendo en p7_final, 0.64 offline.
TEMBLOR_HABLANDO_MAX = 0.8

#: % mínimo de la etiqueta con el brazo en la pose. No es 100: la etiqueta incluye
#: subir la mano, reconocerla y la rampa, unos 2 de sus 10 s, más lo que tarde el
#: operador tras pulsar ESPACIO.
GESTO_MIN = 60.0

#: Temblor crudo de la Face Mesh con la persona quieta en la sesión del P4
#: (2026-09-06), con la misma regla que ``temblor_crudo``. En las nueve sesiones
#: del pipeline, 0.9-1.4 (H60).
TEMBLOR_CRUDO_P4 = 0.36

#: Qué brazos del robot van a la cara en cada etiqueta de ``PROTOCOLO_GESTOS``.
GESTO_ESPERADO: dict[int, str] = {3: "R", 5: "L", 7: "LR", 9: "LR"}

#: Etiquetas en las que ningún brazo puede ir a la cara.
SIN_GESTO: tuple[int, ...] = (0, 1, 2, 11, 12)

#: La sesión del P7.6 (2026-11-07): (etiqueta, qué hacer, segundos, qué debe
#: hacer el robot). Una postura por etiqueta.
PROTOCOLO_AJUSTES: tuple[tuple[int, str, int, str], ...] = (
    (0, "Cara relajada mirando la pantalla, quieto, manos abajo. NO pulses c", 15,
     "calibra 3 s; neutral; la cabeza quieta"),
    (1, "Cara relajada: baja la cabeza a mirar el teclado 5 s, vuelve, súbela a mirar "
        "el techo 5 s, vuelve. Sin gesticular", 20,
     "NEUTRAL todo el tiempo (antes, enojado al bajar la cabeza)"),
    (2, "Enojado de verdad: ceño fruncido, como al regañar, mirando la pantalla", 10, "ENOJADO"),
    (3, "Cara relajada", 5, "NEUTRAL"),
    (4, "«El grito»: las dos manos en las mejillas, y gira la cabeza despacio a un "
        "lado y al otro", 15,
     "los brazos a la cara y la cabeza TE SIGUE (antes se quedaba fija)"),
    (5, "Tápate la cara con las dos manos, quieto", 10,
     "la cabeza se queda quieta y a los 2 s va al frente"),
    (6, "Baja las manos", 5, "la cabeza vuelve a tu pose en ~1 s"),
    (7, "«Yo no fui» desde tu sitio de siempre: manos abiertas a los lados de la cabeza, "
        "por fuera de los hombros, palmas a la cámara", 15,
     "brazos hacia la cruz (aquí oscilaba el izquierdo)"),
    (8, "Baja las manos y siéntate unos 20 cm más atrás, hasta ver tus codos en la ventana", 10,
     "nada"),
    (9, "«Yo no fui» desde ahí, con los codos a la vista", 15,
     "brazos hacia la cruz, SIN oscilar"),
    (10, "Desde ahí: estira los brazos hacia la cámara", 10, "brazos al frente"),
    (11, "Vuelve a tu sitio, manos abajo, quieto. APAGA LA LUZ", 20,
     "la cabeza no vibra (la zona sube); si no basta, el panel dice POCA LUZ y el robot se para"),
    (12, "Prende la luz, quieto", 15, "si se paró, sigue solo a los ~3 s"),
)

#: La sesión del P7.7 (2026-11-07), para reentrenar la emoción con la cara del
#: operador a varias alturas de cabeza. Las etiquetas 0-11 son las de
#: ``reentrenar_p77.ESPERADAS_P77``; 12-15, los brazos.
PROTOCOLO_ENTRENAR: tuple[tuple[int, str, int, str], ...] = (
    (0, "Cara relajada mirando la pantalla, quieto, manos abajo. NO pulses c", 15,
     "calibra 3 s, cabeza y cara"),
    (1, "Cara neutral mirando la pantalla", 15, "NEUTRAL"),
    (2, "Cara neutral mirando el teclado (la cabeza baja)", 15, "NEUTRAL"),
    (3, "Cara neutral mirando arriba (encima de la pantalla)", 10, "NEUTRAL"),
    (4, "Cara neutral leyendo en voz alta un texto, mirando la pantalla", 20, "NEUTRAL"),
    (5, "Enojado: ceño fruncido, como al regañar, mirando la pantalla", 15, "ENOJADO"),
    (6, "Enojado mirando el teclado (la cabeza baja)", 15, "ENOJADO"),
    (7, "Cara neutral", 5, "NEUTRAL"),
    (8, "Triste con puchero, SIN fruncir el ceño, mirando la pantalla", 15, "TRISTE"),
    (9, "Triste con puchero Y el ceño fruncido, mirando la pantalla", 15, "TRISTE"),
    (10, "Triste mirando el teclado (la cabeza baja)", 15, "TRISTE"),
    (11, "Cara neutral mirando la pantalla", 10, "NEUTRAL"),
    (12, "«Yo no fui» desde tu sitio: manos abiertas a los lados de la cabeza, por fuera "
         "de los hombros, palmas a la cámara", 15, "brazos hacia la cruz"),
    (13, "Estira los brazos hacia la cámara", 15, "brazos al frente"),
    (14, "«Yo no fui» otra vez", 10, "brazos hacia la cruz"),
    (15, "Estira los brazos otra vez", 10, "brazos al frente"),
)

#: La sesión de confirmación del P7.7 (2026-11-08): caras que nadie ha mirado
#: para confirmar el reentrenado y la regla suave del enojo. Las etiquetas son
#: las de ``confirmar_p78.ESPERADAS_P78``.
PROTOCOLO_CONFIRMAR: tuple[tuple[int, str, int, str], ...] = (
    (0, "Cara relajada mirando la pantalla, quieto, manos abajo. NO pulses c", 15,
     "calibra 3 s, cabeza y cara"),
    (1, "Cara neutral mirando la pantalla", 15, "NEUTRAL"),
    (2, "Cara neutral mirando el teclado (la cabeza baja)", 15, "NEUTRAL"),
    (3, "Cara neutral mirando arriba (encima de la pantalla)", 10, "NEUTRAL"),
    (4, "Cara neutral leyendo en voz alta, mirando la pantalla", 20, "NEUTRAL"),
    (5, "Enojado mirando la pantalla", 10, "ENOJADO"),
    (6, "Enojado mirando el teclado (la cabeza baja)", 15, "ENOJADO"),
    (7, "Cara neutral", 5, "NEUTRAL"),
    (8, "Triste con puchero, mirando la pantalla", 10, "TRISTE"),
    (9, "Triste mirando el teclado (la cabeza baja)", 10, "TRISTE"),
    (10, "Cara neutral mirando la pantalla", 10, "NEUTRAL"),
)

#: La sesión del P10 (2026-11-08), en dos grabaciones. La etiqueta 1 es la misma
#: en las dos, palabra por palabra: sus fps son el primer criterio.
_HABLAR_P10 = ("Habla en voz alta mirando la pantalla: cuenta qué hiciste hoy. Antebrazos "
               "apoyados en la mesa, sin gestos")

#: La primera, sin la interfaz: solo ``main.py``, como en el P7. Las teclas van a
#: la ventana de Python.
PROTOCOLO_P10_SIN_INTERFAZ: tuple[tuple[int, str, int, str], ...] = (
    (0, "Cara relajada mirando la pantalla, quieto, antebrazos apoyados en la mesa. "
        "NO pulses nada", 10,
     "la ventana de Python dice CALIBRANDO unos 3 s"),
    (1, _HABLAR_P10, 60, "la ventana de Python sigue tu cabeza (aquí no hay robot a la vista). "
                         "Al acabar, q en esa ventana: esta grabación termina aquí"),
)

#: La segunda, con la interfaz de narración: TODAS las teclas en el navegador.
#: Una postura por etiqueta; las 3, 4, 9 y 10 comprueban en vivo el P7.8. La 3
#: era «antebrazos en la mesa» hasta el 2026-11-13: ahí los codos y las manos no
#: entran en cuadro, los brazos no se siguen y la zona muerta no actúa (lo notó
#: el operador). Ahora es el brazo izquierdo a la vista, como el derecho en la 4.
PROTOCOLO_P10_NARRACION: tuple[tuple[int, str, int, str], ...] = (
    (0, "Cara relajada mirando la pantalla, quieto, antebrazos en la mesa. NO pulses nada: "
        "espera a que se abra el navegador con el robot, tu cámara y el libro", 15,
     "abajo dice «Calibrando: cara relajada, mirando la pantalla» unos 3 s; luego, neutral"),
    (1, _HABLAR_P10, 60, "sigue tu cabeza; los brazos quietos"),
    (2, "Cara relajada mirando la pantalla: pulsa c y quédate quieto", 5,
     "«Calibrando: cara relajada, mirando la pantalla» 3 s; luego, cabeza al frente y neutral"),
    (3, "Brazo IZQUIERDO estirado hacia tu izquierda, a la altura del hombro, con la mano "
        "dentro de tu cámara; el derecho en la mesa. Súbelo, y quieto 10 s", 20,
     "su brazo izquierdo estirado y quieto, SIN temblor"),
    (4, "Brazo DERECHO estirado hacia tu derecha, a la altura del hombro, con la mano dentro "
        "de tu cámara; el izquierdo en la mesa. Súbelo, y quieto 10 s", 20,
     "su brazo derecho estirado y quieto, sin temblor"),
    (5, "El robot se ve de frente. Saluda 2 veces con la derecha. Pulsa con el ratón el botón "
        "«Vista» (el último de la fila de botones): ahora se ve desde detrás; saluda 2 veces "
        "más. Déjalo en la vista que prefieras: al final te lo pregunto", 20,
     "de frente saluda por el lado contrario de la pantalla que tú en tu cámara; desde "
     "detrás, por el mismo"),
    (6, "En la lista «Elige un cuento…» (debajo del libro) elige «El túnel». Léelo en voz "
        "alta y pasa 5 páginas, una cada ~6 s: las 3 primeras con l y las 2 últimas con la "
        "flecha →. Lee también las últimas líneas de cada página, bajando la cabeza como "
        "te salga natural", 40,
     "gesto de página en cada cambio; al bajar la cabeza al pie de la página el robot la "
     "sigue bajando, sin quedarse parado"),
    (7, "Vuelve atrás 2 páginas, una cada ~3 s: la primera con j y la segunda con la flecha ←", 10,
     "gesto hacia el otro lado y página anterior, las dos veces"),
    (8, "Pulsa k (pausa). Intenta pasar de página con l. Pulsa k otra vez (reanudar) y pasa "
        "con l", 15,
     "en pausa se queda quieto, abajo dice «En pausa (k)» y la página NO cambia; al reanudar, "
     "cambia con su gesto"),
    (9, "Sonrisa clara, leyendo. Tres veces: baja la cabeza 2 s para mirar la esquina de abajo "
        "a la derecha de la página («buscando la línea siguiente») y vuelve", 20,
     "FELIZ todo el rato, también mientras miras abajo"),
    (10, "Lo mismo con cara triste, con puchero, «cuando el personaje se pierde»: tres veces "
         "baja la cabeza 2 s a la esquina y vuelve", 20,
     "TRISTE todo el rato, también mientras miras abajo"),
    (11, "Pulsa 6 (concentrado) y quédate así 5 s; luego pulsa 0 (automático) con la cara "
         "relajada", 10,
     "ojos concentrados y abajo «Expresión fijada: concentrado»; con 0, vuelve a tu cara: neutral"),
    (12, "En la lista elige «Cambios» (sus páginas son verticales) y pasa 2 páginas con l", 15,
     "la página se ve entera y sin deformar"),
    (13, "Pulsa q dos veces seguidas (en menos de 3 s)", 5,
     "la primera q pide confirmación; con la segunda se cierra la ventana de Python y la página "
     "dice «La sesión terminó»"),
)

#: Lo que se le pregunta al operador al acabar: (clave, pregunta, respuesta que
#: cumple). Sin respuesta que cumpla, la pregunta informa y no decide: el criterio
#: es no tocar la ventana de Python, y la vista la elige él.
PREGUNTAS_P10: tuple[tuple[str, str, str], ...] = (
    ("clic_python", "¿Tuviste que hacer clic en la ventana de Python en algún momento?", "n"),
    ("clic_navegador", "¿Tuviste que hacer clic en el navegador para que las teclas respondieran?", ""),
    ("ESPACIO", "¿ESPACIO pasaba de etiqueta?", "s"),
    ("c", "¿c calibraba (etiqueta 2)?", "s"),
    ("l", "¿l pasaba a la página siguiente?", "s"),
    ("flecha_derecha", "¿La flecha → pasaba a la página siguiente?", "s"),
    ("j", "¿j volvía a la página anterior?", "s"),
    ("flecha_izquierda", "¿La flecha ← volvía a la página anterior?", "s"),
    ("k", "¿k pausaba y reanudaba?", "s"),
    ("6", "¿6 fijaba «concentrado»?", "s"),
    ("0", "¿0 volvía a la expresión automática?", "s"),
    ("q", "¿q dos veces cerraba la sesión?", "s"),
    ("temblor", "¿Viste temblar los brazos del robot en las etiquetas 3 o 4, con tu brazo ya quieto?", "n"),
    ("tope", "¿Viste al robot quedarse parado o dar tirones al mirar al pie de la página?", "n"),
    ("cabeceo", "¿El cabeceo del robot cerca del frente te pareció el de antes?", "s"),
    ("vistazos", "¿El robot perdió la expresión al mirar abajo en las etiquetas 9 o 10?", "n"),
    ("vertical", "¿«Cambios» se veía entera y sin deformar?", "s"),
    ("vista", "¿Qué vista del robot prefieres? (d = detrás, f = frente)", ""),
)

#: Criterios del P10, fijados antes de grabar (plan maestro, P10). Fps medios de
#: la etiqueta 1 con la interfaz frente a sin ella.
FPS_INTERFAZ_MIN = 0.90
#: Publicación con la interfaz (mediana de mqtt_hz fuera de pausa): en el P7,
#: 19.7-19.9 Hz.
HZ_INTERFAZ_MIN = 19.5
#: Páginas que se pasan en cada etiqueta (dentro del mismo cuento).
PAGINAS_P10: dict[int, int] = {6: 5, 7: 2, 8: 1, 12: 2}
#: Vistazos (P7.8): filas del vistazo, dentro de sus 3 s, en que el robot conserva
#: la expresión que tenía justo antes. La regla la conserva siempre; el margen es
#: para la primera fila, que puede llegar con el voto ya hecho.
VISTAZO_CONSERVA_MIN = 95.0
VISTAZO_MAX_S = 3.0
#: P10.2 (H72), fijado el 2026-11-13: segundos, en toda la narración, con el
#: cabeceo del robot en su tope de abajo. En la del 2026-11-12, sin la
#: compresión, unos 15. Solo vale si el operador bajó la cabeza de verdad.
TOPE_MAX_S = 1.0
TOPE_BAJADA_MIN = 25.0
#: Brazos a la vista y quietos (etiquetas 3 y 4, P7.8), fijado el 2026-11-13: en
#: los ``BRAZO_VENTANA_S`` segundos más quietos con el brazo levantado, la
#: desviación del comando no pasa de ``BRAZO_QUIETO_MAX`` grados (0.1-1.2 en las
#: sesiones del 2026-11-10 y 11-12). Levantado = más de ``BRAZO_LEVANTADO``
#: grados por encima del reposo.
BRAZO_QUIETO_MAX = 2.0
BRAZO_VENTANA_S = 5.0
BRAZO_LEVANTADO = 10.0
#: La compensación de contraluz (H71), fijado el 2026-11-13: el ruido de la
#: malla con ella apagada, como mucho este múltiplo del ruido con ella puesta,
#: medidos uno detrás de otro con la misma luz. Por encima, apagarla es lo que
#: mete el temblor leve de la cabeza.
RUIDO_CONTRALUZ_MAX = 1.25
#: Fotos con cara que necesita esa medicion para valer.
CARA_MIN_CONTRALUZ = 0.9

#: Criterios del P7.6, fijados antes de grabar. Enojo del robot con la cabeza
#: subiendo y bajando (etiqueta 1): en el neutral de p75 era el 23 %.
ENOJO_NEUTRAL_MAX = 10.0
#: Enojo de verdad (etiqueta 2): con la regla, 84 % reproducido en p7_final.
ENOJO_REAL_MIN = 60.0
#: Guarda con las manos en las mejillas (4) y tapada (5): en p75, 91 y 81 %;
#: reproducido con la regla nueva, 6 y 71 %.
GUARDA_MEJILLAS_MAX = 10.0
GUARDA_TAPADA_MIN = 60.0
#: Recorrido del pan publicado con las manos en las mejillas: que la cabeza siga.
PAN_MEJILLAS_MIN = 20.0
#: «Yo no fui» con los codos a la vista (9): saltos de más de 5° por minuto en
#: cada brazo (en p75, 216 el izquierdo y 93 el derecho), y codos dentro del
#: cuadro en al menos este % de los frames, para que la prueba valga.
SALTOS_YO_NO_FUI_MAX = 40.0
CODOS_DENTRO_MIN = 80.0


# --------------------------------------------------------------------------
# Antes de grabar
# --------------------------------------------------------------------------


# puerto_abierto, estado_broker y el arranque del broker: telmeow_teleop/broker.py.


def tabla_protocolo(protocolo=PROTOCOLO, *, cabecera: str | None = None, quien: str = "robot",
                    ancho: int | None = None) -> str:
    """La tabla que se lee antes de grabar: qué hacer en cada etiqueta, cuánto, y
    qué debe pasar (``quien``: el robot, o lo que se ve si no hay robot). Con
    ``ancho``, los textos largos se parten en líneas de ese ancho, sangradas."""
    import textwrap

    cabecera = cabecera or "PROTOCOLO (ESPACIO al empezar cada etiqueta; la 0 empieza sola; q al acabar)"
    def partir(primera: str, texto: str, sangria: str) -> list[str]:
        return textwrap.wrap(texto, width=ancho, initial_indent=primera, subsequent_indent=sangria,
                             break_on_hyphens=False) or [primera]

    lineas = ["", cabecera, ""]
    for etiqueta, que, segundos, robot in protocolo:
        if ancho is None:
            lineas += [f"  {etiqueta:>2}  {segundos:>3} s  {que}", f"              -> {quien}: {robot}"]
            continue
        lineas += partir(f"  {etiqueta:>2}  {segundos:>3} s  ", que, " " * 13)
        lineas += partir(f"{' ' * 13}-> {quien}: ", robot, " " * 16)
        lineas.append("")
    total = sum(s for _, _, s, _ in protocolo)
    lineas += ["", f"  Unos {total // 60} min {total % 60} s en total.", ""]
    return "\n".join(lineas)


def _esperar(mensaje: str) -> None:
    input(f"\n{mensaje}\n  [Enter para seguir] ")


# --------------------------------------------------------------------------
# Después: el informe
# --------------------------------------------------------------------------


def saltos(poses: list[tuple[float, float] | None]) -> list[float]:
    """El mayor cambio entre dos poses seguidas (pan o tilt), en grados."""
    validas = [p for p in poses if p is not None]
    return [max(abs(a[0] - b[0]), abs(a[1] - b[1])) for a, b in zip(validas, validas[1:])]


def _percentil(valores, q):
    v = sorted(valores)
    return v[min(len(v) - 1, int(q * (len(v) - 1)))] if v else None


def _num(valor):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _pose(fila: dict) -> tuple[float, float] | None:
    """La cabeza publicada en una fila de ``sesion.csv`` (vacía sin cara)."""
    pan, tilt = _num(fila.get("pan")), _num(fila.get("tilt"))
    return None if pan is None or tilt is None else (pan, tilt)


def retorno_tras_taparse(filas_emocion: list[dict], etiqueta: int = 11) -> float | None:
    """Segundos desde que la cara deja de servir (sin cara o con una mano
    encima) hasta que la expresión vuelve a neutral, en la etiqueta dada."""
    filas = [f for f in filas_emocion if f["etiqueta"] == etiqueta]
    inicio = next((f["t"] for f in filas if not f["rostro"] or f["manos"]), None)
    if inicio is None:
        return None
    fin = next((f["t"] for f in filas if f["t"] >= inicio and f["retorno"]), None)
    return None if fin is None else fin - inicio


def latencia_mqtt(ruta: Path, sin_arranque_s: float = 5.0) -> tuple[float, float, int] | None:
    """``(mediana, p95, n)`` de la latencia captura -> suscriptor, sin los
    primeros segundos (la carga de los modelos)."""
    if not ruta.is_file():
        return None
    with open(ruta, newline="", encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    if not filas:
        return None
    t0 = float(filas[0]["recibido_ms"])
    valores = [float(f["latencia_ms"]) for f in filas
               if float(f["recibido_ms"]) - t0 > 1000.0 * sin_arranque_s]
    if not valores:
        return None
    return statistics.median(valores), _percentil(valores, 0.95), len(valores)


def analizar(base: Path) -> str:
    """El informe de la sesión, como texto."""
    import analizar_p65 as p
    import analizar_registro_emocion as are
    import analizar_sesion as asn
    import medir_latencia_camara as mlc
    from telmeow_teleop.main import rutas_de_grabacion

    rutas = rutas_de_grabacion(base.with_suffix(".csv"))
    lineas = [f"INFORME DE {base.name}", "=" * 60]

    sesion = asn.cargar(rutas["sesion"])
    r = asn.resumir(sesion)

    def cifra(valor, formato=".0f"):
        return "--" if valor is None else format(valor, formato)

    lineas.append(f"\nTasas: camara {cifra(r.fps_medios, '.1f')} fps medios, frames perdidos "
                  f"{cifra(None if r.perdidos is None else 100 * r.perdidos, '.1f')} %, bucle "
                  f"{cifra(r.ms_bucle_sin[0])} ms (con inferencia {cifra(r.ms_bucle_con[0])} ms), "
                  f"MQTT {cifra(r.hz, '.1f')} Hz, perdidos {r.fallidos}")

    emocion = are.cargar(rutas["emocion"])
    modelo = emocion[0].get("modelo", "") if emocion else ""
    lineas.append(f"\nEmocion ({modelo}), robot y prediccion cruda por tramo:")
    columnas = {"esta sesion": p.resumir(emocion)}
    referencia = sorted(PROJECT_ROOT.glob("p65_[gs][12]_emocion.csv"))
    if referencia:
        columnas["P6.5 (antes)"] = p.juntar([p.resumir(are.cargar(c)) for c in referencia])
    import contextlib
    import io

    texto = io.StringIO()
    with contextlib.redirect_stdout(texto):
        p.imprimir_tabla(columnas)
    lineas.append(texto.getvalue().rstrip())

    retorno = retorno_tras_taparse(emocion)
    lineas.append("\nCara tapada (etiqueta 11):")
    lineas.append(f"  expresion a neutral {retorno:.1f} s despues de taparse" if retorno is not None
                  else "  no se vio volver a neutral (¿se tapo la cara en la 11?)")
    tramo = [f for f in sesion if f["etiqueta"] == 11]
    if tramo:
        tapadas = sum(int(f.get("cabeza_tapada") or 0) for f in tramo)
        publicadas = [_pose(f) for f in tramo]
        s = saltos(publicadas)
        lineas.append(f"  la guarda actuo en {100 * tapadas / len(tramo):.0f} % de los frames")
        if s:
            lineas.append(f"  cabeza publicada: salto medio {statistics.mean(s):.2f} grados/frame, "
                          f"p95 {_percentil(s, 0.95):.2f}")
        a, b = tramo[0]["t"], tramo[-1]["t"]
        with open(rutas["cabeza"], newline="", encoding="utf-8") as f:
            medidas = [(float(x["pan"]), float(x["tilt"])) for x in csv.DictReader(f)
                       if a <= float(x["t"]) <= b]
        sm = saltos(medidas)
        if sm:
            lineas.append(f"  cabeza medida (Face Mesh): salto medio {statistics.mean(sm):.2f} "
                          f"grados/frame, p95 {_percentil(sm, 0.95):.2f}")
    tramo = [f for f in sesion if f["etiqueta"] == 12]
    if tramo:
        s = saltos([_pose(f) for f in tramo])
        if s:
            lineas.append(f"\nAl destaparse (etiqueta 12): salto maximo de la cabeza publicada "
                          f"{max(s):.1f} grados/frame")

    lineas.append("\nLatencia:")
    mqtt = latencia_mqtt(base.with_name(base.name + "_latencia.csv"))
    if mqtt:
        lineas.append(f"  captura -> mensaje: mediana {mqtt[0]:.0f} ms, p95 {mqtt[1]:.0f} ({mqtt[2]} mensajes)")
    camara = None
    for nombre in ("_latencia_camara.csv", "_latencia_camara_penumbra.csv"):
        ruta = base.with_name(base.name + nombre)
        if ruta.is_file():
            medidas = mlc.latencias_de_destellos(*mlc.leer_csv(ruta))
            if medidas:
                camara = statistics.median(medidas)
                lineas.append(f"  pantalla + camara ({ruta.name}): mediana {camara:.0f} ms, "
                              f"{len(medidas)} destellos")
                break
            lineas.append(f"  {ruta.name}: sin destellos validos")
    if mqtt and camara is not None:
        lineas.append(f"  TOTAL: ~{mqtt[0] + camara:.0f} ms (sin el render del visor)")
    return "\n".join(lineas)


def temblor_crudo(filas_cabeza: list[dict], etiqueta: int = 0, sin_arranque_s: float = 3.0) -> float | None:
    """Salto mediano de lo que da la Face Mesh, antes de la ganancia y del filtro,
    sin los primeros segundos de la etiqueta. Con ella en la mano se comparan
    sesiones sin que el filtro de cada una se meta por medio."""
    filas = [f for f in filas_cabeza if int(f["etiqueta"]) == etiqueta]
    if not filas:
        return None
    t0 = float(filas[0]["t"])
    filas = [f for f in filas if float(f["t"]) - t0 > sin_arranque_s]
    s = saltos([(float(f["raw_pan"]), float(f["raw_tilt"])) for f in filas])
    return statistics.median(s) if s else None


def de_donde_sale_el_temblor(pipeline: float | None, demo: float | None) -> str:
    """Lo que dice comparar el demo con el pipeline en la misma sesión (H60). El
    2 es un corte de orden de magnitud: lo que hay que separar es 0.36 de 1.3."""
    if pipeline is None or demo is None:
        return "sin las dos medidas no se puede decir de donde sale"
    if pipeline >= 2.0 * demo:
        return "lo causa el PIPELINE: el demo solo tiembla mucho menos en las mismas condiciones"
    if demo >= 2.0 * TEMBLOR_CRUDO_P4:
        return "NO es el pipeline: el demo solo tiembla igual, asi que cambio algo desde septiembre"
    return "ni el demo ni el pipeline tiemblan como antes: mirar que cambio en esta sesion"


def en_la_pose_por_etiqueta(filas_brazos: list[dict]) -> dict[int, dict[str, float]]:
    """``{etiqueta: {lado: % de sus frames con el brazo del todo en la pose}}``."""
    cuenta: dict[int, dict[str, int]] = {}
    total: dict[int, int] = {}
    for f in filas_brazos:
        e = int(f["etiqueta"])
        total[e] = total.get(e, 0) + 1
        por_lado = cuenta.setdefault(e, {"L": 0, "R": 0})
        for lado in "LR":
            if (_num(f.get(f"mano_cara_{lado}")) or 0.0) >= 0.999:
                por_lado[lado] += 1
    return {e: {lado: 100.0 * n / total[e] for lado, n in v.items()} for e, v in cuenta.items()}


def angulo_a_la_cruz(abduccion: float, flexion: float) -> float:
    """Grados entre el brazo del robot y la cruz (su lateral)."""
    import math

    from telmeow_teleop.retarget import direccion

    return math.degrees(math.acos(max(-1.0, min(1.0, direccion(abduccion, flexion)[0]))))


def saltos_por_minuto(filas_brazos: list[dict], lado: str, umbral: float = 5.0) -> float | None:
    """Saltos del comando de un brazo mayores que ``umbral`` grados, por minuto."""
    abd, flex = ("armL", "armLtw") if lado == "L" else ("armR", "armRtw")
    comandos = [(_num(f.get(abd)), _num(f.get(flex))) for f in filas_brazos]
    comandos = [c for c in comandos if None not in c]
    if len(filas_brazos) < 2 or len(comandos) < 2:
        return None
    minutos = (float(filas_brazos[-1]["t"]) - float(filas_brazos[0]["t"])) / 60.0
    if minutos <= 0:
        return None
    return sum(1 for s in saltos(comandos) if s > umbral) / minutos


def analizar_ajustes(base: Path) -> str:
    """El informe de la sesión del P7.6, con los criterios fijados antes."""
    import filtros_cabeza as fc
    from telmeow_teleop.main import rutas_de_grabacion

    rutas = rutas_de_grabacion(base.with_suffix(".csv"))
    sesion = fc.leer(rutas["sesion"])
    brazos = fc.leer(rutas["brazos"])
    emocion = fc.leer(rutas["emocion"]) if rutas["emocion"].is_file() else []

    def de(filas, etiqueta):
        return [f for f in filas if int(f["etiqueta"]) == etiqueta]

    def pct(filas, condicion):
        return 100.0 * sum(1 for f in filas if condicion(f)) / len(filas) if filas else None

    def ok(cumple):
        return "OK" if cumple else "NO CUMPLE"

    lineas = [f"INFORME DEL P7.6 DE {base.name}", "=" * 60, ""]
    lineas.append("Emocion (robot, % de la etiqueta):")
    for etiqueta, emo, umbral, mayor in ((1, "enojado", ENOJO_NEUTRAL_MAX, False),
                                         (2, "enojado", ENOJO_REAL_MIN, True)):
        filas = [f for f in de(emocion, etiqueta) if f.get("rostro") == "1"]
        p = pct(filas, lambda f: f["emocion"] == emo)
        if p is None:
            lineas.append(f"  {etiqueta:>2}: sin datos")
            continue
        cumple = p >= umbral if mayor else p <= umbral
        lineas.append(f"  {etiqueta:>2} {emo}: {p:.0f} % (criterio {'>=' if mayor else '<='} {umbral:.0f} %) "
                      f"{ok(cumple)}")

    lineas += ["", "Cabeza con las manos en la cara:"]
    for etiqueta, umbral, mayor in ((4, GUARDA_MEJILLAS_MAX, False), (5, GUARDA_TAPADA_MIN, True)):
        filas = de(sesion, etiqueta)
        p = pct(filas, lambda f: f.get("cabeza_tapada") == "1")
        if p is None:
            continue
        cumple = p >= umbral if mayor else p <= umbral
        texto = "mejillas" if etiqueta == 4 else "tapada"
        linea = (f"  {etiqueta:>2} {texto}: guarda {p:.0f} % (criterio {'>=' if mayor else '<='} "
                 f"{umbral:.0f} %) {ok(cumple)}")
        if etiqueta == 4:
            pans = [_num(f.get("pan")) for f in filas if _num(f.get("pan")) is not None]
            recorrido = max(pans) - min(pans) if pans else 0.0
            linea += f"; el pan recorre {recorrido:.0f} grados (criterio >= {PAN_MEJILLAS_MIN:.0f}) " \
                     f"{ok(recorrido >= PAN_MEJILLAS_MIN)}"
        lineas.append(linea)

    lineas += ["", "«Yo no fui» (H65): saltos > 5 grados por minuto L / R, angulo mediano a la cruz L / R, "
                   "codos dentro del cuadro:"]
    for etiqueta, texto in ((7, "en tu sitio"), (9, "codos a la vista"), (10, "estirar")):
        filas = de(brazos, etiqueta)
        if len(filas) < 2:
            continue
        sl, sr = saltos_por_minuto(filas, "L"), saltos_por_minuto(filas, "R")
        cruz = {}
        for lado, abd, flex in (("L", "armL", "armLtw"), ("R", "armR", "armRtw")):
            v = sorted(angulo_a_la_cruz(_num(f[abd]), _num(f[flex])) for f in filas
                       if _num(f.get(abd)) is not None and _num(f.get(flex)) is not None)
            cruz[lado] = v[len(v) // 2] if v else None
        alto = _num(filas[0].get("alto")) or 480.0
        dentro = pct(filas, lambda f: all(
            _num(f.get(f"img_codo_y_{lado}")) is not None and _num(f.get(f"img_codo_y_{lado}")) <= alto
            for lado in "LR"))
        linea = (f"  {etiqueta:>2} {texto}: {sl or 0:.0f} / {sr or 0:.0f}, "
                 f"{cruz['L'] or 0:.0f} / {cruz['R'] or 0:.0f} grados, {dentro:.0f} %")
        if etiqueta == 9:
            vale = dentro >= CODOS_DENTRO_MIN
            cumple = vale and max(sl or 0, sr or 0) <= SALTOS_YO_NO_FUI_MAX
            linea += (f"  (criterio <= {SALTOS_YO_NO_FUI_MAX:.0f} con codos >= {CODOS_DENTRO_MIN:.0f} %) "
                      + (ok(cumple) if vale else "NO VALE: los codos no se vieron"))
        lineas.append(linea)

    lineas += ["", "Luz (indice de ruido de la malla, zona muerta, pausa):"]
    for etiqueta, texto in ((0, "con luz"), (11, "luz apagada"), (12, "luz prendida otra vez")):
        filas = de(sesion, etiqueta)
        if not filas:
            continue
        ruido = sorted(v for v in (_num(f.get("ruido_malla")) for f in filas) if v is not None)
        zona = sorted(v for v in (_num(f.get("zona_muerta")) for f in filas) if v is not None)
        pausa = pct(filas, lambda f: f.get("pausa_luz") == "1")
        poses = [_pose(f) for f in filas if f.get("pausa_luz") != "1"]
        s = saltos(poses)
        lineas.append(f"  {etiqueta:>2} {texto}: ruido {ruido[len(ruido) // 2] if ruido else float('nan'):.2f}, "
                      f"zona {zona[len(zona) // 2] if zona else float('nan'):.1f} (max {max(zona) if zona else 0:.1f}), "
                      f"pausa {pausa:.0f} %, cabeza publicada sin pausa: salto mediano "
                      f"{statistics.median(s) if s else float('nan'):.2f}")
    luz = de(sesion, 12)
    if luz:
        t0 = float(luz[0]["t"])
        tarde = [f for f in luz if float(f["t"]) - t0 > 6.0]
        if tarde:
            sigue = pct(tarde, lambda f: f.get("pausa_luz") == "1")
            lineas.append(f"  con la luz prendida, pasados 6 s: pausa {sigue:.0f} % (criterio 0 %) {ok(sigue == 0.0)}")
    return "\n".join(lineas)


def tamano_de_mano(filas_brazos: list[dict], etiquetas: set[int]) -> dict[str, float | None]:
    """Tamaño aparente mediano de cada mano, en píxeles, en esas etiquetas."""
    salida = {}
    for lado in "LR":
        v = sorted(x for x in (_num(f.get(f"tam_mano_{lado}")) for f in filas_brazos
                               if int(f["etiqueta"]) in etiquetas) if x is not None)
        salida[lado] = v[len(v) // 2] if v else None
    return salida


def analizar_entrenamiento(base: Path) -> str:
    """El informe de la sesión del P7.7: lo que hizo el robot con los pesos de
    ahora, tramo a tramo, y el tamaño de la mano en «yo no fui» y al estirar.
    El reentrenamiento va aparte (``tools/reentrenar_p77.py``), porque tarda."""
    import filtros_cabeza as fc
    import reentrenar_p77 as rp
    from telmeow_teleop.main import rutas_de_grabacion

    rutas = rutas_de_grabacion(base.with_suffix(".csv"))
    emocion = fc.leer(rutas["emocion"]) if rutas["emocion"].is_file() else []
    for f in emocion:
        f["etiqueta"] = int(f["etiqueta"])
        f["calibrando"] = f.get("calibrando") == "1"
    lineas = [f"INFORME DEL P7.7 DE {base.name}", "=" * 60, "",
              "Emocion con los pesos de ahora (robot, % del tramo):"]
    for nombre, valor in rp.resumir(emocion).items():
        lineas.append(f"  {nombre:20s} {'--' if valor is None else f'{valor:.0f} %'}")
    brazos = fc.leer(rutas["brazos"])
    yo, estirar = tamano_de_mano(brazos, {12, 14}), tamano_de_mano(brazos, {13, 15})
    lineas += ["", "Tamano aparente de la mano (px, mediana), para el H65:"]
    for lado in "LR":
        a, b = yo[lado], estirar[lado]
        razon = f", estirar / yo no fui = {b / a:.2f}" if a and b else ""
        lineas.append(f"  {lado}: yo no fui {a or 0:.0f}, estirar {b or 0:.0f}{razon}")
    lineas += ["", "Siguiente paso: python tools/reentrenar_p77.py (entrena con lo de antes y "
                   "evalua en esta sesion)"]
    return "\n".join(lineas)


def analizar_confirmacion(base: Path) -> str:
    """El informe de la sesión de confirmación: lo que hizo el robot en vivo,
    tramo a tramo. Las cuatro variantes y sus criterios, aparte
    (``tools/confirmar_p78.py``), porque tarda."""
    import confirmar_p78 as cp
    import filtros_cabeza as fc
    from telmeow_teleop.main import rutas_de_grabacion

    rutas = rutas_de_grabacion(base.with_suffix(".csv"))
    emocion = fc.leer(rutas["emocion"]) if rutas["emocion"].is_file() else []
    for f in emocion:
        f["etiqueta"] = int(f["etiqueta"])
        f["calibrando"] = f.get("calibrando") == "1"
    lineas = [f"INFORME DE CONFIRMACION DE {base.name}", "=" * 60, "",
              "Emocion en vivo (pesos y regla de operacion; robot, % del tramo):"]
    for nombre, valor in cp.resumir(emocion).items():
        lineas.append(f"  {nombre:20s} {'--' if valor is None else f'{valor:.0f} %'}")
    lineas += ["", "Siguiente paso: python tools/confirmar_p78.py (pesos reentrenados y regla suave)"]
    return "\n".join(lineas)


def analizar_gestos(base: Path) -> str:
    """El informe de la sesión de los gestos, con los criterios fijados antes."""
    import filtros_cabeza as fc
    from telmeow_teleop.main import rutas_de_grabacion

    rutas = rutas_de_grabacion(base.with_suffix(".csv"))
    cabeza = fc.leer(rutas["cabeza"])
    brazos = fc.leer(rutas["brazos"])
    lineas = [f"INFORME DE GESTOS DE {base.name}", "=" * 60, "", "Cabeza publicada, salto mediano entre frames:"]
    poses = [(float(f["pan"]), float(f["tilt"])) for f in cabeza]

    def veredicto(cumple: bool) -> str:
        return "OK" if cumple else "NO CUMPLE"

    for etiqueta, texto, maximo, antes in (
        (0, "quieto", TEMBLOR_QUIETO_MAX, 1.02),
        (1, "hablando y sonriendo", TEMBLOR_HABLANDO_MAX, 1.56),
    ):
        try:
            mediana, p95 = fc.temblor(cabeza, poses, {etiqueta})
        except statistics.StatisticsError:
            lineas.append(f"  {etiqueta:>2} {texto}: sin datos")
            continue
        lineas.append(f"  {etiqueta:>2} {texto}: {mediana:.3f} grados/frame, p95 {p95:.2f} "
                      f"(criterio <= {maximo}; en p7_final {antes}) {veredicto(mediana <= maximo)}")
    giros = [p for f, p in zip(cabeza, poses) if int(f["etiqueta"]) == 2]
    if giros:
        lineas.append(f"   2 girando: pan {min(p[0] for p in giros):+.0f}..{max(p[0] for p in giros):+.0f}, "
                      f"tilt {min(p[1] for p in giros):+.0f}..{max(p[1] for p in giros):+.0f} grados")

    crudo = temblor_crudo(cabeza)
    ruta_demo = base.with_name(base.name + "_demo_cabeza.csv")
    demo = temblor_crudo(fc.leer(ruta_demo)) if ruta_demo.is_file() else None
    formato = lambda v: "--" if v is None else f"{v:.2f}"  # noqa: E731
    lineas += ["", f"Temblor crudo de la Face Mesh, quieto (H60): pipeline {formato(crudo)}, "
                   f"demo solo {formato(demo)}, sesion del P4 {TEMBLOR_CRUDO_P4}",
               f"  {de_donde_sale_el_temblor(crudo, demo)}"]

    pose = en_la_pose_por_etiqueta(brazos)
    lineas += ["", "Mano a la cara, % de la etiqueta con el brazo en la pose (L / R):"]
    for etiqueta, que, _, _ in PROTOCOLO_GESTOS:
        if etiqueta not in pose:
            continue
        p = pose[etiqueta]
        nota = ""
        if etiqueta in GESTO_ESPERADO:
            esperado = GESTO_ESPERADO[etiqueta]
            bien = all(p[lado] >= GESTO_MIN for lado in esperado) and all(
                p[lado] == 0.0 for lado in "LR" if lado not in esperado)
            nota = f"  (esperado {esperado}, >= {GESTO_MIN:.0f} %) {veredicto(bien)}"
        elif etiqueta in SIN_GESTO:
            nota = f"  (esperado ninguno) {veredicto(p['L'] == 0.0 and p['R'] == 0.0)}"
        lineas.append(f"  {etiqueta:>2} {p['L']:5.1f} / {p['R']:5.1f}{nota}  {que[:44]}")

    comandos = {lado: [] for lado in "LR"}
    for f in brazos:
        for lado, abd, flex in (("L", "armL", "armLtw"), ("R", "armR", "armRtw")):
            if (_num(f.get(f"mano_cara_{lado}")) or 0.0) >= 0.999 and _num(f.get(abd)) is not None:
                comandos[lado].append((float(f[abd]), float(f[flex])))
    for lado, valores in comandos.items():
        s = saltos(valores)
        if s:
            lineas.append(f"  brazo {lado} en la pose: salto mediano {statistics.median(s):.2f} "
                          f"grados/frame, maximo {max(s):.1f} (antes 2.6-5.1)")
    saludos = [f for f in brazos if int(f["etiqueta"]) == 12]
    if saludos:
        reconocido = sum((_num(f.get("saludo_R")) or 0.0) > 0.0 for f in saludos)
        lineas += ["", f"Saludo (etiqueta 12): el robot saludo en {100 * reconocido / len(saludos):.0f} % "
                       "de la etiqueta"]
    return "\n".join(lineas)


# --------------------------------------------------------------------------
# El P10: la interfaz de narración
# --------------------------------------------------------------------------

INTRO_P10 = """
LA SESIÓN DEL P10: una medición de la cámara y dos grabaciones, unos 7 minutos
en total, con buena luz.

  0. La cámara: 1 minuto sentado y quieto, mirando la pantalla. No se abre
     ninguna ventana: la terminal va diciendo lo que mide.
  1. p10_sin_interfaz: solo la ventana de Python, como en el P7. Es la
     referencia de velocidad: 1 minuto hablando. Las teclas van a ESA ventana.
  2. p10_narracion: el navegador con el robot, tu cámara y el libro. TODAS las
     teclas van al navegador: no hagas clic en la ventana de Python.

  En las dos la etiqueta 0 empieza sola, y cada ESPACIO pasa a la siguiente.
  Antes de cada una sale su tabla. Al final te hago unas preguntas y sale el
  informe.
"""

CABECERA_P10_CAMARA = (
    "LA CÁMARA, antes de grabar: 1 minuto. Siéntate como para narrar, a tu distancia de\n"
    "siempre, con la cara relajada y mirando al centro de la pantalla. QUIETO y sin hablar:\n"
    "se mide cuánto tiembla el seguimiento de tu cara con la compensación de contraluz de\n"
    "la cámara puesta y apagada, cuatro tandas de 11 s. No se abre ninguna ventana.")
CABECERA_P10_SIN_INTERFAZ = (
    "PRIMERA GRABACIÓN, sin la interfaz: SOLO 2 etiquetas, la 0 y la 1, y luego q.\n"
    "Aquí no hay navegador ni botones, solo la ventana de Python: ESPACIO en ella pasa\n"
    "de etiqueta (la 0 empieza sola). Las etiquetas 2 a 13 son de la SEGUNDA grabación.")
CABECERA_P10_NARRACION = (
    "SEGUNDA GRABACIÓN, con la interfaz de narración: TODAS las teclas en el navegador.\n"
    "ESPACIO pasa de etiqueta (la 0 empieza sola). Esta misma tabla sale ARRIBA en la\n"
    "página, etiqueta por etiqueta y con el tiempo que llevas: no hace falta volver a\n"
    "esta terminal (si haces clic en ella, las teclas dejan de llegar al navegador hasta\n"
    "que hagas clic en el libro). La ventana de Python también se abre: déjala, sin\n"
    "hacer clic en ella. Si el navegador no queda delante, haz clic una vez en el libro.")

#: El título de la franja de guía en la página.
TITULO_GUIA_P10 = "Sesión p10 · segunda grabación"

#: Ancho de las tablas del P10 en la terminal.
ANCHO_P10 = 88


def rutas_p10(base: Path) -> tuple[Path, Path]:
    """Las dos grabaciones del P10: sin la interfaz y con ella."""
    return (base.with_name(base.name + "_sin_interfaz.csv"),
            base.with_name(base.name + "_narracion.csv"))


def guia_p10_datos(protocolo=PROTOCOLO_P10_NARRACION) -> dict:
    """La guía que la página enseña arriba (``--guia-sesion``), etiqueta por
    etiqueta: la misma tabla que la terminal."""
    return {"titulo": TITULO_GUIA_P10,
            "etiquetas": [{"etiqueta": etiqueta, "que": que, "segundos": segundos, "robot": robot}
                          for etiqueta, que, segundos, robot in protocolo]}


def _escribir_guia() -> Path:
    """La guía en un JSON temporal, para pasárselo a ``control_panel``."""
    with tempfile.NamedTemporaryFile("wb", suffix="_guia_p10.json", delete=False) as f:
        f.write(json.dumps(guia_p10_datos(), ensure_ascii=False, indent=1).encode("utf-8"))
    return Path(f.name)


def _ensayo_p10(abrir: bool = True) -> int:
    """La segunda grabación sin cámara: la demo de la interfaz con la franja de
    guía. No graba nada ni necesita el broker."""
    from telmeow_teleop.control_panel import ejecutar_demo, guia_desde_datos

    print("\nENSAYO de la segunda grabación, sin cámara: la interfaz con la guía arriba.\n"
          "Nada se graba. Para salir, q dos veces en el navegador (o Ctrl-C aquí).\n")
    return ejecutar_demo(abrir=abrir, guia=guia_desde_datos(guia_p10_datos()))


def guia_p10() -> str:
    """Todo lo que se lee antes de la sesión del P10."""
    return "\n".join([
        INTRO_P10,
        tabla_protocolo(PROTOCOLO_P10_SIN_INTERFAZ, cabecera=CABECERA_P10_SIN_INTERFAZ, quien="se ve",
                        ancho=ANCHO_P10),
        tabla_protocolo(PROTOCOLO_P10_NARRACION, cabecera=CABECERA_P10_NARRACION, ancho=ANCHO_P10),
    ])


def cambios_de_pagina(filas: list[dict]) -> list[tuple[dict, int]]:
    """Las filas en que cambió la página dentro del mismo cuento, con el paso
    (+1 adelante, -1 atrás). Abrir un cuento no cuenta: no lleva gesto."""
    cambios = []
    anterior = None
    for f in filas:
        historia, pagina = f.get("historia") or "", _num(f.get("pagina"))
        if (anterior is not None and historia and historia == anterior[0]
                and pagina is not None and anterior[1] is not None and pagina != anterior[1]):
            cambios.append((f, int(pagina - anterior[1])))
        anterior = (historia, pagina)
    return cambios


def ordenes_de(filas: list[dict]) -> list[tuple[int, str]]:
    """(etiqueta, orden) de cada orden registrada; la columna ``orden`` junta con
    «|» las de un mismo frame."""
    return [(int(f["etiqueta"]), o) for f in filas for o in (f.get("orden") or "").split("|") if o]


def vistazos(filas_emocion: list[dict], etiqueta: int) -> tuple[int, int, int]:
    """En una etiqueta del CSV de emoción: (vistazos, filas mirando fuera dentro
    de sus primeros 3 s, de ellas en cuántas el robot conserva la expresión de
    justo antes). Las filas sin cara no cuentan ni cortan el vistazo."""
    n = fuera = conserva = 0
    antes, inicio = None, None
    for f in filas_emocion:
        if int(f["etiqueta"]) != etiqueta or f.get("rostro") != "1":
            continue
        if f.get("mirada_fuera") == "1":
            t = float(f["t"])
            if inicio is None:
                n, inicio = n + 1, t
            if t - inicio <= VISTAZO_MAX_S and antes is not None:
                fuera += 1
                conserva += f.get("emocion") == antes
        else:
            antes, inicio = f.get("emocion"), None
    return n, fuera, conserva


def movimiento_brazos(filas: list[dict], etiqueta: int) -> dict[str, tuple[float, float, float]]:
    """Por brazo, el comando publicado en una etiqueta: (cambios de más de 1° por
    minuto, saltos de más de 5° por minuto, p95 del salto), como en el P7.8."""
    tramo = [f for f in filas if int(f["etiqueta"]) == etiqueta]
    if len(tramo) < 2:
        return {}
    minutos = (float(tramo[-1]["t"]) - float(tramo[0]["t"])) / 60.0
    salida = {}
    for lado, abd, flex in (("L", "armL", "armLtw"), ("R", "armR", "armRtw")):
        comandos = [(_num(f.get(abd)), _num(f.get(flex))) for f in tramo]
        s = saltos([c if None not in c else None for c in comandos])
        if s and minutos > 0:
            salida[lado] = (sum(x > 1 for x in s) / minutos, sum(x > 5 for x in s) / minutos,
                            _percentil(s, 0.95))
    return salida


def brazo_quieto(filas: list[dict], etiqueta: int, lado: str,
                 ventana_s: float = BRAZO_VENTANA_S) -> tuple[float, float | None]:
    """``(segundos con el brazo levantado, desviación en su ventana más quieta)``
    del comando publicado para un brazo (``"L"`` o ``"R"``) en una etiqueta.

    Levantado es con la abducción más de ``BRAZO_LEVANTADO`` grados sobre el
    reposo: con el brazo en la mesa no se sigue y el comando es el reposo, fijo.
    La desviación es la mayor de las dos articulaciones en la ventana de
    ``ventana_s`` segundos seguidos en que es menor; ``None`` si el brazo no
    estuvo levantado ``ventana_s`` segundos seguidos.
    """
    from telmeow_teleop.config import ArmConfig

    abd, flex = ("armL", "armLtw") if lado == "L" else ("armR", "armRtw")
    reposo = ArmConfig.reposo_abduccion
    tramo = [(float(f["t"]), _num(f.get(abd)), _num(f.get(flex)))
             for f in filas if int(f["etiqueta"]) == etiqueta]
    arriba = [(t, a, x) for t, a, x in tramo if a is not None and x is not None
              and a > reposo + BRAZO_LEVANTADO]
    if len(arriba) < 2:
        return 0.0, None
    paso = statistics.median(b[0] - a[0] for a, b in zip(arriba, arriba[1:]))
    segundos = len(arriba) * paso
    mejor = None
    inicio = 0
    for fin in range(len(arriba)):
        while arriba[fin][0] - arriba[inicio][0] > ventana_s:
            inicio += 1
        ventana = arriba[inicio:fin + 1]
        seguida = all(b[0] - a[0] <= 4.0 * paso for a, b in zip(ventana, ventana[1:]))
        if seguida and ventana[-1][0] - ventana[0][0] >= ventana_s - 2.0 * paso and len(ventana) >= 3:
            desviacion = max(statistics.pstdev(v[1] for v in ventana),
                             statistics.pstdev(v[2] for v in ventana))
            mejor = desviacion if mejor is None else min(mejor, desviacion)
    return segundos, mejor


def cabeceo_en_tope(ruta_cabeza: Path) -> tuple[float, float] | None:
    """``(segundos con el cabeceo del robot en su tope de abajo, lo más que bajó
    la cabeza el operador en grados crudos)`` de un CSV de cabeza; ``None`` si no
    tiene filas."""
    import simular_compresion_tilt as sct

    with open(ruta_cabeza, newline="", encoding="utf-8") as f:
        filas = [(float(x["t"]), _num(x.get("raw_tilt")), _num(x.get("tilt"))) for x in csv.DictReader(f)]
    filas = [x for x in filas if x[1] is not None and x[2] is not None]
    if not filas:
        return None
    segundos = sct.segundos_en_tope([t for t, _, _ in filas], [tilt for _, _, tilt in filas])
    return segundos, -min(crudo for _, crudo, _ in filas)


def ruta_contraluz(base: Path) -> Path:
    return base.with_name(base.name + "_contraluz.csv")


def leer_contraluz(base: Path) -> dict[int, dict[str, float]] | None:
    """La medición de la cámara (``tools/medir_contraluz.py --malla``): por valor
    de la compensación, la mediana de cada cifra entre sus tandas."""
    ruta = ruta_contraluz(base)
    if not ruta.is_file():
        return None
    with open(ruta, newline="", encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    salida: dict[int, dict[str, float]] = {}
    for valor in sorted({int(x["contraluz"]) for x in filas}):
        tandas = [x for x in filas if int(x["contraluz"]) == valor]
        salida[valor] = {}
        for campo in ("fps", "brillo", "ruido_imagen", "ruido_malla", "con_cara"):
            numeros = [v for v in (_num(x.get(campo)) for x in tandas) if v is not None]
            if numeros:
                salida[valor][campo] = statistics.median(numeros)
    return salida or None


def _ruta_respuestas(base: Path) -> Path:
    return base.with_name(base.name + "_respuestas.csv")


def preguntar_p10(base: Path, entrada=None) -> dict[str, str]:
    """Las preguntas del final; las respuestas quedan en ``<base>_respuestas.csv``."""
    entrada = entrada or input
    print("\nUnas preguntas sobre la segunda grabación (s = sí, n = no):")
    respuestas = {}
    for clave, pregunta, _ in PREGUNTAS_P10:
        validas = ("d", "f") if clave == "vista" else ("s", "n")
        while True:
            r = entrada(f"  {pregunta} [{'/'.join(validas)}] ").strip().lower()[:1]
            if r in validas:
                break
            print(f"    Responde {' o '.join(validas)}.")
        respuestas[clave] = r
    with open(_ruta_respuestas(base), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["clave", "pregunta", "respuesta"])
        for clave, pregunta, _ in PREGUNTAS_P10:
            w.writerow([clave, pregunta, respuestas[clave]])
    return respuestas


def leer_respuestas(base: Path) -> dict[str, str] | None:
    ruta = _ruta_respuestas(base)
    if not ruta.is_file():
        return None
    with open(ruta, newline="", encoding="utf-8") as f:
        return {fila["clave"]: fila["respuesta"] for fila in csv.DictReader(f)}


def analizar_narracion(base: Path) -> str:
    """El informe del P10, con los criterios fijados antes de grabar."""
    import analizar_sesion as asn
    import filtros_cabeza as fc
    from telmeow_teleop.main import rutas_de_grabacion

    def ok(cumple):
        return "OK" if cumple else "NO CUMPLE"

    ruta_sin, ruta_con = rutas_p10(base)
    lineas = [f"INFORME DEL P10 DE {base.name}", "=" * 60, ""]
    if not ruta_con.is_file():
        return "\n".join(lineas + [f"Falta {ruta_con.name}: no hay nada que analizar."])
    con = asn.cargar(ruta_con)

    def resumen(filas):
        try:
            return asn.resumir(filas, 1)
        except ValueError:
            return None

    r_con = resumen(con)
    r_sin = resumen(asn.cargar(ruta_sin)) if ruta_sin.is_file() else None
    lineas.append("No le roba tiempo al bucle (etiqueta 1, hablando):")
    falta = [r.name for r, x in ((ruta_sin, r_sin), (ruta_con, r_con)) if x is None or not x.fps_medios]
    if falta:
        lineas.append(f"  sin datos: falta la etiqueta 1 en {', '.join(falta)}")
    else:
        fraccion = r_con.fps_medios / r_sin.fps_medios
        lineas.append(f"  fps medios sin interfaz {r_sin.fps_medios:.1f}, con interfaz {r_con.fps_medios:.1f}: "
                      f"{100 * fraccion:.0f} % (criterio >= {100 * FPS_INTERFAZ_MIN:.0f} %) "
                      f"{ok(fraccion >= FPS_INTERFAZ_MIN)}")
    if r_con is not None and r_con.hz is not None:
        referencia = f"; sin interfaz {r_sin.hz:.1f}" if r_sin is not None and r_sin.hz is not None else ""
        lineas.append(f"  publicacion con interfaz {r_con.hz:.1f} Hz (criterio >= {HZ_INTERFAZ_MIN}{referencia}) "
                      f"{ok(r_con.hz >= HZ_INTERFAZ_MIN)}")
        lineas.append(f"  mensajes perdidos en la etiqueta: {r_con.fallidos}")

    cambios = cambios_de_pagina(con)
    por_etiqueta: dict[int, int] = {}
    for f, _ in cambios:
        por_etiqueta[f["etiqueta"]] = por_etiqueta.get(f["etiqueta"], 0) + 1
    lineas += ["", "Pagina y gesto juntos (cada cambio de pagina con su gesto en la misma fila; ninguno en pausa):",
               f"  cambios de pagina: {len(cambios)} (esperados {sum(PAGINAS_P10.values())}: "
               + ", ".join(f"{n} en la {e}" for e, n in PAGINAS_P10.items()) + "); por etiqueta, "
               + (", ".join(f"{e}: {n}" for e, n in sorted(por_etiqueta.items())) or "ninguno")]
    if cambios:
        con_gesto = sum(1 for f, paso in cambios if f.get("gesto") == ("page_next" if paso > 0 else "page_prev"))
        en_pausa = sum(1 for f, _ in cambios if f["pausa"])
        lineas.append(f"  con su gesto, en su sentido, en la misma fila: {con_gesto} de {len(cambios)} "
                      f"{ok(con_gesto == len(cambios))}")
        lineas.append(f"  en pausa: {en_pausa} (criterio 0) {ok(en_pausa == 0)}")
    else:
        lineas.append("  NO VALE: no se paso ninguna pagina")
    intentos = sum(1 for f in con if f["pausa"]
                   for o in (f.get("orden") or "").split("|") if o.startswith("pagina_"))
    lineas.append(f"  intentos de pasar de pagina en pausa: {intentos} (el protocolo hace 1, en la 8)")

    lineas += ["", "Ordenes que llegaron del navegador (columna orden), por etiqueta:"]
    agrupadas: dict[int, dict[str, int]] = {}
    for etiqueta, orden in ordenes_de(con):
        if orden.endswith("@navegador"):
            nombre = orden.rsplit("@", 1)[0]
            agrupadas.setdefault(etiqueta, {})
            agrupadas[etiqueta][nombre] = agrupadas[etiqueta].get(nombre, 0) + 1
    for etiqueta, cuenta in sorted(agrupadas.items()):
        lineas.append(f"  {etiqueta:>2}: " + ", ".join(f"{n} x{c}" if c > 1 else n for n, c in cuenta.items()))
    desde_python = sum(1 for _, orden in ordenes_de(con) if orden.endswith("@tecla"))
    lineas.append(f"  ordenes desde la ventana de Python: {desde_python} (lo esperado: 0)")

    respuestas = leer_respuestas(base)
    lineas += ["", "Lo que respondio el operador:"]
    if respuestas is None:
        lineas.append(f"  sin respuestas (falta {_ruta_respuestas(base).name})")
    else:
        for clave, pregunta, cumple in PREGUNTAS_P10:
            r = respuestas.get(clave, "")
            texto = {"d": "detras", "f": "frente", "s": "si", "n": "no"}.get(r, "--")
            lineas.append(f"  {pregunta} {texto}" + (f" {ok(r == cumple)}" if cumple else ""))

    lineas += ["", f"P7.8 en vivo, brazos a la vista y quietos (desviacion del comando en sus "
                   f"{BRAZO_VENTANA_S:.0f} s mas quietos con el brazo levantado; criterio <= "
                   f"{BRAZO_QUIETO_MAX:.0f} grados):"]
    for etiqueta, lado, texto in ((3, "L", "brazo izquierdo estirado"), (4, "R", "brazo derecho estirado")):
        segundos, desviacion = brazo_quieto(con, etiqueta, lado)
        if desviacion is None:
            lineas.append(f"  {etiqueta:>2} {texto}: NO VALE: el brazo estuvo levantado {segundos:.0f} s, y "
                          f"hacen falta {BRAZO_VENTANA_S:.0f} seguidos (¿la mano salia de la camara?)")
        else:
            lineas.append(f"  {etiqueta:>2} {texto}: levantado {segundos:.0f} s; desviacion {desviacion:.1f} "
                          f"grados {ok(desviacion <= BRAZO_QUIETO_MAX)}")

    rutas = rutas_de_grabacion(ruta_con)
    emocion = fc.leer(rutas["emocion"]) if rutas["emocion"].is_file() else []
    lineas += ["", "P7.8 en vivo, vistazos (mirando abajo, el robot conserva la expresion de justo antes, "
                   "hasta 3 s):"]
    for etiqueta, emo in ((9, "feliz"), (10, "triste")):
        filas = [f for f in emocion if int(f["etiqueta"]) == etiqueta and f.get("rostro") == "1"]
        if not filas:
            lineas.append(f"  {etiqueta:>2} {emo}: sin datos")
            continue
        n, fuera, conserva = vistazos(emocion, etiqueta)
        tramo = 100.0 * sum(f.get("emocion") == emo for f in filas) / len(filas)
        if fuera == 0:
            lineas.append(f"  {etiqueta:>2} {emo}: NO VALE: no se detecto ningun vistazo (la cabeza no bajo "
                          f"mas de 8 grados); {emo} en el {tramo:.0f} % del tramo")
            continue
        p = 100.0 * conserva / fuera
        lineas.append(f"  {etiqueta:>2} {emo}: {n} vistazos (el protocolo hace 3); conserva la expresion en el "
                      f"{p:.0f} % de sus filas (criterio >= {VISTAZO_CONSERVA_MIN:.0f} %) "
                      f"{ok(p >= VISTAZO_CONSERVA_MIN)}; {emo} en el {tramo:.0f} % del tramo")

    lineas += ["", f"P10.2, el cabeceo hacia abajo (segundos con el robot parado en su tope; criterio < "
                   f"{TOPE_MAX_S:.0f} s; el 2026-11-12, sin la compresion, unos 15):"]
    tope = cabeceo_en_tope(rutas["cabeza"]) if rutas["cabeza"].is_file() else None
    if tope is None:
        lineas.append(f"  sin datos: falta {rutas['cabeza'].name}")
    elif tope[1] < TOPE_BAJADA_MIN:
        lineas.append(f"  NO VALE: lo mas que bajaste la cabeza fueron {tope[1]:.0f} grados, y hacen falta "
                      f"{TOPE_BAJADA_MIN:.0f} para probarlo; en el tope, {tope[0]:.1f} s")
    else:
        lineas.append(f"  en el tope {tope[0]:.1f} s, bajando la cabeza hasta {tope[1]:.0f} grados "
                      f"{ok(tope[0] < TOPE_MAX_S)}")

    ruidos = [r for r in (_num(f.get("ruido_malla")) for f in con if f["etiqueta"] == 1) if r is not None]
    lineas += ["", "El temblor de la cabeza (ruido de la malla; el 2026-11-10, de dia, 0.75; el 2026-11-12, "
                   "de noche, 1.2):"]
    lineas.append(f"  hablando (etiqueta 1): {statistics.median(ruidos):.2f}" if ruidos
                  else "  hablando (etiqueta 1): sin datos")
    medida = leer_contraluz(base)
    if medida is None or 0 not in medida or 1 not in medida:
        lineas.append(f"  la camara, con y sin compensacion de contraluz: sin datos (falta "
                      f"{ruta_contraluz(base).name})")
    else:
        for valor, nombre in ((1, "puesta, de fabrica"), (0, "apagada, como se usa")):
            m = medida[valor]
            lineas.append(f"  compensacion {nombre}: {m.get('fps', float('nan')):.1f} fps, brillo "
                          f"{m.get('brillo', float('nan')):.0f}, ruido de la malla "
                          + (f"{m['ruido_malla']:.2f}" if "ruido_malla" in m else "sin cara"))
        cara = min(medida[0].get("con_cara", 0.0), medida[1].get("con_cara", 0.0))
        if "ruido_malla" not in medida[0] or "ruido_malla" not in medida[1] or cara < CARA_MIN_CONTRALUZ:
            lineas.append(f"  NO VALE: la malla vio la cara en el {100 * cara:.0f} % de las fotos de la medicion, y "
                          f"hace falta el {100 * CARA_MIN_CONTRALUZ:.0f} (hay que estar delante y quieto)")
        else:
            veces = medida[0]["ruido_malla"] / max(medida[1]["ruido_malla"], 1e-9)
            lineas.append(f"  apagada, el ruido de la malla es {veces:.2f} veces el de puesta (criterio <= "
                          f"{RUIDO_CONTRALUZ_MAX}) {ok(veces <= RUIDO_CONTRALUZ_MAX)}")
    return "\n".join(lineas)


def _hay_que_grabar(ruta: Path, entrada) -> bool:
    """Una grabación que ya existe solo se repite si el operador lo pide."""
    if not ruta.is_file():
        return True
    r = entrada(f"\nYa existe {ruta.name}. ¿Grabarla otra vez? La de antes se pierde. [s/N] ")
    return r.strip().lower().startswith("s")


def _sesion_narracion(base: Path, py: str, entrada=None) -> None:
    """Las dos grabaciones del P10, cada una con su tabla delante, y las preguntas."""
    entrada = entrada or input
    sin, con = rutas_p10(base)
    print(INTRO_P10)
    contraluz = ruta_contraluz(base)
    if _hay_que_grabar(contraluz, entrada):
        print(CABECERA_P10_CAMARA)
        _esperar("Colócate como para narrar. Al seguir empieza la medición: 1 minuto quieto.")
        subprocess.run([py, str(PROJECT_ROOT / "tools" / "medir_contraluz.py"), "--vueltas", "2",
                        "--malla", "--csv", str(contraluz)], cwd=PROJECT_ROOT)
    if _hay_que_grabar(sin, entrada):
        print(tabla_protocolo(PROTOCOLO_P10_SIN_INTERFAZ, cabecera=CABECERA_P10_SIN_INTERFAZ, quien="se ve",
                              ancho=ANCHO_P10))
        _esperar("Lee la tabla. Al seguir se abre la ventana de Python y empieza la etiqueta 0.\n"
                 "  Cuando acabe la etiqueta 1, pulsa q en esa ventana.")
        subprocess.run([py, "-m", "telmeow_teleop.main", "--record", str(sin)], cwd=PROJECT_ROOT)
    if _hay_que_grabar(con, entrada):
        print(tabla_protocolo(PROTOCOLO_P10_NARRACION, cabecera=CABECERA_P10_NARRACION, ancho=ANCHO_P10))
        _esperar("Lee la tabla. Al seguir se abre la ventana de Python y, unos segundos después,\n"
                 "  el navegador: desde ahí, todo con el navegador delante.")
        guia = _escribir_guia()
        try:
            subprocess.run([py, "-m", "telmeow_teleop.control_panel", "--record", str(con),
                            "--guia-sesion", str(guia)], cwd=PROJECT_ROOT)
        finally:
            guia.unlink(missing_ok=True)
        preguntar_p10(base, entrada)


def _guardar_informe(base: Path, analisis) -> int:
    print("\nAnalizando...\n")
    informe = analisis(base)
    print(informe)
    destino = base.with_name(base.name + "_informe.txt")
    destino.write_bytes(informe.encode("utf-8"))
    print(f"\nInforme guardado en {destino}")
    return 0


# --------------------------------------------------------------------------
# La sesión
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sesion_verificacion", description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", help="nombre de los archivos, sin .csv (p7_final; p75 con --gestos)")
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument("--gestos", action="store_true", help="la sesion corta de la cabeza y la mano a la cara")
    modo.add_argument("--ajustes", action="store_true", help="la del P7.6: enojo, mejillas, codos y luz")
    modo.add_argument("--entrenar", action="store_true", help="la del P7.7: caras para reentrenar la emocion")
    modo.add_argument("--confirmar", action="store_true", help="la que confirma el reentrenado y la regla suave")
    modo.add_argument("--narracion", action="store_true", help="la del P10: sin y con la interfaz de narracion")
    parser.add_argument("--analizar", action="store_true", help="solo el informe, sin grabar")
    parser.add_argument("--guia", action="store_true", help="solo las tablas del protocolo, sin grabar")
    parser.add_argument("--ensayo", action="store_true",
                        help="con --narracion: la segunda grabacion sin camara, con la guia en la pagina")
    parser.add_argument("--sin-navegador", action="store_true", help="con --ensayo: no abrir el navegador")
    args = parser.parse_args(argv)
    modo_ = ("gestos" if args.gestos else "ajustes" if args.ajustes else "entrenar" if args.entrenar
             else "confirmar" if args.confirmar else "narracion" if args.narracion else "")
    base = PROJECT_ROOT / (args.base or {"gestos": "p75", "ajustes": "p76", "entrenar": "p77",
                                         "confirmar": "p78", "narracion": "p10"}.get(modo_, "p7_final"))
    py = sys.executable
    analisis = {"gestos": analizar_gestos, "ajustes": analizar_ajustes,
                "entrenar": analizar_entrenamiento,
                "confirmar": analizar_confirmacion, "narracion": analizar_narracion}.get(modo_, analizar)
    protocolo = {"gestos": PROTOCOLO_GESTOS, "ajustes": PROTOCOLO_AJUSTES,
                 "entrenar": PROTOCOLO_ENTRENAR, "confirmar": PROTOCOLO_CONFIRMAR}.get(modo_, PROTOCOLO)

    if args.guia:
        print(guia_p10() if modo_ == "narracion" else tabla_protocolo(protocolo))
        return 0
    if args.ensayo:
        if modo_ != "narracion":
            parser.error("--ensayo es de la sesion del P10: --narracion --ensayo")
        return _ensayo_p10(abrir=not args.sin_navegador)

    if args.analizar:
        informe = analisis(base)
        print(informe)
        base.with_name(base.name + "_informe.txt").write_bytes(informe.encode("utf-8"))
        return 0

    arrancados: list[subprocess.Popen] = []
    try:
        # puerto_abierto se pasa desde aquí para que las pruebas lo sustituyan.
        if not asegurar_broker(arrancados, puerto_abierto=puerto_abierto):
            return 1
        if modo_ == "narracion":
            # Ni el visor del 8000 ni el monitor: la interfaz la sirve main.py.
            _sesion_narracion(base, py)
            return _guardar_informe(base, analisis)

        if not puerto_abierto(8000):
            arrancados.append(subprocess.Popen(
                [py, "-m", "http.server", "8000", "--directory", str(PROJECT_ROOT / "telmeow_viewer")],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            time.sleep(1.0)
        import webbrowser

        webbrowser.open(URL_VISOR)
        _esperar(f"Visor: {URL_VISOR}\n  Conectalo a ws://localhost:9001 con el topic telmeow/teleop/pose.")

        print(tabla_protocolo(protocolo))
        _esperar("Lee la tabla. Al seguir se abre la ventana del pipeline: la etiqueta 0\n"
                 "  empieza sola, y cada ESPACIO pasa a la siguiente.")
        latencia = base.with_name(base.name + "_latencia.csv")
        monitor = subprocess.Popen(
            [py, str(PROJECT_ROOT / "tools" / "mqtt_monitor.py"), "--latencia", str(latencia)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            subprocess.run([py, "-m", "telmeow_teleop.main", "--record", str(base.with_suffix(".csv")),
                            "--guardar-recortes", str(base.with_name(base.name + "_recortes"))],
                           cwd=PROJECT_ROOT)
        finally:
            monitor.terminate()
            monitor.wait(timeout=5)

        if args.ajustes or args.entrenar or args.confirmar:
            pass
        elif args.gestos:
            _esperar("Por ultimo, 20 s con el demo de la cabeza solo, para saber de donde sale\n"
                     "  el temblor: cara relajada, quieto, manos abajo. q lo corta.")
            subprocess.run([py, "-m", "telmeow_teleop.head_tracker",
                            "--record", str(base.with_name(base.name + "_demo_cabeza.csv"))],
                           cwd=PROJECT_ROOT)
        else:
            _esperar("Ahora la latencia de la camara, con la LUZ DE TRABAJO de siempre:\n"
                     "  30 s mirando la pantalla, quieto. q la corta.")
            r = subprocess.run([py, str(PROJECT_ROOT / "tools" / "medir_latencia_camara.py"),
                                "--csv", str(base.with_name(base.name + "_latencia_camara.csv"))])
            if r.returncode != 0:
                otra = input("\nNo hubo contraste. ¿Repetir con menos luz (solo una lampara)? [s/N] ")
                if otra.strip().lower().startswith("s"):
                    subprocess.run([py, str(PROJECT_ROOT / "tools" / "medir_latencia_camara.py"),
                                    "--csv", str(base.with_name(base.name + "_latencia_camara_penumbra.csv"))])

        print("\nAnalizando...\n")
        informe = analisis(base)
        print(informe)
        destino = base.with_name(base.name + "_informe.txt")
        destino.write_bytes(informe.encode("utf-8"))
        print(f"\nInforme guardado en {destino}")
        return 0
    finally:
        for proceso in arrancados:
            proceso.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
