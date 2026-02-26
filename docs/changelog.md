# Changelog

## 2026-02-26
- [feat] **Templates always visible:** TabBar "+ New" picker now shows built-in starters (Blank, ReAct Agent, Plan-Execute) at the top, independent of saved graphs. Added `openTabFromTemplate` store action to open a new tab from a predefined template. Fixes "not seeing any templates" when graph list is empty or API fails.
- [fix] **if(use_builtin) gate:** LLM nodes with output_schema now emit a "result" port with the full structured object. Compiler wires strategy_manager.result → if gate (when source has "result") so the gate receives use_builtin for condition evaluation. GateExecutor flattens dict inputs for conditions. Fixes "name 'use_builtin' is not defined" when running vibe research.
- [fix] **Runs with flattened graphs:** Validation no longer flags if_else gates inside a while-loop body as "creates cycle" — only gates that receive a back-edge are cycle creators. Disabled flatten by default (DAN_FLATTEN_LOOP_BODIES=0) so saving doesn't persist a flattened graph that fails validation. Rebuild with run_multi_dept.py --build-only to restore working graph.
- [refactor] **Layout decoupled from examples:** Removed `_inject_orchestrator_department_groups` from layout.py — it hardcoded vibe research node names. Project no longer caters to specific examples. Vibe research example injects its own loop_groups in run_multi_dept.py --build-only.
- [feat] **Flatten loop bodies at top level:** `flatten_loop_bodies()` in layout.py inlines while-loop body composites into the main graph. Enabled by default; set `DAN_FLATTEN_LOOP_BODIES=0` to disable.
- [feat] **Vibe research flattened:** iteration_multi_dept inlines department_strategy — unpack, orchestrator, persist, unpack_strategy, strategy_manager, coder, backtest, run_strategy, bug_fixer, aggregator, governor all visible in one drill. Layout injects Orchestrator (unpack+orchestrator+persist) and Departments (unpack_strategy through aggregator) loop groups.
- [feat] **Plan 6-13 Multi-dept visualization completed:** Layout injects `expand_loop_groups_by_default` metadata for key composites (orchestrator + department_strategy). Editor drillIn/drillOut/jumpToLayer sync `loopGroups` from layer metadata so group toggles persist; saveGraph persists loop_groups to active sub_graph when editing drilled-in layer. Verified layout + loop_groups injection on vibe_research_multi_dept.
- [feat] **Plan 7-5 general tool design:** Shared executor `execute_python()` in `dan.server.exec`; `run_python(code, **context)` tool registered. Refactored `_run_strategy_script` to use shared executor. Deprecated `plot_backtest`, `save_grid_csv` (kept registered). Added `run_python.md` tool spec.
- [docs] **Plan restructuring:** Removed 7-multi-department-adaptive from project plans (example-specific). Moved to examples/vibe_research_md/WORKFLOW.md. Phase 7.5 dropped; vibe research → Backlog.
- [docs] **Plan 7-5 general tool design (improved):** Added state-of-the-art alignment (Cursor/Claude patterns), shared executor extraction, run_python interface spec, safety/sandbox notes, migration tasks, and comparison table.
- [fix] **write_csv → each(plot_one):** Compiler prefers `results` port for tool_operator when present, so write_csv.results flows to each(plot_one).items correctly.
- [fix] **Orchestrator/department visibility:** Loop groups in sub_graph metadata (injected by layout) now apply when drilling in. drillIn, drillOut, jumpToLayer inject loop_groups from the current layer's metadata.
- [refactor] **Vibe research cleanup:** Removed department_state.py (logic in persist_dept_state + app.py), quant_lib/strategy_registry.py (unused), quant_lib/strategy_schema.md (superseded by factor_schema.py), quant_lib/run_grid_backtest.py (superseded by run_grid.py + workflow_grid).
- [feat] **Multi-dept simplify + visibility:** Removed extract_results; write_csv accepts loop output directly, extracts results, returns results for each(plot_one). Renamed iteration→orchestrator_and_departments, strategy_creation_multi→department_strategy. Loop groups for Orchestrator and Departments in iteration body.
- [feat] **Backend dynamic layout:** `GET /api/graphs/{id}?layout=true` or `DAN_LAYOUT_ON_LOAD=1` applies topological layout to nodes (top-level + sub_graphs). Editor requests layout by default.
- [feat] **Plan 7-1:** Multi-dept visualization plan (orchestrator visible, departments, coder retry gate).
- [fix] **Multi-dept run_strategy "No strategy code provided":** run_strategy and backtest_runner were running on both gate branches. Added gate.false→run_strategy.branch_trigger and gate.true→backtest_runner.branch_trigger so each runs only on its branch. Compiler: add if_else gate to nodes_by_id so flow can reference it.
- [feat] **dan-serve auto-reload:** `--reload` is now default; server restarts on Python/workflow changes under project root. Use `--no-reload` for production.
- [feat] **vibe_research_md cleanup:** Removed 24 unused .md files and 3 run scripts (run.py, run_grid.py, run_adaptive.py). Kept only workflow_multi_dept and its 18 agent files; run_multi_dept.py remains.
- [feat] **Orchestrator context:** Prompt now states project purpose, data/output paths, and strategy manager responsibilities (create strategies, reflect on results, improve).
- [feat] **Multi-department max_factors:** Hard constraint as workflow input. When len(results) >= max_factors, orchestrator and governor set try_more=false. entry, unpack, persist, governor updated; run_multi_dept.py --max-factors (default 20).
- [feat] **Multi-department adaptive workflow:** workflow_multi_dept.md — orchestrator (max 6 depts, delete for good), strategy_manager_dept (naming: dept_code+numbering+desc), strategy_coder (script-as-param), run_strategy_script tool, department state persistence. Factor schema (factor_schema.py) with validate_factor_df; run_backtest_from_factor_df; Compustat 6-month lag (apply_compustat_lag). run_multi_dept.py script; json/Path in code executor builtins.
- [fix] **Code executor:** Added `NameError` and `Exception` to `_ALLOWED_BUILTINS` so governor/bug_fixer `try/except NameError` works in restricted exec namespace (was: `NameError: name 'NameError' is not defined`).
- [fix] **Adaptive workflow nodes:** Added defensive `try/except NameError` in aggregator and merge_planner for all inputs (results, strategies_tried, iteration, backtest_result, planner_output, etc.) so missing upstream outputs don't break the loop.
- [fix] **Loop gate condition:** GateExecutor flattens body output `{"result": {...}}` into condition_vars so `until: "not try_more"` can access try_more; adds try_more=True default when missing (was: `name 'try_more' is not defined`).
- [feat] **Adaptive strategy workflow:** `workflow_adaptive.md` — LLM-driven loop with central planner, strategy writer, backtest, governor. `adaptive_iteration` composite: unpack → central_planner → merge_planner → strategy_creation (unpack → strategy_writer → strategy_to_item → backtest_runner → aggregator → governor). Loop `entry_adaptive | loop(adaptive_iteration, until: "not try_more", max: 5)`. Governor overrides try_more when all backtests fail. Standardized nodes: `unpack_loop_input`, `unpack_adaptive_input`, `merge_planner` (single result object), `aggregator` (pass-through start_year/end_year), `governor` (result object for gate).
- [feat] **Adaptive ↔ grid integration:** Adaptive workflow now outputs to same CSV and plots as grid. `extract_adaptive_results` extracts results from loop; `gate.done → extract_results`; `extract_results.results → write_csv`; `extract_results | each(plot_one)`. Compiler: add loop gate to `nodes_by_id` immediately so flow can reference `gate.done`. `run_adaptive.py` script (mirrors `run_grid.py`).
- [fix] **Editor graph list:** TabBar awaits `loadGraphList()` before opening picker; added refresh button in picker header; toolbar Refresh now reloads both graph list and current tab (no dan-serve restart needed).
- [feat] **Adaptive workflow — bug fixer:** Added `bug_fixer.md` between backtest_runner and aggregator; catches malformed/failed backtest output so the loop never breaks. Updated workflow description to "revolving, auto-expanding"; tags include governor, bug-fixer. README notes drill path: adaptive_iteration → strategy_creation to see governor and bug fixer.
- [fix] **Grid workflow diagram:** Compiler no longer creates `workflow_inputs.item → plot_one` — ForEach body agents used only in `each()` are excluded from the main graph so they receive items from the ForEach at runtime, not from Workflow Inputs. Agents used elsewhere (e.g. `processor` in if/else) remain in the main graph.
- [fix] **Grid plots:** Compiler now uses composite "results" (not "result") for ForEach items when composite has both, so plot_one receives all 10 backtest results → 10 plots. Reverted cumulative prepend; time axis uses actual data dates.
- [feat] **Grid-search workflow:** `workflow_grid.md` — 10 strategies via ForEach nodes (no hardcoded loops). strategies_config → each(backtest_one) → write_csv + each(plot_one). Saves factors, CSV summary, cumulative quintile + LS plots. Tools: `run_backtest` (item, lookback, skip, return_series), `plot_backtest`, `save_grid_csv`.
- [fix] **Vibe research summarizer:** Handle missing `results` when upstream tool fails (NameError); README notes to restart `dan-serve` if "Unknown tool: run_backtest" appears.
- [feat] **Vibe research example:** `examples/vibe_research_md/` — simple factor research workflow in markdown format. Nested composite (data_loader → compute_factor), mock data (no CRSP/Compustat), code-only summarizer for demo without LLM API. `run.py` for CLI execution; `--build-only` saves `graphs/vibe_research.json` for the editor.
- [feat] **quant_lib:** CRSP/Compustat loaders, momentum factor, quintile backtest. Data from `AUTO_QUANT_ROOT` (default: `~/Dropbox/CUHK-phd/projects/auto-quant`). `run_backtest.py` CLI; `run_backtest` tool registered in server. `workflow_real.md` uses real data via tool.

## 2026-02-25 (Phase 3.75 — 6-12 control flow consolidation)
- [feat] `NODE_TYPE_CATALOG`: removed legacy `if_else` and `while_loop`; added `gate_if_else` (If/Else Gate) and `gate_while` (While Gate); removed generic `gate`
- [feat] `graphAdapter.ts`: `createDefaultNode` for `gate_if_else` and `gate_while` creates `GateNode` with appropriate `gate_mode`; added default case that throws for unknown types
- [feat] `paletteTemplates.ts`: removed redundant IfElse Gate and While Gate templates; ReAct template now uses CompositeNode with GateNode(while) body (LLM → Tool → Gate with back-edge) instead of WhileLoopNode
- [feat] `app.py`: `DAN_GATE_MIGRATION_ENABLED` defaults to `true` so legacy graphs are migrated on load
- [feat] `nodeIcons.tsx`: replaced `if_else`/`while_loop` with `gate_if_else`/`gate_while` icons
- [feat] `MentionAutocomplete.tsx`: added `gate` fallback to typeLabel for display
- [docs] Created `docs/plans/6-12-control-flow-consolidation.md`; updated `todo.md`
- [test] TypeScript clean, 870 backend tests passing

## 2026-02-25 (Phase 3.75 — 6-12 ReAct cyclic subgraph fix)
- [fix] `scheduler.py`: `_run_subgraph` now uses `_topological_levels_with_backedges` and `_execute_with_cycles` when the sub-graph contains gate(while) back-edges, fixing ReAct palette template (Composite with cyclic body) which previously executed no nodes
- [fix] `_execute_with_cycles`: added `skip_checkpoint=True` parameter so sub-graph execution does not overwrite parent checkpoints
- [test] Added `TestCompositeCyclicSubgraph::test_composite_with_gate_loop_in_body` — verifies Composite with gate loop in body runs correctly
- [test] 871 backend tests passing

## 2026-02-25 (Phase 6 — Extended Capabilities — backend implementation complete)
- [feat] **9-3 Handoff Validator:** `ValidatorNode` model (5 rule types: required_keys, non_empty, schema_conformance, type_check, custom_expression) with valid/invalid output port routing. `ValidatorExecutor` with `resolve_dotpath()` utility, `ValidationViolation` dataclass, `on_failure` modes (route/warn/halt), `strict_mode` early stop, `VALIDATION_RESULT` event emission. Boundary auto-insert utility (`generate_entry_validator`, `generate_exit_validator`, `insert_boundary_validators`) for composite nodes. 49 new tests.
- [feat] **9-2 Subprocess Sandbox:** `SandboxConfig` Pydantic model with `pass_env` glob matching, `SandboxRunner` (asyncio subprocess, timeout enforcement, output truncation, env filtering, memory limits via `resource.setrlimit`), `PythonAdapter` + `ShellAdapter` language adapters. `CodeExecutor` upgraded with subprocess routing (inline `exec()` fast-path preserved as default). `shell_command` tool upgraded with optional sandbox mode via `DAN_SANDBOX_SHELL` env var. `SANDBOX_STARTED`/`SANDBOX_COMPLETED` events. 50 new tests.
- [feat] **9-1 RAG Knowledge Retrieval:** `EmbeddingProvider` protocol with `OpenAIEmbeddingProvider` and `LocalEmbeddingProvider` (sentence-transformers via `asyncio.to_thread()`). `EmbeddingRegistry` with exact/prefix/default resolution. `VectorStore` protocol with 3 backends: `MemoryVectorStore` (pure-Python stdlib `math`, zero deps), `FAISSVectorStore` (faiss-cpu, L2-normalized inner product, persistence), `ChromaVectorStore` (native metadata filtering). `RAGExecutor` (embed → search → filter → return chunks/scores, store caching, events). `Indexer` for batch index lifecycle. 61 new tests (48 pass, 13 skip for optional deps).
- [infra] **Shared foundation:** Added `RAGOperator` and `ValidatorNode` to `Node` discriminated union, `NodeTypeRegistry`, `DEFAULT_OUTPUT_PORTS`, `_build_node()`, `_register_defaults()`. Added `wf.rag()` and `wf.validator()` builder DSL methods. Decompiler support for both new node types. 5 new `EventType` entries. `pyproject.toml`: optional dep groups `faiss`, `chroma`, `embeddings`, `jsonschema`, `all-rag`. Test suite: 771 passed, 15 skipped.
- [fix] **Boundary + chaining correctness:** `insert_boundary_validators()` now preserves arbitrary composite input/output port mappings (not hardcoded to `input`/`result`) by rewiring per-port and generating passthrough validator ports. `ValidatorExecutor` now mirrors input ports on successful validation, enabling boundary passthrough without breaking `valid/invalid` routing. Builder `>>` chaining aligned for new nodes: `validator` now defaults to target port `data`, `rag_operator` defaults to `query` (with decompiler default-edge detection updated accordingly). Added regression tests for custom composite ports and default chaining behavior.
- [feat] **Option B embedding-provider plumbing:** `EngineConfig` now supports first-class RAG embedding settings (`embedding_providers`, `embedding_model_provider_map`, `default_embedding_model`). Scheduler builds an `EmbeddingRegistry` during engine startup and threads it through `ExecutionContext`, so `RAGExecutor` resolves providers from runtime config by default (no ad-hoc context mutation required). Server `_get_engine_config()` now wires embedding provider defaults from env (`DAN_EMBEDDING_API_KEY`, `DAN_EMBEDDING_BASE_URL`, `DAN_DEFAULT_EMBEDDING_MODEL`, optional local provider toggle).

## 2026-02-25
- [docs] **Phase 6 planning complete:** Created top-level plan `9-extended-capabilities.md` and 3 sub-plans for the Extended Capabilities phase. 9-1: RAG / Knowledge Retrieval Node (EmbeddingProvider protocol supporting API + local models, VectorStore abstraction with FAISS/ChromaDB/memory backends, RAGOperator model + executor, index lifecycle management, editor/builder integration; 12 task groups, ~45 sub-tasks). 9-2: Subprocess Sandbox (SandboxRunner with asyncio subprocess, resource limits, language adapters for Python/shell, CodeExecutor upgrade preserving inline exec() fast-path, shell_command tool hardening; 11 task groups, ~35 sub-tasks). 9-3: Handoff Validator Node (ValidatorNode with 5 rule types, valid/invalid port routing, boundary auto-insert utility for composites, GateNode-style colored handles; 8 task groups, ~30 sub-tasks). Recommended execution order: 9-3 first (smallest), 9-2 second (safety foundation), 9-1 last (largest, benefits from sandbox + validators). Updated `todo.md` with plan links.
- [docs] **Phase 6 plan review fixes:** (1) 9-3 `schema_conformance` rule now validates runtime data payloads via `jsonschema.validate()` (optional dep) with pure-Python fallback, NOT design-time `check_schema_compatible()` which is schema-vs-schema. (2) 9-3 `on_failure` renamed `"skip"` to `"warn"` to avoid semantic collision with `RetryPolicy.on_failure="skip"` (which maps to `NodeStatus.SKIPPED`). (3) 9-2 goal reframed from "secure subprocess" to "operational guardrails" (timeouts, memory caps, output limits, env filtering) — explicitly NOT a security sandbox. (4) 9-2 added `pass_env: list[str]` to `SandboxConfig` for selective env var forwarding (supports exact names and glob prefixes like `"DAN_*"`), replacing blanket `DAN_*` removal that would break existing workflows needing `DAN_LLM_API_KEY`. (5) 9-2 removed `network_access` field (was promising unimplemented feature). (6) 9-1 `MemoryVectorStore` corrected from "numpy cosine similarity" to pure-Python stdlib `math` (zero external deps), numpy as future optimization. (7) 9 parent plan sandbox shared decision updated to match.

