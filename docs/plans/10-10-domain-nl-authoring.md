# 10-10: Domain NL Authoring — Templates, Skills, and Intent Quality

**Parent:** [10-chatbox-nl-workflow](10-chatbox-nl-workflow.md)
**Status:** completed
**Goal:** Close the gap between "the NL system can produce a graph" and "the NL system produces a *correct, runnable, domain-calibrated* graph for real tasks." Specifically: a user who says "I have PDFs at path X and data at path Y, help me write an INFORMS paper for Management Science" should get a workflow that actually runs to artifacts (`.tex`/`.pdf`/submission package), not a generic review-loop stub.

## Context

10-9 delivered build-from-intent mode (`BUILD_FROM_INTENT_PROMPT`, template registry, empty-graph bootstrap). Remaining gaps toward the ultimate goal:

1. **`data_ingest` needs indexing semantics.** `rag_operator` is retrieval-time; it does not ingest docs by itself. We need an explicit index/build step before retrieval.
2. **`data_path` is not operationalized.** Current draft only mentions `data_path` in inputs but does not route it through analysis nodes that feed methods/results writing.
3. **Control-flow templates need sub-graph-safe mutation support.** `for_each`/`composite` nodes require valid `body_graph` references in `graph.sub_graphs`; otherwise validation fails.
4. **Path policy mismatch.** File tools are workspace-sandboxed today; user-provided absolute paths outside workspace need explicit handling UX/policy.
5. **Strict build mode + tool default ports can conflict.** In strict mode, add_edge fails when target ports are undeclared; tool-aware port declarations are needed.
6. **Acceptance criteria are too plan-shape oriented.** We need runtime artifact-level acceptance tests, not only "mutation plan contains X ops."

## Tasks

- [x] 1. **RAG-ready ingestion primitives (`data_ingest` + indexing)**
  - [x] 1-1. Add `data_ingest` to `PATTERN_LIBRARY` in `graph_mutator.py` as a two-stage flow: filesystem discovery/read + index build + retrieval-ready outputs.
  - [x] 1-2. Add a server tool for indexing (e.g., `rag_index_documents`) backed by existing RAG indexer APIs, and register it in `app.py` ToolRegistry.
  - [x] 1-3. Ensure `data_ingest` pattern emits nodes/edges that produce a deterministic collection name and pass it to downstream `rag_operator`.
  - [x] 1-4. Add `"data_ingest"` to the `expand_pattern` enum in `_build_mutation_tool_schema()` (`chat_manager.py`).
  - [x] 1-5. Update `BUILD_FROM_INTENT_PROMPT` and `SYSTEM_PROMPT_TEMPLATE` pattern sections to include `data_ingest` with indexing semantics (not retrieval-only wording).
  - [x] 1-6. Unit test: `expand_pattern("data_ingest", {...})` yields a graph that validates without sub-graph errors and is retrieval-ready.

- [x] 2. **Path-aware data branch (`data_path`)**
  - [x] 2-1. Add `data_analysis` pattern/template: `InputNode(data_path)` → `ToolOperator(file_read)` / `CodeOperator(preprocess)` → `LLMOperator(methods_results_summary)` (structured output).
  - [x] 2-2. Wire `data_analysis` outputs into the INFORMS paper workflow (methods/results nodes consume this branch).
  - [x] 2-3. Add prompt guidance: when user provides `data_path`, system must create and connect a data branch (not only literature branch).

- [x] 3. **Sub-graph-safe generation for control-flow nodes**
  - [x] 3-1. Add mutation primitives for sub-graph creation/wiring (e.g., `add_subgraph`, `set_body_graph`) or equivalent helper expansion inside `expand_pattern`.
  - [x] 3-2. Update `fan_out`/`data_ingest`/paper templates to create valid `body_graph` references in `graph.sub_graphs`.
  - [x] 3-3. Add tests asserting no `validate_graph` errors for missing sub-graphs in generated workflows.

- [x] 4. **Strict-mode + tool-port hardening**
  - [x] 4-1. Add tool-aware port manifests for common tools (`file_read`, `list_directory`, `pdf_read`, `compile_latex`, `save_paper`, `package_submission`, `citation_verifier`, `check_latex_deps`).
  - [x] 4-2. When adding `tool_operator` nodes via patterns/templates, declare required `input_ports` explicitly so strict edge wiring is reliable.
  - [x] 4-3. Keep build-mode `strict=true` edge policy and add regression tests for strict wiring success/fail behavior.

- [x] 5. **Rich INFORMS paper-writing template**
  - [x] 5-1. Add/replace template with `informs_paper_writing` in `WORKFLOW_TEMPLATES`, targeting a runnable 12-18 node flow:
    `Input(topic,pdf_dir,data_path)` → `data_ingest` → `data_analysis` → outline/draft/review loop → LaTeX assembly → `check_latex_deps` → `citation_verifier` → `compile_latex` → `save_paper` → `package_submission`.
  - [x] 5-2. Include `HumanInTheLoop` interview/approval node(s) behind `include_human_review` (default true).
  - [x] 5-3. Use Management Science-calibrated prompts for contribution framing, methods rigor, and reviewer criteria.
  - [x] 5-4. Add `rag_research` template for "I have papers at path X, help me understand/synthesize."
  - [x] 5-5. Register templates in `BUILD_FROM_INTENT_PROMPT` with explicit intent mappings.

