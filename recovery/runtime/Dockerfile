# Runtime-only preparation. Recovery source continues to be mounted read-only.
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928
COPY requirements.lock /tmp/recovery-requirements.lock
RUN python -m pip install --no-cache-dir --only-binary=:all: --require-hashes \
    -r /tmp/recovery-requirements.lock \
    && python -m pip check \
    && rm /tmp/recovery-requirements.lock
ENV PYTHONDONTWRITEBYTECODE=1
USER 65532:65532
