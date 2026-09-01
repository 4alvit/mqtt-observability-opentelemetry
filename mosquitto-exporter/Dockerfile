# Mosquitto Exporter - Dockerfile

FROM python:3.14-slim@sha256:656d12e70054d5fda18a045e2494c96701e9792dd1445f95b3d038df954f57e9

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

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