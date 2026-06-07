"""ia-reviewer FastAPI entry point.

Endpoints:
    GET  /                          — HTML UI (form + chat panel)
    GET  /health                    — liveness probe
    POST /review                    — fire-and-forget security review; returns thread_id
    GET  /chat/{thread_id}/history  — chat history for a thread
    WS   /ws/chat/{thread_id}       — live chat for a thread

Run with `uvicorn main:app --host 0.0.0.0 --port 8000`.
"""

import asyncio
import contextlib
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from langgraph.types import Command

from src.agents.coordinator import CoordinatorAgent
from src.agents.past_context import PastContextAgent
from src.chat.active_reviews import ActiveReviewsRegistry
from src.chat.hub import ChatHub
from src.chat.progress_emitter import ProgressEmitter, use_emitter
from src.chat.progress_log import progress_log_loop
from src.chat.progress_store import ProgressStore
from src.chat.store import ChatMessage, ChatStore
from src.graph.coordinator import build_review_graph
from src.graph.state import ALLOWED_SCOPE_ROLES, ReviewRequest, ReviewState
from src.integrations.embedder import Embedder
from src.integrations.github import GitHubClient
from src.integrations.repo_fetcher import (
    cleanup_snapshot,
    clone_repo,
    list_repo_files,
    normalize_repo_url,
)
from src.storage.review_store import ReviewStore
from src.utils.checkpointer import open_checkpointer
from src.utils.config import settings
from src.utils.logger import get_logger
from src.utils.tracing import get_langfuse_callback

logger = get_logger(__name__)

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Hard cap for the `/reviews?limit=…` query — bounds DB and memory cost
# of pagination. Anyone sending `?limit=999999` ends up with 200 rows
# rather than driving the server into OOM.
MAX_REVIEWS_PAGE_SIZE: int = 200

_APP_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def _normalize_uvicorn_logging() -> None:
    """Reformat uvicorn's own loggers to match our app's `<ts> | LEVEL | name | msg`
    layout.

    By default uvicorn prints request/lifecycle lines as `INFO:     <msg>` with
    no timestamp, while our `src.*` loggers print `2026-06-04 11:08:14,302 | INFO …`.
    Mixing the two makes it hard to correlate events in time. We overwrite the
    handler formatter on each known uvicorn logger so every line carries a
    timestamp. `uvicorn.access` records carry per-request fields (client_addr,
    request_line, status_code) which the format string templates against
    `%(message)s` — uvicorn pre-formats them into the message already.
    """
    fmt = logging.Formatter(_APP_LOG_FORMAT)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        target = logging.getLogger(name)
        for handler in target.handlers:
            handler.setFormatter(fmt)


