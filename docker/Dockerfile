# Spec §4: Node 22 builds the React client; a Python 3.14 runtime serves it, with ffmpeg, rsync, ssh and restic.
# Every base image is pinned by digest as well as by tag (digests looked up 2026-10-08), so a moved
# tag can't change what is built. Dependabot's docker updates move the digests
# (.github/dependabot.yml), and test_deploy_template.py checks that each FROM carries one.
FROM ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 AS uv

FROM node:22-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392 AS frontend
WORKDIR /src/packages/ui/frontend
COPY packages/ui/frontend/package.json packages/ui/frontend/package-lock.json ./
COPY packages/ui/frontend/scripts ./scripts
RUN npm ci
COPY packages/ui/frontend/ ./
RUN npm run build

# libsass (shiny -> shinychat) ships Linux wheels for amd64 only, so on arm64 it compiles from
# source and needs a C++ toolchain. Build the venv here; the runtime stage stays toolchain-free.
FROM python:3.14-slim@sha256:f85c5697265c178cc6887276c55fe16cf3d14ca35c3df6a5eab3b360534a55d2 AS build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Never COPY .gitignore into the image: hatch honours it and would drop the built (git-ignored)
# www/ui.js and www/ui.css from the wheel.
COPY pyproject.toml uv.lock .python-version ./
COPY packages/core packages/core
COPY packages/ui packages/ui
COPY --from=frontend /src/packages/ui/src/recordings_ui/www/ui.js /src/packages/ui/src/recordings_ui/www/ui.css packages/ui/src/recordings_ui/www/
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.14-slim@sha256:f85c5697265c178cc6887276c55fe16cf3d14ca35c3df6a5eab3b360534a55d2 AS runtime
ARG TARGETARCH
ARG RESTIC_VERSION=0.19.1
# Stage 2a (spec §15.1, §9.1.1): ffprobe (ffmpeg) measures imported media; rsync and ssh run the
# NAS mirror and the NAS checks; restic makes the backups. restic comes from its GitHub release,
# checked against that release's SHA256SUMS (2026-10-08), the same pins as
# scripts/fetch_restic.py (a test keeps them equal); curl and bzip2 leave with the layer. tzdata
# is for zoneinfo: uv.lock has no tzdata wheel, and without a system database every
# ZoneInfo(...) fails, so the import places no row (Task 8) and Status has no local time.
RUN set -eu; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ffmpeg rsync openssh-client tzdata ca-certificates curl bzip2; \
    case "$TARGETARCH" in \
      amd64) sum=f415415624dcc452f2a02b8c33641791a8c6d6d3b65bbb3543fcf9a25151585c; bin=20d4142678d0d95ec11a4759def1b73fd9190abc9ca19e4b62d067c0b387e639 ;; \
      arm64) sum=a5f64aaab53d51e311fa3829124c5b703f2d14cf187d8640b6be3b2b49376465; bin=2fb45ac6f9071b6f20eb883953a188f9e7c7cb6bbe43c67a2e47ada4e85ee7f0 ;; \
      *) echo "no pinned restic for $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    curl -fsSL --retry 3 -o /tmp/restic.bz2 \
      "https://github.com/restic/restic/releases/download/v${RESTIC_VERSION}/restic_${RESTIC_VERSION}_linux_${TARGETARCH}.bz2"; \
    echo "$sum  /tmp/restic.bz2" | sha256sum -c -; \
    bunzip2 -c /tmp/restic.bz2 > /usr/local/bin/restic; \
    echo "$bin  /usr/local/bin/restic" | sha256sum -c -; \
    chmod 0755 /usr/local/bin/restic; \
    rm /tmp/restic.bz2; \
    apt-get purge -y curl bzip2; \
    apt-get autoremove -y; \
    rm -rf /var/lib/apt/lists/*; \
    restic version
# The containers run as deploy.env's RECORDINGS_UID and RECORDINGS_GID, which compose.yml passes
# here as APP_UID and APP_GID. That user must exist in /etc/passwd: ssh exits before it reads any
# option when its uid has no entry ("No user exists for uid"), and the mirror and the NAS checks
# run ssh. 10001 is for images built without them (CI, the demo). The index's named volume copies
# /index's owner and mode when Docker first creates it. ~/.ssh exists so that ssh, on the read-only
# file system, doesn't complain that it can't create it.
ARG APP_UID=10001
ARG APP_GID=10001
RUN set -eu; \
    if [ "$APP_UID" = 0 ] || [ "$APP_GID" = 0 ]; then echo "APP_UID and APP_GID must not be 0" >&2; exit 1; fi; \
    getent group "$APP_GID" >/dev/null || groupadd --gid "$APP_GID" app; \
    getent passwd "$APP_UID" >/dev/null || useradd --uid "$APP_UID" --gid "$APP_GID" --no-log-init --create-home --shell /usr/sbin/nologin app; \
    install -d -o "$APP_UID" -g "$APP_GID" -m 0700 /index; \
    home=$(getent passwd "$APP_UID" | cut -d: -f6); \
    install -d -o "$APP_UID" -g "$APP_GID" -m 0700 "$home/.ssh"
WORKDIR /app
COPY --from=build /app/.venv .venv
COPY demo/archive demo/archive
USER ${APP_UID}:${APP_GID}
ENV PATH=/app/.venv/bin:$PATH RECORDINGS_DEMO_ARCHIVE=/app/demo/archive
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
CMD ["recordings-ui", "--host", "0.0.0.0", "--port", "8000"]
