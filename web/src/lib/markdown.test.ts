// Tiny custom Markdown renderer ported from legacy
// `templates/index.html`. We deliberately don't use a full Markdown
// library because:
//   1. The security reports use a small subset of features.
//   2. We control the producer (ReportRenderer in Python) so we don't
//      need to handle arbitrary user-authored Markdown.
//   3. Avoiding a dependency keeps the bundle small.
// These tests pin the contract so the React port and the legacy UI
// render the same reports byte-identically.
import { describe, expect, it } from "vitest";
import { escapeHtml, inlineMd, renderMarkdown } from "./markdown";

describe("escapeHtml", () => {
  it("escapes the four dangerous characters", () => {
    expect(escapeHtml("&")).toBe("&amp;");
    expect(escapeHtml("<")).toBe("&lt;");
    expect(escapeHtml(">")).toBe("&gt;");
    expect(escapeHtml('"')).toBe("&quot;");
  });

  it("escapes ampersand BEFORE other entities", () => {
    // `&amp;<` must NOT become `&amp;amp;lt;`. The encoder runs over
    // each source character exactly once.
    expect(escapeHtml("&<")).toBe("&amp;&lt;");
  });

  it("leaves plain text untouched", () => {
    expect(escapeHtml("hello world")).toBe("hello world");
    expect(escapeHtml("")).toBe("");
  });
});

describe("inlineMd", () => {
  it("escapes HTML in input before applying markdown", () => {
    // A finding text that contains literal `<script>` must not be
    // turned into a real script tag by the renderer.
    expect(inlineMd("<script>")).toBe("&lt;script&gt;");
  });

  it("wraps backticks as <code>", () => {
    expect(inlineMd("use `eval` here")).toBe("use <code>eval</code> here");
  });

  it("wraps double-asterisks as <strong>", () => {
    expect(inlineMd("**bold**")).toBe("<strong>bold</strong>");
  });

  it("wraps underscores as <em>", () => {
    expect(inlineMd("an _emphasis_ here")).toBe("an <em>emphasis</em> here");
  });

  it("renders explicit markdown links to https://", () => {
    expect(inlineMd("[OSV](https://osv.dev)")).toBe(
      '<a href="https://osv.dev" target="_blank">OSV</a>',
    );
  });

  it("renders explicit markdown links to /reports/<id>.md", () => {
    expect(inlineMd("[full](/reports/abc-123.md)")).toBe(
      '<a href="/reports/abc-123.md" target="_blank">full</a>',
    );
  });

  it("auto-links bare /reports/<id>.md paths", () => {
    // The chat used to broadcast plain "Full report: /reports/<id>.md"
    // lines. We continue to linkify them in case any rendered Markdown
    // surfaces a bare path.
    const out = inlineMd("see /reports/xyz-456.md for details");
    expect(out).toContain('<a href="/reports/xyz-456.md" target="_blank">');
  });
});

describe("renderMarkdown", () => {
  it("renders an empty string as empty output", () => {
    expect(renderMarkdown("")).toBe("");
  });

  it("renders a top-level # heading as <h2>", () => {
    // Legacy promoted `#` to <h2> so the page H1 stays the page title;
    // pin the same behaviour.
    expect(renderMarkdown("# Top")).toBe("<h2>Top</h2>");
  });

  it("renders ## as <h2>, ### as <h3>, #### as <h4>", () => {
    expect(renderMarkdown("## Sub")).toBe("<h2>Sub</h2>");
    expect(renderMarkdown("### Sub")).toBe("<h3>Sub</h3>");
    expect(renderMarkdown("#### Sub")).toBe("<h4>Sub</h4>");
  });

  it("wraps consecutive plain lines into one <p>", () => {
    const md = "first line\nsecond line";
    expect(renderMarkdown(md)).toBe("<p>first line second line</p>");
  });

  it("starts a new paragraph on a blank line", () => {
    const md = "first\n\nsecond";
    expect(renderMarkdown(md)).toBe("<p>first</p><p>second</p>");
  });

  it("renders dash + asterisk bullets as <ul><li>...</li></ul>", () => {
    const md = "- one\n- two\n* three";
    expect(renderMarkdown(md)).toBe("<ul><li>one</li><li>two</li><li>three</li></ul>");
  });

  it("closes the list when a paragraph follows", () => {
    const md = "- one\n\nafter";
    expect(renderMarkdown(md)).toBe("<ul><li>one</li></ul><p>after</p>");
  });

  it("renders triple-backtick fenced code as <pre><code>", () => {
    const md = "```\nlet x = 1;\n```";
    const out = renderMarkdown(md);
    expect(out).toContain("<pre><code>");
    expect(out).toContain("</code></pre>");
    expect(out).toContain("let x = 1;");
  });

  it("escapes html inside a code block", () => {
    // Critical: a code block must not let raw <script> escape into
    // the rendered DOM.
    const md = "```\n<script>alert(1)</script>\n```";
    const out = renderMarkdown(md);
    expect(out).toContain("&lt;script&gt;");
    expect(out).not.toContain("<script>");
  });

  it("renders a Markdown table with header + rows", () => {
    // Security report Summary tables look like:
    //   | Severity | Dep | Inj | Total |
    //   |----------|-----|-----|-------|
    //   | critical |   0 |   1 |     1 |
    const md = ["| Severity | Dep | Total |", "|---|---|---|", "| critical | 0 | 1 |"].join(
      "\n",
    );
    const out = renderMarkdown(md);
    expect(out).toContain('<table class="md-table">');
    expect(out).toContain("<th>Severity</th>");
    expect(out).toContain("<th>Dep</th>");
    expect(out).toContain("<th>Total</th>");
    expect(out).toContain("<td>critical</td>");
    expect(out).toContain("<td>0</td>");
    expect(out).toContain("<td>1</td>");
  });

  it("treats a `|`-line without a separator as plain text", () => {
    // Prose like "supports a | b syntax" must NOT silently get
    // interpreted as a one-cell table.
    const md = "supports a | b syntax";
    expect(renderMarkdown(md)).toBe("<p>supports a | b syntax</p>");
  });

  it("composes inline formatting inside list items", () => {
    const md = "- use **bold** text\n- a `code` token";
    const out = renderMarkdown(md);
    expect(out).toContain("<li>use <strong>bold</strong> text</li>");
    expect(out).toContain("<li>a <code>code</code> token</li>");
  });

  it("handles trailing CR (Windows line endings) inside code blocks", () => {
    const md = "```\nline\r\n```";
    const out = renderMarkdown(md);
    // The carriage return is stripped before the line lands in the
    // <pre> block — otherwise CRLF input would render with extra
    // blank lines.
    expect(out).toContain("line\n");
    expect(out).not.toContain("\r");
  });
});
