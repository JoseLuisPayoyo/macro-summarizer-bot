"""Tests de `macrobot.pipeline`: el map-reduce de principio a fin.

Sin red: la transcripción se sustituye con monkeypatch y el LLM con `FakeLLMClient`, un
doble que registra cada llamada, la concurrencia en vuelo y el orden real de terminación.
Los retardos que usa son reales pero de milisegundos: suficientes para desordenar la
terminación de las llamadas concurrentes sin ralentizar la suite.
"""

import asyncio

import pytest

from macrobot.config import Settings
from macrobot.llm import LLMError, LLMResult, TokenUsage
from macrobot.pipeline import SummaryResult, summarize
from macrobot.prompts import MAP_SYSTEM, REDUCE_SYSTEM
from macrobot.transcript import NoSubtitlesError, Segment

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

MAP_USAGE = TokenUsage(prompt_tokens=100, completion_tokens=10, total_tokens=110)
REDUCE_USAGE = TokenUsage(prompt_tokens=500, completion_tokens=200, total_tokens=700)


def make_settings(**overrides) -> Settings:
    values = {
        "telegram_token": "tg-token",
        "openrouter_api_key": "or-key",
        "map_model": "barato/mapa",
        "reduce_model": "bueno/informe",
        "chunk_minutes": 10,
        "max_concurrency": 5,
    }
    values.update(overrides)
    return Settings(**values, _env_file=None)


def make_segments(*texts: str) -> list[Segment]:
    """Un segmento por texto, separados 10 minutos: cada uno cae en su propio bloque."""
    return [
        Segment(start=index * 600.0, end=index * 600.0 + 5.0, text=text)
        for index, text in enumerate(texts)
    ]


def install_transcript(monkeypatch, segments: list[Segment]) -> dict:
    """Sustituye la descarga real de subtítulos y registra con qué se llamó."""
    recorded: dict = {}

    def fake_get_transcript(url: str, langs: list[str]) -> list[Segment]:
        recorded["url"] = url
        recorded["langs"] = langs
        return segments

    monkeypatch.setattr("macrobot.transcript.get_transcript", fake_get_transcript)
    return recorded


