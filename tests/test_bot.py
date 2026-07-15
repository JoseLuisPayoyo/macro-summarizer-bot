"""Tests de la lógica pura de `macrobot.bot`.

Todo lo que aquí se prueba son funciones sin SDK real de Telegram, sin red y sin mocks:
el troceo del informe al límite de 4096, la traducción de excepciones a mensajes de
usuario, el pie con el coste estimado, el parseo de una extracción del map en sus
apartados `###`, las vistas compacta/completa de cada bloque y el toggle del botón
inline (que solo construye objetos de datos del SDK, sin red). La integración con
python-telegram-bot (handlers, polling) es una capa fina que no se cubre con unitarios.
"""

import pytest

from macrobot.bot import (
    COLLAPSE_BUTTON_LABEL,
    COMPACT_BLOCK_SECTIONS,
    EXPAND_BUTTON_LABEL,
    FULL_BLOCK_SECTIONS,
    LLM_ERROR_MESSAGE,
    NO_SUBTITLES_MESSAGE,
    TELEGRAM_MAX_CHARS,
    UNEXPECTED_ERROR_MESSAGE,
    VIDEO_ERROR_MESSAGE,
    block_keyboard,
    build_footer,
    clip_message,
    compact_block_view,
    error_message,
    estimate_cost,
    full_block_view,
    parse_block_callback,
    parse_extraction,
    split_message,
    store_block_views,
)
from macrobot.config import Settings
from macrobot.llm import LLMError, LLMRateLimitError, TokenUsage
from macrobot.pipeline import BlockSummary, SummaryResult
from macrobot.prompts import MAP_SECTION_TITLES
from macrobot.transcript import NoSubtitlesError, TranscriptError, find_youtube_url


def make_settings(**overrides) -> Settings:
    values = {"telegram_token": "tg", "openrouter_api_key": "or"}
    values.update(overrides)
    return Settings(**values, _env_file=None)


def make_result(**overrides) -> SummaryResult:
    values = {
        "summary": "el informe",
        "blocks": [],
        "chunk_count": 9,
        "map_model": "barato/mapa",
        "reduce_model": "bueno/informe",
        "map_usage": TokenUsage(90_000, 9_000, 99_000),
        "reduce_usage": TokenUsage(10_000, 2_000, 12_000),
        "total_usage": TokenUsage(100_000, 11_000, 111_000),
    }
    values.update(overrides)
    return SummaryResult(**values)


# Una extracción de ejemplo con TODOS los apartados del esquema de MAP_SYSTEM.
FULL_EXTRACTION = """\
[00:10:00 - 00:20:00]

### Tema del bloque
La inflación subyacente en 2026.

### Tesis / ideas centrales
- La Fed va tarde, según el ponente.

### Argumentos y razonamiento
- Los alquileres entran con retraso de un año en el IPC.

### Datos y cifras citados
- IPC subyacente 3,1 % interanual (mayo 2026).

### Predicciones / escenarios
- Si el IPC baja de 3 %, recorte en septiembre.

### Activos / mercados / tickers
- Bonos del Tesoro a 10 años: alcista.

### Política monetaria / bancos centrales
- La Fed mantiene tipos en 4,25-4,50 %.

### Citas textuales destacadas
- "El último kilómetro es el más caro."

### Términos y conceptos clave
- Efecto base: distorsión interanual por el año anterior.
"""


def make_block(
    index: int = 0,
    timespan: str = "00:10:00 - 00:20:00",
    extraction: str = FULL_EXTRACTION,
) -> BlockSummary:
    return BlockSummary(index=index, timespan=timespan, extraction=extraction)


# --------------------------------------------------------------------------------------
# Extracción de la URL de un mensaje típico de Telegram
# --------------------------------------------------------------------------------------


def test_a_typical_telegram_message_yields_its_url():
    text = "Resúmeme esto porfa 🙏\nhttps://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s\nGracias!"

    assert find_youtube_url(text) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s"


def test_a_message_without_a_url_yields_none():
    assert find_youtube_url("hola bot, ¿qué sabes hacer?") is None


# --------------------------------------------------------------------------------------
# Troceo del informe al límite de Telegram
# --------------------------------------------------------------------------------------


def test_split_message_leaves_a_short_text_untouched():
    assert split_message("informe corto") == ["informe corto"]


def test_split_message_of_an_empty_text_sends_nothing():
    assert split_message("") == []


def test_split_message_respects_the_telegram_limit_by_default():
    text = "\n\n".join(f"Párrafo {index}: " + "palabra " * 120 for index in range(30))

    parts = split_message(text)

    assert len(parts) > 1
    assert all(len(part) <= TELEGRAM_MAX_CHARS for part in parts)


def test_split_message_concatenation_reconstructs_the_original():
    text = "\n\n".join(f"Párrafo {index}: " + "dato " * 150 for index in range(20))

    parts = split_message(text)

    assert "".join(parts) == text


