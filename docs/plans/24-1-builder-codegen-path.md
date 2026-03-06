# 24-1: Builder Codegen Path

**Parent:** [24-reliable-generation](24-reliable-generation.md)
**Status:** completed
**Goal:** Make builder DSL codegen the primary generation path for new workflows.

## Motivation

The current workflow generation path asks LLMs to produce low-level graph operations (add_node, add_edge with exact port specs) via `MUTATION_TOOL_SCHEMA`. This abstraction level is poorly suited to LLMs — it leads to schema drift, sprawling normalization patches, and unreliable auto-retry convergence. LLMs are better at producing high-level intent or readable code.

The builder DSL (`dan.builder`) is a proven authoring surface: 20+ node-creation methods, `>>` chaining, f-string magic, context managers. The deterministic `build()` handles ports, edges, and validation. The `GENERATE_CODE` path in `meta/planner.py` already runs LLM-generated builder code in a sandbox and extracts the resulting Graph — but it is opt-in, lightly tested, and not wired into the ChatManager build-mode flow.

This sub-plan elevates builder codegen to the primary path for *new* workflow creation. Build mode will generate Python builder code, run it in `SandboxRunner`, validate the output, and only then save/apply. Mutate mode (incremental edits to existing graphs) keeps using `MUTATION_TOOL_SCHEMA`. Scope is narrow: one-shot generation, deterministic validation, clear error reporting, and a small bounded repair pass — no hidden retry loops.

This plan also defines the canonical validation contract reused by 24-2 and 24-4. The intent compiler may short-circuit covered cases before codegen, but unsupported or ambiguous builds must eventually land here and pass the same validation gate.

## Current State

| Component | Status |
|-----------|--------|
| `GENERATE_CODE` in `meta/planner.py` | Exists, opt-in via `planner_allow_code_generation`. Uses `_BUILDER_CODE_HARNESS`, runs via `SandboxRunner`, reads `graph` from `_result.json`. No builder-focused prompt, no self-knowledge injection. |
| `_BUILDER_CODE_HARNESS` | Wraps user code, expects `graph`/`wf`/`workflow`/`g` variable. Writes `_result.json`. Does not capture build errors or validation errors separately. |
| `SandboxRunner` | Subprocess with 30s timeout, memory limits, structured `_result.json` output. Works. |
| `validate_graph()` | 12 design-time checks. Works. |
| `SelfKnowledgeIndex` | Indexes DAN docs for planner grounding. Exists; not yet used for codegen prompts. |
| `llm-api-guide.md` | Full builder DSL reference. Not yet injected into codegen prompts. |
| `ChatManager` build mode | Uses `MUTATION_TOOL_SCHEMA` for both empty-graph and non-empty. No codegen path. |
| `decompile()` | Graph → Python. Round-trip validation possible. |

**What works:** Sandbox execution, harness variable extraction, validation, decompiler.
**What doesn't:** No codegen-specific prompt, no self-knowledge in codegen flow, no validation pipeline after sandbox, no ChatManager integration, no structured error reporting, no bounded repair.

## Tasks

- [x] 1. Codegen prompt engineering
  - [x] 1-1. Create `CodegenPromptBuilder` (or extend `PlanningPromptBuilder`) with a system prompt that teaches the builder DSL: workflow init, node methods (llm, tool, code, gate, for_each, while_loop, composite, orchestrator, parallel_subagents, rag, validator), `>>` chaining, `wf.edge()`, `NodeRef` for loop wiring, `wf.build()`.
  - [x] 1-2. Extract builder DSL sections from `llm-api-guide.md` (sections 2–3, key node types, edge wiring) into an injectible prompt block; include in codegen system prompt.
  - [x] 1-3. Add few-shot examples for 6 patterns: chain (3-node sequential), review_loop (gate/while with draft→review→gate), fan_out (for_each or parallel_subagents), rag_qa (rag + llm), while_loop (gate with condition), composite (nested sub-graph).
  - [x] 1-4. Integrate `SelfKnowledgeIndex.retrieve_for_goal()` into codegen prompt: pass goal, get relevant API chunks, append as `## Relevant API Reference` section.
  - [x] 1-5. Format user prompt with: goal, available tools (from discovery), available skills, error context (for retry), and plan constraints (inputs/outputs if provided).

