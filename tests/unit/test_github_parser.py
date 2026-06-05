import pytest

from src.integrations.github import GitHubClient
from tests.conftest import stub_integration_settings


@pytest.fixture
def client():
    with stub_integration_settings():
        yield GitHubClient()


def test_parse_simple_url(client):
    owner_repo, number = client._parse_pr_url("https://github.com/octo/myrepo/pull/42")
    assert owner_repo == "octo/myrepo"
    assert number == "42"


def test_parse_enterprise_url(client):
    owner_repo, number = client._parse_pr_url("https://github.enterprise.com/group/repo/pull/100")
    assert owner_repo == "group/repo"
    assert number == "100"


def test_parse_trailing_slash(client):
    owner_repo, number = client._parse_pr_url("https://github.com/o/r/pull/3/")
    assert owner_repo == "o/r"
    assert number == "3"


def test_parse_url_with_files_suffix(client):
    owner_repo, number = client._parse_pr_url("https://github.com/octo/repo/pull/9/files")
    assert owner_repo == "octo/repo"
    assert number == "9"


def test_parse_rejects_missing_pull_segment(client):
    with pytest.raises(ValueError, match="missing 'pull' segment"):
        client._parse_pr_url("https://github.com/octo/repo/issues/1")


def test_parse_rejects_non_numeric_number(client):
    with pytest.raises(ValueError, match="must be numeric"):
        client._parse_pr_url("https://github.com/octo/repo/pull/main")
