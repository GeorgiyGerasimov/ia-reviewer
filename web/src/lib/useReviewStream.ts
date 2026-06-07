// React hook wrapping the `/ws/chat/{threadId}` WebSocket. Consumers
// get a derived view of the review's live progress: per-node status,
// validator verdict, done/cancelled flags, chat messages — without
// touching the raw socket.
//
// Why a hook (not a context provider): one review per page in the
// current UX, so the cost of "every consumer remounts the socket"
// doesn't exist. If we add multi-review tabs later, lift this into a
// provider keyed by threadId. For now: minimal API surface, easy to
// test (`renderHook` + `MockWebSocket`).
//
// The reducer pattern keeps state transitions explicit and trivially
// inspectable in tests. Adding a new envelope type means adding one
// case to `reduce`, not threading state through useState pairs.

import { useEffect, useReducer, useRef } from "react";
import {
  isChatMessage,
  isFileProgressEnvelope,
  isProgressEnvelope,
  type ChatMessageEnvelope,
  type FileProgressEnvelope,
  type NodeStatus,
  type ProgressEnvelope,
  type WSEnvelope,
} from "../api/ws-events";

export interface ReviewStreamState {
  /** Socket is connected (post-handshake). */
  connected: boolean;
  /** Map: graph node name → latest status seen. Last-write-wins so
   *  pre-graph "active" markers get overridden by the real "fired"
   *  once the node completes. */
  nodeStatuses: Record<string, NodeStatus>;
  /** Validator branch verdict. `null` until validate_request fires. */
  validationAccepted: boolean | null;
  /** Per-role per-file progress snapshot. Last envelope wins per role
   *  — UI cares about the latest state (counts + current file), not
   *  the full history. */
  fileProgress: Record<string, FileProgressEnvelope>;
  /** Chat messages received over the same socket. Append-only. */
  messages: ChatMessageEnvelope[];
  /** Review reached its terminal state — either ran to publish_report
   *  (__done__) or was cancelled (__cancelled__). */
  isDone: boolean;
  /** Set only by __cancelled__ (Stop button). isDone is also true. */
  isCancelled: boolean;
}

const INITIAL_STATE: ReviewStreamState = {
  connected: false,
  nodeStatuses: {},
  validationAccepted: null,
  fileProgress: {},
  messages: [],
  isDone: false,
  isCancelled: false,
};

type Action =
  | { kind: "open" }
  | { kind: "close" }
  | { kind: "reset" }
  | { kind: "progress"; env: ProgressEnvelope }
  | { kind: "file_progress"; env: FileProgressEnvelope }
  | { kind: "chat"; env: ChatMessageEnvelope };

// Cascade map: when KEY fires (or otherwise reaches a non-active
// state), mark each VALUE as `active` UNLESS the value is already
// in a terminal state. Mirrors the legacy template's NEXT_AFTER
// in templates/index.html. Without this the sidebar's dots stay
// grey for ~20s during LLM-busy stretches because the backend
// only emits envelopes on node completion, not entry.
const CASCADE_ACTIVE: Record<string, readonly string[]> = {
  clone_repo: ["validate_request"],
  dependency_review: ["review_decision"],
  injection_review: ["review_decision"],
  owasp_review: ["review_decision"],
  configuration_review: ["review_decision"],
  review_decision: ["aggregate_results"],
  aggregate_results: ["publish_report"],
};

// Set on validate_request acceptance — all four specialists +
// the synthetic security_reviewers parent start pulsing as soon
// as the validator clears.
const ACCEPT_FANOUT = [
  "dependency_review",
  "injection_review",
  "owasp_review",
  "configuration_review",
  "security_reviewers",
] as const;

function isTerminal(s: NodeStatus | undefined): boolean {
  return s === "fired" || s === "empty" || s === "rejected";
}

/** Apply an `active` mark to `node` only if it doesn't already
 *  hold a terminal state. Terminal > active in the legacy UI's
 *  priority order — a late upstream completion can't downgrade
 *  a downstream node that already finished. */
