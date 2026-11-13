FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

WORKDIR /app

COPY publisher.py .
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# Services use unprivileged ports and do not need writable application files.
USER 65532:65532

CMD ["python", "publisher.py"]