## 2026-02-25
- [docs] **Phase 6 planning complete:** Created top-level plan `9-extended-capabilities.md` and 3 sub-plans for the Extended Capabilities phase. 9-1: RAG/knowledge retrieval node (embedding provider protocol with API + local support, vector store abstraction with FAISS/ChromaDB backends, `RAGOperator` model + executor, index lifecycle via `VectorStoreManager`, editor/builder integration; 13 task groups, ~50 sub-tasks). 9-2: subprocess sandbox (`SandboxConfig` model replacing placeholder dict, async subprocess runner with timeout/resource limits, `CodeExecutor` upgrade from `exec()` to subprocess mode, `shell_command` hardening, security policies; 12 task groups, ~40 sub-tasks). 9-3: handoff validator (`ValidatorNode` with 5 rule types — required_keys/schema/non_empty/expression/type_check — `ValidatorExecutor` with dotpath traversal, composite boundary auto-insert utility; 8 task groups, ~35 sub-tasks). Recommended order: 9-3 → 9-2 → 9-1 (all independent). Updated `todo.md` with plan links.
- [test] **Loader advanced fixtures + tests:** Added `tests/fixtures/markdown/composite/` (inner_a, inner_b, outer_composite, workflow) and `tests/fixtures/markdown/schemas/outline.json`, `schema_agent.md`, `schema_workflow.md`. New `tests/test_loader/test_advanced.py` with 9 tests: composite agent parsing, compilation, subgraph structure, inner nodes/edges; linked JSON Schema port parsing, workflow compilation, schema loaded into port, missing-schema diagnostic.
- [docs] **Phase 5 planning complete:** Created top-level plan `8-markdown-agent-format.md` and 4 sub-plans for the Markdown Agent Format phase. 8-1: format design + parser (agent/workflow file formats, flow notation, port type inference, versioning). 8-2: markdown→graph compiler (`dan.loader.compile()`, auto-wiring, diagnostics). 8-3: validation + parity + advanced features (paper-writing rewrite, builder parity checklist, coexistence policy, composite agents, linked JSON Schema). 8-4: round-trip decompiler (graph→markdown, visual editor export, conformance tests). Updated `todo.md` with plan links.
- [docs] **Phase 5 plan review fixes:** Validated all 8-* plans against runtime models and compiler interfaces. Fixed: `gate_type`→`gate_mode` (matches `GateNode` model), loop compilation uses gate-style flat back-edges (not deprecated `WhileLoopNode` sub-graphs), `tool_arguments`→`tool_config`, `HumanInTheLoopNode.prompt_template`→`.prompt`, `RouterNode` uses `model`+`route_descriptions` (not freeform prompt), `input_mapping`→`input_mappings` (plural), gate default output port `true`/`done` (not `result`). Added: `InputNode` strategy for graph-level variables, best-effort decompilation policy with `DecompileResult.diagnostics`, type-specific frontmatter fields for router/human/tool. Fixed YAML dependency note (PyYAML is transitive via Pydantic, not stdlib).


## 2026-02-25 (Phase 5 — markdown loader compiler unblock)
- [feat] Added `src/dan/loader/compiler.py` with end-to-end markdown workflow compilation: agent→node mapping (`llm`/`tool`/`code`/`human`/`router`/`composite`), flow→edge/control-node compilation (`chain`, `each`, `loop`, `if`), `InputNode` inference for unresolved required inputs, context declaration mapping, and graph validation integration.
- [fix] Updated `src/dan/loader/__init__.py` to expose a real `compile_workflow()` export and make `load()` raise with formatted diagnostics on compile errors instead of returning `None`.
- [fix] Updated `src/dan/loader/parser.py` to stop swallowing flow parser import failures; flow-line parse failures are now handled per-line without aborting full workflow parse.
- [fix] Updated `examples/paper_writing_md/section_writer.md` and `examples/paper_writing_md/assembler.md` port contracts/code block output so `examples/paper_writing_md/workflow.md` compiles without errors (warnings remain for intentionally untyped control-flow edges).
- [test] Added/validated loader test coverage for compiler + parser/type/flow modules; `pytest tests/test_loader -q` now passes (`67 passed`), and the compiler-specific suite passes (`16 passed`).
- [docs] Updated Phase 5 tracking docs (`docs/todo.md`, `docs/plans/8-markdown-agent-format.md`, `docs/plans/8-1-format-design-parser.md`, `docs/plans/8-2-markdown-graph-compiler.md`, `docs/plans/8-3-validation-parity-advanced.md`) and updated `docs/architecture.md` to include `src/dan/loader`, `examples/paper_writing_md`, `tests/test_loader`, and current test count (`570` collected).

## 2026-02-25 (Phase 5 — decompiler, validation, parity — Phase 5 complete)
- [feat] Added `src/dan/loader/decompiler.py`: graph→markdown round-trip decompiler. Generates one `.md` per node + `workflow.md`. Supports all node types (LLM, Tool, Code, Human, Router, Composite), control-flow decompilation (`ForEach` → `| each()`, `GateNode(while)` → `| loop()`, `GateNode(if_else)` → `| if()`), chain detection, port→blockquote generation, system prompt sections, retry policy in frontmatter, slugified file naming with collision handling, best-effort output for unsupported features.
- [feat] Added export API endpoints: `GET /api/graphs/{id}/export/markdown` (JSON preview with all generated files + diagnostics) and `GET /api/graphs/{id}/export/python` (Python builder code).
- [feat] Composite agent compilation validated end-to-end: `type: composite` → `CompositeNode` with recursive `body_graph` sub-graph from internal `## Agents` + `## Flow` sections.
- [feat] Linked JSON Schema validated: `> Returns: outline (schema: schemas/outline.json)` loads external schema into `OutputPort.schema`. Missing/malformed schema files produce compiler diagnostics.
- [docs] Builder ↔ Markdown parity checklist in `docs/plans/8-3-validation-parity-advanced.md` — full feature matrix covering all node types, control flow, edges, graph-level features, and meta fields. Documented 4 gaps: `reduce`, `import_workflow`, `control_edge`, `artifact_ref`.
- [docs] Coexistence policy defined: peers-not-layers, no mixed workflows, migration paths via decompilers, usage guidance (markdown for authoring, Python for CI, visual editor for exploration).
- [test] Added `tests/test_loader/test_decompiler.py` (22 tests): simple/complex workflow decompilation, forward+backward round-trip conformance, file naming.
- [test] Added `tests/test_loader/test_advanced.py` (9 tests): composite agent parsing+compilation (5), linked JSON Schema loading+validation (4).
- [test] Added `tests/test_loader/test_paper_writing_parity.py` (17 passed, 2 skipped): structural comparison between `paper_writing_md/` markdown workflow and `paper_writing.py` Python builder workflow. Known gaps documented (GateNode vs WhileLoopNode, foreach granularity).
- [test] Full loader suite: `115 passed, 2 skipped`. Total project test count: `620 collected`.
- [docs] Phase 5 marked complete in `docs/todo.md`, `docs/plans/8-markdown-agent-format.md` (completed), `docs/plans/8-3-validation-parity-advanced.md` (completed), `docs/plans/8-4-round-trip-decompiler.md` (completed). Architecture updated with `compiler.py`, `decompiler.py`, `diagnostics.py`.

## 2026-02-25 (Phase 5 — decompiler review fixes)
- [fix] Decompiler now recursively writes inner agent files for `CompositeNode` sub-graphs and `ForEachNode` body sub-graphs. Previously only top-level node files were written, causing composite markdown to reference missing files and fail to recompile.
- [fix] Decompiler synthesizes a composite agent file for `ForEach` nodes with multi-node body sub-graphs (`>1` non-input nodes). The `| each()` line references the synthetic composite instead of only the first body node, preventing topology loss.
- [fix] Decompiler port emission now preserves schema and `required` values that differ from compiler defaults. Previously, default-named input/output ports were always suppressed, losing `required=True` and non-string schemas on round-trip.
- [fix] Compiler `_create_input_node` now infers output port schemas from actual target port schemas (e.g. `object`, `array`) instead of always using `string`. `InputVariable.type` remains constrained to `string|number|boolean` per the Pydantic model; the output port schema is now inferred separately.
- [fix] Decompiler escapes double-quote characters in gate conditions when emitting `| loop()` and `| if()` flow lines, preventing parse failures on recompile.
- [fix] Parser `parse_workflow_file` now collects `FlowParseError` exceptions as `parse_warnings` on `WorkflowSpec` instead of silently skipping malformed flow lines. Compiler converts these to `Diagnostic(level="warning")` with source location.
- [fix] Removed duplicate `compiler.py`/`diagnostics.py` entries in `docs/architecture.md` loader section.

## 2026-02-25 (Roadmap reprioritization — conversational workflow authoring)
- [docs] Updated `docs/todo.md` to move "Chatbox for NL flow creation" from Phase 9 to the immediate next phase (Phase 7) and expanded it with concrete sub-features: `@` mentions for nodes/workflows, chat history persistence, chat↔canvas co-navigation, graph-diff confirmation UX, and shared execution context.

## 2026-02-25 (Phase 7 — conversational workflow authoring planning)
- [docs] Created top-level plan `10-chatbox-nl-workflow.md` and 5 sub-plans for conversational workflow authoring (Phase 7 lead feature). 10-1: Chat Panel & Backend API (chat UI component, FastAPI message endpoint, LLM integration, graph-aware system prompt, streaming responses; 8 task groups, ~25 sub-tasks). 10-2: `@` Mention & Co-Navigation (trigger detection, autocomplete dropdown for nodes/workflows/sub-graphs, mention chips, click→canvas selection, canvas→chat suggestion, mention resolution for backend; 9 task groups, ~30 sub-tasks). 10-3: NL→Graph Mutation Engine (GraphOperation discriminated union, GraphMutator apply engine, LLM function-calling schema, multi-step planning, validation + error recovery, dry-run mode; 8 task groups, ~35 sub-tasks). 10-4: Graph Diff & Confirmation UX (before/after diff computation, visual diff preview dialog, accept/reject/partial-accept, undo stack integration, post-apply animations, conversation-level rollback; 8 task groups, ~30 sub-tasks). 10-5: Chat History & Execution Integration (ChatMessage/ChatThread models, filesystem persistence, thread list UI, auto-restore on reload/tab-switch, graph delta tracking, run-from-chat commands, execution streaming in thread, error diagnosis; 10 task groups, ~40 sub-tasks). Build order: 10-1→10-2→10-3→10-4→10-5 (mostly linear). Updated `docs/todo.md` with plan links.

## 2026-02-25 (Phase 6 — Plan 9-3: Handoff Validator Node — backend complete)
- [feat] Added `src/dan/executors/validator.py`: `ValidatorExecutor` with five rule types (`required_keys`, `non_empty`, `schema_conformance`, `type_check`, `custom_expression`), `resolve_dotpath()` utility for nested dict/list access, `ValidationViolation` dataclass, three `on_failure` modes (`route`/`warn`/`halt`), `strict_mode` early-stop, `VALIDATION_RESULT` event emission. Uses `jsonschema.validate()` with pure-Python fallback.
- [feat] Added `src/dan/validation/boundaries.py`: `generate_entry_validator()`, `generate_exit_validator()`, and `insert_boundary_validators()` for auto-inserting ValidatorNodes at composite node boundaries based on `external_input_schema`/`external_output_schema`.
- [test] Added `tests/test_engine/test_validator.py` (49 tests): `resolve_dotpath` unit tests, per-rule-type evaluation tests, `ValidatorExecutor` integration tests (routing, strict_mode, multi-rule), `on_failure` mode tests, event emission tests, edge cases (empty rules, non-dict data, data unwrapping), boundary auto-insert tests, builder/decompiler round-trip test, validator chaining test.
- [docs] Updated `docs/architecture.md` with `ValidatorNode` in control_flow.py, `validator.py` in executors, `boundaries.py` in validation. Updated plan `9-3-handoff-validator.md` (tasks 1-4, 6-8 checked off).

## 2026-02-25 (Phase 6 — subprocess sandbox, Plan 9-2)
- [feat] Added `src/dan/sandbox/__init__.py`: `SandboxConfig` (Pydantic BaseModel) with `mode`, `timeout_seconds`, `memory_mb`, `pass_env`, `filesystem_paths`, `max_output_bytes`; `SandboxResult` dataclass with `stdout`, `stderr`, `exit_code`, `output_files`, `duration_ms`, `memory_peak_mb`, `truncated`.
- [feat] Added `src/dan/sandbox/adapters.py`: `LanguageAdapter` protocol, `PythonAdapter` (bootstrap preamble with `_inputs.json`/`_result.json` round-trip), `ShellAdapter` (executable script, env var inputs), `ADAPTERS` registry.
- [feat] Added `src/dan/sandbox/runner.py`: `SandboxRunner` class with `async run()` → `(SandboxResult, dict | None)`. Features: unique temp dir per run, `asyncio.create_subprocess_exec`, timeout enforcement via `asyncio.wait_for`, output truncation, `fnmatch`-based env filtering (`pass_env`), memory limits via `resource.setrlimit` (best-effort), structured output from `_result.json`, temp dir cleanup.
- [feat] Upgraded `src/dan/executors/code.py`: `CodeExecutor` now routes `mode="inline"` to existing `exec()` fast-path (unchanged), `mode="subprocess"` to `SandboxRunner`. Emits `SANDBOX_STARTED`/`SANDBOX_COMPLETED` events around subprocess runs. Maps `SandboxResult` to `NodeResult` with structured output. Preserves `on_failure` handling (error/skip/halt). Invalid `sandbox_config` falls back to inline with warning.
- [feat] Upgraded `src/dan/tools/shell_command.py`: optionally routes through `SandboxRunner` + `ShellAdapter` when `DAN_SANDBOX_SHELL=true` env var is set. Resource limits from `DAN_SANDBOX_TIMEOUT`/`DAN_SANDBOX_MEMORY_MB` env vars. Default behavior (raw `create_subprocess_shell`) unchanged. `DAN_SHELL_ALLOW` allowlist enforced regardless of mode.
- [test] Added `tests/test_engine/test_sandbox.py` (50 tests): `SandboxConfig`/`SandboxResult` models, `PythonAdapter`/`ShellAdapter` preparation, env filtering (`_filter_env`), `SandboxRunner` unit tests (success, timeout, truncation, input injection, structured output, stderr, exit codes, shell execution, unsupported language, temp dir cleanup), resource limits (platform-dependent skip), `CodeExecutor` inline regression (6 tests), `CodeExecutor` subprocess (6 tests), sandbox event emission (4 tests), `shell_command` tool backward compat + sandbox mode (5 tests), backward compat (5 tests including `on_failure` skip/halt).
- [docs] Updated `docs/architecture.md` with `src/dan/sandbox/` package. Updated plan `9-2-subprocess-sandbox.md` (tasks 1-7, 10 checked off; tasks 8-9, 11 remain for editor integration, builder DSL, and remaining docs).

## 2026-02-25 (Phase 6 — Plan 9-1: RAG / Knowledge Retrieval Node)
- [feat] Added `src/dan/rag/__init__.py`: `EmbeddingProvider` protocol, `EmbeddingResult` dataclass, `OpenAIEmbeddingProvider` (wraps `AsyncOpenAI`, default `text-embedding-3-small`, batch support), `LocalEmbeddingProvider` (wraps `sentence-transformers` via `asyncio.to_thread()`), `EmbeddingRegistry` (exact override → prefix match → default fallback, mirrors `ProviderRegistry` pattern).
- [feat] Added `src/dan/rag/stores/__init__.py`: `VectorStore` protocol (7 methods: `create_collection`, `delete_collection`, `list_collections`, `add`, `query`, `delete_by_ids`, `count`), `DocumentRecord`, `QueryResult`, `VectorStoreConfig` dataclasses, `VectorStoreFactory` routing on backend field with graceful fallback.
- [feat] Added `src/dan/rag/stores/memory.py`: `MemoryVectorStore` — pure-Python in-memory store using stdlib `math` for cosine similarity. O(n) linear scan, zero external deps, full protocol compliance, upsert support, metadata filtering.
- [feat] Added `src/dan/rag/stores/faiss_store.py`: `FAISSVectorStore` — wraps `faiss.IndexFlatIP` with L2-normalized vectors (cosine sim via inner product). On-disk persistence (`save`/`load` with JSON metadata sidecar), post-retrieval metadata filtering, batch add, index rebuild for deletes. Optional dep `faiss-cpu`.
- [feat] Added `src/dan/rag/stores/chroma_store.py`: `ChromaVectorStore` — wraps `chromadb.PersistentClient` (or in-memory `Client`). Native metadata filtering via `where` clause, collection management, automatic embedding bypass when pre-computed. Optional dep `chromadb`.
- [feat] Added `src/dan/executors/rag.py`: `RAGExecutor` implementing `NodeExecutor` protocol. Flow: render query template → resolve embedding provider → embed query → query vector store → apply similarity threshold → format chunks → return. Emits `RETRIEVAL_STARTED`/`RETRIEVAL_COMPLETED` events with collection, query preview, chunk count, latency, top score. Module-level store cache keyed by backend+directory+collection.
- [feat] Added `src/dan/rag/indexer.py`: `Indexer` class — `create_index` (chunk documents via `text_chunk` tool logic, batch embed, store), `delete_index`, `list_indices`, `add_documents`, `get_index_stats`. Configurable batch size (default 100), word/character chunking modes.
- [test] Added `tests/test_engine/test_rag.py` (61 tests: 48 passed, 13 skipped for optional deps): `EmbeddingProvider` unit tests (mock provider, batch, determinism, error, protocol), `EmbeddingRegistry` tests (default, override, prefix, error, listing), `MemoryVectorStore` tests (16: CRUD, query ordering, top-k, filters, upsert, cosine sim correctness, protocol), `FAISSVectorStore` tests (7: skip if faiss-cpu missing — CRUD, metadata filtering, persistence, protocol), `ChromaVectorStore` tests (6: skip if chromadb missing — CRUD, where filter, protocol), `VectorStoreFactory` tests, `RAGExecutor` integration tests (7: end-to-end query, threshold filtering, error paths, template rendering, metadata exclusion, registry resolution), `Indexer` tests (9: chunking, empty, delete, add, stats, batching, word mode, metadata), builder/decompiler round-trip tests (2: compile/decompile/recompile).
- [fix] Updated `tests/test_validation/test_graph_validation.py` `test_builtins_registered` count from 12 to 14 (added `rag_operator`, `validator`).
- [fix] Updated `tests/test_builder/test_compiler.py` `test_all_node_types_covered` expected set to include `rag_operator` and `validator`.
- [docs] Updated `docs/architecture.md`: added `src/dan/rag/` package tree (6 files), `src/dan/executors/rag.py`, updated executor count to 13, test count to 780.
- [docs] Updated `docs/plans/9-1-rag-knowledge-retrieval.md`: checked off tasks 1-7, 9, 11 (embedding, stores, executor, indexer, builder, tests). Remaining: server endpoints (7-5/7-6), editor integration (8), migration/parity (10), some docs (12-2/12-3).
- [docs] Updated `docs/todo.md`: marked 9-1 as complete (backend); noted follow-up for server/editor.

