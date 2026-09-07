"""Pruebas del arm tracker.

LO QUE ESTAS PRUEBAS TIENEN QUE ATRAPAR, Y POR QUE ESTAN ESCRITAS ASI
---------------------------------------------------------------------
El PROMPT 4 dejó el cabeceo **invertido** pasando 400 pruebas en verde, y el
mismo modo de fallo ya había ocurrido antes con el eje de referencia a 180° del
sitio (H17 y H22 del plan maestro). La causa fue siempre la misma:

    *una prueba sintética que fija su propia referencia no puede detectar que la
    referencia sea la equivocada.*

El arm tracker tiene cuatro canales con signo más una decisión de espejo, o sea
cuatro veces esa superficie. Así que aquí ninguna prueba de signo se compara
consigo misma. Cada una se ata a algo externo:

- ``test_ida_y_vuelta_contra_el_rig_del_visor`` compone la pose del brazo con
  una **transcripción independiente de los ejes del visor** y exige que el
  estimador la recupere. Si alguien toca los ejes del estimador, esta prueba se
  pone roja aunque el estimador siga siendo coherente consigo mismo.
- Las de postura enuncian **afirmaciones físicas en el propio test** («el brazo
  horizontal hacia el costado es abducción de 90°»), no derivadas del código.
- La del espejo comprueba una **propiedad invariante**, que no depende de qué
  convenio sea el correcto.

Lo que estas pruebas NO pueden atrapar, y hay que decirlo: si el sentido de
``armL`` en el visor fuera el contrario del que se supone, todo esto seguiría en
verde. Por eso el sentido no es una constante sino ``invert_abduccion`` /
``invert_flexion``, y se fija mirando el visor con un solo slider en movimiento.
"""

import math

import numpy as np
import pytest

from telmeow_teleop.arm_tracker import (
    CANALES,
    EJE_ANTERIOR,
    INCLINACION_FLEXION,
    LANDMARKS,
    PUNTOS_MANO,
    ArmAngles,
    ArmTracker,
    arm_angles,
    canal_del_robot,
    torso_basis,
)
from telmeow_teleop.limits import ARM_ABDUCCION_1D, FLEXION_RANGE, Side

# --------------------------------------------------------------------------
# Geometría de referencia
# --------------------------------------------------------------------------
#
# Sistema de ``pose_world_landmarks``: x a la derecha de la imagen, y hacia
# abajo, z hacia el fondo. Operador de frente a la cámara y erguido.
#
# Sin espejo, su hombro IZQUIERDO físico aparece a la derecha de la imagen.

HOMBRO_IZQ = np.array([0.20, -0.50, 0.0])
HOMBRO_DER = np.array([-0.20, -0.50, 0.0])
CADERA_IZQ = np.array([0.12, 0.00, 0.0])
CADERA_DER = np.array([-0.12, 0.00, 0.0])
#: Vector cadera -> hombro. En este sistema, hacia arriba es -y.
SUPERIOR = np.array([0.0, -1.0, 0.0])

LARGO_BRAZO = 0.6


@pytest.fixture
def base_izq():
    return torso_basis(HOMBRO_IZQ, HOMBRO_DER, SUPERIOR)


@pytest.fixture
def base_der():
    return torso_basis(HOMBRO_DER, HOMBRO_IZQ, SUPERIOR)


def _rotar_en_el_plano_frontal(vector, base, grados):
    """Rota ``vector`` en el plano frontal, el de la abducción.

    Es la inversa exacta de lo que ``arm_angles`` hace para deshacer la
    inclinación del eje de flexión, escrita aquí por separado para que el modelo
    directo de las pruebas siga siendo independiente del código bajo prueba.
    """
    u_lat, _, u_sup = base
    radianes = math.radians(grados)
    coseno, seno = math.cos(radianes), math.sin(radianes)
    lateral = float(np.dot(vector, u_lat))
    superior = float(np.dot(vector, u_sup))
    resto = vector - lateral * u_lat - superior * u_sup
    return (
        resto
        + (lateral * coseno - superior * seno) * u_lat
        + (lateral * seno + superior * coseno) * u_sup
    )


def destino(base, abduccion_grados, flexion_grados, hombro=HOMBRO_IZQ,
            inclinacion=0.0):
    """Punto de destino que produce esos ángulos, según la parametrización.

    Es la composición DIRECTA del cardán, escrita a partir de la fórmula del
    docstring de ``arm_angles``. **El reposo es el brazo en horizontal hacia
    afuera** (``u_lat``), no colgando: ver H30. Se usa solo en las pruebas que
    necesitan construir una postura concreta; las de signo usan el rig del
    visor, que es una fuente independiente.

    ``inclinacion`` reproduce que el eje de flexión del robot no es el
    latero-lateral puro: se aplica como una rotación en el plano frontal DESPUES
    de componer el cardán ideal, que es justo lo que ``arm_angles`` deshace
    antes de descomponer.
    """
    u_lat, u_ant, u_sup = base
    a = math.radians(abduccion_grados)
    f = math.radians(flexion_grados)
    direccion = (
        math.cos(a) * u_lat
        + math.sin(a) * math.cos(f) * u_sup
        + math.sin(a) * math.sin(f) * u_ant
    )
    if inclinacion:
        direccion = _rotar_en_el_plano_frontal(direccion, base, inclinacion)
    return hombro + LARGO_BRAZO * direccion


# --------------------------------------------------------------------------
# 1. Ida y vuelta contra el rig del visor
# --------------------------------------------------------------------------
#
# Transcripción INDEPENDIENTE de telmeow_viewer.html. No se importa nada del
# módulo bajo prueba: si los dos coinciden, es porque describen la misma
# cinemática, no porque compartan código.
#
#   RIG.armR_bal   = { piv: V(109.4,-0.1,142.3), axis: V(0,-1,0) }   (linea 273)
#   RIG.armR_twist = { piv: V(63.1,-0.1,134.4),  axis: V(-0.985,0,-0.170) } (274)
#   RIG.armL_bal   = { piv: V(-109.3,0.1,142.5), axis: V(0,1,0) }    (linea 275)
#   RIG.armL_twist = { piv: V(-63.0,0.1,134.3),  axis: V(-0.985,0,0.174) }  (276)
#
#   gArmRtwist = makeJoint(sceneRoot,  RIG.armR_twist)   (linea 380)
#   gArmRbal   = makeJoint(gArmRtwist, RIG.armR_bal)     (linea 381)
#
# Anidamiento: la flexión (twist) es el padre y la abducción (bal) la hija, así
# que la dirección del brazo es  R_flexion @ R_abduccion @ (brazo en reposo).
#
# Los ejes del visor están en coordenadas de Fusion, con z hacia arriba. Se
# traducen al sistema de MediaPipe (y hacia abajo) al construirlos, que es la
# única transformación que esta prueba comparte con el mundo real.

