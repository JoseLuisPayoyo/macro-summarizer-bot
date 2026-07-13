"""Obtención y limpieza de la transcripción de un vídeo de YouTube.

Responsabilidad: dada una URL de YouTube, devolver la transcripción como una lista de
segmentos con marca de tiempo, usando yt-dlp para descargar los subtítulos (primero los
manuales, y si no existen los automáticos). Es la vía gratuita; si el vídeo no tiene
subtítulos en ningún idioma soportado se lanza `NoSubtitlesError` y `pipeline` recurre
al plan B (`macrobot.whisper`).

El módulo separa a propósito dos cosas:

- El PARSEO (`parse_vtt` y las funciones de URL) es puro: no toca la red y es donde está
  toda la lógica delicada, así que es lo que cubren los tests unitarios.
- La DESCARGA (`fetch_subtitles`) es una capa fina de I/O sobre yt-dlp.

Sobre el parseo: los subtítulos de YouTube vienen en WebVTT y, en su variante automática,
traen duplicados por el efecto "rolling captions". La pantalla muestra dos líneas y cada
cue reemite lo que ya se veía añadiendo una o dos palabras al final, con cues intermedios
idénticos al anterior. Reconstruir el texto es, por tanto, quitar el solapamiento entre
cada cue y el anterior. Ver `_new_words`.
"""

import html
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


class TranscriptError(Exception):
    """Error genérico al obtener la transcripción."""


class NoSubtitlesError(TranscriptError):
    """El vídeo no tiene subtítulos en ninguno de los idiomas configurados."""


@dataclass(frozen=True, slots=True)
class Segment:
    """Un fragmento de transcripción con su ventana temporal, en segundos."""

    start: float
    end: float
    text: str


# --------------------------------------------------------------------------------------
# URLs de YouTube (puro)
# --------------------------------------------------------------------------------------

