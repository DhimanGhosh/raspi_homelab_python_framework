FROM python:3.11-slim

WORKDIR /opt/personal-library

COPY requirements.txt ./requirements.txt

RUN apt-get update \
    && apt-get install -y curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt

COPY app /opt/personal-library/app
COPY static /opt/personal-library/static
COPY start.sh /start.sh

RUN ["chmod", "+x", "/start.sh"]
RUN ["mkdir", "-p", "/opt/personal-library/data"]

EXPOSE 8132

CMD ["/start.sh"]
