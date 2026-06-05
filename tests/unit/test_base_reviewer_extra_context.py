"""B.4 — BaseReviewer._build_context splices in state.extra_context.

After the human approves a re-review with extra context, the second pass
reviewers must see that clarification in their prompt. Empty extra_context
must not bloat the prompt with placeholder headers.
"""

from src.agents.base_reviewer import BaseReviewer
from src.graph.state import ReviewRequest, ReviewState


class _DummyReviewer(BaseReviewer):
    role = "dummy"


def _state(extra_context: str = "") -> ReviewState:
    return ReviewState(
        request=ReviewRequest(
            pr_url="https://github.com/o/r/pull/1",
            diff="diff --git a/x.py b/x.py\n+def f(): pass\n",
            files_changed=["src/x.py"],
            author="dev",
        ),
        extra_context=extra_context,
    )


def test_build_context_includes_extra_context_when_present(mocker):
    mocker.patch("src.agents.base_reviewer.ModelFactory.get", return_value=mocker.AsyncMock())
    reviewer = _DummyReviewer()
    context = reviewer._build_context(_state(extra_context="ignore Bazel-generated deps"))
    assert "ignore Bazel-generated deps" in context
    assert "diff --git" in context  # original diff still there


def test_build_context_omits_extra_context_header_when_empty(mocker):
    mocker.patch("src.agents.base_reviewer.ModelFactory.get", return_value=mocker.AsyncMock())
    reviewer = _DummyReviewer()
    context = reviewer._build_context(_state(extra_context=""))
    # No empty-header noise like "Human clarification:\n\n" with nothing after it.
    assert "clarification" not in context.lower()
