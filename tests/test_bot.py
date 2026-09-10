"""Tests de la lógica pura de `macrobot.bot`.

Todo lo que aquí se prueba son funciones sin SDK de Telegram, sin red y sin mocks: el
troceo al límite de 4096, la traducción de excepciones a mensajes de usuario, el pie con
el coste estimado, el parseo de la salida del reduce por su contrato de encabezados y la
construcción de los mensajes HTML (con TODO el texto del LLM escapado: las únicas
etiquetas vivas son las que pone el bot). La integración con python-telegram-bot
(handlers, polling) es una capa fina que no se cubre con unitarios.
"""

import html
import re
from datetime import UTC, datetime

import pytest
from telegram import Chat, Message, Update, User

from macrobot.bot import (
    LLM_ERROR_MESSAGE,
    NO_SUBTITLES_MESSAGE,
    PRIVATE_BOT_MESSAGE,
    TELEGRAM_MAX_CHARS,
    UNEXPECTED_ERROR_MESSAGE,
    VIDEO_ERROR_MESSAGE,
    access_filter,
    build_application,
    build_block_message,
    build_footer,
    build_report_messages,
    error_message,
    estimate_cost,
    handle_message,
    parse_report,
    reject_unauthorized,
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


# Una salida del reduce que sigue el contrato de encabezados de REDUCE_SYSTEM.
SAMPLE_REPORT = """\
## Panorama
La charla arranca en la inflación y desemboca en una tesis de duración.

## [00:00] Contexto de inflación
- El IPC subyacente sigue en 3,1 % y los alquileres entran con retraso.

## [10:00] La Fed
- Va tarde con los recortes (ya tratado en [00:00]).

## Tesis y conclusiones
### Tesis principales
- La Fed recortará en septiembre si el IPC baja de 3 %.

### Conclusiones
- El riesgo de la cartera está en la duración.

### Tesis de inversión
- Bonos largos: alcista, condicionado al IPC.
"""


# --------------------------------------------------------------------------------------
# Extracción de la URL de un mensaje típico de Telegram
# --------------------------------------------------------------------------------------


def test_a_typical_telegram_message_yields_its_url():
    text = "Resúmeme esto porfa 🙏\nhttps://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s\nGracias!"

    assert find_youtube_url(text) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s"


def test_a_message_without_a_url_yields_none():
    assert find_youtube_url("hola bot, ¿qué sabes hacer?") is None


# --------------------------------------------------------------------------------------
# Troceo al límite de Telegram (texto crudo; los mensajes HTML lo usan por debajo)
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
# Parseo de la salida del reduce por su contrato de encabezados
# --------------------------------------------------------------------------------------


def test_parse_report_splits_panorama_blocks_and_conclusions():
    report = parse_report(SAMPLE_REPORT)

    assert report.panorama == (
        "La charla arranca en la inflación y desemboca en una tesis de duración."
    )
    assert [heading for heading, _ in report.blocks] == [
        "[00:00] Contexto de inflación",
        "[10:00] La Fed",
    ]
    assert report.blocks[0][1] == (
        "- El IPC subyacente sigue en 3,1 % y los alquileres entran con retraso."
    )
    assert "### Tesis de inversión" in report.conclusions
    assert "- Bonos largos: alcista, condicionado al IPC." in report.conclusions


def test_parse_report_treats_missing_headings_as_empty_sections():
    report = parse_report("## [00:00] Único bloque\n- Una idea.\n")

    assert report.panorama == ""
    assert report.blocks == [("[00:00] Único bloque", "- Una idea.")]
    assert report.conclusions == ""


def test_parse_report_counts_a_preamble_without_heading_as_panorama():
    report = parse_report("El modelo se saltó el encabezado.\n\n## Tesis y conclusiones\n- C.")

    assert report.panorama == "El modelo se saltó el encabezado."
    assert report.conclusions == "- C."


def test_parse_report_of_prose_without_headings_yields_only_panorama():
    report = parse_report("prosa sin estructura ninguna")

    assert report.panorama == "prosa sin estructura ninguna"
    assert report.blocks == []
    assert report.conclusions == ""


# --------------------------------------------------------------------------------------
# Mensajes HTML: escape total del texto del LLM y citas expandibles
# --------------------------------------------------------------------------------------

# Etiquetas que pone el bot; cualquier otro <> del mensaje debe venir escapado.
BOT_TAGS = ("<blockquote expandable>", "</blockquote>", "<b>", "</b>")


def strip_bot_tags(message: str) -> str:
    for tag in BOT_TAGS:
        message = message.replace(tag, "")
    return message


def test_a_block_message_is_a_bold_heading_plus_an_expandable_quote():
    messages = build_block_message("[00:10] La Fed", "- Va tarde.")

    assert messages == ["<b>[00:10] La Fed</b>\n<blockquote expandable>- Va tarde.</blockquote>"]


def test_llm_text_is_escaped_and_only_bot_tags_survive():
    messages = build_block_message(
        "[00:10] Tema <b>tramposo</b>", "1 < 2 & 3 > 0, y un <i>guiño</i> al parser"
    )

    [message] = messages
    assert "&lt;b&gt;tramposo&lt;/b&gt;" in message  # el encabezado también se escapa
    assert "1 &lt; 2 &amp; 3 &gt; 0" in message
    stripped = strip_bot_tags(message)
    assert "<" not in stripped
    assert ">" not in stripped


def test_a_block_the_reduce_left_empty_sends_only_its_heading():
    assert build_block_message("[00:00] Tema", "") == ["<b>[00:00] Tema</b>"]


def test_long_bodies_are_split_raw_first_so_no_entity_is_cut():
    body = " y ".join(f"dato&cifra<{index}>" for index in range(200))

    messages = build_block_message("[00:00] Datos", body, limit=300)

    assert len(messages) > 1
    assert all(len(message) <= 300 for message in messages)
    # Ninguna entidad partida ni ningún & crudo en ningún trozo.
    assert all(re.search(r"&(?!amp;|lt;|gt;)", message) is None for message in messages)
    # Cada trozo va en su propia cita expandible y el conjunto reconstruye el original.
    inner_parts = []
    for message in messages:
        quote = message.split("</b>\n")[-1]
        assert quote.startswith("<blockquote expandable>")
        assert quote.endswith("</blockquote>")
        inner = quote.removeprefix("<blockquote expandable>").removesuffix("</blockquote>")
        inner_parts.append(html.unescape(inner))
    assert "".join(inner_parts) == body


def test_the_report_is_delivered_as_panorama_blocks_and_conclusions_with_footer():
    result = make_result(summary=SAMPLE_REPORT)

    messages = build_report_messages(result, make_settings())

    assert len(messages) == 4  # panorama + 2 bloques + cierre
    assert "Panorama" in messages[0]
    assert messages[1].startswith("<b>[00:00] Contexto de inflación</b>")
    assert "<blockquote expandable>" in messages[1]
    assert "<blockquote expandable>" in messages[2]
    assert "Tesis y conclusiones" in messages[3]
    # El pie (bloques/tokens/coste) va SOLO en el último mensaje.
    assert "9 bloques" in messages[3]
    assert "111.000 tokens" in messages[3]
    assert all("📊" not in message for message in messages[:-1])


def test_the_conclusions_message_bolds_its_subsections_and_escapes_the_rest():
    result = make_result(summary=SAMPLE_REPORT)

    closing = build_report_messages(result, make_settings())[-1]

    assert "<b>Tesis principales</b>" in closing  # las líneas ### se convierten en negrita
    assert "### " not in strip_bot_tags(closing)
    assert "<" not in strip_bot_tags(closing)


def test_a_summary_without_the_expected_headings_falls_back_to_the_escaped_raw_text():
    result = make_result(summary="informe <crudo> & sin estructura")

    messages = build_report_messages(result, make_settings())

    assert "informe &lt;crudo&gt; &amp; sin estructura" in messages[0]
    assert "9 bloques" in messages[-1]  # el pie no se pierde ni en el fallback
    assert all("<blockquote" not in message for message in messages)


def test_a_report_without_conclusions_still_delivers_the_footer_at_the_end():
    result = make_result(summary="## [00:00] Único bloque\n- Una idea.\n")

    messages = build_report_messages(result, make_settings())

    assert messages[0].startswith("<b>[00:00] Único bloque</b>")
    assert "9 bloques" in messages[-1]
    assert "<blockquote" not in messages[-1]  # el pie no viaja escondido en una cita


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


# --------------------------------------------------------------------------------------
# Control de acceso por lista blanca
# --------------------------------------------------------------------------------------


def make_update(user_id: int, *, username: str = "usuario", text: str = "hola") -> Update:
    """Un Update de Telegram mínimo con un mensaje de texto de `user_id` (sin bot ni red)."""
    user = User(id=user_id, first_name="Test", is_bot=False, username=username)
    chat = Chat(id=user_id, type=Chat.PRIVATE)
    message = Message(message_id=1, date=datetime.now(UTC), chat=chat, from_user=user, text=text)
    return Update(update_id=1, message=message)


class RecordingMessage:
    """Un mensaje de Telegram de pega que solo captura los `reply_text` que recibe."""

    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str, **_: object) -> None:
        self.replies.append(text)


