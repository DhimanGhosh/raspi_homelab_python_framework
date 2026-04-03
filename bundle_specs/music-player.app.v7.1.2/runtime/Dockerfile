FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY . /app
EXPOSE 8140
CMD ["/usr/local/bin/python", "/app/app.py"]
