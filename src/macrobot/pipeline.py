"""Orquestación del map-reduce, de la URL al informe final.

Responsabilidad: encadenar los demás módulos y ser el único sitio donde vive el flujo
completo. `bot` no sabe nada de yt-dlp ni de OpenRouter: solo llama a `summarize`.

Flujo:
1. `transcript.get_transcript` -> segmentos, solo desde subtítulos. Corre en un thread
   porque yt-dlp es bloqueante. DECISIÓN vigente: NO hay plan B de Whisper; un vídeo sin
   subtítulos propaga `NoSubtitlesError` tal cual y el bot se lo explica al usuario.
2. `chunking.chunk_segments` -> bloques de ~`chunk_minutes` minutos.
3. MAP: una llamada por bloque al modelo barato (`map_model`), en paralelo pero acotado
   por un `asyncio.Semaphore` de `max_concurrency` para respetar los límites de
   OpenRouter. Las extracciones se conservan en el orden cronológico de los bloques.
   Si un bloque falla tras agotar los reintentos del cliente, el fallo se PROPAGA:
   mejor ningún informe que un informe con un bloque perdido en silencio.
4. REDUCE: una única llamada al modelo bueno (`reduce_model`) con todo concatenado.
5. `SummaryResult`: el informe más el uso de tokens desglosado en map/reduce/total, para
   poder vigilar el objetivo de coste (<0,10 $/vídeo).

El progreso se comunica con un callback opcional —síncrono o asíncrono, da igual— para
que el bot vaya editando su mensaje de estado sin que este módulo dependa de Telegram.
"""

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from macrobot import transcript
from macrobot.chunking import Chunk, chunk_segments
from macrobot.config import Settings
from macrobot.llm import LLMResult, OpenRouterClient, TokenUsage
from macrobot.prompts import (
    MAP_SYSTEM,
    REDUCE_SYSTEM,
    build_map_user_prompt,
    build_reduce_user_prompt,
)

# Acepta tanto una corutina como una función normal; `_notify` normaliza.
ProgressCallback = Callable[[str], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class SummaryResult:
    """Resultado final del pipeline para un vídeo."""

    summary: str
    chunk_count: int
    map_model: str
    reduce_model: str
    map_usage: TokenUsage  # suma de todos los bloques
    reduce_usage: TokenUsage
    total_usage: TokenUsage


async def _notify(progress: ProgressCallback | None, message: str) -> None:
    """Invoca el callback de progreso si existe, sea síncrono o asíncrono."""
    if progress is None:
        return
    result = progress(message)
    if inspect.isawaitable(result):
        await result


def _sum_usage(usages: list[TokenUsage]) -> TokenUsage:
    return TokenUsage(
        prompt_tokens=sum(usage.prompt_tokens for usage in usages),
        completion_tokens=sum(usage.completion_tokens for usage in usages),
        total_tokens=sum(usage.total_tokens for usage in usages),
    )


async def run_map(
    client: OpenRouterClient,
    chunks: list[Chunk],
    settings: Settings,
    progress: ProgressCallback | None = None,
) -> tuple[list[str], TokenUsage]:
    """Paso map: extrae la información estructurada de cada bloque con `map_model`.

    Las llamadas van en paralelo bajo un semáforo de `settings.max_concurrency`, y las
    extracciones se devuelven en el orden cronológico de los bloques, termine cada
    llamada cuando termine. El progreso cuenta bloques COMPLETADOS, no lanzados.
    """
    semaphore = asyncio.Semaphore(settings.max_concurrency)
    completed = 0

    async def extract(chunk: Chunk) -> LLMResult:
        nonlocal completed
        async with semaphore:
            result = await client.complete(
                MAP_SYSTEM,
                build_map_user_prompt(chunk.timespan, chunk.text),
                model=settings.map_model,
            )
        completed += 1
        await _notify(progress, f"Analizando bloque {completed}/{len(chunks)}…")
        return result

    # return_exceptions=True: se espera a que TODAS las llamadas acaben antes de decidir.
    # Así no quedan tareas huérfanas en vuelo y el error que se propaga es el del primer
    # bloque (en orden cronológico) que falló.
    outcomes = await asyncio.gather(*(extract(chunk) for chunk in chunks), return_exceptions=True)
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise outcome

    results: list[LLMResult] = outcomes  # sin excepciones, gather conserva el orden
    return [result.text for result in results], _sum_usage([r.usage for r in results])


async def run_reduce(
    client: OpenRouterClient,
    title: str,
    extractions: list[str],
    settings: Settings,
) -> LLMResult:
    """Paso reduce: sintetiza las extracciones, en orden, en el informe final."""
    return await client.complete(
        REDUCE_SYSTEM,
        build_reduce_user_prompt(title, extractions),
        model=settings.reduce_model,
    )


async def summarize(
    url: str,
    client: OpenRouterClient,
    settings: Settings,
    *,
    progress: ProgressCallback | None = None,
) -> SummaryResult:
    """Ejecuta el pipeline completo para una URL de YouTube y devuelve el informe.

    Propaga `NoSubtitlesError` si el vídeo no tiene subtítulos (sin plan B por ahora) y
    los errores de `llm` si alguna llamada agota sus reintentos.
    """
    await _notify(progress, "Obteniendo los subtítulos del vídeo…")
    segments = await asyncio.to_thread(transcript.get_transcript, url, settings.sub_lang_list)
    chunks = chunk_segments(segments, settings.chunk_minutes)

    extractions, map_usage = await run_map(client, chunks, settings, progress)

    await _notify(progress, "Redactando el informe final…")
    reduce_result = await run_reduce(client, url, extractions, settings)

    return SummaryResult(
        summary=reduce_result.text,
        chunk_count=len(chunks),
        map_model=settings.map_model,
        reduce_model=settings.reduce_model,
        map_usage=map_usage,
        reduce_usage=reduce_result.usage,
        total_usage=_sum_usage([map_usage, reduce_result.usage]),
    )