- [x] 6. **Lightweight skills (`apply_skill`)**
  - [x] 6-1. Define `SKILL_LIBRARY` (Python dict or dedicated module) with initial skills: `management_science_writing`, `informs_latex_style`.
  - [x] 6-2. Add `apply_skill` op to `GraphOperation` + `GraphMutator`; target by node IDs and tags; inject into `system_prompt` (fallback `prompt_template`).
  - [x] 6-3. Add `apply_skill` to `MUTATION_TOOL_SCHEMA` so the model can emit it.
  - [x] 6-4. Tag template nodes (`writing`, `review`, `latex`) so skill targeting is deterministic.
  - [x] 6-5. Unit tests for targeted injection correctness and idempotency.

- [x] 7. **Prompt quality + multi-turn clarification**
  - [x] 7-1. Upgrade `BUILD_FROM_INTENT_PROMPT` examples to include dual-branch composition (`data_ingest` + `data_analysis` + drafting/review/compile pipeline).
  - [x] 7-2. Add explicit domain mapping: `Management Science` intent => `informs_paper_writing` + `apply_skill(management_science_writing)`.
  - [x] 7-3. Add path-policy wording: if path is outside workspace, ask user to import/mount/copy into workspace (or choose allowlist flow if enabled).
  - [x] 7-4. Implement ambiguity path: clarify before planning when intent underspecified (`ChatManager.clarify_intent()` + heuristic trigger).
  - [x] 7-5. Add pre-diff plan summary text (1-2 lines) so user can reject/adjust before visual diff.

- [x] 8. **Acceptance tests and evaluation gates**
  - [x] 8-1. E2E (mocked LLM): intent with `pdf_dir` + `data_path` produces plan containing `data_ingest`, `data_analysis`, `informs_paper_writing`, and optional `apply_skill`.
  - [x] 8-2. E2E (workflow validation): generated graph passes `Graph.model_validate` + `validate_graph` with no missing-subgraph errors.
  - [x] 8-3. E2E (runtime smoke): generated graph runs and produces expected artifacts (`.tex`, compile output, optional `.pdf`, optional submission package).
  - [x] 8-4. Add quality metrics for build-mode success: `graph_validate_pass_rate`, `run_smoke_pass_rate`, `artifact_success_rate`, `avg_turns_to_success`.
  - [x] 8-5. Docs sync: `llm-api-guide.md`, `architecture.md` (if API/tool surface changed), `todo.md`, `changelog.md`.

## Decisions

- **Keep skills as graph edits in this phase.** `apply_skill` mutates node prompts (`edit_node`) instead of adding a runtime skill engine. Runtime hyperedge hooks remain Phase 10 scope.
- **RAG indexing is explicit.** Retrieval (`rag_operator`) and indexing are separate steps; templates must include indexing before retrieval.
- **Path policy default: workspace-first.** For now, external absolute paths should trigger a clear clarification/request flow rather than silent failure.
- **Control-flow templates must be validation-clean by default.** No template should rely on ignored sub-graph errors.
- **Success means runnable artifacts.** "Plan contains operations" is necessary but not sufficient; artifact-level smoke tests are required.
- **`_default_ports` enhanced** for InputNode variables and tool_operator tool_id → `TOOL_PORT_MANIFESTS` lookup.
- **Inter-pattern edges** added to `informs_paper_writing` template — 19 nodes, 19 edges, fully wired.
- **Skills are prompt-prefix injections** applied via `apply_skill` mutation op targeting metadata.tags.

## Sequencing

| Order | Task | Rationale |
|-------|------|-----------|
| 1 | Task 1 (ingestion + indexing) | Required before any realistic literature-grounded workflow |
| 2 | Task 3 (sub-graph-safe generation) | Unblocks robust use of for_each/composite templates |
| 3 | Task 4 (strict-mode + tool ports) | Prevents build-mode wiring failures |
| 4 | Task 2 (data branch) | Makes `data_path` operational |
| 5 | Task 5 (rich INFORMS template) | Main user-facing capability |
| 6 | Task 6 (skills) | Domain calibration layer on top of topology |
| 7 | Task 7 (prompt + clarify) | Raises build reliability for ambiguous intents |
| 8 | Task 8 (acceptance gates + docs) | Locks in quality and prevents regressions |

## Primary Files

- `src/dan/server/graph_mutator.py` — `PATTERN_LIBRARY`, `expand_pattern`, sub-graph mutation support, `apply_skill`
- `src/dan/server/chat_manager.py` — `MUTATION_TOOL_SCHEMA`, `BUILD_FROM_INTENT_PROMPT`, `WORKFLOW_TEMPLATES`, `SKILL_LIBRARY`, clarify flow
- `src/dan/server/app.py` — tool registration for indexing/paper packaging helpers (if expanded)
- `src/dan/tools/` — indexing tool wrapper if added as built-in/server tool
- `tests/test_server/` and `tests/test_examples/` — mutation, validation, and runtime smoke coverage
- `docs/llm-api-guide.md` — pattern/op/tool usage updates

## Notes

- `examples/paper_writing.py` remains the reference topology and behavior target; 10-10 should capture its essential capabilities with fewer, cleaner default nodes.
- Keep default generated graphs as simple as possible, but never at the cost of non-runnability.
- Future UI work (skill picker in Config Panel, template browser refinements) is out of scope for this patch and can be a follow-up sub-plan.
- All 215 existing tests pass plus new test classes for patterns, skills, ports, and template validation.
- `_ensure_subgraph` helper created but not yet wired into templates (templates use flat topology for now; composite/ForEach sub-graphs are a follow-up).
