FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY packages/moderation_shared /app/packages/moderation_shared
COPY apps/worker/requirements.txt /app/apps/worker/requirements.txt

RUN pip install --no-cache-dir \
    /app/packages/moderation_shared \
    && pip install --no-cache-dir -r /app/apps/worker/requirements.txt

COPY apps/worker/worker /app/apps/worker/worker

ENV PYTHONPATH=/app/apps/worker

CMD ["python", "-m", "worker.main"]
