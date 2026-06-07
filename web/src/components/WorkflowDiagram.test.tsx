// Left-rail status indicator. Reads node statuses + validator
// verdict from props (NOT from the WS hook directly — keeps the
// component testable without standing up a fake socket). The
// parent panel wires this to `useReviewStream(threadId)`.
//
// Tests pin the semantics:
//   * Pending nodes render in muted grey.
//   * Active nodes get the pulsing-blue indicator.
//   * Fired nodes turn green.
//   * `empty` status (scope-skipped specialist) stays grey, NOT green.
//   * Rejected nodes (notify_rejection fired) turn red.
//   * Validator branching: accepted=true → reject branch dimmed;
//     accepted=false → reviewer chain dimmed.

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { WorkflowDiagram } from "./WorkflowDiagram";

describe("<WorkflowDiagram />", () => {
  it("renders every workflow step by default", () => {
    render(<WorkflowDiagram nodeStatuses={{}} validationAccepted={null} />);
    // The canonical step list — operators expect to see all of them
    // even when nothing has fired yet.
    for (const label of [
      "Download",
      "Validate tree",
      "Dependency",
      "Injection",
      "OWASP Top 10",
      "Configuration",
      "Review decision",
      "Aggregate",
      "Publish",
      "Rejected",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("paints a fired node in the done color", () => {
    render(
      <WorkflowDiagram
        nodeStatuses={{ clone_repo: "fired" }}
        validationAccepted={null}
      />,
    );
    const step = screen.getByTestId("wf-step-clone_repo");
    expect(step).toHaveAttribute("data-status", "fired");
  });

  it("paints an empty (scope-skipped) node in the muted color, not done", () => {
    // Important: a reviewer that returned `{}` because it was
    // scope-skipped must NOT look like it succeeded.
    render(
      <WorkflowDiagram
        nodeStatuses={{ configuration_review: "empty" }}
        validationAccepted={true}
      />,
    );
    const step = screen.getByTestId("wf-step-configuration_review");
    expect(step).toHaveAttribute("data-status", "empty");
  });

  it("paints an active node with the pulsing indicator", () => {
    render(
      <WorkflowDiagram
        nodeStatuses={{ clone_repo: "active" }}
        validationAccepted={null}
      />,
    );
    const step = screen.getByTestId("wf-step-clone_repo");
    expect(step).toHaveAttribute("data-status", "active");
  });

  it("paints notify_rejection in the rejected color when it fires", () => {
    render(
      <WorkflowDiagram
        nodeStatuses={{ notify_rejection: "rejected" }}
        validationAccepted={false}
      />,
    );
    const step = screen.getByTestId("wf-step-notify_rejection");
    expect(step).toHaveAttribute("data-status", "rejected");
  });

  it("dims the reject branch when validator accepted (branch won't fire)", () => {
    // Accept verdict: notify_rejection will never fire — show it
    // as out-of-branch so operators don't wait for it.
    render(<WorkflowDiagram nodeStatuses={{}} validationAccepted={true} />);
    const step = screen.getByTestId("wf-step-notify_rejection");
    expect(step).toHaveAttribute("data-branch", "skipped");
  });

  it("dims the reviewer chain when validator rejected", () => {
    render(<WorkflowDiagram nodeStatuses={{}} validationAccepted={false} />);
    // All four specialists + the join nodes downstream of validate
    // sit on the accept branch — they're skipped on reject.
    for (const node of [
      "dependency_review",
      "injection_review",
      "owasp_review",
      "configuration_review",
      "review_decision",
      "aggregate_results",
      "publish_report",
    ]) {
      expect(screen.getByTestId(`wf-step-${node}`)).toHaveAttribute(
        "data-branch",
        "skipped",
      );
    }
  });

  it("leaves both branches in the default state when validator hasn't run yet", () => {
    render(<WorkflowDiagram nodeStatuses={{}} validationAccepted={null} />);
    // Pre-validation we don't know which branch will fire, so
    // nothing is dimmed.
    expect(screen.getByTestId("wf-step-notify_rejection")).toHaveAttribute(
      "data-branch",
      "active",
    );
    expect(screen.getByTestId("wf-step-injection_review")).toHaveAttribute(
      "data-branch",
      "active",
    );
  });

  it("renders the Dependency badge marking it as deterministic (OSV.dev)", () => {
    // Tooltip carries the longer explanation; the badge label is
    // just `OSV.dev` to keep the sidebar compact.
    render(<WorkflowDiagram nodeStatuses={{}} validationAccepted={null} />);
    expect(screen.getByText("OSV.dev")).toBeInTheDocument();
  });
});
