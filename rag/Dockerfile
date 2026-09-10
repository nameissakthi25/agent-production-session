# Installs from uv.lock, so the container runs the resolution you tested.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    # fastembed downloads the ONNX model on first use. Point its cache at a
    # volume, or every container restart re-downloads 130MB.
    FASTEMBED_CACHE_PATH=/app/.cache/fastembed

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY ingest/ ./ingest/
COPY ragbot/ ./ragbot/
COPY corpus/ ./corpus/
COPY app.py chainlit.md ./
COPY .chainlit/config.toml ./.chainlit/config.toml

EXPOSE 8002

CMD ["chainlit", "run", "app.py", "--host", "0.0.0.0", "--port", "8002", "--headless"]
