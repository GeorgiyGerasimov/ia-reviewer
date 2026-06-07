// Smallest possible "real" component. Renders the
// `X.Xk in · Y.Yk out · N calls` line that appears under the elapsed
// timer in the Active reviews sidebar. Pure-display: parent passes
// the payload, this component owns no state and makes no requests.
//
// Tailwind utility classes drive the styling. Token names come from
// the shadcn vocabulary defined in src/styles/globals.css — using
// `text-muted-foreground` (subdued text) + `text-foreground` (primary
// ink) means new shadcn components dropped in next to this one
// inherit the same palette automatically, without bespoke overrides.

import { fmtTokens } from "../lib/format";

export interface TokenSummaryProps {
  /** Total input tokens consumed across all graph nodes so far. */
  input: number;
  /** Total output tokens emitted across all graph nodes so far. */
  output: number;
  /** Number of LLM calls observed. */
  calls: number;
  /** Optional extra class so the parent can style the wrapper. */
  className?: string;
}

// Shared with both render branches so the wrapper looks identical
// regardless of zero / non-zero state.
const WRAPPER_CLASSES =
  "font-mono text-[11px] text-muted-foreground tabular-nums whitespace-nowrap";

export function TokenSummary({ input, output, calls, className }: TokenSummaryProps) {
  const wrapperClass = className ? `${WRAPPER_CLASSES} ${className}` : WRAPPER_CLASSES;

  if (input === 0 && output === 0 && calls === 0) {
    return (
      <div className={wrapperClass} aria-label="No LLM activity yet">
        <span>0 in · 0 out</span>
      </div>
    );
  }
  return (
    <div className={wrapperClass} aria-label="Token usage so far">
      <span>
        <strong className="font-semibold text-foreground">{fmtTokens(input)}</strong> in
        <span className="text-muted-foreground"> · </span>
        <strong className="font-semibold text-foreground">{fmtTokens(output)}</strong> out
        <span className="text-muted-foreground"> · </span>
        {calls} {calls === 1 ? "call" : "calls"}
      </span>
    </div>
  );
}
