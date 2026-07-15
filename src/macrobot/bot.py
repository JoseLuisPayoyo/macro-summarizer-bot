"""Bot de Telegram (modo polling) y punto de entrada de la aplicación.

Responsabilidad: la capa de interfaz. Recibe mensajes, saca el enlace de YouTube con
`transcript.find_youtube_url`, llama a `pipeline.summarize` y entrega el informe. No
contiene lógica de negocio: no sabe nada de yt-dlp ni de OpenRouter.

Funciona en modo polling (`run_polling`), sin webhook ni URL pública. El
`OpenRouterClient` se crea una vez en `main` y se cierra en el `post_shutdown` de la
aplicación, así el Ctrl+C no deja conexiones colgando.

Detalles de la capa de Telegram que resuelve este módulo:
- El pipeline tarda minutos: se responde con UN mensaje de estado que se va EDITANDO con
  los hitos del `progress` del pipeline. Una edición que falle (rate limit de Telegram,
  texto idéntico...) se ignora: el progreso es cosmético y no debe tumbar el resumen.
- La ENTREGA parsea la salida del reduce por su contrato de encabezados (`parse_report`)
  y la envía en `parse_mode=HTML`: un mensaje con el Panorama, uno por bloque —encabezado
  en negrita y contenido en `<blockquote expandable>`, que Telegram colapsa solo, sin
  botones ni callbacks— y el cierre de tesis y conclusiones con el pie. Si el reduce se
  desvía del contrato, se degrada al summary escapado y troceado: nunca se falla por
  formato.
- SEGURIDAD DEL HTML (lo que antes nos hacía evitar `parse_mode`): TODO texto que venga
  del LLM pasa por `html.escape`; las únicas etiquetas vivas son las que pone el bot
  (`<b>`, `<blockquote expandable>`). Y al trocear un mensaje largo se corta el texto
  CRUDO primero y se escapa DESPUÉS — al revés se partiría una entidad (`&amp;`) por la
  mitad.
- Los mensajes de estado y de error siguen en texto plano, sin `parse_mode`.
- `split_message` (el troceo del texto crudo) corta por párrafos/líneas/espacios, nunca
  a media palabra, y la concatenación de los trozos reconstruye el original.

Todos los textos de cara al usuario van en español.
"""

import html
import logging
from collections.abc import Callable
from dataclasses import dataclass

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from macrobot.config import Settings, get_settings
from macrobot.llm import LLMError, OpenRouterClient
from macrobot.pipeline import SummaryResult, summarize
from macrobot.transcript import NoSubtitlesError, TranscriptError, find_youtube_url

logger = logging.getLogger(__name__)

TELEGRAM_MAX_CHARS = 4096

WELCOME_MESSAGE = (
    "👋 Mándame un enlace de YouTube y te devuelvo un resumen estructurado en español.\n\n"
    "Estoy pensado para charlas largas de macroeconomía en inglés (1-2 horas). Tardo unos "
    "minutos: leo los subtítulos del vídeo, analizo la charla por bloques y redacto el "
    "informe.\n\n"
    "Eso sí: de momento solo sé resumir vídeos que tengan subtítulos (manuales o "
    "automáticos)."
)

NO_URL_MESSAGE = (
    "No veo ningún enlace de YouTube en tu mensaje. Mándame la URL del vídeo "
    "(youtube.com/watch?v=… o youtu.be/…) y me pongo con ello."
)

INITIAL_STATUS_MESSAGE = "🎬 Enlace recibido, empiezo a procesar el vídeo…"

DONE_MESSAGE = "✅ Listo. Aquí va el informe:"

NO_SUBTITLES_MESSAGE = (
    "❌ Este vídeo no tiene subtítulos (ni siquiera automáticos), y de momento solo sé "
    "resumir vídeos que los tengan. Prueba con otro vídeo."
)

VIDEO_ERROR_MESSAGE = (
    "❌ No he podido obtener el vídeo. Comprueba que el enlace es correcto y que el vídeo "
    "es público, y vuelve a intentarlo."
)

LLM_ERROR_MESSAGE = (
    "❌ El resumen ha fallado a mitad por un error temporal del modelo. Suele arreglarse "
    "solo: inténtalo de nuevo en un par de minutos."
)

