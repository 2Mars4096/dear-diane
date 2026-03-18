# 37-6: Bounded Runtime Self-Healing & Pending-Node Adaptation

**Parent:** [37-engine-runtime-parallelism](37-engine-runtime-parallelism.md)
**Status:** not-started
**Goal:** Add a bounded runtime repair layer to engine execution so node failures can be classified, retried, schema-repaired, and safely adapted via per-node overlays and persisted lineage without allowing structural or redesign-style autonomy during normal runs.

## Context

- The repo already has strong diagnosis/repair primitives for generation-time and post-run workflows: `meta/diagnosis.py`, `meta/repair.py`, `ReflectionNode`, `RepairClassifier`, and self-evolvement tracking from 29-6.
- Runtime engine execution still treats many node failures as terminal events after ordinary retry/fallback handling, even when the failure is recoverable with a bounded execution-local repair.
- Tail workflows need bounded runtime repair for the common failure classes that appear in long-running runs: transient provider failures, JSON/schema mismatches, tool/config drift, and repairable generated code.
- This slice should stay execution-local and bounded. Normal engine execution must not auto-apply structural fixes or redesign loops during a live run.
- Pending-node overlays also need checkpoint-safe semantics so resumed runs do not forget which runtime adjustments were already applied or exhausted.
- The 2026-03-17 module audit also noted that users often see structural diagnostics without enough actionable repair guidance. This plan should make repair outcomes explainable to runtime/status consumers rather than emitting raw low-level failures only.

## Tasks

### 1. Runtime failure classification and repair bridge
- [ ] 1-1. Define runtime repair models (`RuntimeFailureContext`, `RuntimeRepairAttempt`, `RuntimeRepairPlan`, `RuntimeOverlay`) that capture `run_id`, `workflow_id`, `node_id`, `node_type`, failure signature, retry budget, and overlay provenance.
- [ ] 1-2. Add a scheduler/executor handoff so `NODE_FAILED`, output-normalization exhaustion, tool-loop exhaustion, and code execution failures all flow through one bounded runtime diagnosis entry point.
- [ ] 1-3. Map runtime failures onto stable categories aligned with existing terminology: `retry`, `prompt_fix`, `parameter_fix`, plus classes such as `timeout`, `llm_failure`, `schema_mismatch`, `tool_failure`, `config_failure`, `code_failure`, `condition_failure`, `unknown`.
- [ ] 1-4. Reuse `RepairClassifier`, `ErrorRecord`, and generation-time artifact-mapping ideas where helpful, but keep the runtime path distinct from the generation/post-run diagnosis loops.

### 2. Bounded retry matrix by failure type
- [ ] 2-1. Define an explicit failure-type to action matrix:
  - `timeout` / transient API failures -> retry with backoff
  - `schema_mismatch` / invalid JSON -> schema repair path
  - `tool` / `config` failures -> whitelisted parameter overlay
  - `code` failures -> bounded repair or fail-fast
  - `unknown` -> one conservative retry then surface failure
- [ ] 2-2. Add per-node caps for each repair kind so the engine cannot loop indefinitely on a single failing node.
- [ ] 2-3. Deduplicate repeated no-op repairs by storing a failure-signature hash per node and refusing to re-apply the same repair kind once it already failed for the same signature.
- [ ] 2-4. Keep `RepairLevel.STRUCTURAL` and `RepairLevel.REDESIGN` out of the normal engine path; if runtime diagnosis reaches those conclusions, emit advisory lineage only and fail/defer to post-run handling.

### 3. LLM schema / JSON repair path
- [ ] 3-1. Extend `OutputNormalizer` / `NormResult` and `executors/llm.py` to emit structured normalization failures with invalid payload preview, schema errors, attempt count, and model used.
- [ ] 3-2. Add a deterministic JSON-repair fast path before another model call (fence stripping, extracted-object reuse, trailing-text cleanup), but only when the fix is mechanical and schema-safe.
- [ ] 3-3. If deterministic repair fails, issue one bounded schema-repair re-prompt that asks only for corrected structured output while preserving the node's original task and schema.
- [ ] 3-4. Preserve current tier-escalation behavior as the final bounded step for LLM schema failures; record whether recovery came from normalization retry, schema re-prompt, or tier escalation.

### 4. Tool, config, and code repair paths
- [ ] 4-1. For tool failures, distinguish transient execution errors from bad `tool_id`, `tool_config`, or timeout mismatches, and allow only whitelisted parameter repairs through execution-local overlay state rather than persisted graph mutation.
- [ ] 4-2. For LLM/config failures, allow bounded pending-node overlays for safe fields such as `system_prompt`, `prompt_template`, `model`, `temperature`, `max_tokens`, and `retry_policy`.
- [ ] 4-3. For code failures, add a narrow repair path only when the failing code is runtime-generated or explicitly repairable from available source text; static user-authored `CodeOperator` code should default to fail-fast plus lineage capture.
- [ ] 4-4. Ensure every runtime repair path operates on execution-local state and never mutates the persisted workflow definition during a run.

### 5. Per-node repair lineage, events, and checkpoint persistence
- [ ] 5-1. Extend execution state and checkpoint payloads with per-node repair lineage: attempts made, remaining budgets, failure signatures, active overlays, and last repair outcome.
- [ ] 5-2. Add explicit engine events for repair observability, e.g. `NODE_REPAIR_STARTED`, `NODE_REPAIR_APPLIED`, `NODE_REPAIR_FAILED`, `NODE_OVERLAY_APPLIED`, and `NODE_REPAIR_SKIPPED`.
- [ ] 5-3. Persist enough lineage in checkpoints that resume can continue with the same repair budgets and pending overlays instead of re-running an exhausted repair loop from scratch.
- [ ] 5-4. Mirror completed repair summaries into the repair/failure-memory path after the run so reflection and experience learning can see what runtime self-healing already tried.
- [ ] 5-5. Attach concise actionable repair summaries (`cause`, `repair_attempted`, `next_step`, `user_visible_message`) so run-manager/UI surfaces do not have to infer human-readable guidance from raw exceptions alone.

