"""Tests de `macrobot.transcript`: parseo y limpieza de subtítulos VTT.

`parse_vtt` es una función pura y es el corazón testeable del módulo: todos estos tests
trabajan sobre fixtures de VTT en memoria, sin tocar la red.

La descarga con yt-dlp (`fetch_subtitles`, `get_transcript`) sí hace red, así que solo se
cubre con un test marcado como `integration`, que se salta salvo que se pida.
"""

import os
from pathlib import Path

import pytest

from macrobot import transcript
from macrobot.transcript import (
    NoSubtitlesError,
    Segment,
    TranscriptError,
    _pick_subtitle_file,
    get_transcript,
    parse_vtt,
)

FIXTURES = Path(__file__).parent / "fixtures"

# Los dos VTT grandes viven en ficheros y no como literales de Python a propósito: los
# auto-subs de YouTube traen líneas que son un espacio suelto dentro del cue, y eso no
# sobrevive a un literal (ni al formateador). Así el fixture es byte a byte lo que escupe
# yt-dlp.

# Subtítulos AUTOMÁTICOS: ventana rodante. Cada cue repite lo que ya se veía en pantalla y
# añade una o dos palabras al final; entre medias aparecen cues "de asiento", idénticos al
# anterior y de ~10 ms. El texto viene salpicado de marcas de tiempo inline y etiquetas
# <c>. Este es el patrón que hay que deduplicar.
AUTO_SUBS_VTT = (FIXTURES / "auto_subs_rolling.vtt").read_text(encoding="utf-8")

# Subtítulos MANUALES: cues normales, sin solapamiento, con puntuación y mayúsculas.
MANUAL_SUBS_VTT = (FIXTURES / "manual_subs.vtt").read_text(encoding="utf-8")


def _joined(segments: list[Segment]) -> str:
    return " ".join(segment.text for segment in segments)


# --------------------------------------------------------------------------------------
# Deduplicación de los subtítulos automáticos (el caso clave)
# --------------------------------------------------------------------------------------


def test_parse_vtt_deduplicates_youtube_rolling_captions():
    """La ventana rodante se reconstruye como texto limpio, continuo y sin repeticiones."""
    segments = parse_vtt(AUTO_SUBS_VTT)

    assert _joined(segments) == (
        "so the first thing I want to say is that inflation is falling faster than the Fed expected"
    )


def test_parse_vtt_drops_the_settling_cues_of_auto_subs():
    """Los cues idénticos al anterior no generan segmento."""
    segments = parse_vtt(AUTO_SUBS_VTT)

    # 4 cues aportan texto nuevo; los otros 3 son repeticiones y se descartan.
    assert [segment.text for segment in segments] == [
        "so the first thing",
        "I want to say",
        "is that inflation is falling",
        "faster than the Fed expected",
    ]


def test_parse_vtt_keeps_the_timing_of_the_cue_that_adds_the_text():
    """Cada segmento conserva la ventana temporal del cue que aportó ese texto."""
    segments = parse_vtt(AUTO_SUBS_VTT)

    assert segments[0].start == pytest.approx(0.030)
    assert segments[1].start == pytest.approx(3.130)
    assert segments[2].start == pytest.approx(6.410)
    assert segments[3].start == pytest.approx(9.910)
    assert segments[3].end == pytest.approx(13.200)


def test_parse_vtt_returns_segments_in_chronological_order():
    segments = parse_vtt(AUTO_SUBS_VTT)

    starts = [segment.start for segment in segments]
    assert starts == sorted(starts)


