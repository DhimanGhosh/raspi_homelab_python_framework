FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir flask mutagen

COPY app.py /app/app.py
COPY app /app/app
COPY templates /app/templates
COPY static /app/static

EXPOSE 8140
CMD ["python", "/app/app.py"]