EJE_ABDUCCION_VISOR = {Side.L: np.array([0.0, 1.0, 0.0]),
                       Side.R: np.array([0.0, -1.0, 0.0])}
EJE_FLEXION_VISOR = {Side.L: np.array([-0.985, 0.0, 0.174]),
                     Side.R: np.array([-0.985, 0.0, -0.170])}


def _rodrigues(eje, grados):
    """Matriz de rotación de Rodrigues, sin depender de cv2."""
    eje = np.asarray(eje, dtype=np.float64)
    eje = eje / np.linalg.norm(eje)
    t = math.radians(grados)
    x, y, z = eje
    K = np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])
    return np.eye(3) + math.sin(t) * K + (1 - math.cos(t)) * (K @ K)


#: Dirección del brazo del robot EN REPOSO, en coordenadas del visor.
#:
#: **Está en horizontal, hacia afuera, no colgando.** Verificado mirando el
#: visor el 2026-09-07 (H30): con los seis canales a cero el robot tiene los
#: brazos en cruz. La primera versión de estas pruebas usaba ``(0,0,-1)`` —el
#: brazo colgando— y por eso cerraba consigo misma siendo falsa: es el mismo
#: mecanismo del H17 y del H22, una prueba sintética que fija su propia
#: referencia no puede ver que la referencia sea la equivocada.
#:
#: Va sobre el eje de la flexión, y eso NO es casualidad: por eso en reposo la
#: flexión no mueve el brazo. Es la singularidad del cardán, y en este robot
#: cae justo en su postura de reposo.
#:
#: El SIGNO no se hereda del eje: en el rig los dos ejes de flexión están
#: escritos apuntando al mismo lado (``-0.985`` en x los dos), lo cual es una
#: convención de la rotación, no un hecho sobre el brazo. Los dos brazos
#: descansan hacia AFUERA, así que el derecho lleva el eje negado.
REPOSO_VISOR = {
    Side.L: EJE_FLEXION_VISOR[Side.L] / np.linalg.norm(EJE_FLEXION_VISOR[Side.L]),
    Side.R: -EJE_FLEXION_VISOR[Side.R] / np.linalg.norm(EJE_FLEXION_VISOR[Side.R]),
}


def direccion_del_visor(lado, abduccion, flexion):
    """Dirección del brazo del ROBOT, en coordenadas del visor (z arriba).

    Reproduce la composición del rig: se parte del brazo en reposo, la abducción
    actúa primero por ser la articulación hija, y la flexión la envuelve.
    """
    reposo = REPOSO_VISOR[lado]
    r_abd = _rodrigues(EJE_ABDUCCION_VISOR[lado], abduccion)
    r_flex = _rodrigues(EJE_FLEXION_VISOR[lado], flexion)
    return r_flex @ (r_abd @ reposo)


#: Sentido del canal ``tw`` del visor respecto a la flexión del operador (H29).
#:
#: −1 significa que ``+armLtw`` lleva el brazo del robot hacia ATRAS, o sea que
#: el canal es extensión y no flexión, al contrario de lo que dice el comentario
#: del rig. Sale de tres anclas independientes, dos verificadas en vivo; el
#: razonamiento completo está en el docstring de ``config.ArmConfig``.
#:
#: Está aquí como constante con nombre, y no disuelto en la prueba, porque es lo
#: ÚNICO que hay que cambiar si la confirmación en el visor dice lo contrario.
SENTIDO_FLEXION_VISOR = -1


def visor_a_mediapipe(vector):
    """Del sistema del visor al de ``pose_world_landmarks``.

    Los dos son el marco del propio sujeto, así que la conversión es una
    rotación rígida y **la misma para los dos brazos**:

    ==================  ==========================
    Visor (Fusion)      MediaPipe world
    ==================  ==========================
    +x = derecha robot  -x  (la derecha del operador cae a la izq. de imagen)
    +y = frente robot   -z  (el frente del operador es hacia la cámara)
    +z = arriba robot   -y  (en MediaPipe la y crece hacia abajo)
    ==================  ==========================

    Su determinante es +1: conserva la quiralidad. Una primera versión de esta
    función invertía la x solo en un brazo, lo que la convertía en una reflexión
    para el otro y hacía salir su abducción con el signo cambiado. Merece
    quedar dicho porque es el mismo mecanismo del que protege ``torso_basis``:
    en cuanto se cuela una reflexión, un signo se da la vuelta sin avisar.
    """
    x, y, z = vector
    return np.array([-x, -z, -y])


@pytest.mark.parametrize("lado", [Side.L, Side.R])
@pytest.mark.parametrize("abduccion", [-55.0, -40.0, -15.0, 10.0, 30.0])
@pytest.mark.parametrize("flexion", [-80.0, -60.0, -20.0, 0.0, 30.0, 75.0])
def test_ida_y_vuelta_contra_el_rig_del_visor(lado, abduccion, flexion):
    """El estimador invierte exactamente la cinemática que el visor aplica.

    Es la prueba central del módulo: ata la parametrización a una fuente
    EXTERNA —los ejes del rig, transcritos a mano— en vez de a sí misma. Si
    alguien reordena el cardán o cambia un eje, esta prueba dice además cuál de
    los dos canales se rompió.

    Se comprueba con tolerancia estrecha, y eso solo es posible porque el
    estimador corrige la inclinación real del eje de flexión
    (``INCLINACION_FLEXION``). Sin esa corrección haría falta aflojar a ~10°, y
    con 10° de holgura esta prueba dejaría de detectar la mayoría de los errores
    que existe para detectar.
    """
    if lado is Side.L:
        hombro, base = HOMBRO_IZQ, torso_basis(HOMBRO_IZQ, HOMBRO_DER, SUPERIOR)
    else:
        hombro, base = HOMBRO_DER, torso_basis(HOMBRO_DER, HOMBRO_IZQ, SUPERIOR)

    direccion = visor_a_mediapipe(direccion_del_visor(lado, abduccion, flexion))
    estimado = arm_angles(
        hombro,
        hombro + LARGO_BRAZO * direccion,
        base,
        INCLINACION_FLEXION[lado],
    )
    assert estimado is not None
    abd_est, flex_est = estimado
    assert flex_est is not None
    assert abd_est == pytest.approx(abduccion, abs=0.2)
    assert flex_est == pytest.approx(flexion, abs=0.2)


