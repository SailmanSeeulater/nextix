FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 nextix

WORKDIR /app

# Install dependencies first so source edits don't bust the layer cache.
COPY pyproject.toml ./
RUN mkdir -p nextix && touch nextix/__init__.py \
 && pip install -e ".[dev]"

COPY . .

RUN mkdir -p /data/artifacts && chown -R nextix:nextix /app /data
USER nextix

EXPOSE 8000
CMD ["uvicorn", "nextix.main:app", "--host", "0.0.0.0", "--port", "8000"]
