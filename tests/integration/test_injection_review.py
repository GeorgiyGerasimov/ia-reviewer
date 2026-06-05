import json
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.injection import InjectionReviewer
from src.graph.state import ReviewState


@patch("src.agents.base_reviewer.ModelFactory.get")
async def test_injection_reviewer_flags_sql_injection(mock_get_model, pr_request):
    mock_model = AsyncMock()
    mock_model.ainvoke.return_value = MagicMock(
        content=json.dumps(
            {
                "findings": [
                    {
                        "file": "src/auth/service.py",
                        "line": 8,
                        "category": "sqli",
                        "issue": "f-string interpolated into SQL",
                        "severity": "critical",
                    }
                ],
                "summary": "SQL injection in login query.",
                "severity": "critical",
            }
        )
    )
    mock_get_model.return_value = mock_model

    agent = InjectionReviewer()
    update = await agent.run(ReviewState(request=pr_request))

    review = update["agent_reviews"][0]
    assert review.role == "injection"
    assert review.severity == "critical"
    assert review.passed is False
    assert review.findings[0]["category"] == "sqli"
