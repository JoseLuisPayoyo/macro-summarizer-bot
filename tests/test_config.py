"""Tests de `macrobot.config`: lo que el pipeline y el bot consumen de la configuración."""

import pytest

from macrobot.config import Settings, get_settings


def make_settings(**overrides) -> Settings:
    values = {"telegram_token": "tg", "openrouter_api_key": "or"}
    values.update(overrides)
    return Settings(**values, _env_file=None)


def test_sub_lang_list_splits_the_csv_in_preference_order():
    settings = make_settings(sub_langs="en,en-US,en-GB")

    assert settings.sub_lang_list == ["en", "en-US", "en-GB"]


def test_sub_lang_list_strips_spaces_and_ignores_empty_entries():
    settings = make_settings(sub_langs=" en , es ,,en-US, ")

    assert settings.sub_lang_list == ["en", "es", "en-US"]


def test_allowed_user_id_list_parses_the_csv_into_ints():
    settings = make_settings(allowed_user_ids="123, 456 ,789")

    assert settings.allowed_user_id_list == [123, 456, 789]


def test_allowed_user_id_list_is_empty_by_default_failing_closed():
    # Vacío = nadie autorizado: el bot debe fallar cerrado, no abrir a todo el mundo.
    assert make_settings().allowed_user_id_list == []


def test_allowed_user_id_list_ignores_blank_entries():
    assert make_settings(allowed_user_ids=" , ,42, ").allowed_user_id_list == [42]


def test_max_concurrency_defaults_to_five():
    assert make_settings().max_concurrency == 5


def test_max_concurrency_can_be_overridden():
    assert make_settings(max_concurrency=2).max_concurrency == 2


def test_prices_are_unset_by_default():
    settings = make_settings()

    assert settings.map_input_usd_per_mtok is None
    assert settings.reduce_output_usd_per_mtok is None


@pytest.fixture
def fresh_settings_cache():
    """Vacía la caché de `get_settings` antes y después, para no contaminar otros tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_get_settings_reads_the_environment(monkeypatch, fresh_settings_cache):
    monkeypatch.setenv("TELEGRAM_TOKEN", "token-del-entorno")
    monkeypatch.setenv("OPENROUTER_API_KEY", "key-del-entorno")

    settings = get_settings()

    assert settings.telegram_token == "token-del-entorno"
    assert settings.openrouter_api_key == "key-del-entorno"


def test_get_settings_returns_the_same_cached_instance(monkeypatch, fresh_settings_cache):
    monkeypatch.setenv("TELEGRAM_TOKEN", "t")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")

    assert get_settings() is get_settings()