def _register_routes(app: FastAPI) -> None:
    @app.get("/")
    async def index(request: Request):
        return _TEMPLATES.TemplateResponse(request, "index.html")

    @app.get("/img.png")
    async def img() -> FileResponse:
        """Serve the decorative Eeyore image that lives next to index.html."""
        return FileResponse(
            Path(__file__).parent / "templates" / "img.png",
            media_type="image/png",
        )

    @app.get("/health")
    async def health(request: Request) -> dict:
        """Liveness probe + observability info the UI uses to render the
        'Traces' link in the header.

        `langfuse_url`:
          - The configured `LANGFUSE_HOST` when a CallbackHandler is wired
            on `app.state` (i.e. keys present + package importable). The UI
            reveals a header link to this URL so operators can jump straight
            to the trace view.
          - `None` when tracing is disabled — the UI keeps the link hidden.

        Returning the URL via /health means the UI never needs to hard-code
        a host: Langfuse Cloud users get the cloud URL; default-compose
        users get http://localhost:3000.
        """
        body: dict = {"status": "ok"}
        if getattr(request.app.state, "langfuse_callback", None) is not None:
            # `LANGFUSE_PUBLIC_URL` is the browser-facing URL. In default
            # compose this is `http://localhost:3000`; for Langfuse Cloud,
            # leave it empty and the cloud LANGFUSE_HOST is reused. The
            # docker-internal host (`http://langfuse-web:3000`) lives only
            # in LANGFUSE_HOST and is never returned to the browser.
            body["langfuse_url"] = (
                settings.LANGFUSE_PUBLIC_URL
                or settings.LANGFUSE_HOST
                or "http://localhost:3000"
            )
        else:
            body["langfuse_url"] = None
        return body

    @app.get("/reviews")
    async def list_reviews(request: Request) -> JSONResponse:
        """Most-recent reviews, paginated. Empty list when no store is wired
        (no DATABASE_URL → no persistence) so the UI can render the section
        as empty instead of erroring.

        Rows come back with native asyncpg types (`UUID`, `datetime`); use
        `jsonable_encoder` to coerce those into JSON-safe strings.
        """
        store: ReviewStore | None = getattr(request.app.state, "review_store", None)
        if store is None:
            return JSONResponse([])
        try:
            limit = _parse_positive_int(
                request.query_params.get("limit"),
                default=50,
                field="limit",
                min_value=1,
                max_value=MAX_REVIEWS_PAGE_SIZE,
            )
            offset = _parse_positive_int(
                request.query_params.get("offset"),
                default=0,
                field="offset",
                min_value=0,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        rows = await store.list_reviews(limit=limit, offset=offset)
        return JSONResponse(jsonable_encoder(rows), status_code=200)

    @app.get("/reviews/active")
    async def list_active_reviews(request: Request) -> JSONResponse:
        """In-flight reviews — for the UI's 'Active reviews' poll.

        Pure in-memory snapshot from ActiveReviewsRegistry, enriched
        per-entry with the live TokenUsageHandler totals so the panel
        can render an in-flight `X.Xk in / Y.Yk out` counter under
        the elapsed-time line. No DB query, no extra fetch — just
        reads the per-thread handler that's already accumulating
        token usage from every graph-node LLM call.
        """
        registry = request.app.state.active_reviews
        handlers = _get_token_handlers(request.app)
        items: list[dict] = []
        for state in registry.list_active():
            row = state.to_dict()
            handler = handlers.get(state.thread_id)
            row["tokens"] = handler.totals() if handler else {
                "input": 0, "output": 0, "calls": 0,
            }
            items.append(row)
        return JSONResponse(items, status_code=200)

    @app.post("/reviews/{thread_id}/cancel")
    async def cancel_active_review(request: Request, thread_id: str) -> JSONResponse:
        """Stop an in-flight review.

        Backing endpoint for the UI's per-review Stop button. The
        registry knows the underlying `asyncio.Task` (attached by the
        spawn path); cancellation is cooperative — the task receives
        `CancelledError` at the next `await` and runs its `finally:`
        block (snapshot cleanup, registry unregister, chat broadcast).

        Status codes:
          * 200 — cancellation request delivered to a running task.
          * 404 — unknown thread_id, no task attached yet, or task
            already finished. All three are "nothing to cancel" from
            the caller's perspective and don't need distinguishing.
        """
        registry = request.app.state.active_reviews
        if not registry.cancel(thread_id):
            raise HTTPException(status_code=404, detail="no cancellable review")
        return JSONResponse(
            {"status": "cancelled", "thread_id": thread_id},
            status_code=200,
        )

    @app.get("/reviews/{thread_id}")
    async def get_review(request: Request, thread_id: str) -> JSONResponse:
        """Single review + embedded findings. 404 when the row is missing
        OR the store isn't wired (no DB)."""
        store: ReviewStore | None = getattr(request.app.state, "review_store", None)
        if store is None:
            raise HTTPException(status_code=404, detail="review store not enabled")
        row = await store.get_review(thread_id)
        if row is None:
            raise HTTPException(status_code=404, detail="review not found")
        return JSONResponse(jsonable_encoder(row), status_code=200)

    @app.get("/reviews/{thread_id}/critical-findings")
    async def list_critical_findings(
        request: Request, thread_id: str,
    ) -> JSONResponse:
        """List critical findings for the UI's "Critical findings" panel.

        Joins `review_findings` (severity='critical' subset) with the
        review's existing `exploit_proposals` JSONB so each row carries
        its current `exploit_status` (null when no exploit was attempted
        yet) — the UI decides between rendering a "Create exploit" button
        and a "View existing" link based on that field.

        Returns 404 when the store isn't wired or the thread is unknown;
        returns 200 with [] when the thread exists but has zero critical
        findings.
        """
        store: ReviewStore | None = getattr(request.app.state, "review_store", None)
        if store is None:
            raise HTTPException(status_code=404, detail="review store not enabled")
        row = await store.get_review(thread_id)
        if row is None:
            raise HTTPException(status_code=404, detail="review not found")

        # Map finding_id → existing ExploitProposal payload (whatever
        # status it landed at). The UI cares about the last-known state,
        # not the full history.
        #
        # Defensive normalize: a buggy older write path stored some
        # entries as JSON-strings instead of dicts (double JSON encode).
        # Decode them on the fly so the endpoint stays callable until
        # the corrupted rows are overwritten by fresh saves.
        proposals_by_id = {
            p.get("finding_id"): p
            for p in _decode_exploit_proposals(row.get("exploit_proposals"))
        }
        from src.agents.exploit_proposal import compute_finding_id

        result: list[dict] = []
        for f in row.get("findings") or []:
            if (f.get("severity") or "").lower() != "critical":
                continue
            role = f.get("role") or ""
            fid = compute_finding_id(role, f)
            existing = proposals_by_id.get(fid)
            result.append({
                "finding_id": fid,
                "role": role,
                "severity": "critical",
                "file": f.get("file"),
                "line": f.get("line"),
                "issue": f.get("issue") or "",
                "exploit_status": existing.get("status") if existing else None,
                "confidence": existing.get("confidence") if existing else None,
                # Inline display fields — populated only when an exploit
                # was actually attempted. UI renders proposal_text +
                # artifact in a collapsible <details> block under the row
                # so the user can read the PoC without a second fetch.
                "proposal_text": existing.get("proposal_text") if existing else None,
                "artifact": existing.get("artifact") if existing else None,
            })
        # Stable display order so the UI doesn't shuffle rows between renders.
        result.sort(key=lambda r: (r["role"], r["file"] or "", r["line"] or 0))
        return JSONResponse(jsonable_encoder(result), status_code=200)

    @app.post("/reviews/{thread_id}/exploits/{finding_id}")
    async def create_exploit(
        request: Request, thread_id: str, finding_id: str,
    ) -> JSONResponse:
        """Generate an exploit PoC for one critical finding on demand.

        Replaces the old in-graph `process_proposal` interrupt loop. The
        UI's "Create exploit" button calls this endpoint; the response
        carries the persisted ExploitProposal (approved or
        skipped_low_confidence).

        Status codes:
          * 201 — created (or low-confidence skip persisted)
          * 200 — already created (idempotent return of existing record)
          * 400 — finding exists but is not critical severity
          * 404 — review or finding_id unknown / store not wired
          * 409 — cap (MAX_EXPLOIT_PROPOSALS=3) reached
        """
        from src.agents.exploit_proposal import (
            MAX_EXPLOIT_PROPOSALS,
            compute_finding_id,
        )

        store: ReviewStore | None = getattr(request.app.state, "review_store", None)
        if store is None:
            raise HTTPException(status_code=404, detail="review store not enabled")
        row = await store.get_review(thread_id)
        if row is None:
            raise HTTPException(status_code=404, detail="review not found")

        # Defensive decode — same rationale as `list_critical_findings`.
        existing_proposals = _decode_exploit_proposals(row.get("exploit_proposals"))

        # Idempotency — same finding_id was already processed. Return the
        # existing record verbatim. No LLM call, no second persist.
        for p in existing_proposals:
            if p.get("finding_id") == finding_id:
                return JSONResponse(jsonable_encoder(p), status_code=200)

        # Cap — count ALL proposals regardless of status (approved /
        # declined / skipped_*) so one review can't burn unbounded
        # LLM budget. The idempotency check above runs first so an
        # already-created finding can still be fetched after the cap
        # locked the rest.
        if len(existing_proposals) >= MAX_EXPLOIT_PROPOSALS:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"exploit cap reached "
                    f"({MAX_EXPLOIT_PROPOSALS} per review)"
                ),
            )

        # Locate the matching finding in the review's findings list. We
        # need the FULL finding dict (file, line, issue, severity) for
        # the agent's prompt, so we re-derive the finding_id from each
        # row instead of trusting an indexed lookup.
        matching = None
        matching_role = None
        for f in row.get("findings") or []:
            role = f.get("role") or ""
            if compute_finding_id(role, f) == finding_id:
                matching = f
                matching_role = role
                break
        if matching is None:
            raise HTTPException(status_code=404, detail="finding not found")
        if (matching.get("severity") or "").lower() != "critical":
            raise HTTPException(
                status_code=400,
                detail="finding is not critical severity",
            )

        agent = getattr(request.app.state, "exploit_agent", None)
        if agent is None:
            # Production lifespan wires this; tests inject via
            # app.state.exploit_agent. A None here means a misconfigured
            # deploy — treat as 503 so it's distinguishable from 404.
            raise HTTPException(
                status_code=503, detail="exploit agent not configured"
            )

        # Build the agent's input dict: original finding + role + finding_id.
        finding_for_agent = {
            **matching,
            "role": matching_role,
            "finding_id": finding_id,
        }
        # Per-request token-usage handler — attaches to the agent's two
        # LLM calls (draft + artifact) via the langchain RunnableConfig
        # callbacks list. The ContextVar pins the bucket name so the
        # accumulated usage lands under `exploit:<finding_id>` (unique
        # per click) rather than the generic `_unknown`.
        from src.utils.token_tracking import (
            TokenUsageHandler,
            _current_node,
            set_current_node,
        )

        token_handler = TokenUsageHandler()
        bucket_name = f"exploit:{finding_id}"
        cv_token = set_current_node(bucket_name)
        try:
            proposal = await agent.generate_exploit(
                finding_for_agent, save_mode="file", callbacks=[token_handler],
            )
        finally:
            _current_node.reset(cv_token)

        exploit_tokens = token_handler.usage.get(bucket_name, {
            "input": 0, "output": 0, "calls": 0, "models": [],
        })

        # Persist + re-render. Failures here are logged but do NOT undo
        # the LLM call — we still return the proposal so the user can
        # save its content manually if the DB write race-conditioned.
        proposal_dict = {
            "finding_id": proposal.finding_id,
            "role": proposal.role,
            "severity": proposal.severity,
            "status": proposal.status,
            "proposal_text": proposal.proposal_text,
            "artifact": proposal.artifact,
            "confidence": proposal.confidence,
            # Per-exploit token cost — surfaces in the UI's per-row
            # details so the operator can see "this PoC cost N tokens".
            "tokens": exploit_tokens,
        }
        try:
            await store.add_exploit_proposal(thread_id, proposal_dict)
        except Exception as exc:
            logger.error(
                "failed to persist exploit_proposal for %s/%s: %s",
                thread_id, finding_id, exc,
            )
        # Also fold the per-exploit usage into the review's top-level
        # token_usage bucket so the report summary table reflects ALL
        # costs (including post-publish on-demand PoCs).
        if exploit_tokens["calls"] > 0:
            try:
                await store.merge_token_usage_bucket(
                    thread_id, bucket_name, exploit_tokens,
                )
            except Exception as exc:
                logger.error(
                    "failed to update token_usage for %s/%s: %s",
                    thread_id, finding_id, exc,
                )

        # Write the sibling artifact file when the agent succeeded —
        # the UI's "View" link points at `/reports/<tid>.exploit.<fid>.md`.
        if proposal.status == "approved" and proposal.artifact:
            try:
                _write_exploit_artifact(
                    request.app.state.reports_dir,
                    thread_id, proposal,
                )
            except Exception as exc:
                logger.error(
                    "failed to write sibling exploit file for %s/%s: %s",
                    thread_id, finding_id, exc,
                )

        return JSONResponse(jsonable_encoder(proposal_dict), status_code=201)

    @app.post("/review")
    async def trigger_review(request: Request, background: BackgroundTasks) -> JSONResponse:
        body = await request.json()
        pr_url = body.get("pr_url")
        repo_url = body.get("repo_url")

        # Exactly one of pr_url / repo_url must be supplied.
        if not pr_url and not repo_url:
            return JSONResponse(
                {"error": "one of pr_url or repo_url is required"},
                status_code=400,
            )
        if pr_url and repo_url:
            return JSONResponse(
                {"error": "mixed payload: provide pr_url or repo_url, not both"},
                status_code=400,
            )

        scope_raw = body.get("scope", [])
        if not isinstance(scope_raw, list):
            return JSONResponse({"error": "scope must be a list of role names"}, status_code=400)

        invalid = [s for s in scope_raw if s not in ALLOWED_SCOPE_ROLES]
        if invalid:
            return JSONResponse(
                {
                    "error": f"invalid scope roles: {invalid}; allowed: {list(ALLOWED_SCOPE_ROLES)}",
                },
                status_code=400,
            )

        thread_id = str(uuid.uuid4())
        registry = request.app.state.active_reviews
        if pr_url:
            # Register BEFORE spawning so the Stop button has a target
            # even if the spawn-to-first-await window is non-zero.
            # `asyncio.create_task` (not BackgroundTasks) gives us a
            # handle we can hand to the registry for cancel().
            registry.register(thread_id, mode="pr", target=pr_url)
            task = asyncio.create_task(
                _run_review(request.app, pr_url, scope_raw, thread_id)
            )
            registry.attach_task(thread_id, task)
            return JSONResponse(
                {"status": "started", "thread_id": thread_id, "pr_url": pr_url},
                status_code=202,
            )

        # Canonicalize the repo URL up front: browser URLs commonly carry
        # `/tree/<ref>`, `/blob/<ref>/file`, trailing `.git`, or trailing
        # slash. Reject malformed input with 400 here instead of crashing
        # the BackgroundTask on `git clone`. If `/tree/<ref>` was present
        # and the caller didn't supply an explicit `ref`, use the
        # extracted one.
        try:
            canonical_repo_url, extracted_ref = normalize_repo_url(repo_url)
        except ValueError as e:
            return JSONResponse({"error": f"invalid repo_url: {e}"}, status_code=400)
        explicit_ref = body.get("ref")
        ref = (
            explicit_ref
            if explicit_ref and explicit_ref != "HEAD"
            else (extracted_ref or "HEAD")
        )

        registry.register(thread_id, mode="repo", target=canonical_repo_url, ref=ref)
        task = asyncio.create_task(
            _run_repo_review(
                request.app, canonical_repo_url, ref, scope_raw, thread_id,
            )
        )
        registry.attach_task(thread_id, task)
        return JSONResponse(
            {
                "status": "started",
                "thread_id": thread_id,
                "repo_url": canonical_repo_url,
                "ref": ref,
            },
            status_code=202,
        )

    @app.get("/reports/{filename}")
    async def get_report(request: Request, filename: str) -> PlainTextResponse:
        """Serve a repo-mode review report saved as `<thread_id>.md`.

        The directory is configured on `app.state.reports_dir` (production
        reads `settings.REPORTS_DIR`; tests inject `tmp_path`). Returns 404
        when the file is absent rather than echoing the requested name into
        an error body, to avoid leaking arbitrary paths.
        """
        reports_dir: Path = request.app.state.reports_dir
        # Reject any path-traversal attempt — only flat filenames are valid.
        if "/" in filename or "\\" in filename or filename.startswith("."):
            raise HTTPException(status_code=400, detail="invalid filename")
        report_path = reports_dir / filename
        # Defence-in-depth: even with the basename guard above, a
        # symlink INSIDE reports_dir pointing OUT of it would let
        # `is_file()` traverse and `read_text` leak arbitrary files
        # the app process can read. `resolve()` walks symlinks; we
        # require the resolved target to still sit under reports_dir.
        try:
            resolved = report_path.resolve()
            root = reports_dir.resolve()
        except OSError as exc:
            logger.warning("resolve failed for %s: %s", report_path, exc)
            raise HTTPException(status_code=404, detail="report not found") from exc
        if not resolved.is_relative_to(root):
            logger.warning(
                "report path %r resolves outside reports_dir %r — refusing",
                str(resolved), str(root),
            )
            raise HTTPException(status_code=404, detail="report not found")
        if not resolved.is_file():
            raise HTTPException(status_code=404, detail="report not found")
        return PlainTextResponse(resolved.read_text(), media_type="text/markdown")

    @app.get("/chat/{thread_id}/history")
    async def chat_history(thread_id: str, request: Request) -> JSONResponse:
        store: ChatStore = request.app.state.chat_store
        messages = [m.to_dict() for m in store.get_history(thread_id)]
        return JSONResponse(messages)

    @app.websocket("/ws/chat/{thread_id}")
    async def chat_ws(websocket: WebSocket, thread_id: str) -> None:
        # Browser CSRF guard. The `Origin` header is set by every browser
        # on the WS handshake; non-browser clients (CLI, websockets lib,
        # the TestClient by default) omit it. We refuse browser origins
        # that aren't in the configured allowlist, and let missing-Origin
        # pass — the threat model is a malicious site loaded in the
        # USER'S browser, not arbitrary clients on the internal network.
        # Empty `settings.WS_ALLOWED_ORIGINS` disables the check entirely.
        origin = websocket.headers.get("origin")
        if origin is not None and not _ws_origin_is_allowed(origin):
            logger.warning(
                "rejecting WS connection from origin %r (thread_id=%s)",
                origin, thread_id,
            )
            # 1008 = "Policy Violation" per RFC 6455. Browsers surface
            # this as a clean close to the client without crash.
            await websocket.close(code=1008)
            return
        await websocket.accept()
        store: ChatStore = websocket.app.state.chat_store
        hub: ChatHub = websocket.app.state.chat_hub
        app_ref = websocket.app

        await hub.connect(thread_id, websocket)

        # Replay stored progress events first so the workflow diagram in
        # the UI catches up to whatever stage the review has reached. Then
        # the chat history (so messages render in chronological order).
        progress_store: ProgressStore = websocket.app.state.progress_store
        for event in progress_store.get(thread_id):
            await websocket.send_json(event)
        for msg in store.get_history(thread_id):
            await websocket.send_json(msg.to_dict())

        try:
            while True:
                data = await websocket.receive_json()
                text = (data.get("text") or "").strip()
                if not text:
                    continue
                message = ChatMessage(role="user", text=text)
                store.append(thread_id, message)
                await hub.broadcast(thread_id, message.to_dict())

                # Phase B — if the graph is paused at an interrupt for this
                # thread, treat the user's message as the resume payload
                # instead of just chat noise. Resume runs in a background
                # task so the WS loop stays responsive.
                if await _has_pending_interrupt(app_ref, thread_id):
                    asyncio.create_task(_resume_review(app_ref, thread_id, text))
        except WebSocketDisconnect:
            await hub.disconnect(thread_id, websocket)


