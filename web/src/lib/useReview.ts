// Fetch one review (`GET /reviews/{tid}`) on mount + whenever
// threadId changes. Returns the typed Review payload plus a
// `refresh()` callback the parent can fire after the WS terminal
// `__done__` so the panel picks up the final report markdown +
// token_usage written by `_persist_review`.
//
// `_persist_review` writes the DB row AFTER the WS broadcasts
// `__done__`, so a fetch that fires the moment the terminal envelope
// arrives can race ahead and 404. We mirror the same backoff
// retry chain as `useCriticalFindings` (300/600/1200/2400/4800 ms,
// ~9.3s total patience). The legacy template's CF_FETCH_RETRY_MS
// did the same for `/reports/{tid}.md` — without it the final
// report + token-usage panels stayed empty after every run.
//
// `refresh()` bypasses the retry loop and fetches once — used by
// the parent on WS `__done__` to grab the persisted view without
// re-paying the backoff if it already landed.

import { useCallback, useEffect, useRef, useState } from "react";
import { api, APIError } from "../api/client";
import type { Review } from "../api/types";

const RETRY_DELAYS_MS = [300, 600, 1200, 2400, 4800] as const;

export interface UseReviewResult {
  review: Review | null;
  loading: boolean;
  refresh: () => Promise<void>;
}

export function useReview(threadId: string | null): UseReviewResult {
  const [review, setReview] = useState<Review | null>(null);
  const [loading, setLoading] = useState<boolean>(threadId !== null);
  const isMountedRef = useRef(true);
  // Pending retry timeout — held in a ref so the cleanup function
  // and `refresh()` can cancel it without rebuilding the closure.
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const cancelPendingRetry = useCallback(() => {
    if (retryTimerRef.current !== null) {
      clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    }
  }, []);

  // Single-shot fetcher. Returns the outcome so the retry chain can
  // decide whether to schedule another attempt.
  const fetchOnce = useCallback(
    async (tid: string): Promise<"ok" | "race" | "error"> => {
      try {
        const next = await api.getReview(tid);
        if (isMountedRef.current) setReview(next);
        return "ok";
      } catch (err) {
        if (err instanceof APIError && err.status === 404) {
          return "race";
        }
        console.warn("useReview: GET /reviews/{tid} failed", err);
        return "error";
      }
    },
    [],
  );

  // Recursive retry chain. Each invocation tries once and, on
  // "race", schedules the next attempt — until the schedule is
  // exhausted, then gives up silently.
  const fetchWithRetry = useCallback(
    async (tid: string, attemptIndex: number) => {
      const outcome = await fetchOnce(tid);
      if (!isMountedRef.current) return;
      if (outcome === "ok" || outcome === "error") {
        setLoading(false);
        return;
      }
      // outcome === "race"
      if (attemptIndex >= RETRY_DELAYS_MS.length) {
        setLoading(false);
        return;
      }
      const delay = RETRY_DELAYS_MS[attemptIndex];
      retryTimerRef.current = setTimeout(() => {
        retryTimerRef.current = null;
        void fetchWithRetry(tid, attemptIndex + 1);
      }, delay);
    },
    [fetchOnce],
  );

  const refresh = useCallback(async () => {
    if (!threadId) return;
    cancelPendingRetry();
    await fetchOnce(threadId);
  }, [threadId, fetchOnce, cancelPendingRetry]);

  useEffect(() => {
    isMountedRef.current = true;
    cancelPendingRetry();
    // Always reset on threadId change. Two scenarios:
    //   * threadId → null: switching away from any review.
    //   * tid A → tid B (both non-null): switching between reviews.
    // Without this synchronous reset, the panel would keep showing
    // the previous review's data while the new fetch is in flight —
    // particularly painful for an in-progress review whose 404
    // retry chain runs for ~10s before giving up.
    setReview(null);
    if (!threadId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    void fetchWithRetry(threadId, 0);
    return () => {
      isMountedRef.current = false;
      cancelPendingRetry();
    };
  }, [threadId, fetchWithRetry, cancelPendingRetry]);

  return { review, loading, refresh };
}