## 2026-02-25 (Phase 6 — follow-up: editor integration, server endpoints, builder helper)
- [feat] **Editor TypeScript integration** for `rag_operator` and `validator` node types: `RagOperator`/`ValidatorNode` interfaces in `graph.ts`, added to `DanNode` union, `NODE_TYPE_CATALOG` (operator/control categories), `NODE_DESCRIPTIONS` with port info, SVG icons in `nodeIcons.tsx`, `TYPE_COLORS` in `DanNode.tsx` (purple/emerald), `createDefaultNode` factory cases in `graphAdapter.ts`, dedicated `RAGConfigSection` (collection, top_k, query_template, store backend, embedding_model, threshold) and `ValidatorConfigSection` (rule list editor, on_failure select, strict_mode toggle) in `ConfigPanel.tsx`.
- [feat] **RAG collection CRUD endpoints** on FastAPI server: `GET /api/rag/collections`, `POST /api/rag/collections` (create with documents + chunking), `GET /api/rag/collections/{name}/stats`, `POST /api/rag/collections/{name}/documents` (add docs), `DELETE /api/rag/collections/{name}`. Lazy-initialized `Indexer` backed by `EngineConfig` embedding settings and configurable vector store backend (`DAN_RAG_STORE_BACKEND`, `DAN_RAG_PERSIST_DIR` env vars).
- [feat] **`wf.validated_composite()` builder helper**: context manager wrapping `composite()` that auto-generates entry and/or exit `ValidatorNode` nodes from JSON Schema (auto-derives `required_keys` + `schema_conformance` rules). Wires entry validator `valid` port → composite `input`, composite `result` → exit validator `data` port. Stores schemas on `external_input_schema`/`external_output_schema` for runtime `insert_boundary_validators` compatibility.
- [feat] **"Add Boundary Validators" context menu action**: server endpoint `POST /api/graphs/{graph_id}/nodes/{node_id}/add-boundary-validators` (calls `insert_boundary_validators`, persists updated graph). Editor `api.ts` client function, `useGraphStore.addBoundaryValidators` action (save → call → reload cycle), `ContextMenu.tsx` entry shown for composite/while_loop/for_each nodes with external schemas.

## 2026-02-25 (Phase 7 — conversational planning review refinements)
- [docs] Refined `docs/plans/10-chatbox-nl-workflow.md` with explicit trust and execution decisions: server-authoritative graph context, optimistic concurrency guard for mutation plans, WebSocket-only chat streaming, and session-scoped rollback policy.
- [docs] Updated `docs/plans/10-1-chat-panel-backend.md` to remove SSE ambiguity, require server-loaded graph summaries (`workflow_id` source of truth), and add markdown sanitization requirements for assistant-rendered content.
- [docs] Updated `docs/plans/10-2-mention-co-navigation.md` so mention expansion is resolved server-side from authoritative graph/workflow state rather than client-expanded context.
- [docs] Updated `docs/plans/10-3-nl-graph-mutation.md` with `base_graph_revision`/`base_graph_hash` plan preconditions, stale-plan rejection, and transactional-by-default apply semantics (partial apply only explicit user opt-in).
- [docs] Updated `docs/plans/10-4-graph-diff-confirmation.md` to define conversation rollback as active-session-only with in-memory history cursors and graceful disable behavior after reload.
- [docs] Refocused `docs/plans/10-5-history-execution.md` to chat persistence/session metadata, and split run-from-chat backend/runtime complexity into a new detailed sub-plan `docs/plans/10-6-scoped-run-from-chat.md`.
- [docs] Updated `docs/todo.md` to include `10-6` under Phase 7 and resolved top-level numbering collision by renaming the remaining Phase 7 umbrella item to `10-R`.

## 2026-02-25 (Phase 7 — Plan 10-2: `@` Mention & Co-Navigation)
- [feat] Added `editor/src/lib/mentionParser.ts`: mention serialization/parsing utilities — `serializeMention`, `parseMentions` (regex-based segment splitter), `findMentionQuery` (detect `@` trigger at cursor), `insertMention` (replace `@query` with `@[name](type:id)`), `navigateToMention` (co-navigation dispatcher: node→`setSelectedNode`, subgraph→`drillIn`, workflow→no-op), `mentionTypeColor` (Tailwind classes by mention type).
- [feat] Added `editor/src/components/MentionAutocomplete.tsx`: floating autocomplete dropdown for `@` mentions. Three sections (Nodes, Workflows, Sub-graphs) populated from Zustand store. Case-insensitive substring filtering with bold match highlight. Keyboard navigation (ArrowUp/Down, Enter, Escape). Node type icons from `nodeIcons.tsx`, type badge pills, folder/layers icons for workflows/sub-graphs. Fixed positioning with viewport flip-up logic, max 300px height scroll.
- [feat] Updated `editor/src/components/ChatPanel.tsx`: integrated mention system — `checkMention` detects `@` trigger on input/keyup/click, `handleMentionSelect` inserts serialized mention and repositions cursor, `dismissMention` hides dropdown. Textarea `onKeyDown` suppresses Enter/Arrow when autocomplete is active. `MentionAutocomplete` rendered inside input container.
- [feat] Updated `editor/src/components/ChatMessage.tsx`: mention chip rendering in message bubbles — pre-processes `@[name](type:id)` tokens before HTML escaping (sentinel-swap technique), renders as inline `<button>` pills with colored backgrounds (blue/green/amber by type). Click delegation via `data-mention-*` attributes dispatches to `navigateToMention` for canvas co-navigation. Both user and assistant messages support mention chips.

## 2026-02-25 (Phase 7 — Plan 10-3: LLM Function-Calling for Graph Mutations)
- [feat] Extended `CompletionResult` with `tool_calls: list[dict] | None` field (`providers/__init__.py`). OpenAI provider (`openai_provider.py`) captures `tool_calls` from response messages (id, type, function name/arguments).
- [feat] Added `MUTATION_TOOL_SCHEMA` constant in `chat_manager.py`: OpenAI function-calling tool definition for `plan_graph_mutations` covering all 6 operation types (add_node, remove_node, edit_node, add_edge, remove_edge, set_position).
- [feat] Added `ChatMutationEvent` stream event: `message_id`, `content` (reasoning text), `mutation_plan`, `dry_run_result`, `token_usage`, `graph_revision`, `revision_mismatch`. Updated `ChatStreamEvent` union to include it.
- [feat] Updated system prompt: instructs LLM to use `plan_graph_mutations` tool for modification requests, plain text for questions/explanations.
- [feat] Added `ChatManager.send_message_with_tools()`: non-streaming `complete()` call with `tools=[MUTATION_TOOL_SCHEMA]`, extracts mutation plans from native tool_calls or text JSON fallback, runs `GraphMutator.dry_run()`, yields `ChatMutationEvent` and returns. Graceful fallback via `_stream_with_json_fallback()` when provider doesn't support tools kwarg.
- [feat] Added `_try_parse_mutation_json()` module-level helper: regex-based extraction of mutation plans from markdown JSON code blocks or raw JSON text.
- [feat] Original `send_message()` preserved as text-only streaming path (no function calling).
- [feat] Updated `app.py` `/api/chat/message` endpoint: routes to `send_message_with_tools` when graph exists, falls back to `send_message` for missing graphs.
- [fix] Relaxed `use_tools` guard in `/api/chat/message`: previously required `len(nodes) > 0`, now triggers for any existing graph (even empty). Consistent with system prompt that instructs the LLM to build from scratch for empty workflows.
- [feat] Updated `editor/src/types/chat.ts`: added `chat_mutation` to `ChatStreamEvent.type` union, added `mutation_plan` and `dry_run_result` optional fields.
- [feat] Updated `editor/src/components/ChatPanel.tsx`: handles `chat_mutation` WebSocket events — stores combined `{ plan, dryRunResult }` on assistant message, sets `mutationStatus: "proposed"`. Added `GraphDiffPreview` integration: clicking "Proposed changes" badge computes diff between current graph and `dryRunResult.new_graph`, shows diff dialog. "Apply All" saves new graph via `updateGraph` + reloads store. "Reject" marks message status as `rejected`.
- [feat] Updated `editor/src/components/ChatMessage.tsx`: "Proposed changes" badge changed from `<span>` to clickable `<button>` with `onViewMutation` callback. Badge text reflects status (Applied/Rejected/Proposed changes).

## 2026-02-25 (Phase 7 — Plan 10-5: Chat Thread Management & Session Rollback)
- [feat] **Thread list sidebar** in `ChatPanel.tsx`: "Chat History" view with thread rows sorted by `updated_at` desc, message count badge, relative timestamp, delete button (confirm prompt), empty state ("No conversations yet"), and "New Chat" button. Active chat header has back arrow, inline-editable title, token count.
- [feat] **Thread persistence via API**: auto-create thread on first message (with auto-title from content), save thread messages via PUT after each assistant response completion, save current thread on back-to-list and workflow switch. Uses `listChatThreads`, `getChatThread`, `createChatThread`, `updateChatThread`, `deleteChatThread` API functions.
- [feat] **Auto-restore on load**: when chat panel opens with a workflow, fetches threads and auto-loads the most recent one. On workflow tab switch, saves current thread for old workflow and loads threads for new workflow.
- [feat] **Session-scoped rollback markers**: `sessionMarkers` state tracks `{ historyCursor }` per message ID. `_recordMutationMarker(messageId)` records current undo stack position. "Revert to here" button walks undo stack back to marker position. After reload (markers empty), shows disabled "Revert" with tooltip "Available in current session only". Markers cleared on thread/workflow switch.
- [feat] **ChatMessage mutation badges**: full status-aware rendering — proposed (amber chip + chevron), applied (green chip + check), rejected (gray chip), reverted (gray chip + strikethrough). Replaces previous simple `mutationPlan` presence check.
- [feat] **ChatMessage run reference blocks**: `runRef` rendered as inline status blocks — running (blue spinner + scope), completed (green check + "View logs"), failed (red X + "View logs").
- [feat] Added `updateChatThread` API function in `api.ts` (PUT `/chats/{workflowId}/{threadId}` with title and/or messages).
- [feat] Extended backend PUT `/api/chats/{workflow_id}/{thread_id}` to accept `messages` array for full thread saves (previously title-only). Uses `StoreChatMessage.model_validate` for each message, updates `updated_at`, persists via `ChatStore.save_thread`.
- [feat] Backend/frontend message format conversion helpers (`toBackendMessage`/`fromBackendMessage`) handle camelCase↔snake_case mapping and timestamp number↔ISO string conversion.
- [fix] `ChatMessage.tsx` mutation badge now triggers on `mutationPlan` presence (not just `mutationStatus`), defaulting to "proposed" when plan exists without explicit status.

## 2026-02-25
- [feat] 10-1: Chat panel & backend — ChatPanel.tsx, ChatMessage.tsx, ChatManager, graph-aware system prompt, WebSocket streaming, chat endpoints in app.py
- [feat] 10-2: @ mention system — MentionAutocomplete.tsx, mentionParser.ts, Cursor-style autocomplete, mention chips, click→canvas co-navigation
- [feat] 10-3: NL→Graph mutation engine — GraphMutator with 8 operation types, MutationPlan, LLM function-calling schema, dry-run mode, optimistic concurrency
- [feat] 10-4: Graph diff preview — graphDiff.ts computation, GraphDiffPreview.tsx modal with accept/reject/partial-accept, per-operation checkboxes
- [feat] 10-5: Chat history & session — ChatStore persistence, thread list UI, auto-restore, session-scoped rollback markers, mutation/run badges in messages
- [feat] 10-6: Scoped run execution — scoped_run.py, POST /api/runs/scoped, build_scoped_graph, /run commands, run event→chat block mapping
- [test] Added test suites: test_chat_manager.py, test_graph_mutator.py, test_chat_store.py

## 2026-02-25 (Phase 6 — review fixes: five critical bugs)
- [fix] **`validated_composite` rewiring**: The builder helper now returns a `_ValidatedCompositeRef` proxy. When used with `>>`, inbound edges route to the entry validator and outbound edges originate from the exit validator, ensuring data always flows *through* validators instead of bypassing them.
- [fix] **RAG CRUD `_get_indexer()` uses `EmbeddingRegistry`**: Extracted shared `build_embedding_registry(config)` function in `dan/rag/__init__.py`. Both the engine's `_build_embedding_registry` and the server's `_get_indexer` now use it, honouring local and API embedding provider configurations (Option B parity).
- [fix] **Boundary validator insertion is idempotent**: `insert_boundary_validators()` now checks if `{node_id}__entry_validator` or `{node_id}__exit_validator` already exist and returns the graph unchanged, preventing duplicate node IDs and broken graphs on repeated calls.
- [fix] **Editor `addBoundaryValidators` guards on save**: The Zustand action now checks `saveGraph()` return value and aborts if the save failed, preventing API calls against stale server state.
- [fix] **Validator rule JSON editor UX**: Replaced direct `JSON.parse` on every keystroke with a `RuleConfigEditor` component using local `useState` for the textarea. Parsing and validation happen on blur, with a red border + error message for invalid JSON. Intermediate edits are preserved.
- [refactor] Consolidated `_create_embedding_provider` + `_build_embedding_registry` from `engine/scheduler.py` into `dan/rag/__init__.py` as `_create_embedding_provider` + `build_embedding_registry`. Engine delegates to the shared helper. Test monkeypatches updated.
- [test] 866 passed, 15 skipped.

## 2026-02-25 (Phase 6 — review follow-ups: custom ports, tests)
- [fix] **`validated_composite` custom ports**: Internal validator wiring now derives the composite's first input/output port from `input_ports`/`output_ports` or `input_mappings`/`output_mappings` instead of hardcoding `"input"`/`"result"`. Composites with custom ports (e.g. `payload`, `answer`) are correctly wired.
- [fix] **`_ValidatedCompositeRef` delegation**: Added `__getattr__` so builder methods (llm, code, etc.) inside the block delegate to the sub-workflow. Users can write `with wf.validated_composite(...) as block: block.llm(...)`.
- [test] **`test_validated_composite_flows_through_validators`**: Asserts `a >> block >> b` produces edges through entry/exit validators.
- [test] **`test_validated_composite_with_custom_ports`**: Asserts custom `input_ports`/`output_ports` are used for validator wiring.
- [test] **`test_insert_boundary_validators_idempotent`**: Asserts repeated calls return the same graph; no duplicate validators.
- [test] 870 passed, 15 skipped.

## 2026-02-25 (Phase 7 — review bug-fixes)
- [fix] **Chat mutation events dropped by UI**: `ChatPanel.tsx` `ws.onmessage` now handles `chat_mutation` stream events — updates the assistant message with `mutationPlan`, `mutationStatus: "proposed"`, and `mutationId`, then saves the thread and stops streaming.
- [fix] **`/run` chat commands not wired**: `app.py::chat_message` now calls `parse_run_command()` on each incoming message; `/run`, `/run-node`, `/run-subgraph` commands dispatch through `build_scoped_graph` + `RunManager.start_run`, returning `run_started`/`run_error` responses that the frontend handles inline.
- [fix] **Token usage key mismatch**: Added `_normalize_usage()` in `chat_manager.py` — maps provider keys (`prompt_tokens`/`completion_tokens`) to frontend keys (`prompt`/`completion`). Applied to all three emission paths (stream, tool-call, JSON-fallback).
- [fix] **`_chat_streams` memory leak**: Changed `_chat_streams` from `dict[str, Queue]` to `dict[str, (Queue, float)]` with monotonic timestamps. `_reap_stale_chat_streams()` evicts entries older than 120s on each POST, guarding against leaked queues when clients never connect.
- [fix] **Markdown link XSS via unsafe URL schemes**: `ChatMessage.tsx` `applyInlineMarkdown` now rejects `javascript:` hrefs and only allows `https:`, `http:`, `mailto:`, and `#` schemes; added `rel="noreferrer"`.
- [fix] **Mention trigger fires mid-word**: `mentionParser.ts::findMentionQuery` now requires `@` to be preceded by whitespace or at position 0, preventing false triggers like `email@foo`.
- [fix] **Workflow mention click was a no-op**: `navigateToMention` now calls `store.openTab(mention.id)` for `workflow` mention type.

## 2026-02-25 (Phase 7 — 10-7 Apply Mutation Flow)
- [feat] **Sub-plan 10-7**: Wire chat mutation proposal → diff preview → apply pipeline. Users can now apply LLM-generated graph changes.
- [feat] **Backend**: `POST /api/graphs/{graph_id}/apply-mutation` — accepts `MutationPlan`, calls `GraphMutator.apply()`, persists via GraphStore, returns `{ success, new_graph?, errors?, stale_plan? }`. Concurrency check via `base_graph_revision`.
- [feat] **Frontend**: `api.applyMutation()` client; "Proposed changes" badge in ChatMessage is clickable → opens GraphDiffPreview modal with `computeGraphDiff(currentGraph, dry_run_result.new_graph)`.
- [feat] **Apply flow**: Apply All → `applyMutation` API → `pushSnapshot` + `loadGraph` → update message `mutationStatus: "applied"` → record session marker for "Revert to here" → persist thread.
- [feat] **Reject flow**: Close modal, set `mutationStatus: "rejected"`, persist thread.
- [feat] **ChatMessage**: Added `dryRunResult` to message model and backend conversion for diff computation.
- [test] `test_apply_mutation_success`, `test_apply_mutation_nonexistent` in test_api.py.

## 2026-02-25 (Phase 7 — 10-7 review fixes)
- [fix] **Apply Selected misleading**: GraphDiffPreview now accepts `allowPartialApply` (default true). ChatPanel passes `allowPartialApply={false}` so "Apply Selected" is hidden until partial apply is implemented.
- [fix] **Unrelated gate migration default**: Reverted `DAN_GATE_MIGRATION_ENABLED` default from `"true"` to `""` (was accidentally changed).
- [fix] **Loading state during apply**: Added `isApplying` state; GraphDiffPreview accepts `disabled` prop. Apply/Reject buttons disabled while API call in flight.
- [fix] **Modal closes on error**: On apply failure, modal stays open so user can retry or close manually. `setPreviewingMessage(null)` only on success.

## 2026-02-25 (Phase 7 — 10-7 second review fixes)
- [fix] **Apply error hidden behind modal**: Added `applyError` state and `applyError` prop to GraphDiffPreview. Failed apply shows error banner inside modal with "Try again" button.
- [fix] **Close button during apply**: GraphDiffPreview Close (X) button now disabled when `disabled` is true.
- [fix] **Stale isApplying guard**: Use `applyingRef` for the guard to avoid stale closure; `isApplying` state remains for UI disable.

