"""`classify_file(path)` — pure heuristic file classifier.

Used by `LLMPerFileReviewer._run_repo` to skip categories that a given
reviewer shouldn't scan. Each role declares a `SKIP_CATEGORIES` set;
files classified into one of those are dropped from `matched` before
LLM calls fire.

The classifier is intentionally heuristic (pattern-based, no LLM).
It runs against EVERY file in the snapshot, so it has to be cheap
and deterministic. False positives on the conservative side are
preferred — better to scan an "obviously a test" file than to skip
something that turns out to be production code.
"""

from src.scanners.file_classifier import FileCategory, classify_file

# ── CORE (the default — production source) ────────────────────────────


def test_src_python_module_is_core():
    assert classify_file("src/agents/validator.py") == FileCategory.CORE


def test_top_level_python_file_is_core():
    assert classify_file("main.py") == FileCategory.CORE


def test_app_lib_pkg_paths_are_core():
    assert classify_file("app/users.py") == FileCategory.CORE
    assert classify_file("lib/auth.go") == FileCategory.CORE
    assert classify_file("pkg/api/handler.rb") == FileCategory.CORE


# ── TEST (tests / specs / fixtures) ───────────────────────────────────


def test_tests_directory_python_is_test():
    assert classify_file("tests/unit/test_validator.py") == FileCategory.TEST
    assert classify_file("tests/test_api.py") == FileCategory.TEST


def test_test_suffix_files_are_test():
    """`test_*.py` and `*_test.py` are pytest conventions; `*.test.js`
    and `*.spec.ts` are js conventions; `*_test.go` is go convention.
    All classified as TEST regardless of which directory."""
    assert classify_file("test_helpers.py") == FileCategory.TEST
    assert classify_file("user_test.py") == FileCategory.TEST
    assert classify_file("src/auth.test.js") == FileCategory.TEST
    assert classify_file("src/auth.spec.ts") == FileCategory.TEST
    assert classify_file("internal/auth_test.go") == FileCategory.TEST


def test_test_directories_named_differently_are_test():
    assert classify_file("test/unit/foo.py") == FileCategory.TEST
    assert classify_file("__tests__/api.js") == FileCategory.TEST
    assert classify_file("spec/models/user_spec.rb") == FileCategory.TEST
    assert classify_file("e2e/login.test.ts") == FileCategory.TEST


def test_conftest_and_fixtures_are_test():
    assert classify_file("tests/conftest.py") == FileCategory.TEST
    assert classify_file("tests/fixtures/sample.json") == FileCategory.TEST


# ── DOCS ──────────────────────────────────────────────────────────────


def test_markdown_files_anywhere_are_docs():
    assert classify_file("README.md") == FileCategory.DOCS
    assert classify_file("docs/architecture.md") == FileCategory.DOCS
    assert classify_file("CONTRIBUTING.md") == FileCategory.DOCS


def test_docs_directory_files_are_docs():
    assert classify_file("docs/index.html") == FileCategory.DOCS
    assert classify_file("doc/api.rst") == FileCategory.DOCS
    assert classify_file("documentation/intro.adoc") == FileCategory.DOCS


def test_text_extensions_are_docs():
    assert classify_file("CHANGELOG.txt") == FileCategory.DOCS
    assert classify_file("notes/design.rst") == FileCategory.DOCS


# ── INFRA (deployment / orchestration / build config) ─────────────────


def test_dockerfile_and_compose_are_infra():
    assert classify_file("Dockerfile") == FileCategory.INFRA
    assert classify_file("Dockerfile.prod") == FileCategory.INFRA
    assert classify_file("docker-compose.yml") == FileCategory.INFRA
    assert classify_file("docker-compose.override.yml") == FileCategory.INFRA


def test_terraform_kubernetes_files_are_infra():
    assert classify_file("infra/main.tf") == FileCategory.INFRA
    assert classify_file("k8s/deployment.yaml") == FileCategory.INFRA


def test_github_workflows_are_infra():
    assert classify_file(".github/workflows/ci.yml") == FileCategory.INFRA


def test_makefile_and_nginx_conf_are_infra():
    assert classify_file("Makefile") == FileCategory.INFRA
    assert classify_file("nginx.conf") == FileCategory.INFRA


# ── VENDORED (3rd-party stuff in tree, shouldn't be reviewed) ─────────


def test_node_modules_is_vendored():
    assert classify_file("node_modules/lodash/index.js") == FileCategory.VENDORED


def test_python_venv_caches_are_vendored():
    assert classify_file(".venv/lib/python3.11/site-packages/foo.py") == FileCategory.VENDORED
    assert classify_file("__pycache__/x.pyc") == FileCategory.VENDORED


def test_build_artifact_directories_are_vendored():
    """Operators occasionally check in `dist/` / `build/` / `target/`.
    We don't review them — they're machine output, not source."""
    assert classify_file("dist/bundle.js") == FileCategory.VENDORED
    assert classify_file("build/lib/foo.py") == FileCategory.VENDORED
    assert classify_file("target/debug/main.rs") == FileCategory.VENDORED


# ── GENERATED (machine-output, in-tree) ───────────────────────────────


def test_minified_assets_are_generated():
    assert classify_file("static/app.min.js") == FileCategory.GENERATED
    assert classify_file("static/style.min.css") == FileCategory.GENERATED


def test_protobuf_outputs_are_generated():
    assert classify_file("api/proto_pb2.py") == FileCategory.GENERATED
    assert classify_file("api/service.pb.go") == FileCategory.GENERATED


def test_lockfiles_are_generated():
    """Lockfiles are dependency-resolver output. DependencyReviewer
    handles them through its OSV-scan path; LLM reviewers should
    not waste a call reading them as text."""
    assert classify_file("package-lock.json") == FileCategory.GENERATED
    assert classify_file("yarn.lock") == FileCategory.GENERATED
    assert classify_file("poetry.lock") == FileCategory.GENERATED
    assert classify_file("Cargo.lock") == FileCategory.GENERATED


# ── precedence: test-in-src wins over CORE ─────────────────────────────


def test_test_file_inside_src_classifies_as_test_not_core():
    """The test-suffix detection takes priority over the directory-based
    CORE assumption. A `src/auth/auth_test.py` is a test, not source."""
    assert classify_file("src/auth/auth_test.py") == FileCategory.TEST


def test_vendored_inside_src_takes_priority():
    """If someone vendors a 3rd-party lib INTO src/, we still skip it."""
    assert classify_file("src/vendor/third_party/foo.py") == FileCategory.VENDORED


# ── unknown / oddball paths → CORE (conservative default) ─────────────


def test_unrecognised_path_falls_back_to_core():
    """If a file doesn't match any heuristic, default to CORE.
    Better to scan it than to silently skip something that turns out
    to be production code."""
    assert classify_file("random/unexpected/file.zzz") == FileCategory.CORE
