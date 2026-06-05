"""PastContextAgent — embeds a query derived from the request and pulls
top-K similar past findings per role from `ReviewStore.retrieve_similar`.

Writes the result to `state.past_findings_by_role` (dict keyed by role).
Reviewers' `_build_context` splices this dict into their LLM prompt.

Contract:
- Returns `{"past_findings_by_role": {...}}` partial state update.
- Graceful no-op (empty dict) when:
    * embedder is None or store is None (RAG disabled by config / no DB)
    * embedder.embed returns None (gateway / network failure)
    * state.request is None (defensive)
- Honours `state.request.scope`: only retrieves for in-scope roles.
- Uses `target_url` (pr_url for PR-mode, repo_url for repo-mode) as the
  same-repo filter in `retrieve_similar`.

Tests mock the embedder + the store at their public interfaces.
"""

import pytest

from src.graph.state import RepoFile, ReviewRequest, ReviewState


@pytest.fixture
def embedder(mocker):
    m = mocker.AsyncMock()
    m.embed = mocker.AsyncMock(return_value=[0.1, 0.2, 0.3, 0.4])
    return m


@pytest.fixture
def store(mocker):
    m = mocker.AsyncMock()
    m.retrieve_similar = mocker.AsyncMock(return_value=[])
    return m


def _pr_state(scope: list[str] | None = None) -> ReviewState:
    return ReviewState(
        request=ReviewRequest(
            mode="pr",
            pr_url="https://github.com/o/r/pull/42",
            diff="diff text",
            files_changed=["a.py", "b.py"],
            author="dev",
            scope=scope or [],
        ),
    )


def _repo_state(scope: list[str] | None = None) -> ReviewState:
    return ReviewState(
        request=ReviewRequest(
            mode="repo",
            repo_url="https://github.com/o/r",
            ref="main",
            repo_files=[
                RepoFile(path="src/a.py", content="", size=100),
                RepoFile(path="src/b.py", content="", size=200),
            ],
            scope=scope or [],
        ),
    )


# ── happy path ────────────────────────────────────────────────────────────────


async def test_run_embeds_query_and_retrieves_for_each_role(embedder, store):
    """All three roles (dependency, injection, owasp) get a retrieve_similar
    call. Each role's past findings are written into the per-role dict."""
    from src.agents.past_context import PastContextAgent

    # Distinct rows per role so we can assert each lands in the right bucket.
    def _by_role(*, target_url, role, query_embedding, k):
        return [{"role": role, "issue": f"past-{role}-1"}]

    store.retrieve_similar.side_effect = _by_role
    agent = PastContextAgent(embedder=embedder, store=store, top_k=5)

    update = await agent.run(_pr_state())

    assert "past_findings_by_role" in update
    by_role = update["past_findings_by_role"]
    assert set(by_role.keys()) == {"dependency", "injection", "owasp"}
    assert by_role["dependency"][0]["issue"] == "past-dependency-1"
    assert by_role["injection"][0]["issue"] == "past-injection-1"
    assert by_role["owasp"][0]["issue"] == "past-owasp-1"

    # Embedder called once (single query embedding, reused across roles).
    assert embedder.embed.await_count == 1
    # Three retrieve calls, one per role.
    assert store.retrieve_similar.await_count == 3


async def test_run_uses_pr_url_as_target_in_pr_mode(embedder, store):
    from src.agents.past_context import PastContextAgent

    agent = PastContextAgent(embedder=embedder, store=store, top_k=5)
    await agent.run(_pr_state())

    for c in store.retrieve_similar.await_args_list:
        kwargs = c.kwargs
        assert kwargs["target_url"] == "https://github.com/o/r/pull/42"


async def test_run_uses_repo_url_as_target_in_repo_mode(embedder, store):
    from src.agents.past_context import PastContextAgent

    agent = PastContextAgent(embedder=embedder, store=store, top_k=5)
    await agent.run(_repo_state())

    for c in store.retrieve_similar.await_args_list:
        kwargs = c.kwargs
        assert kwargs["target_url"] == "https://github.com/o/r"


async def test_run_passes_top_k_to_retrieve(embedder, store):
    from src.agents.past_context import PastContextAgent

    agent = PastContextAgent(embedder=embedder, store=store, top_k=3)
    await agent.run(_pr_state())

    for c in store.retrieve_similar.await_args_list:
        assert c.kwargs["k"] == 3


async def test_run_honours_scope_restriction(embedder, store):
    """`scope=["injection"]` → retrieve_similar called ONLY for injection."""
    from src.agents.past_context import PastContextAgent

    agent = PastContextAgent(embedder=embedder, store=store, top_k=5)
    update = await agent.run(_pr_state(scope=["injection"]))

    assert store.retrieve_similar.await_count == 1
    roles_called = [c.kwargs["role"] for c in store.retrieve_similar.await_args_list]
    assert roles_called == ["injection"]
    # Only injection key in the output dict.
    assert set(update["past_findings_by_role"].keys()) == {"injection"}


# ── disabled / failure paths ──────────────────────────────────────────────────


async def test_run_without_embedder_is_a_noop(store):
    """No embedder configured → empty dict, no store calls, no LLM."""
    from src.agents.past_context import PastContextAgent

    agent = PastContextAgent(embedder=None, store=store)
    update = await agent.run(_pr_state())

    assert update == {"past_findings_by_role": {}}
    store.retrieve_similar.assert_not_called()


async def test_run_without_store_is_a_noop(embedder):
    """No store wired → empty dict, no embedder call (no point embedding
    if we can't query)."""
    from src.agents.past_context import PastContextAgent

    agent = PastContextAgent(embedder=embedder, store=None)
    update = await agent.run(_pr_state())

    assert update == {"past_findings_by_role": {}}
    embedder.embed.assert_not_called()


async def test_run_when_embedder_returns_none_is_a_noop(embedder, store):
    """Embedder failure → no store calls, empty dict."""
    from src.agents.past_context import PastContextAgent

    embedder.embed = embedder.embed.__class__(return_value=None)
    agent = PastContextAgent(embedder=embedder, store=store)
    update = await agent.run(_pr_state())

    assert update == {"past_findings_by_role": {}}
    store.retrieve_similar.assert_not_called()


async def test_run_with_no_request_is_a_noop(embedder, store):
    from src.agents.past_context import PastContextAgent

    agent = PastContextAgent(embedder=embedder, store=store)
    update = await agent.run(ReviewState(request=None))

    assert update == {"past_findings_by_role": {}}
    embedder.embed.assert_not_called()
    store.retrieve_similar.assert_not_called()


async def test_run_query_text_includes_target_url(embedder, store):
    """The embedded query text must include something distinguishing about
    the target — same repo with different PRs should produce broadly
    similar embeddings (good for retrieval recall), but at minimum the
    target_url should be present so the embedding isn't generic."""
    from src.agents.past_context import PastContextAgent

    agent = PastContextAgent(embedder=embedder, store=store)
    await agent.run(_pr_state())

    embedded_text = embedder.embed.await_args.args[0]
    assert "https://github.com/o/r/pull/42" in embedded_text
