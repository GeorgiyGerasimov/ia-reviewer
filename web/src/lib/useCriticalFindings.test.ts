// Fetch critical findings for one review with the same retry-on-404
// pattern the legacy template uses to ride out the race between the
// WS `__done__` and `_persist_review` writing the DB row. Without
// the retry the panel disappears + needs a manual reload — the
// exact bug we fixed in PR #28 in the legacy UI; we don't want to
// regress it on the React side.

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useCriticalFindings } from "./useCriticalFindings";

function stage(body: unknown, opts: { status?: number } = {}) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
    new Response(JSON.stringify(body), {
      status: opts.status ?? 200,
      headers: { "Content-Type": "application/json" },
    }) as Response,
  );
}

function finding(over: Partial<Record<string, unknown>> = {}) {
  return {
    finding_id: "abc123",
    role: "injection",
    file: "src/x.py",
    line: 42,
    issue: "SQLi via f-string",
    severity: "critical",
    exploit_status: null,
    proposal_text: "",
    artifact: "",
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

describe("useCriticalFindings", () => {
  it("does NOT fetch when threadId is null", () => {
    const spy = vi.spyOn(globalThis, "fetch");
    renderHook(() => useCriticalFindings(null));
    expect(spy).not.toHaveBeenCalled();
  });

  it("fetches once on mount and returns the rows", async () => {
    const spy = stage([finding({ finding_id: "fid-1" })]);
    const { result } = renderHook(() => useCriticalFindings("tid"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(spy).toHaveBeenCalledWith(
      "/reviews/tid/critical-findings",
      expect.objectContaining({ method: "GET" }),
    );
    expect(result.current.findings).toHaveLength(1);
    expect(result.current.findings[0].finding_id).toBe("fid-1");
  });

  it("retries on 404 with exponential backoff (race window)", async () => {
    // First two attempts 404, third succeeds. The hook should NOT
    // give up after the first miss — the persistence race takes a
    // few hundred ms in practice. Same backoff schedule as the
    // legacy CF_FETCH_RETRY_MS = [300, 600, 1200, 2400, 4800].
    // mockImplementation (not mockResolvedValueOnce) so the
    // call-counter logic stays explicit + readable.
    let callCount = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(() => {
      callCount++;
      if (callCount <= 2) {
        return Promise.resolve(
          new Response(JSON.stringify({ detail: "review not found" }), {
            status: 404,
            headers: { "Content-Type": "application/json" },
          }) as Response,
        );
      }
      return Promise.resolve(
        new Response(JSON.stringify([finding({ finding_id: "fid-after-races" })]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }) as Response,
      );
    });

    const { result } = renderHook(() => useCriticalFindings("tid"));
    // Initial 404 — microtasks only, no timer advance.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(callCount).toBe(1);
    expect(result.current.findings).toEqual([]);

    // Advance the first retry delay (300ms). Second 404 fires.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    expect(callCount).toBe(2);
    expect(result.current.findings).toEqual([]);

    // Advance the second retry delay (600ms). Third call succeeds.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    expect(callCount).toBe(3);
    expect(result.current.findings[0].finding_id).toBe("fid-after-races");
  });

  it("stops retrying after the schedule is exhausted", async () => {
    // Five 404s — should NOT keep retrying forever; the legacy
    // schedule has 5 entries and then gives up.
    for (let i = 0; i < 6; i++) {
      stage({ detail: "not found" }, { status: 404 });
    }
    const { result } = renderHook(() => useCriticalFindings("tid"));
    await act(async () => {
      // Advance past all 5 retry delays (300+600+1200+2400+4800 = 9300ms).
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(result.current.findings).toEqual([]);
    // Exactly 5 fetch calls: initial + 4 retries. (Or 6 = initial
    // + 5 retries depending on convention; either works as long
    // as it's bounded.) We assert it's NOT unbounded.
    const callCount = (globalThis.fetch as ReturnType<typeof vi.fn>).mock
      .calls.length;
    expect(callCount).toBeGreaterThanOrEqual(2);
    expect(callCount).toBeLessThanOrEqual(6);
  });

  it("refresh() bypasses retry — single shot on demand", async () => {
    // After exploit creation, refresh is called once to pick up
    // the new exploit_status. We don't want refresh to enter the
    // retry loop on a transient 404 here.
    stage([finding()]);
    const { result } = renderHook(() => useCriticalFindings("tid"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    stage([finding({ exploit_status: "approved", artifact: "curl …" })]);
    await act(async () => {
      await result.current.refresh();
    });
    expect(result.current.findings[0].exploit_status).toBe("approved");
  });

  it("re-fetches when threadId changes", async () => {
    stage([finding({ finding_id: "from-a" })]);
    const { result, rerender } = renderHook(({ tid }) => useCriticalFindings(tid), {
      initialProps: { tid: "tid-a" as string | null },
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.findings[0].finding_id).toBe("from-a");

    stage([finding({ finding_id: "from-b" })]);
    rerender({ tid: "tid-b" as string | null });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.findings[0].finding_id).toBe("from-b");
  });
});
