"""Tests de `macrobot.chunking`: troceo de la transcripción por ventanas de tiempo.

Todo es función pura, sin red. La invariante más importante es que al trocear NO se pierde
texto: el paso map se alimenta de estos bloques, así que lo que se caiga aquí no aparece en
el resumen.
"""

from itertools import pairwise

import pytest

from macrobot.chunking import Chunk, chunk_segments, format_timestamp
from macrobot.transcript import Segment

WINDOW = 10  # minutos
WINDOW_SECONDS = WINDOW * 60


def _segment(start: float, text: str, duration: float = 5.0) -> Segment:
    return Segment(start=start, end=start + duration, text=text)


# --------------------------------------------------------------------------------------
# format_timestamp
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0.0, "00:00:00"),
        (1.0, "00:00:01"),
        (61.0, "00:01:01"),
        (600.0, "00:10:00"),
        (3661.0, "01:01:01"),
        (3661.9, "01:01:01"),  # se trunca, no se redondea
        (7325.0, "02:02:05"),
    ],
)
def test_format_timestamp(seconds, expected):
    assert format_timestamp(seconds) == expected


def test_chunk_timespan_is_human_readable():
    chunk = Chunk(index=0, start=0.0, end=600.0, text="...")

    assert chunk.timespan == "00:00:00 - 00:10:00"


# --------------------------------------------------------------------------------------
# chunk_segments: casos borde
# --------------------------------------------------------------------------------------


def test_chunk_segments_of_empty_transcript_returns_no_chunks():
    assert chunk_segments([], chunk_minutes=WINDOW) == []


def test_chunk_segments_of_a_single_segment_returns_one_chunk():
    segments = [_segment(12.0, "hello")]

    chunks = chunk_segments(segments, chunk_minutes=WINDOW)

    assert len(chunks) == 1
    assert chunks[0].index == 0
    assert chunks[0].start == pytest.approx(12.0)
    assert chunks[0].end == pytest.approx(17.0)
    assert chunks[0].text == "hello"


def test_chunk_segments_rejects_a_non_positive_window():
    with pytest.raises(ValueError, match="chunk_minutes"):
        chunk_segments([_segment(0.0, "hello")], chunk_minutes=0)


# --------------------------------------------------------------------------------------
# chunk_segments: agrupación por ventana
# --------------------------------------------------------------------------------------


def test_chunk_segments_groups_segments_within_the_same_window():
    segments = [
        _segment(0.0, "one"),
        _segment(120.0, "two"),
        _segment(599.0, "three"),
    ]

    chunks = chunk_segments(segments, chunk_minutes=WINDOW)

    assert len(chunks) == 1
    assert chunks[0].text == "one two three"


def test_chunk_segments_starts_a_new_chunk_exactly_at_the_window_boundary():
    """Un segmento que arranca justo en el límite abre el bloque siguiente."""
    segments = [
        _segment(0.0, "before"),
        _segment(WINDOW_SECONDS, "at the boundary"),
    ]

    chunks = chunk_segments(segments, chunk_minutes=WINDOW)

    assert len(chunks) == 2
    assert chunks[0].text == "before"
    assert chunks[1].text == "at the boundary"
    assert chunks[1].start == pytest.approx(WINDOW_SECONDS)


def test_chunk_segments_keeps_a_segment_just_before_the_boundary_in_the_current_chunk():
    segments = [
        _segment(0.0, "before"),
        _segment(WINDOW_SECONDS - 0.001, "just before the boundary"),
    ]

    chunks = chunk_segments(segments, chunk_minutes=WINDOW)

    assert len(chunks) == 1


def test_chunk_segments_never_splits_a_segment_that_straddles_the_boundary():
    """El corte respeta los límites de segmento: uno que cruza la frontera no se parte.

    El bloque se queda con el segmento entero y se desborda de la ventana; el siguiente
    bloque empieza en el segmento de después.
    """
    segments = [
        _segment(0.0, "first"),
        _segment(WINDOW_SECONDS - 2.0, "straddles the boundary", duration=10.0),
        _segment(WINDOW_SECONDS + 30.0, "next window"),
    ]

    chunks = chunk_segments(segments, chunk_minutes=WINDOW)

    assert len(chunks) == 2
    assert chunks[0].text == "first straddles the boundary"
    assert chunks[0].end == pytest.approx(WINDOW_SECONDS + 8.0)  # se pasa de la ventana
    assert chunks[1].text == "next window"


def test_chunk_segments_puts_a_segment_longer_than_the_window_in_its_own_chunk():
    segments = [
        _segment(0.0, "a very long segment", duration=WINDOW_SECONDS * 2),
        _segment(WINDOW_SECONDS * 2 + 10.0, "after it"),
    ]

    chunks = chunk_segments(segments, chunk_minutes=WINDOW)

    assert len(chunks) == 2
    assert chunks[0].text == "a very long segment"
    assert chunks[1].text == "after it"


def test_chunk_segments_of_a_long_talk_numbers_the_chunks_in_order():
    # 90 minutos de charla, un segmento por minuto.
    segments = [_segment(minute * 60.0, f"minute {minute}") for minute in range(90)]

    chunks = chunk_segments(segments, chunk_minutes=WINDOW)

    assert len(chunks) == 9
    assert [chunk.index for chunk in chunks] == list(range(9))
    assert chunks[0].start == pytest.approx(0.0)
    assert chunks[1].start == pytest.approx(600.0)
    assert chunks[8].start == pytest.approx(4800.0)


def test_chunk_segments_boundaries_are_measured_from_the_start_of_the_chunk():
    """La ventana se mide desde el primer segmento del bloque, no desde el segundo 0."""
    # La charla empieza en el minuto 5; el primer bloque debe llegar hasta el minuto 15.
    segments = [
        _segment(300.0, "start of the talk"),
        _segment(890.0, "still within ten minutes of the start"),
        _segment(900.0, "a new window opens"),
    ]

    chunks = chunk_segments(segments, chunk_minutes=WINDOW)

    assert len(chunks) == 2
    assert chunks[0].text == "start of the talk still within ten minutes of the start"
    assert chunks[1].text == "a new window opens"


# --------------------------------------------------------------------------------------
# Invariantes
# --------------------------------------------------------------------------------------


def test_chunk_segments_does_not_lose_any_text():
    segments = [_segment(index * 37.0, f"word{index}") for index in range(200)]

    chunks = chunk_segments(segments, chunk_minutes=WINDOW)

    assert " ".join(chunk.text for chunk in chunks) == " ".join(
        segment.text for segment in segments
    )


def test_chunk_segments_covers_the_whole_timeline_without_gaps_or_overlaps():
    segments = [_segment(index * 37.0, f"word{index}") for index in range(200)]

    chunks = chunk_segments(segments, chunk_minutes=WINDOW)

    assert chunks[0].start == pytest.approx(segments[0].start)
    assert chunks[-1].end == pytest.approx(segments[-1].end)
    for previous, current in pairwise(chunks):
        assert previous.end <= current.start
