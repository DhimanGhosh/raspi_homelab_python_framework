FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 DEBIAN_FRONTEND=noninteractive
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN /usr/local/bin/python3 -m pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8139
CMD ["/usr/local/bin/python3", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8139"]
