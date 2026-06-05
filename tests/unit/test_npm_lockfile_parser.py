"""Parse `package-lock.json` (npm) into normalized `Dep` records that can
be fed to `OSVClient.query`.

Supports v1 (legacy `dependencies` tree, npm 6) and v2/v3 (flat `packages`
map, npm 7+). The root entry (key `""`) is the project itself and is
skipped — we only want third-party deps. Transitive deps living under
`node_modules/<parent>/node_modules/<child>` are included.

`include_dev=False` excludes dev-dependencies. The v2 schema marks them
via `"dev": true` on the package entry; v1 propagates `"dev": true`
through the `dependencies` tree.
"""

from pathlib import Path

from src.integrations.manifests.npm import parse_package_lock
from src.integrations.osv_client import Dep

FIXTURES = Path(__file__).parent.parent / "fixtures" / "npm_lockfiles"


def test_parse_package_lock_v2_extracts_resolved_versions():
    """v2 lockfile: parse the `packages` map; root ("") is skipped."""
    deps = parse_package_lock(FIXTURES / "package-lock.v2.json")

    # All four third-party packages — root "" must NOT appear.
    expected = {
        Dep(name="lodash", ecosystem="npm", version="4.17.15"),
        Dep(name="express", ecosystem="npm", version="4.18.2"),
        Dep(name="jest", ecosystem="npm", version="29.5.0"),
        Dep(name="@types/node", ecosystem="npm", version="20.0.0"),
    }
    assert set(deps) == expected


def test_parse_package_lock_v1_legacy_dependencies_block():
    """v1 lockfile: parse the nested `dependencies` tree; transitives
    under a parent's `dependencies` are flattened into the result."""
    deps = parse_package_lock(FIXTURES / "package-lock.v1.json")

    expected = {
        Dep(name="lodash", ecosystem="npm", version="4.17.11"),
        Dep(name="minimist", ecosystem="npm", version="1.2.0"),
        Dep(name="mocha", ecosystem="npm", version="5.2.0"),
        Dep(name="debug", ecosystem="npm", version="3.1.0"),
    }
    assert set(deps) == expected


def test_parse_package_lock_skips_dev_when_include_dev_false():
    """`include_dev=False` drops dev-deps in both v1 and v2 formats."""
    deps_v2 = parse_package_lock(FIXTURES / "package-lock.v2.json", include_dev=False)
    names_v2 = {d.name for d in deps_v2}
    assert "jest" not in names_v2
    assert "@types/node" not in names_v2  # transitively dev
    assert {"lodash", "express"} <= names_v2

    deps_v1 = parse_package_lock(FIXTURES / "package-lock.v1.json", include_dev=False)
    names_v1 = {d.name for d in deps_v1}
    assert "mocha" not in names_v1
    assert "debug" not in names_v1  # transitively dev via mocha
    assert {"lodash", "minimist"} <= names_v1
