# 24-2: Intent Compiler

**Parent:** [24-reliable-generation](24-reliable-generation.md)
**Status:** completed
**Goal:** Add a constrained generation path for common workflow shapes via structured intent + deterministic compilation.

## Motivation

The primary generation path (24-1) asks the LLM to write full builder DSL Python code. That works well for novel workflows but is overkill for common shapes — chain, review loop, RAG QA, data pipeline — where the topology is predictable. LLMs are better at describing *what* they want (goal, stages, inputs, outputs) than at producing structurally correct low-level code.

The intent compiler provides a second, constrained path: the LLM emits a compact structured intent (goal, stages, data sources, review requirements). A deterministic compiler maps that intent to builder code or template expansion. For supported patterns, this yields more predictable output than free-form codegen. For unsupported patterns, the compiler fails fast and falls back to the 24-1 codegen path with the intent as additional context.

This is not a replacement for codegen — it is an optimization for the common case. The catalog of supported patterns stays small and explicit. Anything outside the catalog goes to codegen.

The intent compiler is therefore a fast-path front-end to 24-1, not a competing planner architecture. Covered intents compile deterministically and then pass through the same validation contract as builder codegen. Unsupported or ambiguous intents hand off immediately to 24-1 with the extracted intent attached as context.

## Current State

| Component | Status |
|-----------|--------|
| `GENERATE` (declarative) in `meta/planner.py` | `_compile_generate_spec()` maps `{nodes, edges}` JSON to `dan_graph_v1`. Fragile: manual port/edge defaults, no schema validation on the spec. |
| `PATTERN_LIBRARY` in `graph_mutator.py` | 6 patterns: chain, review_loop, fan_out, rag_qa, data_ingest, data_analysis. Each expands to mutation op sequences. Cannot compose patterns or handle novel topologies. |
| `WORKFLOW_TEMPLATES` in `chat_manager.py` | 3 templates: paper_writing, rag_qa, chain_3. Expand to mutation ops. Not driven by structured intent. |
| Builder DSL in `builder/` | Full programmatic workflow construction. Target for compiled output. |
| `SandboxRunner` in `sandbox/runner.py` | Subprocess execution. Intent compiler produces builder code; if fully covered, it may compile in-process, but the output must still satisfy 24-1's validation contract. |

**What works:** Patterns exist, templates exist, builder DSL compiles to valid graphs.
**What doesn't:** No structured intent schema, no deterministic intent→builder compiler, no coverage checker, no ChatManager integration for intent-first flow, and no explicit handoff contract back to 24-1.

## Tasks

- [x] 1. Intent schema design
  - [x] 1-1. Define `WorkflowIntent` Pydantic model: `goal`, `stages` (list of `StageIntent`), `global_inputs`, `global_outputs`, `data_sources`, `review_requirements`, `constraints`.
  - [x] 1-2. Define `StageIntent` model: `name`, `description`, `stage_type` (enum: transform, review_loop, fan_out, rag_retrieval, tool_call, code_execution, human_approval), `inputs`, `outputs`, `config` (type-specific dict).
  - [x] 1-3. Define `ReviewRequirement`: `reviewer_prompt`, `condition`, `max_iterations`.
  - [x] 1-4. Define `DataSource`: `type` (file, url, rag_collection, api), `config`.
  - [x] 1-5. Add validation: all required fields, no circular stage dependencies, inputs/outputs type consistency.
  - [x] 1-6. Export JSON Schema for LLM function-calling via `model.model_json_schema()`.

- [x] 2. LLM intent extraction
  - [x] 2-1. Create system prompt that teaches the intent schema (simpler than full builder DSL); include supported `stage_type` enum values only.
  - [x] 2-2. Define function-calling tool `emit_workflow_intent` with JSON Schema from 1-6.
  - [x] 2-3. Add few-shot examples: "write a paper" → stages (research, outline, draft, review); "analyze data" → stages (ingest, process, summarize).
  - [x] 2-4. Constraint: LLM only picks from supported `stage_type` values — no free-form node types.
  - [x] 2-5. Clarification detection: if intent is ambiguous (e.g., multiple valid interpretations), return a clarification request instead of a plan.

- [x] 3. Intent→builder code compiler
  - [x] 3-1. Create `IntentCompiler` class with `compile(intent: WorkflowIntent) -> str` returning builder Python code.
  - [x] 3-2. Implement stage-type dispatch table: map each `stage_type` to a code-generation function.
  - [x] 3-3. Add template snippets for each supported pattern: chain of transforms, review_loop, fan_out + reduce, rag_retrieval → llm, tool_call, code_execution, human_approval.
  - [x] 3-4. Edge wiring: sequential stages → `>>`, fan_out stages → `for_each`, review stages → `while_loop`.
  - [x] 3-5. Input/output port mapping from intent fields to builder node ports.
  - [x] 3-6. Emit diagnostics when intent includes unsupported patterns (before handing off to 24-1).

- [x] 4. Coverage catalog
  - [x] 4-1. Document supported patterns: linear chain (N transform stages), review loop (draft → review → gate), fan-out/fan-in (source → for_each → reduce), RAG QA (retrieval → answer), data pipeline (ingest → process → analyze), tool-augmented chain (LLM + tool calls), human-in-the-loop approval.
  - [x] 4-2. Document composable combinations: chain + review_loop, fan_out + review_loop.
  - [x] 4-3. Add `COVERAGE_CATALOG` constant: mapping of (stage_type, composition) → supported/unsupported.
  - [x] 4-4. Document what falls back to codegen and why.