@pytest.mark.parametrize("lado", [Side.L, Side.R])
@pytest.mark.parametrize("abduccion", [-40.0, 25.0])
def test_en_los_topes_de_flexion_las_dos_ramas_son_la_misma_postura(lado, abduccion):
    """A flexión ±90 el cardán tiene DOS soluciones para la misma dirección.

    ``(a, +90)`` y ``(-a, -90)`` colocan el brazo exactamente en el mismo sitio:
    con ``cos(±90) = 0``, la componente que distingue el signo de la abducción
    desaparece. No es un defecto del estimador ni del robot, es una propiedad de
    cualquier cardán en su tope; lo que importa es que la POSTURA coincida, y por
    eso aquí se compara la dirección reconstruida y no los ángulos.

    Esta prueba existe porque la de arriba, comparando ángulos, marcaba estos
    casos como fallos siendo correctos. Dejarla escrita evita que alguien
    «arregle» el estimador para que devuelva la otra rama.
    """
    if lado is Side.L:
        hombro, base = HOMBRO_IZQ, torso_basis(HOMBRO_IZQ, HOMBRO_DER, SUPERIOR)
    else:
        hombro, base = HOMBRO_DER, torso_basis(HOMBRO_DER, HOMBRO_IZQ, SUPERIOR)

    for signo in (1.0, -1.0):
        direccion = visor_a_mediapipe(
            direccion_del_visor(lado, abduccion, signo * 90.0)
        )
        estimado = arm_angles(
            hombro, hombro + LARGO_BRAZO * direccion, base,
            INCLINACION_FLEXION[lado],
        )
        assert estimado is not None and estimado[1] is not None
        # Se recompone la postura desde lo estimado y se compara la DIRECCION.
        rehecho = (
            destino(
                base, estimado[0], estimado[1], hombro, INCLINACION_FLEXION[lado]
            )
            - hombro
        )
        rehecho = rehecho / np.linalg.norm(rehecho)
        assert rehecho == pytest.approx(direccion, abs=1e-3)


def test_la_inclinacion_del_eje_sale_de_los_ejes_del_rig():
    """~9.9° en los dos brazos, y con el mismo signo respecto a la abducción.

    Es la comprobación de que ``INCLINACION_FLEXION`` no es un número ajustado a
    ojo hasta que la prueba anterior pasara, sino que se lee del rig.
    """
    assert INCLINACION_FLEXION[Side.L] == pytest.approx(
        math.degrees(math.asin(abs(EJE_FLEXION_VISOR[Side.L][2]))), abs=1e-9
    )
    assert INCLINACION_FLEXION[Side.R] == pytest.approx(
        math.degrees(math.asin(abs(EJE_FLEXION_VISOR[Side.R][2]))), abs=1e-9
    )
    # Los dos lados son la misma pieza reflejada: la diferencia es ruido de
    # medición del CAD, no un diseño asimétrico.
    assert abs(INCLINACION_FLEXION[Side.L] - INCLINACION_FLEXION[Side.R]) < 0.3
    for valor in INCLINACION_FLEXION.values():
        assert 9.0 < valor < 11.0


def test_sin_corregir_la_inclinacion_el_reposo_del_robot_no_lee_cero():
    """Cuantifica lo que compra la corrección, en vez de solo afirmarlo.

    Con el robot en su postura de reposo —abducción 0, brazos en cruz— un cardán
    ideal lee **+10° de abducción que no existe**, porque la dirección de reposo
    del brazo va sobre el eje de flexión y ese eje está inclinado esos 10°
    respecto al latero-lateral puro.

    Es un sesgo, no un ruido: con él, el operador tendría que subir el brazo 10°
    por encima de la horizontal solo para que el robot se quedara quieto.
    """
    base = torso_basis(HOMBRO_IZQ, HOMBRO_DER, SUPERIOR)
    direccion = visor_a_mediapipe(direccion_del_visor(Side.L, 0.0, 0.0))
    punto = HOMBRO_IZQ + LARGO_BRAZO * direccion

    sin_corregir, _ = arm_angles(HOMBRO_IZQ, punto, base)
    corregido, _ = arm_angles(HOMBRO_IZQ, punto, base, INCLINACION_FLEXION[Side.L])
    assert sin_corregir == pytest.approx(10.0, abs=0.5)
    assert corregido == pytest.approx(0.0, abs=0.2)


def test_el_sesgo_de_la_inclinacion_es_constante_no_depende_de_la_flexion():
    """Y por eso es fácil de pasar por alto: no crece ni oscila, siempre son 10°.

    Un error que varía con la postura se nota moviéndose; uno constante se
    confunde con «el neutro del operador» y se calibra encima, que es peor.
    """
    base = torso_basis(HOMBRO_IZQ, HOMBRO_DER, SUPERIOR)
    for flexion in (0.0, 30.0, 60.0, 90.0):
        direccion = visor_a_mediapipe(direccion_del_visor(Side.L, 0.0, flexion))
        abduccion, _ = arm_angles(
            HOMBRO_IZQ, HOMBRO_IZQ + LARGO_BRAZO * direccion, base
        )
        assert abduccion == pytest.approx(10.0, abs=0.2)


# --------------------------------------------------------------------------
# 2. Invariancia al espejo
# --------------------------------------------------------------------------


