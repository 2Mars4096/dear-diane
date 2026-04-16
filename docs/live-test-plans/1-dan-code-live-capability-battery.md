# 1: DAN Code Live Capability Battery

**Status:** in-progress
**Goal:** Define and run a dedicated DAN Code live-test battery for non-website projects so capability claims are grounded in real build tasks, frozen prompts, and reviewer-visible artifacts.

## Tasks
- [x] 1. Establish the live-test planning track
  - [x] 1-1. Add `docs/live-test-plans/` as a sanctioned plan location with local numbering that can restart from `1-*`
  - [x] 1-2. Seed the parent DAN Code live-test plan and child plan stubs in the new folder
- [x] 2. Define the initial project pack via [1-1-project-pack](1-1-project-pack.md)
- [x] 3. Define the scoring and artifact contract via [1-2-scoring-and-artifacts](1-2-scoring-and-artifacts.md)
- [x] 4. Run the first manual battery via [1-3-initial-manual-battery](1-3-initial-manual-battery.md)
- [ ] 5. Add follow-up change requests that measure adaptation quality via [1-4-followup-change-battery](1-4-followup-change-battery.md)
- [x] 6. Add a public coding-benchmark bridge via [1-5-swe-bench-public-track](1-5-swe-bench-public-track.md)
- [ ] 7. Exit with one boringly rerunnable live-test loop
  - [x] 7-1. Freeze the first project pack, prompts, and reviewer scorecard in repo-tracked artifacts
  - [ ] 7-2. Decide which scenarios later graduate into `tests/eval/` automation and which remain manual live tests

## Decisions
- Keep DAN-specific live capability batteries separate from public benchmarks and from implementation-phase plans.
- Reset numbering from `1-*` within `docs/live-test-plans/` instead of continuing the main `docs/plans/` sequence.
- Focus the first battery on non-website project families so DAN Code is measured on backend, tooling, persistence, and follow-up repair behavior instead of frontend polish alone.

## Notes
- Initial target families: AST codemod CLI, FastAPI + SQLite background-job service, terminal Kanban/TUI app.
- Each live-test scenario should eventually include a base build prompt, explicit acceptance checks, one follow-up change request, and archived run artifacts such as `.dan-code/session.json`, `transcript.jsonl`, per-run `events.jsonl`, final diff, tests run, and reviewer notes.
- The initial pack is now frozen under [initial-pack/README.md](initial-pack/README.md), and the reviewer contract is frozen under [reviewer-scorecard.md](reviewer-scorecard.md).
- The SWE-bench work should live in this track as a public-code benchmark bridge, not as a replacement for DAN-specific live batteries. The benchmark adds repo-native issue-resolution tasks and scorer-compatible patch artifacts, while the frozen DAN project pack still covers follow-up adaptation and reviewer-visible quality.
- The first SWE-bench automation slice is now real: `tests/eval/swebench_runner.py` prepares one public repo worktree and shells out to the product `dan code` path instead of bypassing it.
- The first live SWE-bench smoke run is also recorded. On `marshmallow-code__marshmallow-1359` with `kimi-k2.5`, DAN Code reached the right benchmark fix and executed an added validation script successfully inside the benchmark workspace, but the outer product loop still continued into another turn instead of exporting `report.json` / `predictions.jsonl`.
- The first three-run manual battery is now recorded. That initial baseline was a shared no-write stall across the first three heavier greenfield prompts, but follow-on probes refined the boundary: the same setup can complete a tiny scaffold, a local CLI app, and a small API while still stalling on broader tooling/backend tasks. See [1-3-1-three-run-stall-bug-report](1-3-1-three-run-stall-bug-report.md).
- Post-planner reruns are also now recorded. The new milestone planner does materially decompose heavy prompts into bounded first slices, but the current heavy-task failure still reproduces inside that first milestone: the worker lists the empty workspace, reads nonexistent files, and drifts into extra research instead of creating the initial project files.
- The later frozen-pack full rerun is now also recorded. `codemodx` and `taskforge` hit the new read-only finalize guardrail in live runs, `termboard` stalled one round earlier, and all three interrupted runs were still waiting inside provider completion calls before any project files were materialized.
- The post-timeout/heartbeat frozen-pack rerun is now recorded too. `codemodx` and `taskforge` no longer disappear into a silent provider wait after `tool_count=0`: live runs now emit `code.heartbeat` plus `model.timeout`, `codemodx` materializes a milestone-1 scaffold whose bundled parser tests pass, `taskforge` materializes a real FastAPI/SQLite package and begins writing pytest support files during repair, and `termboard` still fails before first file creation.
