// Hook contract pinned: opens a WS on mount, tracks per-node status
// from `progress` envelopes, resolves the validator branch from
// `accepted`, exposes a `done` flag once the terminal `__done__`
// arrives, closes the socket on unmount.
//
// All timing is deterministic — MockWebSocket lets us drive
// open/message/close synchronously from inside `act()`. No setTimeout,
// no fake timers needed.

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useReviewStream } from "./useReviewStream";
import { installMockWebSocket, MockWebSocket } from "../test/mockWebSocket";

beforeEach(() => {
  installMockWebSocket();
});

afterEach(() => {
  MockWebSocket.reset();
});

describe("useReviewStream", () => {
  it("opens a WS to /ws/chat/{threadId} on mount", () => {
    renderHook(() => useReviewStream("tid-abc"));
    const ws = MockWebSocket.latest();
    expect(ws.url).toMatch(/\/ws\/chat\/tid-abc$/);
  });

  it("does NOT open a socket when threadId is null", () => {
    renderHook(() => useReviewStream(null));
    expect(MockWebSocket.instances).toHaveLength(0);
  });

  it("flips `connected` to true on the WS open event", () => {
    const { result } = renderHook(() => useReviewStream("tid"));
    expect(result.current.connected).toBe(false);
    act(() => MockWebSocket.latest().acceptConnection());
    expect(result.current.connected).toBe(true);
  });

  it("records per-node status from `progress` envelopes", () => {
    const { result } = renderHook(() => useReviewStream("tid"));
    const ws = MockWebSocket.latest();
    act(() => ws.acceptConnection());

    act(() => ws.emit({ type: "progress", node: "clone_repo", status: "fired" }));
    act(() => ws.emit({ type: "progress", node: "injection_review", status: "fired" }));
    act(() => ws.emit({ type: "progress", node: "configuration_review", status: "empty" }));

    expect(result.current.nodeStatuses.clone_repo).toBe("fired");
    expect(result.current.nodeStatuses.injection_review).toBe("fired");
    // `empty` means scope-skipped: render in gray, not green.
    expect(result.current.nodeStatuses.configuration_review).toBe("empty");
  });

  it("tracks the latest status when a node fires multiple times", () => {
    // Pre-graph `active` markers (clone_repo started) are followed
    // by a `fired` once LangGraph runs the node. The UI should
    // show `fired` (the latest), not `active`.
    const { result } = renderHook(() => useReviewStream("tid"));
    const ws = MockWebSocket.latest();
    act(() => ws.acceptConnection());

    act(() => ws.emit({ type: "progress", node: "clone_repo", status: "active" }));
    expect(result.current.nodeStatuses.clone_repo).toBe("active");

    act(() => ws.emit({ type: "progress", node: "clone_repo", status: "fired" }));
    expect(result.current.nodeStatuses.clone_repo).toBe("fired");
  });

  it("captures the validator verdict from `accepted` on validate_request", () => {
    const { result } = renderHook(() => useReviewStream("tid"));
    const ws = MockWebSocket.latest();
    act(() => ws.acceptConnection());

    expect(result.current.validationAccepted).toBeNull();

    act(() =>
      ws.emit({
        type: "progress",
        node: "validate_request",
        status: "fired",
        accepted: true,
      }),
    );
    expect(result.current.validationAccepted).toBe(true);
  });

  it("captures a reject verdict and flags the validator branch", () => {
    const { result } = renderHook(() => useReviewStream("tid"));
    const ws = MockWebSocket.latest();
    act(() => ws.acceptConnection());

    act(() =>
      ws.emit({
        type: "progress",
        node: "validate_request",
        status: "fired",
        accepted: false,
      }),
    );
    expect(result.current.validationAccepted).toBe(false);
  });

  it("flags isDone when the terminal __done__ envelope arrives", () => {
    const { result } = renderHook(() => useReviewStream("tid"));
    const ws = MockWebSocket.latest();
    act(() => ws.acceptConnection());

    expect(result.current.isDone).toBe(false);
    act(() => ws.emit({ type: "progress", node: "__done__" }));
    expect(result.current.isDone).toBe(true);
  });

  it("flags isCancelled when __cancelled__ arrives", () => {
    const { result } = renderHook(() => useReviewStream("tid"));
    const ws = MockWebSocket.latest();
    act(() => ws.acceptConnection());

    act(() => ws.emit({ type: "progress", node: "__cancelled__" }));
    expect(result.current.isCancelled).toBe(true);
    // __cancelled__ is terminal — same as __done__ for "the
    // review's not running anymore" decisions.
    expect(result.current.isDone).toBe(true);
  });

  it("ignores chat messages — they don't affect workflow state", () => {
    // Chat messages flow through the same socket but the workflow
    // panel doesn't care about them. The hook silently routes
    // them past the workflow reducer; consumers that want chat
    // can read `messages` (asserted in a separate test).
    const { result } = renderHook(() => useReviewStream("tid"));
    const ws = MockWebSocket.latest();
    act(() => ws.acceptConnection());

    act(() =>
      ws.emit({ role: "system", text: "Review complete.", timestamp: "2026-06-07" }),
    );

    expect(result.current.nodeStatuses).toEqual({});
    expect(result.current.isDone).toBe(false);
  });

  it("exposes the chat message stream separately from progress", () => {
    const { result } = renderHook(() => useReviewStream("tid"));
    const ws = MockWebSocket.latest();
    act(() => ws.acceptConnection());

    act(() =>
      ws.emit({ role: "system", text: "Review complete.", timestamp: "t" }),
    );
    act(() => ws.emit({ role: "user", text: "rerun: more context", timestamp: "t" }));

    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages[0].text).toBe("Review complete.");
    expect(result.current.messages[1].role).toBe("user");
  });

  it("closes the socket on unmount", () => {
    const { unmount } = renderHook(() => useReviewStream("tid"));
    const ws = MockWebSocket.latest();
    act(() => ws.acceptConnection());

    unmount();
    expect(ws.readyState).toBe(MockWebSocket.CLOSED);
  });

  it("opens a fresh socket when threadId changes", () => {
    const { rerender } = renderHook(({ tid }) => useReviewStream(tid), {
      initialProps: { tid: "tid-a" as string | null },
    });
    const first = MockWebSocket.latest();
    expect(first.url).toMatch(/tid-a$/);

    rerender({ tid: "tid-b" as string | null });
    const second = MockWebSocket.latest();
    expect(second.url).toMatch(/tid-b$/);
    // The previous socket is closed.
    expect(first.readyState).toBe(MockWebSocket.CLOSED);
  });

  it("cascades active to the security reviewers when validate_request accepts", () => {
    // Without this cascade, after `validate_request` fires the
    // four reviewer dots stay grey for ~20s while the LLMs run —
    // no visual feedback. Mirroring legacy `handleValidationResult`,
    // we mark all four + the synthetic parent as active.
    const { result } = renderHook(() => useReviewStream("tid"));
    const ws = MockWebSocket.latest();
    act(() => ws.acceptConnection());

    act(() =>
      ws.emit({
        type: "progress",
        node: "validate_request",
        status: "fired",
        accepted: true,
      }),
    );

    for (const node of [
      "dependency_review",
      "injection_review",
      "owasp_review",
      "configuration_review",
      "security_reviewers",
    ]) {
      expect(result.current.nodeStatuses[node]).toBe("active");
    }
  });

  it("cascades active to review_decision when a specialist fires", () => {
    // Once a specialist returns, review_decision is what's next on
    // the LLM-busy path — give it the pulsing indicator so the
    // sidebar shows *something* moving while LangGraph runs the
    // ambiguity heuristic + optional interrupt.
    const { result } = renderHook(() => useReviewStream("tid"));
    const ws = MockWebSocket.latest();
    act(() => ws.acceptConnection());

    act(() => ws.emit({ type: "progress", node: "injection_review", status: "fired" }));
    expect(result.current.nodeStatuses.review_decision).toBe("active");
  });

  it("cascades active to aggregate_results after review_decision fires", () => {
    const { result } = renderHook(() => useReviewStream("tid"));
    const ws = MockWebSocket.latest();
    act(() => ws.acceptConnection());

    act(() =>
      ws.emit({ type: "progress", node: "review_decision", status: "fired" }),
    );
    expect(result.current.nodeStatuses.aggregate_results).toBe("active");
  });

  it("cascades active to publish_report after aggregate_results fires", () => {
    const { result } = renderHook(() => useReviewStream("tid"));
    const ws = MockWebSocket.latest();
    act(() => ws.acceptConnection());

    act(() =>
      ws.emit({ type: "progress", node: "aggregate_results", status: "fired" }),
    );
    expect(result.current.nodeStatuses.publish_report).toBe("active");
  });

  it("does NOT downgrade a terminal status when an upstream node cascades active onto it", () => {
    // Race: review_decision finishes BEFORE all reviewers' envelopes
    // arrive (the join can fire on the first complete batch). A
    // late reviewer envelope must not flip review_decision back
    // to active.
    const { result } = renderHook(() => useReviewStream("tid"));
    const ws = MockWebSocket.latest();
    act(() => ws.acceptConnection());

    act(() => ws.emit({ type: "progress", node: "review_decision", status: "fired" }));
    act(() => ws.emit({ type: "progress", node: "injection_review", status: "fired" }));

    expect(result.current.nodeStatuses.review_decision).toBe("fired");
  });

  it("tolerates malformed JSON on the wire without crashing", () => {
    // A misbehaving proxy / dev tool might inject non-JSON text.
    // We log and drop, never throw — otherwise a single bad frame
    // would unmount the panel.
    const { result } = renderHook(() => useReviewStream("tid"));
    const ws = MockWebSocket.latest();
    act(() => ws.acceptConnection());

    act(() => {
      ws.onmessage?.(new MessageEvent("message", { data: "not json {" }));
    });

    // Still happy; can still process the next valid envelope.
    act(() =>
      ws.emit({ type: "progress", node: "clone_repo", status: "fired" }),
    );
    expect(result.current.nodeStatuses.clone_repo).toBe("fired");
  });
});
