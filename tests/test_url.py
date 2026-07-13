"""Tests de la extracción del ID de vídeo a partir de la URL (`transcript.extract_video_id`).

De momento solo fijan el contrato del andamiaje. `VALID_URLS` e `INVALID_URLS` ya listan
los casos que deberá cubrir la implementación: se convertirán en tests parametrizados en
cuanto `extract_video_id` deje de ser un stub.
"""

import pytest

from macrobot.transcript import extract_video_id

VALID_URLS = [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/live/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
]

INVALID_URLS = [
    "https://example.com/watch?v=dQw4w9WgXcQ",
    "https://www.youtube.com/",
    "no soy una url",
    "",
]


def test_url_fixtures_are_defined():
    """Test trivial: los casos de prueba están sobre la mesa antes de implementar."""
    assert len(VALID_URLS) == 5
    assert all(expected == "dQw4w9WgXcQ" for _, expected in VALID_URLS)
    assert INVALID_URLS


# TODO: convertir en `@pytest.mark.parametrize("url,expected", VALID_URLS)`.
def test_extract_video_id_not_implemented_yet():
    with pytest.raises(NotImplementedError):
        extract_video_id(VALID_URLS[0][0])
