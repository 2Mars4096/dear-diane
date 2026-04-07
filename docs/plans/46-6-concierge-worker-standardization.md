# 46-6: Concierge-First Execution Standardization

**Parent:** [46-universal-worker-primitive](46-universal-worker-primitive.md)
**Status:** completed
**Goal:** Make the concierge the single authoritative execution path for DAN chat. Workers are compute nodes the concierge dispatches — not a parallel prompt/tool/memory universe. Clean up the concierge prompt pipeline so agents receive information-rich, motivating prompts with the right context, trust labels, and output contracts.

## Architectural Decision

**Concierge is the brain. Workers are hands.**

The codebase currently has two separate prompt/execution paths that don't talk to each other. This plan eliminates the split by declaring the concierge path authoritative and demoting Workers to dispatched compute:

- **Concierge owns:** turn intake, queue/task binding, triage, prompt assembly, tool exposure, memory retrieval, child delegation, result synthesis. This is the single path for all DAN chat.
- **Workers own:** contract execution inside workflow runs (input schema → execute → output schema). Workers receive their prompt/tools/context from the concierge or from the engine's `worker_resources` catalog — they do not maintain a parallel prompt-assembly pipeline.
- **What goes away:** the idea that Worker execution is an alternative to concierge execution. When the concierge needs compute done, it dispatches to Workers (or to `chat_manager` for LLM turns). Workers don't independently resolve memory, assemble stage overlays, or manage prompt quality — the caller does.

This means 46-7 (bundle extraction) becomes much simpler: the reusable core is the Worker contract primitive (input → execute → output), not an entire competing agent runtime.

## Dependencies

- **50-1** should land first so the key-script inventory and prompt-seam map are available.
- **50-5** should land first for concierge file narrowing before reshaping the prompt pipeline.
- **Scope split with 50-5:** 50-5 narrows the *files* (extracts schedule/progress modules, fixes dispatch policy); this plan reshapes the *prompt pipeline and contracts* inside those files.
- **Does NOT depend on 50-7.** 50-7 (Worker bridge retirement / AppState cleanup) is optional debt payoff that can run after 46-6 or be deferred. If 50-7 lands later, it simplifies the Worker adapter surface that 46-6 documents, but 46-6 does not need to wait for it.

## Tasks

- [x] 1. Clean up the concierge prompt pipeline
  - [x] 1-1. Document the current pipeline: `_build_prompt()` (tier_executors.py:734-804) → `_stage_prompt_overlay()` (:807-812) → `_extract_chat_params()` (:849-1080) → `_apply_workflow_continuity_context()` (:1111-1212). Each stage independently concatenates fragments into `extra_system_instructions`.
  - [x] 1-2. Refactor into explicit prompt slots assembled in one pass: **system policy** → **stage / response mode** → **user ask first** → **context modules** (memory, expertise, file context, task context) → **evidence blocks** (workflow continuity, authoritative workflow state, retrieved facts) → **output contract** → **user turn**. Replace ad-hoc `extra_system_instructions` concatenation (23 sites across 7 files) with slot-based assembly. Target: ≤8 remaining `extra_system_instructions` uses.
  - [x] 1-3. Extract `SingleShotExecutor` / `MultiStepExecutor` shared prompt logic — both call identical `_build_prompt()` + `_extract_chat_params()` chains (tier_executors.py ~:1361/:1550). Move to a shared base method.
  - [x] 1-4. Remove the double metadata extraction: `_build_prompt()` extracts metadata fields (:786-792) that `_extract_chat_params()` also extracts independently (:854-857). One pass, one extraction.