def test_parse_vtt_does_not_lose_words_when_a_cue_scrolls_out_of_view():
    """Al pasar de página el solapamiento es parcial: ni se duplica ni se pierde nada."""
    # La ventana muestra dos líneas: al pasar de página, la línea 2 del cue anterior pasa a
    # ser la línea 1 del siguiente. El solapamiento no es ni todo el cue anterior ni todo el
    # nuevo, así que hay que calcularlo palabra a palabra.
    vtt = """WEBVTT

00:00:00.000 --> 00:00:02.000
alpha beta
gamma delta

00:00:02.000 --> 00:00:04.000
gamma delta
epsilon zeta
"""
    segments = parse_vtt(vtt)

    assert _joined(segments) == "alpha beta gamma delta epsilon zeta"


def test_parse_vtt_keeps_a_repeated_word_that_is_not_a_rolling_overlap():
    """Un solapamiento de UNA sola palabra es coincidencia, no ventana rodante: no se toca."""
    # El ponente dijo "growth" dos veces seguidas, a caballo entre dos cues. Si el
    # deduplicador se fiara de un solapamiento de una palabra, se comería la segunda.
    vtt = """WEBVTT

00:00:00.000 --> 00:00:02.000
that is what I call growth

00:00:02.000 --> 00:00:04.000
growth is what matters here
"""
    segments = parse_vtt(vtt)

    assert _joined(segments) == "that is what I call growth growth is what matters here"


# --------------------------------------------------------------------------------------
# Subtítulos manuales y limpieza
# --------------------------------------------------------------------------------------


def test_parse_vtt_reads_manual_subtitles():
    segments = parse_vtt(MANUAL_SUBS_VTT)

    assert [segment.text for segment in segments] == [
        "Welcome to the macro outlook for 2026.",
        "Inflation has come down to 2.4%, but the labour market is still tight.",
        "The Fed will cut rates in June.",
    ]


def test_parse_vtt_parses_timestamps_as_seconds():
    segments = parse_vtt(MANUAL_SUBS_VTT)

    assert segments[1].start == pytest.approx(3.5)
    assert segments[1].end == pytest.approx(8.25)


def test_parse_vtt_discards_headers_notes_and_cue_numbers():
    segments = parse_vtt(MANUAL_SUBS_VTT)
    text = _joined(segments)

    assert "WEBVTT" not in text
    assert "NOTE" not in text
    assert "no debe aparecer" not in text
    assert "Kind" not in text
    assert "Language" not in text


def test_parse_vtt_discards_cue_settings():
    segments = parse_vtt(MANUAL_SUBS_VTT)
    text = _joined(segments)

    assert "align:start" not in text
    assert "position:0%" not in text


def test_parse_vtt_strips_inline_timestamps_and_style_tags():
    vtt = """WEBVTT

00:00:01.000 --> 00:00:04.000
the<00:00:01.500><c> ECB</c><00:00:02.000><c.colorE5E5E5> held</c> rates
"""
    segments = parse_vtt(vtt)

    assert [segment.text for segment in segments] == ["the ECB held rates"]


def test_parse_vtt_unescapes_html_entities():
    vtt = """WEBVTT

00:00:01.000 --> 00:00:04.000
growth &amp; inflation &gt; 2%&nbsp;this year
"""
    segments = parse_vtt(vtt)

    assert [segment.text for segment in segments] == ["growth & inflation > 2% this year"]


# --------------------------------------------------------------------------------------
# Casos borde
# --------------------------------------------------------------------------------------


def test_parse_vtt_of_empty_string_returns_no_segments():
    assert parse_vtt("") == []


def test_parse_vtt_of_header_only_returns_no_segments():
    assert parse_vtt("WEBVTT\nKind: captions\nLanguage: en\n\n") == []


def test_parse_vtt_skips_cues_without_text():
    vtt = """WEBVTT

00:00:00.000 --> 00:00:02.000

00:00:02.000 --> 00:00:04.000


00:00:04.000 --> 00:00:06.000
Real text here.
"""
    segments = parse_vtt(vtt)

    assert [segment.text for segment in segments] == ["Real text here."]
    assert segments[0].start == pytest.approx(4.0)


