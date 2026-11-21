"""La voz del narrador hacia el robot, y la de los niños de vuelta: el lado de la PC.

``main.py --interfaz --audio [IP_DEL_ROBOT]`` (o ``control_panel --audio``) lo
arranca junto con la interfaz. Abre el micrófono y los auriculares de la PC,
manda el micrófono al robot y reproduce lo que llega del micrófono del robot.
El otro lado es ``raspberry/robot_audio.py``, y el núcleo es el mismo: este
módulo solo lo monta con la tarjeta de sonido de la PC y le da a la interfaz
una línea de estado.

- La PC escucha en el puerto UDP 8003 (el cortafuegos de Windows tiene que
  dejarlo entrar, como el 1883 del broker) y responde a la dirección de la que
  le llega el sonido del robot.
- Con la IP del robot, solo acepta sonido de esa máquina: nadie más de la red
  puede llevarse la voz del narrador. Sin ella acepta al primero que mande.
- **Con auriculares.** Si el narrador oye a los niños por los altavoces de la
  PC, su micrófono los recoge y los niños se oyen a sí mismos con retraso. El
  cancelador de eco también corre aquí y lo atenúa, pero con micrófono y
  altavoz de tarjetas distintas sus relojes se desfasan y no lo quita del
  todo. Con auriculares no hay eco que quitar.
- No graba nada: el sonido solo se retransmite.
"""

from __future__ import annotations

from .config import PROJECT_ROOT

try:
    from raspberry import robot_audio as ra
except ImportError:  # lanzado desde otra carpeta: la raíz del proyecto no está en sys.path
    import sys

    sys.path.insert(0, str(PROJECT_ROOT))
    from raspberry import robot_audio as ra

__all__ = ["AudioOperador", "resumen_audio", "SIN_SONIDO_S"]

#: Sin sonido del robot en este tiempo, la interfaz lo dice.
SIN_SONIDO_S = 2.0


def resumen_audio(*, abierto: bool, error: str = "", edad_s: float | None) -> dict:
    """El estado del audio en una línea, para la página. ``clave``: ``ok``,
    ``aviso`` (sin sonido del robot) o ``error`` (sin micrófono)."""
    if not abierto:
        texto = "Audio: no puedo abrir tu micrófono o tus auriculares"
        return {"clave": "error", "texto": texto + (f" ({error[:60]})" if error else "")}
    if edad_s is None:
        return {"clave": "aviso", "texto": "Audio: tu micrófono va abierto; aún no llega el robot"}
    if edad_s > SIN_SONIDO_S:
        return {"clave": "aviso", "texto": f"Audio: sin sonido del robot hace {edad_s:.0f} s"}
    return {"clave": "ok", "texto": "Audio: hablando con el robot"}


class AudioOperador:
    """El audio de la PC, con la misma vida que la interfaz: ``arrancar``,
    ``parar`` y ``resumen``. ``dispositivo``: la tarjeta (las pruebas le dan
    una sintética); por defecto, el micrófono y la salida de Windows."""

    def __init__(self, *, puerto: int = ra.PUERTO, robot: str | None = None,
                 cancelar: bool = True, dispositivo=None, host_local: str = "0.0.0.0") -> None:
        self.colchon = ra.Colchon()
        self.enlace = ra.EnlaceAudio(self.colchon, puerto_local=puerto, solo_de=robot or None,
                                     host_local=host_local)
        self.nucleo = ra.Nucleo(self.enlace, ra.Cancelador() if cancelar else None)
        self.supervisor = ra.Supervisor(self.nucleo, dispositivo or ra.DispositivoReal())

    @property
    def puerto(self) -> int:
        return self.enlace.puerto

    def arrancar(self) -> None:
        self.enlace.empezar()
        self.supervisor.empezar()

    def parar(self) -> None:
        self.supervisor.cerrar()
        self.enlace.cerrar()

    def resumen(self) -> dict:
        return resumen_audio(abierto=self.supervisor.abierto, error=self.supervisor.error,
                             edad_s=self.enlace.edad_s())


class AudioDemo:
    """El audio de la demo de la interfaz: sin micrófono ni robot."""

    def arrancar(self) -> None: ...

    def parar(self) -> None: ...

    def resumen(self) -> dict:
        return resumen_audio(abierto=True, edad_s=0.0)
