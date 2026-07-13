"""Tests de `macrobot.prompts`.

Los prompts son texto de cara al usuario (el resumen sale de ellos), así que estos tests
fijan su contrato: que los apartados del esquema de extracción y los encabezados del
informe final estén todos, y que los helpers de formato —funciones puras— monten el
mensaje de usuario sin perder nada.
"""

from macrobot.prompts import (
    MAP_SYSTEM,
    REDUCE_SYSTEM,
    build_map_user_prompt,
    build_reduce_user_prompt,
)

MAP_SECTIONS = [
    "### Tema del bloque",
    "### Tesis / ideas centrales",
    "### Argumentos y razonamiento",
    "### Datos y cifras citados",
    "### Predicciones / escenarios",
    "### Activos / mercados / tickers",
    "### Política monetaria / bancos centrales",
    "### Citas textuales destacadas",
    "### Términos y conceptos clave",
]

REDUCE_HEADINGS = [
    "## Resumen ejecutivo",
    "## Tesis principales",
    "## Datos y cifras clave",
    "## Predicciones y escenarios",
    "## Implicaciones para mercados / activos",
    "## Riesgos y puntos de debate",
    "## Recorrido por bloques",
]


# --------------------------------------------------------------------------------------
# MAP_SYSTEM: el esquema de extracción
# --------------------------------------------------------------------------------------


def test_map_system_lists_every_extraction_section():
    for section in MAP_SECTIONS:
        assert section in MAP_SYSTEM, f"falta el apartado {section!r}"


def test_map_system_orders_extraction_not_paraphrase():
    lowered = MAP_SYSTEM.lower()

    assert "extraer" in lowered
    assert "parafrasear" in lowered  # dice explícitamente lo que NO hay que hacer


def test_map_system_forbids_inventing_and_demands_literal_numbers():
    lowered = MAP_SYSTEM.lower()

    assert "no inventes" in lowered
    assert "literal" in lowered
    assert "sin redondear" in lowered


def test_map_system_warns_that_the_transcript_may_come_raw():
    lowered = MAP_SYSTEM.lower()

    assert "sin puntuación" in lowered
    assert "contexto" in lowered


def test_map_system_demands_compact_output_and_omitting_empty_sections():
    lowered = MAP_SYSTEM.lower()

    assert "compacta" in lowered
    assert "omite" in lowered


def test_map_system_anchors_the_output_to_the_block_timespan():
    assert "rango temporal" in MAP_SYSTEM.lower()


# --------------------------------------------------------------------------------------
# REDUCE_SYSTEM: el informe final
# --------------------------------------------------------------------------------------


def test_reduce_system_lists_every_report_heading():
    for heading in REDUCE_HEADINGS:
        assert heading in REDUCE_SYSTEM, f"falta el encabezado {heading!r}"


def test_reduce_system_headings_appear_in_the_report_order():
    positions = [REDUCE_SYSTEM.index(heading) for heading in REDUCE_HEADINGS]

    assert positions == sorted(positions)


def test_reduce_system_groups_by_topic_not_by_block():
    lowered = REDUCE_SYSTEM.lower()

    assert "agrupa por tema" in lowered
    assert "no inventes" in lowered


def test_reduce_system_writes_the_report_in_spanish():
    assert "español" in REDUCE_SYSTEM.lower()


def test_reduce_system_describes_the_per_block_walkthrough_format():
    assert "[mm:ss]" in REDUCE_SYSTEM.lower()


# --------------------------------------------------------------------------------------
# Helpers de formato (funciones puras)
# --------------------------------------------------------------------------------------


def test_build_map_user_prompt_carries_the_timespan_and_the_raw_text():
    prompt = build_map_user_prompt("00:10:00 - 00:20:00", "texto crudo del bloque")

    assert "[00:10:00 - 00:20:00]" in prompt
    assert "texto crudo del bloque" in prompt


def test_build_map_user_prompt_puts_the_timespan_before_the_text():
    prompt = build_map_user_prompt("00:00:00 - 00:10:00", "lo que se dijo")

    assert prompt.index("00:00:00 - 00:10:00") < prompt.index("lo que se dijo")


def test_build_reduce_user_prompt_carries_the_title_and_every_extraction():
    prompt = build_reduce_user_prompt(
        "Perspectivas macro 2026", ["primera extracción", "segunda extracción"]
    )

    assert "Perspectivas macro 2026" in prompt
    assert "primera extracción" in prompt
    assert "segunda extracción" in prompt


def test_build_reduce_user_prompt_numbers_the_blocks_in_chronological_order():
    prompt = build_reduce_user_prompt("t", ["extracción A", "extracción B", "extracción C"])

    assert (
        prompt.index("extracción A") < prompt.index("extracción B") < prompt.index("extracción C")
    )
    assert "Bloque 1" in prompt
    assert "Bloque 3" in prompt
