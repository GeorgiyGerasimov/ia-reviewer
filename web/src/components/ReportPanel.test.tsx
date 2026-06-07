// Renders a review's Markdown report. Pure display: the parent
// passes `markdown`, `threadId`, `loading`. We pipe the markdown
// through `lib/markdown.ts::renderMarkdown` and dangerouslySetInnerHTML
// — every user-derived content is already escaped by `inlineMd` /
// `escapeHtml` in the renderer.

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ReportPanel } from "./ReportPanel";

describe("<ReportPanel />", () => {
  it("returns nothing when threadId is null", () => {
    const { container } = render(
      <ReportPanel threadId={null} markdown="" loading={false} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("shows a 'waiting' placeholder when loading and no markdown yet", () => {
    render(
      <ReportPanel threadId="tid" markdown="" loading={true} />,
    );
    expect(screen.getByText(/waiting/i)).toBeInTheDocument();
  });

  it("shows an explicit empty-state when not loading and no markdown", () => {
    // Reached when the review is rejected (report_markdown == ""
    // by design). The panel says so explicitly so operators don't
    // think the request is still in flight.
    render(
      <ReportPanel threadId="tid" markdown="" loading={false} />,
    );
    expect(screen.getByText(/no report/i)).toBeInTheDocument();
  });

  it("renders the markdown into HTML using lib/markdown.ts", () => {
    render(
      <ReportPanel
        threadId="tid"
        markdown="## Security review\n\nfindings…"
        loading={false}
      />,
    );
    // The panel-level "Final report" heading + the rendered
    // markdown's "Security review" heading both exist. Get all
    // h2s and pick the second one — order in DOM matches order
    // in JSX, and the rendered-markdown body sits after the
    // header.
    const headings = screen.getAllByRole("heading", { level: 2 });
    expect(headings).toHaveLength(2);
    expect(headings[1]).toHaveTextContent("Security review");
  });

  it("escapes HTML inside the markdown source (no XSS)", () => {
    // A raw <script> in the report body must NOT execute. The
    // renderer's escapeHtml turns it into a literal &lt;script&gt;.
    render(
      <ReportPanel
        threadId="tid"
        markdown="paragraph with <script>alert(1)</script>"
        loading={false}
      />,
    );
    // No script tag in the DOM.
    expect(document.querySelector("script")).toBeNull();
    // The literal text shows up in the rendered paragraph.
    expect(screen.getByText(/alert\(1\)/)).toBeInTheDocument();
  });

  it("shows the thread_id label next to the heading", () => {
    render(
      <ReportPanel
        threadId="tid-abc-123"
        markdown="## report"
        loading={false}
      />,
    );
    expect(screen.getByText(/tid-abc-123/)).toBeInTheDocument();
  });

  it("links the raw markdown file via the report link", () => {
    render(
      <ReportPanel
        threadId="tid-x"
        markdown="## report"
        loading={false}
      />,
    );
    const link = screen.getByRole("link", { name: /raw .md/i });
    expect(link).toHaveAttribute("href", "/reports/tid-x.md");
    expect(link).toHaveAttribute("target", "_blank");
  });
});
