# 25-11: Fallback And Completion Policy

**Parent:** [25-chat-control-plane](25-chat-control-plane.md)
**Status:** completed
**Goal:** Encode the "never just stop" contract as runtime policy so DAN always moves the user closer to done even when the direct path is blocked.

## Tasks
- [x] 1. Define the fallback ladder
  - [x] 1-1. Try an alternate path to the same goal first.
  - [x] 1-2. If full completion is blocked, deliver a useful subset of the task.
  - [x] 1-3. If needed, build the missing capability on the fly with a script, composed tool path, or temporary workflow.
  - [x] 1-4. If human action is unavoidable, scaffold everything around it so the user does the minimum possible work.
  - [x] 1-5. Only then ask for the smallest required unblock.
- [x] 2. Define terminal outcomes
  - [x] 2-1. Standardize `done`, `done_with_assumptions`, `partial_done_with_next_unlock`, and `waiting_on_single_user_action`.
  - [x] 2-2. Prevent generic apology-only or limitation-only endings.
  - [x] 2-3. Require final user-facing output to name what DAN did and what remains, if anything.
- [x] 3. Integrate fallback policy with solver execution
  - [x] 3-1. Make `SolverDecision` carry a `fallback_chain`.
  - [x] 3-2. Let execution backends report when they failed directly so policy can advance to the next fallback.
  - [x] 3-3. Keep destructive/safety policy from `25-7` intact while still preferring forward progress.
- [x] 4. Add reflection and learning hooks
  - [x] 4-1. Record which fallback path succeeded so future planning can prefer it.
  - [x] 4-2. Note repeated unblock requirements as candidates for new tools or workflows.
  - [x] 4-3. Track user corrections when DAN picks a useful default but needs course correction.
- [x] 5. Add tests and acceptance coverage
  - [x] 5-1. Regression tests for blocked direct path → alternate path.
  - [x] 5-2. Regression tests for partial delivery with explicit remaining steps.
  - [x] 5-3. Regression tests ensuring the system never ends with a bare "I can't" style response.

## Decisions
- "Helpful partial completion" is better than stalling for perfect certainty.
- The user should always receive either a result or the smallest meaningful unblock.
- Fallback behavior is runtime policy, not just prompt wording.

## Files

| File | Action |
|---|---|
| `src/dan/server/concierge/policy.py` | Modify — terminal-outcome validation lives here; 28-5 later removed the unused fallback-helper trio while keeping the active validation guardrail |
| `src/dan/server/concierge/solver.py` | Modify — ensure `SolverDecision` always populates `fallback_chain` |
| `src/dan/server/concierge/executor.py` | Modify — add fallback advancement logic when backends report direct failure |
| `tests/test_concierge/test_fallback_policy.py` | Create — originally planned fallback/terminal-outcome tests; current focused regressions live under `tests/test_server/` after later cleanup |

## Dependencies

- **25-7 (Behavior Policy)** provides safety/queue/progress/promotion infrastructure.
- **25-8 (Solver Runtime)** provides planning and decision structures where fallback is defined.
- **25-10 (Execution Selector)** provides backend execution where fallback is enacted.

## Acceptance Criteria

- When a direct path fails, the system advances to the next fallback in the ladder.
- Terminal outcomes are always one of: `done`, `done_with_assumptions`, `partial_done_with_next_unlock`, `waiting_on_single_user_action`.
- Generic limitation or apology-only responses are prevented by policy enforcement.
- Partial completions clearly state what was delivered and what remains to unblock.

## Notes

- This plan complements `25-7` rather than replacing it: `25-7` covers safety/queue/progress/promotion; this plan covers productive failure handling and completion semantics.
- The final runtime should always move the task forward, even under tool or access limitations.
- Fallback success paths should be recorded so future planning can prefer them.
- 2026-03-10 note: `28-5` later removed the unused `FALLBACK_LADDER`, `suggest_fallback_strategy()`, and `format_terminal_message()` helpers from `policy.py`. The live runtime still keeps fallback behavior in `solver.py`/`executor.py` plus the active `validate_terminal_content()` guardrail.
