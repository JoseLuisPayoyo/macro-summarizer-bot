"""macrobot: bot de Telegram que resume vídeos largos de YouTube sobre macroeconomía.

El paquete se organiza en módulos de responsabilidad única que el pipeline encadena:

- `config`     -> configuración de la aplicación (pydantic-settings).
- `transcript` -> obtención de subtítulos con yt-dlp y limpieza del VTT.
- `chunking`   -> troceo de la transcripción en ventanas de tiempo.
- `llm`        -> cliente HTTP de OpenRouter con reintentos.
- `whisper`    -> plan B: transcripción del audio con Whisper en Groq.
- `prompts`    -> prompts de sistema de los pasos map y reduce.
- `pipeline`   -> orquestación del map-reduce de principio a fin.
- `bot`        -> capa de Telegram (polling) y punto de entrada.
"""

__version__ = "0.1.0"