def test_la_terna_del_tronco_es_invariante_al_espejo():
    """Espejar la imagen no cambia el significado de ningún vector de la terna.

    Es la propiedad que hace que los cuatro canales no dependan de si la cámara
    va volteada. Se comprueba sobre la terna y no sobre los ángulos porque es
    ahí donde se decidió no usar productos vectoriales: un ``u_ant`` obtenido
    por producto vectorial cambiaría de signo aquí, y esta prueba lo vería.
    """
    def espejar(v):
        return np.array([-v[0], v[1], v[2]])

    directa = torso_basis(HOMBRO_IZQ, HOMBRO_DER, SUPERIOR)
    # Al espejar, el hombro izquierdo físico pasa a llevar la etiqueta contraria,
    # así que se intercambian los papeles además de negar la x.
    espejada = torso_basis(
        espejar(HOMBRO_IZQ), espejar(HOMBRO_DER), espejar(SUPERIOR)
    )

    u_lat_d, u_ant_d, u_sup_d = directa
    u_lat_e, u_ant_e, u_sup_e = espejada

    # u_lat apunta hacia afuera del brazo en los dos casos: su x cambia de signo
    # porque el brazo está al otro lado de la imagen, pero SIGNIFICA lo mismo.
    assert u_lat_e == pytest.approx(espejar(u_lat_d))
    # u_ant y u_sup no dependen de la x en absoluto.
    assert u_ant_e == pytest.approx(u_ant_d)
    assert u_sup_e == pytest.approx(u_sup_d)


@pytest.mark.parametrize("abduccion", [-30.0, 0.0, 45.0])
@pytest.mark.parametrize("flexion", [-45.0, 0.0, 60.0])
def test_espejar_la_imagen_no_cambia_los_angulos(abduccion, flexion):
    """Los mismos ángulos con la cámara volteada y sin voltear.

    Es la mitad geométrica de la protección contra errores de espejo; la otra
    mitad, la del cruce de lados, la cubre ``canal_del_robot``.
    """
    base = torso_basis(HOMBRO_IZQ, HOMBRO_DER, SUPERIOR)
    punto = destino(base, abduccion, flexion, HOMBRO_IZQ)

    def espejar(v):
        return np.array([-v[0], v[1], v[2]])

    base_e = torso_basis(espejar(HOMBRO_IZQ), espejar(HOMBRO_DER), espejar(SUPERIOR))
    estimado = arm_angles(HOMBRO_IZQ, punto, base)
    estimado_e = arm_angles(espejar(HOMBRO_IZQ), espejar(punto), base_e)

    assert estimado[0] == pytest.approx(estimado_e[0], abs=1e-9)
    assert estimado[1] == pytest.approx(estimado_e[1], abs=1e-9)


# --------------------------------------------------------------------------
# 3. El invariante del lado
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "etiqueta, frame_espejado, mirror, esperado",
    [
        # Sin espejo de cámara: la etiqueta de BlazePose ES el lado físico.
        (Side.L, False, True, Side.L),
        (Side.R, False, True, Side.R),
        (Side.L, False, False, Side.R),
        (Side.R, False, False, Side.L),
        # Con espejo de cámara: BlazePose ve la imagen especular del operador,
        # así que sus LEFT_* son el lado físico DERECHO.
        (Side.L, True, True, Side.R),
        (Side.R, True, True, Side.L),
        (Side.L, True, False, Side.L),
        (Side.R, True, False, Side.R),
    ],
)
def test_la_tabla_de_verdad_del_cruce_de_lados(
    etiqueta, frame_espejado, mirror, esperado
):
    """Las ocho combinaciones, enunciadas una por una.

    Está escrita como tabla y no como fórmula a propósito: la fórmula es un XOR
    de tres booleanos y leerla no convence a nadie de que sea la correcta. Las
    ocho filas sí se pueden razonar de una en una.
    """
    assert (
        canal_del_robot(etiqueta, frame_espejado=frame_espejado, mirror=mirror)
        is esperado
    )


def test_el_cruce_de_lados_es_su_propia_inversa():
    """Aplicar el cruce dos veces con la misma política devuelve al origen.

    Impide una implementación que 'funcione' en un lado y no en el otro.
    """
    for esp in (False, True):
        for mir in (False, True):
            for etiqueta in (Side.L, Side.R):
                canal = canal_del_robot(
                    etiqueta, frame_espejado=esp, mirror=mir
                )
                vuelta = canal_del_robot(canal, frame_espejado=esp, mirror=mir)
                assert vuelta is etiqueta


def test_el_espejo_manda_cada_brazo_a_un_canal_distinto():
    """Los dos brazos nunca acaban en el mismo canal.

    Un cruce mal escrito puede colapsar los dos lados en uno y dejar un brazo
    del robot muerto; sin esta prueba se vería solo mirando el visor.
    """
    for esp in (False, True):
        for mir in (False, True):
            canales = {
                canal_del_robot(e, frame_espejado=esp, mirror=mir)
                for e in (Side.L, Side.R)
            }
            assert canales == {Side.L, Side.R}


# --------------------------------------------------------------------------
# 4. Afirmaciones físicas
# --------------------------------------------------------------------------


def test_el_brazo_en_cruz_es_el_CERO_de_la_abduccion(base_izq):
    """El cero es el brazo en horizontal hacia afuera, no colgando (H30).

    Es la afirmación más importante de este archivo y la que costó una sesión
    con el visor delante. Fijarla aquí es lo que impide que alguien vuelva a
    escribir un modelo directo con el brazo colgando —que fue lo que hizo que
    la prueba contra el rig cerrara consigo misma siendo falsa.
    """
    punto = HOMBRO_IZQ + np.array([LARGO_BRAZO, 0.0, 0.0])  # +x = afuera del izq
    abduccion, flexion = arm_angles(HOMBRO_IZQ, punto, base_izq)
    assert abduccion == pytest.approx(0.0, abs=1e-9)
    # Y ahí mismo la flexión no existe: es la singularidad del cardán.
    assert flexion is None


def test_el_brazo_colgando_pide_noventa_grados_que_el_robot_no_tiene(base_izq):
    """El operador con el brazo caído pide −90°, y el robot solo llega a −55°.

    No es un error del estimador: es el mecanismo. El robot **no puede dejar los
    brazos colgando**, y el recorte lo deja en su tope. Conviene tenerlo escrito
    porque es la postura de reposo del operador y, por tanto, la que más va a
    aparecer.
    """
    punto = HOMBRO_IZQ + np.array([0.0, LARGO_BRAZO, 0.0])  # +y es hacia abajo
    abduccion, flexion = arm_angles(HOMBRO_IZQ, punto, base_izq)
    assert abduccion == pytest.approx(-90.0, abs=1e-9)
    assert flexion == pytest.approx(0.0, abs=1e-9)
    assert ARM_ABDUCCION_1D[Side.L][0] > abduccion


