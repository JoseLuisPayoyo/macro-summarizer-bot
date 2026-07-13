"""Troceo de la transcripción en bloques por ventanas de tiempo.

Responsabilidad: agrupar los `Segment` de la transcripción en `Chunk` de aproximadamente
`chunk_minutes` minutos cada uno, que son la unidad de trabajo del paso "map" del
pipeline. Se trocea por tiempo (y no por número de tokens) para que cada bloque tenga un
rango temporal citable en el resumen final y para que el coste por vídeo sea predecible:
una charla de 90 minutos con ventanas de 10 minutos son ~9 llamadas al modelo barato.

El corte respeta los límites de segmento (nunca parte una frase por la mitad) y se cierra
el bloque en el primer segmento que supere el final de la ventana.
"""

from dataclasses import dataclass

from macrobot.transcript import Segment


@dataclass(frozen=True, slots=True)
class Chunk:
    """Un bloque de transcripción listo para el paso map."""

    index: int
    start: float
    end: float
    text: str

    @property
    def timespan(self) -> str:
        """Rango temporal legible del bloque, p. ej. `"00:10:00 - 00:20:00"`."""
        raise NotImplementedError


def format_timestamp(seconds: float) -> str:
    """Formatea segundos como `HH:MM:SS`."""
    raise NotImplementedError


def chunk_segments(segments: list[Segment], chunk_minutes: int) -> list[Chunk]:
    """Agrupa los segmentos en bloques de ~`chunk_minutes` minutos.

    Devuelve los bloques ordenados y numerados desde 0. Una lista de segmentos vacía
    produce una lista de bloques vacía.
    """
    raise NotImplementedError
