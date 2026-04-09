# 52: Multicellular Composition

**Status:** completed
**Goal:** Build the first trustworthy multicellular layer on top of the hardened universal worker by introducing signaling, tissues, organs, and one bounded reference organism.

## Problem

Once `51-*` produces a strong cell, DAN still needs an explicit way to go from “one viable cell” to “many cooperating cells” without falling into a giant shared prompt or a loose bag of subagents.

The main risk is architectural collapse at the first multicellular step:

- cells communicate through implicit shared context instead of explicit handoffs
- worker pools are treated as ad hoc parallelism instead of reusable tissue patterns
- bounded functional modules are skipped, so every larger composition becomes an undifferentiated manager/worker blob
- the first “organism” arrives before signaling and organ boundaries are strong enough to support it

`52-*` exists to keep the biology stack explicit: cells first, then tissues, then organs, then one reference organism.

## Scope

In scope:

- direct handoff and broadcast/event signaling contracts
- reusable tissue-level coordination patterns
- bounded organ-level modules with strict interfaces
- first-class deep-research and universal-validator organs
- one reference organism that proves the architecture end to end

Out of scope:

- a giant general-purpose swarm framework
- uncontrolled self-spawning across the whole system
- replacing concierge with many competing top-level brains
- trying to solve every future multi-agent use case in the first organism

## Tasks
- [x] 1. Define the multicellular sequencing rule
  - [x] 1-1. Keep `52-*` blocked on the acceptance bar from [51-5-single-cell-evals-and-hardening](51-5-single-cell-evals-and-hardening.md)
- [x] 2. Land the signaling layer
  - [x] 2-1. Plan and sequence [52-1-cell-signaling-and-handoff-contracts](52-1-cell-signaling-and-handoff-contracts.md)
- [x] 3. Land tissue-level coordination patterns
  - [x] 3-1. Plan and sequence [52-2-tissue-patterns-worker-pools-and-quorum](52-2-tissue-patterns-worker-pools-and-quorum.md)
- [x] 4. Land organ-level bounded modules
  - [x] 4-1. Plan and sequence [52-3-organ-patterns-research-coding-validation-synthesis](52-3-organ-patterns-research-coding-validation-synthesis.md)
- [x] 5. Assemble and prove one reference organism
  - [x] 5-1. Plan and sequence [52-4-reference-organism-project-execution-system](52-4-reference-organism-project-execution-system.md)
  - [x] 5-2. Extend the same multicellular substrate into the direct coding product surface via [52-5-dan-code-orchestrator-feedback](52-5-dan-code-orchestrator-feedback.md)
  - [x] 5-3. Extend the same worker substrate into a durable mailbox-backed control-plane runtime via [52-6-durable-agent-runtime](52-6-durable-agent-runtime.md)
  - [x] 5-4. Keep the DAN Code orchestrator alive across turns via [52-7-durable-dan-code-conversation-loop](52-7-durable-dan-code-conversation-loop.md)
  - [x] 5-5. Keep the module cement minimal by moving transient LLM retries into the shared provider layer and tightening the `dan code` control-plane contracts
- [x] 6. Exit with one coherent multicellular story
  - [x] 6-1. Confirm that the same cell contract can be reused across tissues, organs, and the reference organism without collapsing back into ad hoc orchestration

## Dependencies / Sequencing

Recommended order:

```text
51-5 single-cell acceptance bar
  ↓
52-1 cell signaling and handoff contracts
  ↓
52-2 tissue patterns
  ↓
52-3 organ patterns
  ↓
52-4 reference organism
```

Rationale:

- explicit cell signaling must come first or later layers will communicate through hidden shared prompt state
- tissues come before organs because same-type cooperation is the smallest reusable multicellular unit
- organs come before the reference organism because the first full organism should compose bounded modules, not invent them on the fly

## Success Criteria

- cross-cell communication is typed, inspectable, and compact enough to work through refs and bounded packets
- at least one tissue pattern is reusable without changing the underlying cell contract
- at least one organ exposes a strict input/output interface that hides internal cell chatter
- the first organism completes one bounded real task class with explicit decomposition, routing, validation, and synthesis
- the full stack remains intelligible as cells -> tissues -> organs -> organism rather than collapsing back into one oversized orchestration surface

