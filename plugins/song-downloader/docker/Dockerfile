FROM python:3.11

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir flask requests yt-dlp

COPY app.py /app/app.py
COPY templates /app/templates
COPY static /app/static

EXPOSE 8145

CMD ["/usr/local/bin/python3", "/app/app.py"]
