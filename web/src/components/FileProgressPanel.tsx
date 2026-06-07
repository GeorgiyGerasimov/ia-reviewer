// Per-role per-file scan progress for repo-mode reviews. The
// backend emits `file_progress` envelopes from
// `LLMPerFileReviewer._run_repo` — one `started_batch` at the
// top of each reviewer, one `file_done` per file scanned, one
// `finished` at the end. `useReviewStream` keeps the LATEST
// envelope per role (last-write-wins) — this panel renders
// from that map.
//
// What the panel shows is intentionally minimal — operators
// just need to see (a) the scan is running, (b) which role is
// busy, (c) which file is current, (d) the running count. Full
// per-file list expansion can come later when someone asks for it.
//
// Hidden entirely when no role has emitted yet (`roleEnvelopes`
// is empty) — no empty title row in the sidebar.

import type { ReviewerRole } from "../api/types";
import type {
  FileProgressDoneEnvelope,
  FileProgressEnvelope,
  FileProgressStartedEnvelope,
} from "../api/ws-events";

export interface FileProgressPanelProps {
  /** role → latest envelope. Comes from `useReviewStream` state. */
  roleEnvelopes: Record<string, FileProgressEnvelope>;
}

// Display order: stable across runs so operators always see
// dependency first. Roles outside this set are still rendered
// (future-proof) but appended at the end.
const ROLE_ORDER: readonly ReviewerRole[] = [
  "dependency",
  "injection",
  "owasp",
  "configuration",
];

const ROLE_LABELS: Record<ReviewerRole, string> = {
  dependency: "Dependency",
  injection: "Injection",
  owasp: "OWASP Top 10",
  configuration: "Configuration",
};

interface RoleSummary {
  role: string;
  label: string;
  done: number;
  total: number;
  pct: number;
  current: string | null;
  finished: boolean;
  /** Skipped categories from started_batch — `{ test: 12, docs: 4 }`. */
  skipped: Record<string, number> | null;
}

function summarise(env: FileProgressEnvelope): RoleSummary {
  const role = env.role;
  const label = ROLE_LABELS[role] ?? role;
  if (env.state === "started_batch") {
    const e = env as FileProgressStartedEnvelope;
    return {
      role,
      label,
      done: 0,
      total: e.total,
      pct: 0,
      current: null,
      finished: false,
      skipped: e.skipped ?? null,
    };
  }
  if (env.state === "file_done") {
    const e = env as FileProgressDoneEnvelope;
    return {
      role,
      label,
      done: e.index,
      total: e.total,
      pct: e.total > 0 ? Math.min(100, Math.round((e.index / e.total) * 100)) : 0,
      current: e.path,
      finished: false,
      skipped: null,
    };
  }
  // finished
  const done = env.processed + env.failed + env.skipped_empty;
  return {
    role,
    label,
    done,
    total: env.total,
    pct: 100,
    current: null,
    finished: true,
    skipped: null,
  };
}

function orderedRoles(roles: string[]): string[] {
  const known = ROLE_ORDER.filter((r) => roles.includes(r));
  const unknown = roles.filter((r) => !ROLE_ORDER.includes(r as ReviewerRole));
  return [...known, ...unknown];
}

export function FileProgressPanel({ roleEnvelopes }: FileProgressPanelProps) {
  const roles = orderedRoles(Object.keys(roleEnvelopes));
  if (roles.length === 0) return null;

  return (
    <section className="rounded-xl border border-border bg-background p-4">
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        Per-file scan
      </h3>
      <div className="flex flex-col gap-3">
        {roles.map((role) => {
          const s = summarise(roleEnvelopes[role]);
          return (
            <div
              key={role}
              data-testid={`fp-role-${role}`}
              data-finished={s.finished ? "true" : "false"}
              className="text-xs"
            >
              <div className="flex items-baseline justify-between">
                <h4 className="text-xs font-semibold m-0">{s.label}</h4>
                <span className="font-mono tabular-nums text-muted-foreground">
                  {s.finished
                    ? `done (${s.done}/${s.total})`
                    : `${s.done}/${s.total}`}
                </span>
              </div>
              <div className="mt-1 h-2 rounded-full border border-border bg-muted/40 overflow-hidden">
                <div
                  className="h-full bg-primary transition-[width] duration-200"
                  style={{ width: `${s.pct}%` }}
                />
              </div>
              {s.skipped && Object.entries(s.skipped).some(([, n]) => n > 0) && (
                <div className="mt-1 text-[0.7rem] text-muted-foreground">
                  skipped{" "}
                  {Object.entries(s.skipped)
                    .filter(([, n]) => n > 0)
                    .map(([cat, n], i, arr) => (
                      <span key={cat}>
                        <strong className="text-foreground">{n}</strong> {cat}
                        {i < arr.length - 1 ? " · " : ""}
                      </span>
                    ))}{" "}
                  (out of scope)
                </div>
              )}
              {s.current && (
                <div
                  className="mt-1 text-[0.7rem] font-mono text-muted-foreground truncate"
                  title={s.current}
                >
                  ▸ {s.current}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
