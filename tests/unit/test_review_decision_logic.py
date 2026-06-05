"""B.1 — pure-logic tests for the ambiguity predicate and cycle cap.

The graph runs `review_decision` after the three reviewers join. It either:
  - Routes straight to aggregate (clean reviews OR cycle cap exhausted)
  - Triggers a human-clarification interrupt (ambiguous + cap not exhausted)

This file covers the pure-code predicate and cycle-cap helpers without
exercising LangGraph's interrupt() machinery — that lives in B.2.
"""

from src.agents.review_decision import MAX_CYCLES, ReviewDecisionAgent, _needs_clarification
from src.graph.state import AgentReview


def _review(role: str, *, severity: str = "info", findings=None, passed: bool = True) -> AgentReview:
    return AgentReview(
        agent_name=f"{role.title()}Reviewer",
        role=role,
        findings=findings or [],
        summary=f"{role} ran",
        severity=severity,
        passed=passed,
    )


def test_clean_reviews_do_not_need_clarification():
    reviews = [
        _review("dependency", severity="info", passed=True),
        _review("injection", severity="minor", findings=[{"issue": "x"}], passed=True),
        _review("owasp", severity="info", passed=True),
    ]
    assert _needs_clarification(reviews) is False


def test_blocking_severity_with_findings_does_not_trigger_clarification():
    """A reviewer that found something concrete (passed=False AND non-empty findings)
    is doing its job — that's a real signal, not ambiguity."""
    reviews = [
        _review(
            "injection",
            severity="critical",
            findings=[{"file": "x.py", "issue": "sql"}],
            passed=False,
        ),
    ]
    assert _needs_clarification(reviews) is False


def test_blocking_severity_with_empty_findings_triggers_clarification():
    """The signal we DO want to ask the human about: 'I think there's a problem
    but I can't pinpoint it' — passed=False with no concrete findings."""
    reviews = [
        _review("dependency", severity="major", findings=[], passed=False),
    ]
    assert _needs_clarification(reviews) is True


def test_max_cycles_is_three():
    """Initial pass + up to 2 human-approved reruns = 3 passes total. No fourth pass."""
    assert MAX_CYCLES == 3


def test_review_decision_agent_constructor_does_not_crash():
    """ReviewDecisionAgent has no model/LLM dependency — it's pure routing logic
    plus an interrupt call. Constructor should work without any external setup."""
    agent = ReviewDecisionAgent()
    assert agent is not None