def test_bajar_el_brazo_desde_la_horizontal_da_abduccion_negativa(base_izq):
    punto = HOMBRO_IZQ + np.array([LARGO_BRAZO * 0.7, LARGO_BRAZO * 0.7, 0.0])
    abduccion, _ = arm_angles(HOMBRO_IZQ, punto, base_izq)
    assert abduccion == pytest.approx(-45.0, abs=0.5)


def test_subir_el_brazo_desde_la_horizontal_da_abduccion_positiva(base_izq):
    """+30° es todo lo que el robot sube antes de chocar con la cabeza."""
    punto = HOMBRO_IZQ + np.array([LARGO_BRAZO * 0.7, -LARGO_BRAZO * 0.7, 0.0])
    abduccion, _ = arm_angles(HOMBRO_IZQ, punto, base_izq)
    assert abduccion == pytest.approx(45.0, abs=0.5)


def test_el_brazo_al_frente_sale_del_plano_frontal(base_izq):
    """Señalar al frente exige salirse del plano frontal por los dos canales.

    Con el brazo horizontal hacia adelante, la solución del cardán es abducción
    90 y flexión 90: hay que «subir» el brazo y luego rotarlo al frente. Es una
    consecuencia directa de que el reposo esté sobre el eje de flexión, y
    explica por qué el gesto de señalar no es gratis en este mecanismo.
    """
    punto = HOMBRO_IZQ + np.array([0.0, 0.0, -LARGO_BRAZO])  # -z hacia la camara
    abduccion, flexion = arm_angles(HOMBRO_IZQ, punto, base_izq)
    assert abduccion == pytest.approx(90.0, abs=0.5)
    assert flexion == pytest.approx(90.0, abs=0.5)


def test_el_brazo_hacia_atras_invierte_la_flexion(base_izq):
    punto = HOMBRO_IZQ + np.array([0.0, 0.0, LARGO_BRAZO])
    abduccion, flexion = arm_angles(HOMBRO_IZQ, punto, base_izq)
    assert abduccion == pytest.approx(90.0, abs=0.5)
    assert flexion == pytest.approx(-90.0, abs=0.5)


def test_el_sentido_antero_posterior_depende_de_la_abduccion(base_izq):
    """«flexión+ = adelante» NO es cierto: depende del signo de la abducción.

    La componente hacia adelante vale ``sin(abduccion)·sin(flexion)``, así que
    con el brazo por debajo de la horizontal el sentido se invierte. Es lo que
    hace que el comentario del rig del visor —«+θ = adelante en ambos
    brazos»— no pueda ser cierto tal como está escrito, y lo que retracta la
    primera formulación de este hallazgo (H29 -> H30).
    """
    _, u_ant, _ = base_izq
    arriba = destino(base_izq, 20.0, 60.0) - HOMBRO_IZQ
    abajo = destino(base_izq, -50.0, 60.0) - HOMBRO_IZQ
    assert float(np.dot(arriba, u_ant)) > 0.0   # brazo alto: +flexion va adelante
    assert float(np.dot(abajo, u_ant)) < 0.0    # brazo bajo: +flexion va atras


def test_los_dos_ejes_no_se_contaminan_entre_si(base_izq):
    """Mover un canal deja el otro en cero, y al revés.

    Es la comprobación que en el head tracker atrapó la contaminación entre pan
    y tilt. Aquí importa más todavía, porque la abducción es el índice implícito
    de la tabla de límites de la flexión.
    """
    # La abducción se recorre con la flexión fija.
    for grados in (-50.0, -20.0, 25.0, 40.0):
        solo_abd = arm_angles(HOMBRO_IZQ, destino(base_izq, grados, 0.0), base_izq)
        assert solo_abd[0] == pytest.approx(grados, abs=1e-6)
        assert solo_abd[1] == pytest.approx(0.0, abs=1e-6)

    # Y la flexión con la abducción fija. NO se puede partir de abducción 0:
    # ahí está la singularidad del cardán y la flexión no existe, que es
    # precisamente lo que comprueba la prueba del brazo en cruz.
    for grados in (-60.0, -20.0, 25.0, 70.0):
        solo_flex = arm_angles(HOMBRO_IZQ, destino(base_izq, -40.0, grados), base_izq)
        assert solo_flex[0] == pytest.approx(-40.0, abs=1e-6)
        assert solo_flex[1] == pytest.approx(grados, abs=1e-6)


def test_el_brazo_derecho_usa_su_propio_lado(base_der):
    """La abducción del brazo derecho también es positiva hacia afuera.

    El hombro derecho está en -x, así que 'hacia afuera' es -x para él. Si
    ``u_lat`` se construyera con un eje fijo en vez de con el hombro contrario,
    esta prueba saldría con el signo cambiado — que es exactamente el BUG 1 en
    su versión geométrica.
    """
    # -x es hacia afuera para el brazo derecho, +y es hacia abajo: brazo bajado
    # 45° desde la horizontal, o sea abducción -45 en los dos lados.
    punto = HOMBRO_DER + np.array([-LARGO_BRAZO * 0.7, LARGO_BRAZO * 0.7, 0.0])
    abduccion, _ = arm_angles(HOMBRO_DER, punto, base_der)
    assert abduccion == pytest.approx(-45.0, abs=0.5)

    # La postura simétrica del brazo izquierdo tiene que dar el MISMO ángulo.
    espejo = HOMBRO_IZQ + np.array([LARGO_BRAZO * 0.7, LARGO_BRAZO * 0.7, 0.0])
    izquierda, _ = arm_angles(
        HOMBRO_IZQ, espejo, torso_basis(HOMBRO_IZQ, HOMBRO_DER, SUPERIOR)
    )
    assert izquierda == pytest.approx(abduccion, abs=1e-9)


