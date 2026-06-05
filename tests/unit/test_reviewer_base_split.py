"""`BaseReviewer` is split into two sibling shapes — the two repo-mode
strategies that used to share one ambiguous base.

`LLMPerFileReviewer` — repo-mode does the per-file LLM loop. `Injection`
and `OWASPTop10` use this; the inherited `_run_repo` reads each
matched file, makes one LLM call, aggregates findings.

`ScriptedScannerReviewer` — repo-mode delegates to a scripted scanner
(parse manifests + query OSV, etc.); subclasses MUST override
`_run_repo`. `DependencyReviewer` uses this.

Both inherit from the shared `BaseReviewer` for PR-mode + scope filter
+ path-matching + `_parse_response` boilerplate.

Why the split: with the previous shape, a subclass that overrode
`_run_repo` (Dependency) was indistinguishable from one that didn't
(Injection / OWASP). Any change to the base's per-file loop silently
skipped Dependency. Two-headed contract → two named base classes.
"""

import pytest

from src.agents.base_reviewer import (
    BaseReviewer,
    LLMPerFileReviewer,
    ScriptedScannerReviewer,
)


class _ConcreteBaseOnly(BaseReviewer):
    """Subclass `BaseReviewer` directly without picking a repo-mode
    strategy — should raise on the FIRST repo-mode call so the developer
    knows to pick a side."""

    role = "test"
    PATH_PATTERNS = ("*.py",)
    prompt_template = "{context}"


class _ConcreteLLMPerFile(LLMPerFileReviewer):
    role = "test_llm"
    PATH_PATTERNS = ("*.py",)
    prompt_template = "{context}"


class _ConcreteScripted(ScriptedScannerReviewer):
    role = "test_scripted"
    PATH_PATTERNS = ("*.txt",)
    prompt_template = "{context}"


# ── BaseReviewer alone refuses repo-mode ──────────────────────────────


async def test_base_reviewer_repo_mode_raises_without_subclass(mocker):
    """A direct BaseReviewer subclass that didn't pick a strategy must
    NOT silently do nothing on repo-mode — it should fail loudly so the
    author knows to extend `LLMPerFileReviewer` or `ScriptedScannerReviewer`.
    """
    from src.graph.state import RepoFile, ReviewRequest, ReviewState

    mocker.patch("src.agents.base_reviewer.ModelFactory.get", return_value=mocker.AsyncMock())
    r = _ConcreteBaseOnly()
    state = ReviewState(
        request=ReviewRequest(
            mode="repo",
            repo_url="https://github.com/o/r",
            snapshot_dir="/tmp/whatever",
            repo_files=[RepoFile(path="x.py", content="", size=1)],
        )
    )
    with pytest.raises(NotImplementedError, match=r"_run_repo"):
        await r.run(state)


# ── LLMPerFileReviewer keeps the existing per-file loop ───────────────


async def test_llm_per_file_reviewer_runs_per_file_loop(mocker, tmp_path):
    """Concrete `LLMPerFileReviewer` must do the same thing the old
    `BaseReviewer._run_repo` did: one LLM call per matched file."""
    from src.graph.state import RepoFile, ReviewRequest, ReviewState

    (tmp_path / "a.py").write_text("print(1)")
    (tmp_path / "b.py").write_text("print(2)")

    llm = mocker.AsyncMock()
    llm.ainvoke = mocker.AsyncMock(
        return_value=mocker.MagicMock(content='```json\n{"findings": [], "summary": "ok", "severity": "info"}\n```')
    )
    mocker.patch("src.agents.base_reviewer.ModelFactory.get", return_value=llm)

    r = _ConcreteLLMPerFile()
    state = ReviewState(
        request=ReviewRequest(
            mode="repo",
            repo_url="https://github.com/o/r",
            snapshot_dir=str(tmp_path),
            repo_files=[
                RepoFile(path="a.py", content="", size=1),
                RepoFile(path="b.py", content="", size=1),
            ],
        )
    )
    update = await r.run(state)

    assert llm.ainvoke.await_count == 2, (
        "LLMPerFileReviewer must call the LLM once per matched file; "
        f"got {llm.ainvoke.await_count}"
    )
    [review] = update["agent_reviews"]
    assert review.role == "test_llm"


# ── ScriptedScannerReviewer signals "implement _run_repo yourself" ────


async def test_scripted_scanner_reviewer_default_raises(mocker):
    """A `ScriptedScannerReviewer` that didn't override `_run_repo`
    must raise — its whole reason for existing is "subclasses do their
    own scripted scan instead of the LLM loop"."""
    from src.graph.state import RepoFile, ReviewRequest, ReviewState

    mocker.patch("src.agents.base_reviewer.ModelFactory.get", return_value=mocker.AsyncMock())
    r = _ConcreteScripted()
    state = ReviewState(
        request=ReviewRequest(
            mode="repo",
            repo_url="https://github.com/o/r",
            snapshot_dir="/tmp/whatever",
            repo_files=[RepoFile(path="x.txt", content="", size=1)],
        )
    )
    with pytest.raises(NotImplementedError, match=r"_run_repo"):
        await r.run(state)


# ── concrete reviewers picked the right base ─────────────────────────


def test_injection_uses_llm_per_file_base():
    from src.agents.injection import InjectionReviewer

    assert issubclass(InjectionReviewer, LLMPerFileReviewer), (
        "InjectionReviewer should extend LLMPerFileReviewer — its repo-mode "
        "is exactly the per-file LLM loop."
    )


def test_owasp_uses_llm_per_file_base():
    from src.agents.owasp import OWASPTop10Reviewer

    assert issubclass(OWASPTop10Reviewer, LLMPerFileReviewer)


def test_dependency_uses_scripted_scanner_base():
    from src.agents.dependency import DependencyReviewer

    assert issubclass(DependencyReviewer, ScriptedScannerReviewer), (
        "DependencyReviewer should extend ScriptedScannerReviewer — its "
        "repo-mode is the OSV-scan + 1-summary-LLM pattern, not the "
        "per-file loop."
    )


# ── PR-mode behaviour is shared on the BaseReviewer ───────────────────


async def test_pr_mode_still_works_from_either_base(mocker, pr_request):
    """Both subclass families inherit PR-mode `_run_pr` from BaseReviewer.
    PR-mode is identical across the two — just a single LLM call against
    a diff."""
    llm = mocker.AsyncMock()
    llm.ainvoke = mocker.AsyncMock(
        return_value=mocker.MagicMock(content='```json\n{"findings": [], "summary": "ok", "severity": "info"}\n```')
    )
    mocker.patch("src.agents.base_reviewer.ModelFactory.get", return_value=llm)

    from src.graph.state import ReviewState

    for cls in (_ConcreteLLMPerFile, _ConcreteScripted):
        r = cls()
        update = await r.run(ReviewState(request=pr_request))
        assert update.get("agent_reviews"), f"{cls.__name__} PR-mode produced no review"
        llm.ainvoke.reset_mock()
