# ia-reviewer

Multi-agent AI **security** code reviewer for GitHub, built on LangGraph
and FastAPI. Runs three specialist reviewers in parallel, optionally
pauses for a human, and publishes a single consolidated Markdown report.

```
┌── Validate ─┬─→ Dependency ─┐
              ├─→ Injection   ├─→ Aggregate ─→ Exploit proposals ─→ Publish
              └─→ OWASP 10    ┘                  (human-approved, capped)
```

Two modes share the same pipeline:

- **PR mode** — review a PR diff, post a top-level comment on the PR.
- **Repo mode** — shallow-clone a repo and walk it per-specialist
  (one LLM call per file), save a Markdown report locally and link to
  it from the chat.

Architecturally a sibling of `ms-ia-bot` (GitLab + Slack), but scoped
to **GitHub only**, **without Slack**, and **security-focused**.

> ⚠️ **Exploit proposals — defensive use only.** Critical/major findings
> trigger an optional human-gated step that generates a proof-of-concept
> artifact. This feature exists to let security engineers validate
> findings against **their own code** in an isolated environment and
> measure real risk. It is **NOT** for use against third-party systems
> and **NOT** for destructive purposes. The LLM is instructed to refuse
> third-party targets; every chat prompt, button row, report section,
> and sibling artifact file carries the same disclaimer. See
> `src/agents/exploit_proposal.py::EXPLOIT_DISCLAIMER` for the canonical
> wording.

## Quick start (local, public repos)

```bash
git clone https://github.com/<your-org>/ia-reviewer.git
cd ia-reviewer

python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# minimum viable .env: USE_AI_GATEWAY=true, AI_GATEWAY_URL=<your OpenAI-compatible endpoint>
# leave GITHUB_TOKEN empty for anonymous clone of public repos

.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
# open http://127.0.0.1:8000/
```

Paste any GitHub URL into the form:

- `https://github.com/owner/repo` → whole-repo review at HEAD
- `https://github.com/owner/repo/tree/develop` → whole-repo review at `develop`
- `https://github.com/owner/repo/pull/42` → PR diff review

URLs with trailing `.git`, `/blob/…/file`, query strings, or fragments
are normalised server-side.

Or via curl:

```bash
curl -s -X POST http://localhost:8000/review \
  -H 'Content-Type: application/json' \
  -d '{"repo_url": "https://github.com/octocat/Hello-World"}'
# → {"status": "started", "thread_id": "...", "repo_url": "...", "ref": "HEAD"}

curl -s http://localhost:8000/reports/<thread_id>.md   # final report
```

## Three reviewers

- **Dependency** — manifest / lockfile vulnerabilities, typosquats,
  unmaintained packages.
- **Injection** — SQLi, command, template, deserialization, path
  traversal, XSS.
- **OWASP Top 10** — broader sweep covering A01 / A02 / A04 / A05 /
  A07 / A08 / A09 / A10. A03 and A06 are delegated to Injection and
  Dependency, respectively.

Scope a subset with `scope=["dependency", "injection"]` on the request
body. Empty (default) runs all three.

## Architecture in a sentence

LangGraph state machine with a parallel fan-out across the three
reviewers, a Phase B human-in-the-loop re-review decision node bounded
by `MAX_CYCLES=3`, and a Phase C exploit-proposal branch with
per-finding human approval bounded by `MAX_EXPLOIT_PROPOSALS=3`. Both
human-loop branches degrade gracefully when no checkpointer is wired
(auto-skip interrupts). See [`docs/architecture.md`](docs/architecture.md)
for the full picture.

## Web UI

A small static page at `GET /` shows:

- Live workflow diagram, circles light up as the graph progresses.
- Chat panel tied to the LangGraph `thread_id` — ready for human-loop
  questions.
- Final report rendered as Markdown.

See [`docs/ui.md`](docs/ui.md) for the workflow colour codes.

## Docker

```bash
cp .env.example .env
docker compose up -d   # app + postgres + langfuse-web + langfuse-worker + clickhouse + redis + minio
```

Open <http://localhost:8000> for the review UI and <http://localhost:3000>
for Langfuse traces (login `dev@local.dev` / `localdev123!`, self-seeded on
first boot). The UI's header also shows a "View traces ↗" link wired to
the same URL.

See [`docs/observability.md`](docs/observability.md) for the Langfuse
stack details (web + worker + clickhouse + redis + minio). To skip
Langfuse on a constrained host, scope the up command:
`docker compose up -d app postgres`.

> ⚠️ **Dev credentials.** Every service starts with hard-coded
> placeholder passwords so the laptop demo works zero-touch. **They are
> not safe for any network-reachable deployment.** See
> [`docs/security-defaults.md`](docs/security-defaults.md) for the full
> list and regeneration recipes.

## Documentation

| Doc | What's in it |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Graph topology, state, agents, phases. |
| [`docs/api.md`](docs/api.md) | HTTP/WebSocket endpoints reference. |
| [`docs/pr-mode.md`](docs/pr-mode.md) | PR-review flow. |
| [`docs/repo-mode.md`](docs/repo-mode.md) | Repo-review flow, clone + snapshots, caps. |
| [`docs/configuration.md`](docs/configuration.md) | Every env var + in-code constant. |
| [`docs/observability.md`](docs/observability.md) | Langfuse setup, tracing wiring. |
| [`docs/security-defaults.md`](docs/security-defaults.md) | ⚠️ Every dev credential to replace before production. |
| [`docs/ui.md`](docs/ui.md) | Frontend layout + workflow colours. |
| [`docs/development.md`](docs/development.md) | Setup, TDD, testing, adding a reviewer. |
| [`docs/troubleshooting.md`](docs/troubleshooting.md) | Symptoms-to-causes for real-world failures. |

## Tests

```bash
.venv/bin/pytest        # full suite (~5s on M-series Mac)
.venv/bin/pytest tests/unit -q   # fast feedback loop
```

The project follows strict TDD; see
[`docs/development.md`](docs/development.md) and
[`.claude/skills/`](.claude/skills) for the conventions.

## Status

Working end-to-end:

- Both review modes (PR + repo).
- Three reviewers in parallel with per-specialist whitelists in repo
  mode.
- Two-stage validator (pure-code + LLM judge), Phase B re-review,
  Phase C exploit-proposal with human approval + 60-second timeout.
- Resumable graph via `AsyncPostgresSaver` (or degraded-but-functional
  mode without a checkpointer).
- Langfuse tracing across initial reviews and resume-after-interrupt.
- Self-contained UI (no build step) with branch-aware workflow
  visualisation.

## License

(none chosen yet)
