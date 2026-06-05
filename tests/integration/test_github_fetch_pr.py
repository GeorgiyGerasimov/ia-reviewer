import httpx
import pytest
import respx

from src.integrations.github import GitHubClient
from tests.conftest import stub_integration_settings


@pytest.fixture
def client():
    with stub_integration_settings():
        yield GitHubClient()


@respx.mock
async def test_fetch_pr_assembles_review_request(client, github_pr_response, github_files_response):
    pr_url = "https://github.com/octo/myrepo/pull/42"
    respx.get("https://api.github.com/repos/octo/myrepo/pulls/42").mock(
        return_value=httpx.Response(200, json=github_pr_response)
    )
    respx.get("https://api.github.com/repos/octo/myrepo/pulls/42/files").mock(
        return_value=httpx.Response(200, json=github_files_response)
    )

    review_request = await client.fetch_pr(pr_url)

    assert review_request.pr_url == pr_url
    assert review_request.author == "octocat"
    assert review_request.files_changed == ["src/auth/service.py", "tests/test_auth.py"]
    assert "diff --git a/src/auth/service.py" in review_request.diff
    assert "@@ -1,3 +1,3 @@" in review_request.diff


@respx.mock
async def test_post_pr_comment_returns_comment_id(client):
    pr_url = "https://github.com/octo/myrepo/pull/42"
    respx.post("https://api.github.com/repos/octo/myrepo/issues/42/comments").mock(
        return_value=httpx.Response(201, json={"id": 12345})
    )

    comment_id = await client.post_pr_comment(pr_url, "looks good")

    assert comment_id == 12345
