FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY src /app/src

RUN uv pip install --system --no-cache /app

ENV PYTHONUNBUFFERED=1 \
    ARCHIVE_DIR=/data/archive \
    DB_PATH=/data/splitter.db

VOLUME ["/data"]
EXPOSE 8000

CMD ["uvicorn", "actual_tx_splitter.main:app", "--host", "0.0.0.0", "--port", "8000"]
