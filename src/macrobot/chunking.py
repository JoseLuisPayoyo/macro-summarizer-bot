"""Troceo de la transcripción en bloques por ventanas de tiempo.

Responsabilidad: agrupar los `Segment` de la transcripción en `Chunk` de aproximadamente
`chunk_minutes` minutos cada uno, que son la unidad de trabajo del paso "map" del
pipeline. Se trocea por tiempo (y no por número de tokens) para que cada bloque tenga un
rango temporal citable en el resumen final y para que el coste por vídeo sea predecible:
una charla de 90 minutos con ventanas de 10 minutos son ~9 llamadas al modelo barato.

El corte respeta los límites de segmento (nunca parte una frase por la mitad): se abre
bloque nuevo con el primer segmento que empiece pasada la ventana, aunque el anterior la
desborde. Y la ventana se mide desde el primer segmento del bloque, no desde el segundo 0
del vídeo, para que una charla que arranca en el minuto 5 no desperdicie medio bloque.
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
        return f"{format_timestamp(self.start)} - {format_timestamp(self.end)}"


def format_timestamp(seconds: float) -> str:
    """Formatea segundos como `HH:MM:SS` (trunca, no redondea)."""
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def chunk_segments(segments: list[Segment], chunk_minutes: int) -> list[Chunk]:
    """Agrupa los segmentos en bloques de ~`chunk_minutes` minutos.

    Devuelve los bloques ordenados y numerados desde 0. Una lista de segmentos vacía
    produce una lista de bloques vacía.
    """
    if chunk_minutes <= 0:
        raise ValueError(f"chunk_minutes debe ser positivo, y es {chunk_minutes}")

    window = chunk_minutes * 60
    chunks: list[Chunk] = []
    current: list[Segment] = []

    for segment in segments:
        if current and segment.start - current[0].start >= window:
            chunks.append(_build_chunk(len(chunks), current))
            current = []
        current.append(segment)

    if current:
        chunks.append(_build_chunk(len(chunks), current))

    return chunks


def _build_chunk(index: int, segments: list[Segment]) -> Chunk:
    return Chunk(
        index=index,
        start=segments[0].start,
        end=segments[-1].end,
        text=" ".join(segment.text for segment in segments),
    )
