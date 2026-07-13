"""Tests de `macrobot.chunking`.

De momento solo fijan el contrato del andamiaje. Cuando se implemente el módulo, estos
tests se sustituyen por casos reales: ventanas exactas, transcripción vacía, un único
segmento más largo que la ventana, y que ningún segmento se parta entre dos bloques.
"""

import pytest

from macrobot.chunking import Chunk, chunk_segments, format_timestamp
from macrobot.transcript import Segment


def test_chunk_holds_its_fields():
    chunk = Chunk(index=0, start=0.0, end=600.0, text="...")
    assert chunk.index == 0
    assert chunk.start == 0.0
    assert chunk.end == 600.0


# TODO: sustituir por casos reales (0s, 61s, 3661s).
def test_format_timestamp_not_implemented_yet():
    with pytest.raises(NotImplementedError):
        format_timestamp(3661.0)


# TODO: sustituir por el troceo de una transcripción sintética de 90 minutos.
def test_chunk_segments_not_implemented_yet():
    segments = [Segment(start=0.0, end=5.0, text="hello")]
    with pytest.raises(NotImplementedError):
        chunk_segments(segments, chunk_minutes=10)
