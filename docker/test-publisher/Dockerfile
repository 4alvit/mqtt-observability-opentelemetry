FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6

WORKDIR /app

COPY publisher.py .
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# Services use unprivileged ports and do not need writable application files.
USER 65532:65532

CMD ["python", "publisher.py"]