def test_split_message_never_cuts_a_word_in_half():
    text = " ".join(f"palabra{index:04d}" for index in range(3000))

    parts = split_message(text)

    assert len(parts) > 1
    # Cada trozo (salvo el último) termina en el separador por el que se cortó.
    assert all(part.endswith((" ", "\n")) for part in parts[:-1])
    assert "".join(parts) == text


def test_split_message_prefers_paragraph_boundaries():
    paragraph = "x" * 90
    text = f"{paragraph}\n\n{paragraph}\n\n{paragraph}"

    parts = split_message(text, limit=100)

    assert parts[0] == paragraph + "\n\n"
    assert "".join(parts) == text


def test_split_message_hard_cuts_a_word_longer_than_the_limit():
    # Sin ningún separador no hay corte bueno posible: se trocea duro y no se cuelga.
    parts = split_message("x" * 50, limit=10)

    assert parts == ["x" * 10] * 5


def test_split_message_does_not_cut_inside_a_code_block():
    text = "p" * 20 + "\n\n" + "s" * 10 + "\n\n" + "```\ncode\n\nmore\n```" + "\n\n" + "z" * 30

    # Con límite 45, el corte ingenuo (último salto de párrafo de la ventana) caería
    # DENTRO del bloque de código; el troceo debe retroceder al corte anterior.
    parts = split_message(text, limit=45)

    assert "".join(parts) == text
    assert all(len(part) <= 45 for part in parts)
    assert all(part.count("```") % 2 == 0 for part in parts)


# --------------------------------------------------------------------------------------
# Parseo de una extracción del map en sus apartados ###
# --------------------------------------------------------------------------------------


def test_parse_extraction_finds_every_section_of_a_complete_extraction():
    sections = parse_extraction(FULL_EXTRACTION)

    assert set(sections) == set(MAP_SECTION_TITLES)
    assert sections["Tema del bloque"] == "La inflación subyacente en 2026."
    assert "IPC subyacente 3,1 % interanual" in sections["Datos y cifras citados"]


def test_parse_extraction_ignores_the_leading_timespan_line():
    sections = parse_extraction(FULL_EXTRACTION)

    assert all("[00:10:00 - 00:20:00]" not in content for content in sections.values())


def test_parse_extraction_treats_missing_sections_as_absent_without_failing():
    partial = "### Tema del bloque\nEl petróleo.\n\n### Datos y cifras citados\n- Brent a 92 $.\n"

    sections = parse_extraction(partial)

    assert sections == {
        "Tema del bloque": "El petróleo.",
        "Datos y cifras citados": "- Brent a 92 $.",
    }


def test_parse_extraction_drops_sections_the_model_left_empty():
    text = '### Tema del bloque\n\n### Citas textuales destacadas\n- "una cita"\n'

    sections = parse_extraction(text)

    assert "Tema del bloque" not in sections
    assert sections["Citas textuales destacadas"] == '- "una cita"'


def test_parse_extraction_of_prose_without_headings_yields_no_sections():
    assert parse_extraction("el modelo se saltó el esquema y respondió en prosa") == {}


# --------------------------------------------------------------------------------------
# Vistas compacta y completa de un bloque
# --------------------------------------------------------------------------------------


def test_the_compact_view_shows_header_topic_and_the_compact_sections():
    view = compact_block_view(make_block(index=2))

    assert "Bloque 3" in view  # índice 0-based, numeración 1-based de cara al usuario
    assert "00:10:00 - 00:20:00" in view
    assert "La inflación subyacente en 2026." in view
    for title in COMPACT_BLOCK_SECTIONS:
        assert title in view
    assert "La Fed va tarde" in view  # el contenido de las secciones, no solo su título


def test_the_compact_view_leaves_the_detail_sections_out():
    view = compact_block_view(make_block())

    for title in set(FULL_BLOCK_SECTIONS) - set(COMPACT_BLOCK_SECTIONS):
        assert title not in view


def test_the_full_view_shows_every_section_present_in_the_extraction():
    view = full_block_view(make_block())

    for title in FULL_BLOCK_SECTIONS:
        assert title in view
    assert "El último kilómetro es el más caro." in view
    assert "La inflación subyacente en 2026." in view  # el tema sigue en el encabezado


def test_the_views_skip_sections_missing_from_the_extraction():
    block = make_block(
        extraction='### Tema del bloque\nSolo tema.\n\n### Citas textuales destacadas\n- "c"\n'
    )

    compact = compact_block_view(block)
    full = full_block_view(block)

    assert "Solo tema." in compact
    for title in COMPACT_BLOCK_SECTIONS:
        assert title not in compact  # ausentes en la extracción: ni título ni hueco
    assert "Citas textuales destacadas" in full


def test_clip_message_leaves_short_texts_alone_and_clips_long_ones_within_the_limit():
    assert clip_message("texto corto") == "texto corto"

    clipped = clip_message("palabra " * 1000, limit=100)

    assert len(clipped) <= 100
    assert "recortado" in clipped  # el recorte se declara, no se disimula


