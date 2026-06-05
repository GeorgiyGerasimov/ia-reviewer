"""Parse npm `package-lock.json` into normalized `Dep` records.

Handles three lockfile versions:
  - v1 (npm 6): legacy nested `dependencies` tree.
  - v2 (npm 7): hybrid — both `packages` flat map AND `dependencies`
    tree for backwards compat. We prefer `packages`.
  - v3 (npm 9+): `packages`-only.

`include_dev` filters out devDependencies; both formats mark them but
v1 propagates `"dev": true` through nested children, so the flag also
prunes their transitive subtrees.
"""

import json
from pathlib import Path

from src.integrations.osv_client import Dep


def parse_package_lock(path: Path, *, include_dev: bool = True) -> list[Dep]:
    """Read `path` and return a list of `Dep(name=..., ecosystem="npm", version=...)`.

    Stable ordering is not guaranteed — callers should treat the result
    as a set if they care about exact equality (the tests do).
    """
    data = json.loads(Path(path).read_text())
    if "packages" in data:
        return _from_packages_map(data["packages"], include_dev=include_dev)
    return _from_legacy_tree(data.get("dependencies") or {}, include_dev=include_dev)


def _from_packages_map(packages: dict, *, include_dev: bool) -> list[Dep]:
    """v2/v3 lockfile: `packages` is a flat map keyed by install path.

    Root project lives under key `""` — skip it. Each entry's key is
    its install path, e.g. `node_modules/lodash` or
    `node_modules/jest/node_modules/@types/node`. The dependency NAME
    is the substring after the LAST `node_modules/`.
    """
    out: list[Dep] = []
    for install_path, entry in packages.items():
        if not install_path:
            continue  # root project
        if not include_dev and entry.get("dev"):
            continue
        version = entry.get("version")
        if not version:
            continue
        name = _name_from_install_path(install_path)
        if not name:
            continue
        out.append(Dep(name=name, ecosystem="npm", version=version))
    return out


def _name_from_install_path(install_path: str) -> str:
    """`node_modules/<name>` → `<name>`.
    `node_modules/parent/node_modules/@scope/child` → `@scope/child`."""
    marker = "node_modules/"
    idx = install_path.rfind(marker)
    if idx < 0:
        return ""
    return install_path[idx + len(marker) :]


def _from_legacy_tree(tree: dict, *, include_dev: bool) -> list[Dep]:
    """v1 lockfile: recursive `dependencies` map.

    Each child entry is `{name → {version, dev?, dependencies: {...}}}`.
    Dev-flag propagates through nested children — so if `mocha` is dev,
    its transitive `debug` is also dev. The recursion below mirrors this.
    """
    out: list[Dep] = []
    _walk_legacy_tree(tree, out, include_dev=include_dev, parent_dev=False)
    return out


def _walk_legacy_tree(
    tree: dict, out: list[Dep], *, include_dev: bool, parent_dev: bool
) -> None:
    for name, entry in (tree or {}).items():
        is_dev = parent_dev or bool(entry.get("dev"))
        if not include_dev and is_dev:
            continue
        version = entry.get("version")
        if version:
            out.append(Dep(name=name, ecosystem="npm", version=version))
        # Recurse into nested transitives, propagating dev-ness.
        _walk_legacy_tree(
            entry.get("dependencies") or {},
            out,
            include_dev=include_dev,
            parent_dev=is_dev,
        )