### 6. Safe mid-run pending-node overlays
- [ ] 6-1. Add a scheduler-managed overlay map for nodes still in `PENDING` state; overlays apply at dispatch time and are ignored for `RUNNING`, `COMPLETED`, `FAILED`, or `SKIPPED` nodes.
- [ ] 6-2. Support two overlay sources: automatic runtime repair decisions and explicit concierge/user mid-run adaptation requests.
- [ ] 6-3. Persist overlay provenance (`source`, `reason`, `created_at`, `based_on_failure_signature`) so the engine can explain why a pending node ran with modified prompt/config.
- [ ] 6-4. Make checkpoint/resume restore pending-node overlays exactly, so resumed runs preserve the same not-yet-executed prompt/config adjustments.

### 7. Tests, rollout guards, and failure containment
- [ ] 7-1. Extend engine regression coverage with cases for invalid JSON recovery, schema-mismatch exhaustion, transient tool retries, tool/config overlays, code-repair gating, and checkpoint-resume with persisted repair lineage.
- [ ] 7-2. Add negative tests proving normal execution never auto-applies structural graph mutations or redesigns, and never mutates completed/running nodes mid-run.
- [ ] 7-3. Gate the feature behind an explicit config/env flag (`DAN_RUNTIME_SELF_HEALING=1` or equivalent), default off initially.
- [ ] 7-4. Add regression assertions that bounded runtime repair does not introduce scheduler deadlocks or materially degrade the no-failure happy path.

## Primary Files

- `src/dan/engine/scheduler.py`
- `src/dan/engine/executor.py`
- `src/dan/engine/state.py`
- `src/dan/engine/runtime_repair.py`
- `src/dan/engine/normalizer.py`
- `src/dan/engine/events.py`
- `src/dan/engine/checkpoint.py`
- `src/dan/executors/llm.py`
- `src/dan/meta/repair.py`
- `src/dan/meta/diagnosis.py`
- `src/dan/executors/tool.py`
- `src/dan/executors/code.py`
- `src/dan/server/run_manager.py`
- `tests/test_engine/test_scheduler_parallelism.py`
- `tests/test_engine/test_runtime_self_healing.py`

## Dependencies / Sequencing

- Depends on 37-3 and 37-4; eager dispatch and checkpoint/resume semantics are prerequisites for safe pending-node overlays and persisted repair budgets.
- Best started after 37-4 so this work extends an existing engine regression harness instead of inventing test scaffolding mid-stream.
- Reuse terminology and storage hooks from 24-4, 17-2, 19-3, and 29-6, but keep this plan scoped to runtime execution rather than generation-time diagnosis or meta-controller redesign loops.
- Treat learned prompt/model recommendations from 29-6 as future inputs; this slice should consume only bounded, execution-local overlays during a live run.
- Start with the failure-context / event model first so the repair matrix, checkpoint payloads, and run-manager summaries all speak the same runtime vocabulary.

## Success Criteria

- [ ] Runtime node failures are classified into stable, inspectable categories and routed through a bounded repair matrix instead of ad hoc executor-specific behavior.
- [ ] LLM nodes recover known invalid-JSON / schema-mismatch failures within configured caps, with recovery reason recorded in node metadata and events.
- [ ] Pending-node overlays survive checkpoint/resume and never change the persisted workflow graph or mutate already running/completed nodes.
- [ ] Per-node repair lineage is visible in events, checkpoints, and final run metadata, and can feed post-run reflection / experience systems.
- [ ] Normal engine execution never auto-applies `structural_fix` or `redesign`; those remain advisory or post-run concerns.
- [ ] No deadlocks, runaway retry loops, or material happy-path slowdown are introduced when runtime self-healing is enabled.

## Decisions

- Runtime self-healing should stay execution-local and bounded: default-off flag, small attempt caps, explicit failure signatures, and no open-ended autonomy.
- The runtime path stops at `retry`, `prompt_fix`, and `parameter_fix`; `structural_fix` / `redesign` belong to post-run orchestration, not steady-state engine execution.
- The overlay allowlist should stay narrow and explicit: prompt/model/tool/config/retry fields only. No node add/remove/rewire, no graph-level redesign, no mutation of persisted workflow JSON mid-run.
- Completed and running nodes are immutable. Only `PENDING` nodes may receive overlays.
- Prefer extracting a dedicated runtime-repair helper/module rather than further expanding `scheduler.py` with more cross-cutting repair logic.
- Prefer extending existing `EventType`/`RunManager` surfaces for repair observability before adding any separate reporting channel.

## Notes

- Exhausted repair budget should behave like a circuit open: no further retries for that node until explicit user action or resume. This aligns with the chat-dispatch review's recommendation for circuit-breaker semantics on persistent failures.
- Code repair needs the strictest boundary: only repair runtime-generated or explicitly repairable code payloads. Static user-authored code should produce lineage plus failure, not silent autonomous edits.
- Checkpoint equivalence is the main correctness risk: resume must restore repair budgets, failure signatures, and overlays exactly enough that a resumed run behaves like a non-crashed run with the same bounded repair history.
- This slice is about runtime recovery, not topology evolution.
- Review-driven UX requirement: repair events and final run metadata should surface actionable guidance, not just structural/runtime diagnostics.

## Estimate

~3 days
