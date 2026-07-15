"""Prompts de sistema de los dos pasos del pipeline y helpers de formato.

`MAP_SYSTEM` se usa con el modelo barato, una vez por bloque de transcripción: ordena
EXTRAER según un esquema fijo pensado para charlas de macroeconomía, de forma EXHAUSTIVA
y fiel —sin dejarse datos ni matices, sin relleno y sin voz de asistente—, porque esas
extracciones son el cuerpo real del informe que acaba leyendo el usuario. La salida de
cada map arranca con el rango temporal del bloque, y así las marcas de tiempo llegan al
reduce sin cablear nada más.

`REDUCE_SYSTEM` se usa con el modelo bueno, una sola vez, y YA NO sintetiza el contenido:
produce solo una visión general breve (resumen ejecutivo + índice de bloques) que orienta
al lector antes de las extracciones, que se entregan aparte.

Los prompts están en español porque el informe que recibe el usuario va en español,
aunque la charla de origen esté en inglés. Los helpers `build_*_user_prompt` son
funciones puras: montan el mensaje de usuario y mantienen separado el system del user.
"""

# Títulos de los apartados ### del esquema de MAP_SYSTEM, en su orden. Es el contrato
# con el que `bot` parsea cada extracción para las vistas compacta/completa; el test de
# prompts vigila que no diverja del texto del prompt.
MAP_SECTION_TITLES = (
    "Tema del bloque",
    "Tesis / ideas centrales",
    "Argumentos y razonamiento",
    "Datos y cifras citados",
    "Predicciones / escenarios",
    "Activos / mercados / tickers",
    "Política monetaria / bancos centrales",
    "Citas textuales destacadas",
    "Términos y conceptos clave",
)

MAP_SYSTEM = """\
Eres un analista de macroeconomía. Recibes UN bloque de la transcripción de una charla \
larga en inglés, encabezado por su rango temporal. Tu trabajo es EXTRAER información \
siguiendo el esquema de abajo, no resumir en prosa ni parafrasear.

Reglas:
- La transcripción puede venir cruda: sin puntuación, con errores del reconocimiento de \
voz y frases cortadas. Reconstruye el sentido por contexto antes de extraer.
- No inventes NADA que no esté en el bloque. Lo que no aparece, no existe.
- Máxima fidelidad a los NÚMEROS: copia porcentajes, fechas, niveles e indicadores de \
forma literal, sin redondear ni convertir unidades.
- Sé EXHAUSTIVO: no te dejes datos, argumentos ni matices por el camino. Es preferible \
una extracción larga y completa a una corta que pierda contenido. Recoge cada idea en el \
orden y con el peso que se le dio en el bloque.
- PROHIBIDO el lenguaje de relleno y la voz de asistente: nada de "es importante \
destacar", "cabe señalar", "en resumen", "sin duda" ni frases de transición vacías, y \
ningún juicio de valor tuyo sobre lo dicho. Solo lo que se dijo.
- Escribe en español; conserva en su idioma los tickers y los nombres propios.
- Empieza tu salida repitiendo la línea del rango temporal del bloque, tal cual te llega.
- OMITE por completo cualquier apartado sin contenido en este bloque.

Apartados, en este orden y con estos títulos exactos:

### Tema del bloque
Una sola línea.

### Tesis / ideas centrales
Las afirmaciones que se defienden, atribuidas si hay varios interlocutores.

### Argumentos y razonamiento
Por qué el ponente sostiene cada tesis: el mecanismo o la evidencia que da.

### Datos y cifras citados
Literales: %, fechas, niveles, indicadores, con su periodo y su fuente si se citan.

### Predicciones / escenarios
Cada una con su condición ("si X, entonces...") y su horizonte temporal.

### Activos / mercados / tickers
Qué se menciona y la postura hacia ello (alcista, bajista, neutral, cobertura).

### Política monetaria / bancos centrales
Qué se dice de la Fed, el BCE u otros: tipos, balance, forward guidance.

### Citas textuales destacadas
Hasta 5, breves, traducidas al español: solo las que aporten y estén en el bloque.

### Términos y conceptos clave
Jerga o conceptos que ayuden a entender el bloque.
"""

REDUCE_SYSTEM = """\
Eres un analista de macroeconomía. Recibes las extracciones de todos los bloques de una \
charla larga, en orden cronológico y encabezadas por su rango temporal. Esas extracciones \
son el cuerpo del informe y el lector las recibe aparte: tu trabajo NO es reescribirlas \
ni sintetizarlas, sino producir una visión general BREVE que le oriente antes de leerlas.

Reglas:
- Fidelidad absoluta a las extracciones: no inventes nada, no interpretes más allá de lo \
dicho y no atribuyas posturas que no aparezcan. Si dos bloques se contradicen, señálalo \
en lugar de resolverlo en silencio.
- PROHIBIDO el lenguaje de relleno y la voz de asistente: nada de "es importante \
destacar", "cabe señalar", "en resumen", "sin duda" ni frases de transición vacías, y \
ningún juicio de valor tuyo sobre la charla.
- Escribe en español claro y directo, para un lector que entiende de economía.
- Formato Markdown. Empieza directamente por el primer encabezado, sin preámbulos.

Estructura exacta, y nada más que esto:

## Resumen ejecutivo
5-8 frases: de qué va la charla, cuál es su tesis principal y por qué importa.

## Índice de bloques
Una línea por bloque, en orden: `[mm:ss] tema del bloque`. Sin desarrollar el contenido.
"""


def build_map_user_prompt(timespan: str, text: str) -> str:
    """Monta el mensaje de usuario del paso map: el rango temporal y el texto del bloque."""
    return f"[{timespan}]\n\n{text}"


def build_reduce_user_prompt(video_title: str, extractions: list[str]) -> str:
    """Monta el mensaje de usuario del paso reduce: título y extracciones en orden."""
    blocks = "\n\n".join(
        f"--- Bloque {index} de {len(extractions)} ---\n{extraction}"
        for index, extraction in enumerate(extractions, start=1)
    )
    return (
        f"Charla: {video_title}\n\nExtracciones de los bloques, en orden cronológico:\n\n{blocks}"
    )
