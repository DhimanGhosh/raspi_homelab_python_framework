FROM python:3.11-slim

WORKDIR /opt/personal-library
RUN apt-get update \
    && apt-get install -y curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "fastapi>=0.115" "uvicorn[standard]>=0.30" "jinja2>=3.1" "requests>=2.32" "python-multipart>=0.0.9"

COPY app.py /opt/personal-library/app.py
COPY app /opt/personal-library/app
COPY templates /opt/personal-library/templates
COPY static /opt/personal-library/static

RUN mkdir -p /opt/personal-library/data

EXPOSE 8132
CMD ["python", "/opt/personal-library/app.py"]
