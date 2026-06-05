# Security defaults — what must be replaced before going to production

`docker compose up` ships with hard-coded development credentials so the
stack works zero-touch on a laptop. **None of them are safe for any
network-reachable deployment.** This page is the canonical list of what
to override and how.

> Anything below carries a `🔐 REPLACE-BEFORE-PROD` marker in the source
> file. Grep for it: `grep -rn "REPLACE-BEFORE-PROD" .` returns the live
> set.

## At-a-glance checklist

| # | Env var (or hard-coded) | Default | Where | Risk |
|---|---|---|---|---|
| 1 | `POSTGRES_PASSWORD` | `ia` | `.env` + compose | Postgres open to anyone on the network |
| 2 | `LANGFUSE_PUBLIC_KEY` | `pk-lf-dev` | `.env` + compose | anyone with key writes traces |
| 3 | `LANGFUSE_SECRET_KEY` | `sk-lf-dev` | `.env` + compose | same |
| 4 | `LANGFUSE_NEXTAUTH_SECRET` | `replace-me-…` | `.env` + compose | session cookies forgeable |
| 5 | `LANGFUSE_SALT` | `replace-me-…` | `.env` + compose | password hash rainbow-tableable |
| 6 | `LANGFUSE_ENCRYPTION_KEY` | 64×`0` | `.env` + compose | stored API keys decryptable |
| 7 | `LANGFUSE_REDIS_AUTH` | `myredissecret` | `.env` + compose | Redis open to anyone on the network |
| 8 | `LANGFUSE_INIT_USER_EMAIL` | `dev@local.dev` | `.env` + compose | known admin email |
| 9 | `LANGFUSE_INIT_USER_PASSWORD` | `localdev123!` | `.env` + compose | known admin password |
| 10 | ClickHouse `clickhouse/clickhouse` | hard-coded | compose | ClickHouse open |
| 11 | MinIO `minio/miniosecret` | hard-coded | compose | object storage open |
| 12 | `GITHUB_WEBHOOK_SECRET` | empty | `.env` | webhook spoofing |

## Regeneration recipes

Run the snippet for each row, paste the output into `.env`:

```bash
# Postgres / MinIO / Langfuse seed user — printable, easy to copy.
openssl rand -base64 24

# Langfuse signing + encryption keys — must be 32 hex bytes (64 chars).
openssl rand -hex 32

# Langfuse public/secret API keys — Langfuse parses these as opaque
# strings, but rotating means visiting the Langfuse UI → Settings →
# API Keys, generating a fresh pair, and pasting BOTH into .env
# (LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY).
```

## Rotation gotchas

- **`LANGFUSE_ENCRYPTION_KEY`** encrypts stored API keys inside Langfuse.
  Changing it on a populated database makes existing traces unreadable.
  Rotate only on a fresh database, or follow Langfuse's documented
  re-encryption procedure.

- **ClickHouse + MinIO credentials are NOT parameterised in
  `docker-compose.yml`.** They're inlined in four places (web env,
  worker env, the ClickHouse / MinIO service block itself). When you
  rotate, change every occurrence — `grep -n "clickhouse$\|miniosecret"
  docker-compose.yml` shows them all.

- **The Postgres password is shared with Langfuse.** Both the app's
  `DATABASE_URL` and the Langfuse stack's connection strings read
  `${POSTGRES_USER}/${POSTGRES_PASSWORD}` — one override covers both.

- **`POSTGRES_USER` change requires destroying the pgdata volume.**
  PostgreSQL only creates the user on the first cluster init. To
  actually switch the username you need
  `docker compose down -v && docker compose up -d`, which wipes all
  reviews + traces. Prefer rotating just the password.

## What's safe to leave as-is

Format hints (`sk-ant-...`, `ghp-...`, `sk-...`) in `.env.example` are
just human guidance — the file is template-only, not loaded at runtime.
The real keys live in your `.env`, which is gitignored.

`AI_GATEWAY_API_KEY=` empty is fine when pointed at the LAN model box
(no auth on that endpoint). Override it if you switch to a hosted
gateway.

`ANTHROPIC_API_KEY=unused` is intentional in dev `.env` — gateway mode
skips Anthropic auth but pydantic-settings still wants the field
populated. The placeholder string never reaches an HTTP call.
