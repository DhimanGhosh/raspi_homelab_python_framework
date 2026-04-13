FROM python:3.11-slim

WORKDIR /app
COPY app.py /app/app.py
COPY templates /app/templates
COPY static /app/static

EXPOSE 8140
CMD ["python", "/app/app.py"]
