// Fetch a single review (`GET /reviews/{tid}`) when threadId
// changes. Exposes the typed `Review` payload + a `refresh()`
// callback that the parent can fire after the WS signals
// `__done__` so the report markdown + final token_usage land in
// the panel.

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useReview } from "./useReview";

function stageFetch(body: unknown, opts: { status?: number } = {}) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
    new Response(JSON.stringify(body), {
      status: opts.status ?? 200,
      headers: { "Content-Type": "application/json" },
    }) as Response,
  );
}

function review(over: Partial<Record<string, unknown>> = {}) {
  return {
    thread_id: "tid",
    mode: "repo",
    target_url: "https://github.com/o/r",
    report_markdown: "## report\n…",
    findings: [],
    exploit_proposals: [],
    token_usage: {},
    created_at: "2026-06-07T00:00:00Z",
    ...over,
  };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("useReview", () => {
  it("does NOT fetch when threadId is null", () => {
    const spy = vi.spyOn(globalThis, "fetch");
    renderHook(() => useReview(null));
    expect(spy).not.toHaveBeenCalled();
  });

  it("fetches GET /reviews/{tid} when threadId is provided", async () => {
    const spy = stageFetch(review({ thread_id: "tid-x" }));
    const { result } = renderHook(() => useReview("tid-x"));

    expect(result.current.loading).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(spy).toHaveBeenCalledWith(
      "/reviews/tid-x",
      expect.objectContaining({ method: "GET" }),
    );
    expect(result.current.loading).toBe(false);
    expect(result.current.review?.thread_id).toBe("tid-x");
  });

  it("re-fetches when threadId changes", async () => {
    stageFetch(review({ thread_id: "tid-a" }));
    const { result, rerender } = renderHook(({ tid }) => useReview(tid), {
      initialProps: { tid: "tid-a" as string | null },
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.review?.thread_id).toBe("tid-a");

    stageFetch(review({ thread_id: "tid-b" }));
    rerender({ tid: "tid-b" as string | null });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.review?.thread_id).toBe("tid-b");
  });

  it("exposes refresh() that re-fetches the current threadId", async () => {
    stageFetch(review({ report_markdown: "## v1" }));
    const { result } = renderHook(() => useReview("tid"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.review?.report_markdown).toBe("## v1");

    stageFetch(review({ report_markdown: "## v2" }));
    await act(async () => {
      await result.current.refresh();
    });
    expect(result.current.review?.report_markdown).toBe("## v2");
  });

  it("sets review to null on 404 without throwing", async () => {
    // The race window between submit and the persisted row is
    // expected — useReview shouldn't throw, the panel just shows
    // nothing until the next refresh (driven by WS __done__).
    stageFetch({ detail: "review not found" }, { status: 404 });
    const { result } = renderHook(() => useReview("missing"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.review).toBeNull();
    expect(result.current.loading).toBe(false);
  });

  it("clears review when threadId becomes null", async () => {
    stageFetch(review());
    const { result, rerender } = renderHook(({ tid }) => useReview(tid), {
      initialProps: { tid: "tid-a" as string | null },
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.review).not.toBeNull();

    rerender({ tid: null });
    // No fetch fires; review goes back to null.
    expect(result.current.review).toBeNull();
  });
});
