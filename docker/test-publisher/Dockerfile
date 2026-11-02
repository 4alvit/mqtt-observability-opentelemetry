FROM python:3.15-slim@sha256:94d3426f1def7db4a106a232ac121bfd975393e5dfd40a619b955fae45fe7913

WORKDIR /app

COPY publisher.py .
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# Services use unprivileged ports and do not need writable application files.
USER 65532:65532

CMD ["python", "publisher.py"]
