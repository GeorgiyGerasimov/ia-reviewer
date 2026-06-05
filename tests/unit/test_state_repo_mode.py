"""Tests for the repo-review extension of `ReviewRequest`.

PR-mode review (the original) keeps working untouched — `mode` defaults to
"pr" and the existing PR-shaped construction must continue to succeed
without explicit `mode=`. Repo-mode adds `repo_url` + `ref` so the graph
can scan a full snapshot of a GitHub repository rather than a diff.
"""

from src.graph.state import ReviewRequest


def test_review_request_defaults_to_pr_mode():
    """Existing PR-shape construction stays valid; `mode` defaults to 'pr'."""
    req = ReviewRequest(
        pr_url="https://github.com/o/r/pull/1",
        diff="diff --git a/x b/x",
        files_changed=["x"],
        author="u",
    )
    assert req.mode == "pr"
    assert req.pr_url == "https://github.com/o/r/pull/1"
    assert req.repo_url == ""
    assert req.ref == "HEAD"


def test_review_request_repo_mode_shape():
    """Repo-mode is constructed with `mode='repo'`, `repo_url`, and `ref`.

    PR-only fields stay at their dataclass defaults so the same dataclass
    works for both modes without a discriminated union.
    """
    req = ReviewRequest(
        mode="repo",
        repo_url="https://github.com/o/r",
        ref="main",
        author="u",
    )
    assert req.mode == "repo"
    assert req.repo_url == "https://github.com/o/r"
    assert req.ref == "main"
    # PR-only fields stay empty
    assert req.pr_url == ""
    assert req.diff == ""
    assert req.files_changed == []
