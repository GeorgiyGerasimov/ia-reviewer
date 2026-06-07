// Fetch one review (`GET /reviews/{tid}`) on mount + whenever
// threadId changes. Returns the typed Review payload plus a
// `refresh()` callback the parent can fire after the WS terminal
// `__done__` so the panel picks up the final report markdown +
// token_usage written by `_persist_review`.
//
// On 404 the hook silently sets review to null — the race between
// the BackgroundTask completing and the DB row being written is
// known + expected. The parent UI shows "loading" until the next
// refresh succeeds.

import { useCallback, useEffect, useRef, useState } from "react";
import { api, APIError } from "../api/client";
import type { Review } from "../api/types";

export interface UseReviewResult {
  review: Review | null;
  loading: boolean;
  refresh: () => Promise<void>;
}

export function useReview(threadId: string | null): UseReviewResult {
  const [review, setReview] = useState<Review | null>(null);
  const [loading, setLoading] = useState<boolean>(threadId !== null);
  const isMountedRef = useRef(true);

  const fetchOnce = useCallback(async () => {
    if (!threadId) return;
    try {
      const next = await api.getReview(threadId);
      if (isMountedRef.current) setReview(next);
    } catch (err) {
      if (isMountedRef.current) {
        // 404 = persistence race or unknown thread → fall through
        // to null. Anything else gets logged but doesn't crash.
        if (err instanceof APIError && err.status === 404) {
          setReview(null);
        } else {
          console.warn("useReview: GET /reviews/{tid} failed", err);
        }
      }
    } finally {
      if (isMountedRef.current) setLoading(false);
    }
  }, [threadId]);

  useEffect(() => {
    isMountedRef.current = true;
    if (!threadId) {
      // Switching away from a thread wipes the panel — otherwise
      // stale data lingers.
      setReview(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    void fetchOnce();
    return () => {
      isMountedRef.current = false;
    };
  }, [threadId, fetchOnce]);

  return { review, loading, refresh: fetchOnce };
}
