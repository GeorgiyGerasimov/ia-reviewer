"""Helpers for end-to-end graph tests.

LangGraph compiles `build_review_graph` once with node references that capture
the agent instances passed in. Global monkeypatching of the agent classes
won't reach already-compiled nodes — see the testing-principles skill. Use
`build_test_graph()` to inject mock agents directly.
"""

from src.agents.coordinator import CoordinatorAgent
from src.agents.dependency import DependencyReviewer
from src.agents.injection import InjectionReviewer
from src.agents.owasp import OWASPTop10Reviewer
from src.agents.validator import RequestValidator
from src.graph.coordinator import build_review_graph


def build_test_graph(
    *,
    coordinator: CoordinatorAgent | None = None,
    validator: RequestValidator | None = None,
    dependency: DependencyReviewer | None = None,
    injection: InjectionReviewer | None = None,
    owasp: OWASPTop10Reviewer | None = None,
    checkpointer=None,
):
    return build_review_graph(
        coordinator=coordinator,
        validator=validator,
        dependency=dependency,
        injection=injection,
        owasp=owasp,
        checkpointer=checkpointer,
    )
