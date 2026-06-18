FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    "discord.py>=2.6" \
    "asyncpg>=0.30" \
    "msgpack>=1.1" \
    "PyYAML>=6.0" \
    "pydantic>=2" \
    "pydantic-settings>=2"

COPY config/ ./config/
COPY migrations/ ./migrations/
COPY assets/ ./assets/
COPY strife/ ./strife/

CMD ["python", "-m", "strife"]