- [x] 2. Sandbox execution harness
  - [x] 2-1. Extend `_BUILDER_CODE_HARNESS` to wrap user code in try/except: catch `SyntaxError`, `ImportError`, `NameError`, and other build-time exceptions; write structured `_result.json` with `{"error": {"type": "...", "message": "...", "line": N}}` on failure instead of exiting with stderr only.
  - [x] 2-2. On success, keep writing the Graph to `_result.json`; add optional `source_code` field in the harness output (or return it separately from the planner) for traceability.
  - [x] 2-3. Configure `SandboxConfig` for builder code: timeout 30s (adequate), memory 256MB (builder code is not compute-heavy), `pass_env` for `DAN_*` if needed.
  - [x] 2-4. Define a structured output contract: `_result.json` is either `{graph: {...}, source_code?: "..."}` on success, or `{error: {type, message, line?}}` on failure. Update `_execute_generate_code` to parse and handle both shapes.

- [x] 3. Validation pipeline
  - [x] 3-1. After sandbox produces a Graph dict: run `Graph.model_validate()` for schema compliance; on failure, classify as fatal (wrong structure, invalid node types).
  - [x] 3-2. Run `validate_graph()` for design-time checks; collect errors. Classify: recoverable (port mismatch, missing edge, reachability) vs fatal (wrong node types, impossible topology, cycles in disallowed places).
  - [x] 3-3. Define the rollout validation gate: runtime path requires `Graph.model_validate()` + `validate_graph()`; round-trip parity (`decompile()` → rebuild) is mandatory in tests and optional as a debug diagnostic in production.
  - [x] 3-4. Verify entry/exit points: at least one entry, exit points reference existing nodes. Include in validation pipeline.
  - [x] 3-5. Return a `ValidationResult` dataclass: `success`, `errors`, `warnings`, `recoverable`, `fatal`.

- [x] 4. ChatManager integration
  - [x] 4-1. Detect build mode: `is_empty_graph` (node_count==0, edge_count==0) when `workflow_id` exists but graph is empty. Route to codegen path instead of mutation path.
  - [x] 4-2. Define the handoff point from 24-2: if intent extraction reports `fully_covered`, ChatManager may use the deterministic compiler fast path; otherwise it must invoke the 24-1 codegen path. Do not maintain two unrelated build-mode orchestration branches.
  - [x] 4-3. Add codegen tool schema or alternate completion flow: when build mode, use a prompt that asks for builder Python code (or use a tool like `generate_workflow_code` with `code` string). Prefer a dedicated codegen completion over overloading the mutation tool schema.
  - [x] 4-4. Emit new chat events: `ChatCodeGeneratedEvent` (code snippet, before run), `ChatValidationResultEvent` (success/failure, errors), `ChatGraphCreatedEvent` (workflow_id, graph summary). Add to `ChatStreamEvent` union if needed.
  - [x] 4-5. On success: save graph via `GraphStore`, assign new `workflow_id`, track revision. Return graph to client.
  - [x] 4-6. Mode switching: build mode → intent fast path or codegen; mutate mode → mutation (unchanged). Ensure `_build_messages` and tool choice respect mode.

