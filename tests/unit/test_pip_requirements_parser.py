"""Parse pip `requirements*.txt` into normalized `Dep` records.

Supports the common subset of PEP 440 grammar we actually see in
real-world manifests:

  - `name==1.2.3`               → pinned version
  - `name===1.2.3`              → arbitrary-equality pin (treated as `==`)
  - `name~=1.2.3`, `>=`, `>`,
    `<=`, `<`, `!=`             → unpinned; we skip them (OSV needs an
                                  exact version)
  - comments (`#…`), blank
    lines, `-r other.txt`,
    `-e ./local`, `--hash=…`,
    `--index-url=…`             → ignored
  - environment markers
    `name==1; python_version<"3.10"` → kept (marker stripped, version
                                       still pinned)
  - extras `name[ext]==1`       → name preserved without `[ext]` for OSV

Unpinned entries (`name`, `name>=1`) are silently skipped — OSV needs an
exact version to be useful. The scanner surfaces an aggregate "N entries
unpinned, skipped" note up the stack so the reviewer can mention partial
coverage in the report.
"""

from pathlib import Path

import pytest

from src.integrations.manifests.pip import parse_requirements_txt
from src.integrations.osv_client import Dep


def _write(tmp_path: Path, content: str) -> Path:
    f = tmp_path / "requirements.txt"
    f.write_text(content)
    return f


def test_parse_requirements_pinned_versions(tmp_path):
    """`pkg==X.Y.Z` lines become `Dep(name=pkg, ecosystem=PyPI, version=X.Y.Z)`."""
    req = _write(
        tmp_path,
        "Django==4.2.7\n"
        "requests==2.31.0\n"
        "httpx==0.25.0\n",
    )
    deps = parse_requirements_txt(req)
    assert set(deps) == {
        Dep(name="Django", ecosystem="PyPI", version="4.2.7"),
        Dep(name="requests", ecosystem="PyPI", version="2.31.0"),
        Dep(name="httpx", ecosystem="PyPI", version="0.25.0"),
    }


def test_parse_requirements_skips_comments_blank_lines_and_options(tmp_path):
    """Comments / blanks / `-r other.txt` / `-e ./pkg` / `--…` flags are dropped."""
    req = _write(
        tmp_path,
        "# top comment\n"
        "\n"
        "  \n"
        "-r base.txt\n"
        "--index-url https://pypi.org/simple\n"
        "-e ./local-pkg\n"
        "Django==4.2.7  # pinned for CVE fix\n",
    )
    deps = parse_requirements_txt(req)
    assert deps == [Dep(name="Django", ecosystem="PyPI", version="4.2.7")]


def test_parse_requirements_skips_unpinned_ranges(tmp_path):
    """`>=`, `>`, `<=`, `<`, `~=`, `!=`, and bare `name` lines are skipped
    — OSV needs an exact version to give a useful answer."""
    req = _write(
        tmp_path,
        "Django>=4.0\n"
        "requests\n"
        "httpx<1.0\n"
        "flask~=3.0\n"
        "pytest!=8.0.0\n"
        "fastapi==0.110.0\n",
    )
    deps = parse_requirements_txt(req)
    # Only the strictly-pinned entry survives.
    assert deps == [Dep(name="fastapi", ecosystem="PyPI", version="0.110.0")]


def test_parse_requirements_accepts_arbitrary_equality(tmp_path):
    """`name===1.2.3` (PEP 440 arbitrary equality) is treated as `==`."""
    req = _write(tmp_path, "boto3===1.34.0\n")
    deps = parse_requirements_txt(req)
    assert deps == [Dep(name="boto3", ecosystem="PyPI", version="1.34.0")]


def test_parse_requirements_strips_extras_keeps_base_name(tmp_path):
    """`uvicorn[standard]==0.27.0` → `Dep(name=uvicorn, version=0.27.0)`.

    OSV indexes the base distribution, not the extras-suffixed alias.
    """
    req = _write(tmp_path, "uvicorn[standard]==0.27.0\n")
    deps = parse_requirements_txt(req)
    assert deps == [Dep(name="uvicorn", ecosystem="PyPI", version="0.27.0")]


def test_parse_requirements_strips_environment_marker(tmp_path):
    """`pkg==1.2.3; python_version<"3.10"` keeps the pin, drops the marker."""
    req = _write(
        tmp_path,
        'tomli==2.0.1; python_version<"3.11"\n',
    )
    deps = parse_requirements_txt(req)
    assert deps == [Dep(name="tomli", ecosystem="PyPI", version="2.0.1")]


def test_parse_requirements_normalises_pep503_name(tmp_path):
    """PEP 503 normalisation: underscore / mixed-case names are surfaced
    canonically so duplicates dedup. `Pillow_PIL` → `pillow-pil`. We do
    NOT lowercase aggressively because OSV is case-insensitive but our
    Dep equality is by exact string — keeping the source spelling is the
    least-surprising default. Tested here just as a regression guard for
    that decision."""
    req = _write(tmp_path, "Pillow==10.0.0\n")
    deps = parse_requirements_txt(req)
    # Source spelling preserved — OSV's API doesn't care about case.
    assert deps[0].name == "Pillow"


def test_parse_requirements_ignores_hash_continuations(tmp_path):
    """`pkg==1.2.3 \\` followed by `--hash=sha256:…` lines must NOT confuse
    the parser. The hash lines are continuations of the previous one in
    pip's syntax (after a trailing backslash) — we treat them as a
    separate, ignored token."""
    req = _write(
        tmp_path,
        "requests==2.31.0 \\\n"
        "    --hash=sha256:aaaaaaaa \\\n"
        "    --hash=sha256:bbbbbbbb\n"
        "Django==4.2.7\n",
    )
    deps = parse_requirements_txt(req)
    assert set(deps) == {
        Dep(name="requests", ecosystem="PyPI", version="2.31.0"),
        Dep(name="Django", ecosystem="PyPI", version="4.2.7"),
    }


def test_parse_requirements_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_requirements_txt(tmp_path / "does-not-exist.txt")
