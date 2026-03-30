# 45-1: Direct Builder Surface

**Parent:** [45-workflow-generation-hardening](45-workflow-generation-hardening.md)
**Status:** not-started
**Goal:** Add a narrow first-class surface that sends prompt-driven workflow builds directly into the graph-generation runtime, without concierge routing or product-mode ambiguity in the middle.

## Problem

Right now the core graph-building machinery exists, but users and evaluators still reach it through broader chat/concierge flows. That makes it difficult to answer basic questions cleanly:

- did graph generation fail, or did routing/classification fail first?
- is latency coming from the builder, or from upstream orchestration?
- can we benchmark prompt → graph directly without inheriting unrelated chat behavior?

## Tasks

- [ ] 1. Define the direct surface contract
  - [ ] 1-1. Choose the primary surface: a server endpoint and a thin CLI wrapper are the preferred minimum.
  - [ ] 1-2. Define request fields for prompt, workflow id/name, generation mode override, save/apply behavior, and draft-only behavior.
  - [ ] 1-3. Define response fields for graph draft, validation report, generation summary, and failure bucket.
- [ ] 2. Route directly into generation/runtime code
  - [ ] 2-1. Reuse the existing workflow-generation runtime instead of duplicating prompts or acceptance logic.
  - [ ] 2-2. Make direct-build behavior explicit: no concierge routing, no goal orchestration, no mutation-tool negotiation.
  - [ ] 2-3. Support both draft-only output and explicit save/apply behavior, with draft-only as the safer default.
- [ ] 3. Surface path controls for evaluation
  - [ ] 3-1. Allow choosing between auto / direct-build / codegen / structured when supported, so evals can compare apples to apples.
  - [ ] 3-2. Return path-taken metadata and timing so benchmarks can attribute latency correctly.
- [ ] 4. Add regression coverage
  - [ ] 4-1. Prompt → draft success case.
  - [ ] 4-2. Validation-blocked draft case.
  - [ ] 4-3. Save/apply path only after accepted draft.
- [ ] 5. Document the intended use
  - [ ] 5-1. Explain that this surface exists to talk to the graph builder directly.
  - [ ] 5-2. Clarify how it differs from concierge/chat build mode.

## Decisions

- The surface should be narrow and explicit, not another overloaded chat mode.
- Draft-only should be the default so evaluation and experimentation do not persist garbage.
- It should expose generation-path metadata by design, not as a debug-only extra.

## Notes

- This sub-plan is the practical answer to "can I talk directly to the graph builder and skip concierge?".
- The direct surface should become the clean benchmark lane used by the rest of plan 45.
