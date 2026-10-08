# Stage 1 (spec §4): Node 22 builds the React client; a Python 3.14 runtime serves it.
FROM node:22-slim AS frontend
WORKDIR /src/packages/ui/frontend
COPY packages/ui/frontend/package.json packages/ui/frontend/package-lock.json ./
COPY packages/ui/frontend/scripts ./scripts
RUN npm ci
COPY packages/ui/frontend/ ./
RUN npm run build

# libsass (shiny -> shinychat) ships Linux wheels for amd64 only, so on arm64 it compiles from
# source and needs a C++ toolchain. Build the venv here; the runtime stage stays toolchain-free.
FROM python:3.14-slim AS build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Never COPY .gitignore into the image: hatch honours it and would drop the built (git-ignored)
# www/ui.js and www/ui.css from the wheel.
COPY pyproject.toml uv.lock .python-version ./
COPY packages/core packages/core
COPY packages/ui packages/ui
COPY --from=frontend /src/packages/ui/src/recordings_ui/www/ui.js /src/packages/ui/src/recordings_ui/www/ui.css packages/ui/src/recordings_ui/www/
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.14-slim AS runtime
WORKDIR /app
COPY --from=build /app/.venv .venv
COPY demo/archive demo/archive
RUN useradd --create-home --uid 10001 app
USER app
ENV PATH=/app/.venv/bin:$PATH RECORDINGS_DEMO_ARCHIVE=/app/demo/archive
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
CMD ["recordings-ui", "--host", "0.0.0.0", "--port", "8000"]
