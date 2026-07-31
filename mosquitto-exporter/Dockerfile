# Mosquitto Exporter - Dockerfile

FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock* ./
RUN uv pip install --system -e .

COPY src/ ./src/

EXPOSE 9494

ENV PYTHONUNBUFFERED=1
ENV MQTT_UPSTREAM_HOST=mosquitto
ENV MQTT_UPSTREAM_PORT=1883
ENV PROMETHEUS_PORT=9494
ENV OTEL_EXPORTER_OTLP_ENDPOINT=http://otelcol:4317
ENV LOG_LEVEL=INFO

CMD ["python", "-m", "mosquitto_exporter"]