UNEXPECTED_ERROR_MESSAGE = (
    "❌ Algo ha salido mal procesando el vídeo. Inténtalo de nuevo y, si se repite, avisa "
    "a quien administre el bot."
)

# Encabezados fijos del contrato de REDUCE_SYSTEM (los demás `## ` son bloques).
PANORAMA_HEADING = "Panorama"
CONCLUSIONS_HEADING = "Tesis y conclusiones"

_QUOTE_OPEN = "<blockquote expandable>"
_QUOTE_CLOSE = "</blockquote>"


# --------------------------------------------------------------------------------------
# Lógica pura (cubierta por tests, sin SDK de Telegram)
# --------------------------------------------------------------------------------------


def _cut_point(text: str, limit: int) -> int:
    """Elige dónde cortar `text` para que el primer trozo quepa en `limit`.

    Prueba separadores de mejor a peor (párrafo > línea > espacio) y, dentro de cada uno,
    de atrás hacia delante, descartando los cortes que caerían dentro de un bloque de
    código (número impar de vallas ``` antes del corte). Si no hay ningún corte bueno
    —una "palabra" más larga que el límite, o un bloque de código gigante—, corta duro.
    """
    window = text[:limit]
    for separator in ("\n\n", "\n", " "):
        position = window.rfind(separator)
        while position > 0:
            cut = position + len(separator)
            if text[:cut].count("```") % 2 == 0:
                return cut
            position = window.rfind(separator, 0, position)
    return limit


def split_message(text: str, limit: int = TELEGRAM_MAX_CHARS) -> list[str]:
    """Trocea un texto largo en mensajes que quepan en el límite de Telegram.

    Opera sobre texto CRUDO (sin escapar). La concatenación de los trozos reconstruye el
    texto original exactamente: los separadores se quedan al final del trozo anterior.
    """
    parts: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = _cut_point(remaining, limit)
        parts.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        parts.append(remaining)
    return parts


def error_message(error: Exception) -> str:
    """Traduce las excepciones del contrato del pipeline a un mensaje de usuario.

    El orden importa: `NoSubtitlesError` hereda de `TranscriptError` y las subclases de
    `LLMError` caen en el mensaje genérico del modelo. Nunca se filtra el detalle
    interno de la excepción al chat.
    """
    if isinstance(error, NoSubtitlesError):
        return NO_SUBTITLES_MESSAGE
    if isinstance(error, TranscriptError):
        return VIDEO_ERROR_MESSAGE
    if isinstance(error, LLMError):
        return LLM_ERROR_MESSAGE
    return UNEXPECTED_ERROR_MESSAGE


def estimate_cost(result: SummaryResult, settings: Settings) -> float | None:
    """Coste estimado del vídeo en USD, o `None` si falta algún precio en la config."""
    prices = (
        settings.map_input_usd_per_mtok,
        settings.map_output_usd_per_mtok,
        settings.reduce_input_usd_per_mtok,
        settings.reduce_output_usd_per_mtok,
    )
    if any(price is None for price in prices):
        return None
    map_input, map_output, reduce_input, reduce_output = prices
    return (
        result.map_usage.prompt_tokens * map_input
        + result.map_usage.completion_tokens * map_output
        + result.reduce_usage.prompt_tokens * reduce_input
        + result.reduce_usage.completion_tokens * reduce_output
    ) / 1_000_000


def build_footer(result: SummaryResult, settings: Settings) -> str:
    """Pie del informe: bloques analizados, tokens y coste estimado (si hay precios)."""
    tokens = f"{result.total_usage.total_tokens:,}".replace(",", ".")
    pieces = [f"{result.chunk_count} bloques", f"{tokens} tokens"]
    cost = estimate_cost(result, settings)
    if cost is not None:
        pieces.append(f"coste estimado {cost:.3f} $".replace(".", ","))
    return "\n\n📊 " + " · ".join(pieces)


@dataclass(frozen=True, slots=True)
class ReduceReport:
    """La salida del reduce partida por su contrato de encabezados."""

    panorama: str
    blocks: list[tuple[str, str]]  # (encabezado "[mm:ss] tema", contenido)
    conclusions: str


