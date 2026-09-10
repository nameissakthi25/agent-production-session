# The image installs from uv.lock, so what runs in the container is the same
# resolution that ran on your machine. That is the whole point of committing a
# lockfile -- an image built from loose version ranges is a different program.

FROM python:3.12-slim

# uv itself, from its own image. Pinned: `latest` in a Dockerfile is how a build
# that worked last week quietly stops working.
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

# Dependencies before code, so editing app.py does not reinstall spaCy.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY bot/ ./bot/
COPY app.py chainlit.md ./
COPY .chainlit/config.toml ./.chainlit/config.toml

EXPOSE 8001

# --host 0.0.0.0 or the container answers only itself. --headless because there
# is no browser in here for it to open.
CMD ["chainlit", "run", "app.py", "--host", "0.0.0.0", "--port", "8001", "--headless"]