## 2026-02-25 (Phase 7 — 10-7 third review fixes)
- [fix] **dry_run_result not persisted**: Added `dry_run_result` to `ChatMessage` in `chat_store.py` so mutation preview survives thread reload.
- [fix] **No fallback when preview unavailable**: When user clicks "Proposed changes" but `dryRunResult.new_graph` is missing (old thread or failed persistence), show fallback modal with "Preview unavailable" and Close button instead of leaving user stuck.
- [fix] **apply-mutation gate migration parity**: Applied gate migration to graph before mutation in `apply_mutation` endpoint when `DAN_GATE_MIGRATION_ENABLED` is set, matching `get_graph` behavior.

## 2026-02-25 (Phase 7 — 10-7 fourth review fixes)
- [fix] **Run error message wrong field**: Backend `ScopedRunError` uses `message`, not `detail`. Frontend now reads `resBody.error?.message` for run_error display.
- [fix] **Run commands not persisted**: `/run` and `/run-node` responses (run_started/run_error) now save the assistant message to the thread via `updateChatThread`.
- [fix] **TypeScript cast**: `danGraph as Record<string, unknown>` → `danGraph as unknown as Record<string, unknown>` to satisfy strict cast.

## 2026-02-25 (Phase 4 — Core Hardening — implementation complete)
- [feat] **7-1 Runtime Reliability:** Added `RetryPolicy` Pydantic model (`max_retries`, `backoff`, `backoff_max`, `fallback_model`, `on_failure`) on `NodeBase`. Replaced hardcoded retry in `LLMExecutor` with configurable policy. Added retry loop to `ToolExecutor` (transient exceptions: `TimeoutError`, `ConnectionError`, `OSError`). `CodeExecutor` honors `on_failure` (skip/halt) without retry. Scheduler checks `metadata.halt` flag after each level, saves checkpoint and stops. `RETRY_ATTEMPTED` event type added. `EngineConfig.max_concurrency` for graph-wide semaphore. Frontend: `RetryPolicyEditor` in ConfigPanel, `RetryPolicy` TypeScript interface. 27 new tests.
- [feat] **7-2 Multi-Provider LLM Registry:** Created `src/dan/providers/` package with `LLMProvider` protocol, `CompletionResult`, `StreamChunk`, `ProviderConfig` dataclasses. Built-in providers: `OpenAIProvider` (wraps AsyncOpenAI), `AnthropicProvider` (wraps AsyncAnthropic, system prompt extraction), `GoogleProvider` (wraps google.generativeai). `ProviderRegistry` with 3-tier resolution (exact override → prefix pattern → default fallback). `EngineConfig` extended with `providers` dict and `model_provider_map`. `LLMExecutor` and `RouterExecutor` refactored to use provider dispatch. Static cost table (`costs.py`) covering 17 models. Server auto-scans `DAN_OPENAI_API_KEY`, `DAN_ANTHROPIC_API_KEY`, `DAN_GOOGLE_API_KEY`. Frontend: `LLMConfigSection` with basic/advanced pattern, model datalist, provider badges. 128 new tests (38 new provider tests).
- [feat] **7-3 Built-in Tool Library:** Created `src/dan/tools/` package with 11 tools: `file_read`, `file_write`, `list_directory` (file category, workspace-root sandboxed), `web_search` (DuckDuckGo, optional dep), `web_fetch`, `http_request` (web category), `shell_command` (system, allowlist-enforced), `pdf_read` (document, optional dep), `text_chunk` (document), `json_extract`, `regex_match` (utility). Auto-discovery via `get_all_tools()` with graceful `ImportError` handling. `ToolRegistry.register_builtin_tools()` method. `httpx` promoted to main dependency. Optional deps: `pypdf`, `duckduckgo-search`. 60 new tests.
- [feat] **7-4 Templates + Observability:** 5 workflow templates: `simple_chain` (LLM→LLM→Code), `fan_out_fan_in` (ForEach+Reduce), `review_revise` (GateNode while-loop), `rag_qa` (tool-based RAG), `react_agent` (ReAct loop with tools). Frontend: per-node token badges ("1.2k tok"), cost badges ("$0.03") on `DanNode`, total cost in `RunSummaryBar`, per-node usage in LogPanel headers. `nodeUsage` and `nodeCosts` state in Zustand store. 37 new tests.
- [infra] Test suite: 341 → 503 tests (162 new), all passing. `pyproject.toml`: added optional dependency groups (`pdf`, `search`, `anthropic`, `google`, `all-providers`, `all-tools`, `all`).

## 2026-02-25 (Phase 4 plans — review fixes)
- [docs] **7-2 provider routing redesign:** replaced prefix-only routing with 3-tier resolution (exact model→provider map → prefix match → `default` fallback). Added `ProviderConfig` dataclass, named provider registration (`EngineConfig.providers`), and `model_provider_map` override. Current vectorengine `claude-sonnet-4-6` setup preserved via `default` provider. Fixes backward-compat break where `claude-*` prefix would route to Anthropic native SDK.
- [docs] **7-2 RouterExecutor:** added task 5-6 to refactor `RouterExecutor` (currently creates its own `AsyncOpenAI` client directly, bypassing provider abstraction). Added test 8-6 for Router dispatch and test 8-10 for model override map.
- [docs] **7-2 basic + advanced UI:** redesigned config panel from model-only dropdown to basic (model, temperature, system prompt) + collapsible advanced (base_url override, api_key override, max_tokens, extensible extra kwargs). Keeps common case clean, power users can customize per-node.
- [docs] **7-1 on_failure=halt semantics (decided):** halt stops scheduling new topological levels, lets already-running parallel nodes finish, writes checkpoint at halt point, returns `RunResult(success=False)`. Added tasks 2-4, 3-5, scheduler halt-flag check, and 3 new integration tests (7-4, 7-8, 7-9).
- [docs] **7-1 retry_policy naming:** renamed `backoff_base` → `backoff` to match architecture.md contract. `backoff_max` is additive (caps exponential growth). Updated architecture.md.
- [docs] **7-1 retry_attempted event prerequisite:** added task 1-5 to register `RETRY_ATTEMPTED` in `EventType` enum before executors can emit it (emit_event validates via enum).
- [docs] **7-1 CodeExecutor retry scoped:** replaced timeout-retry plan with explicit "no retry loop for code" decision — `exec()` is synchronous/deterministic with no preemption. `on_failure` (skip/halt) still honored. Retry becomes meaningful when Phase 6 adds subprocess sandboxing.
- [docs] **7-3 workspace-root sandboxing:** added shared `_workspace_root()` utility (task 1-5) used by all file tools (`file_read`, `file_write`, `list_directory`). Rejects `../` escapes, symlink escapes, absolute paths outside root. Configurable via `DAN_WORKSPACE_ROOT`.
- [docs] **7-3 graceful SDK import:** `get_all_tools()` catches `ImportError` per tool module (task 1-4 updated). Missing optional SDKs log warning and skip tool, not crash server.
- [docs] Updated `7-core-hardening.md` shared decisions with all resolved items (halt semantics, routing safety, code retry, basic+advanced UI, workspace sandboxing).
- [docs] Updated `architecture.md` — expanded retry_policy description with `backoff_max` field and halt semantics.

## 2026-02-25 (Phase 4 core hardening — detailed planning)
- [docs] Created `docs/plans/7-core-hardening.md` — parent plan for Phase 4 with 4 sub-plans, dependency graph, shared decisions, and execution order recommendation
- [docs] Created `docs/plans/7-1-runtime-reliability.md` — `RetryPolicy` model on `NodeBase`, wire retry into LLM/Tool/Code executors, fallback model, `on_failure` modes, `max_concurrency` audit, Config panel UI (8 task groups, 27 sub-tasks)
- [docs] Created `docs/plans/7-2-multi-provider-llm.md` — `LLMProvider` protocol, OpenAI/Anthropic/Google built-in providers, `ProviderRegistry` with prefix routing, per-provider key management, `LLMExecutor` refactor, static cost table, model picker UI (10 task groups, 30 sub-tasks)
- [docs] Created `docs/plans/7-3-built-in-tools.md` — `dan.tools` package with ~10 tools (file read/write, list_directory, web_search, web_fetch, http_request, shell_command, pdf_read, text_chunk, json_extract, regex_match), `TOOL_METADATA` schema, auto-registration, ACI quality inline (11 task groups, 32 sub-tasks)
- [docs] Created `docs/plans/7-4-templates-observability.md` — 5 workflow templates (simple chain, fan-out/fan-in, review-revise, RAG Q&A, ReAct agent), per-node token/cost badges, `RunSummaryBar` cost extension, LogPanel enhancements (7 task groups, 22 sub-tasks)
- [docs] Updated `docs/todo.md` — replaced flat Phase 4 bullet list with linked sub-plan hierarchy (7 parent + 7-1 through 7-4)
- [docs] Noted `ForEachNode.parallelism` + semaphore already implements per-node concurrency control — `max_concurrency` todo item is largely done, 7-1 audits whether a graph-wide ceiling is needed

## 2026-02-25 (roadmap tightening — dependency fixes)
- [docs] Phase 4 `dan.tools`: clarified HTTP request is a built-in tool (no separate `HTTPOperator` node type needed), expanded PDF/paper ingestion description for tool-based local RAG
- [docs] Phase 4 templates: clarified RAG Q&A template uses tool-based RAG via `dan.tools` (local PDF reading), no dependency on Phase 6 `RAGOperator`
- [docs] Phase 6: removed `HTTPOperator` (covered by `dan.tools.http_request` in Phase 4), clarified `RAGOperator` is upgrade from tool-based approach
- [docs] Updated `development-plan.md` roadmap table to match Phase 6 scope change

## 2026-02-25 (roadmap reorganization)
- [docs] Refactored `docs/todo.md` — replaced old Phases 4-6 (Memory, Markdown, Marketplace) with new Phases 4-10 based on "furnish, don't renovate" principle: (4) Core Hardening, (5) Markdown Agent Format, (6) Extended Capabilities, (7) Author & Distribute, (8) Observe & Recover, (9) Application Layer, (10) Deep Systems
- [docs] Redistributed all backlog items into appropriate phases; backlog now contains only aspirational/exploratory items (coding assistant PoC, science-cursor rebuild, EvoAgentX survey, cross-graph copy)
- [docs] Marked Phase 3.75 parent item as completed (all 11 sub-tasks were already `[x]`)
- [docs] Memory & context scoping moved from Phase 4 to Phase 10 — build when real workflows demand it, not speculatively
- [docs] Markdown agent format moved up from old Phase 5 to new Phase 5 (immediately after core hardening)
- [docs] Added new phases: (6) RAG/HTTP/sandbox, (7) CLI/publish-as-API/MCP/PyPI, (8) run history/audit/checkpoints, (9) agent teams/messaging/user system
- [docs] Updated `docs/development-plan.md` roadmap table — marked Phases 3.5 and 3.75 as Done with test counts, added Phases 4-10 with new descriptions
- [docs] Updated `docs/development-plan.md` recommendation section — struck through completed Phase 3 milestone, added forward-looking summary of Phases 4-10

## 2026-02-24 (6-9 multi-tab workflow sessions implementation)
- [feat] `TabInfo` and `TabSnapshot` types in `useGraphStore.ts` — per-tab state model covering all workflow-scoped slices (graph, nodes, edges, selection, layers, validation, run state, logs, history, iterations, streaming, human input)
- [feat] `_snapshotActiveTab()` / `_restoreTab()` internal helpers — deep-clone all per-tab state into/from a `TabSnapshot` using `structuredClone` and `Set` copies
- [feat] `_persistTabState()` — writes `{ tabs, activeTabId, runs }` to `sessionStorage` under `dan_open_tabs` key (lightweight metadata only, no nodes/edges/logs)
- [feat] `openTab(graphId)` — deduplicates by `graphId` (switches to existing tab), snapshots active tab to cache, creates new tab with fresh state, loads graph via API
- [feat] `switchTab(tabId)` — snapshots active tab, closes active WebSocket, restores target from cache (or loads from API), reconnects WS if target has an active run (`running`/`pending`)
- [feat] `closeTab(tabId)` — blocks closing last tab with toast warning, switches to neighbor before removing active tab, cleans `tabCache`
- [feat] Cross-tab event guard in `handleRunEvent` — ignores WS events whose `run_id` doesn't match active tab's `runId`, preventing log/status contamination during fast tab switches
- [feat] `restoreTabs()` startup action — reads `dan_open_tabs` from `sessionStorage`, rebuilds tab list, loads active tab's graph, reconnects active run via API + WS catch-up
- [feat] Legacy migration in `restoreTabs()` — detects old `dan_active_run` key, converts to tab-aware schema, clears legacy key
- [feat] `startRun` / `resumeRun` / `handleRunEvent` (terminal events) call `_persistTabState()` for session persistence
- [feat] `loadGraphList` creates initial tab from `last_opened` when no tabs exist
- [feat] `createGraph` routes through `openTab` after creation; `deleteGraph` closes matching tab
- [feat] Created `editor/src/components/TabBar.tsx` — horizontal tab bar with graph name (truncated), run status badge (colored dot), close button (×), active tab indigo border styling, "+" button with dropdown picker of unopened graphs
- [feat] Updated `EditorToolbar.tsx` — replaced graph `<select>` dropdown with `<TabBar />` component, kept create/delete controls, import routes through `openTab`
- [feat] Updated `App.tsx` — startup calls `restoreTabs()` after `loadGraphList()` instead of `recoverActiveRun()`
- [infra] TypeScript compiles cleanly (`npx tsc --noEmit` — 0 errors)

## 2026-02-24 (6-10 cycle-aware scheduling + gate validation — phase 2)
- [feat] `PortDataStore.clear_node(node_id)` — removes all port data for a node, replacing raw `_data` dict manipulation in cycle iteration
- [fix] `_should_skip()` back-edge exemption — while-gate continue/loop ports no longer cause downstream cycle nodes to be skipped during initial pass or iterations; only forward gate branches (true/false/done) trigger skip logic
- [fix] Virtual input priority in `_execute_node()` — virtual inputs (from `_inject_inputs` and `_iterate_cycle`) now override stale data-edge values via direct assignment instead of `setdefault`, fixing cycle nodes receiving outdated non-cycle predecessor data
- [fix] `_iterate_cycle()` refactored to use `clear_node()` instead of raw `_data` access
- [feat] Multi-gate cycle validation in `_validate_gate_cycles()` — computes per-gate cycle regions via bidirectional reachability; rejects overlapping regions from two while-gates in the same cycle
- [test] `tests/test_engine/test_cycle_scheduling.py` — 14 tests: PortDataStore.clear_node (3), DAG fast-path (2), while-gate loop execution (2), max_iterations enforcement (1), if_else branch skip (2), gateless cycle rejection (1), gated cycle acceptance (1), if_else-mode cycle rejection (1), multi-gate cycle rejection (1)
- [fix] Updated `test_gate_scheduling.py::test_loop_executes_multiple_iterations` stop_at from 3→5 to match corrected cycle behavior where inc node now properly executes

## 2026-02-24 (6-10 cycle-aware scheduling + gate validation)
- [feat] `_topological_levels_with_backedges(graph)` — extended topo sort that identifies gate-controlled back-edges and computes cycle regions, enabling flat visible-loop scheduling without sub-graph containers
- [feat] Cycle-aware execution in `Engine._execute_with_cycles()` and `_iterate_cycle()` — re-executes cycle region nodes in bounded iterations when a gate(while) node outputs on its continue port; respects `max_iterations` guard
- [feat] Gate branch-port skip logic in `_should_skip()` — nodes downstream of an inactive gate branch are skipped (complements existing ControlEdge-based branching for legacy if_else)
- [feat] `_validate_gate_cycles()` in `validation/graph.py` — validates that gate-controlled cycles use while mode with valid iteration bounds; rejects if_else-mode gates in cycles
- [feat] `_check_data_cycles()` now exempts gate nodes alongside while_loop/for_each so gate-controlled back-edges don't trigger false cycle errors
- [feat] DAG fast-path preserved — when no back-edges exist, `_execute()` runs the original level-by-level scheduling with zero overhead
- [test] `tests/test_engine/test_gate_scheduling.py` — 15 tests covering gate if_else branching, while-loop iteration, max_iterations enforcement, iteration events, DAG fast-path, topo sort back-edge detection, cycle validation, and helper functions