class UpdateStub:
    """Update mínimo para invocar `reject_unauthorized` sin construir objetos de Telegram."""

    def __init__(self, user_id: int, username: str, message: RecordingMessage) -> None:
        self.effective_user = User(id=user_id, first_name="T", is_bot=False, username=username)
        self.effective_message = message


def test_access_filter_lets_an_allowed_user_through():
    allowed = access_filter(make_settings(allowed_user_ids="123,456"))

    assert allowed.check_update(make_update(123))


def test_access_filter_blocks_a_user_not_in_the_list():
    allowed = access_filter(make_settings(allowed_user_ids="123,456"))

    assert not allowed.check_update(make_update(999))


def test_access_filter_with_an_empty_list_blocks_everyone():
    # Lista vacía = fallar cerrado: nadie pasa el filtro (ni siquiera un id cualquiera).
    allowed = access_filter(make_settings(allowed_user_ids=""))

    assert not allowed.check_update(make_update(123))
    assert not allowed.check_update(make_update(999))


def routed_callback(settings: Settings, update: Update):
    """Devuelve el callback del primer handler que atendería `update`, como en runtime."""
    application = build_application(settings, client=object())
    for handler in application.handlers[0]:
        if handler.check_update(update):
            return handler.callback
    return None


def test_an_allowed_user_reaches_the_pipeline_handler():
    callback = routed_callback(make_settings(allowed_user_ids="123"), make_update(123))

    assert callback is handle_message


def test_an_unauthorized_user_is_routed_to_the_reject_handler_not_the_pipeline():
    # No se dispara handle_message: el pipeline no llega a invocarse para un extraño.
    callback = routed_callback(make_settings(allowed_user_ids="123"), make_update(999))

    assert callback is reject_unauthorized
    assert callback is not handle_message


def test_an_empty_list_routes_everyone_to_the_reject_handler():
    callback = routed_callback(make_settings(allowed_user_ids=""), make_update(123))

    assert callback is reject_unauthorized


async def test_reject_unauthorized_sends_the_private_message():
    message = RecordingMessage()
    update = UpdateStub(user_id=999, username="intruso", message=message)

    await reject_unauthorized(update, context=None)

    assert message.replies == [PRIVATE_BOT_MESSAGE]