def _decode_exploit_proposals(raw) -> list[dict]:
    """Normalise the `reviews.exploit_proposals` JSONB column to a clean
    `list[dict]` for endpoint consumption.

    Historically a buggy `add_exploit_proposal` write path passed an
    already-JSON-encoded string to asyncpg whose registered JSONB codec
    then ran `json.dumps` AGAIN, storing a double-encoded value. The
    corrupted shape that landed in real production rows is:

        exploit_proposals = [ '[{"finding_id": "...", ...}]',  <real-dict>, ... ]

    where the FIRST element is a JSON-string of a JSON-array of dicts.
    This walks both that shape and any single/double-nested string-of-
    string-of-dict shape, unwrapping each layer until it bottoms out
    at a dict, then flattens the result so the caller gets a clean
    `list[dict]`.

    Returns `[]` on any decode failure so the endpoint stays callable
    against corrupted rows; broken entries are simply invisible until
    they get overwritten by fresh saves.
    """
    import json as _json

    def _unwrap(value):
        """Strip up to 3 layers of accidental double-encoding. Returns
        a dict, a list, or None when nothing salvageable."""
        for _ in range(3):
            if isinstance(value, (dict, list)):
                return value
            if isinstance(value, str):
                try:
                    value = _json.loads(value)
                    continue
                except (ValueError, TypeError):
                    return None
            return None
        return value if isinstance(value, (dict, list)) else None

    unwrapped = _unwrap(raw)
    if not isinstance(unwrapped, list):
        return []
    out: list[dict] = []
    for item in unwrapped:
        decoded = _unwrap(item)
        if isinstance(decoded, dict):
            out.append(decoded)
        elif isinstance(decoded, list):
            # Nested array (e.g. corrupted row where the whole proposal
            # was wrapped in an outer array): flatten one level.
            for sub in decoded:
                sub_decoded = _unwrap(sub)
                if isinstance(sub_decoded, dict):
                    out.append(sub_decoded)
    return out


