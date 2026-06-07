// Typed wrappers over the ia-reviewer HTTP API. Every component that
// talks to the backend goes through this module — no raw `fetch` calls
// from elsewhere in the codebase. Centralising the wire layer means:
//
//   * Type-checking the wire shape happens once, here. The components
//     consume `Promise<Review>` / `Promise<CriticalFinding[]>`, not
//     raw JSON.
//   * Error handling is uniform: every non-2xx response throws
//     `APIError` carrying status + parsed body + URL. Callers can
//     `try/catch` once at the boundary or let it propagate into an
//     error boundary.
//   * Mocking in tests is trivial — patch `globalThis.fetch` and the
//     whole client surface follows.
//
// We don't include WebSocket here — that's a different lifecycle
// (long-lived, push-driven) and lives in its own module. This file
// is request/response only.

import type {
  ActiveReview,
  CancelResult,
  CriticalFinding,
  ExploitProposal,
  HealthResponse,
  PRReviewRequest,
  RepoReviewRequest,
  Review,
  ReviewStarted,
  ReviewSummary,
} from "./types";

/** Thrown for any non-2xx response. Carries enough context to log /
 *  display a useful error message without re-parsing the response. */
export class APIError extends Error {
  public readonly status: number;
  public readonly url: string;
  /** Either the parsed JSON body or the raw text body when the
   *  response wasn't JSON. Useful in tests + diagnostic logs. */
  public readonly body: unknown;

  constructor(status: number, url: string, body: unknown) {
    super(`API ${status} ${url}`);
    this.name = "APIError";
    this.status = status;
    this.url = url;
    this.body = body;
  }
}

/** Parse the body as JSON when the Content-Type says so, otherwise
 *  return raw text. This lets `APIError.body` carry useful payload
 *  for both JSON 4xx responses (e.g. `{detail: "…"}` from FastAPI)
 *  and non-JSON failures (a plain "Internal Server Error" string). */
async function parseBody(res: Response): Promise<unknown> {
  const ct = res.headers.get("Content-Type") ?? "";
  if (ct.includes("application/json")) {
    return await res.json();
  }
  return await res.text();
}

async function request<T>(
  url: string,
  init: RequestInit & { expectJson?: boolean } = {},
): Promise<T> {
  const { expectJson = true, ...rest } = init;
  const res = await fetch(url, rest);
  if (!res.ok) {
    const body = await parseBody(res);
    throw new APIError(res.status, url, body);
  }
  if (!expectJson) {
    return (await res.text()) as unknown as T;
  }
  return (await res.json()) as T;
}

function get<T>(url: string, expectJson = true): Promise<T> {
  return request<T>(url, { method: "GET", expectJson });
}

function post<T>(url: string, body?: unknown): Promise<T> {
  return request<T>(url, {
    method: "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export interface ListReviewsOptions {
  /** Maximum rows to return. Backend defaults to 50 if omitted. */
  limit?: number;
  /** Skip this many rows. Pair with limit for pagination. */
  offset?: number;
}

export const api = {
  /** GET /health — liveness + (optional) Langfuse URL for the header link. */
  health(): Promise<HealthResponse> {
    return get<HealthResponse>("/health");
  },

  /** GET /reviews — list past, persisted reviews. */
  listReviews(opts: ListReviewsOptions = {}): Promise<ReviewSummary[]> {
    const params = new URLSearchParams();
    if (opts.limit !== undefined) params.set("limit", String(opts.limit));
    if (opts.offset !== undefined) params.set("offset", String(opts.offset));
    const qs = params.toString();
    return get<ReviewSummary[]>(qs ? `/reviews?${qs}` : "/reviews");
  },

  /** GET /reviews/{id} — full review row with findings + exploit proposals. */
  getReview(threadId: string): Promise<Review> {
    return get<Review>(`/reviews/${threadId}`);
  },

  /** GET /reviews/active — in-flight reviews snapshot, enriched with
   *  live token totals from the per-thread TokenUsageHandler. */
  listActiveReviews(): Promise<ActiveReview[]> {
    return get<ActiveReview[]>("/reviews/active");
  },

  /** POST /reviews/{id}/cancel — cancel an in-flight review. */
  cancelReview(threadId: string): Promise<CancelResult> {
    return post<CancelResult>(`/reviews/${threadId}/cancel`);
  },

  /** GET /reviews/{id}/critical-findings — list of critical findings
   *  joined with the review's exploit_proposals JSONB. */
  listCriticalFindings(threadId: string): Promise<CriticalFinding[]> {
    return get<CriticalFinding[]>(`/reviews/${threadId}/critical-findings`);
  },

  /** POST /reviews/{id}/exploits/{fid} — generate (or return existing)
   *  exploit PoC for one critical finding. Idempotent: a 200 means
   *  the existing record was returned; 201 means a fresh generation
   *  ran. Both shapes are identical, so callers don't usually need
   *  to distinguish them. */
  createExploit(threadId: string, findingId: string): Promise<ExploitProposal> {
    return post<ExploitProposal>(`/reviews/${threadId}/exploits/${findingId}`);
  },

  /** POST /review — kick off a new review. Returns the thread_id the
   *  UI uses to subscribe to progress over WebSocket. */
  triggerReview(
    body: PRReviewRequest | RepoReviewRequest,
  ): Promise<ReviewStarted> {
    return post<ReviewStarted>("/review", body);
  },

  /** GET /reports/{filename}.md — raw Markdown body of a saved
   *  repo-mode report. Returns the text verbatim (not JSON). */
  getReport(filename: string): Promise<string> {
    return get<string>(`/reports/${filename}`, false);
  },
};
