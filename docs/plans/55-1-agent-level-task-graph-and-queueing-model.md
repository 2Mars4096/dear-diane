# 55-1: Agent-Level Task Graph And Queueing Model

**Parent:** [55-agent-level-stochastic-orchestration](55-agent-level-stochastic-orchestration.md)
**Status:** in-progress
**Goal:** Define the task DAG, ready queue, capacity model, and dispatch actions that let DAN schedule at the agent/task level instead of only at fixed workflow-stage boundaries.

## Tasks

- [ ] 1. Define the agent-task contract
  - [x] 1-1. Add fields for dependencies, required context, expected duration, expected quality gain, uncertainty, failure risk, artifact output, and validator
  - [x] 1-2. Distinguish hard, soft, suspected, and disproven dependency edges
  - [ ] 1-3. Define task states (`blocked`, `ready`, `running`, `validating`, `promoted`, `cancelled`, `failed`)
  - [x] 1-4. Record coordination/handoff cost so the scheduler does not split tasks more finely than the reducers and validators can absorb
- [ ] 2. Define the scheduler state
  - [ ] 2-1. Track ready queue, in-flight tasks, agent capacity, artifact ledger, context ledger, and promoted outputs
  - [ ] 2-2. Record lower-bound diagnostics: critical path, total remaining work divided by capacity, and observed straggler pressure
  - [ ] 2-3. Include streaming update events so reducers and validators can consume partial outputs before global stage barriers
  - [ ] 2-4. Preserve the parent workflow-stage boundary so agent-level queueing can optimize inside a stage without losing workflow-level dependencies
  - [x] 2-5. Add replay diagnostics on top of `organism_log_v1` for effective lower bounds, terminal barrier tails, and lane-sequence-only missed-parallelism hints before live queue control changes
- [ ] 3. Define dispatch actions
  - [ ] 3-1. Dispatch, split, duplicate, wait, cancel, validate, improve-context, and finalize
  - [ ] 3-2. Keep action decisions inspectable in `organism_log_v1` rather than only implicit in prompts
  - [ ] 3-3. Add a diagnostic dry-run mode that computes queue decisions without changing live product behavior
  - [x] 3-4. Define the universal scheduling agent's typed action contract so schedule decisions are model-proposed but policy-guarded
- [ ] 4. Define decomposition and load-balancing policy
  - [ ] 4-1. Estimate path runtimes using `max_i sum_j(T_ij)` and compare candidate task dissections against the current slowest path
  - [ ] 4-2. Split or reassign straggler paths only when expected critical-path reduction beats coordination overhead
  - [ ] 4-3. Record why a path was split, duplicated, left serial, or cancelled so later trace replay can evaluate the policy
  - [x] 4-4. Add the first live product proof of deterministic owner-lane scheduling: broad website short runs fan out into independent `index.html`, `styles.css`, and `app.js` lanes, then merge by mutation evidence rather than by another model call
- [ ] 5. Define universal-agent integration
  - [ ] 5-1. Reuse DAN-v2 recurrent brief/report/review contracts for scheduler turns
  - [ ] 5-2. Feed worker reports, validator outcomes, artifact promotion, and context debt back into the scheduling agent
  - [x] 5-3. Keep deterministic code responsible for admissibility checks, queue capacity, hard dependency blocking, and audit logging

## Decisions

- The graph is dynamic: tasks and edges can be added, demoted, or reopened when new evidence arrives.
- Agent identity should not dominate scheduling; task contract and current context should.
- Queueing policy should be observable enough to compare against the current fixed-stage organism behavior.
- Workflow-level dependencies remain the outer map; this plan adds an agent-level scheduler inside that map.
- Smart queueing includes deciding when not to parallelize because a real barrier or coordination cost would dominate.
- The scheduler derives from the universal-agent architecture: an agent proposes schedule moves, deterministic guards decide what is admissible.
- Explicit short-timeout operator runs should select a different queue policy from deep/benchmark runs: one compact bounded pass, no hidden second supervision turn, and logged policy choices.

## Notes

- Existing gateway dispatch telemetry covers provider queue/in-flight timing, but this plan needs an agent-task queue above provider calls.
- The first version can be policy-only and run against recorded organism logs before it controls live dispatch.
- Likely code home is a new shared worker scheduler module, not a CLI-specific implementation inside `src/dan/cli/organism.py`.
- Initial integration points should be bounded organism runners that already fan out workers; the scheduler should call into `WorkerCoreExecutor` rather than replace it.
- The implementation should avoid a second orchestration philosophy. The scheduling agent should be another universal-agent controller with a narrower contract.
- Landed first code slice: `src/dan/worker/scheduler/contracts.py` now defines dependency/task/proposal contracts, `src/dan/worker/scheduler/policy.py` now evaluates deterministic admissibility for dispatch/cancel/finalize-like moves, and `src/dan/worker/scheduler/agents.py` now exposes one shared universal scheduling-agent builder.
- Landed replay slice: `src/dan/worker/scheduler/replay.py` now projects critical-path/work-capacity lower bounds, terminal barrier tails, and lane-sequence-only missed-parallelism candidates from recorded traces, and `dan-organism-log scheduler-replay` exposes the same diagnostic payload at the CLI seam.
- DAN Code control-plane events now land in `.dan-code/control-plane-events.jsonl`, which gives replay and future queueing policies visibility into turn decisions, project-planner milestones, review outcomes, and bounded-run launch/completion events instead of only the inner worker/tool trace.
- DAN Code now has a first live queue-policy proving slice: short-timeout runs emit `repair.policy.selected` and `supervision.policy.selected`, broad website/visual-polish work deterministically fans out into three exclusive file owners, and aggregation can preserve worker-owned mutation paths when reducer synthesis times out.
- The short-run owner-lane merge now emits `candidate_source="worker_merge"` and a `contract_hygiene.applied` event when deterministic frontend repairs are made before validation. This gives trace replay a visible dispatch -> worker -> deterministic reducer -> validator shape instead of hiding aggregation policy inside prompts.
