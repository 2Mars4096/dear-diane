# 55-3: Context, Artifact, And Provenance Membrane

**Parent:** [55-agent-level-stochastic-orchestration](55-agent-level-stochastic-orchestration.md)
**Status:** in-progress
**Goal:** Make context quality, artifact capsules, readiness signals, and provenance explicit scheduler inputs so DAN can pass useful partial work quickly, decide when to send better context, validate, reduce, or spawn more work.

## Tasks

- [ ] 1. Define context-state fields
  - [ ] 1-1. Track context completeness, freshness, compression loss, required references, and assumption debt
  - [x] 1-2. Distinguish raw evidence, compressed summaries, briefs, and final-answer material
  - [ ] 1-3. Record which task and source produced each claim or artifact
  - [x] 1-4. Record prompt-replay compaction stats separately from raw tool evidence
  - [x] 1-5. Define a generic context capsule schema with `raw_refs`, retained evidence spans, relevance, confidence, assumptions, open questions, unlocks, and invalidations
  - [x] 1-6. Define bounded context-packet assembly rules so downstream agents receive capsules, selected exact spans, and raw references instead of full upstream transcripts
- [ ] 2. Define artifact promotion
  - [ ] 2-1. Require validator gates before artifacts become final-answer material
  - [ ] 2-2. Support partial promotion for subclaims, files, tests, citations, and blocker reports
  - [ ] 2-3. Preserve unresolved assumptions instead of hiding them in aggregation prose
  - [ ] 2-4. Separate local reducer outputs from globally promoted artifacts so final aggregation waits on smaller, validated units rather than raw worker transcripts
  - [x] 2-5. Track artifact states as provisional, useful-for-downstream, validated, invalidated, or superseded
  - [x] 2-6. Let downstream dependencies target required artifact kinds or readiness predicates instead of only upstream task completion
  - [ ] 2-7. Use artifact kinds and ownership/conflict metadata as generic fanout inputs so the scheduler does not need task-specific decomposition rules
- [ ] 3. Define context-improvement actions
  - [ ] 3-1. Let the scheduler choose to enrich context instead of spawning another worker
  - [ ] 3-2. Add reducer summaries that state what was compressed away
  - [ ] 3-3. Detect stale context when newer artifacts invalidate earlier briefs
  - [x] 3-4. Compact older provider-facing `file_read` replay payloads while preserving full raw evidence in logs
  - [ ] 3-5. Extend retain/reduce behavior beyond `file_read` to shell output, tests, web evidence, browser state, screenshots, git diffs, logs, and validation results
  - [x] 3-6. Prefer deterministic retainers for cheap structure first, then use LLM reducers only when evidence selection requires semantic judgment
  - [ ] 3-7. Support parallel capsule reducers so many tool calls can retain useful context concurrently without forcing one large parent prompt
  - [x] 3-8. Add deterministic retainers for file/directory context, shell/test output, web evidence, browser state, git state/diffs, and mutation deltas
- [ ] 4. Define barrier-aware aggregation
  - [ ] 4-1. Identify which aggregation/delivery steps are true serial barriers for each organism type
  - [x] 4-2. Push partial reduction and validation earlier when it shrinks the final barrier without losing provenance
  - [ ] 4-3. Measure aggregation wait time caused by slow or uncertain upstream branches
  - [x] 4-4. Treat task dependencies as artifact/readiness dependencies, not completed-worker dependencies
  - [ ] 4-5. Add heartbeat/delta packet semantics so long-running workers can publish current status, useful capsules, blockers, and invalidations while still running
  - [ ] 4-6. Let the scheduler start downstream work at the first safe readiness boundary and update that work if upstream capsules are later refined or invalidated
  - [x] 4-7. Publish `context.capsule.emitted` delta events from local tool-loop results
  - [x] 4-8. Let generic reducers merge compatible artifact-owner fragments incrementally, while conflict cases remain explicit validation or repair tasks
  - [x] 4-9. Let tissue pools notify callers as each member finishes so organism-level schedulers can emit readiness/unlock events before the full pool barrier returns
  - [x] 4-10. Start deterministic validation prechecks from complete incremental merge candidates and reuse them at final validation when the candidate is unchanged
  - [x] 4-11. Start compact model validation speculatively from complete incremental merge candidates and reuse or discard it by candidate fingerprint at the final validation boundary
- [ ] 5. Roll out the capsule bus without breaking existing logs
  - [ ] 5-1. Inventory current handoff/report/control-plane packet types and map which fields already behave like capsules
  - [ ] 5-2. Define a compatibility adapter from existing worker reports, organism logs, and handoff packets into the new capsule/readiness shape
  - [ ] 5-3. Add measurement fields for time-to-first-useful-artifact, downstream-unlock latency, capsule size, raw-ref reuse, and invalidation rate
  - [ ] 5-4. Pilot the contract first in DAN Code and DAN Research, then generalize to Super DAN/operator organisms
  - [x] 5-5. Attach capsule payloads to local runtime `executed_tools` and organism-log-compatible context events
  - [x] 5-6. Add a DAN Code artifact-readiness ledger that evaluates worker capsules/readiness signals after each worker pool and records downstream-ready owner lanes before aggregation

