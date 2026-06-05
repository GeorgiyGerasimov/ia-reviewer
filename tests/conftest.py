import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from src.graph.state import AgentReview, ReviewRequest, ReviewState

FIXTURES = Path(__file__).parent / "fixtures"


@contextmanager
def stub_integration_settings():
    """Provides real string defaults for settings used by HTTP-owning clients."""
    with patch("src.integrations.github.settings") as gs:
        gs.GITHUB_API_URL = "https://api.github.com"
        gs.GITHUB_TOKEN = "ghp-test"
        yield


# ── fixture loaders ──────────────────────────────────────────────────────────


def load_json(relative: str) -> dict | list:
    return json.loads((FIXTURES / relative).read_text())


def load_text(relative: str) -> str:
    return (FIXTURES / relative).read_text()


# ── shared state factories ───────────────────────────────────────────────────


@pytest.fixture
def pr_request() -> ReviewRequest:
    return ReviewRequest(
        pr_url="https://github.com/octo/myrepo/pull/42",
        diff=load_text("diffs/backend.diff"),
        files_changed=["src/auth/service.py", "tests/test_auth.py"],
        author="jane-doe",
    )


@pytest.fixture
def pr_state(pr_request) -> ReviewState:
    return ReviewState(request=pr_request)


@pytest.fixture
def state_with_reviews(pr_request) -> ReviewState:
    state = ReviewState(request=pr_request)
    state.agent_reviews = [
        AgentReview(
            agent_name="DependencyReviewer",
            role="dependency",
            findings=[{"file": "requirements.txt", "issue": "unpinned httpx"}],
            summary="One unpinned dependency",
            severity="minor",
            passed=True,
        ),
        AgentReview(
            agent_name="InjectionReviewer",
            role="injection",
            findings=[
                {
                    "file": "src/auth/service.py",
                    "line": 8,
                    "category": "sqli",
                    "issue": "f-string in SQL",
                    "severity": "critical",
                }
            ],
            summary="SQL injection risk in login query",
            severity="critical",
            passed=False,
        ),
        AgentReview(
            agent_name="OWASPTop10Reviewer",
            role="owasp",
            findings=[],
            summary="No additional OWASP findings",
            severity="info",
            passed=True,
        ),
    ]
    return state


# ── github fixture data ──────────────────────────────────────────────────────


@pytest.fixture
def github_pr_response() -> dict:
    return load_json("github_responses/pr.json")


@pytest.fixture
def github_files_response() -> list:
    return load_json("github_responses/files.json")
