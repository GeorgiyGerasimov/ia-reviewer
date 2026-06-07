// Fetch the past-reviews list once on mount; expose a `refresh()`
// ejector for forced re-poll (used after a review completes so the
// new row appears without a page reload).
//
// No interval — past reviews are historical, they don't change
// every 5 seconds. Active reviews already has that polling story.

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { usePastReviews } from "./usePastReviews";

function stageFetch(body: unknown, status = 200) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }) as Response,
  );
}

function summary(over: Partial<Record<string, unknown>> = {}) {
  return {
    thread_id: "tid",
    mode: "repo",
    target_url: "https://github.com/o/r",
    severity: "major",
    findings_count: 3,
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

describe("usePastReviews", () => {
  it("fetches GET /reviews?limit=20 on mount", async () => {
    const spy = stageFetch([summary({ thread_id: "tid-a" })]);
    const { result } = renderHook(() => usePastReviews({ limit: 20 }));

    expect(result.current.loading).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(result.current.loading).toBe(false);
    expect(result.current.items).toHaveLength(1);
    expect(result.current.items[0].thread_id).toBe("tid-a");
    expect(spy).toHaveBeenCalledWith(
      "/reviews?limit=20",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("uses limit=50 by default when no opts passed", async () => {
    const spy = stageFetch([]);
    renderHook(() => usePastReviews());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    // Backend's own default cap is 50; we mirror it here so the
    // UI's default matches what the API serves when limit is
    // omitted entirely.
    expect(spy).toHaveBeenCalledWith(
      "/reviews?limit=50",
      expect.anything(),
    );
  });

  it("exposes refresh() that re-polls on demand", async () => {
    stageFetch([summary({ thread_id: "stale" })]);
    const { result } = renderHook(() => usePastReviews());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.items[0].thread_id).toBe("stale");

    stageFetch([summary({ thread_id: "fresh" })]);
    await act(async () => {
      await result.current.refresh();
    });
    expect(result.current.items[0].thread_id).toBe("fresh");
  });

  it("keeps the previous items array on a fetch failure", async () => {
    // Historical reviews are mostly-static. A 5xx blip shouldn't
    // make the sidebar flash empty.
    stageFetch([summary({ thread_id: "stable" })]);
    const { result } = renderHook(() => usePastReviews());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response("boom", { status: 500 }) as Response,
    );
    await act(async () => {
      await result.current.refresh();
    });
    expect(result.current.items[0].thread_id).toBe("stable");
  });
});
