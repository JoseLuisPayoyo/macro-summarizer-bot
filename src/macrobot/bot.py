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
- La entrega es POR BLOQUES: primero la visión general del reduce (resumen ejecutivo +
  índice) y después un mensaje por bloque en vista compacta (tema, tesis, datos y
  predicciones) con un botón inline que expande/contrae el detalle completo. Las vistas
  salen de parsear la extracción del map por sus apartados `###` (sin volver a llamar al
  LLM); un apartado que falte se trata como ausente, nunca como error.
- El toggle necesita las dos vistas de cada bloque a mano cuando llega el callback: se
  guardan en `bot_data` por request_id, con una cola FIFO acotada para que un proceso de
  semanas no acumule memoria sin límite (un toggle de un informe ya desalojado responde
  con un aviso, no con un fallo).
- El informe de una charla larga supera el límite de 4096 caracteres por mensaje:
  `split_message` lo trocea cortando por párrafos/líneas/espacios, nunca a media palabra
  y evitando (mientras se pueda) partir un bloque de código por la mitad. Lo que se
  muestra EDITANDO un mensaje (la vista expandida) no puede trocearse: `clip_message` lo
  recorta declarándolo.
- Los errores del contrato del pipeline se traducen a mensajes de usuario en español
  (`error_message`); el detalle técnico va al log, nunca al chat.

