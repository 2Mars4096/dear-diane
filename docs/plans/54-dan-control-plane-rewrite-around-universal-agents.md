# 54: DAN Control-Plane Rewrite Around Universal Agents

**Status:** not-started
**Goal:** Rebuild DAN's top control plane as durable universal-agent controllers plus bounded organisms, replacing concierge-first orchestration with a strangler migration instead of a full reset.

## Problem

The reusable worker / tissue / organ substrate is now materially stronger than DAN's older top-level concierge stack, but the default DAN product path still routes through legacy control-plane layers that have accumulated routing, triage, and execution glue over time.

That creates two problems:

- the best current DAN surfaces (`dan code`, `dan research`) already prove that durable universal-agent controllers plus bounded organisms can work better than the legacy top path
- the top-level DAN experience still depends on concierge / dispatcher / chat-manager era control logic that is harder to reason about, test, and extend with new organisms such as Incident Commander

This plan rewrites the DAN control plane around the universal-agent substrate already landed under `46-*`, `51-*`, and `52-*`, while keeping the existing engine, tools, search, and runtime adapters intact.

## Scope

In scope:

- a new durable top-level `DANConversationController`
- route / intent decisions expressed through explicit worker output contracts instead of concierge-first heuristics
- a transport-only migration path for gateway / CLI surfaces
- a first new production-shaped organism (`Incident Commander`) that proves the larger composition model
- explicit coexistence and cutover rules for legacy concierge paths during migration

Out of scope:

- a full rewrite of the engine, tool layer, search subsystem, or graph runtime
- deleting concierge immediately before the replacement path is proven
- turning every side effect into free-form agent behavior instead of keeping deterministic actuation adapters

## Tasks

- [ ] 1. Define the rewrite boundary and migration strategy
  - [ ] 1-1. Keep the worker core, durable runner, tools, search, and run manager as reusable substrate rather than rewriting them
  - [ ] 1-2. Rewrite the DAN control plane with a strangler migration, not a flag-day reset
- [ ] 2. Build the new top-level DAN durable controller via [54-1-dan-conversation-controller-and-routing-strangler](54-1-dan-conversation-controller-and-routing-strangler.md)
- [ ] 3. Build the first larger production-shaped organism via [54-2-incident-commander-organism](54-2-incident-commander-organism.md)
- [ ] 4. Define and execute the coexistence/cutover path via [54-3-legacy-concierge-cutover-and-surface-migration](54-3-legacy-concierge-cutover-and-surface-migration.md)
- [ ] 5. Exit with one coherent DAN-v2 control-plane story
  - [ ] 5-1. DAN top-level routing is owned by durable universal-agent controllers
  - [ ] 5-2. Legacy concierge is either removed from the default path or explicitly retained only for still-unmigrated surfaces

## Success Criteria

- top-level DAN routing is expressed through explicit worker/organism contracts instead of concierge-special-case glue
- at least one new high-value organism beyond Code/Research is running on the same substrate
- gateway / CLI / future app surfaces can talk to the same durable DAN controller without bespoke routing logic
- the migration preserves current working specialist paths (`dan code`, `dan research`) instead of regressing them into one oversized generic assistant
- the cutover is benchmarked and reversible

## Decisions

- This is a control-plane rewrite, not an engine rewrite.
- `dan code` and `dan research` remain specialist product surfaces and become exemplars for the broader DAN rewrite.
- Side effects stay behind deterministic adapters where possible; universal agents own reasoning, routing, review, and synthesis.
- The first proving extension is `Incident Commander`, not a vague general-purpose super-organism.

## Notes

- The current durable primitives (`WorkerCoreExecutor`, `DurableAgentRunner`, tissues, organs, and bounded organisms) are already strong enough to support this rewrite path.
- The rewrite should consume existing specialist organisms wherever they already work well instead of rebuilding them prematurely under a single new top-level shell.
- A future follow-up can promote workflow-building / rebuilding into a larger organism too, but the first priority is replacing the top-level DAN control path with something simpler and more testable.
