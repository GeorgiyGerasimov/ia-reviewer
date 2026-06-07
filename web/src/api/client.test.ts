// Thin layer over `fetch`. Tests pin: (1) URL + method + body
// composition matches the FastAPI route signatures, (2) JSON parsing
// is type-safe, (3) non-2xx responses surface as `APIError` with
// status code + body — never as silent `null` or undefined.
//
// We mock `globalThis.fetch` directly via vi.spyOn. No msw, no
// nock — the surface is small enough that one helper to install
// fake responses per test keeps the suite hermetic and easy to read.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, APIError } from "./client";

/** Install a one-shot fetch mock that returns the given body /
 *  status. Returns the spy so tests can assert on the call args.
 *  Response.ok is derived from the status code by the constructor,
 *  so we only need to pass status. */
function mockFetch(body: unknown, opts: { status?: number } = {}) {
  const status = opts.status ?? 200;
  return vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }) as Response,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("api.health", () => {
  it("GETs /health and returns the parsed body", async () => {
    const spy = mockFetch({ status: "ok", langfuse_url: "http://localhost:3000" });
    const result = await api.health();
    expect(spy).toHaveBeenCalledWith("/health", expect.objectContaining({ method: "GET" }));
    expect(result.status).toBe("ok");
    expect(result.langfuse_url).toBe("http://localhost:3000");
  });

  it("returns null langfuse_url when tracing is disabled", async () => {
    mockFetch({ status: "ok", langfuse_url: null });
    const result = await api.health();
    expect(result.langfuse_url).toBeNull();
  });
});

