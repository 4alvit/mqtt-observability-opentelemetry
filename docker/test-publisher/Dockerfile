FROM python:3.14-slim@sha256:a7fb1e634c4a578f9e0bd6327f11a3cde11b7a9395f48e24360c0988bcc5c2bc

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY publisher.py .
RUN pip install --no-cache-dir paho-mqtt structlog

CMD ["python", "publisher.py"]