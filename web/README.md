# ia-reviewer-web

React frontend for ia-reviewer. Lives as a separate subproject inside the
main repo. Built with Vite; served by FastAPI from `web/dist/` once the
migration off `templates/index.html` is complete.

## Why a subproject (not a sibling repo)

Three reasons:

1. **One PR per change** — backend + frontend pieces of the same feature
   land in the same review.
2. **Shared CI** — `.github/workflows/ci.yml` has one job per side
   (`test` for Python, `web` for the frontend). A single PR shows red /
   green for both.
3. **Atomic deploys** — `docker compose build` produces one image that
   includes the built bundle, so there's no "frontend is one version
   ahead of the API" failure mode.

The deliberate boundary is that nothing in `web/` imports anything from
`../src` and vice versa — they talk only over the HTTP / WebSocket API.

## Stack

- **React 18** — UI library
- **TypeScript** — strict mode, no `any`
- **Vite** — dev server + bundler
- **Tailwind CSS v4** — utility-first styling (CSS-first config via `@theme`)
- **shadcn/ui** — Radix-primitive components, source copied into `src/components/ui/`
- **Vitest** — Jest-compatible test runner with native ESM
- **React Testing Library** — DOM-first component tests
- **ESLint** (flat config) + **Prettier** — lint + format
- **jsdom** — DOM environment for component tests

### shadcn workflow

```bash
# Add a component (puts source under src/components/ui/<name>.tsx).
# All shadcn add calls go through the @/ → src/ alias declared in
# tsconfig.json + vite.config.ts.
npx shadcn@latest add <component>

# Overwrite an existing component (after a shadcn upstream update).
npx shadcn@latest add <component> --overwrite
```

`@/lib/utils.ts` exposes the standard `cn(…classes)` helper (clsx + tailwind-merge). Use it whenever you compose class strings from props.

## Layout

```
web/
├── package.json              # deps + npm scripts
├── tsconfig.json             # TS project references
├── tsconfig.app.json         # strict TS config for src/
├── tsconfig.node.json        # config for vite.config.ts itself
├── vite.config.ts            # bundler + dev-server proxy to uvicorn
├── vitest.config.ts          # test runner config (jsdom + setup)
├── eslint.config.js          # flat-config ESLint rules
├── .prettierrc.json          # formatter rules
├── index.html                # Vite entry HTML
└── src/
    ├── main.tsx              # React root + #root mount
    ├── App.tsx               # top-level component
    ├── vite-env.d.ts         # CSS/asset module types
    ├── api/                  # typed fetch wrappers (HTTP + WS)
    ├── components/           # one .tsx + colocated .test.tsx per UI piece
    ├── lib/                  # pure logic (formatters, markdown, …)
    ├── styles/globals.css    # @tailwind imports + design tokens via @theme
    └── test/setup.ts         # @testing-library/jest-dom registration
```

### File conventions

- Tests sit **next to** their source: `format.ts` + `format.test.ts`,
  `TokenSummary.tsx` + `TokenSummary.test.tsx`. Cross-component wiring
  tests can go under `tests/` (picked up by the same Vitest glob).
- Pure logic goes in `lib/`. No DOM, no React, no fetch.
- API calls go through `api/client.ts`. Components never call `fetch`
  directly — they receive data via props from a hook or container.
- Components we own go in `components/`. shadcn-vendored components
  go in `components/ui/` — those files are upstream code; treat
  edits the same as forking. To pick up shadcn updates re-run
  `npx shadcn@latest add <name> --overwrite`.
- Styles use **Tailwind v4 utility classes** on the JSX. The runtime
  palette and design tokens live in `styles/globals.css` as CSS
  variables using the canonical shadcn vocabulary
  (`--background`, `--foreground`, `--primary`, `--card`,
  `--muted-foreground`, `--border`, …) plus a handful of domain
  extras for this project (`--severity-critical`, `--done`,
  `--notice-bg`, …). They're bridged to Tailwind utilities
  (`bg-background`, `text-muted-foreground`, `border-border`, …)
  via the `@theme inline` block. Dark mode flips through
  `[data-theme="dark"]` on `<html>` — the boot script in `index.html`
  sets it before stylesheet parsing. `dark:` Tailwind variants work
  via the `@custom-variant dark` declaration in `globals.css`.

