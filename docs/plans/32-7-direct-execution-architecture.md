# 32-7: Direct Execution Architecture

**Parent:** [32-workflow-optimization](32-workflow-optimization.md)
**Status:** completed
**Goal:** Eliminate the codegen→sandbox→validate roundtrip for complex tasks by building Graph objects directly in-process and handing them to the Engine, while preserving the option to materialize builder DSL scripts post-hoc for reuse and documentation.

## Problem

When a user asks DAN to do something complex — "Research the top 5 AI papers this week and write a summary" — the current flow involves two layers of indirection:

1. **Intent extraction**: LLM decomposes the goal into a `WorkflowIntent` (stages, types, config).
2. **Code generation**: `IntentCompiler.compile()` emits a Python builder DSL **string** (e.g. `wf = workflow("research"); step1 = wf.llm("gather", ...); ...`).
3. **Sandbox execution**: The code string is `exec()`'d in a `SandboxRunner` subprocess to produce a Graph JSON.
4. **Validation**: The Graph JSON is parsed and validated.
5. **Engine execution**: The validated `Graph` object is run by the Engine.

Steps 2–4 are the fragility zone:
- **Codegen failures** — Syntax errors, import errors, name errors in generated Python code. The sandbox subprocess adds latency (~1-3s) and a whole error class that has nothing to do with the user's task.
- **Unnecessary ceremony** — For most complex tasks the user doesn't care about having a reusable workflow artifact. They want the task done. Yet the system asks "Do you want me to build a workflow?" because it can only do complex work through the workflow-as-artifact path.
- **Latency** — The codegen→sandbox→validate roundtrip adds 2-5 seconds before any real work begins. For conversational use this feels sluggish.
- **Diagnosis complexity** — When codegen fails, the diagnosis loop (24-4) must parse subprocess error output, classify error types, attempt repairs, and potentially re-run the sandbox. This machinery exists solely because of the string-based codegen indirection.

Note: `ChatManager._exec_deterministic_builder_code()` already runs intent-compiled code in-process via `exec()` (no sandbox), which is a half-step toward this plan's goal. However it still operates on code *strings* — `IntentCompiler.compile()` emits a string, which `exec()` runs. The indirection through source code remains. This plan eliminates the code-string step entirely.

The Engine itself is solid. `Engine.run(graph)` handles scheduling, checkpointing, parallel fan-out, review loops, sub-graphs, events, and memory — all the hard stuff. The problem is upstream: turning intent into a Graph should not require emitting, sandboxing, and parsing source code.

## Design

### Core insight

The `IntentCompiler` already contains the full dispatch logic for every stage type — `_compile_transform`, `_compile_review_loop`, `_compile_fan_out`, etc. But these methods emit **code strings** that call the builder API. The same logic can call the builder API **directly**, producing a `Graph` object in-process with zero codegen.

### Architecture change

```
BEFORE (current):
  Goal → LLM intent extraction → IntentCompiler.compile() → code string
       → SandboxRunner.exec() → Graph JSON → validate → Graph → Engine.run()

AFTER (this plan):
  Goal → LLM intent extraction → IntentCompiler.build_graph() → Graph → Engine.run()
         ↓ (post-hoc, optional)
         graph_to_builder_code(graph) → script for reuse/docs
```

The codegen path (`compile()`) stays for backward compatibility and for cases where the user explicitly wants a script. But the **default path** for complex tasks becomes direct in-process Graph construction.

### What changes, what doesn't

| Layer | Changes? | Details |
|-------|----------|---------|
| Engine (`scheduler.py`) | **No** | `Engine.run(graph)` contract unchanged. Doesn't care how the Graph was built. |
| Graph model (`models/graph.py`) | **No** | Same `Graph` Pydantic model. |
| Builder DSL (`builder/`) | **No** | The builder API is called programmatically instead of via generated strings. No API changes. |
| IntentCompiler (`meta/intent_compiler.py`) | **Yes** | New `build_graph()` method — in-process Graph construction. |
| Planner (`meta/planner.py`) | **Yes** | New `execute_plan_direct()` path for intent-eligible plans. |
| Concierge runtime (`concierge/runtime.py`) | **Yes** | New inline execution path: goal → build_graph → Engine.run(). Routing heuristics (`_is_workflow_build_goal()`, `_should_execute_inline()`) also live here. |
| ChatManager (`server/chat_manager.py`) | **Yes** | Existing `_try_intent_then_codegen()` and `_exec_deterministic_builder_code()` replaced/simplified for the WORKFLOW_BUILD intent lane. |
| Post-hoc materializer (new) | **Yes** | Extends existing `builder/decompiler.py` (`decompile()`) with convenience-method-aware output. |

