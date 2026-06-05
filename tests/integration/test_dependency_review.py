import json
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.dependency import DependencyReviewer
from src.graph.state import ReviewState


@patch("src.agents.base_reviewer.ModelFactory.get")
async def test_dependency_reviewer_appends_review(mock_get_model, pr_request):
    mock_model = AsyncMock()
    mock_model.ainvoke.return_value = MagicMock(
        content="```json\n"
        + json.dumps(
            {
                "findings": [
                    {
                        "file": "requirements.txt",
                        "package": "httpx",
                        "version": "0.20",
                        "issue": "outdated and has known CVE",
                        "severity": "major",
                    }
                ],
                "summary": "One outdated dependency with known CVE.",
                "severity": "major",
            }
        )
        + "\n```"
    )
    mock_get_model.return_value = mock_model

    agent = DependencyReviewer()
    state = ReviewState(request=pr_request)
    update = await agent.run(state)

    assert "agent_reviews" in update
    assert len(update["agent_reviews"]) == 1
    review = update["agent_reviews"][0]
    assert review.role == "dependency"
    assert review.severity == "major"
    assert review.passed is False  # major is blocking
    assert review.findings[0]["package"] == "httpx"


@patch("src.agents.base_reviewer.ModelFactory.get")
async def test_dependency_reviewer_respects_scope(mock_get_model, pr_request):
    mock_get_model.return_value = AsyncMock()
    pr_request.scope = ["injection"]  # exclude dependency
    state = ReviewState(request=pr_request)

    agent = DependencyReviewer()
    update = await agent.run(state)

    assert update == {}
    mock_get_model.return_value.ainvoke.assert_not_called()
