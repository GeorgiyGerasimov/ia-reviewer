// Smoke test for the wired top-level page. Verifies the three
// panels show up and the submit→select flow plumbs through to the
// "Current review" indicator — without exercising the WS or the
// polling timing (covered by the per-component tests).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";
import { installMockWebSocket, MockWebSocket } from "./test/mockWebSocket";

beforeEach(() => {
  installMockWebSocket();
  // Stage the mount-side `/reviews/active` poll. We return empty
  // so the sidebar list stays hidden; the form + page chrome are
  // what we actually want to assert.
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response("[]", {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }) as Response,
  );
});

afterEach(() => {
  MockWebSocket.reset();
  vi.restoreAllMocks();
});

describe("<App />", () => {
  it("renders the trigger form and workflow diagram on first paint", () => {
    render(<App />);
    expect(screen.getByText("Trigger a review")).toBeInTheDocument();
    // The form's URL input.
    expect(screen.getByPlaceholderText(/github\.com\/owner\/repo/)).toBeInTheDocument();
    // Workflow diagram is always visible (read-only, no review needed).
    expect(screen.getByText("Workflow")).toBeInTheDocument();
  });

  it("does NOT show 'Current review' until a thread_id is selected", () => {
    render(<App />);
    expect(screen.queryByText("Current review")).not.toBeInTheDocument();
  });

  it("flips into watching a thread after a successful submit", async () => {
    render(<App />);

    // The initial /reviews/active poll already used a generic mock;
    // the form submit is the next fetch. Stage the POST /review
    // response for it.
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          status: "started",
          thread_id: "new-tid-99",
          repo_url: "https://github.com/o/r",
        }),
        { status: 202, headers: { "Content-Type": "application/json" } },
      ) as Response,
    );

    await userEvent.type(
      screen.getByPlaceholderText(/github\.com\/owner\/repo/),
      "https://github.com/o/r",
    );
    await userEvent.click(screen.getByRole("button", { name: /^review$/i }));

    // The status pane appears with the new thread_id.
    expect(await screen.findByText("Current review")).toBeInTheDocument();
    expect(screen.getByText("new-tid-99")).toBeInTheDocument();
    // The WS hook should have opened a socket against the new thread.
    expect(MockWebSocket.latest().url).toMatch(/\/ws\/chat\/new-tid-99$/);
  });
});