- [x] 2. Prompt quality pass
  - [x] 2-1. Audit and rewrite the `_STAGE_PROMPT_OVERLAYS` dict (tier_executors.py:130-166). The current overlays are terse one-liners like "Act as a practical executor." Each stage overlay should:
    - [x] State what the agent's role is in this stage
    - [x] Explain what good output looks like
    - [x] Provide constraints that prevent common failure modes
    - [x] Be concise — no boilerplate filler
  - [x] 2-2. Audit the child prompt derivation in `_build_child_handoff_context()` (tier_executors.py:548-582) and `_build_child_metadata_seed()` (:500-514). Children should receive enough context to act autonomously without re-deriving parent state.
  - [x] 2-3. Audit `chat_prompts.py` (1417 lines) for information density. Are the system prompts telling agents what they need to know? Are mutation schemas clear? Cut dead prompt text, strengthen weak instructions.
  - [x] 2-4. Add explicit trust/provenance labels per prompt block. Historical conversation context is already marked "non-authoritative" (messages.py:165), but memory context (messages.py:172), workflow continuity pack injection (tier_executors.py:1246), and user-profile blocks lack the same explicit trust contract. Each injected block should carry one of: `authoritative`, `advisory`, `retrieved`, `historical`, `user-preference`. This is labeling, not access control — the model needs to know how strongly to weight each block.
  - [x] 2-5. Separate instructions from evidence in the slot contract. Policy, response-mode, and output-contract text should not be mixed into the same block as retrieved memory, workflow pack JSON, or historical conversation context. The model should be able to tell "what I must do" apart from "what facts I may use."
  - [x] 2-6. Define a prompt section-priority and truncation budget. Never drop: current user ask, response mode, workflow identity, output contract. Drop in order: stale memory first, then repo/file snippets, then lower-value advisory context. Compaction should happen at the slot level before history squeezing.
- [x] 3. Introduce concrete typed envelopes
  - [x] 3-1. Introduce a typed `PromptEnvelope` dataclass with explicit slots replacing the current loose `prompt_context` / `extra_system_instructions` / metadata bag. Minimum fields: `system_policy`, `stage_overlay`, `response_mode`, `capability_context`, `memory_context`, `workflow_context_pack`, `attachment_context`, `prefetched_action_context`, `turn_constraints`, `output_contract`. Each slot carries a `trust_label` (`authoritative` | `advisory` | `retrieved` | `historical` | `user-preference`).
  - [x] 3-2. Introduce a typed `TurnExecutionEnvelope` that wraps the turn's full execution context: the `PromptEnvelope`, the resolved `DispatchMode`, the bound `task_id`/`session_id`, and the tool/capability surface. This replaces the current pattern of passing separate `metadata`, `prompt_context`, `extra_system_instructions`, and loose kwargs through the dispatch chain.
  - [x] 3-3. Replace the `_CHILD_METADATA_KEYS` whitelist (:167-188) + `_build_child_handoff_context()` + `_build_child_metadata_seed()` with a typed `ChildHandoffEnvelope` dataclass. Fields: recent turns, task snapshot, memory context, file refs, constraints, definition of done, expected return shape, return channel.
  - [x] 3-4. Define child prompt derivation rules: what is copied, what is summarized, what must not leak from parent, and which parts of the parent prompt become the child's explicit output contract.
- [x] 4. Prune prompt/context duplication
  - [x] 4-1. Retire the following fields/patterns in the same patch series that introduces the typed envelopes:
    - [x] `extra_system_instructions` as a free-form concatenation target (replace with `PromptEnvelope` slots)
    - [x] `prompt_context` as a separate untyped string carrier (merge into `PromptEnvelope.capability_context` or `.attachment_context`)
    - [x] duplicated prompt text in `metadata` dict (e.g., `attachment_prompt_context` stuffed into both `prompt_context` and `extra_system_instructions` in `routers/chat.py:495-497`)
    - [x] overlapping queue identifiers between dispatcher `_project_queues`/`_global_queue` and tiered_dispatch background queue/cap model
  - [x] 4-2. Verify the dispatcher two-queue model (per-project `_project_queues` + `_global_queue` in dispatcher.py:100-107) is intentional and document the ownership split clearly. If overlap exists, collapse.
