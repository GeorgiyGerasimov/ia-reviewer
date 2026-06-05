"""Benchmark suite for ia-reviewer.

The benchmark layer is intentionally separate from `tests/` — its purpose
is to **measure agent accuracy** across a fixed corpus of input/expected
pairs, producing a single success-rate number. Tests are pass/fail
gatekeepers; benchmarks are a meter.

Two suites today:
  * `validator` — 14 cases covering the seven validator categories,
    mix of pure-code prefilter rules and LLM-judge cases. Use
    `--mock-llm` to measure the pure-code coverage alone (no network).
  * `dependency` — 12 cases covering OSV-mocked manifest scans across
    npm v1/v2 lockfiles, pip requirements.txt (including extras +
    strict pins), mixed ecosystems, unsupported manifests, corrupt JSON,
    and severity edge-cases. OSV is always mocked inline so this suite
    is 100% deterministic.

CLI: `python -m benchmarks.run [validator | dependency | all] [--mock-llm]`.
"""