function applyActive(
  acc: Record<string, NodeStatus>,
  node: string,
): Record<string, NodeStatus> {
  if (isTerminal(acc[node])) return acc;
  if (acc[node] === "active") return acc;
  return { ...acc, [node]: "active" };
}

function reduce(state: ReviewStreamState, action: Action): ReviewStreamState {
  switch (action.kind) {
    case "reset":
      return INITIAL_STATE;
    case "open":
      return { ...state, connected: true };
    case "close":
      return { ...state, connected: false };
    case "progress": {
      const { node, status, accepted } = action.env;
      if (node === "__done__") return { ...state, isDone: true };
      if (node === "__cancelled__")
        return { ...state, isDone: true, isCancelled: true };
      let nextStatuses = status
        ? { ...state.nodeStatuses, [node]: status }
        : state.nodeStatuses;
      // Cascade `active` to successors when this node has reached
      // a non-active state. We don't cascade on `active` itself —
      // would create a chain reaction with no observable end.
      if (status && status !== "active") {
        for (const successor of CASCADE_ACTIVE[node] ?? []) {
          nextStatuses = applyActive(nextStatuses, successor);
        }
      }
      // validate_request → if accepted, light up the four
      // specialists + the synthetic parent. The reject branch
      // gets its own envelope from the backend (notify_rejection).
      if (
        node === "validate_request" &&
        typeof accepted === "boolean" &&
        accepted
      ) {
        for (const n of ACCEPT_FANOUT) {
          nextStatuses = applyActive(nextStatuses, n);
        }
      }
      const nextValidation =
        node === "validate_request" && typeof accepted === "boolean"
          ? accepted
          : state.validationAccepted;
      return {
        ...state,
        nodeStatuses: nextStatuses,
        validationAccepted: nextValidation,
      };
    }
    case "file_progress":
      return {
        ...state,
        fileProgress: { ...state.fileProgress, [action.env.role]: action.env },
      };
    case "chat":
      return { ...state, messages: [...state.messages, action.env] };
  }
}

/** Compose the WS URL relative to the page origin. Keeping this here
 *  (rather than letting callers pass a URL) means dev (Vite proxy)
 *  and prod (FastAPI same-origin) work identically — both flow
 *  through `window.location`. */
function wsUrlFor(threadId: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws/chat/${threadId}`;
}

export function useReviewStream(threadId: string | null): ReviewStreamState {
  const [state, dispatch] = useReducer(reduce, INITIAL_STATE);
  // Hold the latest socket in a ref so the cleanup function can
  // close it without re-running the effect on every state change.
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!threadId) return;
    dispatch({ kind: "reset" });

    const ws = new WebSocket(wsUrlFor(threadId));
    socketRef.current = ws;

    ws.onopen = () => dispatch({ kind: "open" });
    ws.onclose = () => dispatch({ kind: "close" });
    ws.onmessage = (ev) => {
      let env: WSEnvelope;
      try {
        env = JSON.parse(ev.data) as WSEnvelope;
      } catch (err) {
        // Don't crash on a malformed frame — one bad proxy injection
        // shouldn't unmount the panel. Log once and continue.
        console.warn("useReviewStream: dropping malformed envelope", err);
        return;
      }
      if (isProgressEnvelope(env)) {
        dispatch({ kind: "progress", env });
      } else if (isFileProgressEnvelope(env)) {
        dispatch({ kind: "file_progress", env });
      } else if (isChatMessage(env)) {
        dispatch({ kind: "chat", env });
      }
      // else: unknown envelope. Same fail-soft contract as malformed
      // JSON — drop silently rather than crash. The set of envelope
      // types will grow; we don't want every old client to break
      // when the backend ships a new one.
    };
    ws.onerror = () => {
      // Network blips also surface as a close event right after, so
      // we don't need to dispatch anything specific here. Logging
      // gives operators a breadcrumb when something's truly broken.
      console.warn("useReviewStream: socket error");
    };

    return () => {
      ws.close();
      socketRef.current = null;
    };
  }, [threadId]);

  return state;
}
