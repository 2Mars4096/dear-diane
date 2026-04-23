# 55: Agent-Level Stochastic Orchestration

**Status:** in-progress
**Goal:** Move DAN from mostly workflow-level orchestration toward agent/task-level stochastic scheduling that optimizes time-to-quality and bottlenecked makespan under uncertain task value, runtime, context, and dependency estimates.

## Tasks

- [x] 1. Capture the theory note and rendered PDF
  - [x] 1-1. Add `docs/theory/agent-level-stochastic-orchestration.tex`
  - [x] 1-2. Render `docs/theory/agent-level-stochastic-orchestration.pdf`
  - [x] 1-3. Include robustness to misspecified duration, quality-gain, uncertainty, failure-risk, and dependency-graph estimates
- [ ] 2. Formalize the agent-level task graph and queueing model via [55-1-agent-level-task-graph-and-queueing-model](55-1-agent-level-task-graph-and-queueing-model.md)
- [ ] 3. Define the quality/time reward and measurement harness via [55-2-quality-time-reward-and-measurement](55-2-quality-time-reward-and-measurement.md)
- [ ] 4. Define the context, artifact, and provenance membrane via [55-3-context-artifact-and-provenance-membrane](55-3-context-artifact-and-provenance-membrane.md)
- [ ] 5. Add robustness policies for misspecification via [55-4-robustness-to-misspecification](55-4-robustness-to-misspecification.md)
- [ ] 6. Integrate the scheduler design with existing organism logs, gateway dispatch telemetry, and live evaluation harnesses
  - [x] 6-1. Add a replay-only scheduler diagnostic layer on top of `organism_log_v1` plus CLI access through `dan-organism-log scheduler-replay`
  - [x] 6-2. Persist DAN Code workspace-level control-plane events in `.dan-code/control-plane-events.jsonl` so controller, project-planner, review, CLI, and bounded-run launch/completion events are visible outside per-run logs
  - [ ] 6-3. Join gateway dispatch telemetry into the same replay/measurement view
  - [ ] 6-4. Feed the same measurement contract into live evaluation harnesses
- [ ] 7. Connect the new agent-level scheduler back to existing workflow-level critical-path optimization so DAN can optimize inside stages without losing the higher-level workflow dependency view
- [ ] 8. Implement the first scheduler slice at the shared worker/organism boundary, with `dan organism` or Super DAN as proving surfaces but not as the exclusive owner
- [x] 9. Derive the scheduler from the universal-agent substrate rather than introducing a separate black-box optimizer
- [x] 10. Harden DAN Code as a first product proof for agent-level scheduling
  - [x] 10-1. Treat explicit short completion timeouts as compact-pass policy: one bounded run, zero inner repair rounds, full policy events in `.dan-code`
  - [x] 10-2. Force broad website/visual-polish objectives into parallel exclusive file-owner lanes when that reduces the slow path
  - [x] 10-3. Carry successful worker-owned mutation evidence through aggregation so reducer timeouts do not erase materialized target files
  - [x] 10-4. Bypass the LLM aggregation organ with a deterministic `worker_merge` candidate when every exclusive owner lane produced material mutation evidence
  - [x] 10-5. Add deterministic frontend hygiene before validation for common cross-lane contract drift: duplicate sections/closing tags, animation delay attributes, reveal CSS compatibility, scroll-cue hidden state, and reduced-motion CSS
  - [x] 10-6. Extend deterministic `worker_merge` and frontend hygiene from short-timeout-only runs to all covered exclusive-owner plans, including normal `90s` website passes
  - [x] 10-7. Tighten first-write materialization for owner lanes: apply soft post-read direct-write budget caps, allow one grounded timeout recovery retry per direct-write stage, and spend repair budget on compact re-planning when aggregation still has no material candidate
  - [ ] 10-8. Reduce the remaining compact-run validator tail without removing the quality gate

## Decisions

- Optimize first for quality and wall-clock time; token cost remains an extensible reward field but not the leading product constraint.
- Use both forms of the objective: `min E[T(Q >= q_target)]` for time-to-quality, and bottleneck/makespan minimization for decomposition and load balancing inside a stage.
- Treat agents as temporary compute slots over a task DAG, not as a large fixed set of named personas.
- Prefer time-to-quality (`min E[T(Q >= q_target)]`) over a pure total-work objective.
- Make context quality part of scheduling: sending better context can be a first-class action, not only a prompt detail.
- Treat all estimates as misspecified until calibrated by observed runtime, validator outcomes, and artifact promotion history.
- Prune branches conservatively with upper/lower confidence bounds and explicit slack so early noisy estimates do not overkill plausible work.
- Do not pretend orchestration, final aggregation, validation, and delivery barriers disappear; reduce their bottleneck cost through streaming reducers, early validation, and smaller promoted artifacts.
- Avoid over-fragmentation: task splitting is only useful when critical-path reduction beats handoff, reducer, and coordination overhead.
- Implementation should live below the durable DAN-v2 conversation controller and above individual `WorkerCoreExecutor` calls: the controller chooses the lane/objective, while the scheduler manages ready task dispatch inside bounded organisms.
- The outer DAN-v2 conversation controller is also a universal-agent specialization; controller, scheduler, worker, reducer, and validator differ by typed contract rather than by separate architectures.
- The scheduler should be a universal-agent-derived controller: a specialized scheduling agent proposes decomposition, queue actions, dependency revisions, and stop/continue decisions, while deterministic policy guards enforce hard dependencies, capacity, safety envelopes, and confidence-bound pruning.
- `dan organism` is a good first proving surface because it is already a reference organism, but the scheduler should be reusable by DAN Code, DAN Research, Super DAN live, and future operator organisms.
- The gateway dispatch broker remains a lower-level provider-call queue. It can provide telemetry, but it should not become the agent-task scheduler.
- Short-timeout DAN Code runs are intentionally not the same policy as deep benchmark runs: explicit `<=60s` completion timeout means compact one-pass execution, while default/benchmark runs can still spend repair/supervision budget.
- For covered exclusive-owner runs, aggregation is now a deterministic evidence merge, not another model bottleneck. Short-timeout mode still controls compact repair/supervision budgets, but the safe merge condition is ownership coverage plus mutation evidence. The validator remains the quality gate.
- When an owner-lane plan still fails to materialize any bounded candidate, the next safe step is not to rediscover the workspace in aggregation. The bounded organism should either retry a grounded direct-write step quickly or spend its repair budget on a smaller write-first re-plan.
- For first-write pressure, prefer soft caps plus stage-aware retry accounting over rigid threshold kills. Ownership boundaries, honest aggregation, and bounded repair rounds stay hard constraints; direct-write timeout pressure should escalate without turning one noisy estimate into a terminal block.

