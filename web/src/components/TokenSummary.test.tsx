// Verifies the engineering template: typed props in, deterministic
// DOM out, formatter composition with `fmtTokens` exercised through
// the rendered text.
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { TokenSummary } from "./TokenSummary";

describe("<TokenSummary />", () => {
  it("renders the zero state with an accessibility label", () => {
    render(<TokenSummary input={0} output={0} calls={0} />);
    // The zero-state aria-label is meaningfully different from the
    // active-state one so screen readers / tests can distinguish them.
    expect(screen.getByLabelText("No LLM activity yet")).toBeInTheDocument();
    expect(screen.getByText(/0 in · 0 out/)).toBeInTheDocument();
  });

  it("renders formatted input + output + call count for non-zero state", () => {
    render(<TokenSummary input={12_345} output={1_200} calls={3} />);
    const label = screen.getByLabelText("Token usage so far");
    expect(label).toHaveTextContent(/12\.3k.*in/);
    expect(label).toHaveTextContent(/1\.2k.*out/);
    expect(label).toHaveTextContent(/3 calls/);
  });

  it("uses singular 'call' for exactly one call", () => {
    render(<TokenSummary input={500} output={50} calls={1} />);
    expect(screen.getByLabelText("Token usage so far")).toHaveTextContent(/1 call$/);
  });

  it("forwards a className to the wrapper", () => {
    render(<TokenSummary input={1} output={1} calls={1} className="custom-class" />);
    const wrapper = screen.getByLabelText("Token usage so far");
    expect(wrapper).toHaveClass("custom-class");
  });
});
