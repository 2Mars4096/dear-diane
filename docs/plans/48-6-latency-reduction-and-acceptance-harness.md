# 48-6: Latency Reduction and Acceptance Harness

**Parent:** [48-workflow-continuity-and-control-plane-hardening](48-workflow-continuity-and-control-plane-hardening.md)
**Status:** completed
**Goal:** Measure DAN-owned workflow-control latency, reduce it where safe, and lock realistic build/edit/run/schedule acceptance sequences into regression coverage.

## Dependencies
- Final optimization and gate enforcement run after **48-2** through **48-5** so measurements reflect the improved path.
- Baseline measurement and acceptance-scenario drafting should start early in parallel with **48-1** (per parent sequencing diagram).
- DB/cache round-trips to the graph store count as DAN-owned latency; upstream provider (LLM) latency is excluded.

## Tasks
- [x] 1. Measure the non-LLM latency budget
  - [x] 1-0. Choose instrumentation stack: structured log spans with `time_ns` deltas (lightweight, no new dependency) as the default; optional OpenTelemetry export for production profiling. Stage names follow a fixed convention: `wf_route`, `wf_resolve`, `wf_context`, `wf_mutate_compile`, `wf_dryrun`, `wf_dispatch`.
  - [x] 1-1. Break workflow continuation turns into DAN-owned stages: routing, identity resolution, context assembly, mutation compilation, dry-run, run/schedule dispatch.
  - [x] 1-2. Record baseline timings (p50 and p95 per stage) before optimization begins so later changes can be compared to a stable before/after number. Baselines are checked into the repo as a JSON fixture under `tests/eval/baselines/`.
  - [x] 1-3. Draft the acceptance battery scenarios in parallel with baseline measurement so the phase is defined by real user sequences early, not only at rollout time.
- [x] 2. Reduce avoidable latency
  - [x] 2-1. Remove redundant lookup and routing stages on obvious workflow follow-ups (e.g. after 48-2 fast-path, follow-up turns skip concierge re-resolution when the lane already carried a resolved identity).
  - [x] 2-2. Parallelize safe independent prep steps (only when dependencies are explicit; document forbidden parallel pairs after inventory).
  - [x] 2-3. Cache cheap deterministic workflow context where it avoids repeated recomputation.
  - [x] 2-4. Document invariants and invalidation rules for any cache introduced in 2-3.
- [x] 3. Build the acceptance battery
  - [x] 3-1. Add realistic prompts for build → edit → rerun → schedule and Save As → continue → schedule. Test layer: deterministic scripted turns with mocked LLM provider for CI stability; optional nightly with a real model for end-to-end confidence.
  - [x] 3-2. Include both positive cases and common failure-recovery cases such as build → bad edit → mutation failure → corrected edit → successful run → schedule. Correctness is asserted structurally (graph state, schedule record, resolver result) — not on model copy.
  - [x] 3-3. Reuse or extend fixtures from [45-workflow-generation-hardening](45-workflow-generation-hardening.md) eval harness where scenarios overlap, to avoid duplicating test infrastructure.
- [x] 4. Define rollout gates
  - [x] 4-1. Set initial target ceilings for DAN-owned latency, for example: obvious workflow follow-up routing/context resolution under about 250ms p50 and 600ms p95, schedule binding under about 200ms p50 and 500ms p95, and medium mutation-preview prep (defined as: dry-run of a typical 10-node graph with 2-3 edge mutations) under about 800ms p50 and 1500ms p95.
  - [x] 4-2. Require acceptance-battery pass rates before rollout.
  - [x] 4-3. CI stability strategy: latency gates run on a stable/nightly environment (not per-PR) using **relative** regression vs checked-in baseline with slack (e.g. no stage regresses more than 20% p95 vs baseline). Per-PR CI runs the acceptance battery for correctness only, not timing.

## Decisions
- This sub-plan measures only DAN-controlled latency, not upstream provider latency.
- Acceptance sequences should mirror how real users actually use DAN.app, not just idealized unit-test flows.
- Baseline measurement and acceptance-scenario design should begin early even though the final optimization pass and rollout gate happen at the end of the phase.
- Instrumentation shipped as shared helpers in `src/dan/server/workflow_latency.py`, with stage samples attached to workflow-lane audit metadata and mutation-preview timing.

## Notes
- This is the closing gate for the whole phase: continuity is not enough if it still feels slow or inconsistent.
- Acceptance battery lives under `tests/eval/` alongside existing benchmark harnesses; scenarios tagged with phase markers for selective runs.
- Rollout gates are informational during development and blocking for the final phase merge.
- The eval harness now uses stable per-fixture `thread_id`s plus `compute_graph_revision(...)` instead of timestamp/version placeholders, and the checked-in baseline lives at `tests/eval/baselines/workflow_followup_latency.json`.
