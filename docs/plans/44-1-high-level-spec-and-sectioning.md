# 44-1: High-Level Spec and Sectioning

**Parent:** [44-structured-workflow-generation](44-structured-workflow-generation.md)
**Status:** completed
**Goal:** Define the high-level workflow spec contract and a deterministic sectioning strategy so DAN can build structured workflow graphs from a staged candidate graph instead of persisting empty graphs up front.

## Problem

Current workflow generation is too serial and too brittle:

- it often creates or leaves behind an empty graph before generation is complete
- prompt-to-graph latency is too high for medium-sized workflows
- section boundaries are not explicit enough to help debugging when generation fails
- the LLM is doing too much of the partitioning work, which makes output variable

We need a higher-level planner that decides what each node is for, what it consumes, what it emits, what runtime node type it should become, and what execution family it needs, then a deterministic partitioner that groups those nodes into debug-friendly sections without giving up speed.

## Tasks

- [x] 1. Define the high-level spec agent output contract
  - [x] 1-1. Specify required fields for each node: purpose, inputs, outputs, dependencies, runtime `node_type`, execution family, and test expectations
  - [x] 1-2. Define allowed planner node types and execution families separately — start from the existing `GENERATE_SPEC_NODE_TYPES` set (`llm_operator`, `tool_operator`, `code_operator`, `gate`) for planner-visible node types, then decide whether the structured spec layer should expand to include richer control-flow node types (`for_each`, `while_loop`, `parallel_subagents`, etc.) or keep the planner subset narrow and let later stages introduce them
  - [x] 1-3. Add explicit support for section/chapter labels so long chains can be split for debugging
  - [x] 1-4. Make the output suitable for candidate-graph generation rather than immediate persistence
  - [x] 1-5. Require semantic grounding declarations per node: `tool_operator` nodes must name the tool they call, `code_operator` nodes must declare the operation type, and `llm_operator` nodes that claim external side effects (fetch, search, read, write, save) must either carry tool bindings or be reclassified as `tool_operator` — enforcing the same rule that `validate_workflow_build_contract()` now applies at acceptance time, but at spec time so bad nodes are never built
  - [x] 1-6. Require data source declarations when the spec references external data — if a node says "fetch earnings data" or "search for papers," the spec must state the tool, API, or data source that makes this possible, not just the intent
  - [x] 1-7. Capture non-graph execution sidecars at the spec layer: expected run inputs, expected output artifacts, and optional schedule intent (`trigger`, delivery target, run profile) when the prompt asks for recurring execution
- [x] 2. Design the deterministic sectioning strategy
  - [x] 2-1. Use a weighted DAG partitioning approach instead of leaving sectioning fully to the LLM
  - [x] 2-2. Balance semantic affinity and execution speed, not just raw node count
  - [x] 2-3. Prefer keeping tightly coupled node pairs together, especially producer/consumer edges
  - [x] 2-4. Minimize cross-section edges while keeping sections interpretable for debugging
- [x] 3. Define the worker pool and orchestration bounds
  - [x] 3-1. Cap parallel node-worker execution via `DAN_STRUCTURED_WORKER_POOL_CAP` (default `8`) — not hardcoded
  - [x] 3-2. Describe how the spec agent fans out node design work by section
  - [x] 3-3. Define how sections are assembled and validated before the whole workflow is linked
  - [x] 3-4. Ensure the orchestration path still optimizes for message-to-working-workflow latency
  - [x] 3-5. Make the sectioning weight/affinity parameters configurable: max section size (`DAN_STRUCTURED_MAX_SECTION_SIZE`), cross-section edge penalty weight (`DAN_STRUCTURED_CROSS_SECTION_PENALTY`), and minimum section node count — all with sensible defaults, none hardcoded inline
- [x] 4. Add candidate-graph staging and acceptance boundaries
  - [x] 4-1. Ensure the graph is built in a candidate/staging form until section and graph validation pass
  - [x] 4-2. Define acceptance criteria for promoting the candidate graph to the saved workflow
  - [x] 4-3. Make section failure reports precise enough to isolate broken ports or boundary edges
  - [x] 4-4. Preserve enough structure in the candidate graph to retry only failed sections
  - [x] 4-5. Define a detached candidate-workspace model so build turns can keep a workflow id and thread context without mutating the persisted graph store until acceptance succeeds
