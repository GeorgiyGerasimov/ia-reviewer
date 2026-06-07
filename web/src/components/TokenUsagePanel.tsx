// Per-node LLM token accounting. Pure-display — usage map comes
// from the parent (typically `useReview(threadId).review?.token_usage`).
// Hides entirely when usage is empty / undefined so very short
// reviews don't get an empty box.

import { fmtTokens } from "../lib/format";
import type { TokenUsage } from "../api/types";

export interface TokenUsagePanelProps {
  usage: TokenUsage | undefined;
}

interface NodeRow {
  node: string;
  input: number;
  output: number;
  calls: number;
  total: number;
  models?: string[];
}

export function TokenUsagePanel({ usage }: TokenUsagePanelProps) {
  if (!usage || Object.keys(usage).length === 0) return null;

  const rows: NodeRow[] = Object.entries(usage).map(([node, b]) => ({
    node,
    input: b.input,
    output: b.output,
    calls: b.calls,
    total: b.input + b.output,
    models: b.models,
  }));
  rows.sort((a, b) => b.total - a.total);

  const totalIn = rows.reduce((s, r) => s + r.input, 0);
  const totalOut = rows.reduce((s, r) => s + r.output, 0);
  const totalCalls = rows.reduce((s, r) => s + r.calls, 0);

  return (
    <section className="rounded-xl border border-border bg-background p-4">
      <header className="flex items-baseline justify-between mb-2">
        <h2 className="text-base font-semibold m-0">Token usage</h2>
        <div className="flex items-baseline gap-2 text-sm tabular-nums font-mono text-foreground">
          {/* Grand total first — the operator's primary question is
              "how much did this scan cost?" The in/out split is
              relevant but secondary, so it sits in the
              parenthesised breakdown. */}
          <strong
            data-testid="tu-total"
            className="font-semibold text-base"
            title="Total tokens (input + output) across all graph nodes"
          >
            {fmtTokens(totalIn + totalOut)}
          </strong>
          <span
            data-testid="tu-breakdown"
            className="text-xs text-muted-foreground"
          >
            ({fmtTokens(totalIn)} in
            <span> · </span>
            {fmtTokens(totalOut)} out
            <span> · </span>
            {totalCalls} {totalCalls === 1 ? "call" : "calls"})
          </span>
        </div>
      </header>

      <details className="mt-2">
        <summary className="text-xs text-primary cursor-pointer select-none">
          Per-node breakdown
        </summary>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr className="text-muted-foreground border-b border-border">
                <th className="text-left py-1 px-2 font-medium">node</th>
                <th className="text-right py-1 px-2 font-medium">in</th>
                <th className="text-right py-1 px-2 font-medium">out</th>
                <th className="text-right py-1 px-2 font-medium">calls</th>
                <th className="text-left py-1 px-2 font-medium">model</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.node}
                  data-testid={`tu-node-${r.node}`}
                  data-node={r.node}
                  className="border-b border-border last:border-b-0"
                >
                  <td className="py-1 px-2 font-mono">{r.node}</td>
                  <td className="py-1 px-2 text-right tabular-nums">{fmtTokens(r.input)}</td>
                  <td className="py-1 px-2 text-right tabular-nums">{fmtTokens(r.output)}</td>
                  <td className="py-1 px-2 text-right tabular-nums">{r.calls}</td>
                  <td className="py-1 px-2 text-muted-foreground">
                    {r.models?.join(", ") ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  );
}