def _write_exploit_artifact(reports_dir: Path, thread_id: str, proposal) -> None:
    """Write a sibling `<thread_id>.exploit.<finding_id>.md` next to the
    main review report. The UI's "View" link points at this file.

    Defensive: skip silently when `thread_id` is empty (matches
    CoordinatorAgent._write_repo_report's policy — no synthetic filenames)
    so a misconfigured request can't leak `unknown.exploit.…md` files.
    """
    from src.agents.report_renderer import ReportRenderer

    if not thread_id or not proposal.finding_id:
        return
    filename = ReportRenderer.exploit_artifact_filename(thread_id, proposal.finding_id)
    body = ReportRenderer().render_exploit_sibling(proposal, thread_id=thread_id)
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / filename).write_text(body, encoding="utf-8")


def _parse_positive_int(
    raw: str | None,
    *,
    default: int,
    field: str,
    min_value: int = 0,
    max_value: int | None = None,
) -> int:
    """Parse a query-param int, clamp to [min_value, max_value].

    Raises `ValueError` with a user-facing message on:
      - non-numeric input,
      - negative input (when min_value=0) or below `min_value`.

    Above `max_value` we DON'T raise — we clamp. Pagination is a
    convenience, not a contract; sending a too-large limit is an
    obvious typo / curiosity, not a malformed request. Clamping
    transparently is friendlier than 400, the user just gets fewer
    rows than they asked for.

    `None` raw → `default` (no validation needed).
    """
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field!r} must be an integer (got {raw!r})") from exc
    if v < min_value:
        raise ValueError(f"{field!r} must be >= {min_value} (got {v})")
    if max_value is not None and v > max_value:
        return max_value
    return v


def _ws_origin_is_allowed(origin: str) -> bool:
    """True iff `origin` matches `settings.WS_ALLOWED_ORIGINS` (case-
    insensitive). Empty allowlist → allow all (escape hatch for ops).

    The origin comparison is done by exact string match. We don't try
    to canonicalise port-only differences or scheme casing here because
    the field validator on `Settings` already lower-cased the allowlist
    entries, and the spec defines Origin matching as exact-string.
    """
    allowed = settings.WS_ALLOWED_ORIGINS or []
    if not allowed:
        return True  # check disabled
    return origin.lower() in allowed


