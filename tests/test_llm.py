"""Tests de `macrobot.llm`: cliente de OpenRouter.

Sin red: httpx se intercepta con respx, y el sleep del backoff se sustituye por un doble
que solo registra cuánto habría dormido cada reintento (los tests no duermen de verdad).
"""

import asyncio
import json

import httpx
import pytest
import respx

from macrobot import llm
from macrobot.config import Settings
from macrobot.llm import (
    LLMError,
    LLMRateLimitError,
    LLMResponseError,
    OpenRouterClient,
    TokenUsage,
)

COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"


def make_settings(api_key: str = "settings-key") -> Settings:
    # _env_file=None aísla el test de cualquier .env que haya en la máquina.
    return Settings(telegram_token="tg-token", openrouter_api_key=api_key, _env_file=None)


@pytest.fixture
def sleeps(monkeypatch):
    """Anula el sleep del backoff y registra las esperas que habría hecho cada reintento."""
    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(llm, "_sleep", fake_sleep)
    return recorded


def ok_response(
    text: str = "resumen del bloque",
    prompt_tokens: int = 100,
    completion_tokens: int = 40,
) -> httpx.Response:
    """Una respuesta 200 con la forma real de OpenRouter."""
    return httpx.Response(
        200,
        json={
            "id": "gen-123",
            "model": "proveedor/modelo-real",
            "choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        },
    )


# --------------------------------------------------------------------------------------
# Camino feliz: parseo de texto y usage
# --------------------------------------------------------------------------------------


@respx.mock
async def test_complete_returns_the_text_and_the_token_usage(sleeps):
    respx.post(COMPLETIONS_URL).mock(return_value=ok_response())

    async with OpenRouterClient(make_settings()) as client:
        result = await client.complete("sistema", "usuario", model="barato/modelo")

    assert result.text == "resumen del bloque"
    assert result.model == "proveedor/modelo-real"  # el que resolvió OpenRouter
    assert result.usage == TokenUsage(prompt_tokens=100, completion_tokens=40, total_tokens=140)
    assert sleeps == []  # sin reintentos no hay esperas


@respx.mock
async def test_complete_sends_the_expected_request(sleeps):
    route = respx.post(COMPLETIONS_URL).mock(return_value=ok_response())

    async with OpenRouterClient(make_settings()) as client:
        await client.complete("eres un analista", "resume esto", model="barato/modelo")

    body = json.loads(route.calls[0].request.content)
    assert body["model"] == "barato/modelo"
    assert body["messages"] == [
        {"role": "system", "content": "eres un analista"},
        {"role": "user", "content": "resume esto"},
    ]
    assert body["temperature"] == 0.2
    assert "max_tokens" not in body  # None -> no se manda


@respx.mock
async def test_complete_sends_max_tokens_only_when_set(sleeps):
    route = respx.post(COMPLETIONS_URL).mock(return_value=ok_response())

    async with OpenRouterClient(make_settings()) as client:
        await client.complete("s", "u", model="m/m", max_tokens=2048, temperature=0.7)

    body = json.loads(route.calls[0].request.content)
    assert body["max_tokens"] == 2048
    assert body["temperature"] == 0.7


# --------------------------------------------------------------------------------------
# Autenticación y cabeceras: la key sale de Settings, no del entorno
# --------------------------------------------------------------------------------------


@respx.mock
async def test_the_api_key_comes_from_settings_not_from_the_environment(sleeps, monkeypatch):
    # Si el cliente leyera os.environ, usaría esta key envenenada.
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key-que-no-debe-usarse")
    route = respx.post(COMPLETIONS_URL).mock(return_value=ok_response())

    async with OpenRouterClient(make_settings(api_key="la-de-settings")) as client:
        await client.complete("s", "u", model="m/m")

    assert route.calls[0].request.headers["Authorization"] == "Bearer la-de-settings"


@respx.mock
async def test_the_optional_attribution_headers_are_sent_when_configured(sleeps):
    route = respx.post(COMPLETIONS_URL).mock(return_value=ok_response())
    client = OpenRouterClient(
        make_settings(), referer="https://github.com/user/macrobot", title="macrobot"
    )

    async with client:
        await client.complete("s", "u", model="m/m")

    headers = route.calls[0].request.headers
    assert headers["HTTP-Referer"] == "https://github.com/user/macrobot"
    assert headers["X-Title"] == "macrobot"


@respx.mock
async def test_the_attribution_headers_are_absent_by_default(sleeps):
    route = respx.post(COMPLETIONS_URL).mock(return_value=ok_response())

    async with OpenRouterClient(make_settings()) as client:
        await client.complete("s", "u", model="m/m")

    headers = route.calls[0].request.headers
    assert "HTTP-Referer" not in headers
    assert "X-Title" not in headers


# --------------------------------------------------------------------------------------
# Reintentos: 429, 5xx y timeouts son transitorios
# --------------------------------------------------------------------------------------


@respx.mock
async def test_a_429_with_retry_after_is_retried_and_succeeds(sleeps):
    route = respx.post(COMPLETIONS_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            ok_response(text="a la segunda"),
        ]
    )

    async with OpenRouterClient(make_settings()) as client:
        result = await client.complete("s", "u", model="m/m")

    assert result.text == "a la segunda"
    assert route.call_count == 2
    assert sleeps == [2.0]  # respeta Retry-After en vez del backoff