## Decisions

- Context is a resource and a failure mode, not just a message payload.
- Reducers should be streaming and hierarchical where possible.
- Final delivery should assemble promoted artifacts and label unresolved assumptions clearly.
- Streaming reducers reduce barrier cost; they do not remove the need for a final coherent synthesis step.
- Prompt replay compaction must be evidence-preserving: the provider can receive lossy excerpts, but raw `executed_tools` evidence and event logs stay full-fidelity.
- Exact recent evidence should stay cheap to use: keep the latest local `file_read` results full in the active prompt and compact only older successful reads with explicit omitted-character markers and a re-read instruction.
- The primitive should be a typed context capsule, not an unstructured summary. Every capsule must point back to raw evidence and carry retained exact spans when exactness matters.
- The generic flow is `raw event/tool output -> retained artifact capsule -> readiness signal -> downstream context packet`.
- Parallelism should produce many small capsules concurrently; serial dependencies should unlock on the first sufficient capsule/readiness signal instead of waiting for full upstream completion.
- Heartbeats are not just liveness signals. They should carry delta capsules, blockers, invalidations, and downstream unlock hints when useful work becomes available.
- Existing handoff packets, worker reports, control-plane events, and organism logs are useful substrate, but the new contract should optimize for live downstream usefulness rather than only replay/completion traceability.
- The capsule bus is the replacement path for task-specific fanout heuristics: fanout should emerge from required artifacts, readiness, provenance, conflicts, and expected value, not from a product-specific rule.

## Notes

- This plan should reuse the richer worker-review membrane from `54-*` where possible.
- The first implementation can be a structured ledger attached to organism logs before it becomes a live scheduling policy.
- First runtime slice: `src/dan/worker/organisms/local_runtime.py` now compacts older `file_read` tool results in the copied provider prompt before each model call and emits prompt-context stats, directly targeting large repeated local-code context without changing raw trace fidelity.
- First capsule-contract slice: `src/dan/worker/context_capsules.py` defines `RawRef`, `EvidenceSpan`, `ContextCapsule`, `ReadinessSignal`, and `ContextPacket`; `src/dan/worker/organisms/local_runtime.py` now attaches deterministic tool capsules to recorded tool results and emits compact `context.capsule.emitted` events.
- First readiness bridge slice: scheduler tasks can now require readiness predicates, artifact kinds, and unlock keys; `evaluate_task_readiness_from_capsules(...)` turns capsules/readiness signals into an explicit ready/not-ready decision plus a bounded downstream context packet. The local runtime also emits `context.readiness.emitted` beside each capsule event, and scheduler replay reports first useful artifact time, first downstream-ready time, and downstream unlock latency.
- First DAN Code ledger slice: `coding_execution.py` now emits `scheduler.readiness.evaluated` after worker pools and preserves `readiness_ledger_history` in final metadata. The ledger is still observational/control-plane input; richer model-validator or context-improvement launch before terminal upstream completion remains open under task 4-6.
- First incremental tissue slice: `execute_tissue_pattern(...)` accepts a member completion callback, and DAN Code uses it to emit `scheduler.readiness.member_evaluated` plus `scheduler.downstream.unlocked` as owner lanes finish.
- First incremental reducer/validator slice: ready owner lanes now produce partial deterministic aggregation fragments during the worker stage (`scheduler.downstream.started`, `aggregation.partial_reduced`), final aggregation can reuse those fragments (`aggregation.incremental_merge.reused`) when they cover all owner paths, complete incremental merge candidates start deterministic validation prechecks early (`validation.precheck.incremental_completed`), and final validation reuses that precheck (`validation.precheck.reused`) when hygiene or aggregation did not change the candidate. If the deterministic precheck admits a compact validator, DAN Code also starts speculative compact model validation (`validation.speculative_model.started/completed`) and reuses it at the final barrier (`validation.speculative_model.reused`) when the candidate fingerprint still matches. Context-improvement launch and richer invalidation/update handling remains open under task 4-6.
- Candidate capsule kinds include `file_context`, `test_result`, `web_evidence`, `browser_state`, `implementation_delta`, `blocker`, `decision`, `validation_result`, and `handoff_brief`.
- Example dependency shape: task B depends on `kind=api_contract` or `ready_for_downstream=true` from task A, not on task A reaching terminal status.
- The first design pass should avoid adding another always-on summarizer agent. Use deterministic extraction, raw references, and selective semantic reducers so the capsule layer reduces latency instead of adding serial LLM calls.
- Keep the implementation minimal: extend the existing capsule schema and local-runtime emitters before introducing new object types; new abstractions must serve multiple organism families.