### Routing: when to use inline vs. workflow-build

The concierge currently treats all complex tasks as workflow-building problems. With this change, it distinguishes two modes:

| Signal | Route to |
|--------|----------|
| "Research X and summarize Y" | **Inline execution** — build graph in-process, run it, stream results |
| "Build me a pipeline for…" / "Create an automation that…" / "I need a workflow for…" | **Workflow build** — current path (build session, codegen, artifact) |
| "Add a review loop" / "Modify the workflow" | **Workflow build** — mutation path (existing) |
| Explicit `/build` command | **Workflow build** — force artifact path |

The distinction is based on **whether the user wants a reusable artifact** vs. **just wants the task done**. The default for natural-language complex tasks is inline execution.

### Post-hoc materialization

After inline execution completes, the concierge can optionally:
1. Persist the `Graph` JSON to the graph store (for experience/reuse).
2. Generate a readable builder DSL script via `graph_to_builder_code()` — the inverse of `IntentCompiler.compile()`.
3. Store the script as a workflow asset in memory for future reuse decisions.

This gives the user the best of both worlds: fast execution without ceremony, and a clean workflow artifact produced after the fact if the task is worth reusing.

## Existing Infrastructure

| Component | Location | Relevance |
|-----------|----------|-----------|
| IntentCompiler | `meta/intent_compiler.py` | Has `compile()` (string output) and `compile_composed()` (string output). Target for new `build_graph()` (Graph output). |
| IntentSchema | `meta/intent_schema.py` | `WorkflowIntent`, `StageIntent`, `StageType` — unchanged. |
| Builder DSL | `builder/builder.py` | `WorkflowBuilder`, `workflow()`, `chain()`, `review_loop()`, `map_reduce()`, `tool_chain()` — called programmatically. |
| Builder compiler | `builder/compiler.py` | `WorkflowCompiler.compile()` → `Graph`. Called via `wf.build()`. |
| SandboxRunner | `sandbox/runner.py` | Currently executes generated code. Bypassed by this plan. |
| Engine | `engine/scheduler.py` | `Engine.run(graph, inputs, ...)` → `RunResult`. Contract unchanged. |
| Planner | `meta/planner.py` | `WorkflowPlanner.plan()`, `execute_plan()`, `_execute_generate_code()`. New `execute_plan_direct()` sibling. |
| Intent extraction | `meta/intent_extraction.py` | Exports `INTENT_EXTRACTION_SYSTEM_PROMPT` and `build_intent_tool_schema()`. No standalone `extract_intent()` function — extraction is performed inline by callers (currently `ChatManager._try_intent_then_codegen()` and `WorkflowPlanner.plan()`). |
| ChatManager | `server/chat_manager.py` | `_try_intent_then_codegen()` — parallel intent+codegen pipeline for WORKFLOW_BUILD. `_exec_deterministic_builder_code()` — in-process `exec()` of intent-compiled code strings (half-step toward `build_graph()`). `_sandbox_exec_builder_code()` — subprocess sandbox for LLM-generated code. |
| Concierge runtime | `concierge/runtime.py` | `_execute_goal()`, `_should_use_goal_orchestrator()`, `_is_workflow_build_goal()` (on `Concierge` class, not `classifier.py`). Modified for inline path. |
| Concierge classifier | `concierge/classifier.py` | `IntentCategory.META_GOAL` / `WORKFLOW_BUILD` enum. Unchanged — routing decisions are in `runtime.py`. |
| MetaController | `meta/controller.py` | `run_session()` → plan → execute → diagnose loop. Inline path can bypass or complement this. |
| Build session | `concierge/build_session.py` | Multi-round DRAFTING → TESTING → COMPLETED loop. Only for explicit workflow-build path. |
| Graph store | `server/app.py` | `GraphStore` for persisting workflow artifacts. Used for post-hoc materialization. |
| Experience memory | `engine/memory_kernel.py` | `store_workflow_asset()`, `retrieve_by_task()`. Stores inline-executed graphs for future reuse. |
| Domain learning | `concierge/domain_learning.py` | `detect_domain()` for domain-aware pattern selection. Feeds into `build_graph()`. |
| Builder decompiler | `builder/decompiler.py` | `decompile(graph) -> str` — already produces executable builder DSL code from a `Graph`. Lossless round-trip. Foundation for convenience-method-aware `graph_to_builder_code()`. |
| Validation | `validation/graph.py` | `validate_graph()` — called by Engine.run() already. No change. |
| Plan scheduler | `engine/plan_scheduler.py` | `execute_plan_tasks()` — existing prototype of concierge-driven task DAG execution. |

