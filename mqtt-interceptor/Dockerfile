# MQTT Interceptor - Dockerfile

FROM python:3.14-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH=/app/src

COPY src/ ./src/

EXPOSE 9464

ENV PYTHONUNBUFFERED=1
ENV MQTT_UPSTREAM_HOST=mosquitto
ENV MQTT_UPSTREAM_PORT=1883
ENV OTEL_EXPORTER_OTLP_ENDPOINT=http://otelcol:4317
ENV LOG_LEVEL=INFO

# Services use unprivileged ports and do not need writable application files.
USER 65532:65532

CMD ["python", "-m", "mqtt_interceptor"]
