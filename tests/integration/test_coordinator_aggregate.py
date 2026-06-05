from src.agents.coordinator import CoordinatorAgent


async def test_aggregate_renders_per_role_sections(state_with_reviews, mocker):
    coordinator = CoordinatorAgent(github=mocker.AsyncMock())
    update = await coordinator.aggregate(state_with_reviews)
    report = update["final_report"]

    assert "## Security review" in report
    assert "### Dependencies — minor" in report
    assert "### Injection — critical" in report
    assert "### OWASP Top 10 — info" in report
    assert "**Overall severity:** critical" in report


async def test_aggregate_includes_findings_lines(state_with_reviews, mocker):
    coordinator = CoordinatorAgent(github=mocker.AsyncMock())
    update = await coordinator.aggregate(state_with_reviews)
    report = update["final_report"]

    assert "src/auth/service.py:8" in report
    assert "[critical]" in report
    assert "f-string in SQL" in report


async def test_aggregate_handles_empty_reviews(pr_state, mocker):
    coordinator = CoordinatorAgent(github=mocker.AsyncMock())
    update = await coordinator.aggregate(pr_state)
    assert "no reviewers ran" in update["final_report"]
