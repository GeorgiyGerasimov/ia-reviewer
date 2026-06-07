# Frontend image. Builds the React/Vite SPA + serves it via nginx
# alongside a reverse-proxy to the backend (Python FastAPI in a
# separate container). Two-stage:
#
#   1. `builder` (node:22-alpine) — `npm ci && npm run build`.
#   2. `runtime` (nginx:alpine) — copies the built bundle into
#      nginx's default doc-root and bakes in our `nginx.conf` with
#      reverse-proxy rules for the API + WebSocket.
#
# The shipped image carries no Node, no `node_modules`, no source
# files — just the hashed Vite output plus nginx. End-to-end size
# is ~25–30 MB.
#
# The backend image (`../Dockerfile`) has no knowledge of this one
# and vice versa; the only contract is the HTTP API + `/ws/chat/*`
# WebSocket. Either side can be redeployed independently or even
# moved to a different host (e.g. SPA to a CDN, backend behind a
# load balancer) without code changes.
#
# Build is intended to run with `context: .` (project root) so that
# `web/` source is available; docker-compose handles that.

# ── Stage 1: Build the SPA ─────────────────────────────────────────
FROM node:22-alpine AS builder

WORKDIR /web

# Copy the manifests first to maximise layer cache reuse — package
# files change far less often than source.
COPY web/package.json web/package-lock.json ./

# `npm ci` honours the lockfile + refuses to mutate it.
# `--no-audit --no-fund` silences npm's chatter in CI logs.
RUN npm ci --no-audit --no-fund

COPY web/ ./

# Output lands at `/web/dist/`. `npm run build` invokes
# `tsc -b && vite build` per package.json.
RUN npm run build


# ── Stage 2: Serve via nginx ───────────────────────────────────────
FROM nginx:alpine AS runtime

# Replace the default nginx config with ours (SPA + reverse proxy).
COPY web/nginx.conf /etc/nginx/conf.d/default.conf

# Drop the built SPA into nginx's doc-root.
COPY --from=builder /web/dist /usr/share/nginx/html

# nginx listens on 80 inside the container. docker-compose maps host
# port 8000 → container 80 so the user-facing URL stays the same as
# the legacy single-image setup.
EXPOSE 80

# Healthcheck hits /health which is proxied to the backend. If the
# backend is unreachable the proxy fails-fast (60-second connect
# timeout on nginx); we use a shorter wget timeout here to surface
# the failure quickly.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget --quiet --tries=1 --timeout=3 --spider http://localhost/health || exit 1

CMD ["nginx", "-g", "daemon off;"]
