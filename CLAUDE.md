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
   │     La capa de FIDELIDAD: es la única que ve la transcripción real. Extrae según
   │     un esquema macro (tesis, argumentos, datos, previsiones, activos, política
   │     monetaria), exhaustiva en ideas e implacable con la paja. Su salida es
   │     MATERIAL INTERMEDIO para el reduce: el usuario no la lee.
   │
   ├─ 4. REDUCE  (pipeline.run_reduce + prompts.REDUCE_SYSTEM + llm.py)
   │     UNA sola llamada al modelo BUENO (REDUCE_MODEL) con todas las extracciones.
   │     Produce EL INFORME que lee el usuario: Panorama, un apartado `## [mm:ss]` por
   │     bloque (deduplicando lo ya tratado: es el único punto que ve la charla entera)
   │     y el cierre "Tesis y conclusiones". Sus encabezados son un CONTRATO que
   │     bot.py parsea.
   │
   └─ 5. ENTREGA  (bot.py)
         En parse_mode=HTML: un mensaje con el Panorama, uno por bloque (encabezado en
         negrita + <blockquote expandable>, que Telegram colapsa solo) y el cierre con
         el pie. Todo troceado a 4096 caracteres (límite de Telegram).
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

**Fases 1-4 hechas: el bot es funcional de punta a punta** (para vídeos con subtítulos).
Implementados y cubiertos con tests:

- `transcript.py` — parseo de VTT, deduplicación de auto-subs y detección de URLs, más el
  wrapper de descarga con yt-dlp (fase 1).
- `chunking.py` — troceo por ventanas de tiempo (fase 1).
- `llm.py` — cliente asíncrono de OpenRouter: reintentos con backoff+jitter solo en
  fallos transitorios (429/5xx/transporte), respeta `Retry-After`, devuelve siempre
  `TokenUsage`, y un único cliente sirve a las llamadas en paralelo del map (fase 2).
- `prompts.py` — `MAP_SYSTEM` (esquema de extracción macro: exhaustivo en IDEAS,
  implacable con ejemplos/anécdotas/digresiones, sin explicar conceptos básicos — el
  lector es experto — y con prohibición explícita de relleno y "voz de IA"; material
  intermedio para el reduce, no se entrega) y `REDUCE_SYSTEM` (EL INFORME que lee el
  usuario: `## Panorama`, un `## [mm:ss] tema` por bloque SIN repetir lo ya tratado —
  puede referenciar "(ya tratado en [mm:ss])" —, datos solo dentro de las ideas que
  sostienen, y el cierre `## Tesis y conclusiones` con `### Tesis principales` /
  `### Conclusiones` / `### Tesis de inversión`, únicas secciones donde van las tesis),
  con los helpers puros `build_map_user_prompt` / `build_reduce_user_prompt`. Los
  encabezados del reduce son un CONTRATO: `bot.parse_report` los parsea, y los tests de
  prompts los fijan. `MAP_SECTION_TITLES` expone los títulos `###` del esquema map con
  su test guardián.
- `pipeline.py` — `summarize(url, client, settings, progress=None)` orquesta
  transcripción -> troceo -> map en paralelo (semáforo de `max_concurrency`) -> reduce, y
  devuelve `SummaryResult` con el uso de tokens desglosado map/reduce/total (fase 3) y
  `blocks: list[BlockSummary]` (`index` cronológico desde 0, `timespan` del Chunk y
  `extraction` tal cual salió del map): material intermedio que ya NO se entrega al
  usuario, pero se conserva en el resultado.
- `config.py` — completo, incluido `get_settings()` (única instancia, con `lru_cache`).
- `bot.py` — la aplicación de python-telegram-bot en polling y el entry point
  `uv run macrobot` (fase 4). La lógica pura (troceo a 4096, traducción de errores,
  coste estimado, pie del informe, `parse_report` y la construcción de los mensajes
  HTML) está separada de los handlers y cubierta por tests; los handlers son capa fina
  sin cobertura unitaria, a propósito.

Sigue como stub (`raise NotImplementedError`): `whisper.py`.

**DECISIÓN vigente: sin plan B de Whisper.** El pipeline NO cae a `whisper.py`; un vídeo
sin subtítulos propaga `NoSubtitlesError` y el bot responde `NO_SUBTITLES_MESSAGE`.
Si algún día se activa el plan B, el sitio es `pipeline.summarize` (capturar
`NoSubtitlesError` y llamar a `whisper.get_transcript_via_whisper`).

Decisiones de la capa de Telegram (fase 4):

