FROM python:3.14-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY publisher.py .
RUN pip install --no-cache-dir paho-mqtt structlog

CMD ["python", "publisher.py"]