## 2026-02-24 (import_workflow + equity research example)
- [feat] `wf.import_workflow(node_id, graph)` — Python builder method to embed a pre-built Graph as a composite node, enabling progressive workflow wrapping (build A, import into B, import B into C)
- [feat] `namespace_graph(graph, prefix)` — prefixes all internal IDs to avoid collisions when importing
- [feat] `derive_ports(graph)` — auto-derives composite input/output ports from entry/exit nodes, using `node_id::port_name` mapping format (matches editor's `graphAsCompositeNode()`)
- [feat] New file `src/dan/builder/importer.py` with import utilities
- [feat] `examples/equity_research.py` — 5-level progressive wrapping demo (Data Gatherer → Section Analyst → Report Orchestrator + Scenario Analysis + Multi-Ticker Comparison) using all 3 edge types and most node types
- [feat] Parallel imported nodes: Section Analyst imports `data_gatherer` graph twice (`dg_primary` + `dg_news`) running concurrently with no data dependency, merged before the draft-review loop — demonstrates reusing the same workflow as multiple parallel composite nodes
- [docs] Updated `docs/llm-api-guide.md` with `import_workflow` API, progressive wrapping pattern, and import map
- [docs] Updated `docs/architecture.md` with new `importer.py` file

## 2026-02-24 (6-8 patch-up implementation)
- [feat] Loop feedback arrows: `drillIn` injects synthetic dashed edges from exit-point output ports back to entry-point input ports (name-matched), with generic fallback arrow when names don't match; edges tagged `data.synthetic=true`
- [fix] Save-leak prevention: `saveGraph()` filters out `edge.data?.synthetic` edges before passing to `reactFlowToDanGraph()`, preventing phantom edges from persisting when saving while drilled into a loop body
- [feat] Smart port derivation: `derivePorts()` in `graphImporter.ts` now skips autonomous entry nodes (zero input ports), uses node-aware mapping format (`nodeId::portName`), reverses exit-node order so primary exit ports appear first
- [feat] Entry/exit validation: `graphAsCompositeNode()` validates that all namespaced entry/exit point IDs exist in the body graph before deriving ports
- [feat] Node-aware input routing: `CompositeExecutor` parses `nodeId::portName` mapping values into per-node `targeted_inputs`, forwarded through `run_subgraph` → `_run_subgraph` for precise per-entry-node injection (backward compatible with legacy flat mappings)
- [feat] Node-aware output routing: `CompositeExecutor` parses `nodeId::portName` mapping keys, extracting port names for lookup in body output
- [feat] Targeted injection in `_run_subgraph`: new optional `targeted_inputs` parameter injects values to specific entry-point nodes instead of broadcasting; `ExecutionContext.run_subgraph` signature updated to forward the parameter
- [fix] Existing mock test signatures updated for new `targeted_inputs` parameter in `test_composite_executor.py`
- [test] 9 new tests in `tests/test_engine/test_68_patchup.py`: node-aware input routing, legacy fallback, mixed mappings, duplicate port collision, node-aware output mapping, zero-input-port entries, partial input coverage, targeted injection
- [docs] Updated `docs/architecture.md` with feedback-arrow strategy, node-aware mapping format, autonomous-entry filtering, test count 265→274

## 2026-02-24 (6-8 patch-up plan)
- [docs] Created `docs/plans/6-8-patch-up.md` — targeted fixes: (1) virtual feedback arrows in loop drill-in views, (2) smart port derivation for workflow-as-node to skip autonomous entry nodes, (3) multi-entry composite run readiness, (4) synthetic edge save-leak prevention, (5) node-aware port mapping collisions
- [docs] Added 6-8 row to parent plan `6-phase-3.75-visual-editor-editing.md`, re-opened parent status to `in-progress`
- [docs] Added 6-8 entry to `docs/todo.md`

## 2026-02-24 (6-7 workflow-as-node implementation)
- [feat] Created `editor/src/lib/graphImporter.ts` — `graphAsCompositeNode()` factory with recursive ID namespacing (`wf_{graphId}__` prefix), deterministic port derivation from entry/exit points, collision-safe naming, and input/output mapping generation
- [feat] Added "Saved Workflows" category in `NodePalette.tsx` — lists all saved graphs (excluding current), drag/drop with `workflow:{graphId}` payload, click-to-insert, emerald styling, search filtering
- [feat] Extended `GraphCanvas.tsx` `onDrop` to handle `workflow:` prefix and delegate to `addGraphAsNode` store action
- [feat] Added `addGraphAsNode(graphId, position)` async action in `useGraphStore.ts` — fetches graph via API, validates payload, runs importer factory, merges sub_graphs, adds CompositeNode atomically with undo snapshot, self-import guard, error toast on failure
- [docs] Marked `6-7-workflow-as-node.md` and parent plan as completed

## 2026-02-24 (6-6 execution UX implementation)
- [feat] Added `ITERATION_STARTED`, `ITERATION_COMPLETED`, `HUMAN_INPUT_NEEDED` event types in `events.py`
- [feat] `WhileLoopExecutor` emits iteration_started/completed events with iteration index, max_iterations, condition per loop turn
- [feat] `ForEachExecutor` emits iteration_started/completed events per item with index and total count
- [feat] `HumanInTheLoopExecutor` generates request_id, emits `human_input_needed` event, passes structured metadata dict to callback
- [feat] Updated `human_input_callback` signature from `Callable[[str], ...]` to `Callable[[dict], ...]` in `executor.py` and `scheduler.py`
- [feat] `LLMExecutor._call_llm` now streams by default (`stream=True`), emits `intermediate_text` every 5 chunks with delta/accumulated text, falls back to non-streaming on failure
- [feat] `RunManager` — added pending-input registry (`_pending_human_inputs`), `submit_human_input()`, `get_pending_human_inputs()`, `_make_human_input_callback()`, wired callback into engine creation
- [feat] `RunManager._event_callback` — coalesces `intermediate_text` events (replaces in-place, preserves `done` events), increased `_max_event_buffer` to 2000
- [feat] `RunManager.subscribe` — catch-up includes `pending_human_inputs` for reconnect-safe dialog recovery
- [feat] Added `POST /api/runs/{run_id}/human-input` endpoint in `app.py` — validates request_id, delegates to `submit_human_input`
- [feat] Added `submitHumanInput` API client in `api.ts`
- [feat] Added `nodeIterations`, `streamingOutputs`, `pendingHumanInput` state slices in `useGraphStore.ts` with event handlers in `handleRunEvent`
- [feat] `DanNode.tsx` — loop indicator icon (↻) in header for while_loop/for_each nodes, condition badge for while_loop, live iteration counter badge
- [feat] `LogPanel.tsx` — added `intermediate_text` icon and data preview, iteration/human-input event colors
- [feat] `OutputPreview.tsx` — shows streaming text with pulsing cursor for running nodes, swaps to finalized output on completion
- [feat] Created `HumanInputDialog.tsx` — modal popup with prompt display, textarea response, Cmd+Enter submit, dismiss, error handling
- [feat] Mounted `HumanInputDialog` in `App.tsx`
- [test] Added 10 new tests: WhileLoop/ForEach iteration events, human-input events, streaming coalescing, event buffer sizing, event type existence (265 total)
- [docs] Marked `6-6-execution-ux.md` as completed; Phase 3.75 parent plan fully completed

## 2026-02-24 (6-7 workflow-as-node planning)
- [docs] Created `docs/plans/6-7-workflow-as-node.md` — new Phase 3.75 sub-plan for wrapping saved workflows as reusable composite nodes via palette/canvas insertion
- [docs] Reviewed/tightened `docs/plans/6-6-execution-ux.md` and `docs/plans/6-7-workflow-as-node.md` — added reconnect-safe human-input catch-up requirement, explicit stream coalescing/throttling task, deterministic port-collision policy, and invalid-import negative-path coverage
- [docs] Updated `docs/plans/6-phase-3.75-visual-editor-editing.md` — added 6-7 sub-plan row, expanded parent goal, and updated sequencing notes
- [docs] Updated `docs/todo.md` — added `6-7-workflow-as-node` under Phase 3.75 tracking

## 2026-02-24 (paper-writing LaTeX workflow hardening)
- [fix] Updated `compile_latex` in both `src/dan/server/app.py` and `examples/paper_writing.py` to auto-bootstrap `informs3.cls` into `output/` (copy from project root if present, otherwise fetch from a public template mirror), reducing first-run template failures
- [fix] Added LaTeX compatibility normalization in `compile_latex`: enforce `\\usepackage{hyperref}`, add `\\providecommand{\\newblock}{}`, and rewrite `\\bibliographystyle{informs2014}` to `\\bibliographystyle{plainnat}` before compilation
- [fix] Added citation/key safety in `compile_latex`: parse cited BibTeX keys from TeX, detect missing entries, and auto-append placeholder BibTeX entries so missing references no longer break/bottleneck compile runs
- [fix] Updated `check_latex_deps` semantics to treat `informs3.cls` as auto-bootstrap-capable (non-blocking warning) instead of hard-failing on missing local template files
- [fix] Updated `examples/paper_writing.py` assembly template and regenerated `graphs/paper_writing.json` so the workflow defaults include `hyperref`, `\\newblock` compatibility, `plainnat`, and review loop `max_iterations=3`
- [test] Verified with targeted suites: `tests/test_examples/test_paper_writing_e2e.py` (8 passed) and `tests/test_server` (27 passed)

## 2026-02-24 (6-6 execution UX planning)
- [docs] Created `docs/plans/6-6-execution-ux.md` — new Phase 3.75 sub-plan for loop visualization, streaming LLM output visibility, and human-in-the-loop popup/submit flow
- [docs] Reviewed and tightened `docs/plans/6-6-execution-ux.md` — added explicit callback-contract update tasks (`ExecutionContext`/`Engine`), request-id concurrency safety for human input, and in-place streaming log update strategy to prevent log/event explosion
- [docs] Added additional 6-6 safeguards — catch-up-safe streaming buffer policy in `run_manager.py`, non-deterministic `for_each` progress handling (`completed/total`), and fallback behavior for ambiguous loop drill-in feedback arrows
- [docs] Updated `docs/plans/6-phase-3.75-visual-editor-editing.md` — added 6-6 sub-plan row, expanded goal, and set status to `in-progress`
- [docs] Updated `docs/todo.md` — added `6-6-execution-ux` under Phase 3.75 and re-opened parent 6 plan as pending

## 2026-02-24 (6-5 InputNode, graph I/O, command palette, sub-graph grouping)
- [feat] Added `InputVariable` and `InputNode` Pydantic models in `src/dan/models/control_flow.py` — typed variables (string/number/boolean) with defaults
- [feat] Added `InputNode` to `Node` discriminated union in `graph.py`, registered in `registry.py`
- [feat] Created `InputExecutor` in `src/dan/executors/input.py` — pass-through executor that reads variable values from inputs, falls back to defaults
- [feat] Registered `InputExecutor` in `scheduler.py` `_register_defaults` and `executors/__init__.py`
- [feat] Added `InputNodeType` TS interface, `"input"` to `NODE_TYPE_CATALOG` (category `"io"`), and `NODE_DESCRIPTIONS` in `types/graph.ts`
- [feat] Added `createDefaultNode` case for `"input"` in `graphAdapter.ts` with one default string variable
- [feat] Added play-triangle SVG icon for `"input"` in `nodeIcons.tsx`
- [feat] Added `"io"` (Input / Output) category to `NodePalette.tsx` category order and labels
- [feat] Added `inputNodeValues: Record<string, Record<string, unknown>>` ephemeral state to Zustand store with `setInputNodeValue` action
- [feat] In `DanNode.tsx`, InputNode renders editable fields per variable (text/number/checkbox) on the node body
- [feat] Updated `startRun` to collect `inputNodeValues` from InputNode and pass as run inputs, bypassing RunInputsDialog
- [feat] Added Export button in `EditorToolbar.tsx` — serializes `danGraph` as formatted JSON, triggers browser download
- [feat] Added Import button in `EditorToolbar.tsx` — file picker validates `dan_graph_v1` structure, creates new graph via API, hard-resets ephemeral state
- [feat] Created `CommandPalette.tsx` — modal overlay with search input, arrow-key navigation, Enter to select, Escape to close; filters nodes by name/type substring match; on select centers viewport and selects node
- [feat] Added `commandPaletteOpen` state to store, bound `Cmd/Ctrl+K` in `useKeyboardShortcuts.ts`, mounted in `App.tsx`
- [feat] Added `groupIntoComposite()` store action — groups multi-selected nodes into a CompositeNode with auto-generated `in_`/`out_` ports, port collision handling, sub-graph creation, entry/exit point detection, edge remapping, and undo snapshot
- [feat] Bound `Cmd/Ctrl+Shift+G` for grouping in `useKeyboardShortcuts.ts`

## 2026-02-24 (6-4 validation)
- [feat] Extended `isValidConnection` in `connectionValidation.ts` with port-existence, single-incoming-edge, and JSON Schema type-level compatibility checks
- [feat] Added `POST /api/graphs/{graph_id}/validate` endpoint in `app.py` — runs `validate_graph()` and returns structured `{errors, warnings}` JSON with extracted `node_id`/`edge_id`
- [feat] Added `validateGraph` API client function in `api.ts` with `ValidationIssue`/`ValidationResult` types
- [feat] Added `validationErrors: Record<string, string[]>` to Zustand store — auto-populated after every successful `saveGraph()`, cleared on load/save-start
- [feat] Added validation error badge on `DanNode` — red dot in top-right corner with tooltip showing error messages
- [feat] Toast summary after validation — "N errors" warning or "Validation passed" info toast
- [fix] Added missing `BaseModel` import in `src/dan/models/control_flow.py` (pre-existing bug)

## 2026-02-24 (6-3 node & port editing)
- [feat] Port editor in `ConfigPanel.tsx` — replaced read-only comma-separated port display with editable rows (name input, required checkbox for input ports, delete button, "Add Port" button) for both input and output port lists
- [feat] Added `renamePort` and `deletePort` store actions in `useGraphStore.ts` — atomic port rename updates node ports + all connected edges' `source_port`/`target_port` + React Flow handles in one undo snapshot; delete removes port + all referencing edges
- [feat] Inline node rename in `DanNode.tsx` — double-click name span enters edit mode with transparent input; Enter/blur commits, Escape reverts, stopPropagation prevents drill-in, auto-select text via ref + useEffect
- [feat] Output schema visual editor (`SchemaEditor`) in `ConfigPanel.tsx` — for `llm_operator` and `router` nodes; Visual mode renders property rows (name, type dropdown, required checkbox, delete); Raw JSON mode with textarea; toggle between modes; invalid JSON blocks visual switch; empty schema auto-initializes as `{type:"object", properties:{}}`
- [feat] Port name validation — no duplicates, no empty names; inline red border + tooltip on violation
- [docs] Updated `docs/plans/6-3-node-port-editing.md` — checked off tasks 1–3 (code), noted 3-6 (nested objects) deferred to v2
- [docs] Updated `docs/architecture.md` — documented port editor, inline rename, SchemaEditor capabilities; updated directory descriptions

## 2026-02-24 (6-2 clipboard, context menu, edge reconnection)
- [feat] Added clipboard slice to Zustand store (`copySelected`, `pasteClipboard`, `duplicateSelected`) with UUID remapping, position offsetting, and internal-edge preservation
- [feat] Added keyboard shortcuts `Cmd/Ctrl+C` (copy), `Cmd/Ctrl+V` (paste), `Cmd/Ctrl+D` (duplicate) in `useKeyboardShortcuts.ts`, respecting text input focus
- [feat] Created `ContextMenu.tsx` — right-click context menu with canvas (Paste), node (Copy/Duplicate/Delete), and edge (Delete/Change Type) actions; dismisses on click-away or Escape
- [feat] Wired context menu in `GraphCanvas.tsx` via `onPaneContextMenu`, `onNodeContextMenu`, `onEdgeContextMenu` callbacks
- [feat] Enabled edge reconnection: `edgesReconnectable` prop + `onReconnect` handler that updates both React Flow edge and embedded DAN edge data, with `isValidConnection` guard
- [docs] Updated `docs/plans/6-2-clipboard-context-menu.md` — checked off tasks 1-5, recorded decisions
- [docs] Updated `docs/architecture.md` — documented ContextMenu component, updated store/hooks/canvas descriptions

## 2026-02-24 (4-1 grounded paper-writing upgrade)
- [feat] Rewrote `examples/paper_writing.py` into an INFORMS-oriented, internet-grounded workflow with parallel literature-aspect fan-out, citation verification, claim-evidence gating, human interview loop, evidence-aware section drafting, multi-role review panel, iterative revision, LaTeX compilation, and submission packaging
- [feat] Added example tool suite: `check_latex_deps`, `search_papers`, `search_web`, `citation_verifier`, `compile_latex`, `save_paper`, `package_submission`
- [feat] Expanded built-in server tool registry in `src/dan/server/app.py` to support search/verification/LaTeX/submission tools for editor-run workflows
- [refactor] Updated `src/dan/executors/control_flow.py` HumanInTheLoop executor to prefer dynamic prompt text from `user_prompt`/`prompt` input ports when provided
- [test] Replaced `tests/test_examples/test_paper_writing_e2e.py` with an updated deterministic suite for the new topology (8 passing tests)
- [test] Regression checks passed: `python -m pytest tests/test_examples/test_paper_writing_e2e.py -q` and `python -m pytest tests/test_server -q`
- [docs] Added and completed `docs/plans/4-1-grounded-paper-writing-upgrade.md`; updated `docs/todo.md` and `docs/architecture.md` to track the finished upgrade

## 2026-02-24 (Phase 3.75 planning)
- [docs] Created `docs/plans/6-phase-3.75-visual-editor-editing.md` — parent plan for Phase 3.75 (Visual Editor Full Editing) with 5 sub-plans, shared decisions (run-state isolation, layer-aware mutations, multi-select ripple effects, grouping scope lock, InputNode value persistence, import hard-reset), and dependency graph
- [docs] Created 5 sub-plan files with hierarchical task breakdowns:
  - `6-1-history-multiselect.md` — undo/redo history stack (GraphSnapshot, push/undo/redo, drag debounce, run-state exclusion, depth cap) + multi-select (lasso, shift-click, selectedNodeIds, bulk delete, ConfigPanel summary)
  - `6-2-clipboard-context-menu.md` — copy/paste/duplicate (clipboard slice, UUID remapping, cross-layer paste, edge preservation) + context menu (canvas/node/edge zones, 3 trigger callbacks) + edge reconnection (edgesReconnectable, onReconnect)
  - `6-3-node-port-editing.md` — port editor (add/remove/rename with atomic edge updates, schema, required toggle) + inline node rename (double-click, stopPropagation) + output schema visual builder (tree editor, raw JSON toggle)
  - `6-4-validation.md` — port-aware connection validation (port existence, schema compatibility, single-incoming-edge) + visual drag feedback (CSS handle classes) + validation API endpoint (POST /api/graphs/{id}/validate) + inline badges + toast summary
  - `6-5-graph-io-input-node.md` — InputNode (backend model/executor/registry + frontend catalog/adapter/icon/palette + ephemeral value persistence) + import/export JSON (hard-reset on import) + Cmd+K command palette + sub-graph creation from selection (cut-edge analysis, deterministic port naming, data-edges-only scope lock)
- [docs] Updated `docs/todo.md` — added plan links for Phase 3.75 (6 parent + 5 sub-plans), renumbered future phase plan references (Markdown 6→7, Marketplace 7→9, Memory stays at 8)

## 2026-02-24 (LLM API guide)
- [docs] Created `docs/llm-api-guide.md` — comprehensive LLM-facing API reference covering builder DSL, all 10 node types with full parameter signatures, four edge wiring mechanisms, sub-graph context managers (WhileLoop, ForEach, Composite), engine setup (EngineConfig, ToolRegistry, ExecutorRegistry, checkpointing, event callbacks), complete paper-writing example, REST API endpoints, type reference tables, patterns/recipes, and full import map
- [docs] Updated `.cursor/rules/project-tracking.mdc` — added `docs/llm-api-guide.md` to document inventory (read before writing API code; update when nodes, edges, builder, engine, executors, or examples change) and "After Each Modification" checklist (item 8)

## 2026-02-24 (README + tracking rule)
- [docs] Created `README.md` — project overview, quick start, builder DSL examples, visual editor features, API endpoints, roadmap
- [docs] Updated `.cursor/rules/project-tracking.mdc` — added `README.md` to document inventory and "After Each Modification" checklist (update on new features, setup changes, CLI commands, roadmap milestones)

## 2026-02-24 (Phase 3.75 roadmap + MVP fixes)
- [docs] Added Phase 3.75 — Visual Editor Full Editing to `todo.md`: 13 items covering port editor, inline rename, edge reconnection, copy/paste, undo/redo, multi-select, context menu, sub-graph creation from selection, schema editor, validation feedback, import/export, node search
- [fix] Sub-graph editing: removed read-only guards from GraphCanvas, ConfigPanel, NodePalette, BreadcrumbBar; `saveGraph` is now layer-aware (writes to correct `sub_graphs[key]`)
- [fix] Auto-layout on graph load: detects degenerate positions (all nodes at 0,0) and applies dagre layout automatically
- [fix] Server `.env` loading: added `load_dotenv()` to `app.py` so `DAN_LLM_API_KEY` is picked up from `.env`
- [fix] `RunInputsDialog`: replaced `useEffect`-based variable detection with synchronous `useMemo` to prevent premature auto-run before variables are computed

## 2026-02-24 (MVP — end-to-end runnable from UI)
- [feat] **Server-side tool registry:** `RunManager` now accepts a `ToolRegistry` parameter; engines created for runs use it. `app.py` lifespan registers `save_paper` as a built-in tool. Paper-writing workflow now runs end-to-end from the visual editor.
- [feat] **Run-inputs dialog:** `RunInputsDialog.tsx` — modal that detects `{variable}` template placeholders from entry node prompts + unconnected input ports. Shows a form before execution so users can provide workflow inputs (e.g. `topic` for paper writing). Graphs with no inputs run immediately. Cmd/Ctrl+Enter shortcut to submit.
- [refactor] `EditorToolbar.tsx`: Run button now opens `RunInputsDialog` instead of calling `startRun()` directly
- [infra] 256 backend tests passing, 0 TypeScript errors

## 2026-02-24 (Phase 3.5 — full implementation)
- [feat] **5-1 Multi-Layered Graph Navigation:**
  - Backend: Added `is_blackbox: bool = False` to `CompositeNode`; created `CompositeExecutor` (input/output mapping + single `run_subgraph` call); registered in scheduler
  - Frontend: `layerStack` + `drillIn`/`drillOut`/`jumpToLayer` in Zustand store; double-click drill-in on composite/while_loop/for_each nodes; read-only guards when drilled in; `BreadcrumbBar.tsx` (Root > Node > Node navigation); `PortMappingOverlay.tsx` (input/output port mapping display); deleted `CompositePreview.tsx` modal; CSS fade-in animation
  - Tests: `test_composite_executor.py` (4 tests, all passing)
- [feat] **5-2 Live Execution Visualization:**
  - `nodeTimings` + `activeExecutionPath` in store; `handleRunEvent` tracks start/end timestamps per node
  - `DanNode.tsx`: CSS pulse animation on active nodes, completion flash, duration badges (e.g. "123ms", "1.2s"), opacity dimming for inactive nodes during runs
  - `AnimatedEdge.tsx`: custom React Flow edge with SVG particle flow on active edges (source completed → target started), dimming for inactive edges; registered as `smoothstep` override
  - `ExecutionTimeline.tsx`: horizontal timeline bar with colored segments per node, click-to-select; mounted above bottom tabs
- [feat] **5-3 Rich Logging Window:**
  - Backend: 5 new `EventType` values (`LLM_THINKING`, `TOOL_CALL_STARTED`, `TOOL_CALL_RESULT`, `CODE_OUTPUT`, `INTERMEDIATE_TEXT`); `emit_event` on `ExecutionContext`; unified parent `run_id` for sub-graph events; rolling latest-500 event buffer (was first-500)
  - LLMExecutor emits `LLM_THINKING` (model, prompt preview); ToolExecutor emits `TOOL_CALL_STARTED`/`TOOL_CALL_RESULT`; CodeExecutor captures stdout/stderr via redirect and emits `CODE_OUTPUT`
  - Frontend: `EVENT_CATEGORY` mapping; rebuilt `LogPanel.tsx` with grouped-by-node sections, sub-grouped by category, inline SVG icons (brain, wrench, terminal, X), color coding, text/node/type filtering, click-to-select, auto-scroll
- [feat] **5-4 Build Palette:**
  - `paletteTemplates.ts`: extensible template factory (`TemplateResult` with `subGraphs: Record<string, DanGraph>`); ReAct template (WhileLoop + LLM→Tool body); Plan-Execute template (Composite + Planner→Executor body)
  - `selectedEdgeType` + `addTemplateNode` in store; `onConnect` uses selected edge type with proper styling (color, label, animation for context edges)
  - Rebuilt `NodePalette.tsx`: search input, collapsible categories (Operators, Control Flow, Pre-defined Agents, MCP/Wrapped Agents, Composite), template drag-drop with `template:` prefix, disabled MCP placeholders, compact edge type selector, `NODE_DESCRIPTIONS` hover tooltips
- [feat] **5-5 UI/UX Polish:**
  - Toast system: `ToastContainer.tsx` (fixed bottom-right, slide-in, auto-dismiss 4s/6s); `addToast`/`removeToast` in store; all async actions wrapped with success/error toasts
  - Loading states: `Spinner.tsx`; `loadingGraph`/`savingGraph` flags in store
  - Connection validation: `connectionValidation.ts` (no self-connect, no duplicates, port existence); `isValidConnection` prop on ReactFlow
  - Merged toolbar: `EditorToolbar.tsx` combining GraphSwitcher + RunPanel (DAN branding, graph selector, save/run/resume/disconnect, status badge, auto-layout button); replaced both components in App.tsx
  - Node icons: `nodeIcons.tsx` (inline SVG for all 10 types); added to `DanNode.tsx` header
  - Keyboard shortcuts: `useKeyboardShortcuts.ts` (Cmd/Ctrl+S → save)
  - Edge labels: data edges now show `source_port → target_port`
  - Auto-layout: `layout.ts` using dagre (LR, nodesep 40, ranksep 60); `applyAutoLayout` in store
  - Favicon: updated title + `favicon.svg`; ConfigPanel: larger textareas, editable edge config
- [infra] All changes validated: 256 backend tests passing, 0 TypeScript errors, 0 lint errors

## 2026-02-24 (Phase 3.5 plan consistency pass)
- [docs] Normalized all 5 sub-plans for consistent formatting: bold-keyword Notes (5-1), standardized "Stretch:" label for deferred items (all plans), unified "Docs sync" task naming with architecture.md → todo.md → changelog.md order (all plans), removed V1/V1.1 version labels (5-4), aligned test paths to existing `tests/test_*` layout (5-1, 5-3)

## 2026-02-24 (Phase 3.5 plan refinements — decision lock)
- [docs] Updated `plans/5-phase-3.5-frontend-design.md` shared decisions: locked unified parent `run_id` event stream for sub-graphs, navigation-only/read-only scope for 5-1 drill-in, and simple-first-but-extensible template strategy for 5-4
- [docs] Updated `plans/5-1-multi-layered-graph.md`: clarified read-only drill-in MVP, added explicit UI guardrails to disable edits while inside nested layers, and updated test scope accordingly
- [docs] Updated `plans/5-3-rich-logging.md`: added tasks for parent `run_id` reuse in `_run_subgraph`, hierarchy metadata tags (`graph_key`, `layer_path`, `parent_node_id`), and rolling latest-500 event buffer validation
- [docs] Updated `plans/5-4-build-palette.md`: changed template factory contract to support multi-level sub-graphs via extensible `subGraphs` payload while keeping V1 template implementations simple

## 2026-02-24 (Phase 3.5 — detailed sub-plans)
- [docs] Created top-level plan `plans/5-phase-3.5-frontend-design.md` — dependency graph, sequencing recommendation, shared decisions across all 5 sub-plans
- [docs] Created `plans/5-1-multi-layered-graph.md` — CompositeExecutor, `is_blackbox`, canvas drill-in replacing modal, breadcrumb bar, animated transitions, port mapping visualization (7 tasks, 23 sub-tasks)
- [docs] Created `plans/5-2-live-execution-viz.md` — pulse/glow CSS animations, animated edges with particles, execution path dimming, timeline/playback scrubber, duration badges (7 tasks)
- [docs] Created `plans/5-3-rich-logging.md` — 5 new backend event types, executor emission, structured collapsible log UI, icons/colors, click-to-select, filtering (6 tasks, 22 sub-tasks)
- [docs] Created `plans/5-4-build-palette.md` — searchable categorized sidebar, ReAct/Plan-Execute agent templates, MCP placeholder, edge type selector, hover preview tooltips (7 tasks)
- [docs] Created `plans/5-5-ui-polish.md` — error handling/toasts, loading states, connection validation, toolbar merge, resizable panels, keyboard shortcuts, auto-layout, syntax highlighting (13 task groups)
- [docs] Updated `todo.md` — replaced inline Phase 3.5 bullet lists with linked sub-plan references

## 2026-02-24 (todo restructure — Phase 3.5 Frontend Design)
- [docs] `todo.md`: added Phase 3.5 — Frontend Design with 5 sub-groups (A. Multi-Layered Graph Navigation, B. Live Execution Visualization, C. Rich Logging Window, D. Build Palette, E. UI/UX Polish)
- [docs] `todo.md`: absorbed old Phase 4 (Composite Nodes) into Phase 3.5-A, old Phase 5 (Execution Visualization) into Phase 3.5-B, old Phase 2.5 non-bug-fix items into Phase 3.5-E
- [docs] `todo.md`: removed Phase 2.5 section (completed bug fixes retained in changelog; remaining items moved to Phase 3.5-E); renumbered Phase 6 → Phase 4
- [docs] `architecture.md`: updated Composite Node Preview section — replaced "Phase 4" reference with Phase 3.5-A drill-in navigation plan
- [docs] Plan file created: `phase_3.5_frontend_design_99e66334.plan.md` with full breakdown, review fixes (sub-plan split guidance, typed event contract lockstep note, `is_blackbox` field), and documentation checklist

## 2026-02-24 (editor build cleanup — TypeScript fixes)
- [fix] `App.tsx`, `DanNode.tsx`, `CompositePreview.tsx`: replaced unsafe `Record<string, unknown>` casts with explicit node-type narrowing for `body_graph` access
- [fix] `NodePalette.tsx`: replaced `Object.groupBy` with typed `reduce` grouping to remove ES2024 dependency and fix strict TypeScript inference errors
- [fix] `useGraphStore.ts`: removed unused `portHandleId` import and unused `EMPTY_GRAPH` constant to satisfy strict compile checks
- [test] `editor/`: `npm run build` now passes (TypeScript + Vite build successful); only Node.js version warning remains (20.17 vs Vite recommended 20.19+)
- [docs] `bugs.md`: moved `Object.groupBy` issue out of Known Limitations and into Resolved Bugs
- [docs] `todo.md`: added and checked off "Fix editor TypeScript build blockers" item in Phase 2.5

## 2026-02-24 (Phase 2.5 — fix 5 open editor bugs)
- [fix] `GraphCanvas.tsx`: replaced manual `clientX - bounds.left` drop position calculation with `screenToFlowPosition()` from `useReactFlow()` — nodes now land correctly when canvas is zoomed/panned
- [fix] `graphAdapter.ts`: replaced module-level `_counter` with `Date.now()` + random suffix for node IDs — eliminates collisions after page refresh
- [fix] `GraphCanvas.tsx`: removed wrapper `onKeyDown` handler and `tabIndex`, added `deleteKeyCode={["Delete", "Backspace"]}` prop to `<ReactFlow>` — delete key now fires reliably via React Flow's native handling
- [fix] `useGraphStore.ts`: extracted `Date.now()` to a single `edgeId` const in `onConnect` — React Flow edge ID and `danEdge.id` are now always identical
- [fix] `App.tsx`: changed `hasBodyGraph` from `useCallback` to `useMemo`, updated call site to use the memoized boolean directly — avoids redundant recomputation on every render
- [fix] `GraphCanvas.tsx`: removed unused `danNodeToReactFlow` import
- [docs] `bugs.md`: moved all 5 bugs from "Open Bugs" to new "Resolved Bugs" section
- [docs] `todo.md`: checked off 5 bug-fix items in Phase 2.5

## 2026-02-24 (visual editor review — bugs + polish backlog)
- [docs] `bugs.md`: added 5 open bugs found during frontend code review — drop position wrong when zoomed/panned, node ID collisions after page refresh, delete key unreliable, onConnect edge ID mismatch, hasBodyGraph useCallback/useMemo
- [docs] `bugs.md`: added 9 known limitations — no error feedback, no loading states, no connection validation, ConfigPanel generic field dump, fixed-height bottom panel, two header bars, no keyboard shortcuts, app title/favicon defaults
- [docs] `todo.md`: added Phase 2.5 (Visual Editor Polish) with 19 MVP-critical items covering bug fixes, UX improvements, and visual polish

## 2026-02-24 (Phase 3)
- [feat] Phase 3 — Paper-Writing Proof of Concept (Extended):
  - `examples/paper_writing.py`: end-to-end workflow using builder DSL (~200 lines)
    - 8 nodes: LLMOperator x3 (idea_gen, lit_survey, outline_planner), ForEach (section_writers with parallel section writing), CodeOperator x2 (assembler, format_output), WhileLoop (review_loop with structured review-and-revise), ToolOperator (save_paper)
    - Structured output normalization on outline_planner (JSON schema → title, abstract, sections) and review_and_revise (verdict, feedback, draft)
    - Explicit ToolRegistry wiring: custom ExecutorRegistry with pre-registered save_paper tool function
    - Configurable CLI: topic, max review iterations, verbose logging
    - Event callback for live progress tracking
    - Saves compiled graph JSON to `graphs/paper_writing.json` and paper output to `output/`
  - `tests/test_examples/test_paper_writing_e2e.py`: 9 tests covering 3 categories
    - Happy-path mock e2e (6 tests): graph compilation, node types, entry/exit, JSON round-trip, full mock run, section count
    - Tool-failure recovery (2 tests): tool exception → FAILED status, unknown tool_id → FAILED status
    - Checkpoint/resume (1 test): full run with filesystem checkpointing, verify checkpoint files created, resume succeeds
  - Live-tested against vectorengine.ai with claude-sonnet-4-6: all 8 nodes completed, outline needed 2 normalization attempts, review loop ran 2 iterations, paper saved to output/
- [test] 9 new tests (252 total)
- [docs] Created `docs/plans/4-phase-3-paper-writing.md` with task breakdown
- [docs] Updated `todo.md`: marked Phase 3 complete, added 3 reliability backlog items (tool retry, per-node retry policy, handoff validators)
- [docs] Updated `architecture.md`: added `examples/` directory to tree

## 2026-02-24 (architecture expansion — hyperedges, HumanNode, context scoping, applications)
- [docs] `architecture.md`: added "Context Scoping Across Agent Boundaries" — four scopes (global, local, pass_down, emit_up) with explicit schemas at agent boundaries, upward signals (sticky/non-sticky), revised agent boundary contract (accepts, returns, reads_global, writes_global, signals)
- [docs] `architecture.md`: added "Hyperedges: Skills and Rules" — skills and rules modeled as hyperedges attaching to multiple nodes. Four types (skill, guardrail, style, override), attachment scope (node ID, type, tags, subgraph), precedence rules, execution hooks (pre_prompt, tool_call, post_output, validation)
- [docs] `architecture.md`: added "HumanNode (Generalized)" — human as a first-class node type, not external to the graph. Chat UI as renderer for HumanNode I/O. Adjustable autonomy via topology. Background mode = zero HumanNodes.
- [docs] `architecture.md`: added "Four Top-Level Agents Architecture" — Ask, Agent, Debug, Plan as independent DAN networks sharing a common context layer. Mode switching via shared context serialization.
- [docs] `development-plan.md`: added Section 6 "Applications: Rebuilding Real Systems on DAN" with two subsections:
  - 6.1 Coding Assistant (Cursor-like): ~15 node types, every mode as a graph template, four top-level agents with shared context
  - 6.2 Research IDE (science-cursor): scholar engines as DAN agents, PaperOrchestrator as a DAN network, rebuild strategy
- [docs] `todo.md`: added 5 backlog items — hyperedges, HumanNode generalization, context scoping, coding assistant PoC, science-cursor rebuild

## 2026-02-24 (docs review fixes)
- [docs] `architecture.md`: moved "Workflows as Code" out of Phase 1 section into its own "Phase 1.5 — not yet built" section (was misleading — `dan.builder` doesn't exist yet)
- [docs] `architecture.md`: fixed checkpointing description — "per topological level" not "per node"; removed stale "Targeted for Phase 1"
- [docs] `architecture.md`: removed duplicate frontend tech stack subsection (already in top-level Tech Stack)
- [docs] `architecture.md`: added missing files to directory tree (`__init__.py` files, `editor/package.json`, `vite.config.ts`, `tsconfig.json`)
- [docs] `architecture.md`: removed hardcoded test count from directory tree
- [docs] `changelog.md`: reordered to consistent newest-first (Phase 0 was at top despite being oldest)
- [docs] `development-plan.md`: fixed stale "Policies (to be defined in Phase 0)" → "(defined in Phase 0)"
- [docs] `development-plan.md`: updated Section 4 recommendation — struck through completed items, changed tense to past
- [docs] `bugs.md`: moved Node.js version warning and `Object.groupBy` from "Open Bugs" to "Known Limitations" (environment notes, not code bugs)
- [docs] `todo.md`: renumbered Phase 1.5 plan ID from `8:` to `1-5:` so future plan file sorts correctly between Phases 0 and 1

## 2026-02-24 (post-Phase 2 docs sync)
- [docs] Updated `architecture.md` tech stack — added FastAPI, Zustand, Tailwind CSS, pytest/httpx to explicitly list all dependencies
- [docs] Updated `development-plan.md` roadmap table — replaced "Est. Effort" with "Status" column, marked Phases 0/1/2 as Done with test counts
- [docs] Populated `bugs.md` — added Node.js version warning, `Object.groupBy` ES2024 requirement, known limitations (no editor-side validation, single-user, no undo/redo)

## 2026-02-24 (Phase 2)
- [feat] Phase 2 — Visual Editor full-stack implementation:
  - Engine event system: 9 typed events (`EngineEvent`, `EventType`), opt-in `event_callback` on `Engine`, backward compatible
  - Run manager (`server/run_manager.py`): background task execution, event pubsub via async queues, catch-up snapshots on subscriber reconnect
  - FastAPI backend (`server/app.py`): graph CRUD (list, create, get, update, delete), run endpoints (start, resume, status, list), WebSocket live event stream
  - Graph store (`server/graph_store.py`): filesystem-based JSON persistence in `./graphs/`, `last_opened` tracking
  - CLI entry point: `dan-serve` / `python -m dan.server` starts backend on localhost:8000
  - React Flow editor (`editor/`): Vite + React + TypeScript + React Flow v12 + Zustand + Tailwind CSS v4
  - Bidirectional DAN <-> React Flow adapter (`graphAdapter.ts`): port handles, edge type colors, node factory for all 10 types
  - Custom `DanNode` component: per-type color coding, port labels, execution status rings
  - `NodePalette`: draggable + click-to-add for all 10 node types grouped by category
  - `ConfigPanel`: dynamic form-based editing of all node/edge properties
  - `GraphCanvas`: React Flow canvas with drag-and-drop from palette, minimap, controls
  - `RunPanel`: save, run, resume, disconnect controls with live status badge
  - `LogPanel`: scrolling timestamped event log with color-coded event types
  - `OutputPreview`: per-node output viewer for selected node
  - `CompositePreview`: read-only sub-graph modal for body_graph-backed nodes
  - `GraphSwitcher`: graph list dropdown, create new, delete, auto-load last opened
- [infra] Added `fastapi`, `uvicorn[standard]`, `websockets`, `httpx` dependencies to `pyproject.toml`; bumped version to 0.2.0
- [infra] Added `dan-serve` script entry point in `pyproject.toml`
- [infra] Vite proxy config: `/api` routes to backend at localhost:8000 during development
- [fix] Replaced `assert _run_manager` in API endpoints with proper `_require_run_manager()` returning HTTP 503
- [test] 27 new tests (167 total): server API integration tests (CRUD + runs), run manager unit tests, engine event instrumentation tests
- [docs] Created `docs/plans/3-phase-2-visual-editor.md` with full task breakdown
- [docs] Updated `architecture.md` with Phase 2 modules, server architecture, API endpoints, frontend layout
- [docs] Updated `todo.md` Phase 2 line with plan link

## 2026-02-24 (Phase 1)
- [feat] Phase 1 complete — async execution engine built on Phase 0 type system:
  - `engine/state.py`: NodeStatus enum, PortDataStore (port data routing), ExecutionState (run-level aggregate)
  - `engine/context_runtime.py`: SharedContextStore (Layer 3), ArtifactStore (Layer 4, immutable versioning), LocalStateManager (Layer 2, scoped to composites)
  - `engine/executor.py`: EngineConfig, NodeExecutor protocol, ExecutionContext (scoped access for executors), ExecutorRegistry
  - `engine/conditions.py`: safe Python expression evaluator with restricted builtins for IfElse/WhileLoop conditions
  - `engine/normalizer.py`: OutputNormalizer pipeline — JSON extraction (fenced/inline), schema validation, re-prompt message builder
  - `engine/checkpoint.py`: CheckpointStore protocol, FileSystemCheckpointStore, NullCheckpointStore
  - `engine/scheduler.py`: Kahn's topological sort with parallel-level detection, asyncio.gather dispatch, sub-graph recursion, Engine.run()/resume() public API
  - `executors/llm.py`: LLMExecutor using AsyncOpenAI SDK (vectorengine.ai default, claude-sonnet-4-6), output normalization loop, transient API retry with backoff
  - `executors/tool.py`: ToolRegistry + ToolExecutor for function-based dispatch
  - `executors/code.py`: CodeExecutor with sandboxed Python exec and restricted builtins
  - `executors/control_flow.py`: IfElseExecutor, WhileLoopExecutor (with compaction + stagnation detection), ForEachExecutor (semaphore-based parallelism), ReduceExecutor, RouterExecutor (LLM-powered), HumanInTheLoopExecutor (callback-based with timeout)
- [infra] Added `openai>=1.0` and `pytest-asyncio` dependencies to `pyproject.toml`
- [infra] Added `.env` and `.env.example` for LLM provider config (vectorengine.ai endpoint)
- [test] 49 new engine tests (140 total): unit tests for state stores, expression evaluator, normalizer; integration tests for linear chain, IfElse branching, WhileLoop with condition exit, ForEach parallel fan-out, checkpoint/resume
- [docs] Added `docs/plans/2-phase-1-orchestration-engine.md` with full task breakdown
- [docs] Updated `architecture.md` with engine module layout and execution engine section
- [docs] Marked Phase 1 complete in `todo.md`
- [docs] Added "Workflows as Code" as first-class design principle — every workflow must be definable in Python code, not just visually. Added to `development-plan.md` Section 5, `architecture.md`, and key decisions.
- [docs] Added Phase 1.5 (Workflow Builder API) to roadmap — fluent Python DSL (`dan.builder`) that compiles to `dan_graph_v1` JSON, round-trips with visual editor. Inserted before Phase 2 in `todo.md` and `development-plan.md`.

## 2026-02-24 (Phase 0 + project setup)
- [docs] Created project tracking structure: `docs/` directory with `architecture.md`, `changelog.md`, `todo.md`, `bugs.md`, and `plans/`
- [docs] Moved `development-plan.md` from project root to `docs/`
- [docs] Scaffolded `architecture.md` from development plan sections 4–5
- [infra] Added `.cursor/rules/project-tracking.mdc` — always-apply rule governing doc maintenance workflow
- [docs] Changed plan naming from flat sequential (`plan-01-name`) to hierarchical (`1-name`, `1-1-name`, `1-1-1-name`) to mirror the task tree
- [docs] Added `docs/plans/1-phase-0-formal-spec.md` and linked Phase 0 in `docs/todo.md`
- [docs] Resequenced roadmap: visual editor moved to Phase 2, paper-writing proof shifted to Phase 3, advanced execution visualization moved to Phase 5
- [docs] Updated architecture to lock language split (Python core + TypeScript editor) and shared `dan_graph_v1` JSON contract
- [feat] Phase 0 complete — implemented formal spec as Python Pydantic types:
  - Port models (`InputPort`, `OutputPort`) with JSON Schema type declarations
  - 10 node types: 3 operators (LLM, Tool, Code), 6 control-flow (IfElse, WhileLoop, ForEach, Reduce, Router, HumanInTheLoop), 1 composite
  - 3 edge types (Data, Control, Context) with discriminated-union deserialization
  - Four-layer context model (edge data, node-local state, shared context store, artifact store)
  - Composite-node contract (external schemas, control state, local state, read/write sets, compaction rules, projections)
  - `dan_graph_v1` JSON serialization contract with UI metadata (position, ui dict) for lossless editor round-trips
  - Graph with recursive sub-graphs, entry/exit points, shared context declarations
  - Node type registry for extensibility
  - Validation: port schema compatibility (MVP structural), graph well-formedness (7 checks), cycle detection
  - 75 tests passing including full paper-writing motivating example
- [docs] Added four-layer context management model to `development-plan.md` Section 5 and `architecture.md`: edge data (bounded), node-local state (scoped), shared context store (blackboard), artifact store (by reference)
- [docs] Added context projection pattern — scope boundary functions that extract minimal views per consumer (loop controller vs. reviser vs. parent graph)
- [docs] Defined composite node contract (external schemas, control state, local working set, read/write sets, compaction rule) and context policies (mutation, parallel merge, compaction, failure exits)
- [docs] Expanded Phase 0 plan with context management tasks (3-1 through 3-6), context-related validation rules (5-3, 5-4), and context scoping tests (6-3)
- [docs] Added memory system concept to `development-plan.md` Section 5 (short-term vs. long-term, encoding/consolidation/retrieval) and backlog item in `todo.md`. Parked as backlog — not yet designed.
- [docs] Added output normalization (batch-norm analogy — deterministic parse → validate → re-prompt → retry on every LLM operator) to `development-plan.md`, `architecture.md`, and Phase 0 plan task 2-5
- [docs] Added operator-level retry policy (`max_retries`, `backoff`, `fallback_model`, `on_failure`) to `development-plan.md`, `architecture.md`, and Phase 0 plan task 2-6
- [docs] Added checkpointing/resumability to Phase 1 scope (`development-plan.md` roadmap, `todo.md`, `architecture.md`)
- [infra] Added root `.gitignore` with Python runtime/build/test ignores and future TypeScript editor artifacts (`node_modules`, JS package-manager logs)
- [docs] Added backlog items: dynamic `model_policy` (budget-aware + learned assignment for cases beyond strategies 1-4), `max_concurrency` on For-Each/Map
- [fix] Validation: edge endpoint checks (node + port existence) now apply to all edge types, not just DataEdge
- [fix] Validation: ContextEdge mode enforcement — read edges require key in node's `read_set`, write/append edges require key in `write_set`
- [fix] Validation: empty-schema data edges now emit a warning ("schema safety bypassed") instead of silently passing
- [fix] Model: added `control_state_schema` to `ForEachNode` and `CompositeNode` to match composite-node contract in docs
- [fix] Registry docstring corrected — registry is for programmatic discovery, not JSON deserialization (which uses Pydantic discriminated union)
- [test] Test suite expanded from 75 to 91 tests: added edge endpoint tests (all edge types), context permission enforcement, empty schema warnings, registry discovery

## 2026-02-24 (Phase 1.5)
- [feat] Phase 1.5 complete — fluent workflow builder DSL (`dan.builder`):
  - `builder/refs.py`: `NodeRef` and `PortRef` compile-time proxies with `__format__` (marker emission), `__rshift__` (>> chaining), `__getitem__` (port subscript), sanitized alias generation
  - `builder/builder.py`: `WorkflowBuilder` class with `workflow()` factory, node creation methods (`.llm()`, `.tool()`, `.code()`, `.if_else()`, `.reduce()`, `.router()`, `.human_in_the_loop()`), `.edge()` explicit wiring, context-manager sub-graphs (`.while_loop()`, `.for_each()`, `.composite()`), `.to_json()` / `.to_dict()` serialization
  - `builder/compiler.py`: compiles builder state → `Graph` model. Resolves f-string markers (`<<dan:node_id:port>>`), auto-generates InputPort/OutputPort, auto-generates DataEdge objects, node-type output contract map (matches runtime executor ports), entry/exit point detection, `validate_graph()` integration
  - `builder/decompiler.py`: `decompile(graph) -> str` produces executable Python. Topological sort with deterministic ordering, chain detection (>> sugar), context-manager emission for sub-graphs, `NodeRef` wrappers for sub-graph wiring, lossless preservation of metadata/context/edge types
- [feat] Four connection mechanisms: (1) f-string magic auto-wiring, (2) >> operator chaining, (3) PortRef passing, (4) explicit wf.edge()
- [feat] Node-type output contract map: `DEFAULT_OUTPUT_PORTS` maps each node_type to its actual runtime output port name (e.g. `llm_operator` → `text`, `for_each` → `results`, `if_else` → `branch`)
- [test] 70 new builder tests (237 total): unit tests for NodeRef/PortRef, marker resolution, compiler (each node type), sub-graph context managers, decompiler round-trip; integration tests for paper-writing workflow, engine execution (code chain, while-loop, for-each), editor round-trip golden test
- [docs] Added `docs/plans/1-5-builder-api.md` with full task breakdown
- [docs] Updated `architecture.md` with builder module layout, DSL design, connection mechanisms, decompiler details
- [docs] Marked Phase 1.5 complete in `todo.md`

## 2026-02-24 (Phase 4 roadmap — review fixes)
- [docs] `todo.md`: reworded Phase 4 description — markdown is a third authoring surface alongside Python DSL and visual editor, not a replacement
- [docs] `todo.md`: added plan file link placeholder (`6:`) for Phase 4; renumbered Phase 5 marketplace to `7:` to avoid collision with Phase 3.5's `plans/5-*` prefix
- [docs] `todo.md`: clarified backlog overlap — split hyperedge items into "engine runtime" (execution hooks, attachment logic) vs "markdown authoring syntax" (`.md` file references in workflow)
- [docs] `todo.md`: added 5 backlog items — markdown/Python coexistence policy, `dan.loader` ↔ `dan.builder` parity checklist, compiler diagnostics + source maps, markdown round-trip conformance tests, markdown format versioning
- [docs] `architecture.md`: replaced "Code-first, visual-second" key decision with "Three authoring surfaces, one IR" (Python DSL, markdown, visual editor all compile to `dan_graph_v1`)
- [docs] `development-plan.md`: replaced "Workflows as Code" section with "Three Authoring Surfaces, One IR" — table comparing Python / markdown / visual editor strengths and use cases
- [docs] `development-plan.md`: updated roadmap table — marked Phases 1.5 and 3 as Done with test counts, added Phase 3.5 and Phase 4, removed stale old Phases 4/5 (composite + visualization, now in 3.5)

## 2026-02-24 (Phase 4 Memory & Context Scoping)
- [docs] Promoted memory & context scoping from backlog to Phase 4 — context scoping across agent boundaries (global/local/pass_down/emit_up) and memory system for long chains (encoding, consolidation, retrieval)
- [docs] Renumbered Markdown Agent Format → Phase 5, Shareable Blocks / Marketplace → Phase 6

## 2026-02-24 (Phase 4 roadmap — now Phase 5)
- [docs] Added Phase 4 — Markdown Agent Format to `todo.md`: agent file format (YAML frontmatter + natural language), workflow file format (arrow notation), flow notation parser, port type inference, auto-wiring, `dan.loader` compiler, and paper-writing rewrite as validation
- [docs] Renumbered Shareable Blocks / Marketplace to Phase 5
- [docs] Added backlog items: composite agents in markdown, skills/rules as markdown hyperedges, markdown round-trip from visual editor, linked JSON Schema files

## 2026-02-24 (Phase 3.5 post-review bug fixes)
- [fix] Read-only drill-in guard: disabled click-to-add, drag, and editing in `NodePalette` and `ConfigPanel` when drilled into sub-graph layer
- [fix] Template port contracts: LLM nodes output port renamed `output` → `text` (matching `LLMExecutor` output key); ReAct loop condition changed `"not done"` → `"True"` (avoids `ConditionError`); Plan-Execute composite given proper `input_mappings`/`output_mappings`
- [fix] `startRun()` stale-graph guard: `saveGraph()` now returns `boolean`; `startRun` aborts if save fails
- [fix] Edge type styling sync: `updateEdgeData` now updates top-level `animated`, `label`, and `style.stroke` when `edge_type` changes (previously only updated `data.danEdge`)
- [fix] Subgraph event hierarchy tags: added `layer_path: tuple[str, ...]` to `ExecutionContext`; `emit_event` injects `layer_path` into event data; `run_subgraph` accepts `parent_node_id` and threads it to child contexts via scheduler
- [fix] LogPanel node names: `nodeNameMap` now reads `data.name` (matching DanNode model) with `data.label` as fallback
- [test] Fixed composite executor test mock to accept new `parent_node_id` parameter; 256 tests pass, TypeScript zero errors

## 2026-02-24 (Phase 1.5 post-review fixes)
- [fix] Builder/compiler idempotence: repeated `build()` calls now produce stable output (no in-place mutation of pending node kwargs/ports during compilation)
- [fix] Sub-graph entry refs: `body.input` / `body.item` now compile to usable entry input placeholders (`{input}` / `{item}`) with proper input-port generation for sub-graph entry nodes
- [feat] Builder typed-edge support: added `wf.control_edge(...)` and `wf.context_edge(...)`; compiler now materializes `ControlEdge` and `ContextEdge` (not only `DataEdge`)
- [fix] Decompiler edge fidelity: emits executable `control_edge`/`context_edge` calls instead of comments; round-trip now preserves edge types
- [fix] Decompiler chain safety: `>>` sugar is emitted only for true default-port chains; custom port wiring is preserved via explicit `wf.edge(...)`
- [fix] Decompiler metadata fidelity: preserves node `position`/`ui`/`metadata`, graph `created_at`/`updated_at`, and `artifact_refs`
- [feat] Builder artifact support: added `wf.artifact_ref(...)` and compiler support for graph-level `artifact_refs`
- [test] Added 6 regression tests for post-review issues (build idempotence, sub-graph entry refs, control/context edge round-trip, custom-port chain fidelity, UI/metadata/artifact preservation); total test suite now 243 passing

## 2026-02-24 (Phase 3.75 — 6-7 Workflow as Reusable Node, partial)
- [feat] Created `editor/src/lib/graphImporter.ts` — `graphAsCompositeNode()` converts a saved DAN graph into a CompositeNode insertion payload with recursive ID namespacing, port derivation from entry/exit points, collision-safe naming, and flattened sub_graphs
- [feat] Updated `NodePalette.tsx` — added "Saved Workflows" category listing all saved graphs (excluding current), with search filtering, drag/drop (`workflow:{graphId}`), and click-to-insert support
- [feat] Updated `GraphCanvas.tsx` — `onDrop` handler routes `workflow:` prefixed payloads to `addGraphAsNode` store action
- [docs] Updated `architecture.md` — added `graphImporter.ts` to directory structure, updated NodePalette description
- [docs] Plan 6-7 marked in-progress; tasks 2 (palette UX), 3 (import utility), and 4-4 (canvas drop) checked off. Remaining: store action `addGraphAsNode` (4-1–4-3, 4-5–4-6), validation/tests (6), docs sync (7)

## 2026-02-24 (Fix: log truncation + run recovery on refresh)
- [fix] Backend: raised event truncation limits — LLM prompt preview 200→2000 chars, tool args 100→2000, tool result 500→5000/2000 (`llm.py`, `tool.py`)
- [fix] Backend: raised `RunManager` event buffer 2000→10000 (`run_manager.py`)
- [feat] Frontend: `LogPanel` rows now expandable — click "more" to reveal full content; removed hard `.slice(0,200)` previews in `dataContent`
- [fix] Frontend: store log buffer raised 500→5000 entries (`useGraphStore.ts`)
- [feat] Frontend: persist active run to `sessionStorage` on start/resume; clear on terminal state (`run_completed`/`run_failed`)
- [feat] Frontend: `recoverActiveRun()` action — on page load, reads `sessionStorage`, validates run via `api.getRun()`, reconnects WebSocket with `_catchup` replay if still running
- [feat] `App.tsx` calls `recoverActiveRun()` after `loadGraphList()` on mount

## 2026-02-24 (Phase 4 memory policy defaults)
- [docs] `todo.md`: expanded Phase 4 defaults with an explicit future-tuning policy — current memory budgets/thresholds/TTLs/reducer choices are baseline defaults to be iteratively tuned using telemetry, retrieval quality, and cost/latency trade-offs

## 2026-02-24 (Phase 3.75 — 6-9 multi-tab workflow sessions plan)
- [docs] Added `docs/plans/6-9-multi-tab-workflow-sessions.md` with a tab-scoped state architecture plan (snapshot/restore, per-tab run recovery, toolbar tab UI, and tests)
- [docs] Reviewed and tightened 6-9 plan scope: added cross-tab event bleed guard (`run_id` check), lightweight storage constraints (metadata-only persistence), legacy-key migration, and `createGraph` tab-open behavior
- [docs] Updated `docs/plans/6-phase-3.75-visual-editor-editing.md` — set parent status to `in-progress` and added sub-plan row for 6-9
- [docs] Updated `docs/todo.md` — added 6-9 as an unchecked Phase 3.75 sub-plan and marked the parent 6-phase item as in-progress

## 2026-02-24 (Phase 3.75 — 6-10 gate editor UX)
- [feat] `editor/src/types/graph.ts`: added `GateNode` interface (`gate_mode`, `condition`, `max_iterations`) to `DanNode` union, `NODE_TYPE_CATALOG`, and `NODE_DESCRIPTIONS`
- [feat] `editor/src/lib/paletteTemplates.ts`: added `ifElseGateFactory` and `whileGateFactory` template factories + registered in `PREDEFINED_AGENT_TEMPLATES`
- [feat] `editor/src/lib/nodeIcons.tsx`: added diamond/rhombus icon for "gate" node type
- [feat] `editor/src/components/DanNode.tsx`: gate condition badge, "IF"/"WHILE" header indicator, green/red branch port coloring for gate output handles
- [feat] `editor/src/lib/graphAdapter.ts`: back-edge detection for while-gate `continue` port (dashed, animated, muted "loop back" label); added "gate" case to `createDefaultNode`
- [feat] `editor/src/components/ConfigPanel.tsx`: dedicated gate config section with gate_mode dropdown (auto-swaps output ports), condition input, and conditional max_iterations field

## 2026-02-24 (Phase 3.75 — 6-10 GateNode model + executor)
- [feat] Added `GateNode` model in `src/dan/models/control_flow.py` — unified conditional gate with `gate_mode="if_else"` (true/false branches) and `gate_mode="while"` (continue/done branches), `max_iterations` guard
- [feat] Added `GateExecutor` in `src/dan/executors/control_flow.py` — evaluates condition, routes inputs to exactly one branch output port, emits `gate_evaluated` event
- [feat] Registered `GateExecutor` in `src/dan/engine/scheduler.py` under `"gate"` node type
- [feat] Added `GateNode` to `NodeTypeRegistry` in `src/dan/registry.py`, `Node` discriminated union in `src/dan/models/graph.py`, and package exports in `src/dan/__init__.py`
- [feat] Added `GATE_EVALUATED` event type to `src/dan/engine/events.py`
- [test] Added `tests/test_engine/test_gate.py` — 10 tests covering model validation, if_else/while branching, error handling, metadata
- [fix] Updated `test_builtins_registered` count from 11 → 12 to account for new gate type

## 2026-02-24 (Phase 3.75 — 6-10 gate loop + condition redesign plan)
- [docs] Added `docs/plans/6-10-gate-loop-condition-redesign.md` covering gate-based visible loop authoring, condition-routing redesign, cycle-aware scheduler updates, collapsible loop groups, and migration strategy
- [docs] Reviewed and tightened plan risk areas: limited scope to while+condition redesign (keep `for_each`/`composite` unchanged), added transition compatibility for legacy graphs, constrained scheduler work to gate-controlled cycle regions with DAG fast-path retained, and added builder/decompiler + rollout-flag tasks
- [docs] Updated `docs/plans/6-phase-3.75-visual-editor-editing.md` with 6-10 sub-plan row and sequencing note
- [docs] Updated `docs/todo.md` with unchecked 6-10 Phase 3.75 sub-plan entry

## 2026-02-24 (Phase 3.75 — 6-10 GateNode/GateExecutor iteration tracking)
- [feat] Updated `GateNode.model_post_init` in `src/dan/models/control_flow.py` — auto-derives `output_ports` from `gate_mode` (if_else: true/false, while: continue/done) when not explicitly set
- [feat] Updated `GateExecutor` in `src/dan/executors/control_flow.py` — while mode now reads `gate_iteration` from `context.local_state` and injects it as `iteration` into condition vars; event data includes `iteration`/`max_iterations`; metadata includes `iteration`; error returns raw `ConditionError` string
- [test] Added `tests/test_engine/test_gate_executor.py` — 9 tests covering if_else branching, while branching with iteration tracking, condition errors, iteration counter availability, and output port derivation
- [fix] Updated `tests/test_engine/test_gate.py` — added `local_state` to MockContext for while-mode tests, updated error assertion to match new error format

## 2026-02-24 (Phase 3.75 — 6-10 cycle-aware scheduler + validation)
- [feat] `src/dan/engine/state.py`: added `PortDataStore.clear_node(node_id)` for clean cycle iteration resets
- [feat] `src/dan/engine/scheduler.py`: cycle-aware scheduling — `_topological_levels_with_backedges()` detects gate back-edges, `_iterate_cycle()` resets and re-executes cycle-region nodes, `_should_skip()` exempts while-gate back-edge ports from skip logic
- [feat] `src/dan/validation/graph.py`: enhanced `_validate_gate_cycles()` with bidirectional reachability for per-gate cycle regions, rejects gateless cycles and overlapping multi-gate cycles
- [test] Added `tests/test_engine/test_cycle_scheduling.py` — 14 tests covering DAG fast-path, while-gate loops, max_iterations, if_else branch skipping, gateless cycle rejection, and multi-gate cycle rejection
- [fix] Fixed `_should_skip` pre-existing bug where while-gate back-edge ports caused cycle-body nodes to be silently skipped
- [fix] Updated `tests/test_engine/test_streaming.py` — corrected `_max_event_buffer` assertion from 2000 → 10000 to match earlier buffer increase

## 2026-02-24 (Phase 3.75 — 6-9 multi-tab workflow sessions implementation)
- [feat] `editor/src/store/useGraphStore.ts`: added `TabInfo`/`TabSnapshot` types, `tabs`/`activeTabId`/`tabCache` state, `_snapshotActiveTab`/`_restoreTab`/`_persistTabState` helpers, `openTab`/`switchTab`/`closeTab`/`restoreTabs` actions, cross-tab event guard, tab-aware `createGraph`/`deleteGraph`/`startRun`/`resumeRun`
- [feat] Added `editor/src/components/TabBar.tsx` — horizontal tab bar with graph name, run status dot, close button, "+" graph picker dropdown
- [feat] `editor/src/components/EditorToolbar.tsx`: replaced graph `<select>` dropdown with `<TabBar />`, routed graph open through `openTab`
- [feat] `editor/src/App.tsx`: startup calls `restoreTabs()` after `loadGraphList()` for tab + run recovery
- [docs] Updated `docs/architecture.md` test count to 322
- [docs] Marked 6-10 tasks 1, 2, 3, 4 (partial), 5 (partial), 7 (partial) as completed in plan

## 2026-02-24 (Phase 3.75 — 6-10 backend migration, builder/decompiler, tests)
- [feat] `src/dan/executors/control_flow.py`: added `DeprecationWarning` to `IfElseExecutor.execute()` and `WhileLoopExecutor.execute()` — legacy executors still work but emit warnings (task 2-3)
- [feat] `src/dan/validation/graph.py`: added `_check_deprecated_edge_conditions()` — emits non-blocking deprecation warnings for `ControlEdge.condition` usage (task 4-3)
- [feat] `src/dan/engine/scheduler.py`, `src/dan/builder/compiler.py`: added `"deprecated"` to `_VALIDATION_WARNING_PATTERNS` so deprecation messages are non-fatal in both engine and builder
- [feat] `src/dan/server/app.py`: validate endpoint now separates deprecated messages into `warnings` list (was always `[]`)
- [feat] Created `src/dan/migration/gate_migration.py` with `migrate_if_else_to_gate()`, `migrate_while_loop_to_flat_gate()`, and `migrate_graph()` (tasks 6-1, 6-2)
- [feat] `src/dan/server/app.py`: `GET /api/graphs/{id}` applies `migrate_graph()` when `DAN_GATE_MIGRATION_ENABLED=true` env var is set (tasks 6-3, 6-5)
- [feat] `src/dan/builder/builder.py`: added `gate()` method to `WorkflowBuilder` (task 6-4)
- [feat] `src/dan/builder/compiler.py`: added `GateNode` to `_build_node()` and `"gate"` to `DEFAULT_OUTPUT_PORTS` (task 6-4)
- [feat] `src/dan/builder/decompiler.py`: added gate node decompilation — emits `wf.gate()` with conditional `gate_mode`/`max_iterations` kwargs (task 6-4)
- [test] Created `tests/test_migration/test_gate_migration.py` — 11 tests covering if_else migration, while_loop flattening, combined migration, noop on clean graphs (task 7-3)
- [test] Added 4 gate round-trip tests to `tests/test_builder/test_decompiler.py` — if_else and while gate modes, default kwarg elision (task 7-4)
- [fix] Updated `tests/test_builder/test_compiler.py` expected node type set to include `"gate"`

## 2026-02-24 (Phase 3.75 — 6-10 collapsible loop groups)
- [feat] `editor/src/types/graph.ts`: added `LoopGroup` interface and `loop_groups` field on `GraphMetadata` for visual-only loop grouping
- [feat] `editor/src/lib/graphAdapter.ts`: added `injectLoopGroups` / `stripLoopGroups` helpers for injecting/removing group nodes + synthetic edges; updated `reactFlowToDanGraph` to filter out `loopGroup` nodes
- [feat] Added `editor/src/components/LoopGroupNode.tsx` — collapsed view (compact card with handles + expand button) and expanded view (dashed amber border with collapse button)
- [feat] `editor/src/components/GraphCanvas.tsx`: registered `loopGroup` node type
- [feat] `editor/src/components/ContextMenu.tsx`: added "Create Loop Group" (when multi-select includes a while-gate) and "Ungroup Loop" (when right-clicking a grouped node)
- [feat] `editor/src/store/useGraphStore.ts`: added `loopGroups` state, `createLoopGroup`/`toggleLoopGroup`/`removeLoopGroup` actions, loop-group-aware load/save (serializes to `metadata.loop_groups`), tab snapshot integration

## 2026-02-24 (Phase 3.75 — 6-10 migration + builder + rollout)
- [feat] `src/dan/executors/control_flow.py`: added `DeprecationWarning` to `IfElseExecutor` and `WhileLoopExecutor`
- [feat] `src/dan/validation/graph.py`: added `_check_deprecated_edge_conditions()` — non-blocking warnings for `ControlEdge.condition` usage
- [feat] Created `src/dan/migration/gate_migration.py`: `migrate_if_else_to_gate()`, `migrate_while_loop_to_flat_gate()`, `migrate_graph()` helpers
- [feat] `src/dan/builder/builder.py`: added `gate()` method to `WorkflowBuilder`
- [feat] `src/dan/builder/decompiler.py`: added gate node decompilation support
- [feat] `src/dan/server/app.py`: `DAN_GATE_MIGRATION_ENABLED` env flag for optional migration on graph load
- [test] Added `tests/test_migration/test_gate_migration.py` — 11 migration tests
- [test] Added gate round-trip tests in `tests/test_builder/test_decompiler.py` — 4 tests
- [docs] Updated `README.md` with Phase 3.75 features, new API endpoints, test count (337), roadmap entry
- [docs] Updated `docs/architecture.md` with gate model, cycle-aware scheduling, migration policy, test count
- [docs] Marked 6-10 plan as completed, parent 6-phase plan as completed, both checked off in `todo.md`

## 2026-02-24 (Phase 3.75 — code review fixes)
- [fix] `editor/src/store/useGraphStore.ts`: `closeTab` now checks `dirty` state (from live store or tabCache) and shows a confirmation dialog before closing
- [fix] `editor/src/store/useGraphStore.ts`: `deleteGraph` on the last open tab now clears `tabs`, `activeTabId`, `loopGroups`, and run state, then persists to sessionStorage
- [fix] `src/dan/migration/gate_migration.py`: updated `migrate_if_else_to_gate` docstring to document that `branch→true` remapping is best-effort; added `logger.warning` on each remapped edge
- [fix] `src/dan/migration/gate_migration.py`: `migrate_while_loop_to_flat_gate` now infers exit/entry port names from body node output_ports/input_ports instead of hardcoding `result`/`input`; skips migration gracefully when entry or exit points are empty
- [fix] `src/dan/server/app.py`: unified validation warning classification — "schema safety bypassed" and "untyped data edge" messages now classified as warnings alongside "deprecated"
- [fix] `editor/src/components/ContextMenu.tsx`: moved `setSelectedNode(targetId)` from render body into `useEffect` to prevent state writes during render
- [test] Added 4 migration edge-case tests: port inference for exit/entry, empty body skip, and warning log assertion (341 tests total)
- [docs] Updated test count to 341 in `README.md` and `docs/architecture.md`

## 2026-02-24 (Phase 3.75 — 6-11 workflow UX polish)
- [feat] `src/dan/executors/llm.py`: widened `_call_llm` return type to include token usage dict; streaming path passes `stream_options={"include_usage": True}`, non-streaming reads `resp.usage`; `execute()` accumulates usage across normalization retries and includes it in `NodeResult.metadata`
- [feat] `src/dan/engine/scheduler.py`: added `_aggregate_usage()` to sum token counts from all `state.node_metadata`; `_execute()` now records `run_start_time` and emits `elapsed_seconds`, `total_prompt_tokens`, `total_completion_tokens`, `total_tokens` in `run_completed`/`run_failed` events
- [feat] `editor/src/store/useGraphStore.ts`: added `runSummary` state (captured from `run_completed`/`run_failed` event data); included in `TabSnapshot` for tab-switch persistence; cleared on run start/resume
- [feat] `editor/src/components/LogPanel.tsx`: added `RunSummaryBar` component — compact summary bar at bottom of log panel showing elapsed time and token counts on run completion/failure
- [feat] `editor/src/lib/layout.ts`: added `needsAutoLayout(nodes)` — detects degenerate positions (all same point, bounding box < 50px, or NaN/undefined)
- [feat] `editor/src/store/useGraphStore.ts`: `drillIn`, `drillOut`, `jumpToLayer` now auto-apply dagre layout when sub-graph positions are degenerate
- [feat] `editor/src/components/TabBar.tsx`: removed already-open graph filter from "+" picker — all saved graphs always shown; replaced "All graphs already open" with "No saved graphs" empty state; added duplicate tab name disambiguation with counter suffix
- [feat] `editor/src/store/useGraphStore.ts`: removed `openTab` short-circuit that redirected to existing tab with same `graphId` — each `openTab` call now creates an independent tab
- [docs] Updated plan `6-11-workflow-ux-polish.md` — patched with review findings (widened return type, aggregate from node_metadata, save-conflict note, NaN positions), marked all tasks completed
- [docs] Updated `docs/todo.md` — marked 6-11 completed
- [docs] Updated `docs/changelog.md` with implementation entry

## 2026-02-24 (Phase 3.75 — 6-11 template switch follow-up)
- [fix] `editor/src/components/TabBar.tsx`: fixed Babel parse error by parenthesizing `??`/`||` expression for tab title rendering
- [feat] `editor/src/store/useGraphStore.ts`: added `replaceActiveTabGraph(graphId)` to swap template/graph in the active tab (dirty-check confirmation, websocket disconnect, run/log/summary reset)
- [feat] `editor/src/components/TabBar.tsx`: added dual picker modes — `+` opens selected template in a new tab; `↺` replaces the current tab template in-place
- [fix] `editor/src/store/useGraphStore.ts`: cleared `runSummary` in last-tab `deleteGraph` branch and in `openTab` reset state to prevent stale summary leakage across template switches
- [test] Verified TypeScript (`npx tsc --noEmit`) and backend tests (`341 passed`)
- [docs] Updated `docs/plans/6-11-workflow-ux-polish.md` with completed sub-task 3-4 for current-tab template switching

## 2026-02-24 (Phase 3.75 — tab UX polish)
- [feat] `TabBar.tsx`: click active tab now opens a dropdown with all templates + search box (replaces `↺` button); removed `PickerMode` dual-button pattern
- [feat] `useGraphStore.ts`: `refreshTab()` action reloads current tab's graph from server, disconnects WS, resets run/log/summary state
- [feat] `useGraphStore.ts`: `closeTab` now allows closing the last tab — auto-creates a blank tab afterwards
- [feat] `useGraphStore.ts`: `openTab("blank")` creates an empty tab (no server graph) with name "blank"
- [feat] `EditorToolbar.tsx`: added refresh icon button (↻) next to Save; renamed `+ New` to `+ Blank`
- [feat] `useGraphStore.ts`: `deleteGraph` on last tab now delegates to `closeTab` which auto-opens blank tab (removed special-case empty-state branch)
- [fix] `useGraphStore.ts`: `restoreTabs` skips `loadGraph` for blank tabs (empty `graphId`)
- [fix] `useGraphStore.ts`: `loadGraphList` fallback opens a blank tab when no `last_opened` graph exists
- [test] TypeScript clean, 341 backend tests passing

## 2025-02-25 (Phase 4 — markdown agent format)
- [feat] `src/dan/loader/flow_parser.py`: flow notation parser — parses `## Flow` lines into typed `FlowStatement` objects (chain, each, loop, if). Supports Unicode/ASCII arrows, port-specific wiring with consume-once semantics, pipe operators with keyword args, quoted condition strings, line continuations, comments, and `FlowParseError` diagnostics with line/position.

## 2026-02-24 (Phase 3.75 — shared template picker)
- [feat] `TabBar.tsx`: unified template picker dropdown shared by active-tab click (replace mode) and `+ New` button (new-tab mode); rendered via `createPortal` to avoid tab-strip overflow clipping
- [feat] `TabBar.tsx`: dropdown shows search box, top 5 most-frequent templates (tracked in `localStorage` via `dan_tpl_freq`), all templates section, and "Blank" option in new-tab mode
- [feat] `TabBar.tsx`: dropdown width matches the anchor element (tab or button), with 220px minimum
- [feat] `EditorToolbar.tsx`: renamed `+ Blank` to `+ Create` (creates a new named server-side graph — distinct from the tab-level template picker)
- [test] TypeScript clean, 341 backend tests passing
