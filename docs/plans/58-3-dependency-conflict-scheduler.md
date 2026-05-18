# 58-3: Dependency and Conflict Scheduler

**Parent:** [58-async-core-and-background-runtime](58-async-core-and-background-runtime.md)
**Status:** completed
**Goal:** Start with conservative dependency/conflict scheduling for the first nonblocking proof: use explicit paths, owned paths, workspace roots, and broad resource locks to decide `start_parallel`, `queue_after`, `append_to_active`, or `ask_clarification`; defer richer semantic/model scheduling until that proof is green.

## Tasks
- [x] 1. Define MVP relation labels
  - [x] 1-1. `independent`: no required upstream artifacts and no overlapping owned paths/resources.
  - [x] 1-2. `dependent_or_conflicting`: needs output from, or overlaps paths/resources with, an active/queued task.
  - [x] 1-3. `conflicting`: may mutate overlapping files/resources or incompatible external state.
  - [x] 1-4. `continuation`: semantically modifies the active objective and should append or queue-after.
  - [x] 1-5. `unclear`: insufficient evidence; ask clarification or run a read-only planner.
- [x] 2. Build deterministic dependency evidence
  - [x] 2-1. Compare explicit target paths, artifact refs, mentioned filenames, and workspace roots.
  - [x] 2-2. Use active run owned paths, planned deliverables, and current lock records.
  - [x] 2-3. Treat missing target-path evidence as `unclear` unless the user explicitly says "new/separate task".
  - [x] 2-4. Treat external side effects and shell commands as broad resources unless a narrower policy exists.
- [x] 3. Add only enough classification for the proof
  - [x] 3-1. Prefer explicit `/new` / `/append` / `/status` style commands over inference.
  - [x] 3-2. If target paths are disjoint, admit `start_parallel` under capacity limits.
  - [x] 3-3. If target paths overlap, admit `queue_after` with conflict reason.
  - [x] 3-4. If relation cannot be determined cheaply, ask one clarification.
- [x] 4. Select scheduling action
  - [x] 4-1. `start_parallel` for independent work under capacity limits.
  - [x] 4-2. `queue_after` for dependent work.
  - [x] 4-3. `append_to_active` for explicit active-run steering.
  - [x] 4-4. `clarify` when relation is ambiguous and the cost of guessing is high.
  - [x] 4-5. Defer `isolate_parallel` until the basic board/background proof works.
- [x] 5. Add regressions
  - [x] 5-1. Two scripts targeting different files can run in parallel.
  - [x] 5-2. A follow-up that says "also add that output to the first script" queues behind the first script.
  - [x] 5-3. Two runs targeting the same file do not write concurrently in the main workspace.
  - [x] 5-4. Low-confidence relation classification asks a clarification rather than appending silently.
- [x] 6. Keep richer scheduling outside Plan 58 unless a separate product need is opened.

## Decisions
- Safety beats throughput. Parallelism is an optimization, not the default when evidence is missing.
- Deterministic file/resource conflicts override model classification.
- Worktree isolation is the preferred path for useful but conflicting write work when merge/admission can stay deterministic.
- The first version should deliberately under-admit parallel work rather than over-admit unsafe overlap.

## Notes
- This plan should reuse `src/dan/worker/scheduler/` contracts and Super DAN worktree policy instead of creating one-off dependency logic in surfaces.
- The first acceptance test should not depend on semantic inference: explicit disjoint filenames are enough to prove nonblocking parallel admission.
- MVP scheduler is deterministic only; model-assisted relation classification and `isolate_parallel` are not part of Plan 58.
