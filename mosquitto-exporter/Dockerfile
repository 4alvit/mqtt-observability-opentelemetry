# Mosquitto Exporter - Dockerfile

FROM python:3.14-slim@sha256:caaf356f40667c496d405780745b9ac25771c189a51dfcc42430d531ea09f8a2

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH=/app/src

COPY src/ ./src/

EXPOSE 9494

ENV PYTHONUNBUFFERED=1
ENV MQTT_UPSTREAM_HOST=mosquitto
ENV MQTT_UPSTREAM_PORT=1883
ENV PROMETHEUS_PORT=9494
ENV OTEL_EXPORTER_OTLP_ENDPOINT=http://otelcol:4317
ENV LOG_LEVEL=INFO

# Services use unprivileged ports and do not need writable application files.
USER 65532:65532

CMD ["python", "-m", "mosquitto_exporter"]
