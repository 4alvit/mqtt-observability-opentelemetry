FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY publisher.py .
RUN pip install --no-cache-dir paho-mqtt structlog

CMD ["python", "publisher.py"]