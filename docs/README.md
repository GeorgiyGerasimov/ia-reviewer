# Documentation

| Document | What's in it |
|---|---|
| [architecture.md](architecture.md) | Graph topology, state model, agents, validator/decision/exploit-proposal phases, progress streaming. |
| [api.md](api.md) | HTTP endpoints, WebSocket envelopes, request/response shapes, validation errors. |
| [pr-mode.md](pr-mode.md) | PR-review flow: fetch_pr, validator stages, publish to GitHub. |
| [repo-mode.md](repo-mode.md) | Repo-review flow: clone, per-specialist whitelists, snapshot lifecycle, caps. |
| [configuration.md](configuration.md) | Every env var and in-code constant with defaults and purpose. |
| [observability.md](observability.md) | Langfuse setup (default-on local stack + cloud), tracing wiring. |
| [ui.md](ui.md) | Frontend layout, workflow diagram colours, branch-aware coloring, retry logic. |
| [ui-testing.md](ui-testing.md) | Three-tier UI test strategy: Vitest (primary), static checks for legacy fallback, Playwright e2e. |
| [development.md](development.md) | Setup, TDD workflow, testing principles, project skills, adding a reviewer. |
| [troubleshooting.md](troubleshooting.md) | Symptoms-to-causes guide for the bugs that show up in real runs. |
| [security-defaults.md](security-defaults.md) | ⚠️ Every dev credential pre-baked into `.env.example` / `docker-compose.yml` and how to regenerate before exposing the app. |
| [security-checklist.md](security-checklist.md) | Honest position against OWASP LLM Top 10 (2025) — code links + known gaps. |
| [performance-and-cost.md](performance-and-cost.md) | Per-node latency expectations, awk/grep recipes for p50/p95, USD cost per mode. |
| [judge-in-production.md](judge-in-production.md) | Inline LLMJudge gate on `format_report` + offline CLI on reports. |
| [model-selection-research.md](model-selection-research.md) | Notes on picking gateway models for reviewer / judge / embedder roles. |

For a high-level pitch and the 60-second quick start, see the project
[`README.md`](../README.md).

For implementation conventions enforced by the Claude harness, see
[`CLAUDE.md`](../CLAUDE.md) and `.claude/skills/`.
