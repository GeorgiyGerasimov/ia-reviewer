"""Unit tests for the clone-based repo fetcher.

The whole-repo review used to call the GitHub REST API once per file
(`fetch_tree` + `fetch_file` × N), which routinely hit the 60 req/h
anonymous rate limit on any repo with more than a few dozen files.
Switching to a single `git clone --depth=1` eliminates the rate limit
entirely — every subsequent read is just local filesystem access.
"""

import subprocess

from src.integrations.repo_fetcher import (
    clone_repo,
    list_repo_files,
)


def test_clone_repo_creates_dir_under_settings_snapshots_dir(mocker, tmp_path):
    """The snapshot tempdir must live under `settings.SNAPSHOTS_DIR`
    (project-relative, e.g. `./snapshots/`) rather than the OS default
    `$TMPDIR`. Inside Docker containers `/tmp` is ephemeral and not
    user-visible; keeping clones inside the project makes them easy to
    inspect and to mount via a volume.
    """
    mocker.patch.object(
        __import__("src.integrations.repo_fetcher", fromlist=["settings"]).settings,
        "SNAPSHOTS_DIR",
        str(tmp_path / "snapshots"),
    )
    mkdtemp_mock = mocker.patch(
        "src.integrations.repo_fetcher.tempfile.mkdtemp",
        return_value=str(tmp_path / "snapshots" / "ia-review-xyz"),
    )
    mocker.patch(
        "src.integrations.repo_fetcher.subprocess.run",
        return_value=subprocess.CompletedProcess(args=[], returncode=0),
    )

    clone_repo("https://github.com/o/r", "main")

    mkdtemp_mock.assert_called_once()
    assert mkdtemp_mock.call_args.kwargs.get("dir") == str(tmp_path / "snapshots"), (
        f"clone_repo must pass dir=settings.SNAPSHOTS_DIR to mkdtemp; "
        f"got kwargs={mkdtemp_mock.call_args.kwargs!r}"
    )


def test_clone_repo_calls_git_with_shallow_branch(mocker, tmp_path):
    """Public repo: shallow clone, branch-pinned, no auth in URL."""
    run_mock = mocker.patch(
        "src.integrations.repo_fetcher.subprocess.run",
        return_value=subprocess.CompletedProcess(args=[], returncode=0),
    )
    # Force the destination so we can assert on it instead of mkdtemp's random name.
    target = tmp_path / "snap"
    mocker.patch("src.integrations.repo_fetcher.tempfile.mkdtemp", return_value=str(target))

    result = clone_repo("https://github.com/octo/myrepo", "main")

    assert result == target
    assert run_mock.call_count == 1
    args = run_mock.call_args.args[0]
    # argv starts with `git` and contains `clone` somewhere after the
    # `-c credential.helper=` global flag (see `test_clone_git_env.py`
    # for the dedicated assertions on the hardened env + flags).
    assert args[0] == "git"
    assert "clone" in args
    assert "--depth=1" in args
    assert "--branch=main" in args or "--branch" in args  # either form is fine
    assert "https://github.com/octo/myrepo" in args
    assert str(target) in args


def test_clone_repo_omits_branch_flag_for_head_ref(mocker, tmp_path):
    """`HEAD` isn't a real branch name — git rejects `--branch=HEAD` with
    'Remote branch HEAD not found in upstream origin'. When the user asks
    for HEAD (or omits the ref), let git clone the repo's default branch
    by dropping `--branch` entirely."""
    run_mock = mocker.patch(
        "src.integrations.repo_fetcher.subprocess.run",
        return_value=subprocess.CompletedProcess(args=[], returncode=0),
    )
    mocker.patch(
        "src.integrations.repo_fetcher.tempfile.mkdtemp",
        return_value=str(tmp_path / "snap"),
    )

    clone_repo("https://github.com/o/r", "HEAD")

    args = run_mock.call_args.args[0]
    assert not any(a.startswith("--branch") for a in args), (
        f"--branch must be omitted for HEAD; got {args!r}"
    )


def test_clone_repo_omits_branch_flag_for_empty_ref(mocker, tmp_path):
    run_mock = mocker.patch(
        "src.integrations.repo_fetcher.subprocess.run",
        return_value=subprocess.CompletedProcess(args=[], returncode=0),
    )
    mocker.patch(
        "src.integrations.repo_fetcher.tempfile.mkdtemp",
        return_value=str(tmp_path / "snap"),
    )

    clone_repo("https://github.com/o/r", "")

    args = run_mock.call_args.args[0]
    assert not any(a.startswith("--branch") for a in args)