describe("api.listReviews", () => {
  it("GETs /reviews with default limit/offset when no opts", async () => {
    const spy = mockFetch([]);
    await api.listReviews();
    expect(spy).toHaveBeenCalledWith("/reviews", expect.objectContaining({ method: "GET" }));
  });

  it("appends limit + offset as query string when provided", async () => {
    const spy = mockFetch([]);
    await api.listReviews({ limit: 20, offset: 10 });
    expect(spy).toHaveBeenCalledWith(
      "/reviews?limit=20&offset=10",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("returns the parsed array of summaries", async () => {
    mockFetch([
      {
        thread_id: "abc",
        mode: "pr",
        target_url: "https://github.com/o/r/pull/1",
        overall_severity: "major",
        finding_count: 3,
        validation_accepted: true,
        created_at: "2026-06-07T12:00:00Z",
      },
    ]);
    const result = await api.listReviews();
    expect(result).toHaveLength(1);
    expect(result[0].thread_id).toBe("abc");
    expect(result[0].finding_count).toBe(3);
  });
});

describe("api.getReview", () => {
  it("GETs /reviews/{id} and returns the parsed body", async () => {
    const spy = mockFetch({
      thread_id: "tid-x",
      mode: "repo",
      target_url: "https://github.com/o/r",
      report_markdown: "## Security review\n…",
      findings: [],
      exploit_proposals: [],
      token_usage: {},
      created_at: "2026-06-07T12:00:00Z",
    });
    const result = await api.getReview("tid-x");
    expect(spy).toHaveBeenCalledWith(
      "/reviews/tid-x",
      expect.objectContaining({ method: "GET" }),
    );
    expect(result.thread_id).toBe("tid-x");
  });

  it("throws APIError with the status code on 404", async () => {
    // `mockResolvedValueOnce` consumes one mock per fetch call, so
    // each assertion needs its own staged response — using two
    // separate awaits against `.rejects` would otherwise call fetch
    // twice and the second call would hit jsdom's no-base relative-URL
    // error instead of our staged response.
    mockFetch({ detail: "review not found" }, { status: 404 });
    let caught: unknown;
    try {
      await api.getReview("missing");
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeInstanceOf(APIError);
    expect(caught).toMatchObject({ status: 404 });
  });
});

describe("api.listActiveReviews", () => {
  it("returns an array with token totals embedded", async () => {
    mockFetch([
      {
        thread_id: "tid-live",
        mode: "repo",
        target: "https://github.com/o/r",
        ref: "main",
        started_at: "2026-06-07T12:00:00Z",
        elapsed_s: 45,
        tokens: { input: 5100, output: 270, calls: 2 },
      },
    ]);
    const result = await api.listActiveReviews();
    expect(result[0].tokens.input).toBe(5100);
    expect(result[0].tokens.output).toBe(270);
    expect(result[0].tokens.calls).toBe(2);
  });
});

describe("api.cancelReview", () => {
  it("POSTs /reviews/{id}/cancel and returns the status payload", async () => {
    const spy = mockFetch({ status: "cancelled", thread_id: "tid-z" });
    const result = await api.cancelReview("tid-z");
    expect(spy).toHaveBeenCalledWith(
      "/reviews/tid-z/cancel",
      expect.objectContaining({ method: "POST" }),
    );
    expect(result.status).toBe("cancelled");
    expect(result.thread_id).toBe("tid-z");
  });
});

describe("api.listCriticalFindings", () => {
  it("GETs the right URL and returns rows", async () => {
    const spy = mockFetch([
      {
        finding_id: "abc123",
        role: "injection",
        file: "src/x.py",
        line: 42,
        issue: "SQLi via f-string",
        severity: "critical",
        exploit_status: null,
        proposal_text: "",
        artifact: "",
      },
    ]);
    const result = await api.listCriticalFindings("tid-c");
    expect(spy).toHaveBeenCalledWith(
      "/reviews/tid-c/critical-findings",
      expect.objectContaining({ method: "GET" }),
    );
    expect(result[0].finding_id).toBe("abc123");
    expect(result[0].exploit_status).toBeNull();
  });
});

describe("api.createExploit", () => {
  it("POSTs to the per-finding endpoint and returns the proposal", async () => {
    const spy = mockFetch(
      {
        finding_id: "abc123",
        role: "injection",
        severity: "critical",
        proposal_text: "Inject ' OR 1=1…",
        artifact: "curl -X POST …",
        status: "approved",
        confidence: 8,
      },
      { status: 201 },
    );
    const result = await api.createExploit("tid-c", "abc123");
    expect(spy).toHaveBeenCalledWith(
      "/reviews/tid-c/exploits/abc123",
      expect.objectContaining({ method: "POST" }),
    );
    expect(result.status).toBe("approved");
  });

  it("treats 200 (idempotent) the same as 201 (created)", async () => {
    mockFetch(
      {
        finding_id: "abc123",
        role: "injection",
        severity: "critical",
        proposal_text: "…",
        artifact: "…",
        status: "approved",
        confidence: 8,
      },
      { status: 200 },
    );
    const result = await api.createExploit("tid-c", "abc123");
    expect(result.status).toBe("approved");
  });

  it("throws APIError on 409 cap-reached", async () => {
    mockFetch({ detail: "exploit cap reached (3 per review)" }, { status: 409 });
    let caught: unknown;
    try {
      await api.createExploit("tid-c", "fid");
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeInstanceOf(APIError);
    expect(caught).toMatchObject({ status: 409 });
  });
});

describe("api.triggerReview", () => {
  it("POSTs PR-mode body and returns thread_id", async () => {
    const spy = mockFetch(
      { status: "started", thread_id: "new-tid", pr_url: "https://github.com/o/r/pull/1" },
      { status: 202 },
    );
    const result = await api.triggerReview({
      pr_url: "https://github.com/o/r/pull/1",
    });
    const call = spy.mock.calls[0];
    const [url, init] = call as [string, RequestInit];
    expect(url).toBe("/review");
    expect(init.method).toBe("POST");
    expect(init.body).toBe(
      JSON.stringify({ pr_url: "https://github.com/o/r/pull/1" }),
    );
    expect(result.thread_id).toBe("new-tid");
  });

  it("POSTs repo-mode body with ref + scope", async () => {
    const spy = mockFetch(
      {
        status: "started",
        thread_id: "new-tid",
        repo_url: "https://github.com/o/r",
        ref: "main",
      },
      { status: 202 },
    );
    await api.triggerReview({
      repo_url: "https://github.com/o/r",
      ref: "main",
      scope: ["injection"],
    });
    const init = spy.mock.calls[0][1] as RequestInit;
    expect(init.body).toBe(
      JSON.stringify({
        repo_url: "https://github.com/o/r",
        ref: "main",
        scope: ["injection"],
      }),
    );
  });

  it("throws APIError when body is invalid (400)", async () => {
    mockFetch({ error: "one of pr_url or repo_url is required" }, { status: 400 });
    await expect(api.triggerReview({} as never)).rejects.toBeInstanceOf(APIError);
  });
});

describe("api.getReport", () => {
  it("fetches the raw markdown text (not JSON)", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response("## Security review\n\nfindings…", {
        status: 200,
        headers: { "Content-Type": "text/markdown" },
      }) as Response,
    );
    const result = await api.getReport("tid-x.md");
    expect(spy).toHaveBeenCalledWith(
      "/reports/tid-x.md",
      expect.objectContaining({ method: "GET" }),
    );
    expect(result).toContain("## Security review");
  });
});

describe("APIError", () => {
  it("carries status, body, and URL for diagnostic logging", async () => {
    mockFetch({ detail: "boom" }, { status: 500 });
    try {
      await api.getReview("tid-x");
      throw new Error("should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(APIError);
      expect((err as APIError).status).toBe(500);
      expect((err as APIError).url).toBe("/reviews/tid-x");
      // Body is the parsed payload (or raw text fallback).
      expect((err as APIError).body).toEqual({ detail: "boom" });
    }
  });

  it("falls back to raw text body when the response isn't JSON", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response("Internal Server Error", {
        status: 500,
        headers: { "Content-Type": "text/plain" },
      }) as Response,
    );
    try {
      await api.getReview("tid-x");
      throw new Error("should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(APIError);
      expect((err as APIError).body).toBe("Internal Server Error");
    }
  });
});