## Workflow

```bash
cd web

# First-time setup
npm install

# Dev server with HMR. Backend must be running on :8000 (uvicorn) —
# the dev proxy in vite.config.ts forwards /review, /reviews, /reports,
# /chat, /ws to localhost:8000.
npm run dev

# Tests
npm test                   # one-shot
npm run test:watch         # watch mode
npm run test:ui            # open the Vitest UI

# Lint + types + format
npm run lint
npm run typecheck
npm run format
npm run format:check

# Production build → dist/
npm run build
```

### Engineering rules (mirror the backend)

- **TDD.** Write the test first, watch it fail, then write the
  implementation. Same red→green→refactor cycle as Python.
- **No real network in tests.** Mock `fetch` (we use Vitest's built-in
  spies; no MSW needed for the size we have today). WebSocket tests
  use a fake socket class.
- **Tests must run in milliseconds.** A 1-second component test is a
  smell. If it's slow, you're rendering too much or polling without
  fake timers.
- **No `any`.** Strict TypeScript catches the same class of bugs that
  pydantic catches on the backend.
- **No new dependencies without justification.** A 3 kB utility belongs
  in `lib/`, not in `package.json`.

## Production serving

`main.py` checks for `web/dist/index.html` at startup. When present, it
mounts the directory as static and routes `GET /` to the SPA shell.
Otherwise it falls back to the legacy Jinja template (`templates/index.html`)
so partial migrations don't break the running app.

To produce the SPA bundle during a Docker build:

```dockerfile
FROM node:22 AS web-builder
WORKDIR /app/web
COPY web/package*.json ./
RUN npm ci
COPY web ./
RUN npm run build
# … COPY --from=web-builder /app/web/dist /app/web/dist into runtime image
```

## Migration status

This subproject is the React skeleton + foundation utilities. The
panels are migrating from `templates/index.html` piece by piece:

| Panel             | Status | Notes                                          |
| ----------------- | ------ | ---------------------------------------------- |
| Tailwind v4 setup | ✅     | `@theme inline` bridges our CSS vars to utilities |
| `format.ts`       | ✅     | Pure helpers extracted with tests              |
| `markdown.ts`     | ✅     | Tiny renderer with tests                       |
| `TokenSummary`    | ✅     | Reference component (Tailwind utilities)       |
| shadcn/ui init    | ✅     | components.json + lib/utils.ts (cn helper)     |
| shadcn batch 1    | ✅     | Button, Card, Dialog, Sheet, Tabs, Badge, Tooltip + 6-test smoke suite on Button |
| API client        | ✅     | `src/api/{types,client}.ts` — typed wrappers + APIError; 19 tests |
| WebSocket hook    | ✅     | `src/lib/useReviewStream.ts` — per-node status + validator verdict + chat messages from `/ws/chat/{tid}`; 14 tests via `MockWebSocket` |
| WorkflowDiagram   | ✅     | Left-rail step indicators (pure props; parent wires hook); 9 tests |
| `useActiveReviews` | ✅    | 5s-polling hook around GET /reviews/active; 5 tests |
| ActiveReviewsList | ✅     | Sidebar with select / stop / live token line; 8 tests |
| ReviewForm        | ✅     | PR/repo URL submit, auto-detects mode from `/pull/N`; 8 tests |
| Wired App page    | ✅     | First integrated React page (form → submit → workflow tracks the new thread); 3 smoke tests |
| PastReviews       | TODO   | Sidebar list, GET /reviews                     |
| CriticalFindings  | TODO   | Exploit-PoC creation panel                     |
| TokenUsagePanel   | TODO   | Per-node LLM accounting in the report view     |
| ReportPanel       | TODO   | Markdown report viewer + WebSocket stream      |
| Theme toggle      | TODO   | Dark mode (palette + localStorage persistence) |
| PWA (manifest + SW) | TODO | vite-plugin-pwa with Workbox                   |
| Web Push          | TODO   | VAPID; notify on critical findings             |
| Mobile layout     | TODO   | Drawer sidebar, stacked findings on narrow     |

The legacy template stays as the live UI until this table is fully
green. New features may land on the React side first and the legacy
template will then catch up only if needed.