def _trace_config(app: FastAPI, thread_id: str, state: ReviewState | None, trigger: str) -> dict:
    """Build the LangGraph invocation config.

    Attaches two callbacks:
      * Langfuse tracing (when a CallbackHandler is wired on
        `app.state.langfuse_callback`),
      * a fresh `TokenUsageHandler` per request — the orchestrator drains
        its accumulated usage into `state.token_usage` after the graph
        completes (and after every `_resume_review` for the human-in-the-
        loop path).

    The thread_id doubles as the Langfuse `session_id` so the chat-side
    interrupts/resumes appear under the same Langfuse session as the
    initial review run. We stash the per-request token handler on
    `app.state.token_handlers[thread_id]` so the orchestrator can read
    it back from any of the three entry points (`/review` → run,
    chat WS → resume, `/exploits/<fid>` → on-demand).
    """
    from src.utils.token_tracking import TokenUsageHandler

    config: dict = {"configurable": {"thread_id": thread_id}}
    callbacks: list = []

    langfuse_handler = getattr(app.state, "langfuse_callback", None)
    if langfuse_handler is not None:
        callbacks.append(langfuse_handler)

    # Per-request token-usage handler. Reuse an existing one on this
    # thread_id when there is one (e.g. resume-after-interrupt) so the
    # totals span the whole run instead of resetting per resume.
    token_handlers = _get_token_handlers(app)
    token_handler = token_handlers.get(thread_id)
    if token_handler is None:
        token_handler = TokenUsageHandler()
        token_handlers[thread_id] = token_handler
    callbacks.append(token_handler)

    if callbacks:
        config["callbacks"] = callbacks

    if langfuse_handler is not None:
        metadata: dict = {
            "session_id": thread_id,
            "tags": ["security-review", trigger],
        }
        if state is not None and state.request is not None:
            metadata["user_id"] = state.request.author
            metadata["pr_url"] = state.request.pr_url
        config["metadata"] = metadata
    return config


def _get_token_handlers(app: FastAPI) -> dict:
    """Lazy-init the per-thread `TokenUsageHandler` registry. Tests can
    construct an app without going through the lifespan, so we can't
    assume the attribute already exists. We also defensively type-check
    — on MagicMock `app.state` objects the attribute auto-creates as a
    MagicMock, which would silently break get/setitem semantics."""
    handlers = getattr(app.state, "token_handlers", None)
    if not isinstance(handlers, dict):
        handlers = {}
        app.state.token_handlers = handlers
    return handlers


def _drain_token_usage(app: FastAPI, thread_id: str, state: ReviewState) -> None:
    """Move accumulated LLM usage off the per-thread handler onto
    `state.token_usage`, then drop the handler so a fresh per-thread
    handler starts the next run clean. Called after every successful
    graph drain (initial `_run_review` / `_run_repo_review` AND after
    each `_resume_review` pass)."""
    handlers = _get_token_handlers(app)
    handler = handlers.pop(thread_id, None)
    if handler is None:
        return
    # Merge into whatever is already on the state (resume paths land
    # here after the initial drain has already populated state).
    merged = dict(state.token_usage or {})
    for node, bucket in handler.usage.items():
        existing = merged.get(node)
        if existing is None:
            merged[node] = dict(bucket)
            merged[node]["models"] = list(bucket.get("models") or [])
            continue
        existing["input"] = (existing.get("input") or 0) + bucket.get("input", 0)
        existing["output"] = (existing.get("output") or 0) + bucket.get("output", 0)
        existing["calls"] = (existing.get("calls") or 0) + bucket.get("calls", 0)
        models = list(existing.get("models") or [])
        for m in bucket.get("models") or []:
            if m not in models:
                models.append(m)
        existing["models"] = models
    state.token_usage = merged


async def _run_review(app: FastAPI, pr_url: str, scope: list[str], thread_id: str) -> None:
    """Fetch the PR, build initial state, and run the security review graph.

    Spawned via `asyncio.create_task` from the `/review` endpoint so the
    caller can hold the task handle in `ActiveReviewsRegistry` for
    cooperative cancellation (Stop button). Registration into the
    tracker is done up-front in the endpoint — this coroutine only owns
    the unregister-on-exit half of the lifecycle.

    Uses `astream(mode="updates")` so progress envelopes can be broadcast to
    the chat WebSocket on every node completion (the UI lights up its
    workflow-diagram circles based on these).

    If the graph pauses at an `interrupt()` (Phase B human-in-the-loop), the
    pending question(s) are surfaced into the chat panel — the user then
    answers via the WebSocket and `_resume_review` carries the resume back
    to the graph.

    On `CancelledError` (Stop button), we broadcast a brief notice into
    the chat so the user sees the stop took effect, then let
    `finally:` run the registry cleanup. Persist is skipped — a
    half-finished state isn't worth the audit row.
    """
    try:
        review_request = await app.state.github.fetch_pr(pr_url)
        review_request.scope = scope
        state = ReviewState(request=review_request, thread_id=thread_id)
        config = _trace_config(app, thread_id, state, trigger="http")
        await _stream_graph_with_progress(app, thread_id, state, config)
        await _broadcast_pending_interrupts(app, thread_id)
        # Move accumulated LLM token usage off the per-thread handler
        # onto `state.token_usage` BEFORE persisting — the DB row needs
        # it. The handler is also dropped here so a long-running app
        # doesn't accumulate orphaned handlers across thread_ids.
        _drain_token_usage(app, thread_id, state)
        await _persist_review(app, state)
    except asyncio.CancelledError:
        logger.info("review %s cancelled by user", thread_id)
        await _broadcast_cancellation(app, thread_id)
        # Do NOT re-raise: this coroutine IS the cancelled task, and
        # swallowing here lets `finally:` run unregister cleanly.
    except Exception as e:
        logger.error("review failed for %s: %s", pr_url, e)
    finally:
        app.state.active_reviews.unregister(thread_id)


