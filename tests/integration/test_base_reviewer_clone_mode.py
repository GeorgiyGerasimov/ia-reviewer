"""BaseReviewer in repo-mode reads files from the cloned snapshot directory
rather than calling GitHub's REST API per file. The snapshot is a real
directory written by `git clone --depth=1` (or a `tmp_path` in tests).

The change removes the API-rate-limit problem the user hit on large repos.

We use `InjectionReviewer` as the witness for BaseReviewer's per-file
repo-mode loop. (DependencyReviewer overrides `_run_repo` to delegate to
the deterministic `DependencyScanner` — covered separately by
`test_dependency_reviewer_scanner.py`.)
"""

import pytest

from src.agents.injection import InjectionReviewer
from src.graph.state import RepoFile, ReviewRequest, ReviewState

_LLM_JSON = (
    '```json\n'
    '{"findings": [{"issue": "stub"}], '
    '"summary": "stub finding", "severity": "minor"}\n'
    '```'
)


def _repo_state(snapshot_dir, *paths: str) -> ReviewState:
    return ReviewState(
        request=ReviewRequest(
            mode="repo",
            repo_url="https://github.com/o/r",
            ref="main",
            snapshot_dir=str(snapshot_dir),
            repo_files=[RepoFile(path=p, content="", size=100) for p in paths],
            author="bot",
        )
    )


@pytest.fixture
def llm_mock(mocker):
    fake_model = mocker.AsyncMock()
    fake_model.ainvoke = mocker.AsyncMock(return_value=mocker.MagicMock(content=_LLM_JSON))
    mocker.patch("src.agents.base_reviewer.ModelFactory.get", return_value=fake_model)
    return fake_model


async def test_base_reviewer_repo_mode_reads_from_snapshot_dir(llm_mock, tmp_path):
    """Reviewer reads file contents from disk under `state.request.snapshot_dir`,
    not from a GitHub client. Each whitelisted file becomes one LLM call.
    """
    (tmp_path / "app.py").write_text("import os\nos.system(user_input)\n")
    (tmp_path / "main.js").write_text("eval(req.query.code)\n")
    # Non-whitelisted file should not be read by InjectionReviewer
    (tmp_path / "README.md").write_text("# hi")

    reviewer = InjectionReviewer()
    state = _repo_state(tmp_path, "app.py", "main.js", "README.md")

    result = await reviewer.run(state)

    # Two whitelisted files → two LLM calls
    assert llm_mock.ainvoke.await_count == 2
    [review] = result["agent_reviews"]
    files_in_findings = {f["file"] for f in review.findings}
    assert files_in_findings == {"app.py", "main.js"}
