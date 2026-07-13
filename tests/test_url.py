"""Tests de la detección de enlaces de YouTube (`transcript.find_youtube_url` / `extract_video_id`).

`find_youtube_url` es lo que usa el bot: al usuario le sobra con pegar el enlace dentro de
un mensaje cualquiera ("resúmeme esto: <enlace> gracias"), así que hay que encontrarlo en
texto arbitrario. `extract_video_id` es la validación estricta de una URL.

Funciones puras, sin red.
"""

import pytest

from macrobot.transcript import extract_video_id, find_youtube_url

VIDEO_ID = "dQw4w9WgXcQ"

VALID_URLS = [
    # Forma canónica y sus variantes de host.
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", VIDEO_ID),
    ("https://youtube.com/watch?v=dQw4w9WgXcQ", VIDEO_ID),
    ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", VIDEO_ID),
    ("http://www.youtube.com/watch?v=dQw4w9WgXcQ", VIDEO_ID),
    ("www.youtube.com/watch?v=dQw4w9WgXcQ", VIDEO_ID),  # sin esquema
    # Enlace corto.
    ("https://youtu.be/dQw4w9WgXcQ", VIDEO_ID),
    ("https://youtu.be/dQw4w9WgXcQ?t=1200", VIDEO_ID),
    # Directos y otras rutas.
    ("https://www.youtube.com/live/dQw4w9WgXcQ", VIDEO_ID),
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", VIDEO_ID),
    ("https://www.youtube.com/embed/dQw4w9WgXcQ", VIDEO_ID),
    # Parámetros extra, antes y después de v=.
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s", VIDEO_ID),
    ("https://www.youtube.com/watch?feature=share&v=dQw4w9WgXcQ", VIDEO_ID),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL1234567890&index=3", VIDEO_ID),
]

INVALID_URLS = [
    "https://example.com/watch?v=dQw4w9WgXcQ",  # otro dominio
    "https://notyoutube.com/watch?v=dQw4w9WgXcQ",  # dominio que solo lo parece
    "https://vimeo.com/123456789",
    "https://www.youtube.com/",  # sin vídeo
    "https://www.youtube.com/@algun-canal",  # un canal, no un vídeo
    "https://www.youtube.com/watch?v=tooshort",  # el ID no tiene 11 caracteres
    "no soy una url",
    "",
]


# --------------------------------------------------------------------------------------
# extract_video_id
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(("url", "expected"), VALID_URLS)
def test_extract_video_id_accepts_every_youtube_url_shape(url, expected):
    assert extract_video_id(url) == expected


@pytest.mark.parametrize("url", INVALID_URLS)
def test_extract_video_id_rejects_anything_that_is_not_a_youtube_video(url):
    with pytest.raises(ValueError, match="YouTube"):
        extract_video_id(url)


def test_extract_video_id_preserves_case_and_dashes_of_the_id():
    assert extract_video_id("https://youtu.be/a-B_c1D2e3F") == "a-B_c1D2e3F"


# --------------------------------------------------------------------------------------
# find_youtube_url: el enlace viene dentro de un mensaje de Telegram
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(("url", "_expected"), VALID_URLS)
def test_find_youtube_url_finds_a_bare_url(url, _expected):
    assert find_youtube_url(url) == url


def test_find_youtube_url_finds_the_url_inside_a_sentence():
    text = "oye, resúmeme https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s porfa"

    assert find_youtube_url(text) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s"


def test_find_youtube_url_ignores_trailing_punctuation():
    text = "mira este vídeo: https://youtu.be/dQw4w9WgXcQ."

    assert find_youtube_url(text) == "https://youtu.be/dQw4w9WgXcQ"


def test_find_youtube_url_finds_a_url_on_its_own_line():
    text = "Resumen porfa\nhttps://youtu.be/dQw4w9WgXcQ\nGracias"

    assert find_youtube_url(text) == "https://youtu.be/dQw4w9WgXcQ"


def test_find_youtube_url_returns_the_first_url_when_there_are_several():
    text = "https://youtu.be/aaaaaaaaaaa y también https://youtu.be/bbbbbbbbbbb"

    assert find_youtube_url(text) == "https://youtu.be/aaaaaaaaaaa"


@pytest.mark.parametrize("text", INVALID_URLS)
def test_find_youtube_url_returns_none_when_there_is_no_youtube_link(text):
    assert find_youtube_url(text) is None


def test_find_youtube_url_returns_none_for_a_message_without_any_link():
    assert find_youtube_url("hola, ¿qué tal?") is None


# --------------------------------------------------------------------------------------
# Las dos funciones encajan: lo que encuentra una, lo valida la otra
# --------------------------------------------------------------------------------------


def test_the_url_found_in_a_message_yields_its_video_id():
    text = "resúmeme https://m.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123 gracias"

    url = find_youtube_url(text)

    assert url is not None
    assert extract_video_id(url) == VIDEO_ID