@respx.mock
async def test_an_unparseable_retry_after_falls_back_to_the_backoff(sleeps):
    route = respx.post(COMPLETIONS_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
            ok_response(),
        ]
    )

    async with OpenRouterClient(make_settings()) as client:
        await client.complete("s", "u", model="m/m")

    assert route.call_count == 2
    assert len(sleeps) == 1
    assert 1.0 <= sleeps[0] <= 1.25  # el primer escalón del backoff, no la fecha


@respx.mock
async def test_a_repeated_500_is_retried_up_to_the_limit_and_then_raises(sleeps):
    route = respx.post(COMPLETIONS_URL).mock(return_value=httpx.Response(500))

    async with OpenRouterClient(make_settings(), max_retries=4) as client:
        with pytest.raises(LLMError, match="500"):
            await client.complete("s", "u", model="m/m")

    assert route.call_count == 5  # 1 intento + 4 reintentos
    assert len(sleeps) == 4  # tras el último fallo ya no se duerme


@respx.mock
async def test_a_429_that_never_stops_raises_the_typed_rate_limit_error(sleeps):
    respx.post(COMPLETIONS_URL).mock(return_value=httpx.Response(429))

    async with OpenRouterClient(make_settings(), max_retries=2) as client:
        with pytest.raises(LLMRateLimitError):
            await client.complete("s", "u", model="m/m")


@respx.mock
async def test_a_timeout_is_retried(sleeps):
    route = respx.post(COMPLETIONS_URL).mock(
        side_effect=[httpx.ConnectTimeout("se agotó"), ok_response(text="recuperado")]
    )

    async with OpenRouterClient(make_settings()) as client:
        result = await client.complete("s", "u", model="m/m")

    assert result.text == "recuperado"
    assert route.call_count == 2
    assert len(sleeps) == 1


@respx.mock
async def test_persistent_connection_errors_exhaust_the_retries_and_raise(sleeps):
    route = respx.post(COMPLETIONS_URL).mock(side_effect=httpx.ConnectError("sin red"))

    async with OpenRouterClient(make_settings(), max_retries=2) as client:
        with pytest.raises(LLMError):
            await client.complete("s", "u", model="m/m")

    assert route.call_count == 3


@respx.mock
async def test_the_backoff_grows_between_consecutive_retries(sleeps):
    respx.post(COMPLETIONS_URL).mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(500),
            httpx.Response(500),
            ok_response(),
        ]
    )

    async with OpenRouterClient(make_settings()) as client:
        await client.complete("s", "u", model="m/m")

    assert len(sleeps) == 3
    assert sleeps[0] < sleeps[1] < sleeps[2]  # exponencial: el jitter no solapa escalones


# --------------------------------------------------------------------------------------
# Errores NO recuperables: ni un solo reintento
# --------------------------------------------------------------------------------------


@respx.mock
async def test_a_400_raises_without_retrying(sleeps):
    route = respx.post(COMPLETIONS_URL).mock(
        return_value=httpx.Response(400, json={"error": {"message": "modelo inexistente"}})
    )

    async with OpenRouterClient(make_settings()) as client:
        with pytest.raises(LLMResponseError, match="400"):
            await client.complete("s", "u", model="no/existe")

    assert route.call_count == 1
    assert sleeps == []


@respx.mock
async def test_a_401_raises_without_retrying(sleeps):
    route = respx.post(COMPLETIONS_URL).mock(return_value=httpx.Response(401))

    async with OpenRouterClient(make_settings(api_key="mala")) as client:
        with pytest.raises(LLMResponseError, match="401"):
            await client.complete("s", "u", model="m/m")

    assert route.call_count == 1


@respx.mock
async def test_a_200_without_choices_raises_a_typed_error(sleeps):
    respx.post(COMPLETIONS_URL).mock(return_value=httpx.Response(200, json={"id": "gen-1"}))

    async with OpenRouterClient(make_settings()) as client:
        with pytest.raises(LLMResponseError):
            await client.complete("s", "u", model="m/m")


@respx.mock
async def test_a_200_whose_content_is_not_text_raises_a_typed_error(sleeps):
    respx.post(COMPLETIONS_URL).mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": None}}], "usage": {}},
        )
    )

    async with OpenRouterClient(make_settings()) as client:
        with pytest.raises(LLMResponseError):
            await client.complete("s", "u", model="m/m")


@respx.mock
async def test_a_200_that_is_not_json_raises_a_typed_error(sleeps):
    respx.post(COMPLETIONS_URL).mock(
        return_value=httpx.Response(200, text="<html>pasarela rota</html>")
    )

    async with OpenRouterClient(make_settings()) as client:
        with pytest.raises(LLMResponseError):
            await client.complete("s", "u", model="m/m")


def test_a_negative_max_retries_is_rejected_at_construction():
    with pytest.raises(ValueError, match="max_retries"):
        OpenRouterClient(make_settings(), max_retries=-1)


# --------------------------------------------------------------------------------------
# Uso en paralelo: el paso map hará asyncio.gather sobre un único cliente
# --------------------------------------------------------------------------------------


@respx.mock
async def test_one_client_serves_many_parallel_calls(sleeps):
    route = respx.post(COMPLETIONS_URL).mock(return_value=ok_response())

    async with OpenRouterClient(make_settings()) as client:
        results = await asyncio.gather(
            *(client.complete("s", f"bloque {index}", model="m/m") for index in range(12))
        )

    assert len(results) == 12
    assert route.call_count == 12
    assert all(result.usage.total_tokens == 140 for result in results)
