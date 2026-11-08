"""Las órdenes del operador: qué existe, qué tecla las pide y cómo llegan del
navegador (P10).

Una orden es algo que el operador pide —calibrar, fijar una expresión, pausar,
pasar de página, abrir un cuento, terminar—. Llega por dos vías:

- una **tecla en la ventana de OpenCV** (``ORDENES_DE_TECLA``), como en el P7;
- un **botón o una tecla en el navegador** (modo narración del visor), que la
  manda en JSON a ``POST /api/orden`` (``orden_desde_json``).

Las dos acaban en ``main.ejecutar_orden``, en el hilo del bucle: hacen
exactamente lo mismo, y las pruebas lo comprueban con lo que sale por MQTT.

Solo biblioteca estándar, a propósito: el servidor de la interfaz valida las
órdenes sin cargar MediaPipe ni TensorFlow.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import VIEWER_EMOTIONS

__all__ = [
    "Orden",
    "ORDENES",
    "ORDENES_DE_TECLA",
    "orden_desde_json",
    "TECLA_SALIR",
    "TECLA_CALIBRAR",
    "TECLA_AUTOMATICO",
    "TECLA_PAUSA",
    "TECLA_PAGINA_ANTERIOR",
    "TECLA_PAGINA_SIGUIENTE",
    "TECLA_ETIQUETA",
]

TECLA_SALIR = ord("q")
TECLA_CALIBRAR = ord("c")
TECLA_AUTOMATICO = ord("0")
TECLA_PAUSA = ord("k")
TECLA_PAGINA_ANTERIOR = ord("j")
TECLA_PAGINA_SIGUIENTE = ord("l")
TECLA_ETIQUETA = ord(" ")


@dataclass(frozen=True)
class Orden:
    """Algo que el operador pide.

    ``valor`` es la expresión de ``expresion`` y el nombre del archivo de
    ``historia``. ``origen`` queda en la grabación: ``tecla`` o ``navegador``.
    """

    nombre: str
    valor: str | None = None
    origen: str = "tecla"

    def para_registro(self) -> str:
        """Cómo queda en la columna ``orden``: ``nombre=valor@origen``."""
        valor = f"={self.valor}" if self.valor is not None else ""
        return f"{self.nombre}{valor}@{self.origen}"


#: Las órdenes que existen. Lo que no esté aquí no se ejecuta.
ORDENES = ("salir", "etiqueta", "calibrar", "expresion", "automatico", "pausa",
           "pagina_anterior", "pagina_siguiente", "historia")

#: La tecla de cada orden en la ventana de OpenCV. Las cifras 1..6 son las
#: expresiones en el orden de los botones del visor (``VIEWER_EMOTIONS``). El
#: modo narración del visor lleva la misma tabla en JavaScript, y una prueba
#: exige que coincidan.
ORDENES_DE_TECLA: dict[int, Orden] = {
    TECLA_SALIR: Orden("salir"),
    TECLA_ETIQUETA: Orden("etiqueta"),
    TECLA_CALIBRAR: Orden("calibrar"),
    **{ord(str(i + 1)): Orden("expresion", e) for i, e in enumerate(VIEWER_EMOTIONS)},
    TECLA_AUTOMATICO: Orden("automatico"),
    TECLA_PAUSA: Orden("pausa"),
    TECLA_PAGINA_ANTERIOR: Orden("pagina_anterior"),
    TECLA_PAGINA_SIGUIENTE: Orden("pagina_siguiente"),
}

#: Largo máximo del nombre de un cuento que se acepta de la página.
_MAX_VALOR = 300


def orden_desde_json(datos: object) -> Orden:
    """La orden que pide el navegador: ``{"orden": nombre, "valor": ...}``.

    Lanza ``ValueError`` con el motivo si no es una orden válida: nombre fuera
    de ``ORDENES``, una expresión que el visor no tiene, un cuento sin nombre o
    un valor donde no lo lleva. Que el cuento exista lo decide después la
    biblioteca, que solo acepta nombres de su lista.
    """
    if not isinstance(datos, dict):
        raise ValueError("se esperaba un objeto JSON")
    nombre = datos.get("orden")
    valor = datos.get("valor")
    if nombre not in ORDENES:
        raise ValueError(f"orden desconocida: {nombre!r}")
    if nombre == "expresion":
        if valor not in VIEWER_EMOTIONS:
            raise ValueError(f"expresion desconocida: {valor!r}")
    elif nombre == "historia":
        if not isinstance(valor, str) or not valor or len(valor) > _MAX_VALOR:
            raise ValueError("la orden historia necesita el nombre del cuento")
    elif valor is not None:
        raise ValueError(f"la orden {nombre} no lleva valor")
    return Orden(nombre, valor, origen="navegador")
