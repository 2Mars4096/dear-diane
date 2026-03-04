# 19-2: Workflow Planner

**Parent:** [19-meta-orchestrator](19-meta-orchestrator.md)
**Status:** completed
**Goal:** Build an LLM-powered planner agent that receives a high-level user goal, discovers available tools/skills/past workflows, and produces a valid workflow graph — reusing existing workflows when possible, adapting them for new requirements, or generating from scratch when nothing fits.

## Existing Baseline

| Component | Location | What exists | Gap |
|---|---|---|---|
| `ExperienceIndex` (19-1) | `engine/experience.py` (planned) | RAG search over past workflow experiences | Planned; planner's primary discovery channel |
| Builder DSL | `builder/builder.py` | `workflow()`, `llm()`, `tool()`, `code()`, `>>`, `build()` → `Graph` | Programmatic graph construction; planner generates this code |
| `PATTERN_LIBRARY` | `server/graph_mutator.py` | `chain`, `review_loop`, `fan_out`, `rag_qa`, `data_analysis`, `data_ingest` | Composable building blocks; planner can reference these |
| `ToolRegistry` | `executors/tool.py` | `registered_ids()`, `register_builtin_tools()` | Tool discovery; planner queries available tools |
| `SKILL_LIBRARY` | `server/skill_library.py` | Named skills with descriptions, tags, content | Skill discovery; planner attaches relevant skills |
| GraphStore | `server/graph_store.py` | `list_graphs()`, `get_graph()`, `load_as_model()` | Raw graph access; planner can inspect existing workflows |
| Export as Python | `server/app.py` `/export/python` | Decompiles graph → builder DSL code | Planner can read existing workflows as code and modify |
| `GraphMutator` | `server/graph_mutator.py` | Structural mutations with validation | Adaptation path: mutate existing workflow instead of generating from scratch |
| `dan.llm_api_guide` | `docs/llm-api-guide.md` | LLM-facing reference for DAN's builder DSL, node types, edges, engine API | **Planner's instruction manual**: system prompt for the planning LLM |

## Tasks

- [x] 1. Discovery service
  - [x] 1-1. `DiscoveryService` class in new `meta/discovery.py`:
    - `discover_tools() -> list[ToolInfo]` — query `ToolRegistry.registered_ids()` and return structured tool descriptors (id, description, parameter schema). Source tool metadata from `TOOL_METADATA` in each tool module.
    - `discover_skills() -> list[SkillInfo]` — query `SKILL_LIBRARY` and return structured descriptors (name, description, tags, inject_as).
    - `discover_patterns() -> list[PatternInfo]` — query `PATTERN_LIBRARY` and return structured descriptors (name, description, required params).
    - `discover_workflows(query: str, top_k: int = 5) -> list[WorkflowMatch]` — query `ExperienceIndex.search_similar()` and return matched workflows with relevance scores and experience summaries.
  - [x] 1-2. `ToolInfo`, `SkillInfo`, `PatternInfo`, `WorkflowMatch` — lightweight Pydantic models for the planner's context.
  - [x] 1-3. Add reuse-fit scoring: compute a deterministic `reuse_fit_score` per `WorkflowMatch` (0-1) from semantic similarity + required tools/skills overlap + constraint coverage (output format, domain tags). Planner policy:
    - `reuse_fit_score >= 0.8` → prefer `REUSE`
    - `0.4 <= reuse_fit_score < 0.8` → prefer `ADAPT`
    - `< 0.4` → `GENERATE`

- [x] 2. Planning prompt construction
  - [x] 2-1. `PlanningPromptBuilder` class in `meta/planner.py`:
    - `build_system_prompt()` — base system prompt incorporating the DAN builder DSL reference (from `llm-api-guide.md` or a condensed version), available node types, and planning instructions.
    - `build_user_prompt(goal: str, discoveries: DiscoveryResult, error_context: str | None) -> str` — formats the user's goal, available tools/skills/patterns, similar past workflows, and optionally error context from a prior failed attempt.
  - [x] 2-2. The system prompt instructs the LLM to output one of three declarative actions (`PlanIR`):
    - `REUSE(workflow_id, input_mapping)` — use an existing workflow as-is (with input substitution)
    - `ADAPT(workflow_id, mutation_plan, input_mapping)` — modify an existing workflow with a `MutationPlan`
    - `GENERATE(spec)` — generate a structured workflow spec (node/edge/hyperedge declarations), not executable code
  - [x] 2-3. Include few-shot examples of each action type in the system prompt. Three examples (REUSE/paper_writing, ADAPT/equity_research+beamer, GENERATE/RAG QA) added to `PlanningPromptBuilder.SYSTEM_TEMPLATE`. Unit test `test_system_prompt_contains_few_shot_examples` validates presence.

