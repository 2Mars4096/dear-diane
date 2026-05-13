# 58-3: Dependency and Conflict Scheduler

**Parent:** [58-async-core-and-background-runtime](58-async-core-and-background-runtime.md)
**Status:** not-started
**Goal:** Start with conservative dependency/conflict scheduling for the first nonblocking proof: use explicit paths, owned paths, workspace roots, and broad resource locks to decide `start_parallel`, `queue_after`, `append_to_active`, or `ask_clarification`; defer richer semantic/model scheduling until that proof is green.

## Tasks
- [ ] 1. Define MVP relation labels
  - [ ] 1-1. `independent`: no required upstream artifacts and no overlapping owned paths/resources.
  - [ ] 1-2. `dependent_or_conflicting`: needs output from, or overlaps paths/resources with, an active/queued task.
  - [ ] 1-3. `conflicting`: may mutate overlapping files/resources or incompatible external state.
  - [ ] 1-4. `continuation`: semantically modifies the active objective and should append or queue-after.
  - [ ] 1-5. `unclear`: insufficient evidence; ask clarification or run a read-only planner.
- [ ] 2. Build deterministic dependency evidence
  - [ ] 2-1. Compare explicit target paths, artifact refs, mentioned filenames, and workspace roots.
  - [ ] 2-2. Use active run owned paths, planned deliverables, and current lock records.
  - [ ] 2-3. Treat missing target-path evidence as `unclear` unless the user explicitly says "new/separate task".
  - [ ] 2-4. Treat external side effects and shell commands as broad resources unless a narrower policy exists.
- [ ] 3. Add only enough classification for the proof
  - [ ] 3-1. Prefer explicit `/new` / `/append` / `/status` style commands over inference.
  - [ ] 3-2. If target paths are disjoint, admit `start_parallel` under capacity limits.
  - [ ] 3-3. If target paths overlap, admit `queue_after` with conflict reason.
  - [ ] 3-4. If relation cannot be determined cheaply, ask one clarification.
- [ ] 4. Select scheduling action
  - [ ] 4-1. `start_parallel` for independent work under capacity limits.
  - [ ] 4-2. `queue_after` for dependent work.
  - [ ] 4-3. `append_to_active` for explicit active-run steering.
  - [ ] 4-4. `clarify` when relation is ambiguous and the cost of guessing is high.
  - [ ] 4-5. Defer `isolate_parallel` until the basic board/background proof works.
- [ ] 5. Add regressions
  - [ ] 5-1. Two scripts targeting different files can run in parallel.
  - [ ] 5-2. A follow-up that says "also add that output to the first script" queues behind the first script.
  - [ ] 5-3. Two runs targeting the same file do not write concurrently in the main workspace.
  - [ ] 5-4. Low-confidence relation classification asks a clarification rather than appending silently.
- [ ] 6. Add richer scheduling after the proof
  - [ ] 6-1. Use readiness capsules and claimed artifacts from Plan 55 when available.
  - [ ] 6-2. Ask a small no-tool classifier only after deterministic evidence is assembled.
  - [ ] 6-3. Require structured rationale and confidence for model-assisted relation labels.
  - [ ] 6-4. Let deterministic conflict/lock evidence override model optimism.
  - [ ] 6-5. Add `isolate_parallel` for conflicts that can safely use worktrees/diff admission.

## Decisions
- Safety beats throughput. Parallelism is an optimization, not the default when evidence is missing.
- Deterministic file/resource conflicts override model classification.
- Worktree isolation is the preferred path for useful but conflicting write work when merge/admission can stay deterministic.
- The first version should deliberately under-admit parallel work rather than over-admit unsafe overlap.

## Notes
- This plan should reuse `src/dan/worker/scheduler/` contracts and Super DAN worktree policy instead of creating one-off dependency logic in surfaces.
- The first acceptance test should not depend on semantic inference: explicit disjoint filenames are enough to prove nonblocking parallel admission.
