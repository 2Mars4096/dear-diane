# 46-6: Concierge/Worker Standardization

**Parent:** [46-universal-worker-primitive](46-universal-worker-primitive.md)
**Status:** not-started
**Goal:** Standardize the end-to-end turn -> queue -> task -> session -> executor -> result path so concierge sessions, child handoffs, and Worker execution use one coherent contract instead of overlapping metadata and duplicated control-plane steps.

## Dependencies

- Follows **46-1** through **46-5**: the Worker-first model/executor/authoring surfaces already exist, but the runtime/control-plane contracts around them are still uneven.
- Coordinate with **50-1** so the key-script inventory and duplicate-step map are available before choosing what gets deleted.
- Coordinate with **50-5** because concierge runtime/scheduler narrowing should produce cleaner seams for queue, context, and prompt standardization.
- Coordinate with **50-7** because Worker/legacy bridge retirement affects which runtime-core execution contracts remain in scope.

## Tasks

- [ ] 1. Standardize turn and execution envelopes
  - [ ] 1-1. Inventory the current boundary objects and where they are authoritative: `SurfaceMessage`, `ResolvedContext`, `ConciergeTask`, `Session`, child handoff payloads, and Worker execution inputs/results.
  - [ ] 1-2. Define one authoritative ownership model for queue keys, task ids, session ids, and child handoff ids so queue ownership does not precede task ownership by accident.
  - [ ] 1-3. Document where project scoping is authoritative versus advisory, especially for queue serialization and follow-up continuation.
- [ ] 2. Standardize the connection interfaces
  - [ ] 2-1. Define one tool-capability contract usable by both concierge/session execution and Worker execution. The target boundary should not depend on hidden global schema lookup or DAN-only registries in the eventual reusable core.
  - [ ] 2-2. Define one memory interface separating retrieval/write APIs from prompt-text rendering, so "memory access" is not just prompt text stuffed into metadata.
  - [ ] 2-3. Define one parent/child handoff envelope covering recent turns, task snapshot, repo snapshot, memory context, file refs/snippets, constraints, and return channel semantics.
- [ ] 3. Standardize prompt evolution
  - [ ] 3-1. Write the exact root-turn prompt lifecycle: unified system prompt -> stage overlay -> prompt modules -> context blocks -> workflow continuity pack -> current user turn.
  - [ ] 3-2. Define child prompt derivation rules: what is copied, what is summarized, what is re-resolved, and what must not leak from parent context.
  - [ ] 3-3. Turn stage overlays, handoff prompt blocks, and workflow context packs into explicit standardized slots/modules instead of ad hoc metadata-only seams.
- [ ] 4. Prune unnecessary steps
  - [ ] 4-1. Remove duplicated context-resolution passes where the same turn is re-resolved by multiple layers without a real ownership change.
  - [ ] 4-2. Collapse the current two-queue model into one authoritative queue/lease model, or document a sharp permanent split with no overlapping ownership.
  - [ ] 4-3. Delete prompt/context duplication that exists only because `metadata`, `prompt_context`, and `extra_system_instructions` each carry overlapping state.
- [ ] 5. Validation and documentation
  - [ ] 5-1. Land one durable sequence/state-flow document for message -> queue -> task -> session -> executor -> result.
  - [ ] 5-2. Add focused regressions for handoff-envelope stability, queue/task ownership, and prompt-stage metadata.

## Success Criteria

- the repo has one documented and standardized turn/session/worker contract instead of layer-specific implicit variants
- tool, memory, and message/handoff connections are standardized enough that core execution does not depend on ad hoc metadata fields
- root and child prompt evolution are documented in one place with explicit slots and inheritance rules
- at least one duplicate queue/context/prompt step is deleted or explicitly retired as part of the standardization pass

## Decisions

- Standardize before extracting. No "reusable bundle" claim should ship while queue ownership, handoff semantics, and prompt evolution are still split across multiple layers.
- Prompt fields are part of the runtime contract, not incidental implementation detail.
- The goal is subtractive normalization, not a new orchestration framework.

## Notes

- The review found that queueing currently happens before task binding, a second background queue exists later in the dispatch path, child handoffs are mostly prompt text, and Worker tool/memory interfaces are only partially standardized.
- This plan is the bridge between the structural 50-series cleanup and the later reusable-bundle extraction in 46-7.
