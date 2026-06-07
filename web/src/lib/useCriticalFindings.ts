// Fetch the critical-findings list for one review with retry-on-404
// for the persistence race window. `_persist_review` writes the DB
// row AFTER the WS broadcasts `__done__`, so a panel that fetches
// the moment the WS terminal arrives might land on a 404 if it
// raced ahead. The legacy template's CF_FETCH_RETRY_MS schedule
// rode that out with 5 backoff attempts; we mirror it here.
//
// Schedule lives as a module-level constant so it's easy to tune
// from one place. Each entry is the DELAY before the next attempt;
// totaling ~9.3s of patience.

import { useCallback, useEffect, useRef, useState } from "react";
import { api, APIError } from "../api/client";
import type { CriticalFinding } from "../api/types";

const RETRY_DELAYS_MS = [300, 600, 1200, 2400, 4800] as const;

export interface UseCriticalFindingsResult {
  findings: CriticalFinding[];
  loading: boolean;
  /** Forced re-fetch outside the retry loop — single shot.
   *  Use this after a POST /reviews/{tid}/exploits/{fid} so the
   *  row picks up the new exploit_status without paying the
   *  9-second backoff if the new row 404s for any reason. */
  refresh: () => Promise<void>;
}

export function useCriticalFindings(
  threadId: string | null,
): UseCriticalFindingsResult {
  const [findings, setFindings] = useState<CriticalFinding[]>([]);
  const [loading, setLoading] = useState<boolean>(threadId !== null);
  const isMountedRef = useRef(true);
  // Held in a ref so the retry chain can cancel pending timeouts
  // on unmount / threadId change without re-creating the closure.
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const cancelPendingRetry = useCallback(() => {
    if (retryTimerRef.current !== null) {
      clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    }
  }, []);

  // Single-shot fetcher — used by `refresh()` and also by the
  // retry chain's individual attempts.
  const fetchOnce = useCallback(
    async (tid: string): Promise<"ok" | "race" | "error"> => {
      try {
        const next = await api.listCriticalFindings(tid);
        if (isMountedRef.current) setFindings(next);
        return "ok";
      } catch (err) {
        if (err instanceof APIError && err.status === 404) {
          return "race";
        }
        console.warn("useCriticalFindings: GET failed", err);
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
        // Schedule exhausted. The DB row may simply not exist for
        // this thread (older reviews from before persistence
        // landed, or a wrong thread_id). Stop here and let the
        // parent re-trigger via `refresh()` if it wants to.
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
    if (!threadId) {
      setFindings([]);
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

  return { findings, loading, refresh };
}
