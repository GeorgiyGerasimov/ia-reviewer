// Top-level layout. Wires the React UI end-to-end:
//
//   * Sidebar: WorkflowDiagram + ActiveReviewsList + PastReviewsList
//   * Main pane: ReviewForm → CriticalFindings → TokenUsage → Report
//
// State management is intentionally flat — one `currentThreadId`
// drives the four data hooks (`useReviewStream`, `useReview`,
// `useCriticalFindings`) which in turn drive their panels. When
// the WS sees `__done__` we cascade a refresh across the
// HTTP-backed hooks so the persisted view catches up with the
// just-finished run without a page reload.

import { useCallback, useEffect, useMemo, useState } from "react";
import { api, APIError } from "./api/client";
import { ActiveReviewsList } from "./components/ActiveReviewsList";
import { CriticalFindingsPanel } from "./components/CriticalFindingsPanel";
import { PastReviewsList } from "./components/PastReviewsList";
import { ReportPanel } from "./components/ReportPanel";
import { ReviewForm } from "./components/ReviewForm";
import { TokenUsagePanel } from "./components/TokenUsagePanel";
import { WorkflowDiagram } from "./components/WorkflowDiagram";
import { useActiveReviews } from "./lib/useActiveReviews";
import { useCriticalFindings } from "./lib/useCriticalFindings";
import { usePastReviews } from "./lib/usePastReviews";
import { useReview } from "./lib/useReview";
import { useReviewStream } from "./lib/useReviewStream";

// Mirrors `MAX_EXPLOIT_PROPOSALS` in
// `src/agents/exploit_proposal.py`. Kept in sync by humans for
// now — a future PR could expose it via /health to remove the
// duplication.
const MAX_EXPLOIT_PROPOSALS = 3;

export function App() {
  const [currentThreadId, setCurrentThreadId] = useState<string | null>(null);
  const [creatingExploits, setCreatingExploits] = useState<Set<string>>(
    () => new Set(),
  );

  const { items: activeItems, refresh: refreshActive } = useActiveReviews();
  const { items: pastItems, refresh: refreshPast } = usePastReviews();
  const { review, loading: reviewLoading, refresh: refreshReview } = useReview(
    currentThreadId,
  );
  const { findings, refresh: refreshFindings } = useCriticalFindings(
    currentThreadId,
  );
  const stream = useReviewStream(currentThreadId);

  // Cascade refresh when the WS signals the review is done. The
  // BackgroundTask persists the row + writes the report markdown
  // AFTER it broadcasts `__done__`, so we have to re-poll the
  // HTTP-backed views (they were on stale data when the WS
  // arrived).
  useEffect(() => {
    if (!stream.isDone) return;
    void refreshReview();
    void refreshFindings();
    void refreshActive();
    void refreshPast();
  }, [stream.isDone, refreshReview, refreshFindings, refreshActive, refreshPast]);

  // Number of exploit proposals that already exist for this
  // review (any status). Anything ≥ MAX_EXPLOIT_PROPOSALS means
  // the cap has been reached and fresh-status findings can't
  // create new ones.
  const exploitCount = useMemo(
    () => findings.filter((f) => f.exploit_status !== null).length,
    [findings],
  );
  const capReached = exploitCount >= MAX_EXPLOIT_PROPOSALS;

  const handleCreateExploit = useCallback(
    async (findingId: string) => {
      if (!currentThreadId) return;
      setCreatingExploits((prev) => {
        const next = new Set(prev);
        next.add(findingId);
        return next;
      });
      try {
        await api.createExploit(currentThreadId, findingId);
      } catch (err) {
        // 409 = cap reached after we last saw findings — refresh
        // will reconcile. Any other 4xx/5xx ends up in the UI
        // staying as-is; the user can retry.
        if (!(err instanceof APIError)) {
          console.warn("createExploit failed", err);
        }
      } finally {
        setCreatingExploits((prev) => {
          const next = new Set(prev);
          next.delete(findingId);
          return next;
        });
        await refreshFindings();
      }
    },
    [currentThreadId, refreshFindings],
  );

  const handleCancel = useCallback(
    async (threadId: string) => {
      try {
        await api.cancelReview(threadId);
      } catch {
        // Cancel races with the task completing naturally; the
        // sidebar will drop the row on the next poll either way.
      }
      await refreshActive();
    },
    [refreshActive],
  );

  const handleSubmitted = useCallback(
    (threadId: string) => {
      setCurrentThreadId(threadId);
      // The BackgroundTask registers the thread within a few ms;
      // force-poll so the sidebar shows it without waiting.
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
        <PastReviewsList
          items={pastItems}
          currentThreadId={currentThreadId}
          onSelect={setCurrentThreadId}
        />
      </aside>

      <section className="flex flex-col gap-6">
        <div className="rounded-xl border border-border bg-background p-4">
          <h2 className="text-base font-semibold mb-3 mt-0">Trigger a review</h2>
          <ReviewForm onSubmitted={handleSubmitted} />
        </div>

        <CriticalFindingsPanel
          findings={findings}
          creating={creatingExploits}
          capReached={capReached}
          onCreate={handleCreateExploit}
        />

        <TokenUsagePanel usage={review?.token_usage} />

        <ReportPanel
          threadId={currentThreadId}
          markdown={review?.report_markdown ?? ""}
          loading={reviewLoading}
        />
      </section>
    </main>
  );
}