def test_clone_repo_injects_token_for_private_repo(mocker, tmp_path):
    """When `settings.GITHUB_TOKEN` is set, embed it in the clone URL so
    private repos work without further configuration."""
    mocker.patch.object(
        __import__("src.integrations.repo_fetcher", fromlist=["settings"]).settings,
        "GITHUB_TOKEN",
        "ghp-secret",
    )
    run_mock = mocker.patch(
        "src.integrations.repo_fetcher.subprocess.run",
        return_value=subprocess.CompletedProcess(args=[], returncode=0),
    )
    mocker.patch(
        "src.integrations.repo_fetcher.tempfile.mkdtemp",
        return_value=str(tmp_path / "snap"),
    )

    clone_repo("https://github.com/octo/private", "main")

    args = run_mock.call_args.args[0]
    auth_url = next((a for a in args if a.startswith("https://")), "")
    assert "ghp-secret" in auth_url
    assert "octo/private" in auth_url


def test_list_repo_files_walks_tree_filters_size_skips_git(tmp_path):
    """Walk the snapshot, return `RepoFile(path, content="", size=...)`
    entries, drop blobs above `MAX_FILE_BYTES`, and skip `.git/`."""
    # A normal source file
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def hello(): pass\n")
    # An over-size blob — minified-bundle style
    (tmp_path / "dist").mkdir()
    big = tmp_path / "dist" / "bundle.min.js"
    big.write_text("x" * 300_000)
    # .git internals — must NOT show up
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main")
    (tmp_path / ".git" / "objects").mkdir()
    (tmp_path / ".git" / "objects" / "abc.pack").write_text("binary")

    files = list_repo_files(tmp_path)

    paths = sorted(f.path for f in files)
    assert paths == ["src/app.py"], paths
    assert all(f.content == "" for f in files), "content is fetched lazily"
    assert files[0].size > 0


def test_list_repo_files_skips_symlinks_to_outside_files(tmp_path):
    """Symlink-leak guard: if a cloned repo contains a symlink pointing
    outside the snapshot (e.g. to `/etc/shadow`, `~/.ssh/id_rsa`, host
    `.env`), `list_repo_files` MUST NOT enumerate it. Otherwise the
    reviewer downstream would read the file and ship its contents to
    the LLM gateway — a host-secret exfiltration path.

    Surfaced by the Qwen3.6-27B self-review case (2026-06-06). See
    docs/observed-quality-cases/symlink-leak-anatomy.md for the full
    attack walkthrough.
    """
    # Realistic project layout — one ordinary source file
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def hello(): pass\n")

    # Simulate a host secret OUTSIDE the snapshot
    secret_dir = tmp_path.parent / "host-secret-area"
    secret_dir.mkdir()
    host_secret = secret_dir / "shadow"
    host_secret.write_text("root:$6$weakhash$:0:0:99999:7:::\n")

    # Attacker-planted symlink INSIDE the snapshot pointing OUTSIDE
    (tmp_path / "config" / "evil").mkdir(parents=True)
    (tmp_path / "config" / "evil" / "harmless-looking.cfg").symlink_to(host_secret)

    files = list_repo_files(tmp_path)
    paths = sorted(f.path for f in files)

    # The real file is enumerated...
    assert "src/app.py" in paths
    # ...but the symlink is NOT (regardless of where it points).
    assert "config/evil/harmless-looking.cfg" not in paths, (
        f"symlink leaked into file list: {paths}"
    )


def test_list_repo_files_skips_symlinks_even_when_target_is_inside_snapshot(tmp_path):
    """Defense in depth: even when the symlink points to a file INSIDE
    the snapshot (which is technically safe), we still drop it. Two
    reasons:
      1. Avoids accidentally double-enumerating the same content under
         two different `RepoFile.path` values.
      2. A simple, blanket `skip if symlink` rule is much harder to
         get wrong than a "resolve and compare" check that has to
         handle relative paths, ../ sequences, and cross-filesystem
         edge cases.
    """
    (tmp_path / "real.py").write_text("payload")
    (tmp_path / "alias.py").symlink_to(tmp_path / "real.py")

    files = list_repo_files(tmp_path)
    paths = sorted(f.path for f in files)
    assert "real.py" in paths
    assert "alias.py" not in paths


def test_list_repo_files_handles_broken_symlinks_without_crashing(tmp_path):
    """Broken symlink (target doesn't exist) — must not raise; should
    be silently skipped same as live symlinks."""
    (tmp_path / "ok.py").write_text("ok")
    (tmp_path / "dangling.txt").symlink_to(tmp_path / "does-not-exist")

    files = list_repo_files(tmp_path)
    paths = sorted(f.path for f in files)
    assert paths == ["ok.py"]


def test_clone_repo_raises_when_git_missing(mocker, tmp_path):
    """If `git` is not on PATH, raise a clear error rather than crashing
    deep in subprocess. The app's lifespan logs a warning at startup; this
    test covers the path where someone calls clone_repo anyway."""
    mocker.patch(
        "src.integrations.repo_fetcher.subprocess.run",
        side_effect=FileNotFoundError("git not found"),
    )
    mocker.patch(
        "src.integrations.repo_fetcher.tempfile.mkdtemp",
        return_value=str(tmp_path / "snap"),
    )

    import pytest

    with pytest.raises(RuntimeError, match="git"):
        clone_repo("https://github.com/o/r", "main")
