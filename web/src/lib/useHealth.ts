// One-shot fetch of `/health` on mount. Exposes the typed
// HealthResponse so the header can decide whether to render the
// "View traces ↗" link to Langfuse.
//
// We don't poll — the langfuse_url doesn't change at runtime;
// any subsequent change requires an app restart and the user
// will hit refresh. Single fetch keeps the surface minimal.
//
// Fail-soft: on any error we return `health = null`, which
// collapses the link in the header. The page itself is fine —
// the polling APIs that drive the rest of the UI run
// independently.

import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { HealthResponse } from "../api/types";

export interface UseHealthResult {
  health: HealthResponse | null;
}

export function useHealth(): UseHealthResult {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    void api
      .health()
      .then((h) => {
        if (isMountedRef.current) setHealth(h);
      })
      .catch((err) => {
        // Logged once; the UI renders without the link.
        console.warn("useHealth: /health fetch failed", err);
      });
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  return { health };
}