- [x] 5. Clarify Worker's role as dispatched compute
  - [x] 5-1. Document that Workers are compute nodes, not an alternative execution path. The concierge (or engine scheduler) is always the caller; Workers never independently resolve memory, assemble stage overlays, or manage their own prompt quality.
  - [x] 5-2. Worker's `_resolve_effective_config()` / `_resolved_system_prompt()` is valid for workflow-node execution — it assembles instruction from `worker_resources` catalogs. This is the Worker doing its job as a compute node, not a competing prompt path. Leave it alone but document the boundary: concierge prompt pipeline for chat turns, Worker config resolution for workflow nodes.
  - [x] 5-3. Define one shared minimum prompt contract across concierge root turns, concierge child turns, and workflow Worker nodes: **task**, **scope/constraints**, **evidence/context**, **trust labels**, and **output contract**. Concierge prompts may stay richer, and Worker prompts may stay smaller, but the slot meanings must line up so the reusable bundle has one standard contract surface.
- [x] 6. Validation
  - [x] 6-1. Add focused regressions for prompt-slot assembly, provenance labels, truncation-budget behavior, handoff envelope return contracts, and stage-overlay content.
  - [x] 6-2. Capture 3 representative prompts (concierge session, child handoff, workflow Worker node) as golden fixtures for information-density comparison and slot alignment.

## Primary Files

- `src/dan/server/concierge/tier_executors.py` (2146 lines — prompt assembly, stage routing, child handoff)
- `src/dan/chat_prompts.py` (1417 lines — master prompt definitions)
- `src/dan/server/chat_manager.py` (`extra_system_instructions` plumbing)
- `src/dan/agent_runtime/messages.py` (`extra_system_instructions` + `prompt_context` assembly)
- `src/dan/server/concierge/dispatcher.py` (queue ownership)
- `src/dan/worker/executor.py` (Worker-node prompt boundary)

## Success Criteria

- the concierge prompt pipeline is one documented slot-based assembly via `PromptEnvelope`, not 4 stages concatenating into `extra_system_instructions`
- `TurnExecutionEnvelope` replaces the current loose kwargs/metadata passing through the dispatch chain
- `ChildHandoffEnvelope` replaces the `_CHILD_METADATA_KEYS` whitelist with typed fields including definition-of-done and return-shape
- instructions and evidence live in distinct prompt slots with explicit trust/provenance labels
- stage overlays are information-rich and motivating, not terse one-liners
- a slot-level prompt budget exists, and low-value context is pruned before current-turn intent or output contracts
- `extra_system_instructions` injection sites reduced from 23 to ≤8
- `prompt_context` as a separate untyped carrier is retired in favor of typed `PromptEnvelope` slots
- Workers are documented as dispatched compute, not a parallel execution path
- concierge root, concierge child, and workflow Worker prompts share one documented minimum contract
- at least one duplicate prompt/context step is deleted

## Decisions

- **Concierge is the single execution brain for DAN chat.** Workers are compute nodes it dispatches. This is a simplification, not a compromise.
- Prompt quality is a first-class deliverable. Agents that receive better prompts produce better results.
- Worker prompt resolution (`_resolve_effective_config`) for workflow nodes is valid and separate from the concierge pipeline — two different callers (chat vs workflow engine), not two competing architectures.
- The review findings about provenance labels, prompt budgets, and child return contracts stay inside 46-6. No `46-6-*` plan-file fan-out is needed.
- The goal is subtractive normalization, not a new orchestration framework.

## Notes

- This plan replaces the earlier framing that tried to "unify two orthogonal paths." Instead, we accept one path (concierge) as authoritative and leave the other (Worker config resolution) as a downstream compute concern. Much simpler.
- The `_STAGE_PROMPT_OVERLAYS` dict is the single highest-leverage target for agent quality improvement. Rewriting these overlays is cheap and directly impacts every concierge turn.
