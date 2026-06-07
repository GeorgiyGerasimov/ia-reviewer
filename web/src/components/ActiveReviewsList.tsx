// Sidebar list of in-flight reviews. Pure display — `items`,
// `currentThreadId`, `onSelect`, `onCancel` come from the parent.
// The App container wires `useActiveReviews` + `api.cancelReview`
// to this. No fetch, no internal state.

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { fmtElapsed } from "../lib/format";
import { TokenSummary } from "./TokenSummary";
import type { ActiveReview } from "../api/types";

export interface ActiveReviewsListProps {
  items: ActiveReview[];
  /** thread_id of the currently-watched review. The matching row
   *  gets a stronger background + accent border. */
  currentThreadId?: string | null;
  onSelect: (threadId: string) => void;
  onCancel: (threadId: string) => void;
}

export function ActiveReviewsList({
  items,
  currentThreadId = null,
  onSelect,
  onCancel,
}: ActiveReviewsListProps) {
  // Hide the panel entirely when empty — operators shouldn't see
  // a permanent placeholder on the sidebar.
  if (items.length === 0) return null;

  return (
    <div className="rounded-xl border border-border bg-background p-4">
      <div className="flex items-center gap-2 mb-3">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground m-0">
          Active reviews
        </h3>
        <span className="inline-flex items-center justify-center min-w-[1.4rem] h-[1.1rem] px-1.5 rounded-full bg-primary text-primary-foreground text-[11px] font-semibold tabular-nums">
          {items.length}
        </span>
      </div>
      <ul className="list-none p-0 m-0 flex flex-col gap-1.5">
        {items.map((item) => {
          const isCurrent = item.thread_id === currentThreadId;
          return (
            <li key={item.thread_id}>
              <div
                role="button"
                tabIndex={0}
                // Explicit accessible name keeps the row clickable +
                // keyboard-navigable without colliding with the inner
                // Stop button's "Stop" name in role-based queries.
                aria-label={`Open review ${item.thread_id}`}
                data-testid={`active-row-${item.thread_id}`}
                data-current={isCurrent ? "true" : "false"}
                onClick={() => onSelect(item.thread_id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelect(item.thread_id);
                  }
                }}
                className={cn(
                  "cursor-pointer rounded-md border-l-[3px] p-2 text-xs transition-colors",
                  isCurrent
                    ? "bg-accent border-l-primary"
                    : "bg-card border-l-primary/60 hover:bg-accent/60",
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-[10px] uppercase text-primary border border-primary rounded px-1.5 py-0.5">
                    {item.mode}
                  </span>
                  <span className="font-variant-numeric-tabular text-muted-foreground text-[11px]">
                    {fmtElapsed(item.elapsed_s)}
                  </span>
                  <Button
                    type="button"
                    variant="destructive"
                    size="sm"
                    // Stop must not trigger row selection — the user
                    // clearly meant to cancel, not open.
                    onClick={(e) => {
                      e.stopPropagation();
                      onCancel(item.thread_id);
                    }}
                    className="ml-auto h-6 px-2 text-[11px]"
                  >
                    Stop
                  </Button>
                </div>
                <div className="text-muted-foreground text-[11px] whitespace-nowrap overflow-hidden text-ellipsis mt-1">
                  {item.target}
                </div>
                <TokenSummary
                  input={item.tokens.input}
                  output={item.tokens.output}
                  calls={item.tokens.calls}
                  className="mt-1"
                />
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
