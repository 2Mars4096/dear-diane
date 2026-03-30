# 45-4: Meta Workflow Builder Eval Harness

**Parent:** [45-workflow-generation-hardening](45-workflow-generation-hardening.md)
**Status:** not-started
**Goal:** Build a workflow-that-generates-workflows path as an evaluation harness with explicit inputs, outputs, validation stages, and delivery artifacts, without making it the default product surface.

## Problem

The meta-builder idea is compelling, but risky if used too early:

- it can easily become "LLMs all the way down" without clear mechanical gates
- it can add latency and complexity instead of reducing them
- the current deterministic `node_worker.py` is not the same thing as an LLM worker system, so backlog ideas like context-enriched node workers need a clear home

## Tasks

- [ ] 1. Define the harness contract
  - [ ] 1-1. Formalize the pipeline shape: message → orchestrator → plan → execution → mechanical validation → minimal tests → delivery.
  - [ ] 1-2. Define the required artifacts at each stage: plan, candidate graph, validation report, minimal test report, delivery payload.
- [ ] 2. Keep it non-default and bounded
  - [ ] 2-1. Run it behind explicit flags or harness entrypoints only.
  - [ ] 2-2. Bound recursion, retries, and worker counts up front.
- [ ] 3. Add context-rich worker contracts where needed
  - [ ] 3-1. If prompt-driven node/section workers are introduced, include whole-goal plus predecessor/successor context.
  - [ ] 3-2. Keep those contracts explicit and versioned rather than hidden in prompts.
- [ ] 4. Reuse mechanical validation and minimal tests
  - [ ] 4-1. Feed candidates through the same hardened contract from 45-2.
  - [ ] 4-2. Add a minimal runnable test pack before delivery.
- [ ] 5. Evaluate honestly
  - [ ] 5-1. Compare the harness against the direct surface and the speed-first path.
  - [ ] 5-2. Retire it if it is slower and not materially better.

## Decisions

- The meta-builder is an eval harness first, not a shipping-default path.
- Any prompt-driven worker layer must publish explicit I/O contracts and bounded budgets.
- Mechanical validation remains the authority; the harness is not allowed to self-certify.

## Notes

- This sub-plan intentionally absorbs the backlog items "meta workflow builder" and "node-generation context enrichment" into one explicit experimental track.
- The current deterministic node-plan layer should not be retroactively described as an LLM worker system.
