FROM ghcr.io/astral-sh/uv:0.12.9 AS uv

FROM python:3.13-slim-bookworm AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY assets ./assets
COPY LICENSE LICENSE-agentic-katas ./
# Editable installation deliberately preserves /app/src + /app/assets. The
# renderer locates the font relative to its source file, not site-packages.
RUN uv sync --frozen --no-dev

FROM python:3.13-slim-bookworm AS runtime
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MALLOC_MMAP_THRESHOLD_=131072
# Keep glibc from retaining freed multi-megabyte Pillow buffers in thread arenas.
WORKDIR /app
# Pillow enables RAQM only when its dynamically loaded shaping libraries exist.
# Match the CLI/test environment instead of silently switching text layout.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libharfbuzz0b libfribidi0 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 meme \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent meme
COPY --from=build /app /app
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read()"]
# One serving process is intentional: admission and the RSS budget are per worker.
CMD ["uvicorn", "meme_generator.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]

# Test the runtime itself, adding only the development/browser dependencies.
FROM runtime AS test
USER root
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 \
    && uv sync --frozen && uv run playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/*
COPY tests ./tests
COPY e2e ./e2e
USER 10001:10001
HEALTHCHECK NONE
CMD ["/app/.venv/bin/pytest", "tests", "e2e", "-q", "-o", "cache_dir=/tmp/pytest-cache", "--basetemp=/tmp/pytest", "--output=/tmp/test-results"]

# The default image never includes the optional test layer.
FROM runtime AS production
