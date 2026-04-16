# Reviewer Scorecard

Frozen scoring and artifact contract for the first DAN Code live capability battery.

## Scoring Axes

Score each axis from `0` to `3`.

- `correctness`
  - `0`: did not materially implement the requested project
  - `1`: partial scaffold only, key acceptance checks missing
  - `2`: core workflow works, but meaningful gaps remain
  - `3`: requested workflow and reviewer acceptance checks are convincingly met
- `test_quality`
  - `0`: no meaningful tests or only placeholder coverage
  - `1`: weak smoke coverage with obvious blind spots
  - `2`: relevant tests cover the core path
  - `3`: tests cover core behavior plus realistic edge cases or regressions
- `edit_precision`
  - `0`: sprawling or confused output, or repeated rebuilds without a coherent structure
  - `1`: structure exists, but the diff feels noisy or unfocused
  - `2`: output is mostly bounded and intentional
  - `3`: output is cleanly scoped, modular, and avoids collateral churn
- `recovery_quality`
  - `0`: follow-up repair/adaptation failed or regressed the project badly
  - `1`: follow-up partially landed but regressed existing behavior or rewrote too broadly
  - `2`: follow-up landed with limited collateral damage
  - `3`: follow-up landed cleanly with relevant regression coverage

## Required Artifacts Per Run

- Frozen prompt brief and acceptance checklist used for the scenario
- Workspace path and run timestamp
- Environment metadata:
  - DAN Code command surface
  - resolved model
  - tool-round/tool-call limits
  - approval mode if non-default
- `.dan-code/session.json` when present
- `.dan-code/transcript.jsonl` when present
- `.dan-code/runs/turn-XX/events.jsonl`
- Final workspace tree or a focused file inventory
- Final diff or explicit note that no project files were materialized
- Tests and smoke checks actually run, with outcomes
- Reviewer notes with acceptance outcomes and failure taxonomy

## Scenario Status Gates

- `pass`
  - Project materializes beyond `prompt.md` / `acceptance.md`
  - At least one meaningful verification command runs
  - Most explicit acceptance checks pass
- `partial`
  - Project materializes, but acceptance is incomplete or verification is weak
- `fail`
  - No meaningful project files are created, or the result is too incomplete to verify honestly

## Battery Roll-Up

- `strong`
  - At least `2/3` scenarios are `pass`
  - No more than `1/3` is `fail`
- `mixed`
  - At least one scenario is `pass` or `partial`, but the pack is not yet strong
- `blocked`
  - `2/3` or more scenarios are `fail`, or the same structural runtime failure dominates the pack

## Minimum Comparison Bundle

Two runs are only comparable when both include:

- the same frozen prompt and acceptance files
- the same scenario name and workspace shape
- the resolved model and DAN Code budget settings
- the run event log
- the final workspace outcome
- at least one reviewer verification note

If any of those are missing, treat the comparison as anecdotal rather than scored.
