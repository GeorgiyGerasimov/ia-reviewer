"""Parse pip-flavoured `requirements*.txt` into normalized `Dep` records.

Scope intentionally narrow — only the PEP 440 forms we actually see in
real-world dependency manifests:

  - `pkg==X.Y.Z` and `pkg===X.Y.Z`   → pinned, surfaced as a `Dep`
  - `pkg>=…`, `pkg<…`, `pkg~=…`,
    `pkg!=…`, bare `pkg`              → unpinned, silently skipped
  - extras (`uvicorn[standard]==…`)   → keep base name, drop the extras
  - environment markers (`; python_version<"3.10"`) → marker stripped
  - comments, blank lines, `-r/-e`,
    `--index-url`, `--hash=…`         → ignored

OSV needs an exact version to answer usefully; unpinned entries get a
"skipped" aggregate the scanner surfaces in its summary instead of being
queried.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.integrations.osv_client import Dep

# Strict-equality only: PEP 440 `==` or `===`. Everything else is unpinned.
# Captures the canonical (extras-stripped) name and the version literal.
#
#   group 1 = name (e.g. `uvicorn` from `uvicorn[standard]==…`)
#   group 2 = version (e.g. `0.27.0`)
_PINNED_RE = re.compile(
    r"""
    ^\s*
    (?P<name>[A-Za-z0-9_.\-]+)        # PEP 503-permissive distribution name
    (?:\[[^\]]*\])?                   # optional `[extras]` — discarded
    \s*===?\s*                        # `==` or `===`
    (?P<version>[^\s;#]+)             # version up to space / `;` marker / `#` comment
    """,
    re.VERBOSE,
)


def parse_requirements_txt(path: Path) -> list[Dep]:
    """Return all strictly-pinned dependencies from a `requirements*.txt`.

    Raises `FileNotFoundError` (intentional — the scanner already filters
    by the on-disk file list, so a missing path is a programmer error,
    not a normal flow).
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    deps: list[Dep] = []
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()  # strip inline comments
        if not line.strip():
            continue
        if line.lstrip().startswith(("-", "--")):
            # `-r other.txt`, `-e ./local`, `--index-url=…`, `--hash=…`, etc.
            continue
        match = _PINNED_RE.match(line)
        if not match:
            continue  # unpinned / range / unsupported form
        deps.append(
            Dep(
                name=match.group("name"),
                ecosystem="PyPI",
                version=match.group("version"),
            )
        )
    return deps