def parse_report(summary: str) -> ReduceReport:
    """Parte la salida del reduce por los encabezados `## ` del contrato de REDUCE_SYSTEM.

    Tolerante con un LLM que se desvíe: un encabezado ausente deja su sección vacía, el
    texto antes del primer `## ` cuenta como panorama y cualquier `## ` que no sea el
    Panorama ni el cierre se trata como un bloque. Nunca lanza por formato.
    """
    panorama: list[str] = []
    conclusions: list[str] = []
    blocks: list[tuple[str, list[str]]] = []
    current = panorama  # el preámbulo sin encabezado cuenta como panorama
    for line in summary.splitlines():
        if line.startswith("## "):
            title = line.removeprefix("## ").strip()
            if title == PANORAMA_HEADING:
                current = panorama
            elif title == CONCLUSIONS_HEADING:
                current = conclusions
            else:
                blocks.append((title, []))
                current = blocks[-1][1]
        else:
            current.append(line)
    return ReduceReport(
        panorama="\n".join(panorama).strip(),
        blocks=[(title, "\n".join(lines).strip()) for title, lines in blocks],
        conclusions="\n".join(conclusions).strip(),
    )


def _escape(text: str) -> str:
    """Escapa `<`, `>` y `&` para `parse_mode=HTML` (el texto nunca va en atributos)."""
    return html.escape(text, quote=False)


def _render_closing(raw: str) -> str:
    """Escapa el cierre del informe convirtiendo sus líneas `### X` en negrita."""
    rendered = []
    for line in raw.splitlines():
        if line.startswith("### "):
            rendered.append(f"<b>{_escape(line.removeprefix('### '))}</b>")
        else:
            rendered.append(_escape(line))
    return "\n".join(rendered)


def _split_raw(text: str, budget: int, render: Callable[[str], str]) -> list[str]:
    """Trocea texto CRUDO en trozos cuya versión renderizada (escapada) quepa en `budget`.

    Se trocea SIEMPRE antes de escapar —al revés se partiría una entidad `&amp;` por la
    mitad— y se comprueba después: si el escape infló un trozo por encima del hueco, se
    recorta y el resto vuelve a la cola. La concatenación reconstruye el original.
    """
    parts: list[str] = []
    pending = split_message(text, budget)
    while pending:
        piece = pending.pop(0)
        excess = len(render(piece)) - budget
        if excess <= 0:
            parts.append(piece)
            continue
        cut = _cut_point(piece, max(1, len(piece) - excess))
        pending.insert(0, piece[cut:])
        pending.insert(0, piece[:cut])
    return parts


def _section_messages(
    title: str | None,
    body: str,
    *,
    quoted: bool,
    limit: int,
    render: Callable[[str], str],
) -> list[str]:
    """Los mensajes HTML de una sección: título en negrita y cuerpo (en cita si `quoted`)."""
    header = f"<b>{_escape(title)}</b>" if title else ""
    if not body:
        return [header] if header else []
    prefix = f"{header}\n" if header else ""
    overhead = len(prefix) + (len(_QUOTE_OPEN) + len(_QUOTE_CLOSE) if quoted else 0)
    messages: list[str] = []
    for position, piece in enumerate(_split_raw(body, limit - overhead, render)):
        rendered = render(piece)
        if quoted:
            rendered = f"{_QUOTE_OPEN}{rendered}{_QUOTE_CLOSE}"
        messages.append((prefix if position == 0 else "") + rendered)
    return messages


def build_block_message(heading: str, body: str, limit: int = TELEGRAM_MAX_CHARS) -> list[str]:
    """El/los mensajes HTML de un bloque: encabezado en negrita + cita expandible.

    Telegram colapsa solo el `<blockquote expandable>` largo, así que el detalle se
    expande con el gesto nativo del cliente, sin botones ni callbacks. Si el contenido
    no cabe en un mensaje, cada trozo va en su propia cita; el encabezado, en el primero.
    """
    return _section_messages(heading, body, quoted=True, limit=limit, render=_escape)


