// ReviewForm pins the contract for: URL → triggerReview → onSubmitted.
// It auto-detects PR mode from `/pull/N` substring; repo mode otherwise.
// Errors from the API surface inline (the form doesn't crash).
// The Submit button is disabled while the request is in flight so a
// double-click can't fire two reviews against the same URL.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ReviewForm } from "./ReviewForm";

function stagePost(body: unknown, opts: { status?: number } = {}) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
    new Response(JSON.stringify(body), {
      status: opts.status ?? 202,
      headers: { "Content-Type": "application/json" },
    }) as Response,
  );
}

beforeEach(() => {
  // Vitest 4 keeps spies installed across tests by default — without
  // explicit cleanup, `vi.spyOn(globalThis, "fetch")` in test N+1
  // composes onto the spy from test N and `toHaveBeenCalledTimes(1)`
  // counts BOTH tests' calls. `restoreAllMocks` puts globalThis.fetch
  // back to the original between tests.
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("<ReviewForm />", () => {
  it("renders the URL input and a Review button", () => {
    render(<ReviewForm onSubmitted={vi.fn()} />);
    expect(screen.getByPlaceholderText(/github\.com\/owner\/repo/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^review$/i })).toBeInTheDocument();
  });

  it("submits a PR-mode body when the URL contains /pull/N", async () => {
    const spy = stagePost({ status: "started", thread_id: "tid-pr",
      pr_url: "https://github.com/o/r/pull/42" });
    const onSubmitted = vi.fn();
    render(<ReviewForm onSubmitted={onSubmitted} />);

    await userEvent.type(
      screen.getByLabelText(/URL/i),
      "https://github.com/o/r/pull/42",
    );
    await userEvent.click(screen.getByRole("button", { name: /^review$/i }));

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    const init = spy.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(init.body).toBe(
      JSON.stringify({ pr_url: "https://github.com/o/r/pull/42" }),
    );
    await waitFor(() => expect(onSubmitted).toHaveBeenCalledWith("tid-pr"));
  });

  it("submits a repo-mode body when URL is a plain repo URL", async () => {
    const spy = stagePost({ status: "started", thread_id: "tid-repo",
      repo_url: "https://github.com/o/r" });
    render(<ReviewForm onSubmitted={vi.fn()} />);

    await userEvent.type(
      screen.getByLabelText(/URL/i),
      "https://github.com/o/r",
    );
    await userEvent.click(screen.getByRole("button", { name: /^review$/i }));

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    const init = spy.mock.calls[0][1] as RequestInit;
    expect(init.body).toBe(JSON.stringify({ repo_url: "https://github.com/o/r" }));
  });

  it("includes the ref field in repo mode when provided", async () => {
    const spy = stagePost({ status: "started", thread_id: "tid",
      repo_url: "https://github.com/o/r", ref: "feature-x" });
    render(<ReviewForm onSubmitted={vi.fn()} />);

    await userEvent.type(screen.getByLabelText(/URL/i), "https://github.com/o/r");
    await userEvent.type(screen.getByLabelText(/ref/i), "feature-x");
    await userEvent.click(screen.getByRole("button", { name: /^review$/i }));

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    const init = spy.mock.calls[0][1] as RequestInit;
    expect(init.body).toBe(
      JSON.stringify({ repo_url: "https://github.com/o/r", ref: "feature-x" }),
    );
  });

  it("omits the ref field in PR mode even when typed (it's repo-only)", async () => {
    // The PR diff is fully scoped by the PR number — `ref` is
    // meaningless. We don't send it to avoid confusing the backend.
    const spy = stagePost({ status: "started", thread_id: "tid",
      pr_url: "https://github.com/o/r/pull/1" });
    render(<ReviewForm onSubmitted={vi.fn()} />);

    await userEvent.type(
      screen.getByLabelText(/URL/i),
      "https://github.com/o/r/pull/1",
    );
    await userEvent.type(screen.getByLabelText(/ref/i), "junk-ignored");
    await userEvent.click(screen.getByRole("button", { name: /^review$/i }));

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    const init = spy.mock.calls[0][1] as RequestInit;
    expect(init.body).toBe(
      JSON.stringify({ pr_url: "https://github.com/o/r/pull/1" }),
    );
  });

  it("disables the Submit button while the request is in flight", async () => {
    // Stage a fetch that never resolves — the button stays disabled
    // for the full window.
    let resolveFetch!: (r: Response) => void;
    vi.spyOn(globalThis, "fetch").mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          resolveFetch = resolve;
        }),
    );
    render(<ReviewForm onSubmitted={vi.fn()} />);

    await userEvent.type(screen.getByLabelText(/URL/i), "https://github.com/o/r");
    const btn = screen.getByRole("button", { name: /^review$/i });
    await userEvent.click(btn);

    expect(btn).toBeDisabled();
    // Resolve and let RTL settle so we don't leak the promise.
    resolveFetch(
      new Response(
        JSON.stringify({ status: "started", thread_id: "tid", repo_url: "x" }),
        { status: 202, headers: { "Content-Type": "application/json" } },
      ),
    );
    await waitFor(() => expect(btn).not.toBeDisabled());
  });

  it("surfaces an inline error when the API rejects (400)", async () => {
    stagePost({ error: "invalid scope roles: ['foo']" }, { status: 400 });
    render(<ReviewForm onSubmitted={vi.fn()} />);

    await userEvent.type(screen.getByLabelText(/URL/i), "https://github.com/o/r");
    await userEvent.click(screen.getByRole("button", { name: /^review$/i }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/invalid scope/i),
    );
  });

  it("does not call onSubmitted when the API rejects", async () => {
    stagePost({ error: "nope" }, { status: 400 });
    const onSubmitted = vi.fn();
    render(<ReviewForm onSubmitted={onSubmitted} />);

    await userEvent.type(screen.getByLabelText(/URL/i), "https://github.com/o/r");
    await userEvent.click(screen.getByRole("button", { name: /^review$/i }));

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(onSubmitted).not.toHaveBeenCalled();
  });
});
