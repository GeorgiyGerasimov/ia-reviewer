// Top-level layout. First end-to-end integrated page:
//
//   * `ReviewForm` triggers a new review; thread_id flows back via
//     `onSubmitted` and selects the new run.
//   * `ActiveReviewsList` (left sidebar) polls /reviews/active every
//     5s, shows live token counter under each row, supports
//     selecting + cancelling.
//   * `WorkflowDiagram` (right pane) tracks the currently-selected
//     review via the WS hook — dots light up as nodes fire.
//
// Future panels (PastReviews, CriticalFindings, TokenUsagePanel,
// ReportPanel) slot in alongside WorkflowDiagram in the right pane.

import { useState, useCallback } from "react";
import { api } from "./api/client";
import { ActiveReviewsList } from "./components/ActiveReviewsList";
import { ReviewForm } from "./components/ReviewForm";
import { WorkflowDiagram } from "./components/WorkflowDiagram";
import { useActiveReviews } from "./lib/useActiveReviews";
import { useReviewStream } from "./lib/useReviewStream";

export function App() {
  const [currentThreadId, setCurrentThreadId] = useState<string | null>(null);
  const { items: activeItems, refresh: refreshActive } = useActiveReviews();
  const stream = useReviewStream(currentThreadId);

  const handleCancel = useCallback(
    async (threadId: string) => {
      try {
        await api.cancelReview(threadId);
      } catch {
        // Cancel races with the task completing naturally; either
        // way the row will drop off the next poll. Silent retry on
        // failure isn't useful here.
      }
      // Force-poll so the cancelled row disappears immediately
      // rather than waiting up to 5s for the next tick.
      await refreshActive();
    },
    [refreshActive],
  );

  const handleSubmitted = useCallback(
    (threadId: string) => {
      setCurrentThreadId(threadId);
      // The backend's BackgroundTask registers the thread within a
      // few ms; force-poll so the sidebar shows it without waiting.
      void refreshActive();
    },
    [refreshActive],
  );

  return (
    <main className="grid grid-cols-1 md:grid-cols-[260px_1fr] gap-6">
      <aside className="flex flex-col gap-6">
        <WorkflowDiagram
          nodeStatuses={stream.nodeStatuses}
          validationAccepted={stream.validationAccepted}
        />
        <ActiveReviewsList
          items={activeItems}
          currentThreadId={currentThreadId}
          onSelect={setCurrentThreadId}
          onCancel={handleCancel}
        />
      </aside>

      <section className="flex flex-col gap-6">
        <div className="rounded-xl border border-border bg-background p-4">
          <h2 className="text-base font-semibold mb-3 mt-0">Trigger a review</h2>
          <ReviewForm onSubmitted={handleSubmitted} />
        </div>

        {currentThreadId && (
          <div className="rounded-xl border border-border bg-background p-4">
            <header className="flex items-center justify-between mb-2">
              <h2 className="text-base font-semibold m-0">Current review</h2>
              <span className="text-xs text-muted-foreground font-mono">
                thread <code className="bg-transparent p-0">{currentThreadId}</code>
              </span>
            </header>
            <p className="text-sm text-muted-foreground m-0">
              {stream.isCancelled
                ? "Cancelled."
                : stream.isDone
                  ? "Done — see the workflow dots on the left."
                  : stream.connected
                    ? "Live — watching workflow events on the left."
                    : "Connecting…"}
            </p>
          </div>
        )}
      </section>
    </main>
  );
}
