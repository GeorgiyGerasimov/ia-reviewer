// Sidebar list of historical reviews. Pure-display: items +
// callbacks via props; parent wires `usePastReviews` to it.
//
// Differs from `ActiveReviewsList` in two ways:
//   * Renders an inline placeholder when empty (newcomers should
//     see a "No persisted reviews yet" hint, not a missing block).
//   * No Stop / Cancel — past reviews are done; they only support
//     re-opening (onSelect).

import { cn } from "@/lib/utils";
import { normalizeSeverity } from "../lib/format";
import type { ReviewSummary } from "../api/types";

export interface PastReviewsListProps {
  items: ReviewSummary[];
  currentThreadId?: string | null;
  onSelect: (threadId: string) => void;
}

// Severity → CSS-token-name for the colored dot. `normalizeSeverity`
// upstream ensures we only ever land on these five keys.
const DOT_BG: Record<string, string> = {
  critical: "bg-severity-critical",
  major: "bg-severity-major",
  minor: "bg-severity-minor",
  info: "bg-muted-foreground",
  rejected: "bg-destructive",
};

function rowSeverity(item: ReviewSummary): string {
  // Rejected reviews ship `severity: "rejected"` AND `rejected:
  // true`. Either flag tips us into the red branch.
  if (item.rejected || item.severity === "rejected") return "rejected";
  return normalizeSeverity(item.severity);
}

export function PastReviewsList({
  items,
  currentThreadId = null,
  onSelect,
}: PastReviewsListProps) {
  return (
    <div className="rounded-xl border border-border bg-background p-4">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground m-0 mb-3">
        Past reviews
      </h3>
      {items.length === 0 ? (
        <div className="text-xs italic text-muted-foreground py-2">
          No persisted reviews yet.
        </div>
      ) : (
        <ul className="list-none p-0 m-0 flex flex-col gap-1.5 max-h-[360px] overflow-y-auto">
          {items.map((item) => {
            const isCurrent = item.thread_id === currentThreadId;
            const severity = rowSeverity(item);
            return (
              <li key={item.thread_id}>
                <div
                  role="button"
                  tabIndex={0}
                  data-testid={`past-row-${item.thread_id}`}
                  data-current={isCurrent ? "true" : "false"}
                  data-severity={severity}
                  aria-label={`Open past review ${item.thread_id}`}
                  onClick={() => onSelect(item.thread_id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSelect(item.thread_id);
                    }
                  }}
                  className={cn(
                    "cursor-pointer rounded-md p-2 text-xs border transition-colors",
                    isCurrent
                      ? "border-primary bg-accent"
                      : "border-transparent bg-card hover:bg-accent/60 hover:border-border",
                  )}
                >
                  <div className="flex items-center gap-2 font-semibold mb-0.5">
                    <span
                      aria-hidden
                      className={cn("inline-block w-2 h-2 rounded-full shrink-0", DOT_BG[severity])}
                    />
                    <span className="capitalize">{severity}</span>
                    <span className="ml-auto text-muted-foreground font-normal text-[11px] tabular-nums">
                      {item.findings_count} {item.findings_count === 1 ? "finding" : "findings"}
                    </span>
                  </div>
                  <div className="text-foreground text-[11px] overflow-hidden text-ellipsis whitespace-nowrap">
                    {item.target_url}
                  </div>
                  <div className="text-[10px] text-muted-foreground mt-0.5">
                    {item.created_at}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
