"""SSRF / token-leak guard: `clone_repo` must refuse any URL whose host
is not in the allowlist, and `_inject_token` must NEVER embed the
GitHub PAT into a URL pointing at a non-allowlist host.

Threat model (review finding PR1.1): if `normalize_repo_url` accepted
`https://attacker.com/owner/repo`, `_inject_token` would push
`https://<TOKEN>@attacker.com/...` and `git clone` would send the
GitHub token as basic-auth to a third party.

Two layers of defence:
  * `normalize_repo_url` rejects bad hosts up front (primary).
  * `_inject_token` refuses to embed when host is not in the allowlist
    (defence in depth — catches bugs in any future code path that
    skips normalisation).

Allowlist source: `settings.GITHUB_ALLOWED_HOSTS` (csv, default
`github.com`). Operators on GitHub Enterprise add their host:
  GITHUB_ALLOWED_HOSTS=github.com,ghe.corp.example
"""

import pytest

from src.integrations.repo_fetcher import _inject_token, normalize_repo_url

# ── normalize_repo_url: host allowlist ─────────────────────────────────


def test_normalize_accepts_github_dot_com():
    url, _ = normalize_repo_url("https://github.com/octo/myrepo")
    assert url == "https://github.com/octo/myrepo"


def test_normalize_rejects_arbitrary_host():
    """The whole point of this PR: a non-allowlist host must never
    reach `git clone`."""
    with pytest.raises(ValueError, match=r"host.*not allowed"):
        normalize_repo_url("https://attacker.com/owner/repo")


def test_normalize_rejects_internal_ip():
    """Private-range hosts are common SSRF targets (cloud metadata
    services, internal admin panels). The host-allowlist check rejects
    them by string match, no DNS resolution needed."""
    with pytest.raises(ValueError, match=r"host.*not allowed"):
        normalize_repo_url("https://10.0.0.1/owner/repo")


def test_normalize_rejects_localhost():
    with pytest.raises(ValueError, match=r"host.*not allowed"):
        normalize_repo_url("https://localhost/owner/repo")


def test_normalize_is_case_insensitive_on_host():
    """`Github.COM` and `github.com` should resolve identically — case
    in DNS is irrelevant, and we don't want trivial bypass."""
    url, _ = normalize_repo_url("https://Github.COM/octo/myrepo")
    # Lower-cased back to the canonical form
    assert url.startswith("https://github.com/") or url.startswith("https://Github.COM/")


def test_normalize_honours_settings_allowlist(monkeypatch):
    """Operators on GitHub Enterprise can add their host via env.
    `settings.GITHUB_ALLOWED_HOSTS` is parsed as a csv list."""
    from src.utils import config as cfg

    monkeypatch.setattr(
        cfg.settings, "GITHUB_ALLOWED_HOSTS", ["github.com", "ghe.corp.example"]
    )
    # Enterprise host now passes…
    url, _ = normalize_repo_url("https://ghe.corp.example/owner/repo")
    assert "ghe.corp.example" in url
    # …but anything still outside the allowlist fails
    with pytest.raises(ValueError, match=r"host.*not allowed"):
        normalize_repo_url("https://gitlab.com/owner/repo")


# ── _inject_token: defence-in-depth (never embed for foreign hosts) ────


def test_inject_token_embeds_for_allowed_host():
    """The normal case: PAT goes into the URL as basic-auth so git can
    fetch a private repo."""
    out = _inject_token("https://github.com/o/r", token="ghp_xxx")
    assert "ghp_xxx@github.com" in out


def test_inject_token_refuses_foreign_host():
    """Even if a caller somehow skipped `normalize_repo_url`, the token
    must NOT be embedded into a URL for a non-allowlist host. This is
    the defence-in-depth layer.
    """
    out = _inject_token("https://attacker.com/o/r", token="ghp_xxx")
    # Output is the URL UNCHANGED — no token leak.
    assert "ghp_xxx" not in out
    assert out == "https://attacker.com/o/r"


def test_inject_token_returns_unchanged_when_no_token():
    """No token configured → no change, no error."""
    assert _inject_token("https://github.com/o/r", token="") == "https://github.com/o/r"
