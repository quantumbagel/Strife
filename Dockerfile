FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

# Install [project].dependencies from pyproject.toml only, so this layer is cached until
# the dependency list changes. The strife package itself runs from /app via PYTHONPATH.
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']))" \
        > /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY strife/ ./strife/
RUN python -m strife.plugins sync-deps --builtins-only

COPY config/ ./config/
COPY changelog/ ./changelog/
COPY migrations/ ./migrations/
COPY assets/ ./assets/

CMD ["python", "-m", "strife"]
