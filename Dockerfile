# syntax=docker/dockerfile:1

# ------------------------------------------------------------------------------------
# Builder: instala dependencias y el proyecto con uv, de forma reproducible desde uv.lock
# ------------------------------------------------------------------------------------
FROM python:3.12-slim AS builder

# uv, fijado por versión para builds reproducibles (coincide con el `uv_build` del
# pyproject). La imagen de uv es multi-arch e incluye arm64, así que este COPY vale también
# en la VM Ampere (ARM64) de Oracle Cloud: no hay binarios atados a x86.
COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /uvx /bin/

# - UV_COMPILE_BYTECODE: precompila .pyc en la imagen (arranque más rápido, sin recompilar).
# - UV_LINK_MODE=copy: el .venv queda autocontenido para poder copiarlo tal cual a la final.
# - UV_PYTHON_DOWNLOADS=0: usa el Python de la imagen base, no descarga otro intérprete.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Primero SOLO los manifiestos: esta capa se cachea y no se invalida al tocar el código.
# --no-install-project instala únicamente las dependencias; el proyecto va en el paso siguiente.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# Ahora el código y la instalación del propio proyecto (crea el entry point `macrobot`).
# README.md se copia porque el backend `uv_build` lo exige (es el `readme` del pyproject);
# no llega a la imagen final, que solo copia .venv y src/.
COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ------------------------------------------------------------------------------------
# Runtime: imagen ligera final, sin uv ni herramientas de compilación
# ------------------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# Usuario NO root: el bot no necesita privilegios (polling saliente, sin puertos abiertos).
RUN useradd --create-home --uid 1000 app

# NOTA: el plan B de Whisper (whisper.py) sigue siendo un stub y NO se usa hoy. Si algún día
# se activa, yt-dlp necesitará ffmpeg en runtime para extraer el audio antes de mandarlo a
# Groq; habría que añadirlo aquí:
#   RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
#       && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# El .venv trae el intérprete y el proyecto ya instalados. El shebang del script `macrobot`
# apunta a /app/.venv/bin/python, por eso mantenemos la ruta /app idéntica a la del builder.
# Copiamos también src/ para que un install editable siga resolviendo el paquete.
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/src /app/src

ENV PATH="/app/.venv/bin:$PATH"

USER app

# Modo polling: sin webhook ni puertos. Un único proceso; los logs van a stdout/stderr.
CMD ["macrobot"]
