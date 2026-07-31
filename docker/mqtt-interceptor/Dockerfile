# MQTT Interceptor - Dockerfile

FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock* ./
RUN uv pip install --system -e .

COPY src/ ./src/
COPY config.yaml.example config.yaml

EXPOSE 1884 9464

ENV PYTHONUNBUFFERED=1
ENV MQTT_LISTEN_HOST=0.0.0.0
ENV MQTT_LISTEN_PORT=1884
ENV MQTT_UPSTREAM_HOST=mosquitto
ENV MQTT_UPSTREAM_PORT=1883
ENV OTEL_EXPORTER_OTLP_ENDPOINT=http://otelcol:4317
ENV LOG_LEVEL=INFO

CMD ["python", "-m", "mqtt_interceptor"]