- [x] 5. Error reporting and bounded repair
  - [x] 5-1. Extract structured error info from sandbox: parse stderr for `SyntaxError` (line number), `ImportError` (module name), `NameError` (name), `ValidationError` (Pydantic). Produce `{error_type, message, line?, snippet?}`.
  - [x] 5-2. Hand off first-failure repair to 24-4's bounded diagnosis loop instead of embedding ad hoc retry logic here. The codegen path owns error collection and final user-facing diagnostics; 24-4 owns targeted correction policy.
  - [x] 5-3. Cap repair at one diagnosis pass with at most one LLM-assisted correction round. If repair fails: surface the generated code, validation output, and structured error. No further retries.
  - [x] 5-4. Add `CodegenDiagnostics` model: `attempts`, `errors`, `final_code`, `validation_errors`, `diagnosis_summary` for logging and UI display.

- [x] 6. Planner integration
  - [x] 6-1. Make `GENERATE_CODE` the default action for new workflow generation when `planner_allow_code_generation=True`. Update `PlanningPromptBuilder.SYSTEM_TEMPLATE` to include GENERATE_CODE as the preferred option over GENERATE when creating from scratch.
  - [x] 6-2. Add `build_codegen_prompt()` to `PlanningPromptBuilder` (or a dedicated `CodegenPromptBuilder`): produces system + user prompt for code generation, including self-knowledge RAG and few-shot examples.
  - [x] 6-3. Implement `_compile_generate_code()` in `WorkflowPlanner`: call codegen prompt builder, get LLM completion (code string), run sandbox, run validation pipeline, return Graph or raise with diagnostics.
  - [x] 6-4. Keep the old declarative `GENERATE` path only as a rollout escape hatch, not as the steady-state design. If retained, gate it behind a flag and log every fallback for removal follow-up.
  - [x] 6-5. Wire `WorkflowPlanner.plan()` to use `_compile_generate_code` when action is `GENERATE_CODE`; ensure `plan()` returns the compiled graph and exposes a uniform validation/diagnostic contract to downstream callers.

- [x] 7. Tests
  - [x] 7-1. Unit tests: `CodegenPromptBuilder` produces prompts containing builder DSL snippets and few-shot examples for chain, review_loop, fan_out, rag_qa.
  - [x] 7-2. Unit tests: sandbox harness correctly captures `SyntaxError`, `ImportError`, and successful Graph output; `_result.json` shape matches contract.
  - [x] 7-3. Unit tests: validation pipeline catches missing ports, bad edges, cycles; classifies recoverable vs fatal.
  - [x] 7-4. Integration test: ChatManager build mode (empty graph) triggers codegen path, not mutation path; mock LLM returns valid builder code, verify graph is saved.
  - [x] 7-5. Integration test: round-trip — generated code → build → Graph → decompile → rebuild produces structurally equivalent graph (node/edge count, entry/exit).
  - [x] 7-6. Integration test: failed codegen returns structured diagnostics; retry receives error context; second failure surfaces to user without further retries.

## Files to Touch

| File | Changes |
|------|---------|
| `meta/planner.py` | Extend `_BUILDER_CODE_HARNESS` for structured error output; add `_compile_generate_code()`; make GENERATE_CODE default for new workflows; integrate codegen prompt builder; keep old declarative fallback behind explicit rollout control if retained |
| `meta/self_knowledge.py` | Ensure `retrieve_for_goal()` (or equivalent) is callable for codegen prompts; may need `retrieve_for_codegen(goal, token_budget)` |
| `sandbox/runner.py` | No changes if harness handles errors internally; optional: support `_error.json` as alternate output |
| `validation/graph.py` | No changes; use existing `validate_graph()`. Optional: add `classify_validation_error()` helper |
| `server/chat_manager.py` | Build-mode detection; intent-fast-path handoff; codegen completion path; new events (`ChatCodeGeneratedEvent`, etc.); save graph on success |
| `docs/llm-api-guide.md` | No changes; extract sections for prompt injection |
| `builder/decompiler.py` | No changes; use for round-trip check |
| `tests/test_meta/test_planner_codegen.py` | New: codegen prompt, harness, validation pipeline tests |
| `tests/test_server/test_chat_manager_codegen.py` | New: ChatManager build-mode codegen integration tests |

## Decisions

- (filled in during execution)

## Notes

- (filled in during execution)