def build_report_messages(
    result: SummaryResult, settings: Settings, limit: int = TELEGRAM_MAX_CHARS
) -> list[str]:
    """Todos los mensajes HTML del informe, en su orden de envío.

    Panorama, un mensaje por bloque y el cierre de tesis y conclusiones (el pie de
    bloques/tokens/coste va en el último mensaje). Si el reduce no siguió el contrato de
    encabezados (ni bloques ni cierre), se degrada al summary completo escapado y
    troceado: nunca se falla por formato.
    """
    report = parse_report(result.summary)
    footer = build_footer(result, settings)

    if not report.blocks and not report.conclusions:
        body = result.summary.strip() + footer
        return _section_messages(None, body, quoted=False, limit=limit, render=_escape)

    messages: list[str] = []
    if report.panorama:
        messages += _section_messages(
            f"🧭 {PANORAMA_HEADING}", report.panorama, quoted=False, limit=limit, render=_escape
        )
    for heading, body in report.blocks:
        messages += build_block_message(heading, body, limit)
    if report.conclusions:
        messages += _section_messages(
            f"📌 {CONCLUSIONS_HEADING}",
            report.conclusions + footer,
            quoted=False,
            limit=limit,
            render=_render_closing,
        )
    else:
        # Sin cierre no hay dónde colgar el pie: va en su propio mensaje, nunca se pierde.
        messages += _section_messages(
            None, footer.strip(), quoted=False, limit=limit, render=_escape
        )
    return messages


# --------------------------------------------------------------------------------------
# Handlers de Telegram
# --------------------------------------------------------------------------------------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler de `/start`: envía el mensaje de bienvenida."""
    message = update.effective_message
    if message is not None:
        await message.reply_text(WELCOME_MESSAGE)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler de los mensajes de texto: saca el enlace, resume y entrega el informe."""
    message = update.effective_message
    if message is None or not message.text:
        return

    url = find_youtube_url(message.text)
    if url is None:
        await message.reply_text(NO_URL_MESSAGE)
        return

    status = await message.reply_text(INITIAL_STATUS_MESSAGE)

    async def progress(text: str) -> None:
        # El progreso es cosmético: una edición fallida (rate limit de Telegram, texto
        # idéntico al anterior...) no debe tumbar un resumen que lleva minutos en marcha.
        try:
            await status.edit_text(text)
        except TelegramError:
            logger.debug("No se pudo editar el mensaje de estado", exc_info=True)

    settings: Settings = context.bot_data["settings"]
    client: OpenRouterClient = context.bot_data["client"]

    logger.info("Resumiendo %s", url)
    try:
        result = await summarize(url, client, settings, progress=progress)
    except Exception as error:
        if isinstance(error, TranscriptError | LLMError):
            logger.warning("Fallo previsto resumiendo %s: %s", url, error)
        else:
            logger.exception("Error inesperado resumiendo %s", url)
        await progress(error_message(error))
        return

    logger.info(
        "Informe de %s listo: %d bloques, %d tokens",
        url,
        result.chunk_count,
        result.total_usage.total_tokens,
    )
    await progress(DONE_MESSAGE)
    for text in build_report_messages(result, settings):
        await message.reply_text(text, parse_mode=ParseMode.HTML)


# --------------------------------------------------------------------------------------
# Construcción de la aplicación y punto de entrada
# --------------------------------------------------------------------------------------


def build_application(settings: Settings, client: OpenRouterClient) -> Application:
    """Monta la `Application` de python-telegram-bot con los handlers y el ciclo de vida."""

    async def close_client(_: Application) -> None:
        await client.aclose()

    application = (
        Application.builder().token(settings.telegram_token).post_shutdown(close_client).build()
    )
    application.bot_data["settings"] = settings
    application.bot_data["client"] = client
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return application


def main() -> None:
    """Punto de entrada (`uv run macrobot`): arranca el bot en modo polling."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # una línea por petición es ruido

    settings = get_settings()
    client = OpenRouterClient(settings, title="macrobot")
    application = build_application(settings, client)

    logger.info("Bot arrancado en modo polling; Ctrl+C para parar.")
    application.run_polling()


if __name__ == "__main__":
    main()
