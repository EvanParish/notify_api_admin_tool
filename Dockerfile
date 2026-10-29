FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_PATH=data/app.db \
    PORT=8080 \
    UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

# Pinned uv version; bump deliberately.
COPY --from=ghcr.io/astral-sh/uv:0.9.18 /uv /usr/local/bin/uv

WORKDIR /app

# --frozen: install exactly what uv.lock says and fail rather than re-resolve.
# The venv lives outside /app so the ./app bind mount can never shadow it.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY main.py ./
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh && mkdir -p data

EXPOSE 8080

# The entrypoint chowns the bind-mounted data directory and then drops to APP_UID:APP_GID.
# It must start as root to do that, so do NOT add a USER directive or a compose `user:`.
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "main.py"]
