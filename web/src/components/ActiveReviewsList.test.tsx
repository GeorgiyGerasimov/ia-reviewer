// Sidebar list of in-flight reviews. Pure display — items + callback
// props from the parent, no fetch, no state. Mirrors TokenSummary
// and WorkflowDiagram: the App container wires `useActiveReviews`
// + cancel handler to it.

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ActiveReviewsList } from "./ActiveReviewsList";
import type { ActiveReview } from "../api/types";

function row(over: Partial<ActiveReview> = {}): ActiveReview {
  return {
    thread_id: "tid-1",
    mode: "repo",
    target: "https://github.com/owner/repo",
    ref: "main",
    started_at: "2026-06-07T00:00:00Z",
    elapsed_s: 42,
    tokens: { input: 1200, output: 80, calls: 2 },
    ...over,
  };
}

describe("<ActiveReviewsList />", () => {
  it("renders nothing visible when there are no items", () => {
    const { container } = render(
      <ActiveReviewsList items={[]} onSelect={vi.fn()} onCancel={vi.fn()} />,
    );
    // The whole panel hides — operators don't get a permanent empty
    // box on the sidebar.
    expect(container.firstChild).toBeNull();
  });

  it("renders one row per active review", () => {
    render(
      <ActiveReviewsList
        items={[row({ thread_id: "tid-a" }), row({ thread_id: "tid-b" })]}
        onSelect={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getAllByTestId(/^active-row-/)).toHaveLength(2);
  });

  it("shows elapsed time via fmtElapsed", () => {
    render(
      <ActiveReviewsList
        items={[row({ elapsed_s: 65 })]}
        onSelect={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    // 65s → "1m 5s" per format.ts.
    expect(screen.getByText("1m 5s")).toBeInTheDocument();
  });

  it("shows the live token totals under each row", () => {
    render(
      <ActiveReviewsList
        items={[row({ tokens: { input: 5100, output: 270, calls: 2 } })]}
        onSelect={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    // 5100 → "5.1k" per fmtTokens.
    expect(screen.getByLabelText(/Token usage so far/i)).toHaveTextContent(/5\.1k/);
  });

  it("calls onSelect(thread_id) when the row body is clicked", async () => {
    const onSelect = vi.fn();
    render(
      <ActiveReviewsList
        items={[row({ thread_id: "tid-x" })]}
        onSelect={onSelect}
        onCancel={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByTestId("active-row-tid-x"));
    expect(onSelect).toHaveBeenCalledWith("tid-x");
  });

  it("calls onCancel(thread_id) when Stop is clicked", async () => {
    const onCancel = vi.fn();
    render(
      <ActiveReviewsList
        items={[row({ thread_id: "tid-x" })]}
        onSelect={vi.fn()}
        onCancel={onCancel}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /stop/i }));
    expect(onCancel).toHaveBeenCalledWith("tid-x");
  });

  it("Stop click does not bubble into onSelect", async () => {
    // The Stop button sits inside the clickable row — clicking it
    // must NOT also select the review (that's a footgun: 'I tried
    // to stop it, but you opened it instead').
    const onSelect = vi.fn();
    const onCancel = vi.fn();
    render(
      <ActiveReviewsList
        items={[row({ thread_id: "tid-x" })]}
        onSelect={onSelect}
        onCancel={onCancel}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /stop/i }));
    expect(onCancel).toHaveBeenCalledWith("tid-x");
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("highlights the row that matches `currentThreadId`", () => {
    render(
      <ActiveReviewsList
        items={[
          row({ thread_id: "tid-a" }),
          row({ thread_id: "tid-b" }),
        ]}
        currentThreadId="tid-b"
        onSelect={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByTestId("active-row-tid-a")).toHaveAttribute(
      "data-current",
      "false",
    );
    expect(screen.getByTestId("active-row-tid-b")).toHaveAttribute(
      "data-current",
      "true",
    );
  });
});
