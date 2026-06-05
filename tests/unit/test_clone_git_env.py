"""`clone_repo` must run `git` with a hardened env + flags.

Two guardrails this test pins (review finding PR1.2):

1. `GIT_TERMINAL_PROMPT=0` — without it git INTERACTIVELY prompts for
   credentials when auth fails. In our subprocess context that means the
   call hangs until the 120s timeout fires, the user has no idea why,
   and ops have no signal in logs. Setting the env var makes auth
   failures fast and noisy ("could not read Username for 'https://…'").

2. `-c credential.helper=` (empty) — disables ALL credential helpers
   for this one process. Otherwise git on macOS hands the URL+token to
   the osxkeychain helper which can persist the credentials to the
   user keychain. We pass the token as basic-auth in the URL on purpose;
   we do NOT want it written to disk.

We assert on the call args of the mocked `subprocess.run` — no real
git invocation.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch

from src.integrations import repo_fetcher
from src.integrations.repo_fetcher import clone_repo


def _fake_completed(cmd, dest_dir: Path) -> subprocess.CompletedProcess:
    """Mimic a successful `git clone`: create the destination so callers
    that walk it (or call `cleanup_snapshot`) don't blow up."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")


@patch.object(repo_fetcher.subprocess, "run")
def test_clone_sets_non_interactive_env(mock_run, tmp_path, monkeypatch):
    """`subprocess.run` must be called with an env dict carrying
    `GIT_TERMINAL_PROMPT=0`."""
    monkeypatch.setattr(repo_fetcher.settings, "SNAPSHOTS_DIR", str(tmp_path))
    mock_run.side_effect = lambda cmd, **kw: _fake_completed(cmd, Path(cmd[-1]))

    clone_repo("https://github.com/o/r", "main")

    # Inspect the kwargs of the call
    assert mock_run.call_count == 1
    kwargs = mock_run.call_args.kwargs
    env = kwargs.get("env")
    assert env is not None, "clone_repo must pass an explicit `env=` to subprocess.run"
    assert env.get("GIT_TERMINAL_PROMPT") == "0", (
        f"GIT_TERMINAL_PROMPT must be set to '0' so git fails fast on auth "
        f"problems instead of hanging on a prompt. Got: {env.get('GIT_TERMINAL_PROMPT')!r}"
    )


@patch.object(repo_fetcher.subprocess, "run")
def test_clone_disables_credential_helper(mock_run, tmp_path, monkeypatch):
    """`git clone` argv must contain `-c credential.helper=` (empty value
    after the equals sign) so the system keychain helper doesn't pocket
    the token we pass as basic-auth."""
    monkeypatch.setattr(repo_fetcher.settings, "SNAPSHOTS_DIR", str(tmp_path))
    mock_run.side_effect = lambda cmd, **kw: _fake_completed(cmd, Path(cmd[-1]))

    clone_repo("https://github.com/o/r", "main")

    [(args, _kwargs)] = mock_run.call_args_list
    argv = list(args[0])
    # Look for the `-c credential.helper=` pair anywhere in argv.
    has_helper_disable = any(
        argv[i] == "-c" and argv[i + 1] == "credential.helper="
        for i in range(len(argv) - 1)
    )
    assert has_helper_disable, (
        f"git argv must include `-c credential.helper=` to prevent the "
        f"system keychain from saving the embedded token. Got: {argv!r}"
    )


@patch.object(repo_fetcher.subprocess, "run")
def test_clone_passes_existing_path_env(mock_run, tmp_path, monkeypatch):
    """The subprocess still needs `PATH` (otherwise it can't find `git`
    itself) — we set GIT_TERMINAL_PROMPT but don't blow away the rest
    of the env."""
    monkeypatch.setattr(repo_fetcher.settings, "SNAPSHOTS_DIR", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin")
    mock_run.side_effect = lambda cmd, **kw: _fake_completed(cmd, Path(cmd[-1]))

    clone_repo("https://github.com/o/r", "main")

    env = mock_run.call_args.kwargs["env"]
    assert env.get("PATH"), "subprocess env must inherit PATH so `git` is findable"


@patch.object(repo_fetcher.subprocess, "run")
def test_clone_credential_disable_appears_before_clone_keyword(mock_run, tmp_path, monkeypatch):
    """`-c <key>=<val>` must appear BEFORE the `clone` subcommand —
    git's `-c` is a global flag, not a clone-specific one. Putting it
    after `clone` makes git silently ignore it."""
    monkeypatch.setattr(repo_fetcher.settings, "SNAPSHOTS_DIR", str(tmp_path))
    mock_run.side_effect = lambda cmd, **kw: _fake_completed(cmd, Path(cmd[-1]))

    clone_repo("https://github.com/o/r", "main")

    argv = list(mock_run.call_args.args[0])
    # Find positions
    clone_idx = argv.index("clone")
    c_idx = argv.index("-c") if "-c" in argv else len(argv)
    assert c_idx < clone_idx, (
        f"`-c credential.helper=` must come BEFORE `clone` for git to honour "
        f"it. argv: {argv!r}"
    )
