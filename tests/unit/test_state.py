from dataclasses import fields
from operator import add
from typing import get_args, get_origin

from src.graph.state import ALLOWED_SCOPE_ROLES, AgentReview, ReviewState


def test_allowed_scope_roles_are_security_only():
    assert ALLOWED_SCOPE_ROLES == ("dependency", "injection", "owasp")


def test_review_state_defaults_are_empty():
    state = ReviewState()
    assert state.request is None
    assert state.agent_reviews == []
    assert state.final_report == ""
    assert state.pr_comment_id is None
    assert state.error == ""
    assert state.completed is False


def test_agent_reviews_uses_add_reducer():
    """Parallel security agents rely on the `add` reducer; without it the
    last writer would overwrite the others' findings."""
    agent_reviews_field = next(f for f in fields(ReviewState) if f.name == "agent_reviews")
    annotation = agent_reviews_field.type
    if isinstance(annotation, str):
        # Postponed evaluation — resolve via typing
        from typing import get_type_hints

        annotation = get_type_hints(ReviewState, include_extras=True)["agent_reviews"]
    assert get_origin(annotation).__name__ == "Annotated" or _is_annotated(annotation)
    args = get_args(annotation)
    assert add in args, f"expected `add` reducer in Annotated args, got {args!r}"


def test_agent_review_dataclass_shape():
    review = AgentReview(
        agent_name="X",
        role="dependency",
        findings=[],
        summary="ok",
        severity="info",
        passed=True,
    )
    assert review.role == "dependency"
    assert review.passed is True


def _is_annotated(tp) -> bool:
    return getattr(tp, "__class__", None).__name__ == "_AnnotatedAlias"
