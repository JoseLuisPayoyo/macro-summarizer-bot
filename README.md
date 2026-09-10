# macrobot

Bot de Telegram que resume vídeos largos de YouTube. Le mandas el enlace de una charla de
macroeconomía en inglés (1-2 horas) y te devuelve un resumen completo y estructurado en
español.

Obtiene los subtítulos con yt-dlp, trocea la transcripción por bloques de tiempo, extrae
lo relevante de cada bloque con un modelo barato y sintetiza el informe final con uno
mejor. Todas las llamadas a LLM van por OpenRouter, y el objetivo es quedarse por debajo
de **0,10 $ por vídeo**. Si el vídeo no tiene subtítulos, transcribe el audio con Whisper
en Groq.

> **Estado:** andamiaje. La estructura y las firmas están puestas, la lógica todavía no.

## Instalación

Necesitas [uv](https://docs.astral.sh/uv/). El propio uv se encarga de Python 3.12:

```bash
uv sync
```

## Configuración

Copia la plantilla y rellena tus claves:

```bash
cp .env.example .env
```

| Variable | Obligatoria | Qué es |
| --- | --- | --- |
| `TELEGRAM_TOKEN` | Sí | Token del bot, te lo da [@BotFather](https://t.me/BotFather). |
| `OPENROUTER_API_KEY` | Sí | Clave de [OpenRouter](https://openrouter.ai/keys). |
| `GROQ_API_KEY` | No | Solo para el plan B (Whisper) en vídeos sin subtítulos. |
| `MAP_MODEL` | No | Modelo barato del paso map. ID de [openrouter.ai/models](https://openrouter.ai/models). |
| `REDUCE_MODEL` | No | Modelo bueno del paso reduce. Mismo formato de ID. |
| `SUMMARY_LANG` | No | Idioma del resumen (`es` por defecto). |
| `CHUNK_MINUTES` | No | Tamaño de la ventana de troceo (10 por defecto). |
| `SUB_LANGS` | No | Idiomas de subtítulos a probar, por orden (`en,en-US,en-GB`). |

## Ejecución

```bash
uv run macrobot
```

Arranca en modo polling: no hace falta webhook ni URL pública. Abre Telegram, escríbele al
bot y mándale un enlace de YouTube.

## Despliegue

Para correr el bot 24/7 (por ejemplo en una VM) hay un `Dockerfile` y un
`docker-compose.yml`. El contenedor lee los secretos de `.env` en runtime (nunca se
hornean en la imagen) y arranca en modo polling, sin exponer puertos.

```bash
docker compose up --build        # prueba local: construye y arranca
docker compose logs -f           # ver los logs (rotados: 3 ficheros de 10 MB)
docker compose down              # parar
```

Actualizar tras cambios en el repo:

```bash
git pull && docker compose up -d --build
```

El servicio arranca con `restart: unless-stopped`, así que sobrevive a caídas del proceso
y a reinicios del host. La imagen es multi-arch (funciona en ARM64, p. ej. una VM Ampere).

## Desarrollo

```bash
uv run pytest         # tests
uv run ruff check .   # lint
uv run ruff format .  # formato
```

Los detalles de arquitectura y las convenciones del repo están en [CLAUDE.md](CLAUDE.md).