## Tasks

### 1. IntentCompiler.build_graph() — in-process Graph construction

The core deliverable. Add a `build_graph()` method to `IntentCompiler` that takes a `WorkflowIntent` and returns a `Graph` object directly by calling the builder API in-process.

- [x] 1-1. **`build_graph(intent, *, domain=None) -> Graph`** — new public method on `IntentCompiler`. Constructs a `WorkflowBuilder`, dispatches each stage to a builder-call method, wires edges, calls `wf.build()`, returns the `Graph`. Same stage-type dispatch table as `compile()` but calls builder methods instead of emitting code strings.

- [x] 1-2. **Stage-type builder dispatchers** — one method per `StageType`:
  - `_build_transform(stage, wf)` → calls `wf.llm(name, prompt=...)`
  - `_build_review_loop(stage, wf)` → calls `wf.review_loop(writer_prompt=..., reviewer_prompt=..., ...)`
  - `_build_fan_out(stage, wf)` → calls `wf.for_each(name, parallelism=...)` context manager + inner `body.llm(...)`
  - `_build_rag_retrieval(stage, wf)` → calls `wf.rag(name, ...)` + `wf.llm(answer_name, ...)`
  - `_build_tool_call(stage, wf)` → calls `wf.tool(name, tool_id=...)`
  - `_build_code_execution(stage, wf)` → calls `wf.code(name, code=...)`
  - `_build_human_approval(stage, wf)` → calls `wf.human_in_the_loop(name, ...)`
  - `_build_conditional(stage, wf)` → calls `wf.branch(condition=..., then_prompt=..., else_prompt=...)`