# --------------------------------------------------------------------------
# 5. El robot no tiene rotación axial (H28)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("giro_axial", [15.0, 90.0, -120.0])
def test_rotar_el_brazo_sobre_su_eje_no_mueve_ningun_canal(base_izq, giro_axial):
    """Girar el brazo sobre su propio eje no cambia nada. Y debe ser así.

    Es H28 convertido en prueba. El enunciado original del PROMPT 5 pedía
    estimar la rotación axial del húmero y mandarla al canal ``tw``; el rig dice
    que ese canal es flexión, no torsión, así que hacerlo movería el brazo del
    robot hacia adelante cuando el operador rota el antebrazo.

    Esta prueba fija la lectura correcta: el estimador consume una DIRECCION, y
    una dirección es invariante a la rotación alrededor de sí misma. Si alguien
    vuelve a introducir un canal de torsión, algo se pondrá rojo.
    """
    punto = destino(base_izq, 30.0, 40.0)
    esperado = arm_angles(HOMBRO_IZQ, punto, base_izq)

    eje = (punto - HOMBRO_IZQ) / np.linalg.norm(punto - HOMBRO_IZQ)
    rotado = HOMBRO_IZQ + _rodrigues(eje, giro_axial) @ (punto - HOMBRO_IZQ)
    obtenido = arm_angles(HOMBRO_IZQ, rotado, base_izq)

    assert obtenido[0] == pytest.approx(esperado[0], abs=1e-6)
    assert obtenido[1] == pytest.approx(esperado[1], abs=1e-6)


def test_con_el_brazo_estirado_mano_y_codo_dan_lo_mismo(base_izq):
    """``target`` cambia el PUNTO, no el modelo.

    Con el codo alineado entre hombro y mano —el brazo estirado— las dos
    opciones son el mismo rayo, así que tienen que dar el mismo ángulo. Lo que
    las separa en la práctica no es la geometría sino qué se ve en cuadro y
    cuánto tiembla cada landmark, y eso se mide con el operador, no aquí.
    """
    mano = destino(base_izq, 35.0, -20.0)
    codo = HOMBRO_IZQ + 0.5 * (mano - HOMBRO_IZQ)
    por_mano = arm_angles(HOMBRO_IZQ, mano, base_izq)
    por_codo = arm_angles(HOMBRO_IZQ, codo, base_izq)
    assert por_mano[0] == pytest.approx(por_codo[0], abs=1e-9)
    assert por_mano[1] == pytest.approx(por_codo[1], abs=1e-9)


def test_el_destino_solo_importa_por_su_direccion(base_izq):
    """Alargar o acortar el brazo no cambia los ángulos.

    Es la razón por la que ``target="mano"`` y ``target="codo"`` dan lo mismo
    con el brazo estirado: cambian el punto, no el modelo. Y es también la
    corrección del BUG 3, visible desde otro ángulo — el legacy dividía
    longitudes, así que para él la distancia sí importaba, y por eso reventaba.
    """
    direccion = destino(base_izq, 25.0, -35.0) - HOMBRO_IZQ
    referencia = arm_angles(HOMBRO_IZQ, HOMBRO_IZQ + direccion, base_izq)
    for escala in (0.1, 0.5, 3.0, 50.0):
        obtenido = arm_angles(HOMBRO_IZQ, HOMBRO_IZQ + escala * direccion, base_izq)
        assert obtenido[0] == pytest.approx(referencia[0], abs=1e-9)
        assert obtenido[1] == pytest.approx(referencia[1], abs=1e-9)


# --------------------------------------------------------------------------
# 6. La singularidad del cardán
# --------------------------------------------------------------------------


def test_en_el_reposo_del_robot_la_flexion_se_declara_ausente(base_izq):
    """En la singularidad, la flexión se declara ausente en vez de inventarse.

    Y aquí duele especialmente, porque la singularidad de este cardán **cae en
    la postura de reposo del robot**: brazos en cruz. Comprobado en el visor —
    con ``armR = 0``, barrer ``armRtw`` no mueve el brazo.

    Sin esta guarda, ``atan2(0, -0.0)`` devuelve **180°** —no cero, ni un valor
    ruidoso: 180 exactos, por el signo del cero en coma flotante— y eso recorta
    al tope y da un tirón visible en el robot.
    """
    punto = HOMBRO_IZQ + LARGO_BRAZO * base_izq[0]  # exactamente sobre u_lat
    abduccion, flexion = arm_angles(HOMBRO_IZQ, punto, base_izq)
    assert abduccion == pytest.approx(0.0, abs=1e-6)
    assert flexion is None


def test_fuera_de_la_singularidad_la_flexion_si_existe(base_izq):
    """A 5° de abducción la flexión ya es un número, no None.

    El umbral es estrecho a propósito: el brazo sale de la singularidad en
    cuanto se separa un poco de la horizontal, y el robot tiene 55° de recorrido
    hacia abajo para hacerlo.
    """
    punto = destino(base_izq, 5.0, 0.0)
    _, flexion = arm_angles(HOMBRO_IZQ, punto, base_izq)
    assert flexion is not None
    assert flexion == pytest.approx(0.0, abs=0.5)


# --------------------------------------------------------------------------
# 7. Degeneraciones y valores no finitos (BUG 2 y BUG 3)
# --------------------------------------------------------------------------


def test_un_brazo_de_longitud_cero_se_descarta(base_izq):
    assert arm_angles(HOMBRO_IZQ, HOMBRO_IZQ.copy(), base_izq) is None


@pytest.mark.parametrize("malo", [float("nan"), float("inf"), float("-inf")])
def test_un_landmark_no_finito_se_descarta(base_izq, malo):
    """BUG 2: el legacy comparaba con ``float('nan')``, que nunca es igual a
    nada. Aquí un NaN no produce un ángulo NaN: descarta el brazo."""
    punto = np.array([malo, 0.0, 0.0])
    assert arm_angles(HOMBRO_IZQ, punto, base_izq) is None


def test_una_terna_degenerada_se_descarta():
    """Sin vertical de tronco no hay terna, y sin terna no hay ángulos."""
    assert torso_basis(HOMBRO_IZQ, HOMBRO_DER, np.zeros(3)) is None
    # Hombros coincidentes: no hay dirección lateral.
    assert torso_basis(HOMBRO_IZQ, HOMBRO_IZQ.copy(), SUPERIOR) is None


def test_los_hombros_alineados_con_la_vertical_se_descartan():
    """Si la línea de hombros es paralela al tronco, ``u_lat`` no existe."""
    assert torso_basis(
        np.array([0.0, -1.0, 0.0]), np.array([0.0, 0.0, 0.0]), SUPERIOR
    ) is None


