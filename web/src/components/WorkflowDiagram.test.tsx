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

  it("renders a 'Security reviewers' synthetic parent above the four specialists", () => {
    // The four reviewers fan out from a synthetic parent so the
    // diagram visually groups them. The parent fires (turns green)
    // when all four children have reached a terminal state — but
    // the LABEL must always be present so operators can see the
    // grouping even before anything runs.
    render(<WorkflowDiagram nodeStatuses={{}} validationAccepted={null} />);
    expect(screen.getByText("Security reviewers")).toBeInTheDocument();
    expect(screen.getByTestId("wf-step-security_reviewers")).toBeInTheDocument();
  });

  it("marks security_reviewers as fired once all four specialists have fired/empty", () => {
    // The parent doesn't get its own backend envelope — the UI
    // derives the state from the four children. Once they're all
    // terminal, the parent flips to fired.
    render(
      <WorkflowDiagram
        nodeStatuses={{
          dependency_review: "fired",
          injection_review: "fired",
          owasp_review: "empty",
          configuration_review: "fired",
        }}
        validationAccepted={true}
      />,
    );
    expect(screen.getByTestId("wf-step-security_reviewers")).toHaveAttribute(
      "data-status",
      "fired",
    );
  });

  it("paints clone_repo as empty (grey) from the start in PR mode", () => {
    // Background: clone_repo only fires for repo-mode reviews —
    // `_run_repo_review` emits the active/fired envelope, PR mode
    // goes straight to validate_request. We KEEP the row visible
    // (full topology stays legible) but pre-paint it `empty` so
    // it reads as "not applicable" instead of "failed step that
    // never ran". Same convention as notify_rejection on the
    // accept branch (visible but dimmed via data-branch=skipped).
    render(
      <WorkflowDiagram
        nodeStatuses={{ validate_request: "fired" }}
        validationAccepted={true}
        mode="pr"
      />,
    );
    const dl = screen.getByTestId("wf-step-clone_repo");
    expect(dl).toBeInTheDocument();
    expect(dl).toHaveAttribute("data-status", "empty");
    // The label is still legible (the topology row stays in place).
    expect(screen.getByText("Download")).toBeInTheDocument();
  });

  it("does not override clone_repo in PR mode if the backend somehow emits it", () => {
    // Defensive: if the backend ever decides to emit clone_repo
    // for a PR review (e.g. shallow clone for diff resolution),
    // the real envelope wins — we don't force `empty` over a real
    // terminal status.
    render(
      <WorkflowDiagram
        nodeStatuses={{ clone_repo: "fired" }}
        validationAccepted={null}
        mode="pr"
      />,
    );
    expect(screen.getByTestId("wf-step-clone_repo")).toHaveAttribute(
      "data-status",
      "fired",
    );
  });

  it("leaves clone_repo pending in repo mode (default)", () => {
    // Repo mode preserves the existing "pending until envelope"
    // behavior — operators see the dot fill in live.
    render(<WorkflowDiagram nodeStatuses={{}} validationAccepted={null} />);
    expect(screen.getByTestId("wf-step-clone_repo")).toHaveAttribute(
      "data-status",
      "pending",
    );
  });

  it("paints all non-terminal nodes as empty (skipped) when the run terminated", () => {
    // After Stop is clicked the WS emits __cancelled__; useReviewStream
    // sets isDone+isCancelled. App passes `terminal=true` to the diagram
    // so anything still pending/active drops to grey — operators
    // shouldn't see a node pulsing forever after Stop.
    render(
      <WorkflowDiagram
        nodeStatuses={{
          clone_repo: "fired",
          validate_request: "fired",
          dependency_review: "active",
        }}
        validationAccepted={true}
        terminal={true}
      />,
    );
    // The completed node stays green.
    expect(screen.getByTestId("wf-step-clone_repo")).toHaveAttribute(
      "data-status",
      "fired",
    );
    // The active one downgrades to empty.
    expect(screen.getByTestId("wf-step-dependency_review")).toHaveAttribute(
      "data-status",
      "empty",
    );
    // A node that never fired also reads as empty (rather than pending).
    expect(screen.getByTestId("wf-step-publish_report")).toHaveAttribute(
      "data-status",
      "empty",
    );
  });

  it("renders the Dependency badge marking it as deterministic (OSV.dev)", () => {
    // Tooltip carries the longer explanation; the badge label is
    // just `OSV.dev` to keep the sidebar compact.
    render(<WorkflowDiagram nodeStatuses={{}} validationAccepted={null} />);
    expect(screen.getByText("OSV.dev")).toBeInTheDocument();
  });
});
