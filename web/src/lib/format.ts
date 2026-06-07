// Pure formatters. Zero DOM. Zero side effects. Imported by any
// component that needs to render a token count, elapsed time, or
// finding severity. Behaviour pinned by `format.test.ts`.

/** Compact token formatter: `0` / `42` / `1.2k` / `12.3M`.
 *
 * We round explicitly via `Math.round(n / 100) / 10` rather than
 * `(n / 1000).toFixed(1)`. The latter relies on `toFixed` and runs
 * into IEEE-754 representation issues at the half-way mark (the
 * canonical example: `(9.95).toFixed(1) === "9.9"` because 9.95 has
 * no exact binary representation). Rounding ourselves keeps the
 * output monotone — the displayed counter never reads lower than
 * the actual value.
 */
export function fmtTokens(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "0";
  if (n < 1_000) return String(Math.round(n));
  if (n < 1_000_000) {
    const k = Math.round(n / 100) / 10;
    return `${k.toFixed(1)}k`;
  }
  const m = Math.round(n / 100_000) / 10;
  return `${m.toFixed(1)}M`;
}

/** Elapsed-seconds → `Xs` / `Xm Ys` / `Xh Ym`. Floors fractional seconds. */
export function fmtElapsed(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "0s";
  const total = Math.floor(seconds);
  if (total < 60) return `${total}s`;
  if (total < 3_600) {
    const m = Math.floor(total / 60);
    const s = total % 60;
    return `${m}m ${s}s`;
  }
  const h = Math.floor(total / 3_600);
  const m = Math.floor((total % 3_600) / 60);
  return `${h}h ${m}m`;
}

const SEVERITY_RANKS: Record<string, number> = {
  critical: 4,
  major: 3,
  minor: 2,
  info: 1,
};

const KNOWN_SEVERITIES = new Set(["critical", "major", "minor", "info"]);

/** Sort key: higher = more severe. Unknown → 0 (sorts last). */
export function severityRank(severity: string): number {
  return SEVERITY_RANKS[severity.toLowerCase()] ?? 0;
}

/**
 * Normalize a free-form severity string to one of the four known
 * values. Falls back to "info" — never returns a value outside the
 * known set, so callers can safely use it as a CSS class.
 */
export function normalizeSeverity(severity: string | null | undefined): string {
  if (typeof severity !== "string") return "info";
  const lower = severity.toLowerCase();
  return KNOWN_SEVERITIES.has(lower) ? lower : "info";
}
