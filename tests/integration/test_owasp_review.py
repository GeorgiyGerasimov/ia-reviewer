import json
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.owasp import OWASPTop10Reviewer
from src.graph.state import ReviewState


@patch("src.agents.base_reviewer.ModelFactory.get")
async def test_owasp_reviewer_returns_info_when_clean(mock_get_model, pr_request):
    mock_model = AsyncMock()
    mock_model.ainvoke.return_value = MagicMock(
        content=json.dumps(
            {
                "findings": [],
                "summary": "no OWASP findings beyond injection / dependencies",
                "severity": "info",
            }
        )
    )
    mock_get_model.return_value = mock_model

    agent = OWASPTop10Reviewer()
    update = await agent.run(ReviewState(request=pr_request))

    review = update["agent_reviews"][0]
    assert review.role == "owasp"
    assert review.severity == "info"
    assert review.passed is True
    assert review.findings == []
