FROM python:3.11-slim

WORKDIR /opt/offline-dictionary
RUN apt-get update \
    && apt-get install -y curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "fastapi>=0.115" "uvicorn[standard]>=0.30" "nltk>=3.9" "jinja2>=3.1"

COPY app.py /opt/offline-dictionary/app.py
COPY templates /opt/offline-dictionary/templates
COPY static /opt/offline-dictionary/static

RUN mkdir -p /opt/offline-dictionary/data

EXPOSE 8133
CMD ["python", "/opt/offline-dictionary/app.py"]
