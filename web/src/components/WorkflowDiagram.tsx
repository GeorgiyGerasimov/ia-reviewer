// Left-rail status indicator for a single review run. Pure-display
// React: reads `nodeStatuses` + `validationAccepted` from props,
// renders an ordered list of steps with a colored dot per node.
//
// The parent panel wires this to `useReviewStream(threadId)`:
//
//     const { nodeStatuses, validationAccepted } = useReviewStream(tid);
//     <WorkflowDiagram {...{ nodeStatuses, validationAccepted }} />
//
// Keeping the WS hook outside the component means tests can drive
// it with plain prop arrays — no fake socket, no `act()`. Mirrors
// the same prop-in / DOM-out pattern as TokenSummary.

import { cn } from "@/lib/utils";
import type { NodeStatus } from "../api/ws-events";

export interface WorkflowDiagramProps {
  /** Map: graph-node name → latest status seen. Missing keys render
   *  as "pending" (muted grey dot, no label color). */
  nodeStatuses: Record<string, NodeStatus>;
  /** Validator verdict, used to dim the alternate branch:
   *   `true`  → `notify_rejection` will never fire, dim it.
   *   `false` → reviewer chain + join nodes never fire, dim them.
   *   `null`  → validator hasn't run yet, leave both branches alive. */
  validationAccepted: boolean | null;
}

// Steps + their display labels. Order matches the runtime flow.
// `branch: "accept"` means this step only fires when validator
// accepted; `branch: "reject"` means it fires only on reject.
// `branch: undefined` means it fires regardless (or before
// validation — clone_repo, validate_request).
type Branch = "accept" | "reject";

interface Step {
  node: string;
  label: string;
  branch?: Branch;
  /** Two-level: top-level workflow vs nested specialist row. */
  level?: "child";
  /** Optional badge (e.g. "OSV.dev" on Dependency). */
  badge?: { label: string; title: string };
}

const STEPS: Step[] = [
  { node: "clone_repo", label: "Download" },
  { node: "validate_request", label: "Validate tree" },
  {
    node: "dependency_review",
    label: "Dependency",
    branch: "accept",
    level: "child",
    badge: {
      label: "OSV.dev",
      title: "Deterministic OSV.dev scan + 1 LLM summary call",
    },
  },
  {
    node: "injection_review",
    label: "Injection",
    branch: "accept",
    level: "child",
  },
  {
    node: "owasp_review",
    label: "OWASP Top 10",
    branch: "accept",
    level: "child",
  },
  {
    node: "configuration_review",
    label: "Configuration",
    branch: "accept",
    level: "child",
  },
  { node: "review_decision", label: "Review decision", branch: "accept" },
  { node: "aggregate_results", label: "Aggregate", branch: "accept" },
  { node: "publish_report", label: "Publish", branch: "accept" },
  { node: "notify_rejection", label: "Rejected", branch: "reject" },
];

function branchState(step: Step, accepted: boolean | null): "active" | "skipped" {
  if (accepted === null || !step.branch) return "active";
  if (step.branch === "accept" && accepted) return "active";
  if (step.branch === "reject" && !accepted) return "active";
  return "skipped";
}

const DOT_BY_STATUS: Record<NodeStatus | "pending", string> = {
  pending: "bg-transparent border-muted-foreground",
  active: "bg-transparent border-primary animate-pulse",
  fired: "bg-done border-done",
  empty: "bg-skipped border-skipped",
  rejected: "bg-destructive border-destructive",
};

const TEXT_BY_STATUS: Record<NodeStatus | "pending", string> = {
  pending: "text-foreground",
  active: "text-primary font-semibold",
  fired: "text-done",
  empty: "text-skipped",
  rejected: "text-destructive",
};

export function WorkflowDiagram({ nodeStatuses, validationAccepted }: WorkflowDiagramProps) {
  return (
    <div className="rounded-xl border border-border bg-background p-4">
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        Workflow
      </h3>
      <ol className="flex flex-col gap-1.5 list-none p-0 m-0">
        {STEPS.map((step) => {
          const status = nodeStatuses[step.node] ?? "pending";
          const branch = branchState(step, validationAccepted);
          const isChild = step.level === "child";
          return (
            <li
              key={step.node}
              data-testid={`wf-step-${step.node}`}
              data-status={status}
              data-branch={branch}
              className={cn(
                "flex items-center gap-2 text-[0.83rem]",
                isChild && "ml-4 pl-2.5 border-l-2 border-border",
                branch === "skipped" && "opacity-40",
              )}
            >
              <span
                aria-hidden
                className={cn(
                  "inline-block w-3 h-3 rounded-full border-2 shrink-0 transition-colors",
                  DOT_BY_STATUS[status],
                )}
              />
              <span className={cn(TEXT_BY_STATUS[status])}>{step.label}</span>
              {step.badge && (
                <span
                  className="ml-1 px-1.5 py-0.5 text-[0.65rem] font-semibold uppercase tracking-wide rounded border border-primary text-primary bg-primary/10 cursor-help"
                  title={step.badge.title}
                >
                  {step.badge.label}
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
