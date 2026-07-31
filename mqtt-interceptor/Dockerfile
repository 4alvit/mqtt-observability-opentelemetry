# MQTT Interceptor - Dockerfile

FROM python:3.14-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock* ./
RUN uv pip install --system -e .

COPY src/ ./src/

EXPOSE 9464

ENV PYTHONUNBUFFERED=1
ENV MQTT_UPSTREAM_HOST=mosquitto
ENV MQTT_UPSTREAM_PORT=1883
ENV OTEL_EXPORTER_OTLP_ENDPOINT=http://otelcol:4317
ENV LOG_LEVEL=INFO

CMD ["python", "-m", "mqtt_interceptor"]