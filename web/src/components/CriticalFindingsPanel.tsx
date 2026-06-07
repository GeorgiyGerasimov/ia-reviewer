// Critical-findings + exploit-PoC creation surface. Pure-display:
// parent owns the data + the create handler. Three pieces of state
// per row:
//   * `creating: Set<finding_id>` — rows currently waiting on a
//     POST. The action button is disabled + shows a spinner.
//   * `capReached: boolean` — MAX_EXPLOIT_PROPOSALS reached. All
//     fresh-status rows show "Cap reached" instead of "Create
//     exploit"; existing approved / skipped rows still render
//     their own action.
//   * `exploit_status` on each row — the source of truth for
//     whether the row already has an approved PoC, was skipped,
//     or is fresh.
//
// All defensive-use messaging lives at the top of the panel (one
// disclaimer line) + inside the LLM prompts on the backend (see
// EXPLOIT_DISCLAIMER in src/agents/exploit_proposal.py). The
// canonical text lives there; we render a short summary here.

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { CriticalFinding } from "../api/types";

export interface CriticalFindingsPanelProps {
  findings: CriticalFinding[];
  /** finding_ids whose POST /exploits/{fid} is currently in
   *  flight. The action button shows a spinner + is disabled
   *  while a finding's id is in this set. */
  creating: Set<string>;
  /** True when the backend's MAX_EXPLOIT_PROPOSALS quota has been
   *  reached for this review. Fresh rows can't create new
   *  exploits; existing rows still show their previously-stored
   *  action. */
  capReached: boolean;
  onCreate: (findingId: string) => void;
}

export function CriticalFindingsPanel({
  findings,
  creating,
  capReached,
  onCreate,
}: CriticalFindingsPanelProps) {
  if (findings.length === 0) return null;

  return (
    <section className="rounded-xl border border-border bg-background p-4">
      <h2 className="text-base font-semibold m-0 mb-2 flex items-baseline gap-2">
        Critical findings
        <span className="text-xs text-muted-foreground font-normal">
          {findings.length}
        </span>
      </h2>

      {/* One-line defensive-use disclaimer. Canonical text lives
          server-side in EXPLOIT_DISCLAIMER. */}
      <div className="rounded-md border border-notice-border bg-notice-bg text-notice-text text-xs px-3 py-2 mb-3 leading-snug">
        ⚠️ Defensive use only — generate this PoC ONLY for testing your own
        code in an isolated environment. NOT for use against third-party
        systems or for destructive purposes.
      </div>

      {/* Bounded scroll container — real reports easily hit 40+
          critical findings on a midsize repo. Letting the list
          expand to natural height pushed the report panel + token
          usage off-screen. The ceiling (~288px) fits ~2-3 typical
          headline rows; the rest is one scroll away inside the
          panel. `pr-1` reserves room for the scrollbar so it
          doesn't crowd the action buttons. */}
      <ul
        data-testid="cf-list"
        className="list-none p-0 m-0 flex flex-col gap-2 max-h-[288px] overflow-y-auto pr-1"
      >
        {findings.map((f) => (
          <li key={f.finding_id}>
            <CriticalFindingRow
              finding={f}
              busy={creating.has(f.finding_id)}
              capReached={capReached}
              onCreate={onCreate}
            />
          </li>
        ))}
      </ul>
    </section>
  );
}

interface RowProps {
  finding: CriticalFinding;
  busy: boolean;
  capReached: boolean;
  onCreate: (findingId: string) => void;
}

function CriticalFindingRow({ finding: f, busy, capReached, onCreate }: RowProps) {
  const fileLine = f.file ? (f.line !== null ? `${f.file}:${f.line}` : f.file) : "—";
  const hasApprovedExploit = f.exploit_status === "approved";
  const isSkipped = f.exploit_status === "skipped_low_confidence";
  const isFresh = f.exploit_status === null;

  return (
    <div className="rounded-md border border-border bg-card overflow-hidden">
      <div className="flex items-center gap-3 px-3 py-2">
        <div className="flex-1 min-w-0 text-sm leading-snug">
          <div className="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
            <span className="uppercase font-semibold text-destructive">
              {f.role}
            </span>
            <span>·</span>
            <span className="uppercase tracking-wide text-destructive">
              {f.severity}
            </span>
            <span>·</span>
            <span className="font-mono">{fileLine}</span>
          </div>
          <div className="text-foreground mt-1">{f.issue}</div>
        </div>

        <div className="shrink-0 flex items-center gap-2">
          {busy && (
            <span
              data-testid={`cf-spinner-${f.finding_id}`}
              aria-hidden
              className="inline-block w-3.5 h-3.5 border-2 border-border border-t-primary rounded-full animate-spin"
            />
          )}
          {isFresh && (
            <Button
              type="button"
              size="sm"
              disabled={busy || capReached}
              onClick={() => onCreate(f.finding_id)}
            >
              {busy ? "Generating…" : capReached ? "Cap reached" : "Create exploit"}
            </Button>
          )}
          {isSkipped && (
            <span
              className={cn(
                "px-2 py-1 rounded text-xs uppercase tracking-wide",
                "bg-muted text-muted-foreground",
              )}
              title={`Confidence ${f.confidence ?? "?"}/10 — too low to draft a meaningful PoC`}
            >
              Skipped (low confidence)
            </span>
          )}
        </div>
      </div>

      {hasApprovedExploit && (
        <details className="border-t border-dashed border-border px-3 py-2 text-sm" open>
          <summary className="cursor-pointer text-xs text-primary select-none mb-1">
            View existing PoC
          </summary>
          {f.proposal_text && (
            <p className="text-foreground mb-2 whitespace-pre-wrap">
              {f.proposal_text}
            </p>
          )}
          {f.artifact && (
            <>
              <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
                Artifact
              </div>
              <pre className="bg-muted text-foreground rounded p-2 text-xs overflow-x-auto whitespace-pre-wrap m-0">
                {f.artifact}
              </pre>
            </>
          )}
        </details>
      )}
    </div>
  );
}
