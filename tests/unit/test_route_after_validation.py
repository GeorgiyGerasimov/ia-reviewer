"""A.3 — pure-logic tests for the validator conditional-edge router."""

from src.graph.coordinator import route_after_validation
from src.graph.state import ReviewState, ValidationVerdict


def test_route_after_validation_picks_fanout_on_accept():
    state = ReviewState(validation=ValidationVerdict(accepted=True, category="accepted", reason="ok"))
    target = route_after_validation(state)
    assert target == ["dependency_review", "injection_review", "owasp_review"]


def test_route_after_validation_picks_rejection_on_reject():
    state = ReviewState(
        validation=ValidationVerdict(accepted=False, category="docs_only", reason="docs only"),
    )
    assert route_after_validation(state) == "notify_rejection"


def test_route_after_validation_missing_validation_routes_to_rejection():
    """Safety net: if a partial impl forgets to set state.validation, route to
    rejection rather than letting the LLM-heavy fan-out run anyway."""
    state = ReviewState(validation=None)
    assert route_after_validation(state) == "notify_rejection"
