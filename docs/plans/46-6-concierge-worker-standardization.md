# 46-6: Concierge-First Execution Standardization

**Parent:** [46-universal-worker-primitive](46-universal-worker-primitive.md)
**Status:** not-started
**Goal:** Make the concierge the single authoritative execution path for DAN chat. Workers are compute nodes the concierge dispatches — not a parallel prompt/tool/memory universe. Clean up the concierge prompt pipeline so agents receive information-rich, motivating prompts with the right context.

## Architectural Decision

**Concierge is the brain. Workers are hands.**

The codebase currently has two separate prompt/execution paths that don't talk to each other. This plan eliminates the split by declaring the concierge path authoritative and demoting Workers to dispatched compute:

- **Concierge owns:** turn intake, queue/task binding, triage, prompt assembly, tool exposure, memory retrieval, child delegation, result synthesis. This is the single path for all DAN chat.
- **Workers own:** contract execution inside workflow runs (input schema → execute → output schema). Workers receive their prompt/tools/context from the concierge or from the engine's `worker_resources` catalog — they do not maintain a parallel prompt-assembly pipeline.
- **What goes away:** the idea that Worker execution is an alternative to concierge execution. When the concierge needs compute done, it dispatches to Workers (or to `chat_manager` for LLM turns). Workers don't independently resolve memory, assemble stage overlays, or manage prompt quality — the caller does.

This means 46-7 (bundle extraction) becomes much simpler: the reusable core is the Worker contract primitive (input → execute → output), not an entire competing agent runtime.

## Dependencies

- Coordinate with **50-1** so the key-script inventory and prompt-seam map are available.
- Coordinate with **50-5** for concierge file narrowing before reshaping the prompt pipeline.
- **Scope split with 50-5:** 50-5 narrows the *files* (extracts schedule/progress modules); this plan reshapes the *prompt pipeline and contracts* inside those files.

## Tasks

- [ ] 1. Clean up the concierge prompt pipeline
  - [ ] 1-1. Document the current pipeline: `_build_prompt()` (tier_executors.py:734-804) → `_stage_prompt_overlay()` (:807-812) → `_extract_chat_params()` (:849-1080) → `_apply_workflow_continuity_context()` (:1111-1212). Each stage independently concatenates fragments into `extra_system_instructions`.
  - [ ] 1-2. Refactor into explicit prompt slots assembled in one pass: **system prompt** → **stage overlay** → **context modules** (memory, expertise, file context, task context, workflow continuity) → **user turn**. Replace ad-hoc `extra_system_instructions` concatenation (23 sites across 7 files) with slot-based assembly. Target: ≤8 remaining `extra_system_instructions` uses.
  - [ ] 1-3. Extract `SingleShotExecutor` / `MultiStepExecutor` shared prompt logic — both call identical `_build_prompt()` + `_extract_chat_params()` chains (tier_executors.py ~:1361/:1550). Move to a shared base method.
  - [ ] 1-4. Remove the double metadata extraction: `_build_prompt()` extracts metadata fields (:786-792) that `_extract_chat_params()` also extracts independently (:854-857). One pass, one extraction.
- [ ] 2. Prompt quality pass
  - [ ] 2-1. Audit and rewrite the `_STAGE_PROMPT_OVERLAYS` dict (tier_executors.py:130-166). The current overlays are terse one-liners like "Act as a practical executor." Each stage overlay should:
    - State what the agent's role is in this stage
    - Explain what good output looks like
    - Provide constraints that prevent common failure modes
    - Be concise — no boilerplate filler
  - [ ] 2-2. Audit the child prompt derivation in `_build_child_handoff_context()` (tier_executors.py:548-582) and `_build_child_metadata_seed()` (:500-514). Children should receive enough context to act autonomously without re-deriving parent state.
  - [ ] 2-3. Audit `chat_prompts.py` (1417 lines) for information density. Are the system prompts telling agents what they need to know? Are mutation schemas clear? Cut dead prompt text, strengthen weak instructions.
- [ ] 3. Standardize the handoff envelope
  - [ ] 3-1. Replace the `_CHILD_METADATA_KEYS` whitelist (:167-188) + `_build_child_handoff_context()` + `_build_child_metadata_seed()` with a typed `HandoffEnvelope` dataclass. Fields: recent turns, task snapshot, memory context, file refs, constraints, return channel.
  - [ ] 3-2. Define child prompt derivation rules: what is copied, what is summarized, what must not leak from parent.
- [ ] 4. Prune prompt/context duplication
  - [ ] 4-1. Collapse overlapping prompt carriers: `metadata`, `prompt_context`, `extra_system_instructions` currently carry overlapping state. Define which slot each belongs to and remove the overlap.
  - [ ] 4-2. Verify the dispatcher two-queue model (per-project `_project_queues` + `_global_queue` in dispatcher.py:100-107) is intentional and document the ownership split clearly. If overlap exists, collapse.
- [ ] 5. Clarify Worker's role as dispatched compute
  - [ ] 5-1. Document that Workers are compute nodes, not an alternative execution path. The concierge (or engine scheduler) is always the caller; Workers never independently resolve memory, assemble stage overlays, or manage their own prompt quality.
  - [ ] 5-2. Worker's `_resolve_effective_config()` / `_resolved_system_prompt()` is valid for workflow-node execution — it assembles instruction from `worker_resources` catalogs. This is the Worker doing its job as a compute node, not a competing prompt path. Leave it alone but document the boundary: concierge prompt pipeline for chat turns, Worker config resolution for workflow nodes.
- [ ] 6. Validation
  - [ ] 6-1. Add focused regressions for prompt-slot assembly, handoff envelope, and stage-overlay content.
  - [ ] 6-2. Capture 3 representative prompts (concierge session, child handoff, workflow Worker node) as golden fixtures for information-density comparison.

## Primary Files

- `src/dan/server/concierge/tier_executors.py` (2146 lines — prompt assembly, stage routing, child handoff)
- `src/dan/chat_prompts.py` (1417 lines — master prompt definitions)
- `src/dan/server/chat_manager.py` (`extra_system_instructions` plumbing)
- `src/dan/agent_runtime/messages.py` (`extra_system_instructions` + `prompt_context` assembly)
- `src/dan/server/concierge/dispatcher.py` (queue ownership)

## Success Criteria

- the concierge prompt pipeline is one documented slot-based assembly, not 4 stages concatenating into `extra_system_instructions`
- stage overlays are information-rich and motivating, not terse one-liners
- child handoffs use a typed envelope, not a metadata-key whitelist
- `extra_system_instructions` injection sites reduced from 23 to ≤8
- Workers are documented as dispatched compute, not a parallel execution path
- at least one duplicate prompt/context step is deleted

## Decisions

- **Concierge is the single execution brain for DAN chat.** Workers are compute nodes it dispatches. This is a simplification, not a compromise.
- Prompt quality is a first-class deliverable. Agents that receive better prompts produce better results.
- Worker prompt resolution (`_resolve_effective_config`) for workflow nodes is valid and separate from the concierge pipeline — two different callers (chat vs workflow engine), not two competing architectures.
- The goal is subtractive normalization, not a new orchestration framework.

## Notes

- This plan replaces the earlier framing that tried to "unify two orthogonal paths." Instead, we accept one path (concierge) as authoritative and leave the other (Worker config resolution) as a downstream compute concern. Much simpler.
- The `_STAGE_PROMPT_OVERLAYS` dict is the single highest-leverage target for agent quality improvement. Rewriting these overlays is cheap and directly impacts every concierge turn.
