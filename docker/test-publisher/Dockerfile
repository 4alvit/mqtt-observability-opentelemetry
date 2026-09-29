FROM python:3.14-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d

WORKDIR /app

COPY publisher.py .
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# Services use unprivileged ports and do not need writable application files.
USER 65532:65532

CMD ["python", "publisher.py"]