## Decisions
- `52-*` is not “build a giant swarm framework.” It is a controlled multicellular layer on top of a hardened cell.
- Most cells remain workers. Management and coordination should emerge at tissue/organ/organism levels rather than being baked into every cell.

## Notes
- This plan is the execution counterpart to the “cells -> tissues -> organs -> organism” framing in [docs/universal-worker-strategy-summary.md](../universal-worker-strategy-summary.md).
- `52-*` stayed blocked until `51-5` made the single-cell viability bar explicit and green. That gate is now satisfied by the `tests/eval/test_single_cell_acceptance_gate.py` basket plus the focused worker-core regressions.
- 2026-04-08: `52-1` is now landed in the main workspace. `src/dan/worker/signaling.py` and `src/dan/worker/composition.py` establish typed handoff packets, supervisory signals, append-only cross-cell traces, and the thin `execute_cell_handoff(...)` bridge over `WorkerCoreExecutor`; the focused signaling basket in `tests/test_worker/test_model.py` passes in the main workspace.
- 2026-04-08: `52-2` is now landed in the main workspace. `src/dan/worker/tissue.py` establishes reusable worker-pool, quorum, and retrieval-enrichment tissue patterns on top of the existing `CellHandoffPacket` membrane rather than inventing a second coordination primitive.
- 2026-04-08: the first bounded organ layer is now landed in the main workspace. `src/dan/worker/organ.py` adds the strict two-stage organ membrane (`tissue -> lead cell`), organ boundary contracts, escalation surfaces, and bounded organ presets; `tests/eval/test_bounded_organ_patterns.py` proves the first hardened universal-validator organ plus the shared preset shape.
- 2026-04-08: `52-3` is now fully landed in the main workspace. The bounded organ runtime lives under `src/dan/worker/organs/__init__.py`, `src/dan/worker/organ.py` stays as the compatibility export surface, and the first organ set now includes explicit deep-research, coding/build, universal-validator, and synthesis presets with strict outward contracts.
- 2026-04-08: `52-4` is now landed in the main workspace. `src/dan/worker/organisms/project_execution.py` proves one bounded project-execution organism with an explicit planner brain, research/build/validator/synthesis organs, a bounded validator-driven repair loop, and final accountability synthesis instead of ad hoc top-level orchestration.
- 2026-04-09: `52-5` is now landed in the main workspace. `src/dan/worker/organisms/coding_execution.py` and `src/dan/cli/code.py` prove that the same multicellular substrate can power a direct coding product surface, with the orchestrator emitting formal intent-aware public responses and structured status updates instead of relying on a separate chat runtime.
- 2026-04-09: `52-6` is now landed in the main workspace. `src/dan/worker/runner.py` now carries a mailbox-backed `DurableAgentRunner` and `DurableAgentPolicy` on top of `StandaloneWorkerRunner`, so durable/chat-like control planes and bounded runs can share one runtime model instead of forking into separate execution stacks.
- 2026-04-09: `52-7` is now landed in the main workspace. `src/dan/worker/organisms/coding_conversation.py` and `src/dan/cli/code.py` now keep the coding orchestrator alive across CLI turns, route ordinary user messages through that durable agent first, and send bounded coding reports back into the same orchestrator session so it can decide `done`, `continue`, or `clarify`.
- 2026-04-09: the follow-up seam-hardening pass on top of `52-7` is now landed in the main workspace. `src/dan/providers/retrying_provider.py` keeps transient LLM API retries in the shared provider layer rather than the shell, `src/dan/llm_core/factory.py` now builds its default provider through the same provider factory seam, and `coding_conversation.py` plus `cli/code.py` now exchange explicit `CodingConversationContext` / `CodingConversationReportSummary` packets instead of loose dict payloads.
- 2026-04-08: the full multicellular proof basket is green in the main workspace: `PYTHONPATH=src pytest -q tests/test_worker/test_model.py tests/test_worker/test_reference_organism.py tests/eval/test_reference_organism_acceptance.py` passed (`24 passed`), which closes the intended `cells -> tissues -> organs -> organism` story for this phase.
- The first serious organs should include a deep researcher and a universal validator. The meta workflow builder remains rooted in [45-4-meta-workflow-builder-eval-harness](45-4-meta-workflow-builder-eval-harness.md) and should only graduate into `52-*` after it proves itself there.