## Notes

- The seed theory artifact lives in `docs/theory/agent-level-stochastic-orchestration.tex` with a rendered PDF beside it.
- This phase complements the active `54-*` DAN-v2 control-plane work: `54-*` gives the durable controller/membrane, while `55-*` should make the underlying task dispatch policy smarter.
- The implementation should reuse existing primitives where possible: `organism_log_v1`, gateway dispatch queue telemetry, worker reports, validators, and bounded organism run artifacts.
- A useful first runtime target is a scheduler-side diagnostic mode before fully changing product behavior.
- This phase should explicitly compare agent-level scheduling against the older workflow-level critical-path/dependency optimizer rather than duplicating that layer blindly.
- Likely implementation shape: add scheduler/task-graph policy modules under `src/dan/worker/` (for example a `scheduler` subpackage), feed them with `organism_log_v1`/worker-report telemetry first, then plug the policy into bounded organism runners once trace replay proves the decisions.
- The scheduler module should not become a parallel architecture. It should expose typed contracts and deterministic guardrails around a universal scheduling agent that reuses the same recurrent brief/report/review membrane as the rest of DAN-v2.
- The intended hierarchy is `DAN-v2 universal conversation controller -> universal scheduling agent -> universal worker/reducer/validator agents -> deterministic tools and policy guards`.
- Landed first substrate slice: `src/dan/worker/specialized_agents.py` now provides reusable controller/scheduler membranes plus deterministic guardrail metadata, `src/dan/worker/scheduler/` now provides task/proposal contracts plus deterministic schedule admissibility checks, and the DAN conversation controller, incident controller, research orchestrator, and DAN Code project planner now route through that shared specialized-agent substrate.
- Landed replay/diagnostic slice: `src/dan/worker/scheduler/replay.py` now projects replay-only scheduler diagnostics from `organism_log_v1` (critical-path/work-capacity lower bounds, terminal barrier tails, and lane-sequence-only missed-parallelism hints), and `dan-organism-log scheduler-replay` exposes the same payload for trace inspection without changing live behavior.
- DAN Code now also writes a workspace-level `.dan-code/control-plane-events.jsonl` stream through the same `organism_log_v1` substrate, with report/transcript pointers and server discovery support. This makes the controller/project-planner/review layer replayable instead of only preserving bounded worker/tool events.
- Live DAN Code smokes now verify the compact policy events, one outer bounded turn, three exclusive-owner worker lanes, deterministic `worker_merge` aggregation, and full control/run logs. `/tmp/dan-live-website.retry6.lyEfZG` passed validation at `0.91` but exposed a duplicate closing-tag defect; `/tmp/dan-live-website.retry7.7lCUjQ` proved the new `contract_hygiene.applied` event and 1 ms aggregation, then failed validation at `0.84` on delay/hide/reduced-motion contract drift that is now covered by the local hygiene regression. The 90s `.dan-code/runs/turn-04` trace showed that the same deterministic merge/hygiene condition should not be gated on `short_completion_timeout`, so normal-timeout exclusive-owner merges now use the same evidence-backed path and repair unclosed style-block/document-tail drift before validation. The remaining runtime bottleneck is validation, not aggregation.
- The `.dan-code/runs/turn-08/events.jsonl` trace exposed the remaining first-write boundary: owner lanes could read once, get narrowed to `file_edit`, then burn the full inherited `90s` provider budget and die with empty `target_files`. The shared local runtime now caps that post-read direct-write budget, permits one grounded timeout-recovery retry for that state, and the coding organism now spends repair budget on a smaller write-first re-plan when aggregation still has no material candidate.
- The `.dan-code/runs/turn-10/events.jsonl` trace showed that the first timeout patch was still too rigid: the compact repair attempt reached direct-write mode after its final read, then hit a hard `30s` cliff with no stage-specific retry. The shared runtime now treats those direct-write caps as soft (`45s` exclusive-owner, `60s` generic), tracks timeout recovery by stage, and still allows one grounded retry after the final-read transition instead of hard-blocking that edge.