async def _run_repo_review(
    app: FastAPI,
    repo_url: str,
    ref: str,
    scope: list[str],
    thread_id: str,
) -> None:
    """Run a whole-repo security review for `repo_url@ref`.

    Orchestrates:
      1. Fetch the repo tree (`github.fetch_tree`) → populate `repo_files`.
         The validator and per-specialist reviewers operate on this list.
      2. Run the graph with `state.thread_id` set so CoordinatorAgent can
         write `reports/<thread_id>.md` in publish.
      3. Surface any interrupts to chat (same Phase B/C path as PR review).
      4. After completion, broadcast a brief 'review complete' chip pointing
         at the saved report.
    """
    snapshot_dir = None
    progress_log_task: asyncio.Task | None = None
    # NB: register(...) is done in the `/review` endpoint before the
    # task is spawned, so the Stop button can address us from the very
    # first WS poll. This coroutine only owns the unregister-on-exit
    # half of the lifecycle.
    # Bind a ProgressEmitter for this review's thread_id and install it
    # into the contextvar so per-file LLM iteration inside the reviewer
    # agents (LLMPerFileReviewer._run_repo) can emit `file_progress`
    # envelopes to BOTH the persistent ProgressStore (UI replay on
    # connect) AND the live ChatHub (live UI updates). Same emitter
    # backs the periodic_log_task below.
    emitter = ProgressEmitter(
        thread_id=thread_id,
        store=app.state.progress_store,
        hub=app.state.chat_hub,
    )
    try:
        # Local shallow-clone instead of GitHub REST tree/blob fetches —
        # avoids the 60 req/h anonymous rate limit on bigger repos.
        # Surround the clone with progress envelopes so the UI can spin
        # the `clone_repo` workflow circle while the subprocess runs and
        # turn it green when it finishes.
        await _emit_progress(app, thread_id, "clone_repo", "active")
        snapshot_dir = await asyncio.to_thread(clone_repo, repo_url, ref)
        await _emit_progress(app, thread_id, "clone_repo", "fired")
        repo_files = list_repo_files(snapshot_dir)
        review_request = ReviewRequest(
            mode="repo",
            repo_url=repo_url,
            ref=ref,
            snapshot_dir=str(snapshot_dir),
            repo_files=repo_files,
            scope=scope,
        )
        state = ReviewState(request=review_request, thread_id=thread_id)
        config = _trace_config(app, thread_id, state, trigger="http_repo")
        # Periodic snapshot logger — one INFO line per minute on
        # `graph.progress` logger summarising every active reviewer's
        # done/total counts. See src/chat/progress_log.py for shape.
        progress_log_task = asyncio.create_task(
            progress_log_loop(thread_id, app.state.progress_store)
        )
        with use_emitter(emitter):
            await _stream_graph_with_progress(app, thread_id, state, config)
        await _broadcast_pending_interrupts(app, thread_id)
        await _broadcast_repo_completion(app, thread_id)
        _drain_token_usage(app, thread_id, state)
        await _persist_review(app, state)
    except asyncio.CancelledError:
        logger.info("repo review %s cancelled by user", thread_id)
        await _broadcast_cancellation(app, thread_id)
        # Do NOT re-raise — see _run_review for the same pattern.
        # Snapshot cleanup happens in `finally:` regardless.
    except Exception as e:
        logger.error("repo review failed for %s@%s: %s", repo_url, ref, e)
    finally:
        if progress_log_task is not None:
            progress_log_task.cancel()
            # Drain the cancel so the final-snapshot log line lands
            # (the loop catches CancelledError to flush before exiting).
            with contextlib.suppress(asyncio.CancelledError):
                await progress_log_task
        if snapshot_dir is not None:
            cleanup_snapshot(snapshot_dir)
        app.state.active_reviews.unregister(thread_id)


async def _persist_review(app: FastAPI, state: ReviewState) -> None:
    """Write the finalized review state into Postgres if a store is wired.

    Silently no-op when `app.state.review_store is None` (no DATABASE_URL,
    or init failed during lifespan). Any DB error is logged but does NOT
    propagate — persistence is an audit side-effect, not the primary
    deliverable (the markdown report is already on disk by this point).
    """
    store = getattr(app.state, "review_store", None)
    if store is None:
        return
    try:
        await store.save_review(state)
    except Exception as e:
        logger.error("ReviewStore.save_review failed: %s", e)
        return

    # RAG backfill — embed any unembedded findings (including the ones we
    # just inserted) so the next review on this repo can retrieve them.
    # Fire-and-forget: a missing embedder or a partial failure must not
    # delay the HTTP response or block the chat broadcast.
    embedder = getattr(app.state, "embedder", None)
    if embedder is not None:
        try:
            count = await store.embed_pending(embedder)
            if count:
                logger.info("RAG backfill: embedded %d new finding(s)", count)
        except Exception as e:
            logger.warning("RAG backfill failed (non-fatal): %s", e)


async def _emit_progress(app: FastAPI, thread_id: str, node: str, status: str) -> None:
    """Send a single progress envelope through both the persistent store
    and the live WebSocket broadcast. Used for pre-graph "active" markers
    (e.g. clone_repo) where the timing is owned by the orchestrator rather
    than LangGraph's astream chunks."""
    event = {"type": "progress", "node": node, "status": status}
    app.state.progress_store.append(thread_id, event)
    await app.state.chat_hub.broadcast(thread_id, event)


def _classify_progress(node_name: str, update) -> str:
    """Decide how the UI should colour the workflow circle for this node.

    - `notify_rejection` always means the review was rejected → red.
    - A node that returned `{}` (or a non-dict no-op) didn't produce any
      meaningful work this run — scope-skipped specialist or empty
      formatter → gray.
    - Anything else is a real, fired-with-work step → green.
    """
    if node_name == "notify_rejection":
        return "rejected"
    if not isinstance(update, dict) or not update:
        return "empty"
    return "fired"


def _merge_chunk_into_state(state: ReviewState, update: dict) -> None:
    """Apply a single LangGraph chunk update to our local accumulator.

    LangGraph's astream(mode="updates") yields per-node partial updates;
    we mirror its merge semantics here so we have a finalized state to
    hand to ReviewStore.save_review once the stream completes.

    `agent_reviews` and `exploit_proposals` carry an `add` reducer ─
    concatenate. Everything else is last-write-wins (replace).
    """
    for key, value in update.items():
        if key in ("agent_reviews", "exploit_proposals"):
            current = getattr(state, key, []) or []
            setattr(state, key, list(current) + list(value or []))
        elif hasattr(state, key):
            setattr(state, key, value)


async def _stream_graph_with_progress(app: FastAPI, thread_id: str, state, config: dict) -> None:
    """Drive `graph.astream` and broadcast a progress envelope per node.

    Each chunk yielded by LangGraph in default streaming mode is a dict
    keyed by node name → that node's update. For every key we
      1. classify the status (fired / empty / rejected) so the UI can
         choose the right colour;
      2. record the event in `app.state.progress_store` so a late WS
         client can replay the workflow state on connect (the BackgroundTask
         starts immediately after the 202, so early events are lost otherwise);
      3. broadcast the envelope live to any sockets already connected for
         this thread.
    A terminal `{"type": "progress", "node": "__done__"}` is emitted after
    the stream ends — the UI uses it to gray out circles whose nodes never
    fired at all (lone validator-accept path leaves notify_rejection cold,
    etc.).
    """
    async for chunk in app.state.graph.astream(state, config=config):
        if not isinstance(chunk, dict):
            continue
        for node_name, update in chunk.items():
            # Mirror the graph's merge into our local state so we have a
            # finalized snapshot ready for ReviewStore.save_review when
            # the stream ends. (LangGraph maintains its own internal copy
            # — this one is for us.)
            if isinstance(update, dict):
                _merge_chunk_into_state(state, update)
            # Server-side trace of every node completion. Lets us correlate
            # WS progress envelopes with server-side activity when debugging
            # "report missing" — if publish_report never appears here, we
            # know the graph never reached it.
            update_keys = list(update.keys()) if isinstance(update, dict) else None
            logger.info("graph node %s done; update keys=%s", node_name, update_keys)
            event = {
                "type": "progress",
                "node": node_name,
                "status": _classify_progress(node_name, update),
            }
            # `validate_request` is the accept/reject branch point — surface
            # the verdict so the UI can resolve `notify_rejection` (and the
            # reviewers / aggregate / publish chain) to their final colour
            # immediately, instead of leaving the wrong side spinning.
            if node_name == "validate_request" and isinstance(update, dict):
                verdict = update.get("validation")
                accepted = getattr(verdict, "accepted", None)
                if accepted is not None:
                    event["accepted"] = bool(accepted)
            app.state.progress_store.append(thread_id, event)
            await app.state.chat_hub.broadcast(thread_id, event)
    done = {"type": "progress", "node": "__done__"}
    app.state.progress_store.append(thread_id, done)
    await app.state.chat_hub.broadcast(thread_id, done)


