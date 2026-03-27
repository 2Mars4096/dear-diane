# 44-2: Node Worker Contracts and Node Tests

**Parent:** [44-structured-workflow-generation](44-structured-workflow-generation.md)
**Status:** not-started
**Goal:** Define the per-node worker contract for structured workflow generation so node specs can be turned into typed, testable node plans and configs before section assembly or final graph linking.

## Problem

The high-level spec agent can describe what each node should do, but the system still needs a concrete per-node contract for turning that spec into an executable node.

Without that contract:

- node implementations drift from the original spec
- executor choice stays implicit instead of being classified up front
- section assembly inherits malformed ports, weak typing, or mismatched inputs/outputs
- testing becomes too coarse, so node-level failures are hard to isolate

This subplan keeps the scope on node contracts and node-level validation only. Final section linking and whole-graph repair belong to later subplans.

## Tasks

- [ ] 1. Define the node-spec input contract for worker consumption
  - [ ] 1-1. Require typed fields for node id, purpose, runtime `node_type`, execution family, inputs, outputs, dependencies, and section id
  - [ ] 1-2. Add support for worker hints such as test intent, failure risk, and expected side effects
  - [ ] 1-3. Make the contract explicit enough that workers do not need to infer graph structure
- [ ] 2. Define worker output shape for node plans and configs
  - [ ] 2-1. Emit a typed node plan that includes executor-specific configuration
  - [ ] 2-2. Emit typed input/output port declarations for later section assembly
  - [ ] 2-3. Require the worker output to be serializable and deterministic for debugging
- [ ] 3. Classify executor kind before node implementation
  - [ ] 3-1. Support at least the existing `GENERATE_SPEC_NODE_TYPES` set: `llm_operator`, `tool_operator`, `code_operator`, `gate` (there is no `shell` executor — code execution uses `code_operator`)
  - [ ] 3-2. Separate planner-visible runtime node type from execution family so workers do not confuse graph shape (`for_each`, `gate`) with how work is executed (`tool`, `llm`, `code`, `control_flow`)
  - [ ] 3-3. Decide whether control-flow types (`for_each`, `while_loop`, `parallel_subagents`, `orchestrator`, `reduce`, `router`, etc.) are classified at the worker level or introduced later during section assembly
  - [ ] 3-4. Allow richer specializations (e.g. `rag_operator`, `reflection`, `vote`) only when they map cleanly onto one of the supported kinds
  - [ ] 3-5. Reject nodes whose runtime node type or execution family cannot be inferred or justified from the spec
- [ ] 4. Add minimal but real node-level tests including semantic grounding checks
  - [ ] 4-1. Require each worker to validate its own input/output contract before acceptance
  - [ ] 4-2. Run a lightweight executor-specific smoke test or dry-run where applicable
  - [ ] 4-3. Fail the node if the worker cannot prove that its declared outputs are reachable from its declared inputs
  - [ ] 4-4. Verify semantic grounding per executor kind: `tool_operator` nodes must reference a tool that exists in the tool registry and bind its required arguments; `llm_operator` nodes with prompts claiming external actions must carry the tools to produce them; `code_operator` nodes must have non-empty executable code — reuse the existing rejection rules from `validate_workflow_build_contract()` (`_check_run_readiness`) but apply them at node-creation time
  - [ ] 4-5. Verify data-source reachability: if a node declares it consumes data from an upstream node, the upstream node must declare a compatible output; if it consumes external data, the spec-level data source declaration (44-1 task 1-6) must exist
  - [ ] 4-6. Emit a minimal runnable test contract per node or control-flow fragment so later section and whole-graph smoke tests know what success looks like beyond static validation
- [ ] 5. Keep worker parallelism bounded and explicit
  - [ ] 5-1. Read the worker pool cap from `DAN_STRUCTURED_WORKER_POOL_CAP` (default `8`) — reuse the same env var as 44-1 so the cap is consistent across the pipeline
  - [ ] 5-2. Ensure node workers can run independently when their specs do not overlap
  - [ ] 5-3. Record per-node timing so the later section planner can reason about cost and latency
  - [ ] 5-4. Make the per-node worker timeout configurable via `DAN_STRUCTURED_NODE_WORKER_TIMEOUT` (default derived from `DAN_MAX_GENERATION_SECONDS` divided by expected node count) — not a hardcoded constant
- [ ] 6. Add regression coverage for worker contract stability
  - [ ] 6-1. Test executor classification for realistic workflow node specs
  - [ ] 6-2. Test that invalid port typing or missing inputs fails before acceptance
  - [ ] 6-3. Test that worker outputs remain stable enough for section assembly to consume

## Likely Files

New:
- `src/dan/server/agent_runtime/node_worker.py` — new per-node worker implementation
- `tests/test_meta/test_node_worker.py` — new worker contract tests

Existing (modify or extend):
- `src/dan/meta/workflow_spec.py` — the typed spec contract introduced in 44-1
- `src/dan/meta/intent_schema.py` — existing `StageIntent` (has `name`, `description`, `stage_type`, `inputs`, `outputs`, `config`) which the node-spec contract should align with or supersede
- `src/dan/models/node_taxonomy.py` — existing `GENERATE_SPEC_NODE_TYPES` (`llm_operator`, `tool_operator`, `code_operator`, `gate`) and `RUNTIME_NODE_TYPE_MAP`
- `src/dan/server/agent_runtime/workflow_generation.py`
- `src/dan/meta/workflow_contract.py` — existing `validate_workflow_build_contract()` for per-node validation reuse
- `tests/test_meta/test_workflow_spec.py` — spec contract tests from 44-1
- `tests/test_meta/test_workflow_generation_pipeline.py`

## Decisions

- Node workers should consume a typed node-spec contract, not raw prose.
- Worker contracts should distinguish runtime node type from execution family. `StageType` is a stage archetype, not a drop-in replacement for either of those fields.
- Node-level tests should be minimal but real, with executor-specific smoke checks instead of a schema-only pass. The existing `validate_workflow_build_contract()` can validate individual node shapes as part of this.
- Worker outputs must be typed and serializable so later section assembly can remain deterministic. Decide whether workers emit `Node` model instances (from `dan.models.graph`) directly or an intermediate representation that section assembly compiles into `Node`s.
- Parallelism belongs at the worker layer, with a default cap of `8`, but this subplan does not handle final graph linking.

## Notes

- Control-flow nodes (`gate`, `if_else`, `while_loop`, `for_each`, `parallel_subagents`, `orchestrator`, `reduce`, `router`, `composite`, `goal_loop`) may need contract-level tests rather than side-effecting smoke tests, but they still need explicit inputs, outputs, and executor classification. The subgraph-bearing types (`SUBGRAPH_BEARING_RUNTIME_NODE_TYPES` in `node_taxonomy.py`) require additional structure.
- The worker contract should be strict enough that later stages do not need to reinterpret free-form prose.
- The existing `StageIntent` already carries `name`, `description`, `stage_type`, `inputs`, `outputs`, `config`, `review`, `conditional`, `loop`, and `parallelism` — the node-spec contract should decide how much of this to reuse vs. extend.
- Workers should produce output compatible with the builder DSL (`dan.builder.builder`) or the `Graph`/`Node` model (`dan.models.graph`) — one or the other, not an ad-hoc intermediate format that neither layer can consume.
- Shell-like actions should be expressed explicitly as `tool_operator` + `shell_command` unless the worker truly needs custom executable code.
