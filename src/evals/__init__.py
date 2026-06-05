"""LLM-as-judge evals — measure the quality of agent output by asking
another LLM to score it against a fixed rubric.

Distinct from `tests/` (programmatic asserts) and `benchmarks/`
(success-rate over known input/expected pairs):

  * `tests/`     — gates: pass/fail on exact-match assertions.
  * `benchmarks/` — meters: how often does the agent reach the right
                    answer across N cases?
  * `evals/`     — judges: when there IS no single right answer (free
                    text, narrative reports), is the output's *quality*
                    above the bar?

The judge runs offline against finished review reports — it does NOT
participate in production review traffic.
"""
