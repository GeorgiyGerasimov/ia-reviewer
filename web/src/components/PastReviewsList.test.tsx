// Past reviews sidebar. Pure display — items + currentThreadId +
// onSelect from props, no fetch, no state. Same pattern as
// ActiveReviewsList. Empty state shows an inline placeholder
// (unlike ActiveReviewsList which hides entirely) because past
// reviews is the FIRST place new operators look when nothing's
// running.

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PastReviewsList } from "./PastReviewsList";
import type { ReviewSummary } from "../api/types";

function row(over: Partial<ReviewSummary> = {}): ReviewSummary {
  return {
    thread_id: "tid",
    mode: "pr",
    target_url: "https://github.com/o/r/pull/42",
    overall_severity: "major",
    finding_count: 3,
    validation_accepted: true,
    created_at: "2026-06-07T00:00:00Z",
    ...over,
  };
}

describe("<PastReviewsList />", () => {
  it("renders an empty-state placeholder when there are no items", () => {
    render(
      <PastReviewsList items={[]} currentThreadId={null} onSelect={vi.fn()} />,
    );
    expect(screen.getByText(/no persisted reviews/i)).toBeInTheDocument();
  });

  it("renders one row per past review", () => {
    render(
      <PastReviewsList
        items={[row({ thread_id: "a" }), row({ thread_id: "b" })]}
        currentThreadId={null}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getAllByTestId(/^past-row-/)).toHaveLength(2);
  });

  it("calls onSelect(thread_id) when a row is clicked", async () => {
    const onSelect = vi.fn();
    render(
      <PastReviewsList
        items={[row({ thread_id: "tid-x" })]}
        currentThreadId={null}
        onSelect={onSelect}
      />,
    );
    await userEvent.click(screen.getByTestId("past-row-tid-x"));
    expect(onSelect).toHaveBeenCalledWith("tid-x");
  });

  it("marks the matching row as current via data-current", () => {
    render(
      <PastReviewsList
        items={[row({ thread_id: "a" }), row({ thread_id: "b" })]}
        currentThreadId="b"
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByTestId("past-row-a")).toHaveAttribute("data-current", "false");
    expect(screen.getByTestId("past-row-b")).toHaveAttribute("data-current", "true");
  });

  it("renders a rejected row with the rejected indicator", () => {
    // Rejected reviews don't have findings — show them in red so
    // operators don't confuse them with successful runs.
    render(
      <PastReviewsList
        items={[row({ thread_id: "x", overall_severity: "rejected", validation_accepted: false,
          finding_count: 0 })]}
        currentThreadId={null}
        onSelect={vi.fn()}
      />,
    );
    const r = screen.getByTestId("past-row-x");
    expect(r).toHaveAttribute("data-severity", "rejected");
  });

  it("normalizes a casing variant on severity", () => {
    // The summary table on the backend can produce uppercase /
    // mixed-case severities. The UI must still pick the right
    // color via `normalizeSeverity` from lib/format.
    render(
      <PastReviewsList
        items={[row({ thread_id: "x", overall_severity: "CRITICAL" as never })]}
        currentThreadId={null}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByTestId("past-row-x")).toHaveAttribute(
      "data-severity",
      "critical",
    );
  });

  it("shows the findings count next to each row", () => {
    render(
      <PastReviewsList
        items={[row({ thread_id: "x", finding_count: 7 })]}
        currentThreadId={null}
        onSelect={vi.fn()}
      />,
    );
    // "7 findings" or "7" — at least the number appears.
    expect(screen.getByTestId("past-row-x")).toHaveTextContent(/7/);
  });
});