- [x] 5. Add tests and eval coverage
  - [x] 5-1. Add unit tests for the workflow-spec contract and section boundaries
  - [x] 5-2. Add tests for deterministic partitioning behavior on small and medium DAGs
  - [x] 5-3. Add a regression test that ensures no empty graph is persisted before generation completes
  - [x] 5-4. Add an eval fixture that exercises the staged generation path on a realistic multi-step workflow
  - [x] 5-5. Add a regression test that schedule-bearing prompts preserve schedule intent as a sidecar instead of dropping it during graph authoring

## Likely Files

New:
- `src/dan/meta/workflow_spec.py` — new typed spec contract (extends/replaces `WorkflowIntent` from `intent_schema.py`)
- `src/dan/server/agent_runtime/workflow_sectioning.py` — new deterministic sectioning algorithm
- `tests/test_meta/test_workflow_spec.py` — new spec contract tests

Existing (modify or extend):
- `src/dan/meta/intent_schema.py` — existing `WorkflowIntent`, `StageIntent`, `StageType` contracts that the new spec should either extend or supersede
- `src/dan/meta/intent_compiler.py` — existing `CoverageChecker` (currently a shim returning `fully_covered=True`), natural routing integration point
- `src/dan/models/node_taxonomy.py` — existing `GENERATE_SPEC_NODE_TYPES` defining the planner subset (`llm_operator`, `tool_operator`, `code_operator`, `gate`)
- `src/dan/server/agent_runtime/workflow_generation.py`
- `src/dan/server/agent_runtime/workflow_generation_acceptance.py` — existing `accept_candidate_graph()` that the candidate staging should build on
- `src/dan/meta/workflow_contract.py` — existing `validate_workflow_build_contract()` with `apply_repairs=True`
- `src/dan/meta/graph_quality.py`
- `tests/test_meta/test_workflow_generation_pipeline.py`
- `tests/eval/workflow_contract_comparison_prompts.json`

## Decisions

- The high-level spec agent should emit a typed node/edge plan, not final graph code.
- The spec contract should distinguish runtime `node_type` from execution family so planner logic does not conflate graph shape with executor implementation.
- The new spec contract should explicitly decide its relationship to the existing `WorkflowIntent`/`StageIntent` in `intent_schema.py`: either extend that schema or supersede it with a richer typed alternative. The existing `StageType` enum (`transform`, `review_loop`, `fan_out`, `rag_retrieval`, `tool_call`, `code_execution`, `human_approval`, `conditional`, `loop`) is the current stage taxonomy.
- Sectioning should be deterministic and weighted, with the LLM only providing semantic hints.
- The generation path should stay in a candidate graph until section-level and whole-graph validation pass. The existing `accept_candidate_graph()` pattern should be reused rather than replaced.
- Sections should be explicit enough to debug, but coarse enough that the partitioner can still optimize latency.
- The worker pool cap should default to `8` but be configurable via env var, not hardcoded.
- Schedule intent should be modeled as a first-class sidecar of the authored workflow request, not smuggled into the graph structure itself.
- The existing `CoverageChecker` (currently a shim in `intent_compiler.py`) is a natural gating point for routing between the current intent-compiler path and the new structured path.
- All thresholds and tuning parameters introduced by this plan (section size limits, affinity weights, quality gates, timeout budgets) must be `DAN_*` env-var configurable with documented defaults — no inline magic numbers.

## Notes

- Completed foundation + runtime handoff: the typed spec and deterministic sectioner now feed the live structured branch in `src/dan/server/agent_runtime/workflow_generation.py`.
- Candidate graphs stay detached until section/boundary/whole-graph acceptance succeeds, and regression coverage now locks that the structured generation path does not mutate the graph store during candidate build.
- Realistic staged-path coverage now lives in `tests/test_meta/test_structured_generation_runtime.py` and the expanded `tests/eval/workflow_contract_comparison_prompts.json` fixture set.
- A good default section count is whatever the partitioner derives from DAG structure and cost, not one section per node.
- The spec agent should be allowed to create chapter-like groupings when the chain is long or semantically segmented.
- The runtime node type set is much richer than the planner subset: `llm_operator`, `tool_operator`, `code_operator`, `rag_operator`, `input`, `gate`, `while_loop`, `for_each`, `parallel_subagents`, `orchestrator`, `reduce`, `router`, `human`, `validator`, `composite`, `vote`, `agent_team`, `reflection`, `goal_loop`. The spec layer should decide which of these can appear in the high-level plan vs. which are introduced during node-worker implementation.
- There is no `shell` executor kind in the runtime — code execution goes through `code_operator`.
- If the user asks for periodic execution, the spec layer should preserve that schedule intent so later rollout code can hand it off to the existing `src/dan/server/concierge/scheduler.py` path after the graph is accepted.
