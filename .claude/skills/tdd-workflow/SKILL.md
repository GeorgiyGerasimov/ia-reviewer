---
name: tdd-workflow
description: TDD/XP development workflow for ia-reviewer. Always write a failing test before implementation. 5-step red→green→refactor cycle with a hard stop after 10 stuck iterations. Triggers on any "add feature", "implement", "fix bug" request.
---

# TDD Workflow (XP)

**Rule:** Never write production code without a failing test written first.

## Cycle

1. **Propose** the tests that describe the desired behaviour. Get user sign-off if the change is non-trivial.
2. **Write the tests.** Run `pytest -x` — verify they fail (RED). A test that passes immediately is testing nothing.
3. **Write the minimal implementation** to make them pass. No speculative scaffolding.
4. **Run tests** — verify green (GREEN).
5. **Refactor** if needed, keeping tests green.

## Hard stop

If the red→green cycle repeats **more than 10 iterations** without the test moving from red to green:

- **Stop immediately.** Do not try another fix.
- Report to the user:
  - what the test expects
  - what the implementation actually produces
  - why further iteration isn't working
- Ask the user to choose: redesign the test, redesign the implementation, or skip.

## Why

Writing tests first ensures the implementation is testable by design, forces clear thinking about behaviour before code, and prevents shipping untested functionality.

## When the user asks for implementation directly

Redirect: propose the tests first and ask for sign-off before implementing. One sentence is enough — *"Here are the tests I'd write first — shall I go ahead?"*

## Anti-patterns

- Writing code, then writing tests to match it
- Writing tests so broad they pass with empty implementation
- Skipping the RED step ("the test will obviously fail, no need to run it")
- Saying "task complete" when only some of the proposed tests were implemented
