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
  /** Run has reached a terminal state — either `__done__` ran end-to-end
   *  or Stop fired `__cancelled__`. Anything still pending/active is
   *  coerced to `empty` so the sidebar shows the final shape (no
   *  pulsing dots after the WS terminates). */
  terminal?: boolean;
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
  // Synthetic parent — backend never emits an envelope for this
  // node. The UI derives its state from the four child specialists
  // (see `deriveDerivedStatuses`): all-children-terminal → "fired".
  // Operators read this row as "what stage of the graph are we in",
  // distinct from the individual reviewer rows below.
  { node: "security_reviewers", label: "Security reviewers", branch: "accept" },
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

const SECURITY_CHILDREN = [
  "dependency_review",
  "injection_review",
  "owasp_review",
  "configuration_review",
] as const;

// Derive synthetic node statuses the backend doesn't emit.
// Today: only `security_reviewers`. Becomes "fired" once every
// child specialist has reached a terminal state (fired / empty /
// rejected). Until then, the parent stays at whatever the
// upstream cascade set — typically "active" once validate_request
// accepted (set by useReviewStream), so the parent dot pulses
// while the children are in flight.
function deriveDerivedStatuses(
  raw: Record<string, NodeStatus>,
): Record<string, NodeStatus> {
  const terminal = (s: NodeStatus | undefined): boolean =>
    s === "fired" || s === "empty" || s === "rejected";
  const allDone = SECURITY_CHILDREN.every((n) => terminal(raw[n]));
  if (!allDone) return raw;
  // Don't override a backend-emitted value if the parent ever
  // gets one in the future — only fill in when absent.
  if (raw.security_reviewers && terminal(raw.security_reviewers)) return raw;
  return { ...raw, security_reviewers: "fired" };
}

/** When the run terminated, coerce any node that didn't reach a
 *  terminal state (still active or never set) to `empty`. Mirrors
 *  the legacy template's `finalizeWorkflow` — once the WS terminates
 *  there's no point in leaving dots pulsing. */
function coerceTerminal(
  statuses: Record<string, NodeStatus>,
): Record<string, NodeStatus> {
  const out: Record<string, NodeStatus> = { ...statuses };
  for (const step of STEPS) {
    const s = out[step.node];
    if (s === "fired" || s === "empty" || s === "rejected") continue;
    out[step.node] = "empty";
  }
  return out;
}

export function WorkflowDiagram({
  nodeStatuses,
  validationAccepted,
  terminal = false,
}: WorkflowDiagramProps) {
  const derived = deriveDerivedStatuses(nodeStatuses);
  const effectiveStatuses = terminal ? coerceTerminal(derived) : derived;
  return (
    <div className="rounded-xl border border-border bg-background p-4">
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        Workflow
      </h3>
      <ol className="flex flex-col gap-1.5 list-none p-0 m-0">
        {STEPS.map((step) => {
          const status = effectiveStatuses[step.node] ?? "pending";
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
