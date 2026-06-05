"""Validation + canonicalisation of repo URLs before cloning.

Users routinely paste URLs straight from the browser address bar — those
contain extra path segments (`/tree/<ref>`, `/blob/<ref>/<file>`),
trailing slashes, `.git` suffixes, query strings, and fragments. Feeding
those to `git clone` either fails outright or clones the wrong branch.

`normalize_repo_url(raw)` returns `(canonical_url, optional_ref)`:
  - canonical_url is always `https://<host>/<owner>/<repo>` (no extras)
  - optional_ref is the branch/sha extracted from `/tree/<ref>` /
    `/blob/<ref>/...` / `/commits/<ref>...` if present, else None
  - raises ValueError on malformed input (no scheme, missing owner/repo)
"""

import pytest

from src.integrations.repo_fetcher import normalize_repo_url


def test_strips_trailing_slash():
    url, ref = normalize_repo_url("https://github.com/octo/myrepo/")
    assert url == "https://github.com/octo/myrepo"
    assert ref is None


def test_strips_dot_git_suffix():
    url, ref = normalize_repo_url("https://github.com/octo/myrepo.git")
    assert url == "https://github.com/octo/myrepo"
    assert ref is None


def test_extracts_tree_ref_and_canonicalizes():
    url, ref = normalize_repo_url("https://github.com/octo/myrepo/tree/develop")
    assert url == "https://github.com/octo/myrepo"
    assert ref == "develop"


def test_extracts_blob_ref_dropping_file_path():
    url, ref = normalize_repo_url("https://github.com/octo/myrepo/blob/main/src/app.py")
    assert url == "https://github.com/octo/myrepo"
    assert ref == "main"


def test_strips_query_and_fragment():
    url, ref = normalize_repo_url("https://github.com/octo/myrepo?foo=1#section")
    assert url == "https://github.com/octo/myrepo"
    assert ref is None


def test_rejects_non_http_or_missing_segments():
    with pytest.raises(ValueError):
        normalize_repo_url("not-a-url")
    with pytest.raises(ValueError):
        normalize_repo_url("")
    with pytest.raises(ValueError):
        normalize_repo_url("https://github.com/owner-only")
    with pytest.raises(ValueError):
        normalize_repo_url("ftp://github.com/o/r")


def test_preserves_enterprise_host_when_in_allowlist(monkeypatch):
    """GitHub Enterprise host passes when explicitly added to
    `GITHUB_ALLOWED_HOSTS`. Without the allowlist entry the same URL is
    rejected as SSRF — see `test_repo_host_allowlist.py` for the full
    set of allowlist tests."""
    from src.utils import config as cfg

    monkeypatch.setattr(
        cfg.settings, "GITHUB_ALLOWED_HOSTS",
        ["github.com", "github.enterprise.com"],
    )
    url, _ = normalize_repo_url("https://github.enterprise.com/group/svc.git")
    assert url == "https://github.enterprise.com/group/svc"