def test_ninguna_distancia_produce_NaN(base_izq):
    """BUG 3: ``arccos(largo_brazo / largo_tronco)`` daba NaN en cuanto la
    muñeca se alejaba del hombro más que la cadera. Aquí no hay cociente de
    longitudes que pueda superar 1."""
    for escala in (1e-6, 1.0, 1e3, 1e6):
        for direccion in (
            np.array([1.0, 0.0, 0.0]),
            np.array([0.0, 1.0, 0.0]),
            np.array([0.0, 0.0, 1.0]),
            np.array([1.0, 1.0, 1.0]),
        ):
            resultado = arm_angles(
                HOMBRO_IZQ, HOMBRO_IZQ + escala * direccion, base_izq
            )
            assert resultado is not None
            assert math.isfinite(resultado[0])
            assert resultado[1] is None or math.isfinite(resultado[1])


# --------------------------------------------------------------------------
# 8. La cadena hasta el robot: ganancias, inversión, filtro y recorte
# --------------------------------------------------------------------------


class RelojFalso:
    """Reloj determinista, para que el One Euro no dependa de la máquina."""

    def __init__(self, paso=1 / 30):
        self.t = 0.0
        self.paso = paso

    def __call__(self):
        self.t += self.paso
        return self.t


@pytest.fixture
def tracker(monkeypatch):
    """Un ArmTracker sin MediaPipe: solo la cadena de mapeo.

    BlazePose tarda segundos en instanciarse y no interviene en nada de lo que
    estas pruebas comprueban.
    """
    import telmeow_teleop.arm_tracker as modulo

    class PoseFalsa:
        def process(self, _frame):
            return None

        def close(self):
            pass

    monkeypatch.setattr(
        modulo.mp.solutions.pose, "Pose", lambda **kwargs: PoseFalsa()
    )
    return modulo.ArmTracker(clock=RelojFalso())


def test_la_ganancia_multiplica_el_gesto_del_operador(tracker):
    tracker.gain_abduccion = 2.0
    salida = tracker.map_to_robot(Side.L, 10.0, 0.0)
    assert salida[0] == pytest.approx(20.0, abs=1e-6)


def test_cada_sentido_usa_su_propia_ganancia(tracker):
    """Abducción y aducción se calibran por separado, como el tilt del P4."""
    tracker.gain_abduccion = 2.0
    tracker.gain_aduccion = 3.0
    assert tracker.map_to_robot(Side.L, 10.0, 0.0)[0] == pytest.approx(20.0, abs=1e-6)
    # Se reinicia el filtro para que la muestra anterior no arrastre.
    tracker = type(tracker)(clock=RelojFalso())
    tracker.gain_aduccion = 3.0
    assert tracker.map_to_robot(Side.L, -10.0, 0.0)[0] == pytest.approx(
        -30.0, abs=1e-6
    )


def test_la_ganancia_es_continua_al_cruzar_el_cero(tracker):
    """Dos ganancias por eje no pueden producir un salto en el origen."""
    tracker.gain_abduccion = 1.2
    tracker.gain_aduccion = 3.0
    arriba = tracker.map_to_robot(Side.L, 1e-9, 0.0)[0]
    abajo = tracker.map_to_robot(Side.L, -1e-9, 0.0)[0]
    assert abs(arriba - abajo) < 1e-6


def test_invertir_cambia_el_sentido(tracker):
    directo = tracker.map_to_robot(Side.L, 20.0, 30.0)
    otro = type(tracker)(clock=RelojFalso(), invert_abduccion=True,
                         invert_flexion=True)
    invertido = otro.map_to_robot(Side.L, 20.0, 30.0)
    assert invertido[0] == pytest.approx(-directo[0], abs=1e-6)
    assert invertido[1] == pytest.approx(-directo[1], abs=1e-6)


def test_la_inversion_se_comparte_entre_los_dos_brazos(tracker):
    """Una sola bandera por eje, no cuatro.

    La convención del robot es simétrica —las tablas de ``limits.py`` usan el
    mismo sentido en los dos lados— así que dos banderas por eje solo
    multiplicarían la superficie del error de signo.
    """
    otro = type(tracker)(clock=RelojFalso(), invert_flexion=True)
    izq = otro.map_to_robot(Side.L, 0.0, 30.0)
    der = otro.map_to_robot(Side.R, 0.0, 30.0)
    assert izq[1] == pytest.approx(der[1], abs=1e-6)
    assert izq[1] < 0


@pytest.mark.parametrize("lado", [Side.L, Side.R])
def test_la_salida_nunca_se_sale_de_los_limites(tracker, lado):
    for abduccion in (-200.0, -80.0, 0.0, 80.0, 200.0):
        for flexion in (-200.0, -45.0, 0.0, 45.0, 200.0):
            salida = tracker.map_to_robot(lado, abduccion, flexion)
            assert salida is not None
            assert FLEXION_RANGE[0] <= salida[1] <= FLEXION_RANGE[1]


@pytest.mark.parametrize("malo", [float("nan"), float("inf")])
def test_un_angulo_no_finito_descarta_el_brazo(tracker, malo):
    """BUG 2, en la frontera con el robot: ``clamp_arm`` lanza ``ValueError``
    ante NaN a propósito, y aquí se convierte en descartar ESE brazo."""
    assert tracker.map_to_robot(Side.L, malo, 0.0) is None
    assert tracker.map_to_robot(Side.L, 0.0, malo) is None


def test_descartar_un_brazo_no_toca_al_otro(tracker):
    """Un brazo fuera de cuadro no puede tumbar al que sí se ve."""
    bueno = tracker.map_to_robot(Side.R, 10.0, 20.0)
    assert tracker.map_to_robot(Side.L, float("nan"), 0.0) is None
    assert tracker.last_clamp.get(Side.R) is not None
    assert tracker.last_clamp.get(Side.L) is None
    assert bueno is not None


def test_no_se_replica_la_tabla_de_limites(tracker):
    """Con flexión +90 la abducción llega a +70, no a +32.

    Es la prueba que §0.5 del plan señala como la que distingue una
    implementación correcta de una que aplique los límites 1D: el brazo flexado
    se despeja del tronco y gana recorrido. Si alguien 'simplificara' el recorte
    a un par de constantes, esta se pone roja.
    """
    salida = tracker.map_to_robot(Side.L, 70.0, 90.0)
    assert salida[0] == pytest.approx(70.0, abs=1e-6)


