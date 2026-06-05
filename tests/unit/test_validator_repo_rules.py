"""Unit tests for the repo-mode rules of RequestValidator.

In repo-mode there is no diff to inspect — the validator runs pure-code
checks on the fetched `repo_files` tree:
  - Empty tree → rejected as `empty_repo`
  - Tree larger than `MAX_REPO_FILES_HARD` → rejected as `oversized_repo`
  - Otherwise → accepted without an LLM call

The LLM trolling judge is PR-specific and should never run in repo-mode.
"""

from unittest.mock import AsyncMock, patch

from src.agents.validator import RequestValidator
from src.graph.state import RepoFile, ReviewRequest, ReviewState
from src.utils.config import settings


def _repo_state(*paths: str) -> ReviewState:
    return ReviewState(
        request=ReviewRequest(
            mode="repo",
            repo_url="https://github.com/o/r",
            ref="main",
            repo_files=[RepoFile(path=p, content="", size=100) for p in paths],
            author="bot",
        )
    )


async def test_validator_rejects_empty_repo_tree():
    """Empty tree → reject with category `empty_repo`, no LLM call."""
    mock_model = AsyncMock()
    with patch("src.agents.validator.ModelFactory.get", return_value=mock_model):
        validator = RequestValidator()
        update = await validator.run(_repo_state())  # no files

    verdict = update["validation"]
    assert verdict.accepted is False
    assert verdict.category == "empty_repo"
    mock_model.ainvoke.assert_not_awaited()


async def test_validator_rejects_oversized_repo():
    """Tree above MAX_REPO_FILES_HARD → reject as `oversized_repo`, no LLM call."""
    paths = [f"src/file_{i}.py" for i in range(settings.MAX_REPO_FILES_HARD + 1)]
    mock_model = AsyncMock()
    with patch("src.agents.validator.ModelFactory.get", return_value=mock_model):
        validator = RequestValidator()
        update = await validator.run(_repo_state(*paths))

    verdict = update["validation"]
    assert verdict.accepted is False
    assert verdict.category == "oversized_repo"
    mock_model.ainvoke.assert_not_awaited()


async def test_validator_accepts_normal_repo_without_llm():
    """Reasonable tree → accept; LLM judge is PR-only and must not be called."""
    mock_model = AsyncMock()
    with patch("src.agents.validator.ModelFactory.get", return_value=mock_model):
        validator = RequestValidator()
        update = await validator.run(_repo_state("src/app.py", "README.md", "go.mod"))

    verdict = update["validation"]
    assert verdict.accepted is True
    assert verdict.category == "accepted"
    mock_model.ainvoke.assert_not_awaited()
