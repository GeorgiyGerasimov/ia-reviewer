"""BaseReviewer must splice retrieved past findings into the prompt
context, filtered by the reviewer's own role.

Contract:
- When `state.past_findings_by_role[self.role]` has entries, a
  "Previous findings on this repo:" block appears in the rendered
  context (PR-mode `_build_context` and repo-mode
  `_build_repo_file_context`).
- When the role's list is empty / the role is missing / the whole dict
  is empty, the block is omitted (no noise in the prompt).
- The block contains the issue text + severity + file location for
  each finding so the reviewer can recognise duplicates / regressions.
- Reviewers see ONLY their own past findings — `injection` does not get
  `dependency` entries.
"""

from src.agents.base_reviewer import BaseReviewer
from src.graph.state import RepoFile, ReviewRequest, ReviewState


class _RoleReviewer(BaseReviewer):
    """Minimal subclass — just sets a role so we can test context build."""
    role = "injection"
    prompt_template = "ctx={context}"


def _pr_state(by_role: dict | None = None) -> ReviewState:
    return ReviewState(
        request=ReviewRequest(
            mode="pr",
            pr_url="https://github.com/o/r/pull/42",
            diff="diff text",
            files_changed=["a.py"],
            author="dev",
        ),
        past_findings_by_role=by_role or {},
    )


def _repo_state(by_role: dict | None = None) -> ReviewState:
    return ReviewState(
        request=ReviewRequest(
            mode="repo",
            repo_url="https://github.com/o/r",
            ref="main",
            repo_files=[],
        ),
        past_findings_by_role=by_role or {},
    )


# ── PR-mode context ───────────────────────────────────────────────────────────


def test_pr_context_includes_past_findings_block_when_present():
    reviewer = _RoleReviewer()
    state = _pr_state(by_role={
        "injection": [
            {
                "issue": "SQL via f-string",
                "severity": "major",
                "file": "src/auth.py",
                "line": 42,
                "category": "sqli",
            }
        ],
    })

    context = reviewer._build_context(state)

    assert "Previous findings on this repo" in context
    assert "SQL via f-string" in context
    assert "src/auth.py" in context
    assert "major" in context


def test_pr_context_omits_block_when_role_has_no_past_findings():
    reviewer = _RoleReviewer()
    state = _pr_state(by_role={"injection": []})

    context = reviewer._build_context(state)

    assert "Previous findings on this repo" not in context


def test_pr_context_omits_block_when_past_findings_dict_is_empty():
    reviewer = _RoleReviewer()
    state = _pr_state(by_role={})

    context = reviewer._build_context(state)

    assert "Previous findings on this repo" not in context


def test_pr_context_filters_by_reviewer_role():
    """An injection reviewer never sees dependency-role past findings even
    if they're present in the dict."""
    reviewer = _RoleReviewer()  # role="injection"
    state = _pr_state(by_role={
        "injection": [{"issue": "inj-found", "severity": "major", "file": "x.py"}],
        "dependency": [{"issue": "dep-found", "severity": "critical", "file": "package.json"}],
    })

    context = reviewer._build_context(state)

    assert "inj-found" in context
    assert "dep-found" not in context
    assert "package.json" not in context


# ── Repo-mode per-file context ────────────────────────────────────────────────


def test_repo_file_context_includes_past_findings_block_when_present():
    reviewer = _RoleReviewer()
    state = _repo_state(by_role={
        "injection": [
            {
                "issue": "command injection via shell=True",
                "severity": "critical",
                "file": "src/cmd.py",
                "line": 7,
                "category": "cmd",
            },
        ],
    })

    repo_file = RepoFile(path="src/cmd.py", content="...", size=42)
    context = reviewer._build_repo_file_context(state, repo_file, "file content")

    assert "Previous findings on this repo" in context
    assert "command injection via shell=True" in context
    assert "critical" in context


def test_repo_file_context_omits_block_when_no_role_findings():
    reviewer = _RoleReviewer()
    state = _repo_state(by_role={})

    repo_file = RepoFile(path="x.py", content="...", size=10)
    context = reviewer._build_repo_file_context(state, repo_file, "content")

    assert "Previous findings on this repo" not in context


def test_past_findings_block_lists_each_entry_one_per_line():
    """Multiple past findings render as a readable bullet list."""
    reviewer = _RoleReviewer()
    state = _pr_state(by_role={
        "injection": [
            {"issue": "first issue", "severity": "major", "file": "a.py"},
            {"issue": "second issue", "severity": "minor", "file": "b.py"},
            {"issue": "third issue", "severity": "info", "file": "c.py"},
        ],
    })

    context = reviewer._build_context(state)

    assert "first issue" in context
    assert "second issue" in context
    assert "third issue" in context
    # Each finding on its own line — count newlines in the past-findings region.
    block_start = context.index("Previous findings on this repo")
    block = context[block_start:]
    # 3 findings → at least 3 separate lines of content beyond the header.
    assert block.count("\n") >= 3
