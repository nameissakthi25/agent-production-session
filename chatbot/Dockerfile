FROM python:3.11-slim

WORKDIR /app

# Requirements first, so a code change does not reinstall the world.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ ./bot/
COPY app.py chainlit.md ./
COPY .chainlit/ ./.chainlit/

EXPOSE 8001

# --host 0.0.0.0 or the container answers only itself.
CMD ["chainlit", "run", "app.py", "--host", "0.0.0.0", "--port", "8001", "--headless"]
