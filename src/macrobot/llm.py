"""Cliente de OpenRouter.

Responsabilidad: ser el único punto del código que habla con un LLM. Expone una API
mínima de chat-completions sobre httpx, con reintentos y backoff exponencial ante
errores transitorios (429 y 5xx) y timeouts generosos, porque el paso reduce sobre una
charla de dos horas puede tardar bastante.

Todas las llamadas (map y reduce) pasan por aquí; el modelo concreto se elige por
parámetro con los IDs de `Settings.map_model` / `Settings.reduce_model`.
"""

from dataclasses import dataclass

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class LLMError(Exception):
    """La llamada al LLM falló de forma definitiva (tras agotar los reintentos)."""


@dataclass(frozen=True, slots=True)
class Completion:
    """Respuesta de un modelo, con el uso de tokens para poder vigilar el coste."""

    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int


class OpenRouterClient:
    """Cliente asíncrono de la API de chat-completions de OpenRouter."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = OPENROUTER_BASE_URL,
        timeout: float = 180.0,
        max_retries: int = 4,
    ) -> None:
        raise NotImplementedError

    async def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> Completion:
        """Lanza una petición de chat-completion y devuelve el texto de la respuesta.

        Reintenta con backoff exponencial ante 429 y 5xx; lanza `LLMError` si se agotan
        los reintentos o si la respuesta no tiene el formato esperado.
        """
        raise NotImplementedError

    async def aclose(self) -> None:
        """Cierra el cliente httpx subyacente."""
        raise NotImplementedError
