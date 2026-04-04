# 45-4: Meta Workflow Builder Eval Harness

**Parent:** [45-workflow-generation-hardening](45-workflow-generation-hardening.md)
**Status:** in-progress
**Goal:** Build a workflow-that-generates-workflows path as an evaluation harness with explicit inputs, outputs, validation stages, and delivery artifacts, without making it the default product surface.

## Problem

The meta-builder idea is compelling, but risky if used too early:

- it can easily become "LLMs all the way down" without clear mechanical gates
- it can add latency and complexity instead of reducing them
- the current deterministic `node_worker.py` is not the same thing as an LLM worker system, so backlog ideas like context-enriched node workers need a clear home

## Tasks

- [x] 1. Define the harness contract
  - [x] 1-1. Formalize the pipeline shape: message → orchestrator → plan → execution → mechanical validation → minimal tests → delivery.
  - [x] 1-2. Define the required artifacts at each stage: plan, candidate graph, validation report, minimal test report, delivery payload.
- [ ] 2. Keep it non-default and bounded
  - [x] 2-1. Run it behind explicit flags or harness entrypoints only.
  - [ ] 2-2. Bound recursion, retries, and worker counts up front.
- [ ] 3. Add context-rich worker contracts where needed
  - [ ] 3-1. If prompt-driven node/section workers are introduced, include whole-goal plus predecessor/successor context.
  - [ ] 3-2. Keep those contracts explicit and versioned rather than hidden in prompts.
- [x] 4. Reuse mechanical validation and minimal tests
  - [x] 4-1. Feed candidates through the same hardened contract from 45-2.
  - [x] 4-2. Add a minimal runnable test pack before delivery.
- [ ] 5. Evaluate honestly
  - [x] 5-1. Compare the harness against the direct surface and the speed-first path.
  - [ ] 5-2. Retire it if it is slower and not materially better.

## Decisions

- The meta-builder is an eval harness first, not a shipping-default path.
- Any prompt-driven worker layer must publish explicit I/O contracts and bounded budgets.
- Mechanical validation remains the authority; the harness is not allowed to self-certify.

## Notes

- This sub-plan intentionally absorbs the backlog items "meta workflow builder" and "node-generation context enrichment" into one explicit experimental track.
- The current deterministic node-plan layer should not be retroactively described as an LLM worker system.
- The initial harness now lives at `tests/eval/workflow_result_similarity_benchmark.py`.
- Current pipeline shape: fixture contract → reference Worker graph → DAN build-mode generated graph → graph validation → local execution of both graphs → deterministic similarity metrics → optional LLM judge → JSON report in `tests/eval/results/`.
- The reference side is intentionally lightweight for live runs: a short canonical-Worker LLM chain is used as the baseline so 10-case benchmark shells stay tractable on ordinary provider budgets.
- The initial fixture catalog contains 10 long-form tasks: equity daily brief, incident postmortem, product launch brief, vendor risk review, feedback synthesis, literature review digest, operations weekly brief, policy compliance assessment, meeting-to-project-plan, and hiring interview packet.
- `tests/eval/test_workflow_result_similarity_benchmark.py` covers fixture count, reference-graph validity, deterministic metric helpers, and `.env` JSON parsing needed for live benchmark shells.
