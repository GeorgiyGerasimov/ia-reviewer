// Polling wrapper around `GET /reviews/active`. Mounts → fires
// once immediately, then every `intervalMs`. Exposes an ejector
// `refresh()` so callers (e.g. the review-form submit handler) can
// force a fresh poll without waiting for the next tick — the new
// thread appears in the sidebar within ~one round-trip rather than
// after the full interval.
//
// Why a hook and not a context provider: only one consumer
// (`ActiveReviewsList`) needs this data today. If a second consumer
// shows up, lift into a provider keyed on a shared interval — but
// not until then. YAGNI.

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { ActiveReview } from "../api/types";

export interface UseActiveReviewsOptions {
  /** Polling interval in milliseconds. The default matches the
   *  legacy template's 5-second cadence. */
  intervalMs?: number;
}

export interface UseActiveReviewsResult {
  items: ActiveReview[];
  /** True while the FIRST poll is in flight. Subsequent re-polls
   *  do not flip this — the sidebar shouldn't flash empty between
   *  ticks. Callers can use this to suppress an initial render
   *  while data loads. */
  loading: boolean;
  /** Force an immediate poll outside the interval. Resolves when
   *  the round-trip completes. */
  refresh: () => Promise<void>;
}

export function useActiveReviews({
  intervalMs = 5000,
}: UseActiveReviewsOptions = {}): UseActiveReviewsResult {
  const [items, setItems] = useState<ActiveReview[]>([]);
  const [loading, setLoading] = useState(true);
  // Ref so `refresh()` and the interval callback both call the
  // latest poll function (which captures the latest setItems).
  const isMountedRef = useRef(true);

  const poll = useCallback(async () => {
    try {
      const next = await api.listActiveReviews();
      if (isMountedRef.current) setItems(next);
    } catch {
      // Swallow: a polling tool that blanks the sidebar on a
      // transient 5xx is more annoying than useful. Keep the last
      // good snapshot; the next tick will retry.
    } finally {
      if (isMountedRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    isMountedRef.current = true;
    // Fire immediately on mount, then schedule the recurring tick.
    void poll();
    const id = setInterval(poll, intervalMs);
    return () => {
      isMountedRef.current = false;
      clearInterval(id);
    };
  }, [intervalMs, poll]);

  return { items, loading, refresh: poll };
}
