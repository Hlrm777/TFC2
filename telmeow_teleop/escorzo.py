"""Corrección de la profundidad del brazo por escorzo (P5.5).

POR QUE HACE FALTA (H39)
------------------------
La profundidad de ``pose_world_landmarks`` es la coordenada más débil de
BlazePose, y el 2026-09-27 se vio fallar de la forma más molesta posible. Con
las manos levantadas junto a la cara y los codos abajo —el gesto de «manos
arriba», y el del saludo—, BlazePose ponía las manos unos 35 cm POR DELANTE de
los hombros y apenas 3-4 cm por encima. En la imagen, en cambio, el brazo y el
antebrazo se veían con casi todo su largo (32 y 28 cm aparentes frente a ~30 y
~27 reales): estaban casi en el plano de la imagen, así que la mano no podía
estar 35 cm por delante. Con la mano «adelante», ningún mapeo podía dar el
gesto, y el robot apuntaba al frente.

LA IDEA
-------
La medida buena de BlazePose es la de la imagen: dice por qué RAYO de la cámara
se ve cada articulación. Lo que no dice es a qué distancia está sobre ese rayo,
y eso lo fija el largo del hueso: el codo está en su rayo y a un largo de brazo
del hombro, o sea, en la intersección del rayo con una esfera centrada en el
hombro. Lo mismo para la mano, con el antebrazo desde el codo. Es la
reconstrucción clásica de Taylor (2000), con la cámara como un modelo de
agujero (pinhole), y tiene dos ventajas sobre la profundidad de BlazePose: el
punto reconstruido cae exactamente en su píxel, y cada hueso conserva su largo.

LA AMBIGÜEDAD, QUE ES LO DELICADO. El rayo corta la esfera en DOS puntos: el
mismo píxel lo produce el hueso en una postura y en otra que apunta más hacia la
cámara. Con perspectiva, las dos no coinciden ni siquiera para un hueso en el
plano de la imagen: en la geometría de la foto del H39, el codo tenía una
solución 2 cm por detrás del hombro y otra 22 cm hacia la cámara. Elegir por la
profundidad de BlazePose, sin más, cae en la trampa del H39: su sesgo hacia
delante escoge la falsa. Así que la elección suma tres costes (``coste``):

- **Parecido a BlazePose**: en los gestos que BlazePose distingue bien —acercar
  el brazo a la cámara— manda, porque su profundidad es grande y clara.
- **Preferencia por el plano del tronco** (``PESO_PLANO``): entre dos
  soluciones que BlazePose apenas distingue, la más cercana al plano de la
  articulación anterior. Es donde está el brazo casi todo el tiempo al narrar.
- **Continuidad** (``PESO_CONTINUIDAD``): la solución del frame anterior, como
  desempate, para que el brazo no salte de una a otra cuando los costes se
  parecen. Pesa MENOS que BlazePose a propósito: con más peso, una vez elegido
  el plano ya no dejaría acercar el brazo a la cámara, y eso deshace el P5.3.

Si BlazePose ya era coherente con la imagen, la solución elegida es la suya: la
corrección solo cambia algo cuando su profundidad no casa con los píxeles.

EL ANCLA: la distancia de los hombros a la cámara sale de comparar su
separación en la imagen con la que da BlazePose en metros. Su profundidad
relativa —el tronco girado— la aporta BlazePose, que en el tronco sí es fiable.

LO QUE HAY QUE DECLARAR: la focal de la cámara no está calibrada con un patrón.
Se ESTIMO con la grabación de validación del 2026-09-27 (``FOCAL_RELATIVA``,
ajustable con ``ARM_FOCAL_RELATIVA``). Un error en la escala del cuerpo de
BlazePose escala la escena entera sin cambiar las direcciones del brazo; uno en
la focal, en cambio, altera la perspectiva, y de ella depende cuánto se separan
las dos soluciones de cada hueso. Con la focal del head tracker
(``focal = ancho``) el gesto de las manos junto a la cara quedaba justo en la
frontera entre las dos, y bastaba un cambio de 0.4 a 0.6 en ``PESO_PLANO`` para
pasar de 27° a 3° de la cruz (H41).

Solo biblioteca estándar y numpy, sin estado: todo se prueba con una cámara
sintética (``tests/test_escorzo.py``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

__all__ = [
    "Camara",
    "interseccion",
    "anclar_hombros",
    "reconstruir_brazo",
    "PESO_PLANO",
    "PESO_CONTINUIDAD",
    "FOCAL_RELATIVA",
]

#: Peso de la preferencia por el plano de la articulación anterior, por metro
#: de profundidad relativa.
#:
#: Es un compromiso, y estrecho. En papel, con la geometría de la foto del H39,
#: el codo de la foto pide más de 0.39 para quedarse en el plano con BlazePose
#: diciendo 14 cm hacia delante, y una mano que se acerca 25 cm a la cámara
#: fuera del eje pide menos de 0.49 para no quedarse atrás con BlazePose
#: diciendo 20. De ahí 0.4. Depende de cuánto se equivoque BlazePose en cada
#: caso, así que se valida con la grabación de puntos 2D y 3D.
PESO_PLANO: float = 0.4

#: Peso de la continuidad con la profundidad elegida en el frame anterior, por
#: metro. Menor que el de BlazePose (1): solo desempata. Con 1.5 se probó, en
#: papel, que el brazo quedaba atrapado en el plano al acercarlo a la cámara.
PESO_CONTINUIDAD: float = 0.3

#: Focal de la cámara integrada del operador dividida por el ancho de la imagen:
#: unos 71° de campo horizontal, lo normal en la webcam de un portátil.
#:
#: ESTIMADA, no calibrada (H41). Rehaciendo offline el escorzo de las dos
#: grabaciones de validación del 2026-09-27 con focales de 0.5 a 1.2, las de 0.6
#: a 0.8 son las únicas que aciertan a la vez los dos gestos de referencia: las
#: manos junto a la cara con los codos abajo quedan en el plano (el robot a
#: 0-23° de la cruz, frente a 27-29° con 1.0) y los brazos estirados hacia la
#: cámara siguen delante (el robot a 55-62°, su tope es 68°). Y es lo que sale
#: de la geometría: 190 px entre hombros a 0.6-0.9 m de la cámara dan 0.5-0.85.
#: Con 1.0, la convención del head tracker, el primer gesto quedaba en la
#: frontera entre las dos soluciones. Se confirma midiendo la cámara (una hoja
#: A4 a distancia conocida).
FOCAL_RELATIVA: float = 0.7


@dataclass(frozen=True)
class Camara:
    """Cámara de agujero: focal y centro óptico, en píxeles.

    Marco de la cámara: x a la derecha de la imagen, y hacia abajo y z hacia el
    fondo de la escena, el mismo sentido que ``pose_world_landmarks``.
    """

    focal: float
    cx: float
    cy: float

    @classmethod
    def desde_imagen(
        cls, ancho: float, alto: float, focal_relativa: float = 1.0
    ) -> "Camara":
        """Cámara sin calibrar: centro óptico en el centro de la imagen y
        ``focal = focal_relativa * ancho`` (la convención del head tracker)."""
        if not (ancho > 0 and alto > 0 and focal_relativa > 0):
            raise ValueError("ancho, alto y focal_relativa deben ser positivos")
        return cls(focal=focal_relativa * ancho, cx=ancho / 2.0, cy=alto / 2.0)

    def rayo(self, u: float, v: float) -> np.ndarray:
        """Dirección del rayo del píxel ``(u, v)``, con componente z = 1."""
        return np.array(
            [(u - self.cx) / self.focal, (v - self.cy) / self.focal, 1.0],
            dtype=np.float64,
        )

    def proyectar(self, punto) -> tuple[float, float]:
        """Píxel en que se ve un punto 3D del marco de la cámara."""
        x, y, z = (float(c) for c in punto)
        return (self.focal * x / z + self.cx, self.focal * y / z + self.cy)


def interseccion(
    rayo,
    centro,
    radio: float,
    profundidad_bp: float,
    profundidad_previa: float | None = None,
    peso_plano: float = PESO_PLANO,
    peso_continuidad: float = PESO_CONTINUIDAD,
) -> tuple[np.ndarray, bool]:
    """Punto del ``rayo`` que está a ``radio`` de ``centro``.

    De las dos soluciones se toma la de menor coste (ver el docstring del
    módulo), con las profundidades medidas RESPECTO AL CENTRO: la que estimó
    BlazePose, cero (el plano) y la elegida en el frame anterior, si la hay.

    Si el rayo no llega a cortar la esfera —el hueso se ve más largo de lo que
    mide: error de escala, de focal o de largo—, se toma el punto del rayo más
    cercano al centro, que es a la vez el más cercano a la esfera.

    Returns:
        ``(punto, exacto)``, con ``exacto = False`` en el caso sin corte.
    """
    r = np.asarray(rayo, dtype=np.float64)
    c = np.asarray(centro, dtype=np.float64)
    a = float(r @ r)
    b = float(r @ c)
    k = float(c @ c) - float(radio) * float(radio)
    discriminante = b * b - a * k
    if discriminante <= 0.0:
        return (b / a) * r, False
    raiz = math.sqrt(discriminante)

    def coste(t: float) -> float:
        # El rayo tiene z = 1, así que la profundidad de t·r es t.
        dz = t - c[2]
        total = abs(dz - profundidad_bp) + peso_plano * abs(dz)
        if profundidad_previa is not None:
            total += peso_continuidad * abs(dz - profundidad_previa)
        return total

    t = min(((b - raiz) / a, (b + raiz) / a), key=coste)
    return t * r, True


def anclar_hombros(
    camara: Camara, img_a, img_b, mundo_a, mundo_b
) -> tuple[np.ndarray, np.ndarray] | None:
    """Posición 3D de los dos hombros en el marco de la cámara.

    La distancia a la cámara sale de su separación: en la imagen se ven a
    ``focal · ancho / z`` píxeles, con ``ancho`` la separación en el plano de la
    imagen que da BlazePose en metros. La diferencia de profundidad entre los
    dos —el tronco girado— la pone BlazePose.

    Args:
        img_a, img_b: píxeles ``(u, v)`` de cada hombro.
        mundo_a, mundo_b: ``(x, y, z)`` de cada hombro en ``pose_world_landmarks``.

    Returns:
        ``(hombro_a, hombro_b)``, o ``None`` si la separación es degenerada.
    """
    ua, va = (float(c) for c in img_a)
    ub, vb = (float(c) for c in img_b)
    xa, ya, za = (float(c) for c in mundo_a)
    xb, yb, zb = (float(c) for c in mundo_b)
    ancho_imagen = math.hypot(ua - ub, va - vb)
    ancho_mundo = math.hypot(xa - xb, ya - yb)
    if not (ancho_imagen > 1e-6 and ancho_mundo > 1e-6):
        return None
    z_medio = camara.focal * ancho_mundo / ancho_imagen
    z_mundo_medio = (za + zb) / 2.0
    hombros = []
    for (u, v), z_mundo in (((ua, va), za), ((ub, vb), zb)):
        z = z_medio + (z_mundo - z_mundo_medio)
        if not z > 1e-6:
            return None
        hombros.append(camara.rayo(u, v) * z)
    return hombros[0], hombros[1]


def reconstruir_brazo(
    camara: Camara,
    hombro_3d,
    img_codo,
    img_mano,
    largo_brazo: float,
    largo_antebrazo: float,
    dz_codo_bp: float,
    dz_mano_bp: float,
    previo: tuple[float, float] | None = None,
) -> tuple[np.ndarray, np.ndarray, tuple[float, float]]:
    """Codo y mano en el marco de la cámara, desde sus píxeles y el largo de
    cada hueso, encadenando dos intersecciones rayo-esfera.

    ``dz_codo_bp`` y ``dz_mano_bp`` son las profundidades relativas que estimó
    BlazePose (codo respecto al hombro, mano respecto al codo), y ``previo`` las
    que se eligieron en el frame anterior: las tres entran en el coste de
    elegir entre las dos soluciones de cada hueso.

    Returns:
        ``(codo, mano, (dz_codo, dz_mano))``: las dos articulaciones y las
        profundidades relativas elegidas, para pasarlas como ``previo`` en el
        frame siguiente.
    """
    previo_codo, previo_mano = previo if previo is not None else (None, None)
    codo, _ = interseccion(
        camara.rayo(*img_codo), hombro_3d, largo_brazo, dz_codo_bp, previo_codo
    )
    mano, _ = interseccion(
        camara.rayo(*img_mano), codo, largo_antebrazo, dz_mano_bp, previo_mano
    )
    elegidas = (float(codo[2] - hombro_3d[2]), float(mano[2] - codo[2]))
    return codo, mano, elegidas
