"""Tests de la lógica pura de `macrobot.bot`.

Todo lo que aquí se prueba son funciones sin SDK de Telegram, sin red y sin mocks: el
troceo del informe al límite de 4096, la traducción de excepciones a mensajes de usuario
y el pie con el coste estimado. La integración con python-telegram-bot (handlers,
polling) es una capa fina que no se cubre con unitarios.
"""

import pytest

from macrobot.bot import (
    LLM_ERROR_MESSAGE,
    NO_SUBTITLES_MESSAGE,
    TELEGRAM_MAX_CHARS,
    UNEXPECTED_ERROR_MESSAGE,
    VIDEO_ERROR_MESSAGE,
    build_footer,
    error_message,
    estimate_cost,
    split_message,
)
from macrobot.config import Settings
from macrobot.llm import LLMError, LLMRateLimitError, TokenUsage
from macrobot.pipeline import SummaryResult
from macrobot.transcript import NoSubtitlesError, TranscriptError, find_youtube_url


def make_settings(**overrides) -> Settings:
    values = {"telegram_token": "tg", "openrouter_api_key": "or"}
    values.update(overrides)
    return Settings(**values, _env_file=None)


def make_result(**overrides) -> SummaryResult:
    values = {
        "summary": "el informe",
        "chunk_count": 9,
        "map_model": "barato/mapa",
        "reduce_model": "bueno/informe",
        "map_usage": TokenUsage(90_000, 9_000, 99_000),
        "reduce_usage": TokenUsage(10_000, 2_000, 12_000),
        "total_usage": TokenUsage(100_000, 11_000, 111_000),
    }
    values.update(overrides)
    return SummaryResult(**values)


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
