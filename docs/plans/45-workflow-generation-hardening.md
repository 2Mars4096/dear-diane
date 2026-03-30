# 45: Workflow Generation Hardening

**Status:** not-started
**Goal:** Turn workflow generation into a narrower, faster, and more measurable system by adding a direct builder surface, stronger generation contracts, a speed-first minimal path experiment, and a meta-builder evaluation harness before any broader rollout.

## Problem

Workflow generation has enough machinery now to work, but the next bottlenecks are clearer:

- concierge/routing variability still makes it hard to measure graph generation itself in isolation
- the current generation stack still mixes advisory routing, fallback complexity, and acceptance logic in ways that make failures harder to reason about than they should be
- validation is stronger than it used to be, but more semantic guardrails are still needed before generated graphs should be trusted by default
- latency and reliability work needs an explicit worktree-backed evaluation lane instead of landing piecemeal on `main`

The backlog now points in one direction:

- give users and evaluators a direct way to talk to the graph builder
- add more mechanical validation and reject ambiguous generation states earlier
- explore a smaller speed-first path for common graph shapes and weaker models
- treat the "workflow-that-builds-workflows" idea as an evaluation harness first, not a product default

## Scope

In scope:

- a direct workflow-builder surface that bypasses concierge classification and routes straight into generation
- generation-contract and validation hardening across intent/spec/node/section/graph boundaries
- a speed-first minimal build-path experiment with explicit scope and kill-switches
- a meta workflow builder used first as a test/eval harness with explicit contracts and bounded delivery
- benchmark, merge, and rollout gates so none of this lands on `main` by faith alone

Out of scope:

- prompt-secrecy hardening as part of this same tranche; that remains a separate backlog item
- replacing the main product surface with the meta-builder before it proves itself
- broad runtime/execution-engine redesign unrelated to generation hardening

## Sub-Plans

| # | Sub-Plan | Scope | Priority | Status |
|---|----------|-------|----------|--------|
| [45-1](45-1-direct-builder-surface.md) | Direct Builder Surface | Add a narrow first-class surface that talks directly to workflow generation without concierge routing | P1 | not-started |
| [45-2](45-2-generation-contract-and-validation-hardening.md) | Generation Contract and Validation Hardening | Add stronger semantic/mechanical validation and remove misleading advisory gates | P1 | not-started |
| [45-3](45-3-speed-first-minimal-build-path.md) | Speed-First Minimal Build Path | Define and benchmark a smaller, faster graph-generation lane for common workflows | P1 | not-started |
| [45-4](45-4-meta-workflow-builder-eval-harness.md) | Meta Workflow Builder Eval Harness | Use a workflow-that-generates-workflows path as an eval harness with explicit contracts | P2 | not-started |
| [45-5](45-5-benchmark-and-rollout-gates.md) | Benchmark and Rollout Gates | Define the worktree benchmark loop and the criteria for merging any of this back to `main` | P1 | not-started |

## Dependencies / Sequencing

Recommended execution order:

```text
45-1 (Direct Builder Surface)
  ↓
45-2 (Generation Contract and Validation Hardening)
  ├→ 45-3 (Speed-First Minimal Build Path)
  └→ 45-4 (Meta Workflow Builder Eval Harness)
       ↓
45-5 (Benchmark and Rollout Gates)
```

Rationale:

- the direct surface should exist before benchmarking or hardening is judged, otherwise concierge variability contaminates the results
- contract hardening defines the acceptance target that both the minimal path and meta-builder harness must satisfy
- the speed-first path and meta-builder harness are experiments and should compete against the same hardened contract
- rollout gates come last because they depend on comparable evidence across all candidate paths

## Success Criteria

- there is a first-class direct builder surface for prompt → draft graph generation without concierge routing in the loop
- generation failures are attributed at the right layer: extraction/spec/node/section/boundary/whole-graph/acceptance
- semantic guardrails catch ambiguous schedules, weak grounding, and invalid merge/topology states before save
- a speed-first minimal path is either proven faster and good enough on its scoped prompt set or clearly rejected
- the meta workflow builder can be evaluated on explicit contracts without becoming the default product path
- no implementation from this tranche merges to `main` without passing the benchmark and rollout gates

## Decisions

- This tranche is planning-first and benchmark-first. It should be developed in an isolated worktree and merged only after explicit evidence.
- The direct builder surface is the first priority because it removes concierge noise from generation evaluation.
- Mechanical and semantic validation are more important than adding more prompt cleverness.
- The speed-first path is an experiment, not a rewrite commitment.
- The meta workflow builder is an eval harness first. If it wins, it can be promoted later.
- Prompt-secrecy hardening remains separate because it is a different risk/ownership track.

## Notes

- This plan builds directly on the intent/compiler/codegen/diagnosis lineage from [24-reliable-generation](24-reliable-generation.md), [32-workflow-optimization](32-workflow-optimization.md), [33-9-build-path-trustworthiness](33-9-build-path-trustworthiness.md), and [44-structured-workflow-generation](44-structured-workflow-generation.md).
- The currently proposed backlog items "meta workflow builder", "node-generation context enrichment", and "speed-first graph-generation rebuild track" are intentionally folded into this plan rather than left as disconnected bullets.
- The current structured `node_worker.py` is deterministic; any future "node-generation context enrichment" work belongs to the meta-builder or another explicit prompt-driven worker track, not the current deterministic node-plan layer.
