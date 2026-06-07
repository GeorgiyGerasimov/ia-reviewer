// Smallest possible "real" component. Renders the
// `X.Xk in · Y.Yk out · N calls` line that appears under the elapsed
// timer in the Active reviews sidebar. Pure-display: parent passes
// the payload, this component owns no state and makes no requests.
// Purpose during the foundation PR: prove the React + RTL + Vitest
// stack end-to-end. The pattern (typed props, colocated test,
// formatter composition) is the template every other component will
// follow.

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

export function TokenSummary({ input, output, calls, className }: TokenSummaryProps) {
  if (input === 0 && output === 0 && calls === 0) {
    return (
      <div className={className} aria-label="No LLM activity yet">
        <span className="tu-tokens">0 in · 0 out</span>
      </div>
    );
  }
  return (
    <div className={className} aria-label="Token usage so far">
      <span className="tu-tokens">
        <strong>{fmtTokens(input)}</strong> in
        <span className="tu-sep"> · </span>
        <strong>{fmtTokens(output)}</strong> out
        <span className="tu-sep"> · </span>
        {calls} {calls === 1 ? "call" : "calls"}
      </span>
    </div>
  );
}
