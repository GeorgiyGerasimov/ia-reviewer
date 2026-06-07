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

  it("does NOT open a WS socket until a thread_id is selected", () => {
    render(<App />);
    // The hook only opens the socket when threadId becomes
    // non-null. With nothing selected, MockWebSocket.instances
    // is empty.
    expect(MockWebSocket.instances).toHaveLength(0);
  });

  it("opens a WS for the submitted thread and triggers the report fetch", async () => {
    render(<App />);

    // The initial /reviews/active + /reviews polls used the
    // generic mock from beforeEach. The form submit is the next
    // fetch — stage the POST /review response for it.
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

    // After submit, the hook opens a WS against the new thread.
    // We poll the mock registry until it appears (RTL's act
    // flushes the setState, but the hook's useEffect chain runs
    // one tick later).
    await screen.findByPlaceholderText(/github\.com\/owner\/repo/);
    // Use waitFor-style retry via findByText against the report
    // panel's thread label.
    await screen.findByText("new-tid-99");
    expect(MockWebSocket.latest().url).toMatch(/\/ws\/chat\/new-tid-99$/);
  });
});
