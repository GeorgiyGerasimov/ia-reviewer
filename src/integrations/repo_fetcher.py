"""Clone a GitHub repository into a temporary directory and walk its files.

Replaces the GitHub REST tree+blob calls that the whole-repo reviewer used
to make (one HTTP request per file → 60 req/h anonymous rate limit). A
single `git clone --depth=1 --branch=<ref>` is enough; every subsequent
file read is just local filesystem access.

Public surface:
  - `clone_repo(repo_url, ref) -> Path`  — shallow clone, returns dest dir
  - `list_repo_files(snapshot_dir)`       — walk and produce `RepoFile`s
  - `cleanup_snapshot(snapshot_dir)`      — `shutil.rmtree` (ignore errors)

git is a runtime dependency. The app's lifespan logs a warning if it's
missing, but a direct `clone_repo` call still raises a clean RuntimeError
so callers fail fast instead of crashing deep in subprocess.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from src.graph.state import RepoFile
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Per-file size cap for repo-mode review. Files larger than this are dropped
# during the snapshot walk so they never reach the per-reviewer whitelist
# or an LLM prompt. Aggressive on purpose: minified bundles, lockfile dumps
# and binary blobs aren't useful to the security reviewers and would blow
# the token budget. Reviewers surface a truncation note when this cap fires.
MAX_FILE_BYTES: int = 200_000

# Generous timeout for the clone subprocess. Most public repos finish in
# a few seconds with --depth=1; bigger ones (e.g. monorepos with large
# binary artefacts in HEAD) can take longer. Above this we bail.
_CLONE_TIMEOUT_SECONDS: float = 120.0


_REF_PATH_SEGMENTS = frozenset({"tree", "blob", "commit", "commits"})


def normalize_repo_url(raw: str) -> tuple[str, str | None]:
    """Validate and canonicalize a GitHub repo URL.

    Users routinely paste browser URLs (`/tree/<ref>`, `/blob/<ref>/...`,
    trailing slash, `.git` suffix, query/fragment). Feeding those to
    `git clone` either fails or clones the wrong branch. This helper
    canonicalizes to `https://<host>/<owner>/<repo>` and, when the input
    carried a branch in its path, returns that as an extracted ref.

    Returns `(canonical_url, optional_ref)`. Raises `ValueError` on
    malformed input (no scheme, missing owner or repo segment).
    """
    if not raw or not isinstance(raw, str):
        raise ValueError("repo_url is required")
    raw = raw.strip()
    if not raw.startswith(("http://", "https://")):
        raise ValueError(f"repo_url must use http(s): {raw!r}")

    parts = urlsplit(raw)
    if not parts.netloc:
        raise ValueError(f"repo_url has no host: {raw!r}")

    # SSRF / token-leak guard: any URL whose host is outside the
    # configured allowlist is refused BEFORE we hand it to `git clone`
    # or `_inject_token`. Without this check a request like
    # `https://attacker.com/o/r` would (a) clone arbitrary remote code,
    # (b) ship the GitHub PAT as basic-auth to the attacker.
    if not _host_is_allowed(parts.netloc):
        raise ValueError(
            f"repo_url host is not allowed: {parts.netloc!r} "
            f"(allowlist: {settings.GITHUB_ALLOWED_HOSTS}). "
            f"Add the host to GITHUB_ALLOWED_HOSTS if it's a trusted "
            f"GitHub Enterprise instance."
        )

    segments = [s for s in parts.path.split("/") if s]
    if len(segments) < 2:
        raise ValueError(f"repo_url is missing owner/repo: {raw!r}")

    owner, repo = segments[0], segments[1]
    if not owner or not repo:
        raise ValueError(f"repo_url has empty owner or repo: {raw!r}")
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]

    # `/tree/<ref>`, `/blob/<ref>/...`, `/commit/<sha>`, `/commits/<ref>...`
    # — the third path segment is the conventional "view-this-ref" marker
    # and the fourth is the ref itself.
    ref: str | None = None
    if len(segments) >= 4 and segments[2] in _REF_PATH_SEGMENTS:
        ref = segments[3]

    canonical = f"{parts.scheme}://{parts.netloc}/{owner}/{repo}"
    return canonical, ref


def _host_is_allowed(netloc: str) -> bool:
    """Return True when `netloc` (host[:port]) is in the configured
    `GITHUB_ALLOWED_HOSTS` list. Comparison is case-insensitive and
    strips the optional `:port` suffix so `github.com:443` and
    `github.com` both pass.

    Reads `settings` at call time so monkeypatching in tests just works.
    """
    if not netloc:
        return False
    host = netloc.split(":", 1)[0].lower()
    allowed = {h.lower() for h in (settings.GITHUB_ALLOWED_HOSTS or [])}
    return host in allowed


def _inject_token(url: str, token: str) -> str:
    """Embed `token` as basic-auth username in an HTTPS URL.

    GitHub accepts `https://<token>@github.com/owner/repo` as authenticated
    HTTPS git access. SSH-style URLs (git@github.com:owner/repo.git) are
    left alone — they go through the system's SSH agent and don't need
    inline credentials.

    **Defence-in-depth host-allowlist check.** `normalize_repo_url`
    already refuses non-allowlist hosts upstream, but we re-check here
    so any future code path that builds a clone URL directly (skipping
    normalisation) STILL can't leak the GitHub PAT to a foreign server.
    On host mismatch we return the URL unchanged, no token, no error
    — the upstream caller has already validated, so silent here is OK.
    """
    if not token or not url.startswith(("http://", "https://")):
        return url
    parts = urlsplit(url)
    if "@" in parts.netloc:  # already has auth
        return url
    if not _host_is_allowed(parts.netloc):
        return url
    netloc = f"{token}@{parts.netloc}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def clone_repo(repo_url: str, ref: str = "HEAD") -> Path:
    """Shallow-clone `repo_url` at `ref` into a unique tempdir.

    Returns the destination directory. Caller is responsible for calling
    `cleanup_snapshot` once finished — typically in a `finally:` block.
    """
    auth_url = _inject_token(repo_url, settings.GITHUB_TOKEN)
    # Place the snapshot under the project-local snapshots dir rather than
    # $TMPDIR. mkdir(parents=True, exist_ok=True) keeps the call idempotent
    # if the dir was wiped between reviews.
    snapshots_root = Path(settings.SNAPSHOTS_DIR)
    snapshots_root.mkdir(parents=True, exist_ok=True)
    dest = Path(tempfile.mkdtemp(prefix="ia-review-", dir=str(snapshots_root)))
    # `--branch=HEAD` is rejected by git ("Remote branch HEAD not found in
    # upstream origin") because HEAD is a symbolic ref, not a real branch
    # name. When the caller asks for HEAD (or omits the ref entirely), drop
    # `--branch` and let git pick the repo's default branch automatically.
    #
    # `-c credential.helper=` (empty value) MUST appear before the `clone`
    # subcommand — `-c` is a global git flag. Setting the credential
    # helper to empty disables every configured helper (osxkeychain,
    # libsecret, store, cache) FOR THIS PROCESS ONLY. The token we just
    # embedded as basic-auth never gets cached to disk.
    cmd = ["git", "-c", "credential.helper=", "clone", "--depth=1"]
    if ref and ref != "HEAD":
        cmd.extend([f"--branch={ref}", "--single-branch"])
    cmd.extend([auth_url, str(dest)])

    # Hardened env: GIT_TERMINAL_PROMPT=0 makes git fail fast on auth
    # problems instead of hanging on an interactive password prompt
    # (subprocess has no TTY anyway; without this var git can wedge for
    # the full _CLONE_TIMEOUT_SECONDS). GIT_ASKPASS=/usr/bin/false is a
    # second layer for the case where some packages still try GUI askpass.
    # We inherit the rest of os.environ so PATH / proxy settings keep
    # working.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/false"}

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_CLONE_TIMEOUT_SECONDS,
            env=env,
        )
    except FileNotFoundError as e:
        cleanup_snapshot(dest)
        raise RuntimeError(
            "git executable not found on PATH — repo-mode review requires git installed"
        ) from e
    except subprocess.TimeoutExpired as e:
        cleanup_snapshot(dest)
        raise RuntimeError(
            f"git clone timed out after {_CLONE_TIMEOUT_SECONDS}s for {repo_url}@{ref}"
        ) from e

    if result.returncode != 0:
        cleanup_snapshot(dest)
        # stderr can leak the token if the URL is malformed; strip token
        # before logging.
        safe_url = repo_url
        stderr = (result.stderr or "").replace(settings.GITHUB_TOKEN or "***", "***")
        raise RuntimeError(
            f"git clone failed for {safe_url}@{ref} (exit {result.returncode}): {stderr.strip()}"
        )
    logger.info("Cloned %s@%s into %s", repo_url, ref, dest)
    return dest


def list_repo_files(snapshot_dir: Path) -> list[RepoFile]:
    """Walk the cloned tree and produce `RepoFile` entries.

    `.git/` is excluded outright. Files larger than `MAX_FILE_BYTES` are
    dropped (binary blobs, minified bundles) — the per-reviewer whitelist
    filters further. `content` stays empty: reviewers read it lazily from
    `snapshot_dir / path` when they actually want to scan a file.
    """
    snapshot_dir = Path(snapshot_dir)
    files: list[RepoFile] = []
    for path in snapshot_dir.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(snapshot_dir).parts
        if rel_parts and rel_parts[0] == ".git":
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > MAX_FILE_BYTES:
            continue
        rel = "/".join(rel_parts)
        files.append(RepoFile(path=rel, content="", size=size))
    return files


def cleanup_snapshot(snapshot_dir: Path) -> None:
    """Remove `snapshot_dir` if it exists. Errors are logged and swallowed —
    a leaked tempdir is preferable to a crashed cleanup path."""
    try:
        if Path(snapshot_dir).exists():
            shutil.rmtree(snapshot_dir, ignore_errors=True)
    except Exception as e:
        logger.warning("Failed to clean snapshot %s: %s", snapshot_dir, e)
