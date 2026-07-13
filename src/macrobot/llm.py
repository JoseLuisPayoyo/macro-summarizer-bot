"""Cliente de OpenRouter.

Responsabilidad: ser el único punto del código que habla con un LLM. Expone una API
mínima de chat-completions sobre httpx, asíncrona porque el paso map lanza ~12 llamadas
en paralelo con `asyncio.gather` sobre un único cliente compartido.

Decisiones que fija este módulo:

- La API key sale de `config.Settings`, nunca de `os.environ` directamente.
- El ID de modelo llega SIEMPRE por parámetro: este módulo no conoce modelos concretos
  (los elige `pipeline` desde `Settings.map_model` / `Settings.reduce_model`).
- Reintentos con backoff exponencial y jitter SOLO ante fallos transitorios: 429, 5xx y
  errores de transporte (timeouts, conexión). Un 429 con `Retry-After` respeta esa espera.
  Cualquier otro 4xx es un error del programador o de configuración: se lanza a la
  primera, sin reintentar.
- Toda respuesta devuelve el uso de tokens (`TokenUsage`): el pipeline lo necesita para
  vigilar el objetivo de coste (<0,10 $/vídeo).
"""

import asyncio
import random
from dataclasses import dataclass
from types import TracebackType
from typing import Self

import httpx

from macrobot.config import Settings

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_MAX_SECONDS = 30.0

# Alias para que los tests puedan sustituir el sleep sin tocar asyncio globalmente.
_sleep = asyncio.sleep


class LLMError(Exception):
    """La llamada al LLM falló de forma definitiva (tras agotar los reintentos)."""


class LLMRateLimitError(LLMError):
    """OpenRouter siguió devolviendo 429 incluso después de agotar los reintentos."""


class LLMResponseError(LLMError):
    """Respuesta no recuperable: un 4xx distinto de 429, o un cuerpo sin el formato esperado."""


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Tokens consumidos por una llamada, tal y como los reporta OpenRouter."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True, slots=True)
class LLMResult:
    """Respuesta de un modelo, con el uso de tokens para poder vigilar el coste."""

    text: str
    model: str  # el que resolvió OpenRouter, que puede diferir del pedido
    usage: TokenUsage


def _backoff_delay(attempt: int) -> float:
    """Espera exponencial con jitter aditivo.

    El jitter se acota a un cuarto del escalón para que dos reintentos consecutivos nunca
    se solapen (el peor caso del escalón N queda por debajo del mejor caso del N+1).
    """
    delay = min(_BACKOFF_BASE_SECONDS * 2**attempt, _BACKOFF_MAX_SECONDS)
    return delay + random.uniform(0.0, delay / 4)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Lee `Retry-After` en segundos; el formato fecha no se soporta (se cae al backoff)."""
    header = response.headers.get("Retry-After")
    if header is None:
        return None
    try:
        return float(header)
    except ValueError:
        return None


def _parse_completion(response: httpx.Response, *, requested_model: str) -> LLMResult:
    """Extrae texto, modelo y usage de un 200 de OpenRouter; `LLMResponseError` si no cuadra."""
    try:
        data = response.json()
    except ValueError as error:
        raise LLMResponseError(
            f"OpenRouter devolvió un 200 cuyo cuerpo no es JSON: {response.text[:200]!r}"
        ) from error

    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise LLMResponseError(
            f"Respuesta sin choices[0].message.content: {str(data)[:300]}"
        ) from error
    if not isinstance(text, str):
        raise LLMResponseError(f"El content de la respuesta no es texto: {type(text).__name__}")

    usage = data.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens", 0))
    completion_tokens = int(usage.get("completion_tokens", 0))
    return LLMResult(
        text=text,
        model=str(data.get("model", requested_model)),
        usage=TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=int(usage.get("total_tokens", prompt_tokens + completion_tokens)),
        ),
    )


class OpenRouterClient:
    """Cliente asíncrono de la API de chat-completions de OpenRouter.

    Una instancia por proceso: reutiliza las conexiones y no guarda estado por petición,
    así que es segura para llamadas concurrentes. Usar como context manager asíncrono o
    cerrar con `aclose()`.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        base_url: str = OPENROUTER_BASE_URL,
        timeout: float = 180.0,
        max_retries: int = 4,
        referer: str | None = None,
        title: str | None = None,
    ) -> None:
        if max_retries < 0:
            raise ValueError(f"max_retries debe ser >= 0, y es {max_retries}")

        headers = {"Authorization": f"Bearer {settings.openrouter_api_key}"}
        # Cabeceras de atribución opcionales de OpenRouter (salen en su ranking de apps).
        if referer is not None:
            headers["HTTP-Referer"] = referer
        if title is not None:
            headers["X-Title"] = title

        self._max_retries = max_retries
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            # El read timeout es generoso a propósito: el paso reduce de una charla de
            # dos horas puede tardar minutos en generar.
            timeout=httpx.Timeout(timeout, connect=15.0),
        )

    async def complete(
        self,
        system: str,
        user: str,
        *,
        model: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> LLMResult:
        """Lanza una petición de chat-completion y devuelve texto + uso de tokens.

        Reintenta ante 429 (respetando `Retry-After`), 5xx y errores de transporte, con
        backoff exponencial. Lanza `LLMRateLimitError` si el 429 persiste, `LLMError` si
        se agotan los reintentos y `LLMResponseError` ante un 4xx no recuperable o una
        respuesta malformada.
        """
        payload: dict[str, object] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        for attempt in range(self._max_retries + 1):
            is_last_attempt = attempt == self._max_retries

            try:
                response = await self._client.post("/chat/completions", json=payload)
            except httpx.TransportError as error:
                if is_last_attempt:
                    raise LLMError(
                        f"Sin respuesta de OpenRouter tras {attempt + 1} intentos: {error}"
                    ) from error
                await _sleep(_backoff_delay(attempt))
                continue

            if response.status_code == httpx.codes.OK:
                return _parse_completion(response, requested_model=model)

            if response.status_code == httpx.codes.TOO_MANY_REQUESTS or response.is_server_error:
                if is_last_attempt:
                    detail = (
                        f"OpenRouter devolvió {response.status_code} "
                        f"en {attempt + 1} intentos consecutivos"
                    )
                    if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
                        raise LLMRateLimitError(detail)
                    raise LLMError(detail)
                retry_after = _retry_after_seconds(response)
                await _sleep(retry_after if retry_after is not None else _backoff_delay(attempt))
                continue

            # 4xx que no es 429: reintentarlo solo repetiría el mismo error.
            raise LLMResponseError(
                f"OpenRouter devolvió {response.status_code}: {response.text[:300]}"
            )

        raise LLMError("Bucle de reintentos agotado sin respuesta")  # inalcanzable

    async def aclose(self) -> None:
        """Cierra el cliente httpx subyacente."""
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
