"""Plan B: transcripción del audio con Whisper en Groq.

Responsabilidad: producir la misma estructura de segmentos que `macrobot.transcript`
cuando el vídeo no tiene subtítulos. Descarga solo la pista de audio con yt-dlp, la
trocea si hace falta (la API de Groq limita el tamaño del fichero) y la envía al modelo
Whisper pidiendo la respuesta con marcas de tiempo por segmento.

Es la vía de pago del pipeline: solo se usa como respaldo, ya que a los precios de Groq
transcribir una charla de dos horas se come una parte apreciable del presupuesto de
0,10 $ por vídeo. Requiere `GROQ_API_KEY`.
"""

from pathlib import Path

from macrobot.transcript import Segment, TranscriptError

GROQ_TRANSCRIPTIONS_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
DEFAULT_WHISPER_MODEL = "whisper-large-v3-turbo"


class WhisperError(TranscriptError):
    """Falló la transcripción del audio con Groq."""


def download_audio(url: str, dest_dir: Path) -> Path:
    """Descarga con yt-dlp solo el audio del vídeo y devuelve la ruta del fichero."""
    raise NotImplementedError


async def transcribe_audio(
    audio_path: Path,
    *,
    api_key: str,
    model: str = DEFAULT_WHISPER_MODEL,
) -> list[Segment]:
    """Envía el audio a Whisper en Groq y devuelve los segmentos con marca de tiempo.

    Lanza `WhisperError` si la API falla o si no hay clave configurada.
    """
    raise NotImplementedError


async def get_transcript_via_whisper(url: str, *, api_key: str) -> list[Segment]:
    """Plan B completo: `download_audio` + `transcribe_audio`, con limpieza del temporal."""
    raise NotImplementedError
