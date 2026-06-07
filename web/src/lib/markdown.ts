// Small Markdown renderer for the security report. Handles only the
// subset the backend's ReportRenderer actually produces: H1-H4,
// paragraphs, dash/asterisk bullets, fenced code, inline code/bold/
// italic, https + /reports/ links, GFM-style pipe tables. Behaviour
// pinned by `markdown.test.ts`.

const TABLE_ROW_RE = /^\s*\|.*\|\s*$/;
const TABLE_SEP_RE = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/;

/** Escape the four characters that would break safe insertion as HTML. */
export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * Apply inline Markdown to a single line. Escapes HTML first, then
 * replaces backticks, bold, italic, and links. Order matters — the
 * escape MUST run before anything else or we'd let raw HTML through.
 */
export function inlineMd(s: string): string {
  let x = escapeHtml(s);
  x = x.replace(/`([^`]+?)`/g, "<code>$1</code>");
  x = x.replace(/\*\*([^*]+?)\*\*/g, "<strong>$1</strong>");
  x = x.replace(/_([^_]+?)_/g, "<em>$1</em>");
  x = x.replace(
    /\[([^\]]+?)\]\((https?:[^)]+|\/reports\/[\w-]+\.md)\)/g,
    '<a href="$2" target="_blank">$1</a>',
  );
  // Auto-linkify bare /reports/<id>.md paths, but NOT when they
  // already appear inside an `href="..."` produced by the previous
  // replacement — otherwise an explicit `[label](/reports/x.md)`
  // would nest a second <a> inside the first (the legacy
  // index.html had this exact bug; pinning it here so the port
  // stays clean).
  x = x.replace(
    /(?<!href=")(\/reports\/[\w-]+\.md)/g,
    '<a href="$1" target="_blank">$1</a>',
  );
  return x;
}

function splitCells(s: string): string[] {
  return s
    .replace(/^\s*\|/, "")
    .replace(/\|\s*$/, "")
    .split("|")
    .map((c) => c.trim());
}

/**
 * Render a Markdown document to HTML. Output is a flat HTML string
 * suitable for `dangerouslySetInnerHTML` — all user content has been
 * escaped via `inlineMd` / `escapeHtml`.
 */
export function renderMarkdown(src: string): string {
  const lines = src.split("\n");
  const out: string[] = [];
  let listOpen = false;
  let codeOpen = false;
  let para: string[] = [];

  const flushPara = () => {
    if (para.length) {
      out.push("<p>" + inlineMd(para.join(" ")) + "</p>");
      para = [];
    }
  };
  const closeList = () => {
    if (listOpen) {
      out.push("</ul>");
      listOpen = false;
    }
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].replace(/\r$/, "");

    if (line.startsWith("```")) {
      flushPara();
      closeList();
      if (codeOpen) {
        out.push("</code></pre>");
        codeOpen = false;
      } else {
        out.push("<pre><code>");
        codeOpen = true;
      }
      continue;
    }
    if (codeOpen) {
      out.push(escapeHtml(line));
      out.push("\n");
      continue;
    }

    // Pipe-table: header row + separator row required. Without the
    // separator, prose with a stray `|` falls through to the
    // paragraph path.
    if (
      TABLE_ROW_RE.test(line) &&
      i + 1 < lines.length &&
      TABLE_SEP_RE.test(lines[i + 1].replace(/\r$/, ""))
    ) {
      flushPara();
      closeList();
      const headers = splitCells(line);
      const bodyRows: string[][] = [];
      let j = i + 2;
      while (j < lines.length && TABLE_ROW_RE.test(lines[j].replace(/\r$/, ""))) {
        bodyRows.push(splitCells(lines[j].replace(/\r$/, "")));
        j++;
      }
      let html = '<table class="md-table"><thead><tr>';
      for (const h of headers) html += "<th>" + inlineMd(h) + "</th>";
      html += "</tr></thead><tbody>";
      for (const row of bodyRows) {
        html += "<tr>";
        for (const cell of row) html += "<td>" + inlineMd(cell) + "</td>";
        html += "</tr>";
      }
      html += "</tbody></table>";
      out.push(html);
      i = j - 1; // outer loop's i++ moves us past the last consumed row
      continue;
    }

    const h4 = line.match(/^####\s+(.*)$/);
    const h3 = line.match(/^###\s+(.*)$/);
    const h2 = line.match(/^##\s+(.*)$/);
    const h1 = line.match(/^#\s+(.*)$/);
    // Note: `#` promotes to <h2> — page H1 stays the document title.
    if (h4) {
      flushPara();
      closeList();
      out.push("<h4>" + inlineMd(h4[1]) + "</h4>");
      continue;
    }
    if (h3) {
      flushPara();
      closeList();
      out.push("<h3>" + inlineMd(h3[1]) + "</h3>");
      continue;
    }
    if (h2) {
      flushPara();
      closeList();
      out.push("<h2>" + inlineMd(h2[1]) + "</h2>");
      continue;
    }
    if (h1) {
      flushPara();
      closeList();
      out.push("<h2>" + inlineMd(h1[1]) + "</h2>");
      continue;
    }

    const bullet = line.match(/^\s*[-*]\s+(.*)$/);
    if (bullet) {
      flushPara();
      if (!listOpen) {
        out.push("<ul>");
        listOpen = true;
      }
      out.push("<li>" + inlineMd(bullet[1]) + "</li>");
      continue;
    }
    if (line.trim() === "") {
      flushPara();
      closeList();
      continue;
    }
    para.push(line);
  }
  flushPara();
  closeList();
  if (codeOpen) out.push("</code></pre>");
  return out.join("");
}
