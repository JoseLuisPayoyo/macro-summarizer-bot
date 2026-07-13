"""Tests de `macrobot.transcript`.

De momento solo fijan el contrato del andamiaje: los stubs existen y todavía no están
implementados. Cuando se implemente el módulo, estos tests se sustituyen por casos reales
(parseo de un VTT de ejemplo, deduplicación de subtítulos automáticos, URLs inválidas).
"""

import pytest

from macrobot.transcript import (
    NoSubtitlesError,
    Segment,
    TranscriptError,
    get_transcript,
    parse_vtt,
)


def test_segment_holds_its_timespan():
    segment = Segment(start=0.0, end=4.5, text="inflation is coming down")
    assert segment.start == 0.0
    assert segment.end == 4.5
    assert segment.text == "inflation is coming down"


def test_no_subtitles_error_is_a_transcript_error():
    assert issubclass(NoSubtitlesError, TranscriptError)


# TODO: sustituir por el parseo de un VTT de ejemplo con solapamiento de cues.
def test_parse_vtt_not_implemented_yet():
    with pytest.raises(NotImplementedError):
        parse_vtt("WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nhello\n")


# TODO: sustituir por un test con yt-dlp mockeado.
def test_get_transcript_not_implemented_yet():
    with pytest.raises(NotImplementedError):
        get_transcript("https://www.youtube.com/watch?v=dQw4w9WgXcQ", ["en"])
