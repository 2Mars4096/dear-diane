# 1-1: Project Pack

**Parent:** [1-dan-code-live-capability-battery](1-dan-code-live-capability-battery.md)
**Status:** completed
**Goal:** Choose the first non-website DAN Code project pack and define concrete prompts plus acceptance checks that stress different capability surfaces.

## Tasks
- [x] 1. Fix the project-selection criteria
  - [x] 1-1. Require each candidate to be local, stateful, testable, and multi-file
  - [x] 1-2. Prefer project families with objective acceptance checks over taste-driven demos
  - [x] 1-3. Keep website tasks out of the initial pack except as optional later control cases
- [x] 2. Draft the first three core projects
  - [x] 2-1. AST codemod CLI — parser-safe symbol/import rewrites with `--dry-run`, diff preview, and golden tests
  - [x] 2-2. Local job service — job submission, status, cancellation, persistence, and API tests
  - [x] 2-3. Terminal Kanban / notes TUI — keyboard flows, undo, filtering, and durable state
- [x] 3. Define stretch candidates for later rounds
  - [x] 3-1. Log triage and clustering tool
  - [x] 3-2. Local workflow/job runner with retries and resumable state
- [x] 4. Freeze the prompt briefs and acceptance checklists for the initial pack

## Decisions
- The first project pack should span tooling, backend/data, and non-web local UI rather than clustering on one domain.
- Keep the initial core pack aligned with the scenarios already exercised live: `codemodx`, `taskforge`, and `termboard`.
- Freeze the operator-visible prompt and reviewer-visible acceptance checklist as separate files so reruns stay human-authored but still comparable.

## Notes
- The pack should test more than “can it create files”: it should also test whether DAN Code preserves structure under follow-up changes and writes useful tests on its own.
- Frozen pack files live under [initial-pack](initial-pack/README.md).
- Core scenarios:
  - `codemodx`: [prompt](initial-pack/codemodx.prompt.md), [acceptance](initial-pack/codemodx.acceptance.md)
  - `taskforge`: [prompt](initial-pack/taskforge.prompt.md), [acceptance](initial-pack/taskforge.acceptance.md)
  - `termboard`: [prompt](initial-pack/termboard.prompt.md), [acceptance](initial-pack/termboard.acceptance.md)
- Stretch candidates remain future additions to the battery rather than immediate rerun targets.
