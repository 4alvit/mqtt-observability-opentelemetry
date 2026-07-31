# MQTT Interceptor - Dockerfile

FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

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