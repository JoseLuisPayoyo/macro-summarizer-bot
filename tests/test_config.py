"""Tests de `macrobot.config`: lo que el pipeline consume de la configuración."""

from macrobot.config import Settings


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


def test_max_concurrency_defaults_to_five():
    assert make_settings().max_concurrency == 5


def test_max_concurrency_can_be_overridden():
    assert make_settings(max_concurrency=2).max_concurrency == 2
