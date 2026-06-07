// Renders per-node LLM token accounting from `review.token_usage`.
// Pure props: parent supplies the map. Shows total in/out + a
// collapsible <details> with per-node breakdown sorted by total
// tokens descending.

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { TokenUsagePanel } from "./TokenUsagePanel";
import type { TokenUsage } from "../api/types";

describe("<TokenUsagePanel />", () => {
  it("returns nothing when usage is empty", () => {
    const { container } = render(<TokenUsagePanel usage={{}} />);
    expect(container.firstChild).toBeNull();
  });

  it("returns nothing when usage is undefined", () => {
    const { container } = render(<TokenUsagePanel usage={undefined} />);
    expect(container.firstChild).toBeNull();
  });

  it("shows total in/out summed across all nodes", () => {
    const usage: TokenUsage = {
      validate_request: { input: 200, output: 30, calls: 1 },
      injection_review: { input: 5000, output: 250, calls: 3 },
    };
    render(<TokenUsagePanel usage={usage} />);
    // 200+5000 = 5200 → "5.2k", 30+250 = 280
    expect(screen.getByText(/5\.2k/)).toBeInTheDocument();
    expect(screen.getByText(/280/)).toBeInTheDocument();
  });

  it("shows total call count summed across nodes", () => {
    const usage: TokenUsage = {
      a: { input: 1, output: 1, calls: 1 },
      b: { input: 1, output: 1, calls: 2 },
    };
    render(<TokenUsagePanel usage={usage} />);
    expect(screen.getByText(/3 calls/)).toBeInTheDocument();
  });

  it("singularizes call count to 'call' when total is 1", () => {
    render(
      <TokenUsagePanel
        usage={{ a: { input: 100, output: 20, calls: 1 } }}
      />,
    );
    expect(screen.getByText(/1 call$/)).toBeInTheDocument();
  });

  it("renders a per-node row for every key in usage", () => {
    const usage: TokenUsage = {
      validate_request: { input: 200, output: 30, calls: 1 },
      injection_review: { input: 5000, output: 250, calls: 3 },
      owasp_review: { input: 1500, output: 80, calls: 2 },
    };
    render(<TokenUsagePanel usage={usage} />);
    expect(screen.getByText("validate_request")).toBeInTheDocument();
    expect(screen.getByText("injection_review")).toBeInTheDocument();
    expect(screen.getByText("owasp_review")).toBeInTheDocument();
  });

  it("sorts per-node rows by total tokens descending", () => {
    const usage: TokenUsage = {
      validate_request: { input: 200, output: 30, calls: 1 },     // 230
      injection_review: { input: 5000, output: 250, calls: 3 },   // 5250
      owasp_review: { input: 1500, output: 80, calls: 2 },        // 1580
    };
    render(<TokenUsagePanel usage={usage} />);
    // Read all node labels in DOM order; expect injection first.
    const labels = screen.getAllByTestId(/^tu-node-/).map((el) =>
      el.getAttribute("data-node"),
    );
    expect(labels).toEqual([
      "injection_review",
      "owasp_review",
      "validate_request",
    ]);
  });

  it("shows the model name(s) when present on a bucket", () => {
    render(
      <TokenUsagePanel
        usage={{
          injection_review: {
            input: 1000,
            output: 50,
            calls: 2,
            models: ["claude-sonnet-4-6"],
          },
        }}
      />,
    );
    expect(screen.getByText(/claude-sonnet-4-6/)).toBeInTheDocument();
  });
});
