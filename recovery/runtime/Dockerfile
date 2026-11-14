# Runtime-only preparation. Recovery source continues to be mounted read-only.
FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6
COPY requirements.lock /tmp/recovery-requirements.lock
RUN python -m pip install --no-cache-dir --only-binary=:all: --require-hashes \
    -r /tmp/recovery-requirements.lock \
    && python -m pip check \
    && rm /tmp/recovery-requirements.lock
ENV PYTHONDONTWRITEBYTECODE=1
USER 65532:65532
