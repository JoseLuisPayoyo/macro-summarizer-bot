"""Obtención y limpieza de la transcripción de un vídeo de YouTube.

Responsabilidad: dada una URL de YouTube, devolver la transcripción como una lista de
segmentos con marca de tiempo, usando yt-dlp para descargar los subtítulos (primero los
manuales, y si no existen los automáticos). Es la vía gratuita; si el vídeo no tiene
subtítulos en ningún idioma soportado se lanza `NoSubtitlesError` y `pipeline` recurre
al plan B (`macrobot.whisper`).

Los subtítulos de YouTube vienen en formato WebVTT y, en su variante automática, traen
duplicados por el efecto "rolling captions": cada cue repite parte de la línea anterior.
El parseo debe deduplicar ese solapamiento además de eliminar las etiquetas de estilo
(`<c>`, `<00:00:01.234>`) y las cabeceras del fichero.
"""

from dataclasses import dataclass


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


def extract_video_id(url: str) -> str:
    """Extrae el ID de 11 caracteres de una URL de YouTube.

    Acepta las formas habituales: `youtube.com/watch?v=ID`, `youtu.be/ID`,
    `youtube.com/live/ID`, `youtube.com/shorts/ID`, con o sin parámetros extra.
    Lanza `ValueError` si la URL no es un enlace de YouTube reconocible.
    """
    raise NotImplementedError


def fetch_subtitles(url: str, langs: list[str]) -> str:
    """Descarga el fichero VTT de subtítulos del vídeo y devuelve su contenido crudo.

    Usa yt-dlp sin descargar el vídeo (`skip_download`), probando los idiomas de `langs`
    en orden y prefiriendo los subtítulos manuales sobre los automáticos.
    Lanza `NoSubtitlesError` si no hay ninguna pista disponible.
    """
    raise NotImplementedError


def parse_vtt(vtt: str) -> list[Segment]:
    """Convierte el contenido de un fichero WebVTT en segmentos limpios y ordenados.

    Elimina cabeceras, etiquetas de estilo y marcas de tiempo inline, y deduplica el
    solapamiento de los subtítulos automáticos de YouTube.
    """
    raise NotImplementedError


def get_transcript(url: str, langs: list[str]) -> list[Segment]:
    """Devuelve la transcripción del vídeo: `fetch_subtitles` + `parse_vtt`."""
    raise NotImplementedError
