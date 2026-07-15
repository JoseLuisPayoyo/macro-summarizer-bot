"""Tests de `macrobot.prompts`.

Los prompts son texto de cara al usuario (el informe sale de ellos), así que estos tests
fijan su contrato: que los apartados del esquema de extracción estén todos (el map es
material intermedio para el reduce), que el reduce produzca el informe completo con los
encabezados exactos que `bot.py` parsea (Panorama, un `## [mm:ss]` por bloque y el cierre
de tesis y conclusiones) sin repetir temas ya tratados, que ambos prohíban el lenguaje de
relleno, y que los helpers de formato —funciones puras— monten el mensaje sin perder nada.
"""

from macrobot.prompts import (
    MAP_SECTION_TITLES,
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
]

# Apartados retirados del esquema map (citas y glosario ya no aportan al reduce).
OLD_MAP_SECTIONS = [
    "### Citas textuales destacadas",
    "### Términos y conceptos clave",
]

# El contrato del informe del reduce, tal como lo parsea bot.py.
REDUCE_HEADINGS = [
    "## Panorama",
    "## Tesis y conclusiones",
    "### Tesis principales",
    "### Conclusiones",
    "### Tesis de inversión",
]

# Estructuras viejas del reduce que no deben volver (se comprueban línea a línea:
# "## Tesis principales" es substring de "### Tesis principales", que sí existe).
OLD_REDUCE_HEADINGS = [
    "## Resumen ejecutivo",
    "## Índice de bloques",
    "## Tesis principales",
    "## Datos y cifras clave",
    "## Predicciones y escenarios",
    "## Implicaciones para mercados / activos",
    "## Riesgos y puntos de debate",
    "## Recorrido por bloques",
]

# Muletillas de "voz de IA" que ambos prompts deben prohibir por su nombre.
FILLER_PHRASES = [
    "es importante destacar",
    "cabe señalar",
    "en resumen",
    "sin duda",
]


# --------------------------------------------------------------------------------------
# MAP_SYSTEM: el esquema de extracción
# --------------------------------------------------------------------------------------


def test_map_system_lists_every_extraction_section():
    for section in MAP_SECTIONS:
        assert section in MAP_SYSTEM, f"falta el apartado {section!r}"


def test_map_section_titles_constant_matches_the_fixed_schema():
    # `bot` parsea las extracciones con esta constante: si diverge del esquema, el
    # informe por bloques se quedaría en blanco en silencio.
    assert [f"### {title}" for title in MAP_SECTION_TITLES] == MAP_SECTIONS


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


def test_map_system_demands_exhaustive_ideas_and_omitting_empty_sections():
    lowered = MAP_SYSTEM.lower()

    assert "exhaustivo" in lowered
    # La regla vieja de preferir extracciones largas no debe volver.
    assert "extracción larga" not in lowered
    assert "omite" in lowered


def test_map_system_is_ruthless_with_residual_content():
    lowered = MAP_SYSTEM.lower()

    assert "anécdotas" in lowered
    assert "digresiones" in lowered
    assert "conceptos básicos" in lowered  # el lector sabe de macro: no se le explica nada


def test_map_system_dropped_the_quotes_and_glossary_sections():
    for section in OLD_MAP_SECTIONS:
        assert section not in MAP_SYSTEM, f"el apartado retirado {section!r} ha vuelto"


def test_map_system_forbids_filler_and_ai_voice():
    lowered = MAP_SYSTEM.lower()

    assert "relleno" in lowered
    for phrase in FILLER_PHRASES:
        assert phrase in lowered, f"debe prohibir por su nombre {phrase!r}"


def test_map_system_anchors_the_output_to_the_block_timespan():
    assert "rango temporal" in MAP_SYSTEM.lower()


# --------------------------------------------------------------------------------------
# REDUCE_SYSTEM: el informe final
# --------------------------------------------------------------------------------------


def test_reduce_system_lists_every_report_heading():
    for heading in REDUCE_HEADINGS:
        assert heading in REDUCE_SYSTEM, f"falta el encabezado {heading!r}"


def test_reduce_system_headings_appear_in_order():
    positions = [REDUCE_SYSTEM.index(heading) for heading in REDUCE_HEADINGS]

    assert positions == sorted(positions)


def test_reduce_system_describes_the_per_block_heading_format():
    assert "## [mm:ss]" in REDUCE_SYSTEM


def test_reduce_system_dropped_the_old_structures():
    lines = REDUCE_SYSTEM.splitlines()
    for heading in OLD_REDUCE_HEADINGS:
        assert heading not in lines, f"el encabezado viejo {heading!r} ha vuelto"


def test_reduce_system_forbids_repeating_what_earlier_blocks_already_covered():
    lowered = REDUCE_SYSTEM.lower()

    assert "no lo repitas" in lowered
    assert "ya tratado en [mm:ss]" in lowered  # la referencia que sustituye a la repetición


def test_reduce_system_keeps_data_inside_ideas_not_in_lists():
    assert "listas de cifras" in REDUCE_SYSTEM.lower()


def test_reduce_system_gathers_theses_and_conclusions_only_in_the_final_section():
    assert "solo en la sección final" in REDUCE_SYSTEM.lower()


def test_reduce_system_forbids_inventing_and_interpreting_beyond_what_was_said():
    lowered = REDUCE_SYSTEM.lower()

    assert "no inventes" in lowered
    assert "no interpretes" in lowered


def test_reduce_system_forbids_filler_and_ai_voice():
    lowered = REDUCE_SYSTEM.lower()

    assert "relleno" in lowered
    for phrase in FILLER_PHRASES:
        assert phrase in lowered, f"debe prohibir por su nombre {phrase!r}"


def test_reduce_system_writes_the_report_in_spanish():
    assert "español" in REDUCE_SYSTEM.lower()


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
