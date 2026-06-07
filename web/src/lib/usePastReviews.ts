// Fetch the past-reviews list once on mount and on every
// `refresh()` call. No interval — past reviews are historical and
// don't change every 5s (active reviews already has that polling
// story via `useActiveReviews`).
//
// Use `refresh()` from the parent after a review completes so the
// new row shows up in the sidebar without a full page reload.

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { ReviewSummary } from "../api/types";

export interface UsePastReviewsOptions {
  /** Max rows to fetch. Defaults to 50 — matches the backend's
   *  default `?limit=` when omitted. */
  limit?: number;
}

export interface UsePastReviewsResult {
  items: ReviewSummary[];
  loading: boolean;
  refresh: () => Promise<void>;
}

export function usePastReviews({
  limit = 50,
}: UsePastReviewsOptions = {}): UsePastReviewsResult {
  const [items, setItems] = useState<ReviewSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const isMountedRef = useRef(true);

  const fetchOnce = useCallback(async () => {
    try {
      const next = await api.listReviews({ limit });
      if (isMountedRef.current) setItems(next);
    } catch {
      // Sidebar that blanks on a 5xx blip is worse than a slightly
      // stale list. Keep the last good snapshot.
    } finally {
      if (isMountedRef.current) setLoading(false);
    }
  }, [limit]);

  useEffect(() => {
    isMountedRef.current = true;
    void fetchOnce();
    return () => {
      isMountedRef.current = false;
    };
  }, [fetchOnce]);

  return { items, loading, refresh: fetchOnce };
}
