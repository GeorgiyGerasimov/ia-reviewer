// Wire shapes for WebSocket envelopes broadcast by the FastAPI
// backend over `/ws/chat/{thread_id}`. Mirror what's emitted in
// `main.py::_stream_graph_with_progress` and
// `src/agents/base_reviewer.py::_emit_file_done` etc. Drift between
// these and the Python side breaks the UI silently — keep in sync.

import type { ReviewerRole } from "./types";

/** Workflow-level node status. `_classify_progress` in main.py maps
 *  the LangGraph chunk to one of these. The UI colours dots from
 *  this value. `active` is set client-side when we want a pulsing
 *  indicator before the node has fired (e.g. clone_repo started). */
export type NodeStatus = "active" | "fired" | "empty" | "rejected";

/** Workflow envelope — fires once per graph-node completion plus
 *  the terminal `__done__` / `__cancelled__` markers. */
export interface ProgressEnvelope {
  type: "progress";
  /** Graph-node name, OR the synthetic terminal markers
   *  `__done__` / `__cancelled__`. */
  node: string;
  status?: NodeStatus;
  /** Set ONLY on `node === "validate_request"` — carries the
   *  accept/reject verdict so the UI can resolve the alternate
   *  branch (reviewers ↔ notify_rejection) to its final colour
   *  immediately. */
  accepted?: boolean;
}

/** Per-file scan progress emitted from inside LLMPerFileReviewer.
 *  Three sub-states keyed by `state`. */
export interface FileProgressStartedEnvelope {
  type: "file_progress";
  role: ReviewerRole;
  state: "started_batch";
  total: number;
  paths: string[];
  skipped?: Record<string, number>;
}

export interface FileProgressDoneEnvelope {
  type: "file_progress";
  role: ReviewerRole;
  state: "file_done";
  path: string;
  index: number;
  total: number;
  findings_count: number;
  failed: boolean;
}

export interface FileProgressFinishedEnvelope {
  type: "file_progress";
  role: ReviewerRole;
  state: "finished";
  processed: number;
  failed: number;
  skipped_empty: number;
  findings_total: number;
  total: number;
}

export type FileProgressEnvelope =
  | FileProgressStartedEnvelope
  | FileProgressDoneEnvelope
  | FileProgressFinishedEnvelope;

/** Chat-style message broadcast over the same socket — used today
 *  for "review complete" notices, cancellation notices, system
 *  alerts. */
export interface ChatMessageEnvelope {
  role: "user" | "system" | "agent";
  text: string;
  timestamp: string;
}

/** Anything we receive on the socket. Discriminated by the
 *  presence + value of `type`. */
export type WSEnvelope = ProgressEnvelope | FileProgressEnvelope | ChatMessageEnvelope;

/** Type guard helpers used by the hook to route envelopes to the
 *  right reducer branch without ad-hoc `'type' in env` checks
 *  scattered across the codebase. */
export function isProgressEnvelope(env: WSEnvelope): env is ProgressEnvelope {
  return (env as ProgressEnvelope).type === "progress";
}

export function isFileProgressEnvelope(env: WSEnvelope): env is FileProgressEnvelope {
  return (env as FileProgressEnvelope).type === "file_progress";
}

export function isChatMessage(env: WSEnvelope): env is ChatMessageEnvelope {
  return typeof (env as ChatMessageEnvelope).role === "string" && "text" in env;
}
