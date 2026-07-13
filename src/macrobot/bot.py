"""Bot de Telegram (modo polling) y punto de entrada de la aplicación.

Responsabilidad: la capa de interfaz. Recibe mensajes, detecta enlaces de YouTube, llama
a `pipeline.summarize_video` y devuelve el resumen. No contiene lógica de negocio.

Funciona en modo polling (`run_polling`), sin webhook ni URL pública: basta con ejecutar
el proceso en cualquier máquina con salida a internet.

Detalles de la capa de Telegram que resuelve este módulo:
- El resumen de una charla de dos horas supera con facilidad el límite de 4096 caracteres
  por mensaje, así que hay que trocearlo antes de enviarlo.
- El pipeline tarda minutos: se envía un mensaje de "procesando" y se va editando con el
  progreso que reporta el callback.

Todos los textos de cara al usuario van en español.
"""

from telegram import Update
from telegram.ext import ContextTypes

TELEGRAM_MAX_CHARS = 4096

WELCOME_MESSAGE = (
    "👋 Envíame un enlace de YouTube y te devuelvo un resumen estructurado en español.\n\n"
    "Está pensado para charlas largas de macroeconomía en inglés (1-2 horas). "
    "Tardo unos minutos: primero leo los subtítulos y luego resumo la charla por bloques."
)


def split_message(text: str, limit: int = TELEGRAM_MAX_CHARS) -> list[str]:
    """Trocea un texto largo en mensajes que quepan en el límite de Telegram.

    Corta por saltos de párrafo o de línea siempre que sea posible, para no partir una
    frase (ni una etiqueta de formato) por la mitad.
    """
    raise NotImplementedError


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler de `/start`: envía el mensaje de bienvenida."""
    raise NotImplementedError


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler del mensaje con el enlace: ejecuta el pipeline y envía el resumen.

    Avisa del progreso editando un mensaje de estado y, si algo falla, responde con un
    mensaje de error en español en lugar de dejar la excepción sin gestionar.
    """
    raise NotImplementedError


def build_application() -> object:
    """Construye la `Application` de python-telegram-bot y registra los handlers."""
    raise NotImplementedError


def main() -> None:
    """Punto de entrada: arranca el bot en modo polling (`uv run macrobot`)."""
    raise NotImplementedError


if __name__ == "__main__":
    main()
