# Documentation

| Document | What's in it |
|---|---|
| [architecture.md](architecture.md) | Graph topology, state model, agents, validator/decision/exploit-proposal phases, progress streaming. |
| [api.md](api.md) | HTTP endpoints, WebSocket envelopes, request/response shapes, validation errors. |
| [pr-mode.md](pr-mode.md) | PR-review flow: fetch_pr, validator stages, publish to GitHub. |
| [repo-mode.md](repo-mode.md) | Repo-review flow: clone, per-specialist whitelists, snapshot lifecycle, caps. |
| [configuration.md](configuration.md) | Every env var and in-code constant with defaults and purpose. |
| [observability.md](observability.md) | Langfuse setup (local self-hosted stack + cloud), tracing wiring. |
| [ui.md](ui.md) | Frontend layout, workflow diagram colours, branch-aware coloring, retry logic. |
| [development.md](development.md) | Setup, TDD workflow, testing principles, project skills, adding a reviewer. |
| [troubleshooting.md](troubleshooting.md) | Symptoms-to-causes guide for the bugs that show up in real runs. |

For a high-level pitch and the 60-second quick start, see the project
[`README.md`](../README.md).

For implementation conventions enforced by the Claude harness, see
[`CLAUDE.md`](../CLAUDE.md) and `.claude/skills/`.
