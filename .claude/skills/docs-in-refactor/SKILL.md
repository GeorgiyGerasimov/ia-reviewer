---
name: docs-in-refactor
description: Documentation is part of every refactor. When removing, renaming, or restructuring code, update README.md / docs/ / CLAUDE.md / .env.example in the same change. Apply at the end of any refactor before declaring done.
---

# Docs in Refactor

## Rule

When a refactor removes a feature, changes architecture, renames a module, or changes config, **always** update the docs in the same change.

## Files to check

- `README.md` — user-facing description, setup, env vars, examples, diagrams
- `docs/architecture.md` — architectural diagrams, role tables, integration list
- `CLAUDE.md` — anything Claude needs to know to work on the project
- `.env.example` — environment variable list
- `pyproject.toml` description field — if conceptually changed

## Why

Stale docs mislead future contributors and erode trust in everything else the docs claim. The lesson came from the parent project (`ms-ia-bot`) where README still mentioned Confluence after it had been removed — the cleanup wasn't part of the refactor commit.

## How to apply

1. After making a behavioral or structural change, grep for the removed/renamed concept:
   ```bash
   grep -ri "<removed-concept>" --include="*.md" --include="*.toml" --include="*.yml" --include=".env*" .
   ```
   Should return **nothing** (excluding `.venv/`).

2. Before declaring a refactor done, mentally check:
   - Does the README still describe the system accurately?
   - Does the architecture diagram match the new layout?
   - Do examples in the README still work as written?
   - Is the project layout tree still accurate?

3. Treat doc updates as part of the implementation, not an afterthought. **Don't say "task complete" until docs are aligned.**

## Common stale doc traps

- ASCII diagrams in README/docs that reference removed components
- Tech stack lists that include deleted dependencies
- Project layout trees with files that no longer exist
- `curl` examples with removed request fields
- Required/optional env var tables
- Tables comparing approaches where one approach was removed
