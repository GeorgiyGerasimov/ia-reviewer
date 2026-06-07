// Wire-level types for the ia-reviewer HTTP API. Mirror the Python
// pydantic / dataclass shapes in `src/graph/state.py`,
// `src/storage/review_store.py`, and the FastAPI handlers in
// `main.py`. Drift between this file and the backend models breaks
// the UI silently — keep them in lock-step when adding fields.

/** GET /health */
export interface HealthResponse {
  status: "ok";
  /** Set when Langfuse tracing is wired and a CallbackHandler is
   *  attached to `app.state.langfuse_callback`. Null when keys
   *  unset or the package failed to import. */
  langfuse_url: string | null;
}

export type ReviewerRole = "dependency" | "injection" | "owasp" | "configuration";

export type Severity = "info" | "minor" | "major" | "critical";

/** GET /reviews — list of past, persisted reviews. */
export interface ReviewSummary {
  thread_id: string;
  mode: "pr" | "repo";
  target_url: string;
  ref?: string | null;
  severity: Severity | "rejected";
  findings_count: number;
  rejected?: boolean;
  created_at: string;
}

/** GET /reviews/{id} — full review row. */
export interface Review {
  thread_id: string;
  mode: "pr" | "repo";
  target_url: string;
  ref?: string | null;
  report_markdown: string;
  findings: Finding[];
  exploit_proposals: ExploitProposal[];
  /** Per-graph-node LLM token accounting. Empty `{}` when no LLM
   *  call fired (very short reviews, or reviews completed before
   *  the token-tracking PR landed). */
  token_usage?: TokenUsage;
  rejected?: boolean;
  rejection_reason?: string | null;
  rejection_category?: string | null;
  created_at: string;
}

export interface Finding {
  finding_id: string;
  role: ReviewerRole;
  file: string | null;
  line: number | null;
  severity: Severity;
  category: string | null;
  issue: string;
}

export interface ExploitProposal {
  finding_id: string;
  role: ReviewerRole;
  severity: Severity;
  proposal_text: string;
  artifact: string;
  status: "approved" | "skipped_low_confidence";
  confidence: number;
}

/** GET /reviews/{id}/critical-findings — same as Finding but joined
 *  with the review's exploit_proposals JSONB, so each row carries
 *  current exploit status + (if approved) the artifact + proposal
 *  text inline. The UI uses `exploit_status` to decide between
 *  rendering a Create-exploit button vs a View-existing link. */
export interface CriticalFinding {
  finding_id: string;
  role: ReviewerRole;
  file: string | null;
  line: number | null;
  issue: string;
  severity: "critical";
  exploit_status: "approved" | "skipped_low_confidence" | null;
  proposal_text: string;
  artifact: string;
  /** Draft confidence score 0-10, surfaced by the backend on
   *  skipped rows so the UI can explain *why* it was skipped.
   *  Null when no exploit was attempted yet. */
  confidence?: number | null;
}

/** GET /reviews/active — in-flight review snapshot. */
export interface ActiveReview {
  thread_id: string;
  mode: "pr" | "repo";
  target: string;
  ref?: string | null;
  started_at: string;
  elapsed_s: number;
  /** Live totals from the per-thread TokenUsageHandler. Zero values
   *  when no LLM call has fired yet (very brief window at start). */
  tokens: TokenTotals;
}

export interface TokenTotals {
  input: number;
  output: number;
  calls: number;
}

export interface TokenBucket extends TokenTotals {
  /** Distinct model names observed in this bucket. Optional —
   *  legacy rows may not have it. */
  models?: string[];
}

/** Per-graph-node accounting. Key = graph node name
 *  (`validate_request`, `injection_review`, …). */
export type TokenUsage = Record<string, TokenBucket>;

/** POST /review request body — PR mode. */
export interface PRReviewRequest {
  pr_url: string;
  scope?: ReviewerRole[];
}

/** POST /review request body — repo mode. */
export interface RepoReviewRequest {
  repo_url: string;
  ref?: string;
  scope?: ReviewerRole[];
}

/** POST /review response. The actual review runs as a background
 *  task; this is the 202-equivalent acknowledgement carrying the
 *  thread_id the WebSocket / poller will track. */
export interface ReviewStarted {
  status: "started";
  thread_id: string;
  pr_url?: string;
  repo_url?: string;
  ref?: string;
}

export interface CancelResult {
  status: "cancelled";
  thread_id: string;
}