async def _broadcast_cancellation(app: FastAPI, thread_id: str) -> None:
    """Push a short notice into the chat when the Stop button cancelled
    this review. Also emits a workflow-level `__cancelled__` progress
    envelope so the UI can flip its workflow chips to a neutral state
    (mirrors `__done__` for completions).

    Best-effort: any error broadcasting here must NOT mask the
    underlying `CancelledError` flow. Caller handles all exceptions.
    """
    try:
        msg = ChatMessage(role="agent", text="Review cancelled by user.")
        app.state.chat_store.append(thread_id, msg)
        await app.state.chat_hub.broadcast(thread_id, msg.to_dict())
        cancelled_event = {"type": "progress", "node": "__cancelled__"}
        app.state.progress_store.append(thread_id, cancelled_event)
        await app.state.chat_hub.broadcast(thread_id, cancelled_event)
    except Exception as e:
        logger.warning("cancellation broadcast for %s failed: %s", thread_id, e)


async def _broadcast_repo_completion(app: FastAPI, thread_id: str) -> None:
    """Push a short 'review complete' message into the chat with the report URL.

    Skipped when the graph is still paused at an interrupt — in that case the
    interrupt question is already in chat and the completion notice would
    confuse the user. Once the human answers the last interrupt, the resume
    path runs this again on the final pass.
    """
    config = {"configurable": {"thread_id": thread_id}}
    try:
        snapshot = await app.state.graph.aget_state(config)
        if snapshot.tasks and any(t.interrupts for t in snapshot.tasks):
            return
    except ValueError:
        # No checkpointer → no persistent task list. Proceed with the
        # completion broadcast; if a real interrupt fired without a
        # checkpointer it would have errored earlier anyway.
        pass
    msg = ChatMessage(
        role="agent",
        text=f"Review complete. Full report: /reports/{thread_id}.md",
    )
    app.state.chat_store.append(thread_id, msg)
    await app.state.chat_hub.broadcast(thread_id, msg.to_dict())


async def _has_pending_interrupt(app: FastAPI, thread_id: str) -> bool:
    config = {"configurable": {"thread_id": thread_id}}
    try:
        snapshot = await app.state.graph.aget_state(config)
    except ValueError:
        # `aget_state` raises "No checkpointer set" when the graph is
        # compiled without one (e.g. local demo with empty DATABASE_URL).
        # Treat that as 'no pending interrupts'.
        return False
    return bool(snapshot.tasks and any(t.interrupts for t in snapshot.tasks))


async def _broadcast_pending_interrupts(app: FastAPI, thread_id: str) -> None:
    """Push every pending interrupt's `question` into the chat panel.

    Today the only interrupt kind is `review_clarification` (Phase B —
    the validator asks the human to confirm before re-running the
    reviewers). Exploit-approval interrupts were removed when the
    exploit feature moved to an on-demand HTTP endpoint.
    """
    config = {"configurable": {"thread_id": thread_id}}
    try:
        snapshot = await app.state.graph.aget_state(config)
    except ValueError:
        # No checkpointer compiled in — interrupts are not persistent and
        # there's nothing to retrieve. Quietly no-op.
        return
    if not snapshot.tasks:
        return
    for task in snapshot.tasks:
        for intr in task.interrupts:
            payload = intr.value if isinstance(intr.value, dict) else {"question": str(intr.value)}
            question = payload.get("question") or str(payload)
            msg = ChatMessage(role="agent", text=question)
            app.state.chat_store.append(thread_id, msg)
            await app.state.chat_hub.broadcast(thread_id, msg.to_dict())


async def _resume_review(app: FastAPI, thread_id: str, user_text: str) -> None:
    """Feed the user's message back into the paused graph via `Command(resume=...)`.

    After the resume, the graph may either complete (publish_report runs) or
    hit another interrupt — in which case we broadcast that next question too.
    """
    config = _trace_config(app, thread_id, state=None, trigger="resume")
    try:
        await app.state.graph.ainvoke(Command(resume=user_text), config=config)
        await _broadcast_pending_interrupts(app, thread_id)
        # Persist whatever `exploit_proposals` / cycle state the resume
        # accumulated. Without this the DB row stays at the snapshot
        # taken right after publish_report — which never had any
        # exploit decisions yet. Read final state from the checkpointer
        # since the local accumulator from the initial run is gone.
        await _persist_state_from_checkpointer(app, thread_id)
    except Exception as e:
        logger.error("resume failed for %s: %s", thread_id, e)


async def _persist_state_from_checkpointer(app: FastAPI, thread_id: str) -> None:
    """Reload the graph's current state from the checkpointer and upsert
    the review row. Called after `_resume_review` so any state mutated
    during the human-in-the-loop pass gets persisted to the DB.
    """
    if getattr(app.state, "review_store", None) is None:
        return
    try:
        snapshot = await app.state.graph.aget_state(
            {"configurable": {"thread_id": thread_id}}
        )
    except ValueError:
        # No checkpointer wired — nothing to read.
        return
    values = getattr(snapshot, "values", None)
    if not values:
        return
    # `values` is a dict view of the dataclass state — reconstruct as
    # ReviewState so the helpers in ReviewStore see the expected shape.
    reconstructed = ReviewState(**values) if isinstance(values, dict) else values
    # Fold in any LLM usage accumulated during the resume into the
    # reconstructed state's token_usage before persisting (the per-thread
    # TokenUsageHandler survives across the initial run and every resume
    # so the totals are cumulative, not per-pass).
    _drain_token_usage(app, thread_id, reconstructed)
    await _persist_review(app, reconstructed)