# El lookbehind evita picar en dominios que solo se parecen a YouTube (notyoutube.com):
# exige que delante del host no haya ni letra ni punto.
_YOUTUBE_URL_RE = re.compile(
    r"""
    (?<![\w.])
    (?:https?://)?
    (?:
        (?:[\w-]+\.)?youtube\.com/
        (?:
            watch\?(?:[^\s&#]*&)*v=(?P<watch_id>[\w-]{11})
            |
            (?:live|shorts|embed|v)/(?P<path_id>[\w-]{11})
        )
        |
        youtu\.be/(?P<short_id>[\w-]{11})
    )
    \S*
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Puntuación con la que un humano suele cerrar la frase justo después de pegar el enlace.
_TRAILING_PUNCTUATION = ".,;:!?)]}>\"'«»…"


def find_youtube_url(text: str) -> str | None:
    """Busca el primer enlace de YouTube dentro de un texto cualquiera.

    Es lo que usa el bot: al usuario le basta con pegar el enlace en medio de un mensaje.
    Devuelve la URL tal cual aparece (sin la puntuación final de la frase), o `None` si el
    texto no contiene ningún enlace de YouTube.
    """
    match = _YOUTUBE_URL_RE.search(text)
    if match is None:
        return None
    return match.group(0).rstrip(_TRAILING_PUNCTUATION)


def extract_video_id(url: str) -> str:
    """Extrae el ID de 11 caracteres de una URL de YouTube.

    Acepta las formas habituales: `youtube.com/watch?v=ID`, `youtu.be/ID`,
    `youtube.com/live/ID`, `youtube.com/shorts/ID`, con o sin esquema y con parámetros
    extra. Lanza `ValueError` si la URL no es un enlace de YouTube reconocible.
    """
    match = _YOUTUBE_URL_RE.search(url)
    if match is None:
        raise ValueError(f"No parece un enlace de un vídeo de YouTube: {url!r}")
    video_id = match.group("watch_id") or match.group("path_id") or match.group("short_id")
    return video_id


# --------------------------------------------------------------------------------------
# Parseo del VTT (puro)
# --------------------------------------------------------------------------------------

_TIMESTAMP = r"(?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3}"

# La línea de tiempos del cue. Lo que venga detrás (align:start, position:0%...) son
# metadatos de posición y se ignoran.
_CUE_TIME_RE = re.compile(rf"^\s*(?P<start>{_TIMESTAMP})\s*-->\s*(?P<end>{_TIMESTAMP})")

# Marcas de tiempo inline (<00:00:01.234>) y etiquetas de estilo (<c>, </c>, <c.colorXXX>).
# Se quita la etiqueta y se conserva lo que envuelve.
_TAG_RE = re.compile(r"<[^>]*>")

# Umbral para tratar un solapamiento como ventana rodante y no como coincidencia. Que dos
# cues consecutivos compartan una palabra en la frontera pasa continuamente en subtítulos
# manuales ("...lo llamo crecimiento" / "crecimiento es lo que importa") y ahí las dos son
# buenas. Que compartan dos o más seguidas ya no es casualidad: es la ventana rodante.
_MIN_ROLLING_OVERLAP = 2


def _parse_timestamp(value: str) -> float:
    """Convierte `HH:MM:SS.mmm` (o `MM:SS.mmm`) en segundos."""
    parts = value.replace(",", ".").split(":")
    seconds = float(parts[-1])
    seconds += int(parts[-2]) * 60
    if len(parts) == 3:
        seconds += int(parts[0]) * 3600
    return seconds


def _clean_cue_text(payload: str) -> str:
    """Deja el texto de un cue en palabras limpias: sin etiquetas, entidades ni espacios de más."""
    text = _TAG_RE.sub("", payload)
    text = html.unescape(text)
    return " ".join(text.split())


def _new_words(previous: list[str], current: list[str]) -> list[str]:
    """Devuelve las palabras de `current` que no venían ya arrastradas del cue anterior.

    Busca el mayor solapamiento entre el final de `previous` y el principio de `current`
    (que es exactamente lo que produce la ventana rodante de YouTube, incluso cuando la
    ventana pasa de página y el solapamiento es solo parcial) y se queda con el resto.
    """
    for size in range(min(len(previous), len(current)), 0, -1):
        if not _same_words(previous[-size:], current[:size]):
            continue
        is_rolling = (
            size >= _MIN_ROLLING_OVERLAP  # solapamiento largo: ventana rodante
            or size == len(previous)  # el cue nuevo arranca repitiendo el anterior entero
            or size == len(current)  # el cue nuevo ya estaba entero en el anterior
        )
        return current[size:] if is_rolling else current
    return current


def _same_words(left: list[str], right: list[str]) -> bool:
    return [word.casefold() for word in left] == [word.casefold() for word in right]


def parse_vtt(vtt: str) -> list[Segment]:
    """Convierte el contenido de un fichero WebVTT en segmentos limpios y ordenados.

    Elimina la cabecera, los bloques NOTE, los números de cue, los metadatos de posición,
    las etiquetas inline y deduplica el solapamiento de los subtítulos automáticos. Los
    cues que no aportan texto nuevo no generan segmento.
    """
    segments: list[Segment] = []
    previous_words: list[str] = []

    for start, end, payload in _iter_cues(vtt):
        words = _clean_cue_text(payload).split()
        if not words:
            continue

        added = _new_words(previous_words, words)
        previous_words = words
        if added:
            segments.append(Segment(start=start, end=end, text=" ".join(added)))

    return segments


def _iter_cues(vtt: str) -> list[tuple[float, float, str]]:
    """Recorre el VTT y devuelve (inicio, fin, texto crudo) de cada cue, en orden.

    Todo lo que no sea un cue —cabecera WEBVTT, metadatos `Kind:`/`Language:`, bloques
    NOTE, números de cue— cae por el camino: solo se entra a un cue por su línea de
    tiempos.
    """
    lines = vtt.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues: list[tuple[float, float, str]] = []
    index = 0

    while index < len(lines):
        match = _CUE_TIME_RE.match(lines[index])
        if match is None:
            index += 1
            continue

        start = _parse_timestamp(match.group("start"))
        end = _parse_timestamp(match.group("end"))
        index += 1

        payload: list[str] = []
        # El cue termina en la primera línea VACÍA (no "en blanco": los auto-subs de
        # YouTube meten líneas de un solo espacio dentro del cue y ahí sí hay texto detrás)
        # o al empezar el cue siguiente.
        while index < len(lines) and lines[index] != "" and not _CUE_TIME_RE.match(lines[index]):
            payload.append(lines[index])
            index += 1

        cues.append((start, end, "\n".join(payload)))

    return cues


# --------------------------------------------------------------------------------------
# Descarga con yt-dlp (I/O)
# --------------------------------------------------------------------------------------


def fetch_subtitles(url: str, langs: list[str]) -> str:
    """Descarga el fichero VTT de subtítulos del vídeo y devuelve su contenido crudo.

    Usa yt-dlp sin descargar el vídeo, probando los idiomas de `langs` en orden. Pide a la
    vez los subtítulos manuales y los automáticos: yt-dlp da preferencia a los manuales
    cuando existen para ese idioma, que es justo lo que queremos.

    Lanza `NoSubtitlesError` si el vídeo no tiene ninguna pista en esos idiomas, y
    `TranscriptError` si yt-dlp no puede procesar el vídeo (privado, borrado, etc.).
    """
    video_id = extract_video_id(url)

    with tempfile.TemporaryDirectory(prefix="macrobot-subs-") as raw_tmpdir:
        tmpdir = Path(raw_tmpdir)
        options = {
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": list(langs),
            "subtitlesformat": "vtt",
            "outtmpl": str(tmpdir / "%(id)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
        }

        try:
            with YoutubeDL(options) as ydl:
                ydl.download([url])
        except DownloadError as error:
            raise TranscriptError(f"yt-dlp no pudo procesar {url}: {error}") from error

        subtitle_file = _pick_subtitle_file(tmpdir, video_id, langs)
        if subtitle_file is None:
            raise NoSubtitlesError(f"El vídeo {video_id} no tiene subtítulos en {', '.join(langs)}")

        return subtitle_file.read_text(encoding="utf-8")


def _pick_subtitle_file(tmpdir: Path, video_id: str, langs: list[str]) -> Path | None:
    """Elige el VTT descargado siguiendo el orden de preferencia de idiomas."""
    for lang in langs:
        candidate = tmpdir / f"{video_id}.{lang}.vtt"
        if candidate.exists():
            return candidate

    # yt-dlp puede haber resuelto el idioma a una variante (en-US, en-orig...).
    remaining = sorted(tmpdir.glob("*.vtt"))
    return remaining[0] if remaining else None


def get_transcript(url: str, langs: list[str]) -> list[Segment]:
    """Devuelve la transcripción del vídeo: `fetch_subtitles` + `parse_vtt`.

    Lanza `NoSubtitlesError` también cuando los subtítulos existen pero están vacíos, para
    que el pipeline pueda caer al plan B en lugar de resumir la nada.
    """
    segments = parse_vtt(fetch_subtitles(url, langs))
    if not segments:
        raise NoSubtitlesError(f"Los subtítulos de {url} no tienen texto")
    return segments
