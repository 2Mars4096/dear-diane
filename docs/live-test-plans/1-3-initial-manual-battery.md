# 1-3: Initial Manual Battery

**Parent:** [1-dan-code-live-capability-battery](1-dan-code-live-capability-battery.md)
**Status:** completed
**Goal:** Run the first small manual DAN Code battery across three non-website projects and record a baseline before the track expands.

## Tasks
- [x] 1. Freeze the initial run configuration
  - [x] 1-1. Keep the first battery on the resolved DAN Code model (`kimi-k2.5`) with isolated `/tmp` workspaces and `--max-tool-rounds 6`
  - [x] 1-2. Use human prompts plus separate reviewer acceptance criteria, then inspect the resulting workspace and `events.jsonl` log for each run
- [x] 2. Run the first three-project battery
  - [x] 2-1. One easy scenario: `codemodx` AST codemod CLI
  - [x] 2-2. One medium scenario: `taskforge` local job service
  - [x] 2-3. One hard scenario: `termboard` terminal Kanban app
- [x] 3. Capture the baseline artifacts and reviewer scores for each run
- [x] 4. Summarize the first-pass findings, failure taxonomy, and obvious next fixes via [1-3-1-three-run-stall-bug-report](1-3-1-three-run-stall-bug-report.md)

## Decisions
- Start with three scenarios, not ten, so the first baseline is tractable and actually reviewable.
- Use isolated `/tmp` workspaces for fair live runs after a repo-local nested workspace proved vulnerable to parent-repo git-status contamination.
- Treat "no project files created before timeout" as a DAN Code runtime/product failure, not just a quality miss on the requested project.

## Notes
- The first battery should be manual and observer-heavy on purpose; automation can come after the acceptance contract stabilizes.
- The initial three isolated human-prompt runs all failed before producing any project files beyond the prompt/checklist inputs and `.dan-code` event logs.
- Follow-on probes later refined that baseline: DAN Code succeeded on a tiny scaffold, a local CLI todo app, and a small notes API in the same isolated `/tmp` setup, while heavier tooling/backend prompts still reproduced the stall.
- The shared first-pass failure mode and the later refined boundary are captured in [1-3-1-three-run-stall-bug-report](1-3-1-three-run-stall-bug-report.md).