class FakeLLMClient:
    """Doble de `OpenRouterClient` para el pipeline.

    Responde a las llamadas map con `EXTRACCIÓN<mensaje de usuario>` (así el texto del
    bloque viaja hasta el reduce y se puede afirmar el orden) y a la de reduce con
    `INFORME FINAL`. Registra las llamadas, el máximo de llamadas simultáneas en vuelo y
    el orden en que terminaron los map.
    """

    def __init__(
        self,
        map_delays: dict[int, float] | None = None,
        fail_on_map_index: int | None = None,
    ) -> None:
        self.map_calls: list[dict] = []
        self.reduce_calls: list[dict] = []
        self.completion_order: list[int] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self._map_delays = map_delays or {}
        self._fail_on_map_index = fail_on_map_index

    async def complete(
        self,
        system: str,
        user: str,
        *,
        model: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> LLMResult:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if system == MAP_SYSTEM:
                index = len(self.map_calls)
                self.map_calls.append({"user": user, "model": model})
                await asyncio.sleep(self._map_delays.get(index, 0.0))
                if index == self._fail_on_map_index:
                    raise LLMError(f"fallo simulado en el bloque {index}")
                self.completion_order.append(index)
                return LLMResult(text=f"EXTRACCIÓN<{user}>", model=model, usage=MAP_USAGE)

            assert system == REDUCE_SYSTEM, "llamada con un system prompt desconocido"
            self.reduce_calls.append({"user": user, "model": model})
            return LLMResult(text="INFORME FINAL", model=model, usage=REDUCE_USAGE)
        finally:
            self.in_flight -= 1


# --------------------------------------------------------------------------------------
# El flujo: una llamada map por bloque y una única de reduce
# --------------------------------------------------------------------------------------


async def test_summarize_makes_one_map_call_per_chunk_and_a_single_reduce(monkeypatch):
    install_transcript(monkeypatch, make_segments("bloque A", "bloque B", "bloque C"))
    client = FakeLLMClient()

    result = await summarize(URL, client, make_settings())

    assert len(client.map_calls) == 3
    assert len(client.reduce_calls) == 1
    assert result.summary == "INFORME FINAL"
    assert result.chunk_count == 3


async def test_the_map_calls_use_the_map_prompt_and_the_cheap_model(monkeypatch):
    install_transcript(monkeypatch, make_segments("bloque A", "bloque B"))
    client = FakeLLMClient()

    await summarize(URL, client, make_settings())

    assert [call["model"] for call in client.map_calls] == ["barato/mapa", "barato/mapa"]
    # El mensaje de usuario lo monta el helper de prompts: rango temporal + texto crudo.
    assert client.map_calls[0]["user"].startswith("[00:00:00 - 00:00:05]")
    assert "bloque A" in client.map_calls[0]["user"]
    assert client.map_calls[1]["user"].startswith("[00:10:00 - 00:10:05]")


async def test_the_reduce_call_uses_the_reduce_prompt_and_the_good_model(monkeypatch):
    install_transcript(monkeypatch, make_segments("bloque A", "bloque B"))
    client = FakeLLMClient()

    await summarize(URL, client, make_settings())

    reduce_call = client.reduce_calls[0]
    assert reduce_call["model"] == "bueno/informe"
    assert URL in reduce_call["user"]  # la charla queda identificada en el prompt
    assert "Bloque 1 de 2" in reduce_call["user"]  # concatenado con el helper de prompts


async def test_summarize_passes_the_configured_languages_to_the_transcript(monkeypatch):
    recorded = install_transcript(monkeypatch, make_segments("bloque A"))

    await summarize(URL, FakeLLMClient(), make_settings(sub_langs="es, en-US"))

    assert recorded["url"] == URL
    assert recorded["langs"] == ["es", "en-US"]


async def test_the_result_reports_the_models_used(monkeypatch):
    install_transcript(monkeypatch, make_segments("bloque A"))

    result = await summarize(URL, FakeLLMClient(), make_settings())

    assert result.map_model == "barato/mapa"
    assert result.reduce_model == "bueno/informe"


# --------------------------------------------------------------------------------------
# Uso de tokens: desglose map/reduce y total
# --------------------------------------------------------------------------------------


async def test_token_usage_is_aggregated_and_broken_down(monkeypatch):
    install_transcript(monkeypatch, make_segments("bloque A", "bloque B", "bloque C"))

    result = await summarize(URL, FakeLLMClient(), make_settings())

    assert result.map_usage == TokenUsage(300, 30, 330)  # 3 bloques x (100, 10, 110)
    assert result.reduce_usage == REDUCE_USAGE
    assert result.total_usage == TokenUsage(800, 230, 1030)
    assert result.total_usage.total_tokens == (
        result.map_usage.total_tokens + result.reduce_usage.total_tokens
    )


# --------------------------------------------------------------------------------------
# Orden y concurrencia
# --------------------------------------------------------------------------------------


async def test_extractions_reach_the_reduce_in_order_even_if_calls_finish_reversed(monkeypatch):
    """gather conserva el orden de los bloques aunque la terminación sea la inversa."""
    install_transcript(monkeypatch, make_segments("bloque A", "bloque B", "bloque C"))
    # El primer bloque es el más lento y el último el más rápido: terminan al revés.
    client = FakeLLMClient(map_delays={0: 0.06, 1: 0.03, 2: 0.0})

    await summarize(URL, client, make_settings(max_concurrency=3))

    # Sanidad: las llamadas terminaron de verdad en orden inverso al cronológico...
    assert client.completion_order == [2, 1, 0]

    # ...y aun así el reduce recibe las extracciones en orden cronológico.
    reduce_user = client.reduce_calls[0]["user"]
    assert (
        reduce_user.index("bloque A")
        < reduce_user.index("bloque B")
        < reduce_user.index("bloque C")
    )


async def test_the_semaphore_caps_the_number_of_simultaneous_map_calls(monkeypatch):
    install_transcript(monkeypatch, make_segments(*(f"bloque {index}" for index in range(8))))
    client = FakeLLMClient(map_delays={index: 0.01 for index in range(8)})

    await summarize(URL, client, make_settings(max_concurrency=3))

    assert client.max_in_flight == 3  # nunca más de max_concurrency, y se aprovecha entero


# --------------------------------------------------------------------------------------
# Progreso
# --------------------------------------------------------------------------------------


async def test_an_async_progress_callback_is_called_at_every_milestone(monkeypatch):
    install_transcript(monkeypatch, make_segments("bloque A", "bloque B", "bloque C"))
    messages: list[str] = []

    async def progress(message: str) -> None:
        messages.append(message)

    await summarize(URL, FakeLLMClient(), make_settings(), progress=progress)

    lowered = [message.lower() for message in messages]
    assert any("subtítulos" in message for message in lowered)
    assert any("bloque 1/3" in message for message in lowered)
    assert any("bloque 3/3" in message for message in lowered)
    assert any("informe" in message for message in lowered)


async def test_a_sync_progress_callback_also_works(monkeypatch):
    install_transcript(monkeypatch, make_segments("bloque A"))
    messages: list[str] = []

    await summarize(URL, FakeLLMClient(), make_settings(), progress=messages.append)

    assert messages  # se invocó sin que el pipeline intentara await sobre None


async def test_the_milestones_arrive_in_pipeline_order(monkeypatch):
    install_transcript(monkeypatch, make_segments("bloque A"))
    messages: list[str] = []

    await summarize(URL, FakeLLMClient(), make_settings(), progress=messages.append)

    subtitles_at = next(i for i, m in enumerate(messages) if "subtítulos" in m.lower())
    map_at = next(i for i, m in enumerate(messages) if "bloque 1/1" in m.lower())
    report_at = next(i for i, m in enumerate(messages) if "informe" in m.lower())
    assert subtitles_at < map_at < report_at


async def test_without_a_progress_callback_nothing_breaks(monkeypatch):
    install_transcript(monkeypatch, make_segments("bloque A"))

    result = await summarize(URL, FakeLLMClient(), make_settings())

    assert isinstance(result, SummaryResult)


# --------------------------------------------------------------------------------------
# Errores: se propagan, sin plan B y sin informes a medias
# --------------------------------------------------------------------------------------


async def test_no_subtitles_error_propagates_without_touching_the_llm(monkeypatch):
    def no_subs(url: str, langs: list[str]) -> list[Segment]:
        raise NoSubtitlesError("este vídeo no tiene subtítulos")

    monkeypatch.setattr("macrobot.transcript.get_transcript", no_subs)
    client = FakeLLMClient()

    with pytest.raises(NoSubtitlesError):
        await summarize(URL, client, make_settings())

    assert client.map_calls == []  # sin plan B: ni Whisper ni llamadas a medias
    assert client.reduce_calls == []


async def test_a_map_block_that_exhausts_its_retries_propagates_and_skips_the_reduce(monkeypatch):
    install_transcript(monkeypatch, make_segments("bloque A", "bloque B", "bloque C"))
    client = FakeLLMClient(fail_on_map_index=1)

    with pytest.raises(LLMError, match="bloque 1"):
        await summarize(URL, client, make_settings())

    assert client.reduce_calls == []  # mejor sin informe que un informe con huecos
