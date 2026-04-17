FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir flask requests yt-dlp && \
    printf '%s\n' \
    '#!/bin/sh' \
    'set -eu' \
    'PY_BIN=""' \
    'if command -v python3 >/dev/null 2>&1; then PY_BIN="$(command -v python3)"; fi' \
    'if [ -z "$PY_BIN" ] && command -v python >/dev/null 2>&1; then PY_BIN="$(command -v python)"; fi' \
    'if [ -z "$PY_BIN" ] && [ -x /usr/local/bin/python ]; then PY_BIN="/usr/local/bin/python"; fi' \
    'if [ -z "$PY_BIN" ]; then echo "No Python interpreter found in container PATH" >&2; exit 127; fi' \
    'exec "$PY_BIN" -u /app/app.py' \
    > /usr/local/bin/start-song-downloader && chmod +x /usr/local/bin/start-song-downloader

COPY app.py /app/app.py
COPY templates /app/templates
COPY static /app/static

EXPOSE 8145

CMD ["/usr/local/bin/start-song-downloader"]
