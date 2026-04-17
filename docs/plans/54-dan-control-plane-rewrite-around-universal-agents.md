# 54: DAN Control-Plane Rewrite Around Universal Agents

**Status:** not-started
**Goal:** Rebuild DAN's top control plane as durable universal-agent controllers plus bounded organisms, replacing concierge-first orchestration with a strangler migration so DAN can serve as one high-trust Mac/operator chat plane with a question-driven recurrent hierarchy instead of a collection of partial brains.

## Problem

The reusable worker / tissue / organ substrate is now materially stronger than DAN's older top-level concierge stack, but the default DAN product path still routes through legacy control-plane layers that have accumulated routing, triage, and execution glue over time.

That creates three problems:

- the best current DAN surfaces (`dan code`, `dan research`) already prove that durable universal-agent controllers plus bounded organisms can work better than the legacy top path
- the top-level DAN experience still depends on concierge / dispatcher / chat-manager era control logic that is harder to reason about, test, and extend with new organisms such as Incident Commander
- the repo already has much broader capability reach (local files, shell, git, web, browser, desktop control, messaging adapters), but the current top path is not organized as one clear general operator plane over those capabilities

This plan rewrites the DAN control plane around the universal-agent substrate already landed under `46-*`, `51-*`, and `52-*`, while keeping the existing engine, tools, search, and runtime adapters intact.

## Scope

In scope:

- a new durable top-level `DANConversationController`
- route / intent decisions expressed through explicit worker output contracts instead of concierge-first heuristics
- a reusable recurrent supervision contract (`supervisor_brief -> worker_report -> review_decision`) for multi-tier loops
- a transport-only migration path for gateway / CLI surfaces
- a general operator lane for local workspace, web/browser, and messaging/desktop actions under explicit approval envelopes
- a first new production-shaped organism (`Incident Commander`) that proves the larger composition model
- a benchmarked use-case matrix for the general Mac/operator surface
- explicit coexistence and cutover rules for legacy concierge paths during migration

Out of scope:

- a full rewrite of the engine, tool layer, search subsystem, or graph runtime
- deleting concierge immediately before the replacement path is proven
- turning every side effect into free-form agent behavior instead of keeping deterministic actuation adapters

## Tasks

- [ ] 1. Define the rewrite boundary and migration strategy
  - [ ] 1-1. Keep the worker core, durable runner, tools, search, and run manager as reusable substrate rather than rewriting them
  - [ ] 1-2. Rewrite the DAN control plane with a strangler migration, not a flag-day reset
  - [ ] 1-3. Freeze the recurrent supervision loop as the stable architecture contract rather than hard-coding fixed chains
- [ ] 2. Build the new top-level DAN durable controller via [54-1-dan-conversation-controller-and-routing-strangler](54-1-dan-conversation-controller-and-routing-strangler.md)
- [ ] 3. Freeze the broad general-operator use-case matrix and safety envelopes via [54-4-general-mac-operator-use-case-matrix-and-safety-envelopes](54-4-general-mac-operator-use-case-matrix-and-safety-envelopes.md)
- [ ] 4. Build the first larger production-shaped organism via [54-2-incident-commander-organism](54-2-incident-commander-organism.md)
- [ ] 5. Define and execute the coexistence/cutover path via [54-3-legacy-concierge-cutover-and-surface-migration](54-3-legacy-concierge-cutover-and-surface-migration.md)
- [ ] 6. Exit with one coherent DAN-v2 control-plane story
  - [ ] 6-1. DAN top-level routing is owned by durable universal-agent controllers
  - [ ] 6-2. General Mac/operator tasks route through explicit organisms or operator lanes instead of surface-specific glue
  - [ ] 6-3. Legacy concierge is either removed from the default path or explicitly retained only for still-unmigrated surfaces

## Success Criteria

- top-level DAN routing is expressed through explicit worker/organism contracts instead of concierge-special-case glue
- DAN can handle at least one frozen benchmark in each major general family: ask/research, local workspace mutation, browser/computer assistance, and incident response
- multi-round control loops show `intent down / evidence up` behavior and converge by narrowing uncertainty, requesting a sharper delta, producing clearer artifacts, or stopping honestly
- at least one new high-value organism beyond Code/Research is running on the same substrate
- gateway / CLI / future app surfaces can talk to the same durable DAN controller without bespoke routing logic
- the migration preserves current working specialist paths (`dan code`, `dan research`) instead of regressing them into one oversized generic assistant
- the cutover is benchmarked and reversible

## Decisions

- This is a control-plane rewrite, not an engine rewrite.
- `dan code` and `dan research` remain specialist product surfaces and become exemplars for the broader DAN rewrite.
- DAN-v2 should become one high-trust operator plane for Mac/server work, not only a code/research switchboard.
- The stable architecture element is the recurrent supervision contract, not a menu of fixed production chains.
- The strangler path should add a new controller path, not clone every downstream tool/runtime script; both DAN-v1 and DAN-v2 should share the same substrate wherever possible.
- Side effects stay behind deterministic adapters where possible; universal agents own reasoning, routing, review, and synthesis.
- Unsafe side effects such as browser input, desktop input, outbound messaging, or raw OS automation stay behind policy, approval, and audit boundaries instead of becoming unrestricted prompt behavior.
- The first proving extension is `Incident Commander`, not a vague general-purpose super-organism.

## Notes

- The current durable primitives (`WorkerCoreExecutor`, `DurableAgentRunner`, tissues, organs, and bounded organisms) are already strong enough to support this rewrite path.
- The rewrite should consume existing specialist organisms wherever they already work well instead of rebuilding them prematurely under a single new top-level shell.
- The broad use-case families to anchor now are: ask/research, local workspace operator, browser/download operator, desktop/messaging operator, and operational incident handling.
- Intent should flow downward as sharper briefs; evidence, blockers, and best-next-questions should flow upward into review decisions.
- A future follow-up can promote workflow-building / rebuilding into a larger organism too, but the first priority is replacing the top-level DAN control path with something simpler and more testable.