- [x] 3. Planner agent
  - [x] 3-1. `WorkflowPlanner` class in `meta/planner.py`:
    - `plan(goal: str, error_context: str | None = None) -> PlanResult` — the main entry point:
      1. Call `DiscoveryService` to gather available tools, skills, patterns, and similar workflows
      2. Build planning prompt
      3. Call LLM (configurable model via `EngineConfig.planner_model`)
      4. Parse the LLM's output into a `PlanResult`
    - `PlanResult` — discriminated union:
      - `ReusePlan(workflow_id, input_mapping: dict)` — use existing workflow
      - `AdaptPlan(workflow_id, mutation_plan: MutationPlan, input_mapping: dict)` — adapt existing
      - `GeneratePlan(spec: dict, description: str)` — generate new workflow spec (`PlanIR`)
  - [x] 3-2. PlanIR compilation path (primary): when the planner outputs `GeneratePlan(spec)`, compile spec into a `Graph` via a deterministic compiler (or builder-DSL emitter + parser), then validate via `dan.validation.graph.validate_graph()`. If validation fails, feed errors back to the LLM for self-correction (max 3 retries).
  - [x] 3-3. Builder-code path: `GenerateCodePlan` action + `_execute_generate_code()` executes LLM-generated builder DSL code in `SandboxRunner` subprocess (30s timeout, memory limits). A harness wrapper serialises the compiled `Graph` to `_result.json`. Gated by `planner_allow_code_generation` config flag. 5 tests in `test_integration_llm.py::TestBuilderCodePath`.
  - [x] 3-4. Adaptation execution: when the planner outputs `AdaptPlan`, load the existing graph, apply `GraphMutator.apply(graph_dict, mutation_plan)` (where `graph_dict = graph.model_dump(mode=\"json\")`), validate the result, and rehydrate to `Graph`. If mutation fails, escalate to `GeneratePlan`.

- [x] 4. Plan validation and safety
  - [x] 4-1. Validate generated workflows before execution:
    - Graph validation (`validate_graph()`) — structural correctness
    - Tool availability check — all `tool_id` references resolve in `ToolRegistry`
    - Skill compatibility check — all hyperedge skills reference valid hooks
    - Cost estimation — rough token budget estimate based on node count and model assignments
  - [x] 4-2. `PlanReview` model: stores the validation results, estimated cost, and a confidence score (how closely the plan matches the user's goal, self-assessed by the planner). Returned alongside `PlanResult` so the controller (19-4) can decide whether to proceed or ask for human review.

- [x] 5. Configuration
  - [x] 5-1. `EngineConfig` fields:
    - `planner_model: str | None` — LLM model for the planner (defaults to engine default)
    - `planner_max_retries: int = 3` — self-correction attempts for invalid plans
    - `planner_temperature: float = 0.3` — low temperature for deterministic planning
    - `planner_discovery_top_k: int = 5` — number of similar workflows to retrieve
    - `planner_allow_code_generation: bool = False` — whether executable builder code output is allowed

- [x] 6. REST API
  - [x] 6-1. `POST /api/meta/plan` — body: `{"goal": "...", "error_context": null}`. Returns `PlanResult` + `PlanReview`.
  - [x] 6-2. `GET /api/meta/discover` — returns available tools, skills, patterns (for UI display).
  - [x] 6-3. `POST /api/meta/validate-plan` — validates a `PlanResult` without executing.

- [x] 7. Tests
  - [x] 7-1. Unit: `DiscoveryService` returns correct tool/skill/pattern descriptors.
  - [x] 7-2. Unit: `PlanningPromptBuilder` produces well-formed prompts with discoveries.
  - [x] 7-3. Unit: `WorkflowPlanner` with mocked LLM produces valid `PlanResult` for each action type (REUSE/ADAPT/GENERATE).
  - [x] 7-4. Unit: PlanIR compiler validates/rejects malformed specs. `TestCompileGenerateSpec` covers basic compilation, valid Graph output, all node types, edge name resolution, passthrough, source_id fallback, and rejection of unsupported types.
  - [x] 7-5. Integration: planner discovers existing paper-writing workflow and produces plan for a similar goal. (`test_reuse_plan_with_existing_workflow` + `test_adapt_plan_with_partial_match` in `test_integration_llm.py`, using real LLM.)
  - [x] 7-6. Integration: planner generates a new workflow from scratch when no similar past workflow exists. (`test_generate_plan_for_novel_goal` + `test_generate_produces_compilable_spec` in `test_integration_llm.py`, using real LLM.)

## Decisions

- **Planner outputs declarative `PlanIR` first.** Primary output is structured REUSE/ADAPT/GENERATE payloads compiled deterministically into a `Graph`. Builder DSL code generation is a fallback path only.
- **Three action modes (REUSE/ADAPT/GENERATE).** This mirrors the user's stated preference: try to reuse first, adapt if needed, generate as a last resort. The planner explicitly chooses the mode.
- **Executable code generation is opt-in and isolated.** If enabled, generated code must run out-of-process with strict containment controls; in-process `exec` is not the default or trusted path.
- **Self-correction on validation failure.** If the planner produces an invalid graph, the validation errors are fed back to the LLM (up to `planner_max_retries` times). This matches the pattern already used for output normalization in `LLMExecutor`.
- **The planner is itself an LLM call, not a DAN workflow.** It's a single LLM invocation with a rich system prompt and discovery context. In the future it could be promoted to a DAN workflow itself (recursive meta-orchestration), but starting simple is correct.

## Notes

- The quality of the planner depends heavily on the system prompt. The condensed builder DSL reference must be accurate, concise, and include enough examples. `llm-api-guide.md` is the starting point.
- Existing workflows (like the paper-writing workflow in `examples/`) serve as both training examples for the system prompt and retrieval targets for the experience index.
- The `/export/python` endpoint already decompiles graphs to builder DSL code — this is how the planner "reads" existing workflows to understand and adapt them.