def test_el_rango_de_la_barra_del_hud_sigue_a_la_flexion(tracker):
    """``lower``/``upper`` de la abducción cambian con la flexión, en vivo."""
    tracker.map_to_robot(Side.L, 0.0, 0.0)
    estrecho = tracker.last_clamp[Side.L].abduccion
    otro = type(tracker)(clock=RelojFalso())
    otro.map_to_robot(Side.L, 0.0, 90.0)
    ancho = otro.last_clamp[Side.L].abduccion
    assert ancho.upper > estrecho.upper


def test_las_ganancias_no_positivas_se_rechazan():
    for clave in ("gain_abduccion", "gain_aduccion", "gain_flexion",
                  "gain_extension"):
        with pytest.raises(ValueError):
            ArmTracker(**{clave: 0.0})
        with pytest.raises(ValueError):
            ArmTracker(**{clave: -1.0})


def test_un_target_invalido_se_rechaza():
    with pytest.raises(ValueError):
        ArmTracker(target="muneca")


def test_una_visibilidad_fuera_de_rango_se_rechaza():
    with pytest.raises(ValueError):
        ArmTracker(min_visibility=1.5)


# --------------------------------------------------------------------------
# 9. Ganancias sugeridas
# --------------------------------------------------------------------------


def test_sin_movimiento_no_hay_ganancia_que_sugerir(tracker):
    assert all(v is None for v in tracker.suggested_gains().values())


def test_la_abduccion_apunta_al_rango_1D_y_no_al_de_la_tabla(tracker):
    """El objetivo de calibración de la abducción es ``ARM_ABDUCCION_1D``.

    El rango de la tabla depende de la flexión, así que dividir por él daría una
    ganancia distinta en cada frame. Es la misma trampa que el P4.3: allí
    ``suggested_gains`` dividía por el rango mecánico del pan en vez de por el
    objetivo de ±50°, y proponía ganancias 1.8x más altas de lo que tocaba.
    """
    tracker._extremos[Side.L]["abduccion"][1] = 16.0
    sugeridas = tracker.suggested_gains()
    assert sugeridas["abduccion"] == pytest.approx(
        ARM_ABDUCCION_1D[Side.L][1] / 16.0, abs=1e-6
    )


def test_la_flexion_apunta_a_su_rango_mecanico(tracker):
    tracker._extremos[Side.L]["flexion"][1] = 45.0
    assert tracker.suggested_gains()["flexion"] == pytest.approx(2.0, abs=1e-6)


def test_una_excursion_minuscula_no_propone_ganancia(tracker):
    """3° de residuo mientras se mueve el otro eje no es una calibración."""
    tracker._extremos[Side.L]["flexion"][1] = 3.0
    assert tracker.suggested_gains()["flexion"] is None


def test_se_propone_la_ganancia_del_brazo_que_mas_lejos_llega(tracker):
    """Una ganancia por canal, no por brazo: el robot es simétrico.

    Se toma la MENOR de las dos propuestas, que es la del brazo que más se
    movió; con la mayor, ese brazo se pasaría de tope.
    """
    tracker._extremos[Side.L]["flexion"][1] = 45.0   # propondría 2.00
    tracker._extremos[Side.R]["flexion"][1] = 60.0   # propondría 1.50
    assert tracker.suggested_gains()["flexion"] == pytest.approx(1.5, abs=1e-6)


def test_los_extremos_se_reinician(tracker):
    tracker._extremos[Side.L]["flexion"][1] = 45.0
    tracker.reset_extremes()
    assert tracker.raw_extremes[Side.L]["flexion"] == (0.0, 0.0)


# --------------------------------------------------------------------------
# 10. Contrato con el resto del paquete
# --------------------------------------------------------------------------


def test_los_indices_de_landmark_coinciden_con_los_de_mediapipe():
    """Los índices literales del módulo contra el enum real de BlazePose.

    Se escriben literales para que las funciones puras se puedan probar sin
    instanciar MediaPipe; esta prueba es lo que impide que se desincronicen si
    alguna versión reordena el enum.
    """
    import mediapipe as mp

    enum = mp.solutions.pose.PoseLandmark
    esperado = {
        Side.L: {
            "hombro": enum.LEFT_SHOULDER, "codo": enum.LEFT_ELBOW,
            "muneca": enum.LEFT_WRIST, "menique": enum.LEFT_PINKY,
            "indice": enum.LEFT_INDEX, "pulgar": enum.LEFT_THUMB,
            "cadera": enum.LEFT_HIP,
        },
        Side.R: {
            "hombro": enum.RIGHT_SHOULDER, "codo": enum.RIGHT_ELBOW,
            "muneca": enum.RIGHT_WRIST, "menique": enum.RIGHT_PINKY,
            "indice": enum.RIGHT_INDEX, "pulgar": enum.RIGHT_THUMB,
            "cadera": enum.RIGHT_HIP,
        },
    }
    for lado, puntos in esperado.items():
        for nombre, landmark in puntos.items():
            assert LANDMARKS[lado][nombre] == landmark.value, f"{lado} {nombre}"


def test_los_puntos_de_la_mano_estan_todos_declarados():
    for lado in (Side.L, Side.R):
        for nombre in PUNTOS_MANO:
            assert nombre in LANDMARKS[lado]


def test_los_canales_son_los_del_contrato_mqtt():
    """Los nombres de campo tienen que ser exactamente los que lee el visor.

    ``telmeow_viewer.html:1041`` itera sobre ``['armL','armLtw','armR','armRtw']``.
    Un nombre distinto no da error: el visor sencillamente ignora el campo y
    conserva el último valor, así que el brazo se queda quieto sin avisar.
    """
    assert CANALES[Side.L] == ("armL", "armLtw")
    assert CANALES[Side.R] == ("armR", "armRtw")


def test_arm_angles_devuelve_una_pareja_no_una_tupla_larga():
    assert ArmAngles().as_tuple == (None, None, None, None)
    assert ArmAngles(armL=1.0).vacio is False
    assert ArmAngles().vacio is True


def test_el_eje_anterior_apunta_hacia_la_camara():
    """En el sistema de MediaPipe, hacia la cámara es -z.

    Si este signo estuviera al revés, la flexión saldría invertida — y es
    precisamente lo que ``invert_flexion`` corrige sin tocar código. La prueba
    fija la intención declarada, no la verdad del mundo: esa se comprueba en el
    visor.
    """
    assert EJE_ANTERIOR[2] < 0
    assert EJE_ANTERIOR[0] == 0 and EJE_ANTERIOR[1] == 0