def create_test_app(
    *,
    graph,
    github=None,
    store: ChatStore | None = None,
    hub: ChatHub | None = None,
    langfuse_callback=None,
    reports_dir: Path | None = None,
    progress_store: ProgressStore | None = None,
    active_reviews: ActiveReviewsRegistry | None = None,
    exploit_agent=None,
) -> FastAPI:
    """Build a test-mode FastAPI app with pre-injected dependencies.

    Tests construct mocks for `graph` (and optionally `github`) and pass
    them here. No lifespan is registered, so settings/validation/Postgres
    don't activate. `store`, `hub`, `langfuse_callback`, `reports_dir`,
    `progress_store`, `active_reviews`, and `exploit_agent` default to
    disabled/fresh. POST /reviews/{tid}/exploits/{fid} tests overwrite
    `app.state.exploit_agent` directly, so the default `None` here is
    fine — the endpoint surfaces a 503 when it's missing.
    """
    app = FastAPI(title="ia-reviewer-test")
    app.state.graph = graph
    app.state.github = github
    app.state.chat_store = store or ChatStore()
    app.state.chat_hub = hub or ChatHub()
    app.state.progress_store = progress_store or ProgressStore()
    app.state.active_reviews = active_reviews or ActiveReviewsRegistry()
    app.state.langfuse_callback = langfuse_callback
    app.state.reports_dir = reports_dir or Path(settings.REPORTS_DIR)
    app.state.exploit_agent = exploit_agent
    _register_routes(app)
    return app


def _maybe_build_embedder() -> Embedder | None:
    """Build an Embedder when EMBEDDING_MODEL is configured; else None.

    Falls back to AI_GATEWAY_URL/API_KEY when the EMBEDDING_API_URL /
    EMBEDDING_API_KEY are blank — single-box setups don't need to
    duplicate creds. Empty model = RAG disabled (no instance created).
    """
    if not settings.EMBEDDING_MODEL:
        return None
    base_url = settings.EMBEDDING_API_URL or settings.AI_GATEWAY_URL
    api_key = settings.EMBEDDING_API_KEY or settings.AI_GATEWAY_API_KEY
    return Embedder(
        base_url=base_url,
        model=settings.EMBEDDING_MODEL,
        api_key=api_key,
        dim=settings.EMBEDDING_DIM,
    )


def _build_app() -> FastAPI:
    """Production app factory. Registers a lifespan that constructs the real
    GitHubClient, opens a PostgresSaver if DATABASE_URL is set, and compiles
    the LangGraph review graph against those dependencies."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.validate_required()
        # Add timestamps to uvicorn's own lifecycle/access log lines so they
        # interleave cleanly with our timestamped app logs.
        _normalize_uvicorn_logging()
        github = GitHubClient()
        reports_dir = Path(settings.REPORTS_DIR)
        # Pre-create the snapshots dir so the first `git clone` doesn't
        # race on it; placed inside the project so it's visible under
        # Docker (mountable via volume) and not lost when /tmp is wiped.
        Path(settings.SNAPSHOTS_DIR).mkdir(parents=True, exist_ok=True)
        coordinator = CoordinatorAgent(github=github, reports_dir=reports_dir)
        # RAG embedder — built only when EMBEDDING_MODEL is configured.
        # The agent is constructed lazily so build_review_graph stays
        # checkpointer-aware ordering (graph compile must still happen
        # inside the checkpointer context manager).
        embedder = _maybe_build_embedder()
        app.state.embedder = embedder
        async with open_checkpointer(settings.DATABASE_URL) as checkpointer:
            # past_context will be re-bound below once we know whether
            # the ReviewStore opened cleanly; meanwhile build the graph
            # with a None-deps PastContextAgent so the topology is fixed.
            past_context_agent = PastContextAgent(
                embedder=embedder, store=None, top_k=settings.RAG_TOP_K
            )
            app.state.graph = build_review_graph(
                coordinator=coordinator,
                checkpointer=checkpointer,
                past_context=past_context_agent,
            )
            app.state.github = github
            # Bounded in-memory stores — see settings.IN_MEMORY_STORE_MAX_THREADS.
            # Set to 0 to disable the cap (escape hatch for short-lived
            # smoke tests that don't care about memory). The setting flows
            # ONLY to production-built app; `create_test_app` keeps an
            # unbounded default for back-compat with the test suite.
            cap = settings.IN_MEMORY_STORE_MAX_THREADS
            app.state.chat_store = ChatStore(max_threads=cap)
            app.state.chat_hub = ChatHub()
            app.state.progress_store = ProgressStore(max_threads=cap)
            app.state.active_reviews = ActiveReviewsRegistry()
            app.state.reports_dir = reports_dir
            app.state.langfuse_callback = get_langfuse_callback()
            # Exploit agent powers POST /reviews/{tid}/exploits/{fid}.
            # Constructed once at startup so the per-request handler doesn't
            # pay model-factory init cost. No state on the instance — safe
            # to share across concurrent requests.
            from src.agents.exploit_proposal import ExploitProposalAgent
            app.state.exploit_agent = ExploitProposalAgent()
            # Open the ReviewStore against the same Postgres if available.
            # The checkpointer already proves the DSN is reachable.
            app.state.review_store = None
            if settings.DATABASE_URL:
                try:
                    app.state.review_store = await ReviewStore.create(settings.DATABASE_URL)
                    logger.info("ReviewStore connected — persisting reviews to DB")
                    # Late-bind the store on the PastContextAgent now that
                    # it's open. The graph still references the same agent
                    # instance, so attribute mutation propagates.
                    past_context_agent.store = app.state.review_store
                except Exception as e:
                    logger.warning("ReviewStore disabled (init failed): %s", e)
            else:
                logger.info("ReviewStore disabled (DATABASE_URL empty)")
            # RAG status log so operators see at a glance whether it's live.
            if embedder is None:
                logger.info("RAG disabled (EMBEDDING_MODEL not set)")
            elif app.state.review_store is None:
                logger.info("RAG disabled (no ReviewStore — DATABASE_URL empty or open failed)")
            else:
                logger.info(
                    "RAG enabled: model=%r dim=%d top_k=%d",
                    settings.EMBEDDING_MODEL,
                    settings.EMBEDDING_DIM,
                    settings.RAG_TOP_K,
                )
            if app.state.langfuse_callback is None:
                logger.info("Langfuse tracing disabled (LANGFUSE_* keys not set)")
            else:
                logger.info("Langfuse tracing enabled")
            try:
                yield
            finally:
                if app.state.review_store is not None:
                    try:
                        await app.state.review_store.aclose()
                    except Exception as e:
                        logger.warning("ReviewStore aclose failed: %s", e)
                if app.state.embedder is not None:
                    try:
                        await app.state.embedder.aclose()
                    except Exception as e:
                        logger.warning("Embedder aclose failed: %s", e)
                await github.aclose()

    app = FastAPI(title="ia-reviewer", version="0.1.0", lifespan=lifespan)
    _register_routes(app)
    return app


app = _build_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
