// Renders the Markdown report for the currently-selected review.
// Pure-display: `threadId` + `markdown` + `loading` come from the
// parent (which wires `useReview(threadId)`).
//
// Markdown is converted via `lib/markdown.ts::renderMarkdown`.
// Every user-controlled value is escaped inside the renderer
// (escapeHtml before any inline regex), so the
// `dangerouslySetInnerHTML` here is XSS-safe — verified by a test
// that stages `<script>` in the source.

import { useMemo } from "react";
import { renderMarkdown } from "../lib/markdown";

export interface ReportPanelProps {
  threadId: string | null;
  markdown: string;
  loading: boolean;
}

export function ReportPanel({ threadId, markdown, loading }: ReportPanelProps) {
  // Memoize the rendered HTML so re-renders driven by sibling
  // state (workflow dots ticking, sidebar polling) don't re-run
  // the markdown parser on every paint.
  const html = useMemo(() => renderMarkdown(markdown), [markdown]);

  if (!threadId) return null;

  return (
    <section className="rounded-xl border border-border bg-background p-4">
      <header className="flex items-baseline justify-between mb-2">
        <div className="flex items-baseline gap-2">
          <h2 className="text-base font-semibold m-0">Final report</h2>
          <span className="text-xs text-muted-foreground font-mono">
            thread <code className="bg-transparent p-0">{threadId}</code>
          </span>
        </div>
        <a
          href={`/reports/${threadId}.md`}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-primary hover:underline"
        >
          view raw .md ↗
        </a>
      </header>

      {markdown ? (
        <div
          // Tailwind utilities target the rendered tags via the
          // `[&_h2]` etc. group syntax — keeps the markdown renderer
          // free of any class-injection logic.
          className={
            "rounded-md border border-border bg-card p-4 max-h-[520px] overflow-y-auto " +
            "text-sm leading-6 break-words " +
            "[&_h2]:text-lg [&_h2]:font-semibold [&_h2]:border-b [&_h2]:border-border [&_h2]:pb-1 [&_h2]:mb-2 [&_h2]:mt-1 " +
            "[&_h3]:text-base [&_h3]:font-semibold [&_h3]:mt-4 [&_h3]:mb-1 " +
            "[&_h4]:text-sm [&_h4]:text-muted-foreground [&_h4]:mt-3 [&_h4]:mb-1 " +
            "[&_p]:my-1 [&_ul]:pl-5 [&_ul]:my-1 [&_li]:mb-1 " +
            "[&_code]:bg-muted [&_code]:px-1 [&_code]:rounded [&_code]:text-[0.86em] [&_code]:font-mono " +
            "[&_pre]:bg-muted [&_pre]:p-2 [&_pre]:rounded [&_pre]:overflow-x-auto [&_pre]:text-xs " +
            "[&_strong]:font-semibold [&_em]:italic [&_em]:text-muted-foreground " +
            "[&_table]:block [&_table]:overflow-x-auto [&_table]:border-collapse [&_table]:my-2 [&_table]:text-xs " +
            "[&_th]:border [&_th]:border-border [&_th]:px-2 [&_th]:py-1 [&_th]:font-semibold [&_th]:bg-muted " +
            "[&_td]:border [&_td]:border-border [&_td]:px-2 [&_td]:py-1 [&_td]:whitespace-nowrap " +
            "[&_a]:text-primary [&_a]:underline"
          }
          // Renderer outputs already-escaped HTML — see
          // lib/markdown.ts. Safe to inject.
          dangerouslySetInnerHTML={{ __html: html }}
        />
      ) : loading ? (
        <div className="text-center text-muted-foreground py-4 text-sm">
          Waiting for the review to finish…
        </div>
      ) : (
        <div className="text-center text-muted-foreground py-4 text-sm italic">
          No report content (review was rejected or hasn't produced one yet).
        </div>
      )}
    </section>
  );
}
