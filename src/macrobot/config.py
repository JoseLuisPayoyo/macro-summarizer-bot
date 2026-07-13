"""Configuración de la aplicación.

Toda la configuración se lee de variables de entorno (o de un fichero `.env` en la raíz
del repositorio) mediante pydantic-settings. Ningún otro módulo debe leer `os.environ`
directamente: todos reciben o importan un objeto `Settings`.

Los IDs de modelo (`map_model`, `reduce_model`) son cadenas de OpenRouter y se copian
tal cual de https://openrouter.ai/models.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración del bot, cargada desde el entorno o desde `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Credenciales ---
    telegram_token: str
    openrouter_api_key: str
    groq_api_key: str | None = None  # opcional: solo hace falta para el plan B (Whisper)

    # --- Modelos (IDs de openrouter.ai/models) ---
    map_model: str = "google/gemini-2.0-flash-001"
    reduce_model: str = "anthropic/claude-sonnet-4"

    # --- Comportamiento del resumen ---
    summary_lang: str = "es"
    chunk_minutes: int = 10
    sub_langs: str = "en,en-US,en-GB"

    @property
    def sub_lang_list(self) -> list[str]:
        """`sub_langs` como lista de códigos de idioma, en orden de preferencia."""
        raise NotImplementedError


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Devuelve la instancia única de `Settings` (cacheada durante todo el proceso)."""
    raise NotImplementedError
