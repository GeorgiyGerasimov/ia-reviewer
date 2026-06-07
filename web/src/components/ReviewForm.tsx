// PR-or-repo URL submit form. Detects mode from the URL, dispatches
// to the backend, and notifies the parent of the resulting thread_id
// via `onSubmitted`. The parent decides what to do next — typically
// store the thread_id in state and pass it to `useReviewStream` to
// open the WS for live progress.

import { useState, type FormEvent } from "react";
import { api, APIError } from "../api/client";
import { Button } from "@/components/ui/button";

export interface ReviewFormProps {
  /** Called after a successful POST /review with the new thread_id.
   *  The form doesn't clear the input — the user might want to
   *  trigger another review against the same / a similar URL. */
  onSubmitted: (threadId: string) => void;
}

/** True when the URL points at a specific PR (contains `/pull/N`).
 *  Otherwise repo mode. Heuristic kept intentionally simple — the
 *  backend validates the URL definitively. */
function isPRUrl(url: string): boolean {
  return /\/pull\/\d+(?:[/?#]|$)/.test(url);
}

export function ReviewForm({ onSubmitted }: ReviewFormProps) {
  const [url, setUrl] = useState("");
  const [ref, setRef] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!url.trim() || submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      const trimmed = url.trim();
      const body = isPRUrl(trimmed)
        ? // PR mode: `ref` is meaningless when the PR number scopes
          // the diff; drop it even if typed.
          { pr_url: trimmed }
        : ref.trim()
          ? { repo_url: trimmed, ref: ref.trim() }
          : { repo_url: trimmed };
      const result = await api.triggerReview(body);
      onSubmitted(result.thread_id);
    } catch (err) {
      // APIError.body is either a parsed JSON object or raw text.
      // Both 400 (`{error: "..."}`) and 5xx shapes flow through here.
      const message =
        err instanceof APIError
          ? extractErrorMessage(err.body) ?? `API ${err.status}`
          : err instanceof Error
            ? err.message
            : "unknown error";
      setError(message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-wrap gap-2 items-start">
      <label className="sr-only" htmlFor="review-url">
        URL
      </label>
      <input
        id="review-url"
        type="text"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        placeholder="https://github.com/owner/repo  or  https://github.com/owner/repo/pull/123"
        className="flex-1 min-w-[200px] px-3 py-2 rounded-md border border-border bg-card text-foreground text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        autoComplete="off"
        required
      />
      <label className="sr-only" htmlFor="review-ref">
        ref (branch / tag / sha) — repo only
      </label>
      <input
        id="review-ref"
        type="text"
        value={ref}
        onChange={(e) => setRef(e.target.value)}
        placeholder="ref (branch / tag / sha) — repo only"
        className="w-[220px] px-3 py-2 rounded-md border border-border bg-card text-foreground text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        autoComplete="off"
      />
      <Button type="submit" disabled={submitting || !url.trim()}>
        Review
      </Button>
      {error && (
        <div
          role="alert"
          className="basis-full text-sm text-destructive"
        >
          {error}
        </div>
      )}
    </form>
  );
}

/** Unwrap whatever the FastAPI error body shape happens to be:
 *  `{error: string}` (our custom 400s), `{detail: string}` (FastAPI
 *  HTTPException), or raw text. */
function extractErrorMessage(body: unknown): string | null {
  if (typeof body === "string") return body;
  if (body && typeof body === "object") {
    const obj = body as Record<string, unknown>;
    if (typeof obj.error === "string") return obj.error;
    if (typeof obj.detail === "string") return obj.detail;
  }
  return null;
}
