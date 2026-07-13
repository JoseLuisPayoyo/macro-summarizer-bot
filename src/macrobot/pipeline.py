"""Orquestación del map-reduce, de la URL al resumen final.

Responsabilidad: encadenar los demás módulos y ser el único sitio donde vive el flujo
completo. `bot` no sabe nada de yt-dlp ni de OpenRouter: solo llama aquí.

Flujo:
1. `transcript.get_transcript` -> subtítulos con yt-dlp (gratis).
   Si lanza `NoSubtitlesError`, plan B: `whisper.get_transcript_via_whisper` (Groq).
2. `chunking.chunk_segments` -> bloques de ~`chunk_minutes` minutos.
3. MAP: una llamada por bloque al modelo barato (`map_model`), en paralelo con un límite
   de concurrencia para no chocar con el rate limit de OpenRouter.
4. REDUCE: una única llamada al modelo bueno (`reduce_model`) con todas las extracciones.
5. Devuelve el resumen y el coste/uso, para poder comprobar el objetivo de <0,10 $/vídeo.

El progreso se comunica hacia fuera con un callback, para que el bot vaya editando su
mensaje de "procesando..." sin que este módulo dependa de Telegram.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from macrobot.chunking import Chunk
from macrobot.config import Settings
from macrobot.llm import OpenRouterClient

ProgressCallback = Callable[[str], Awaitable[None]]

MAP_CONCURRENCY = 5


@dataclass(frozen=True, slots=True)
class SummaryResult:
    """Resultado final del pipeline para un vídeo."""

    video_id: str
    title: str
    summary: str
    chunk_count: int
    used_whisper: bool
    prompt_tokens: int
    completion_tokens: int


async def run_map(
    client: OpenRouterClient,
    chunks: list[Chunk],
    settings: Settings,
) -> list[str]:
    """Paso map: extrae la información estructurada de cada bloque con `map_model`.

    Lanza las llamadas en paralelo con un semáforo (`MAP_CONCURRENCY`) y devuelve las
    extracciones en el orden original de los bloques.
    """
    raise NotImplementedError


async def run_reduce(
    client: OpenRouterClient,
    title: str,
    extractions: list[str],
    settings: Settings,
) -> str:
    """Paso reduce: sintetiza las extracciones en el resumen final con `reduce_model`."""
    raise NotImplementedError


async def summarize_video(
    url: str,
    settings: Settings,
    *,
    on_progress: ProgressCallback | None = None,
) -> SummaryResult:
    """Ejecuta el pipeline completo para una URL de YouTube y devuelve el resumen."""
    raise NotImplementedError
