# Runtime-only preparation. Recovery source continues to be mounted read-only.
FROM python:3.15-slim@sha256:e97b88e416c3c01eb427b21fd8cd3ebf006e6ee38afcfcc3cae1c54ee2ab0a75
COPY requirements.lock /tmp/recovery-requirements.lock
RUN python -m pip install --no-cache-dir --only-binary=:all: --require-hashes \
    -r /tmp/recovery-requirements.lock \
    && python -m pip check \
    && rm /tmp/recovery-requirements.lock
ENV PYTHONDONTWRITEBYTECODE=1
USER 65532:65532
