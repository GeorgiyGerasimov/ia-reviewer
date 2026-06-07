# Backend-only image. Python + FastAPI + LangGraph runtime.
#
# This image ships NO frontend assets — the React SPA is built and
# served by the sibling `web/Dockerfile` (nginx). The two images
# only communicate over HTTP on the compose network, so the backend
# has zero source-level coupling to the web frontend.
#
# What the backend still serves at `GET /` is the legacy Jinja
# template (`templates/index.html`) — kept for `uvicorn main:app`
# bare-metal runs and as a debug fallback. In `docker compose up`
# production, nginx terminates `/` and the backend's Jinja handler
# is never reached.

# ── Stage 1: Python deps ───────────────────────────────────────────
FROM python:3.11-slim AS py-builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

COPY requirements.txt ./
RUN pip install --upgrade pip && \
    pip install --prefix=/install -r requirements.txt


# ── Stage 2: Runtime ───────────────────────────────────────────────
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
# `volumes:` would leave them owned by root and the app would fail
# to write.
RUN mkdir -p /app/reports /app/snapshots && \
    chown -R app:app /app

COPY --chown=app:app src ./src
COPY --chown=app:app db ./db
COPY --chown=app:app templates ./templates
COPY --chown=app:app main.py ./

# Note the deliberate absence of `COPY web/dist`. Backend-only image;
# the SPA is a separate artifact built by `web/Dockerfile`.

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health', timeout=3).raise_for_status()" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
