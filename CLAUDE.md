# CLAUDE.md

Referencia permanente del repositorio. Léela antes de tocar código.

## Qué es esto

Un bot de Telegram que resume vídeos largos de YouTube: charlas de macroeconomía en
inglés, de 1 a 2 horas. El usuario le manda un enlace por Telegram y recibe de vuelta un
resumen completo y estructurado **en español**.

El problema que resuelve: una charla de dos horas no cabe en una sola llamada a un LLM (y
si cupiera, saldría cara y el modelo se dejaría cosas por el camino). La solución es un
map-reduce sobre la transcripción.

## Arquitectura del pipeline

```
URL de YouTube
   │
   ├─ 1. TRANSCRIPCIÓN  (transcript.py)
   │     yt-dlp descarga los subtítulos (manuales > automáticos). Gratis.
   │     Se parsea el VTT: fuera cabeceras, etiquetas y el solapamiento de cues.
   │     │
   │     └─ Plan B si el vídeo NO tiene subtítulos  (whisper.py)
   │           yt-dlp baja solo el audio -> Whisper en Groq -> segmentos.
   │           Es la vía de pago: solo como respaldo.
   │
   ├─ 2. TROCEO  (chunking.py)
   │     Los segmentos se agrupan en bloques de ~CHUNK_MINUTES minutos (10 por defecto).
   │     Se trocea por TIEMPO, no por tokens: así cada bloque tiene un rango temporal
   │     citable en el resumen y el coste por vídeo es predecible.
   │
   ├─ 3. MAP  (pipeline.run_map + prompts.MAP_SYSTEM + llm.py)
   │     Una llamada por bloque al modelo BARATO (MAP_MODEL), en paralelo.
   │     No resume en prosa: hace extracción estructurada según un esquema macro
   │     (tesis, datos y cifras, previsiones, riesgos, citas). Comprime y tira la paja.
   │
   ├─ 4. REDUCE  (pipeline.run_reduce + prompts.REDUCE_SYSTEM + llm.py)
   │     UNA sola llamada al modelo BUENO (REDUCE_MODEL) con todas las extracciones.
   │     Sintetiza el informe final agrupando por tema, no por bloque.
   │
   └─ 5. ENTREGA  (bot.py)
         El resumen se trocea a 4096 caracteres (límite de Telegram) y se envía.
```

Todas las llamadas a LLM van por **OpenRouter** (`llm.py` es el único módulo que habla con
un LLM). El bot corre en **modo polling**: no hay webhook ni URL pública, basta con
ejecutar el proceso.

## Objetivo de coste

**Por debajo de 0,10 $ por vídeo.** Es la restricción de diseño que explica casi todo lo
anterior. Al tocar el pipeline, ten presente:

- Los subtítulos de yt-dlp son gratis; Whisper en Groq no. Whisper es el plan B, nunca la
  vía por defecto.
- El paso map domina el coste (una charla de 2 h son ~12 llamadas): ahí va el modelo
  barato, y por eso el prompt map extrae en vez de parafrasear.
- El paso reduce se llama una vez: ahí sí compensa el modelo bueno.
- Subir `CHUNK_MINUTES` reduce el número de llamadas map pero pierde detalle. 10 minutos
  es el punto de equilibrio actual.
- `pipeline.SummaryResult` devuelve el uso de tokens: úsalo para comprobar el coste real
  antes de dar por buena una optimización.

## Stack y convenciones

- **Python 3.12**. Entorno y dependencias con **uv** (`pyproject.toml`; no hay
  `requirements.txt` ni lo va a haber).
- **Layout src/**: el paquete vive en `src/macrobot/`.
- **ruff** para lint y formato; **pytest** para tests. Ambos se configuran dentro de
  `pyproject.toml`.
- **Type hints en todo el código** (ruff lo exige con la regla `ANN`).
- **Idiomas**: los identificadores (variables, funciones, clases) en **inglés**; los textos
  de cara al usuario —mensajes de Telegram y prompts de resumen— en **español**.
- **Configuración**: `pydantic-settings` leyendo de variables de entorno o de `.env`.
  Ningún módulo lee `os.environ` directamente: todo pasa por `config.Settings`.

## Estructura

```
src/macrobot/
  __init__.py
  config.py       Settings con pydantic-settings (.env -> objeto tipado)
  transcript.py   yt-dlp: descarga de subtítulos y parseo/limpieza del VTT
  chunking.py     troceo de la transcripción por ventanas de tiempo
  llm.py          cliente de OpenRouter con reintentos y backoff
  whisper.py      plan B: transcripción del audio con Groq
  prompts.py      MAP_SYSTEM y REDUCE_SYSTEM (esquema de extracción macro)
  pipeline.py     orquesta el map-reduce de principio a fin
  bot.py          bot de Telegram (polling) y punto de entrada
tests/
  test_transcript.py
  test_chunking.py
  test_url.py
.env.example      plantilla de configuración
pyproject.toml    dependencias + config de ruff y pytest
```

Dependencia entre capas: `bot` -> `pipeline` -> {`transcript`|`whisper`, `chunking`,
`llm`, `prompts`} -> `config`. **`bot.py` no debe saber nada de yt-dlp ni de OpenRouter**,
y `pipeline.py` no debe saber nada de Telegram (por eso el progreso se reporta con un
callback).

## Cómo ejecutar

```bash
uv sync                      # instala el entorno (crea .venv)
cp .env.example .env         # y rellena TELEGRAM_TOKEN y OPENROUTER_API_KEY

uv run macrobot              # arranca el bot en modo polling
uv run python -m macrobot.bot   # equivalente

uv run pytest                # tests
uv run ruff check .          # lint
uv run ruff format .         # formato
```

Añadir una dependencia: `uv add <paquete>` (y `uv add --dev <paquete>` para las de
desarrollo). No edites a mano la lista de `dependencies` del `pyproject.toml`.

## Estado actual

**Andamiaje.** Los módulos tienen sus docstrings y las firmas previstas, pero el cuerpo de
las funciones es `raise NotImplementedError`. Los tests actuales fijan ese contrato (y
llevan `TODO` señalando los casos reales que deben sustituirlos); al implementar un módulo,
reemplaza sus tests `*_not_implemented_yet` por pruebas de verdad.