# --------------------------------------------------------------------------------------
# El toggle expandir/contraer: botón y callback_data en ambos sentidos
# --------------------------------------------------------------------------------------


def test_the_collapsed_view_button_offers_the_full_detail():
    button = block_keyboard("req12345", 3, expanded=False).inline_keyboard[0][0]

    assert button.text == EXPAND_BUTTON_LABEL
    assert button.callback_data == "blk:req12345:3:full"


def test_the_expanded_view_button_offers_going_back_to_compact():
    button = block_keyboard("req12345", 3, expanded=True).inline_keyboard[0][0]

    assert button.text == COLLAPSE_BUTTON_LABEL
    assert button.callback_data == "blk:req12345:3:compact"


@pytest.mark.parametrize("expanded", [False, True])
def test_the_callback_data_round_trips_and_asks_for_the_opposite_view(expanded):
    data = block_keyboard("abc", 7, expanded=expanded).inline_keyboard[0][0].callback_data

    request_id, index, wants_full = parse_block_callback(data)

    assert (request_id, index) == ("abc", 7)
    assert wants_full is not expanded  # el botón siempre lleva a la vista contraria


@pytest.mark.parametrize(
    "data",
    ["otra:cosa", "blk:req:no-numero:full", "blk:req:1:jpg", "blk:sin-partes", ""],
)
def test_a_callback_that_is_not_a_block_toggle_is_rejected(data):
    with pytest.raises(ValueError):
        parse_block_callback(data)


def test_the_block_store_evicts_the_oldest_request_beyond_the_limit():
    store: dict[str, list[tuple[str, str]]] = {}

    for number in range(5):
        store_block_views(store, f"req{number}", [("compacta", "completa")], max_requests=3)

    assert list(store) == ["req2", "req3", "req4"]  # FIFO: caen los más antiguos


# --------------------------------------------------------------------------------------
# Traducción de errores a mensaje de usuario
# --------------------------------------------------------------------------------------


def test_a_video_without_subtitles_gets_its_specific_message():
    # NoSubtitlesError hereda de TranscriptError: debe ganar el mensaje específico.
    assert error_message(NoSubtitlesError("vid")) == NO_SUBTITLES_MESSAGE
    assert "subtítulos" in NO_SUBTITLES_MESSAGE


def test_a_transcript_error_suggests_checking_the_link():
    assert error_message(TranscriptError("privado")) == VIDEO_ERROR_MESSAGE
    assert "enlace" in VIDEO_ERROR_MESSAGE
    assert VIDEO_ERROR_MESSAGE != NO_SUBTITLES_MESSAGE


def test_an_llm_error_suggests_retrying():
    assert error_message(LLMError("500")) == LLM_ERROR_MESSAGE
    assert error_message(LLMRateLimitError("429")) == LLM_ERROR_MESSAGE  # las subclases también
    assert "inténtalo" in LLM_ERROR_MESSAGE.lower() or "reint" in LLM_ERROR_MESSAGE.lower()


def test_an_unexpected_error_gets_a_generic_message_without_internals():
    error = ValueError("detalle-interno-secreto-123")

    message = error_message(error)

    assert message == UNEXPECTED_ERROR_MESSAGE
    assert "detalle-interno-secreto-123" not in message  # nada de tracebacks al usuario


# --------------------------------------------------------------------------------------
# Coste estimado y pie del informe
# --------------------------------------------------------------------------------------

PRICES = {
    "map_input_usd_per_mtok": 0.1,
    "map_output_usd_per_mtok": 0.4,
    "reduce_input_usd_per_mtok": 3.0,
    "reduce_output_usd_per_mtok": 15.0,
}


def test_estimate_cost_multiplies_each_leg_by_its_price():
    cost = estimate_cost(make_result(), make_settings(**PRICES))

    # map: 90k*0.1 + 9k*0.4 = 12_600; reduce: 10k*3 + 2k*15 = 60_000 -> 72_600 / 1M
    assert cost == pytest.approx(0.0726)


@pytest.mark.parametrize("missing", sorted(PRICES))
def test_estimate_cost_is_none_if_any_price_is_missing(missing):
    prices = {**PRICES, missing: None}

    assert estimate_cost(make_result(), make_settings(**prices)) is None


def test_the_footer_reports_blocks_and_tokens():
    footer = build_footer(make_result(), make_settings())

    assert "9 bloques" in footer
    assert "111.000 tokens" in footer


def test_the_footer_omits_the_cost_when_prices_are_not_configured():
    footer = build_footer(make_result(), make_settings())

    assert "$" not in footer  # sin precios no se inventa ningún coste


def test_the_footer_includes_the_estimated_cost_when_prices_are_configured():
    footer = build_footer(make_result(), make_settings(**PRICES))

    assert "0,073 $" in footer