- [x] 1-3. **Edge wiring** — sequential chaining between stages using `>>` operator on NodeRef objects returned by each builder call. Conditional stages wire both branches to the next stage (same logic as `compile()`'s `__both__` sentinel, but using direct `>>` calls).

- [x] 1-4. **`build_graph_composed(intent, constituent_patterns, *, domain=None) -> Graph`** — composed variant for multi-pattern intents. Mirrors `compile_composed()` but calls builder convenience methods (`wf.chain()`, `wf.review_loop()`, `wf.map_reduce()`) directly. Chains segments via `>>` between last NodeRef of segment N and first of segment N+1.

- [x] 1-5. **Domain-aware builder selection** — when `domain` is provided, prefer domain-preferred patterns (same `DOMAIN_PATTERN_PREFERENCES` dict already in `intent_compiler.py`). Pass through to `build_graph_composed()`.

- [x] 1-6. **Tests: single-pattern intents** — one test per `StageType`: construct a `WorkflowIntent`, call `build_graph()`, assert the returned `Graph` is valid (passes `validate_graph()`), has the expected number of nodes, node types, and edge topology.

- [x] 1-7. **Tests: multi-pattern composition** — 3-4 composition scenarios: `research_review` (chain + review_loop), `fan_out + chain`, `tool_chain + review`, `multi_source + analysis`. Verify valid Graph output and correct node/edge count.

- [x] 1-8. **Tests: parity with compile()** — for each stage type, verify that `build_graph(intent)` produces a structurally equivalent graph to `compile(intent)` → sandbox exec → parse. Same node types, same edge topology, same port connections. This proves the two paths are interchangeable.

- [x] 1-9. **Error handling** — when a stage type is unsupported or builder call fails, raise a typed `DirectBuildError` with stage name, stage type, and underlying error. The caller can then fall back to codegen.

### 2. Planner direct-build path

Wire the new `build_graph()` into the planner so that intent-eligible plans skip the codegen→sandbox roundtrip entirely.

- [x] 2-1. **`WorkflowPlanner.execute_plan_direct(plan, *, domain=None) -> dict`** — new method on `WorkflowPlanner`. When the plan is a `GenerateCodePlan` that was derived from an intent (has `WorkflowIntent` attached), calls `IntentCompiler.build_graph()` directly. Returns the same `{"workflow_id": ..., "graph_data": ..., "success": True}` dict as `execute_plan()`.

- [x] 2-2. **Intent attachment on GenerateCodePlan** — ensure that when the planner takes the intent path (intent extraction → compile), the `WorkflowIntent` object is attached to the `GenerateCodePlan` so `execute_plan_direct()` can access it. Add an optional `intent: WorkflowIntent | None` field to `GenerateCodePlan`.

- [x] 2-3. **Fallback to codegen** — when `build_graph()` raises `DirectBuildError`, `execute_plan_direct()` falls back to the existing `_execute_generate_code()` path (sandbox). Log the fallback reason for telemetry.

- [x] 2-4. **Planner routing decision** — `execute_plan()` checks: if plan has attached intent and `DAN_DIRECT_BUILD` is not disabled, call `execute_plan_direct()` first. On failure, fall back to codegen. This makes direct build the default while keeping codegen as a safety net.

- [x] 2-5. **Validation** — `execute_plan_direct()` runs `validate_codegen_output()` (or just `validate_graph()`) on the built Graph before returning, same as the codegen path. Validation errors trigger fallback to codegen.

- [x] 2-6. **Post-generation enrichment** — the existing post-generation enrichment logic (32-3: auto-wire retry, validation gates, feedback loops) must run on directly-built graphs the same way it runs on codegen-built graphs. Factor the enrichment into a shared `_enrich_graph()` call used by both paths.

- [x] 2-7. **Tests** — test `execute_plan_direct()` with a simple intent (linear chain), a composed intent (research_review), and a fallback scenario (malformed intent that triggers `DirectBuildError` → codegen).

### 3. Concierge inline execution path

Currently two parallel generation paths exist:
- **WORKFLOW_BUILD lane** — `WorkflowBuildHandler` → `ChatManager._try_intent_then_codegen()` → intent extraction + `IntentCompiler.compile()` → `_exec_deterministic_builder_code()` (in-process `exec()`) or `_sandbox_exec_builder_code()` (subprocess). Produces a workflow artifact.
- **META_GOAL lane** — `_execute_goal()` → `MetaController.run_session()` → `WorkflowPlanner.plan()` → `execute_plan()` → codegen → sandbox → Engine.run(). Also produces a workflow artifact.

Both paths go through code strings. The inline execution path replaces both for eligible goals.

- [x] 3-1. **`_execute_goal_inline()` method** — new async generator on `Concierge` that:
  1. Extracts intent from goal description using the `INTENT_EXTRACTION_SYSTEM_PROMPT` and `build_intent_tool_schema()` from `meta/intent_extraction.py` (same LLM call pattern as `ChatManager._try_intent_then_codegen()`, factored into a shared helper).
  2. Calls `IntentCompiler.build_graph(intent, domain=detected_domain)` to get a `Graph` in-process.
  3. Calls `Engine.run(graph, inputs=..., workflow_id=..., session_id=...)` to execute.
  4. Yields progress events as the Engine runs (node started, node completed, etc.).
  5. On completion, yields a summary event with the final output.
  6. On failure, yields an error event with diagnosis context.

- [x] 3-2. **Factor intent extraction into shared helper** — extract the LLM call pattern from `ChatManager._try_intent_then_codegen()` into a reusable `async def extract_workflow_intent(llm_provider, goal_text, model=...) -> WorkflowIntent | None` function in `meta/intent_extraction.py`. Uses the existing `INTENT_EXTRACTION_SYSTEM_PROMPT` and `build_intent_tool_schema()`. Both the concierge inline path and the ChatManager workflow-build path call this helper instead of duplicating the extraction logic.

- [x] 3-3. **Engine access** — the concierge needs access to an `Engine` instance. Currently, the `MetaController` wraps engine access behind `_run_workflow`. For the inline path, the concierge needs either:
  - Direct access to `Engine` (preferred — cleaner, no MetaController indirection).
  - A new `_run_graph_inline` callable injected at construction time.
  Wire this through `make_concierge()` and `app.py` lifespan.

- [x] 3-4. **Event forwarding** — Engine emits `EngineEvent` objects (NODE_STARTED, NODE_COMPLETED, RUN_COMPLETED, etc.). Map these to `ChatStreamEvent` objects that the concierge can yield to the surface. Reuse existing event mapping logic from `ProgressReporter` where possible.

- [x] 3-5. **Wire into `_execute_goal()` routing** — modify `_execute_goal()` to check: if the goal is eligible for inline execution (see task 4), call `_execute_goal_inline()` instead of the MetaController path. Falls back to MetaController on `DirectBuildError`.

- [x] 3-6. **Goal lifecycle** — inline-executed goals still follow the `ConciergeGoal` lifecycle: created → active → completed/failed. Update `goal.status`, `goal.iteration`, `goal.workflow_id` after inline execution. Store the graph_id for future reuse.

- [x] 3-7. **Memory extraction** — after successful inline execution, run the existing memory extraction pipeline (`_extract_memories()`) on the goal/result. The inline path should learn just like the MetaController path.

- [x] 3-8. **Build session bypass** — inline execution does NOT create a `BuildSession`. The build session state machine (DRAFTING → VALIDATING → TESTING → ...) is for the explicit workflow-build ceremony. Inline execution is fire-and-forget from the user's perspective.

- [x] 3-9. **ChatManager simplification** — update `ChatManager._try_intent_then_codegen()` to call `IntentCompiler.build_graph()` directly instead of `compile()` + `_exec_deterministic_builder_code()`. This unifies the WORKFLOW_BUILD lane with the same direct-build path. `_exec_deterministic_builder_code()` becomes dead code (keep as fallback initially, remove in cleanup).

- [x] 3-10. **Tests** — mock Engine, verify that a simple goal ("summarize this document") goes through intent extraction → build_graph → Engine.run() without touching MetaController or SandboxRunner. Verify events are yielded. Verify fallback to MetaController on DirectBuildError.

### 4. Routing logic — inline vs. workflow-build

Define clear rules for when the concierge uses inline execution vs. the existing workflow-build/MetaController path.

- [x] 4-1. **`_should_execute_inline()` method** — new method on `Concierge`. Returns `True` when:
  - Classification intent is `META_GOAL` (complex task) but not explicitly about building a workflow artifact.
  - Goal description does NOT contain explicit workflow/pipeline/automation construction language ("build me a pipeline", "create a workflow", "automate this process").
  - No active build session for this goal.
  - `DAN_DIRECT_BUILD` env var is not set to `"off"`.
  Returns `False` when:
  - `_is_workflow_build_goal()` returns True (explicit build language).
  - User used `/build` command.
  - Classification intent is `WORKFLOW_BUILD`.
  - Goal already has a `build_session_id` (continuation of existing build).

- [x] 4-2. **Update `_is_workflow_build_goal()`** — currently checks for "build", "create", "make", "automate" keywords broadly. Narrow it to check for workflow/pipeline construction intent specifically: require both an action verb AND a workflow/pipeline/automation object. "Create a summary" → inline. "Create a workflow that summarizes" → workflow build.

- [x] 4-3. **Update `_detect_goal()` routing** — when `META_GOAL` is detected and `_should_execute_inline()` returns True, create the `ConciergeGoal` with `goal.context["execution_mode"] = "inline"` so downstream routing can check it without re-evaluating.

- [x] 4-4. **`/build` command override** — add a `/build` slash command that forces the workflow-build ceremony for the current goal. If the user wants a reusable artifact, they can opt in explicitly.

- [x] 4-5. **Autonomy interaction** — inline execution respects the autonomy level. In `interactive` mode: show the decomposed stages and ask "Shall I proceed?" before running. In `supervised` mode: show stages, proceed automatically, check in after completion. In `autonomous` mode: just do it.

- [x] 4-6. **Tests** — test routing for various messages:
  - "Research X and summarize" → inline
  - "Build me a research pipeline" → workflow build
  - "Create a workflow for daily reports" → workflow build
  - "Analyze these documents" → inline
  - "Automate the quarterly review process" → workflow build
  - "What's the stock price of AAPL" → direct task (not inline, not workflow build)
  - "/build Research X" → workflow build (command override)

### 5. Post-hoc Graph materialization

After inline execution, optionally generate a builder DSL script from the executed Graph — the inverse of `compile()`. This gives users a clean, editable workflow script they can save, modify, and reuse.

- [x] 5-1. **`graph_to_builder_code(graph) -> str`** — wrapper/extension of the existing `builder/decompiler.py` → `decompile(graph)` function. The existing decompiler already produces lossless executable builder DSL code. This task adds a convenience-method-aware mode: detect common patterns in the graph topology and emit `wf.chain()`, `wf.review_loop()`, `wf.map_reduce()` instead of node-by-node code. Can live in `builder/decompiler.py` as a new mode or in `meta/graph_materializer.py` wrapping the decompiler.

- [x] 5-2. **Node type mapping** — map each node type in the graph back to the appropriate builder call: handled by existing `decompile()` which already maps all node types to their builder calls losslessly.

- [x] 5-3. **Convenience method detection** — when the graph topology matches a known pattern (linear chain → `wf.chain(...)`, writer + reviewer loop → `wf.review_loop(...)`), annotate the decompiled output with comments indicating the detected pattern and equivalent convenience method. Annotations preserve the lossless decompiler output for exact round-trips.

- [x] 5-4. **Edge reconstruction** — handled by existing `decompile()` which emits `>>` for sequential default edges and explicit port connections for non-default wiring. `materialize_graph()` wraps this with optional graph store persistence.

- [x] 5-5. **Round-trip test** — for each graph type: `build() → Graph → graph_to_builder_code() → exec() → Graph₂`. Verify `Graph` and `Graph₂` are structurally equivalent (same nodes, edges, ports). 23 tests in `tests/test_meta/test_graph_materializer.py`.

- [x] 5-6. **Post-execution materialization hook** — in `_execute_goal_inline()`, after successful Engine execution:
  - If the graph has 3+ nodes, generate the builder script via `graph_to_builder_code()`.
  - Persist the graph JSON and the generated script to the graph store.
  - Store as a workflow asset in memory for future reuse decisions.
  - Log with telemetry: `metadata.materialized: true, metadata.node_count: N`.

- [x] 5-7. **User-facing recap** — optionally include a condensed version of the materialized script in the completion message. Controlled by autonomy level: `autonomous` mode skips it, `interactive`/`supervised` mode includes a brief "Here's the workflow I ran:" summary.

### 6. Telemetry and observability

Track inline execution separately from codegen execution for quality comparison.

- [x] 6-1. **Telemetry fields** — add `metadata.execution_path: "inline" | "codegen" | "codegen_fallback"` to `chat_turn` telemetry events. Track which path was used and whether inline fell back to codegen.

- [x] 6-2. **Timing comparison** — log `metadata.graph_build_ms` (time to construct Graph in-process) alongside existing `metadata.codegen_ms` (time for codegen + sandbox). Enables measuring the latency improvement.

- [x] 6-3. **Success rate tracking** — track `metadata.inline_build_success: bool` and `metadata.inline_execution_success: bool` separately. This lets the eval harness (Phase 23) measure inline vs. codegen reliability.

- [x] 6-4. **Eval harness integration** — update the eval test harness (`tests/eval/`) to support running prompts through the inline path. Add a `--execution-path inline|codegen|auto` flag to the eval runner.

### 7. Error handling and fallback

Robust fallback when inline execution fails at any stage.

- [x] 7-1. **`DirectBuildError` exception class** — typed error with `stage_name`, `stage_type`, `reason`, and `underlying_error` fields. Raised by `build_graph()` when in-process construction fails.

- [x] 7-2. **Intent extraction failure** — if the LLM fails to extract a valid `WorkflowIntent`, fall back to the codegen path (MetaController). Do not attempt inline execution with a malformed intent.

- [x] 7-3. **Build failure fallback** — if `build_graph()` raises `DirectBuildError`, `_execute_goal_inline()` falls back to the MetaController path and logs the failure reason. The user sees no interruption — just slightly more latency.

- [x] 7-4. **Engine execution failure** — if `Engine.run()` returns `success=False`, the inline path uses the same diagnosis/repair logic as the MetaController path: extract errors, classify severity, attempt repair. For inline goals, repair means re-running with modified prompts/config, not rebuilding the graph from scratch.

- [x] 7-5. **Partial result preservation** — when inline execution fails mid-graph, completed node outputs are preserved. The failure message includes what was completed and what remains.

- [x] 7-6. **Tests** — test each failure mode: malformed intent → codegen fallback, build_graph failure → codegen fallback, Engine failure → diagnosis, partial completion → preserved outputs.

### 8. Configuration

- [x] 8-1. **`DAN_DIRECT_BUILD` env var** — controls the default execution path:
  - `"on"` (default) — inline execution for eligible goals.
  - `"off"` — always use codegen/MetaController path.
  - `"only"` — never fall back to codegen (for testing).

- [x] 8-2. **`DAN_MATERIALIZE_THRESHOLD` env var** — minimum node count for post-hoc materialization. Default: `3`. Graphs with fewer nodes aren't worth saving as workflow artifacts.

- [x] 8-3. **Behavior store integration** — register `execution/direct_build_enabled` and `execution/materialize_threshold` as adaptable parameters in the `BehaviorStore`. This allows the self-adaptive system (31-22) to tune these based on observed success rates.

### 9. Documentation

- [x] 9-1. **Update `docs/architecture.md`** — add a section on the direct execution architecture. Explain the two paths (inline vs. codegen), when each is used, and how they relate to the Engine.

- [x] 9-2. **Update `docs/llm-api-guide.md`** — document `IntentCompiler.build_graph()` alongside `compile()`. Explain that `build_graph()` is the preferred path for programmatic Graph construction from intents.

- [x] 9-3. **Update `docs/development-plan.md`** — note the architectural shift in the Phase 22 section.

- [x] 9-4. **Changelog entries** — one entry per shipped task group.

## Files

| File | Action | Details |
|------|--------|---------|
| `src/dan/meta/intent_compiler.py` | Modify | Add `build_graph()`, `build_graph_composed()`, `_build_*()` stage dispatchers, `DirectBuildError` |
| `src/dan/meta/intent_extraction.py` | Modify | Add shared `extract_workflow_intent()` async helper function |
| `src/dan/meta/planner.py` | Modify | Add `execute_plan_direct()`, `_enrich_generated_graph()` shared enrichment, intent attachment on `GenerateCodePlan` |
| `src/dan/meta/graph_materializer.py` | Create (or extend `builder/decompiler.py`) | Convenience-method-aware `graph_to_builder_code()` wrapping existing `decompile()` |
| `src/dan/server/concierge/runtime.py` | Modify | Add `_execute_goal_inline()`, `_should_execute_inline()`, update `_execute_goal()` routing, narrow `_is_workflow_build_goal()`, Engine access wiring |
| `src/dan/server/chat_manager.py` | Modify | Simplify `_try_intent_then_codegen()` to use `build_graph()` directly; use shared `extract_workflow_intent()` |
| `src/dan/server/app.py` | Modify | Wire Engine instance into concierge construction |
| `tests/test_meta/test_intent_compiler_build.py` | Create | Tests for `build_graph()`, parity with `compile()`, all stage types |
| `tests/test_meta/test_graph_materializer.py` | Create | Tests for `graph_to_builder_code()`, round-trip, convenience detection |
| `tests/test_concierge/test_inline_execution.py` | Create | Tests for inline execution routing, fallback, event forwarding |
| `tests/eval/runner.py` | Modify | Add `--execution-path` flag for eval harness |
| `docs/architecture.md` | Modify | Direct execution architecture section |
| `docs/llm-api-guide.md` | Modify | `build_graph()` documentation |
| `docs/development-plan.md` | Modify | Phase 22 update |

## Dependencies / Sequencing

```
Task 1 (IntentCompiler.build_graph)  ← foundational, start here
  ├→ Task 2 (Planner direct-build path)  ← needs build_graph()
  │    └→ Task 7 (Error handling / fallback)  ← needs planner routing
  ├→ Task 5 (Post-hoc materialization)  ← needs build_graph() to test round-trip
  └→ Task 3 (Concierge inline execution)  ← needs build_graph()
       ├── 3-2 (Factor intent extraction)  ← shared prerequisite for 3-1 and 3-9
       ├── 3-3 (Engine access)  ← wiring prerequisite for 3-1
       ├── 3-9 (ChatManager simplification)  ← needs build_graph() + 3-2
       └→ Task 4 (Routing logic)  ← needs inline execution path to exist
            └→ Task 6 (Telemetry)  ← needs both paths wired

Task 8 (Configuration)  ← can start after Task 2, before Task 3
Task 9 (Documentation)  ← after all implementation tasks
```

Tasks 2, 3, and 5 can start in parallel after Task 1. Task 3-2 (factor intent extraction) and 3-9 (ChatManager simplification) can progress in parallel with 3-1 once build_graph() exists. Task 4 depends on Task 3. Tasks 6-8 can interleave. Task 9 is last.

## Success Criteria

- [ ] `IntentCompiler.build_graph()` produces valid graphs for all 8 `StageType` values (transform, review_loop, fan_out, rag_retrieval, tool_call, code_execution, human_approval, conditional).
- [ ] `build_graph()` output is structurally equivalent to `compile()` → sandbox exec for the same `WorkflowIntent` input (parity tests pass).
- [ ] The concierge's default path for natural-language complex tasks ("Research X and summarize Y") uses inline execution — no sandbox subprocess, no codegen string.
- [ ] Explicit workflow-build requests ("Build me a pipeline for…") still use the existing build-session/codegen path.
- [ ] Inline execution latency (goal received → first Engine node starts) is measurably lower than codegen path (target: < 2s for graph construction vs. 3-5s for codegen+sandbox).
- [ ] Fallback from inline → codegen is seamless: if `build_graph()` fails, the user sees the same result they would have with the codegen path, just slightly slower.
- [ ] `graph_to_builder_code()` round-trips: intent → build_graph → graph_to_builder_code → exec → Graph₂ produces structurally equivalent graphs.
- [ ] Post-hoc materialized scripts are stored in the experience/memory system for future reuse decisions.
- [ ] All existing tests pass — no regressions in the codegen path, builder DSL, or Engine execution.
- [ ] Eval harness (Phase 23) can run the same prompt battery through both inline and codegen paths for comparison.

## Decisions

- (filled in during execution)

## Notes

- The codegen path (`IntentCompiler.compile()`, `SandboxRunner`, `_execute_generate_code()`) is NOT removed. It stays as a fallback and for cases where the user explicitly asks for a workflow script. The inline path is an alternative default, not a replacement.
- `build_graph()` calls the builder API (`wf.llm()`, `wf.chain()`, etc.) which goes through the same `WorkflowCompiler.compile()` → validation pipeline as hand-written builder code. The graph output is identical — just produced without the code-string indirection.
- The Engine doesn't know or care whether a Graph came from inline build, codegen, loaded JSON, or the visual editor. Its contract is `Graph in → RunResult out`.
- Post-hoc materialization via `graph_to_builder_code()` is intentionally a separate concern from execution. The execution path should be fast and simple; materialization is an optional post-processing step.
- `builder/decompiler.py` already has `decompile(graph) -> str` which produces lossless executable builder DSL code. `graph_to_builder_code()` extends this with convenience-method awareness — detecting chain/review_loop/map_reduce patterns and emitting the higher-level API calls for readability.
- Inline execution does NOT create a `BuildSession`. The build session state machine exists for the iterative build→test→modify ceremony. Inline execution is closer to "just run this task" — one-shot, no iteration ceremony unless the Engine itself encounters errors.
- The `DAN_DIRECT_BUILD` env var follows the existing pattern (`DAN_LLM_FIRST_CHAT`, `DAN_ENABLE_TIER_POLICY`, etc.) for feature rollout with kill switch.
- This plan does not change how the Engine executes graphs. All Engine capabilities (checkpointing, parallelism, review loops, sub-graphs, memory, events) are preserved because we're still feeding it a proper `Graph` object.
- The `plan_scheduler.py` → `execute_plan_tasks()` function is an earlier prototype of concierge-driven execution. The inline path in this plan is more integrated: it uses the full Engine rather than dispatching individual tasks through the concierge's chat interface.
- **Two parallel generation paths currently exist**: `ChatManager._try_intent_then_codegen()` (WORKFLOW_BUILD lane, uses `_exec_deterministic_builder_code()` for intent-compiled code) and `MetaController.run_session()` → `WorkflowPlanner.execute_plan()` (META_GOAL lane, uses `SandboxRunner`). Both produce code strings. `build_graph()` eliminates the string indirection for both. Over time this plan converges the two paths: `ChatManager._try_intent_then_codegen()` can call `build_graph()` directly instead of `compile()` + `exec()`, and the planner's `execute_plan_direct()` does the same.
- `ConciergeGoal.context` is an untyped `dict[str, Any]`. No schema changes in `models.py` are needed — `execution_mode` is stored as a dict key like other context fields (`build_session_id`, `plan_action`, etc.).
- Intent extraction is not a standalone function. `meta/intent_extraction.py` provides `INTENT_EXTRACTION_SYSTEM_PROMPT` and `build_intent_tool_schema()`. The actual LLM call is done by callers (`ChatManager._try_intent_then_codegen()` and `WorkflowPlanner.plan()`). The inline execution path should factor the extraction logic into a reusable async helper (e.g. `extract_workflow_intent(llm_complete, goal_text) -> WorkflowIntent | None`) to avoid duplicating LLM call patterns across ChatManager, Planner, and Concierge.