- [x] 5. Fail-fast and fallback (core classes done; integration deferred to task 6)
  - [x] 5-1. Implement `CoverageChecker` that validates intent against the catalog before compilation.
  - [x] 5-2. Return `CoverageResult`: `supported`, `unsupported` stages, `recommendation` (compile / fallback / partial).
  - [x] 5-3. Unsupported intents: fall back to 24-1 builder codegen path with intent as additional context. *(integration — deferred to task 6)*
  - [x] 5-4. Partial coverage follows one rule for Phase 14: if any required stage is unsupported, fall back to full codegen. Mixed deterministic/LLM assembly is explicitly deferred to avoid a third hybrid path.
  - [x] 5-5. Diagnostic messages: `describe_coverage()` returns human-readable summary; CoverageResult carries unsupported stage names for diagnostic logging.

- [x] 6. ChatManager integration
  - [x] 6-1. Add intent extraction as first step in build mode (before codegen).
  - [x] 6-2. If intent is fully covered: compile directly (fast path, no sandbox needed).
  - [x] 6-3. If partially/not covered: fall back to codegen (24-1) with intent in context.
  - [x] 6-4. Emit chat events: `intent_extracted`, `coverage_check`, `compilation_result`.
  - [x] 6-5. Graph validation after compilation uses the same runtime contract as 24-1 (`Graph.model_validate()` + `validate_graph()`), so covered and fallback flows return diagnostics in the same shape.

- [x] 7. Tests
  - [x] 7-1. Intent schema validation: valid intents pass, invalid intents (missing fields, circular deps, bad stage_type) fail with clear errors.
  - [x] 7-2. Compiler produces valid builder code for each supported pattern (chain, review_loop, fan_out, rag_qa, data_pipeline, tool_chain, human_approval).
  - [x] 7-3. Generated builder code compiles to valid `Graph` via `build()`.
  - [x] 7-4. Coverage checker correctly identifies supported vs unsupported intents.
  - [x] 7-5. Fallback triggers correctly on unsupported patterns; intent is passed to codegen context.
  - [x] 7-6. Round-trip: intent → compile → build → validate → decompile produces structurally consistent code.
  - [x] 7-7. Integration: ChatManager routes to intent compiler when appropriate; emits expected events.

## Files to Touch

| File | Changes |
|------|---------|
| `meta/intent_schema.py` | New: `WorkflowIntent`, `StageIntent`, `ReviewRequirement`, `DataSource` Pydantic models, JSON Schema export |
| `meta/intent_compiler.py` | New: `IntentCompiler`, `CoverageChecker`, `CoverageResult`, stage-type dispatch, template snippets |
| `meta/intent_extraction.py` | New: intent extraction prompt, `emit_workflow_intent` tool definition, few-shot examples |
| `meta/planner.py` | Integrate intent extraction / coverage check as a pre-codegen fast path; do not add a separate long-lived planner action for intent mode |
| `server/chat_manager.py` | Intent extraction as first build step; coverage check; route to compiler or 24-1 codegen; new events |
| `docs/llm-api-guide.md` | Add intent schema section, supported stage types, coverage catalog |
| `tests/test_meta/test_intent_schema.py` | New: schema validation tests |
| `tests/test_meta/test_intent_compiler.py` | New: compiler, coverage checker, fallback tests |
| `tests/test_server/test_chat_manager_intent.py` | New: ChatManager intent-path integration tests |

## Decisions

- Intent schema follows existing Pydantic v2 conventions from `dan.models.nodes` (`from __future__ import annotations`, `Field(default_factory=...)`, `str | None` unions, docstrings on models).
- Validation uses `@model_validator(mode="after")` for cross-field checks (duplicate names, circular deps, global input references).
- `to_json_schema()` is a thin classmethod wrapper around `model_json_schema()` for LLM function-calling.

## Notes

- 27 tests pass covering all models, enums, validators, and JSON schema export.
- Schema lives in `meta/intent_schema.py`; exported from `dan.meta.__init__`.
- 58 tests pass for `IntentCompiler` and `CoverageChecker` in `tests/test_meta/test_intent_compiler.py`. Covers all 7 stage types (syntax + exec→Graph), chaining, string escaping, helper functions, and coverage checker recommendations.
- Compiler uses 3-tuple return `(entry_var, exit_var, code_lines)` internally to handle multi-node stages like `rag_retrieval` (retrieve + answer). Builder's `>>` operator chains default ports (`text`→`input`, `chunks`→`query`, etc.) which the compiler relies on for sequential wiring.
- 25 tests pass for intent extraction in `tests/test_meta/test_intent_extraction.py`. Covers system prompt content, tool schema, few-shot parsing, coverage catalog, describe_coverage(), and Phase 14 fail-fast fallback logic.
- `COVERAGE_CATALOG` in `intent_compiler.py` defines 7 supported patterns (linear_chain, review_loop, fan_out_fan_in, rag_qa, data_pipeline, tool_augmented, human_gate). All composable.
- Phase 14 fail-fast: `CoverageChecker.check()` returns `"fallback"` whenever ANY stage is unsupported — no `"partial"` recommendation. This avoids a third hybrid compilation path.
- `describe_coverage()` on `CoverageChecker` returns human-readable summary for diagnostic logging and user feedback.
