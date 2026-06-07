// Per-role per-file progress (repo-mode). Renders one block per
// active role with a progress bar + current file + expandable
// per-file list. Pure-display — `roleEnvelopes` comes from
// `useReviewStream(threadId).fileProgress`, last-envelope-per-role
// wins.

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { FileProgressPanel } from "./FileProgressPanel";
import type { FileProgressEnvelope } from "../api/ws-events";

function started(role: string, paths: string[], extras: Partial<FileProgressEnvelope> = {}): FileProgressEnvelope {
  return {
    type: "file_progress",
    role: role as FileProgressEnvelope["role"],
    state: "started_batch",
    total: paths.length,
    paths,
    ...extras,
  } as FileProgressEnvelope;
}

function fileDone(role: string, path: string, index: number, total: number, failed = false): FileProgressEnvelope {
  return {
    type: "file_progress",
    role: role as FileProgressEnvelope["role"],
    state: "file_done",
    path,
    index,
    total,
    findings_count: 0,
    failed,
  };
}

function finished(role: string, processed: number, failed: number, total: number, skipped_empty = 0): FileProgressEnvelope {
  return {
    type: "file_progress",
    role: role as FileProgressEnvelope["role"],
    state: "finished",
    processed,
    failed,
    skipped_empty,
    findings_total: 0,
    total,
  };
}

describe("<FileProgressPanel />", () => {
  it("renders nothing when no role has emitted yet", () => {
    const { container } = render(<FileProgressPanel roleEnvelopes={{}} />);
    // Pure: an empty envelope map collapses the entire panel —
    // no empty title row hanging in the sidebar.
    expect(container.firstChild).toBeNull();
  });

  it("renders one block per active role with the human label", () => {
    render(
      <FileProgressPanel
        roleEnvelopes={{
          injection: started("injection", ["a.py", "b.py"]),
          owasp: started("owasp", ["c.yml"]),
        }}
      />,
    );
    expect(screen.getByText("Injection")).toBeInTheDocument();
    expect(screen.getByText("OWASP Top 10")).toBeInTheDocument();
  });

  it("shows the running count and the current file when scanning", () => {
    // After one file_done envelope, current = that file, progress = 1/N.
    render(
      <FileProgressPanel
        roleEnvelopes={{
          injection: fileDone("injection", "src/app.py", 1, 3),
        }}
      />,
    );
    // The most recent envelope is `file_done` so the panel shows
    // "1/3" and the path under "currently scanning".
    expect(screen.getByText(/1\/3/)).toBeInTheDocument();
    expect(screen.getByText(/src\/app\.py/)).toBeInTheDocument();
  });

  it("flips to terminal state after a `finished` envelope", () => {
    render(
      <FileProgressPanel
        roleEnvelopes={{
          injection: finished("injection", 3, 0, 3),
        }}
      />,
    );
    // Finished state: counts read as "done" not "scanning"; no
    // "currently scanning" line.
    expect(screen.getByText(/done/i)).toBeInTheDocument();
    expect(screen.queryByText(/▸/)).not.toBeInTheDocument();
  });

  it("renders the skipped categories summary from started_batch", () => {
    render(
      <FileProgressPanel
        roleEnvelopes={{
          injection: started("injection", ["a.py"], { skipped: { test: 12, docs: 4 } } as Partial<FileProgressEnvelope>),
        }}
      />,
    );
    // "skipped 12 test · 4 docs (out of scope)" — operators
    // immediately see WHY the scan list is shorter than the tree.
    expect(screen.getByText(/12/)).toBeInTheDocument();
    expect(screen.getByText(/4/)).toBeInTheDocument();
    expect(screen.getByText(/out of scope/)).toBeInTheDocument();
  });

  it("orders roles deterministically (dependency, injection, owasp, configuration)", () => {
    render(
      <FileProgressPanel
        roleEnvelopes={{
          owasp: started("owasp", ["a"]),
          dependency: started("dependency", ["b"]),
          configuration: started("configuration", ["c"]),
          injection: started("injection", ["d"]),
        }}
      />,
    );
    const headings = screen.getAllByRole("heading", { level: 4 });
    const labels = headings.map((h) => h.textContent);
    expect(labels).toEqual(["Dependency", "Injection", "OWASP Top 10", "Configuration"]);
  });
});
