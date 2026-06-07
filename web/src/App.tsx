// Top-level component. Intentionally minimal during the foundation PR
// — it just proves the React/Vite/Vitest stack runs. The actual UI
// migration lands in follow-up PRs that swap in panels one at a time:
//
//   * ActiveReviews     — sidebar list with live token counter
//   * PastReviews       — sidebar list, GET /reviews
//   * WorkflowDiagram   — left rail step indicators
//   * ReviewForm        — PR/repo URL input
//   * CriticalFindings  — exploit-PoC creation panel
//   * TokenUsage        — per-node LLM accounting
//   * ReportPanel       — markdown report viewer + WebSocket stream
//
// Until those land, the legacy `templates/index.html` continues to
// serve the production UI (see main.py — the FastAPI route prefers
// `web/dist/index.html` when present, otherwise falls back).

export function App() {
  return (
    <main>
      <h1>ia-reviewer</h1>
      <p>React skeleton — UI migration in progress. See web/README.md.</p>
    </main>
  );
}