def test_parse_vtt_accepts_short_mm_ss_timestamps():
    vtt = """WEBVTT

01:02.500 --> 01:05.000
short form timestamps
"""
    segments = parse_vtt(vtt)

    assert segments[0].start == pytest.approx(62.5)
    assert segments[0].end == pytest.approx(65.0)


def test_parse_vtt_handles_crlf_line_endings():
    vtt = "WEBVTT\r\n\r\n00:00:00.000 --> 00:00:02.000\r\nhello there\r\n"
    segments = parse_vtt(vtt)

    assert [segment.text for segment in segments] == ["hello there"]


def test_parse_vtt_ignores_a_cue_with_a_malformed_timestamp():
    vtt = """WEBVTT

not a timestamp --> neither is this
ignored text

00:00:02.000 --> 00:00:04.000
kept text
"""
    segments = parse_vtt(vtt)

    assert [segment.text for segment in segments] == ["kept text"]


# --------------------------------------------------------------------------------------
# Contrato de errores
# --------------------------------------------------------------------------------------


def test_no_subtitles_error_is_a_transcript_error():
    # `pipeline` distingue este caso para caer al plan B (Whisper), así que la jerarquía
    # de excepciones es parte del contrato del módulo.
    assert issubclass(NoSubtitlesError, TranscriptError)


def test_get_transcript_raises_no_subtitles_when_the_subtitles_have_no_text(monkeypatch):
    """Unos subtítulos vacíos valen tanto como no tenerlos: hay que caer al plan B."""
    monkeypatch.setattr(transcript, "fetch_subtitles", lambda url, langs: "WEBVTT\n\n")

    with pytest.raises(NoSubtitlesError):
        get_transcript("https://youtu.be/dQw4w9WgXcQ", ["en"])


def test_get_transcript_parses_what_yt_dlp_downloaded(monkeypatch):
    """`get_transcript` es descarga + parseo: aquí se comprueba el cableado, sin red."""
    monkeypatch.setattr(transcript, "fetch_subtitles", lambda url, langs: AUTO_SUBS_VTT)

    segments = get_transcript("https://youtu.be/dQw4w9WgXcQ", ["en"])

    assert _joined(segments).startswith("so the first thing I want to say")


# --------------------------------------------------------------------------------------
# Elección del fichero descargado (toca disco, no red)
# --------------------------------------------------------------------------------------


def test_pick_subtitle_file_follows_the_language_preference_order(tmp_path):
    (tmp_path / "vid.es.vtt").write_text("WEBVTT", encoding="utf-8")
    (tmp_path / "vid.en.vtt").write_text("WEBVTT", encoding="utf-8")

    chosen = _pick_subtitle_file(tmp_path, "vid", ["en", "es"])

    assert chosen is not None
    assert chosen.name == "vid.en.vtt"


def test_pick_subtitle_file_accepts_a_variant_of_the_requested_language(tmp_path):
    # yt-dlp puede resolver "en" a "en-US" o "en-orig"; el fichero sirve igual.
    (tmp_path / "vid.en-US.vtt").write_text("WEBVTT", encoding="utf-8")

    chosen = _pick_subtitle_file(tmp_path, "vid", ["en"])

    assert chosen is not None
    assert chosen.name == "vid.en-US.vtt"


def test_pick_subtitle_file_returns_none_when_yt_dlp_downloaded_nothing(tmp_path):
    assert _pick_subtitle_file(tmp_path, "vid", ["en"]) is None


# --------------------------------------------------------------------------------------
# Integración (hace red: se salta por defecto)
# --------------------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("MACROBOT_INTEGRATION") != "1",
    reason="hace red; ejecútalo con MACROBOT_INTEGRATION=1",
)
def test_get_transcript_downloads_real_subtitles():
    segments = get_transcript("https://www.youtube.com/watch?v=dQw4w9WgXcQ", ["en"])

    assert segments
    assert all(segment.text for segment in segments)