- UN mensaje de estado por vídeo, que se EDITA con cada hito del `progress`; las
  ediciones fallidas (rate limit, texto idéntico) se ignoran con log en DEBUG — el
  progreso es cosmético y no debe tumbar un resumen de varios minutos.
- El informe se envía en `parse_mode=HTML` con CITAS EXPANDIBLES nativas: un mensaje con
  el Panorama, uno por bloque (`<b>[mm:ss] Tema</b>` + `<blockquote expandable>` con el
  contenido, que Telegram colapsa solo — sin botones ni callbacks) y el cierre "Tesis y
  conclusiones" con el pie. `bot.parse_report` parte `result.summary` por el contrato de
  encabezados del reduce; si el LLM se desvía (ni bloques ni cierre), se degrada al
  summary escapado y troceado: nunca se falla por formato.
- SEGURIDAD DEL HTML (lo que antes nos hacía enviar texto plano): TODO texto que venga
  del LLM pasa por `html.escape`; las únicas etiquetas vivas son las que pone el bot
  (`<b>`, `<blockquote expandable>`). Al trocear, el corte va sobre el texto CRUDO y el
  escape DESPUÉS — al revés partiría una entidad (`&amp;`) por la mitad. Cada trozo va
  en su propia cita expandible. Los mensajes de estado y de error siguen en texto plano.
- `split_message` (troceo del texto crudo) corta por párrafo > línea > espacio, nunca a
  media palabra, y evita partir bloques de código (paridad de vallas ```) mientras sea
  posible; la concatenación de los trozos reconstruye el original byte a byte.
- El coste del pie sale de los precios `*_USD_PER_MTOK` de `Settings` (por pata:
  entrada/salida de map y de reduce). Sin precios configurados, el pie omite el coste:
  nunca se inventa.
- El `OpenRouterClient` se crea una vez en `main` y se cierra en el `post_shutdown` de la
  `Application`; el detalle técnico de los errores va al log (`logging`), nunca al chat.
- La salida de cada map empieza con el rango temporal del bloque (lo exige `MAP_SYSTEM` y
  lo inyecta `build_map_user_prompt`): así el reduce recibe las marcas de tiempo sin
  cableado extra, y de ahí salen los encabezados `## [mm:ss] tema` del informe.

## Cómo se prueba

La regla que ordena el módulo `transcript`: **la lógica delicada es pura y la I/O es una
capa fina encima**. `parse_vtt`, `find_youtube_url`, `extract_video_id` y todo `chunking`
son funciones puras y están al 100% de cobertura; `fetch_subtitles` (que es quien llama a
yt-dlp) no se cubre con unitarios.

- **Ningún test unitario toca la red.** El único que lo haría lleva `@pytest.mark.integration`
  y se salta salvo que se ejecute con `MACROBOT_INTEGRATION=1`. En `test_llm.py`, httpx se
  intercepta con respx y el sleep del backoff se sustituye (`llm._sleep`) para que los
  reintentos no duerman: si tocas los reintentos, mantén ese alias.
- Los VTT de prueba están en `tests/fixtures/*.vtt` como ficheros de verdad, no como
  literales de Python: los auto-subs de YouTube contienen líneas que son un espacio suelto
  dentro del cue, y eso no sobrevive a un literal ni al formateador. El parser depende de
  ese detalle, así que el fixture tiene que ser fiel al byte.

```bash
uv run pytest                                    # unitarios (los de red se saltan)
uv run pytest --cov=macrobot --cov-report=term-missing
MACROBOT_INTEGRATION=1 uv run pytest -m integration   # el que sí descarga de YouTube
```

### Deduplicación de los subtítulos automáticos

Es la pieza con más trampa del repo. Los auto-subs de YouTube usan una **ventana rodante**:
la pantalla muestra dos líneas y cada cue reemite lo que ya se veía añadiendo un par de
palabras, con cues intermedios de ~10 ms idénticos al anterior. Reconstruir el texto es
quitar el solapamiento entre cada cue y el anterior (`transcript._new_words`), y hay que
hacerlo palabra a palabra: cuando la ventana pasa de página, el solapamiento no es ni todo
el cue anterior ni todo el nuevo.

El umbral `_MIN_ROLLING_OVERLAP = 2` no es arbitrario: que dos cues consecutivos compartan
**una** palabra en la frontera pasa constantemente en subtítulos manuales ("...lo llamo
crecimiento" / "crecimiento es lo que importa") y ahí las dos son buenas. A partir de dos
palabras seguidas ya no es casualidad. Si tocas esto, el test que lo vigila es
`test_parse_vtt_keeps_a_repeated_word_that_is_not_a_rolling_overlap`.
