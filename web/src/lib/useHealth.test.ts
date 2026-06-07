// One-shot /health on mount. Used by the header to render the
// Langfuse traces link when the backend's `langfuse_url` is non-null.

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useHealth } from "./useHealth";

function stageHealth(body: unknown, opts: { status?: number } = {}) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
    new Response(JSON.stringify(body), {
      status: opts.status ?? 200,
      headers: { "Content-Type": "application/json" },
    }) as Response,
  );
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("useHealth", () => {
  it("fetches /health once on mount and surfaces the response", async () => {
    const spy = stageHealth({ status: "ok", langfuse_url: "http://localhost:3000" });
    const { result } = renderHook(() => useHealth());

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(spy).toHaveBeenCalledWith(
      "/health",
      expect.objectContaining({ method: "GET" }),
    );
    expect(result.current.health?.langfuse_url).toBe("http://localhost:3000");
  });

  it("returns health = null on fetch failure (fail-soft)", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(new Error("network down"));
    const { result } = renderHook(() => useHealth());

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(result.current.health).toBeNull();
  });

  it("returns health = null when /health returns a 5xx error", async () => {
    stageHealth({ detail: "lifespan not ready" }, { status: 503 });
    const { result } = renderHook(() => useHealth());

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(result.current.health).toBeNull();
  });
});
