// The exploit-PoC creation surface. Pure-display: parent passes
// findings + a `creating` set (in-flight POSTs) + `capReached`
// flag + `onCreate(fid)` callback. The panel renders:
//   * A one-line defensive-use disclaimer at the top.
//   * One row per critical finding with a status-dependent action:
//      - exploit_status=null + !capReached → "Create exploit"
//      - exploit_status=null + capReached  → disabled "Cap reached"
//      - exploit_status="approved"         → expandable artifact
//      - exploit_status="skipped_low_confidence" → grey badge
//   * A spinner next to the row when creating[fid] is set.

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CriticalFindingsPanel } from "./CriticalFindingsPanel";
import type { CriticalFinding } from "../api/types";

function finding(over: Partial<CriticalFinding> = {}): CriticalFinding {
  return {
    finding_id: "fid-1",
    role: "injection",
    file: "src/x.py",
    line: 42,
    issue: "SQLi via f-string in user query",
    severity: "critical",
    exploit_status: null,
    proposal_text: "",
    artifact: "",
    ...over,
  };
}

describe("<CriticalFindingsPanel />", () => {
  it("renders nothing when there are no findings", () => {
    const { container } = render(
      <CriticalFindingsPanel
        findings={[]}
        creating={new Set()}
        capReached={false}
        onCreate={vi.fn()}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders the defensive-use disclaimer banner above the rows", () => {
    render(
      <CriticalFindingsPanel
        findings={[finding()]}
        creating={new Set()}
        capReached={false}
        onCreate={vi.fn()}
      />,
    );
    expect(screen.getByText(/defensive use only/i)).toBeInTheDocument();
  });

  it("renders one row per finding with file:line + issue", () => {
    render(
      <CriticalFindingsPanel
        findings={[
          finding({ finding_id: "a", file: "x.py", line: 1, issue: "first" }),
          finding({ finding_id: "b", file: "y.py", line: 2, issue: "second" }),
        ]}
        creating={new Set()}
        capReached={false}
        onCreate={vi.fn()}
      />,
    );
    expect(screen.getByText(/x\.py:1/)).toBeInTheDocument();
    expect(screen.getByText(/y\.py:2/)).toBeInTheDocument();
    expect(screen.getByText(/first/)).toBeInTheDocument();
    expect(screen.getByText(/second/)).toBeInTheDocument();
  });

  it("renders 'Create exploit' for a fresh finding", () => {
    render(
      <CriticalFindingsPanel
        findings={[finding({ exploit_status: null })]}
        creating={new Set()}
        capReached={false}
        onCreate={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("button", { name: /create exploit/i }),
    ).not.toBeDisabled();
  });

  it("calls onCreate(finding_id) when 'Create exploit' is clicked", async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined);
    render(
      <CriticalFindingsPanel
        findings={[finding({ finding_id: "fid-x" })]}
        creating={new Set()}
        capReached={false}
        onCreate={onCreate}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /create exploit/i }));
    expect(onCreate).toHaveBeenCalledWith("fid-x");
  });

  it("disables the create button when the cap is reached", () => {
    render(
      <CriticalFindingsPanel
        findings={[finding()]}
        creating={new Set()}
        capReached={true}
        onCreate={vi.fn()}
      />,
    );
    const btn = screen.getByRole("button", { name: /cap reached/i });
    expect(btn).toBeDisabled();
  });

  it("shows a spinner + disables the button while creating", () => {
    render(
      <CriticalFindingsPanel
        findings={[finding({ finding_id: "fid-spin" })]}
        creating={new Set(["fid-spin"])}
        capReached={false}
        onCreate={vi.fn()}
      />,
    );
    // Button text changes to "Generating…" or similar
    const btn = screen.getByRole("button", { name: /generating/i });
    expect(btn).toBeDisabled();
    // Spinner marker for visual feedback
    expect(screen.getByTestId("cf-spinner-fid-spin")).toBeInTheDocument();
  });

  it("renders an expandable artifact block when exploit is approved", () => {
    render(
      <CriticalFindingsPanel
        findings={[
          finding({
            finding_id: "fid-a",
            exploit_status: "approved",
            proposal_text: "Inject ' OR 1=1 into login form",
            artifact: "curl -X POST localhost:8000/login -d username=admin'OR'1=1",
          }),
        ]}
        creating={new Set()}
        capReached={false}
        onCreate={vi.fn()}
      />,
    );
    // The proposal text is inside the <details> block.
    expect(
      screen.getByText(/Inject ' OR 1=1 into login form/),
    ).toBeInTheDocument();
    // The artifact PoC is inside the <pre>.
    expect(screen.getByText(/curl -X POST localhost:8000/)).toBeInTheDocument();
  });

  it("renders a 'Skipped (low confidence)' badge when status is skipped", () => {
    render(
      <CriticalFindingsPanel
        findings={[
          finding({ exploit_status: "skipped_low_confidence", confidence: 3 }),
        ]}
        creating={new Set()}
        capReached={false}
        onCreate={vi.fn()}
      />,
    );
    expect(screen.getByText(/skipped/i)).toBeInTheDocument();
    // No create button for skipped rows.
    expect(
      screen.queryByRole("button", { name: /create exploit/i }),
    ).not.toBeInTheDocument();
  });

  it("does NOT call onCreate when the button is disabled (cap)", async () => {
    const onCreate = vi.fn();
    render(
      <CriticalFindingsPanel
        findings={[finding()]}
        creating={new Set()}
        capReached={true}
        onCreate={onCreate}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /cap reached/i }));
    expect(onCreate).not.toHaveBeenCalled();
  });
});