Todos los textos de cara al usuario van en español.
"""

import logging
from uuid import uuid4

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from macrobot.config import Settings, get_settings
from macrobot.llm import LLMError, OpenRouterClient
from macrobot.pipeline import BlockSummary, SummaryResult, summarize
from macrobot.prompts import MAP_SECTION_TITLES
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

EXPAND_BUTTON_LABEL = "🔽 Ver detalle completo"
COLLAPSE_BUTTON_LABEL = "🔼 Ver menos"
EXPIRED_BLOCKS_ANSWER = "Ya no guardo el detalle de este informe. Pídeme el resumen otra vez."

# El "Tema del bloque" no es una sección de las vistas: va en el encabezado del mensaje.
_TOPIC_SECTION = MAP_SECTION_TITLES[0]

# Vista compacta (la que se manda por defecto): lo esencial de cada bloque.
COMPACT_BLOCK_SECTIONS = (
    "Tesis / ideas centrales",
    "Datos y cifras citados",
    "Predicciones / escenarios",
)

# Vista completa (al pulsar el botón): todos los apartados del esquema del map.
FULL_BLOCK_SECTIONS = tuple(title for title in MAP_SECTION_TITLES if title != _TOPIC_SECTION)

_CALLBACK_PREFIX = "blk"  # callback_data: "blk:<request_id>:<índice>:<full|compact>"
_BLOCK_VIEWS_KEY = "block_views"  # clave en bot_data del almacén de vistas por request
_MAX_STORED_REQUESTS = 20  # informes cuyos toggles siguen vivos; más allá, FIFO


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

    La concatenación de los trozos reconstruye el texto original exactamente: los
    separadores por los que se corta se quedan al final del trozo anterior.
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


def parse_extraction(extraction: str) -> dict[str, str]:
    """Trocea una extracción del map en sus apartados: título `###` -> contenido.

    El LLM no es 100% determinista: un apartado que falte, que llegue vacío o con otro
    título simplemente no aparece en el resultado. Lo anterior al primer `###` (la línea
    del rango temporal) se descarta: el rango ya viaja en `BlockSummary.timespan`.
    """
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        if current is not None and (content := "\n".join(buffer).strip()):
            sections[current] = content

    for line in extraction.splitlines():
        if line.startswith("### "):
            flush()
            current = line.removeprefix("### ").strip()
            buffer = []
        else:
            buffer.append(line)
    flush()
    return sections


def _render_block(block: BlockSummary, titles: tuple[str, ...]) -> str:
    """Encabezado (número, rango y tema) más los apartados pedidos que existan."""
    sections = parse_extraction(block.extraction)
    header = f"🧩 Bloque {block.index + 1} · [{block.timespan}]"
    if topic := sections.get(_TOPIC_SECTION):
        header += f"\n{topic}"
    pieces = [header]
    pieces += [f"▫️ {title}\n{content}" for title in titles if (content := sections.get(title))]
    return "\n\n".join(pieces)


def compact_block_view(block: BlockSummary) -> str:
    """La vista por defecto de un bloque: tema, tesis, datos y predicciones."""
    return _render_block(block, COMPACT_BLOCK_SECTIONS)


def full_block_view(block: BlockSummary) -> str:
    """La vista expandida: todos los apartados del esquema presentes en la extracción."""
    return _render_block(block, FULL_BLOCK_SECTIONS)


def clip_message(text: str, limit: int = TELEGRAM_MAX_CHARS) -> str:
    """Recorta un texto a UN mensaje de Telegram, con corte limpio y aviso del recorte.

    Para texto que se muestra editando un mensaje ya enviado (la vista expandida), donde
    trocear en varios mensajes no es una opción.
    """
    if len(text) <= limit:
        return text
    notice = "\n\n… (recortado: el detalle completo no cabe en un mensaje de Telegram)"
    cut = _cut_point(text, limit - len(notice))
    return text[:cut].rstrip() + notice


def block_keyboard(request_id: str, index: int, *, expanded: bool) -> InlineKeyboardMarkup:
    """El botón inline de un bloque: siempre ofrece la vista contraria a la mostrada."""
    if expanded:
        label, target = COLLAPSE_BUTTON_LABEL, "compact"
    else:
        label, target = EXPAND_BUTTON_LABEL, "full"
    callback_data = f"{_CALLBACK_PREFIX}:{request_id}:{index}:{target}"
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=callback_data)]])


def parse_block_callback(data: str) -> tuple[str, int, bool]:
    """Descompone el callback_data de un botón de bloque: (request_id, índice, ¿a completa?).

    Lanza `ValueError` si el dato no es un toggle de bloque bien formado.
    """
    prefix, request_id, index, target = data.split(":")  # ValueError si no son 4 partes
    if prefix != _CALLBACK_PREFIX or target not in ("full", "compact"):
        raise ValueError(f"callback_data desconocido: {data!r}")
    return request_id, int(index), target == "full"


def store_block_views(
    store: dict[str, list[tuple[str, str]]],
    request_id: str,
    views: list[tuple[str, str]],
    max_requests: int = _MAX_STORED_REQUESTS,
) -> None:
    """Guarda las vistas (compacta, completa) de un informe y desaloja las más antiguas.

    El almacén es un dict ordenado por inserción: pasado `max_requests`, cae el informe
    más antiguo y sus botones responden con `EXPIRED_BLOCKS_ANSWER` en vez de romperse.
    """
    store[request_id] = views
    while len(store) > max_requests:
        store.pop(next(iter(store)))


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

    # 1) La visión general del reduce (resumen ejecutivo + índice), con el pie.
    overview = result.summary + build_footer(result, settings)
    for part in split_message(overview):
        if part.strip():
            await message.reply_text(part)

    # 2) Un mensaje por bloque, en vista compacta y con el botón de expandir. El toggle
    # edita el ÚLTIMO mensaje del bloque, así que es esa parte la que se guarda como
    # vista compacta; la expandida se recorta a un único mensaje editable.
    request_id = uuid4().hex[:8]
    views: list[tuple[str, str]] = []
    for block in result.blocks:
        parts = split_message(compact_block_view(block))
        for part in parts[:-1]:
            await message.reply_text(part)
        compact = parts[-1]
        await message.reply_text(
            compact, reply_markup=block_keyboard(request_id, block.index, expanded=False)
        )
        views.append((compact, clip_message(full_block_view(block))))
    store_block_views(context.bot_data.setdefault(_BLOCK_VIEWS_KEY, {}), request_id, views)


async def handle_block_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler del botón de un bloque: alterna entre la vista compacta y la completa.

    Siempre responde al callback (aunque sea en vacío) para que Telegram no deje el
    reloj de carga colgado en el cliente.
    """
    query = update.callback_query
    if query is None or query.data is None:
        return

    try:
        request_id, index, wants_full = parse_block_callback(query.data)
    except ValueError:
        logger.debug("callback_data inesperado: %r", query.data)
        await query.answer()
        return

    store: dict[str, list[tuple[str, str]]] = context.bot_data.get(_BLOCK_VIEWS_KEY, {})
    views = store.get(request_id)
    if views is None or not 0 <= index < len(views):
        # El informe ya cayó de la cola FIFO (o el proceso se reinició): se avisa, no se rompe.
        await query.answer(EXPIRED_BLOCKS_ANSWER, show_alert=True)
        return

    compact, full = views[index]
    try:
        await query.edit_message_text(
            full if wants_full else compact,
            reply_markup=block_keyboard(request_id, index, expanded=wants_full),
        )
    except TelegramError:
        # Doble pulsación o rate limit: el botón es cosmético, el informe ya está entregado.
        logger.debug("No se pudo editar el mensaje del bloque", exc_info=True)
    await query.answer()


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
    application.add_handler(
        CallbackQueryHandler(handle_block_toggle, pattern=rf"^{_CALLBACK_PREFIX}:")
    )
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
