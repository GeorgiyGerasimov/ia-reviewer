# Multi-stage build:
#
#   1. `py-builder`  — installs Python deps into a prefix that the
#                      runtime stage copies wholesale.
#   2. `web-builder` — builds the React/Vite SPA bundle from `web/`.
#                      Runs `npm ci` against the committed
#                      `package-lock.json` for a deterministic tree.
#   3. `runtime`     — minimal python:3.11-slim image carrying the
#                      pre-built Python deps, the application code,
#                      AND the SPA bundle at `/app/web/dist/`.
#
# Goal of the multi-stage layout: shipped image has NO node, NO
# npm, NO source-only files (web/src, web/node_modules) — just the
# hashed Vite output that FastAPI mounts at `/` + `/assets/*`.
# Operators get the React UI from `docker compose up` without ever
# touching their local Node toolchain.

# ── Stage 1: Python deps ───────────────────────────────────────────
FROM python:3.11-slim AS py-builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

COPY requirements.txt ./
RUN pip install --upgrade pip && \
    pip install --prefix=/install -r requirements.txt


# ── Stage 2: Web bundle ────────────────────────────────────────────
# `node:22-alpine` matches the CI matrix (`actions/setup-node@v4`
# with `node-version: "22"`). Alpine keeps the builder layer small;
# the actual `node_modules` doesn't survive into runtime anyway.
FROM node:22-alpine AS web-builder

WORKDIR /web

# Copy the manifests first to maximise layer cache reuse — package
# files change far less often than source.
COPY web/package.json web/package-lock.json ./

# `npm ci` honours the lockfile + refuses to mutate it. Deterministic
# install for reproducible Docker builds. `--no-audit --no-fund`
# silences npm's chatter in CI logs.
RUN npm ci --no-audit --no-fund

COPY web/ ./

# Output lands at `/web/dist/`. We do NOT run tsc here separately —
# `npm run build` invokes `tsc -b && vite build` per package.json.
RUN npm run build


# ── Stage 3: Runtime ───────────────────────────────────────────────
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/install/bin:$PATH \
    PYTHONPATH=/app:/install/lib/python3.11/site-packages

# git is a runtime dep for repo-mode review (`git clone --depth=1`).
# Install before creating the app user so apt's cache cleanup is one
# layer.
RUN apt-get update && \
    apt-get install -y --no-install-recommends git ca-certificates && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 1000 app && \
    useradd  --system --uid 1000 --gid app --create-home --shell /bin/bash app

COPY --from=py-builder /install /install

WORKDIR /app
# Pre-create the dirs the app writes to so the non-root `app` user
# owns them. Without this, mounting empty host directories via
# `volumes:` would leave them owned by root and the app would fail to
# write.
RUN mkdir -p /app/reports /app/snapshots /app/web/dist && \
    chown -R app:app /app

COPY --chown=app:app src ./src
COPY --chown=app:app db ./db
COPY --chown=app:app templates ./templates
COPY --chown=app:app main.py ./

# Bring in the built SPA. `_register_routes` checks
# `/app/web/dist/index.html` at startup and mounts the React shell
# when present; a layer where this COPY is skipped (e.g. development
# `docker build --target py-builder`-only workflows) leaves the
# directory empty and the Jinja shell wins.
COPY --chown=app:app --from=web-builder /web/dist /app/web/dist

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health', timeout=3).raise_for_status()" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
