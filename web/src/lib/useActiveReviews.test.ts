// Polling hook: fetches `/reviews/active` on mount and every N ms
// thereafter. Surface mirrors the API: callers get the latest array
// + a `loading` flag + a `refresh()` ejector to force an immediate
// poll outside the interval (used when a Create-review submit wants
// to see the new entry without waiting up to 5s).
//
// Tests use Vitest's fake timers — no actual setInterval delay, no
// flake. `vi.advanceTimersByTimeAsync` flushes pending microtasks
// alongside time, so we never combine it with RTL's `waitFor`
// (which polls in real wall-clock time and would hang under fake
// timers).

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useActiveReviews } from "./useActiveReviews";

function stagePoll(rows: unknown[], status = 200) {
  vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
    new Response(JSON.stringify(rows), {
      status,
      headers: { "Content-Type": "application/json" },
    }) as Response,
  );
}

function activeRow(over: Partial<Record<string, unknown>> = {}) {
  return {
    thread_id: "tid",
    mode: "repo",
    target: "https://github.com/o/r",
    started_at: "2026-06-07T00:00:00Z",
    elapsed_s: 0,
    tokens: { input: 0, output: 0, calls: 0 },
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

describe("useActiveReviews", () => {
  it("fetches /reviews/active immediately on mount", async () => {
    stagePoll([activeRow({ thread_id: "tid-1" })]);
    const { result } = renderHook(() => useActiveReviews({ intervalMs: 5000 }));
    expect(result.current.loading).toBe(true);

    // Flush microtasks so the mount-side fetch promise resolves
    // and state updates land before we assert.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(result.current.loading).toBe(false);
    expect(result.current.items).toHaveLength(1);
    expect(result.current.items[0].thread_id).toBe("tid-1");
  });

  it("re-polls every intervalMs ms", async () => {
    stagePoll([activeRow({ thread_id: "tid-a" })]);
    const { result } = renderHook(() => useActiveReviews({ intervalMs: 5000 }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.items[0].thread_id).toBe("tid-a");

    // Stage what the NEXT scheduled poll will see, then advance
    // past the interval boundary. Both the timer firing AND its
    // resolution flush in one go.
    stagePoll([activeRow({ thread_id: "tid-b" })]);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(result.current.items[0].thread_id).toBe("tid-b");
  });

  it("exposes refresh() that polls immediately outside the interval", async () => {
    stagePoll([]);
    const { result } = renderHook(() => useActiveReviews({ intervalMs: 5000 }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    // Stage a fresh response, then force a poll via the ejector.
    stagePoll([activeRow({ thread_id: "fresh" })]);
    await act(async () => {
      await result.current.refresh();
    });
    expect(result.current.items[0].thread_id).toBe("fresh");
  });

  it("keeps the previous items array on a transient fetch failure", async () => {
    // A polling tool shouldn't blank the sidebar when the network
    // hiccups — keep the last good snapshot until the next success.
    stagePoll([activeRow({ thread_id: "stable" })]);
    const { result } = renderHook(() => useActiveReviews({ intervalMs: 5000 }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.items[0].thread_id).toBe("stable");

    // Simulate a 500. The hook should swallow it (not throw) and
    // leave the items untouched.
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response("boom", { status: 500 }) as Response,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(result.current.items[0].thread_id).toBe("stable");
  });

  it("stops polling on unmount", async () => {
    stagePoll([]);
    const { unmount } = renderHook(() => useActiveReviews({ intervalMs: 5000 }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(globalThis.fetch).toHaveBeenCalledTimes(1);

    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    // No further fetch beyond the first one — the interval was
    // cleared on unmount.
    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
  });
});
