# 17-2: Reflection Node (Tier 2)

**Parent:** [17-self-evolving-orchestrator](17-self-evolving-orchestrator.md)
**Status:** completed
**Goal:** Add a `ReflectionNode` that post-processes failed or degraded runs, uses LLM reasoning to distill raw errors into structured causal principles ("when X happens, avoid Y because Z"), and persists them as reusable memory entries for future retrieval and Tier 3 rule generation.

## Existing Baseline

| Component | Location | What exists | Gap |
|---|---|---|---|
| `ErrorRecord` (17-1) | `engine/error_memory.py` (planned) | Structured error records with category, input snapshot, upstream context | Raw errors; no causal analysis or abstraction |
| `ErrorMemoryIndex` (17-1) | `engine/error_memory.py` (planned) | RAG index of past errors, `query_similar()` | Stores verbatim errors, not distilled principles |
| `RunStore` | `server/run_store.py` | Full event log (`events.jsonl`) + run summary (`.json`) with `errors`, `node_statuses`, token/cost metrics | Data source for reflection; no analysis pipeline |
| `MemoryStore` | `engine/memory_store.py` | Cross-run KV persistence with GLOBAL/WORKFLOW/SESSION scopes, atomic writes | **Tier 2 storage target** for principles |
| `ShortTermMemory` | `engine/memory_pipeline.py` | Buffer with compaction strategies (sliding_window, keep_last, diff_based, summarize) | Available for principle buffering before consolidation |
| `ConsolidationPipeline` | `engine/memory_pipeline.py` | Short-term → long-term transfer with configurable thresholds | Available for principle promotion |
| `LLMExecutor` | `executors/llm.py` | LLM call with prompt construction, output normalization, retry | **Pattern**: reflection uses the same call-and-normalize cycle |
| `NodeBase` | `models/nodes.py` | Base for all 16+ node types, registered in `NodeTypeRegistry` | `ReflectionNode` extends this |
| `ExecutorRegistry` | `engine/executor.py` | Maps `node_type` → executor instance | `ReflectionExecutor` registers here |
| `EngineEvent` / `EventType` | `engine/events.py` | 26 typed events | May need new types for reflection lifecycle |
| `ErrorContextProvider` (17-1) | `engine/error_memory.py` (planned) | Retrieves and formats past errors for prompt injection | Tier 2 extends this to also retrieve principles alongside raw errors |

## Tasks

- [x] 1. `ReflectionNode` model
  - [x] 1-1. Define `ReflectionNode(NodeBase)` in `models/nodes.py`: `node_type: Literal["reflection"] = "reflection"`, `reflection_prompt: str = ""` (system prompt guiding the LLM's analysis), `reflection_model: str | None = None` (defaults to engine default), `source: Literal["last_run", "last_n_runs", "error_index"] = "last_run"` (what to reflect on), `source_config: dict = {}` (e.g., `{"n": 5}` for last_n_runs, `{"query": "timeout errors"}` for error_index), `output_format: Literal["principles", "rules", "summary"] = "principles"`, `max_principles: int = 10`, `min_confidence: float = 0.3` (filter low-confidence output), `dedup_strategy: Literal["embedding_similarity", "exact_key", "none"] = "embedding_similarity"`.
  - [x] 1-2. Define `CausalPrinciple` model in `engine/error_memory.py`: `id: str` (uuid), `condition: str` (when this applies — e.g., "CRSP data loader receives dates before 1990"), `action: str` (what to do/avoid — e.g., "pre-filter input dates to avoid merge_asof tolerance errors"), `reason: str` (why — e.g., "merge_asof with DateOffset tolerance fails on pre-1990 data due to calendar boundaries"), `source_run_ids: list[str]`, `source_node_ids: list[str]`, `confidence: float` (0–1, LLM self-assessed), `created_at: float`, `updated_at: float`, `tags: list[str]` (for hyperedge attachment targeting in Tier 3), `workflow_id: str`.
  - [x] 1-3. Register `ReflectionNode` in `NodeTypeRegistry` **and** add it to the discriminated `Node` union in `models/graph.py` so `Graph.model_validate()` can deserialize it. Add to `NODE_TYPE_NAMES` / `NODE_DESCRIPTIONS` in `editor/src/types/graph.ts`.

- [x] 2. `ReflectionExecutor`
  - [x] 2-1. Implement `ReflectionExecutor` in new `executors/reflection.py` with `execute(node, inputs, context) -> NodeResult`. Flow: (a) gather source data based on `node.source`; (b) build reflection prompt with structured error context; (c) call LLM via provider registry; (d) parse output into `list[CausalPrinciple]` using output normalization; (e) filter by `min_confidence`; (f) deduplicate against existing principles; (g) persist to `PrincipleStore`; (h) return principles as node output.
  - [x] 2-2. Implement source data gathering methods:
    - `_gather_last_run(context)` — load `RunStore` summary + events for the most recent run of this workflow. Extract `ErrorRecord` list and successful node outputs for contrast.
    - `_gather_last_n_runs(context, n)` — load summaries for last N runs; identify recurring error patterns (same `node_id` + similar error message across runs).
    - `_gather_from_error_index(context, query)` — query `ErrorMemoryIndex` with a semantic search; return top-K similar errors across all past runs.
    Each method returns a structured text summary suitable for LLM consumption, truncated to a configurable max tokens.
  - [x] 2-3. Build the default reflection prompt template. Requirements: instruct the LLM to analyze the error patterns; identify root causes (not symptoms); produce structured principles with condition/action/reason/confidence/tags; include 3 few-shot examples of good vs. bad principles. Output schema enforced via JSON Schema + `OutputNormalizer` (same mechanism used by `LLMOperator.output_json_schema`) — JSON array of `{condition, action, reason, confidence, tags}`.
  - [x] 2-4. Implement deduplication logic:
    - `embedding_similarity`: embed both new and existing principles; merge if cosine similarity > 0.9. On merge: union `source_run_ids`, keep higher `confidence`, update `updated_at`.
    - `exact_key`: merge if `(condition, action)` pair is identical (case-normalized).
    - `none`: no dedup (useful for debugging).
  - [x] 2-5. Register `ReflectionExecutor` in scheduler `_register_defaults()` (reflection node type).

- [x] 3. Principle storage and retrieval
  - [x] 3-1. Define `PrincipleStore` helper in `engine/error_memory.py`: wraps `MemoryStore` with principle-specific API. Storage layout: key = `principle:{principle_id}`, scope = `WORKFLOW`, value = serialized `CausalPrinciple` dict.
  - [x] 3-2. Implement `PrincipleStore` methods: `store_principles(workflow_id, principles: list[CausalPrinciple])`, `load_principles(workflow_id, tags: list[str] | None = None, min_confidence: float = 0.0) -> list[CausalPrinciple]`, `delete_principle(workflow_id, principle_id)`, `expire_principles(workflow_id, max_age_days: int)`.
  - [x] 3-3. Wire principle retrieval into Tier 1's `ErrorContextProvider`: alongside raw error retrieval, load relevant principles from `PrincipleStore` and append a "Learned Principles" section to the prompt context. Principles are formatted as: `"- When {condition}: {action} (confidence: {confidence:.0%})"`. Limited to top-N by confidence, within the `error_memory_max_tokens` budget.
  - [ ] 3-4. Add principle compaction (deferred — requires `ConsolidationPipeline` adapter): when principle count per workflow exceeds a threshold (configurable, default 50), invoke `ConsolidationPipeline` to merge similar principles and drop those below `min_confidence`. *(deferred — requires ConsolidationPipeline adapter; Phase 10 stretch)*

- [x] 4. Reflection scheduling
  - [x] 4-1. Add `EngineConfig` field: `reflection_trigger: Literal["on_failure", "on_every_run", "manual", "disabled"] = "disabled"`. When not `disabled`, `RunManager` schedules a reflection after the main run completes.
  - [x] 4-2. Implement trigger logic in `RunManager._enrich_and_persist()`: after error capture and indexing, check trigger condition:
    - `on_failure`: trigger if `record.status == "failed"` or `record.result.errors` is non-empty.
    - `on_every_run`: always trigger.
    - `manual`: never auto-trigger; only via API or explicit `ReflectionNode` in graph.
  - [x] 4-3. Implement `RunManager._schedule_reflection(record)`: creates a mini-workflow (single `ReflectionNode` with `source="last_run"`) and schedules it as a background run managed by `RunManager` (via `asyncio.create_task(...)` path) so it is visible in run history/events. Use a canonical run ID prefix (`run_id=f"reflection-{record.run_id}"`) to clearly mark it as a system-generated reflection run.
  - [x] 4-4. Guard against infinite recursion: reflection runs (identified by the `"reflection-"` prefix on their `run_id`) never trigger further reflections regardless of trigger setting.

- [ ] 5. (Deferred to Phase 11) Cross-workflow principle sharing
  - [ ] 5-1. Keep Tier 2 in Phase 9D workflow-scoped (`PrincipleStore` keyed by workflow). *(deferred — Phase 11 scope; see 19-1)*
  - [ ] 5-2. Move global principle sharing design (`scope=GLOBAL`, cross-workflow retrieval, global endpoints) to [19-1-workflow-experience-memory](19-1-workflow-experience-memory.md). *(deferred — Phase 11 scope; see 19-1)*
  - [ ] 5-3. Add explicit migration guidance in Phase 11 for promotion from workflow-scoped to globally-shareable principles. *(deferred — Phase 11 scope; migration guidance to be written when cross-workflow landing)*

- [x] 6. Graduated repair classification (extends Tier 2 reflection output)
  - [x] 6-1. Add `repair_level: Literal["retry", "prompt_fix", "parameter_fix", "structural_fix", "redesign"] = "prompt_fix"` field to `CausalPrinciple`. The reflection LLM classifies each principle by the severity of repair needed:
    - `retry` — transient error, retry policy sufficient (no principle needed).
    - `prompt_fix` — LLM needs different instructions (existing Tier 3 handles this).
    - `parameter_fix` — node needs different model, temperature, tool config, or timeout (level 2 repair, extends Tier 3).
    - `structural_fix` — workflow topology needs changes: add/remove/rewire nodes (signal to future meta-orchestrator).
    - `redesign` — fundamental approach is wrong, workflow should be rebuilt (signal to future meta-orchestrator).
  - [x] 6-2. Extend the default reflection prompt template: add few-shot examples for each `repair_level`. Instruct the LLM to classify based on whether the fix can be achieved by changing prompts alone, changing parameters, or changing the graph structure.
  - [x] 6-3. Add `suggested_parameter_changes: dict[str, Any] = {}` field to `CausalPrinciple` for `parameter_fix` level. Example: `{"model": "gpt-4", "temperature": 0.2}` or `{"tool": "web_search", "timeout": 60}`. The reflection LLM fills this when it identifies a parameter mismatch as the root cause. These suggestions are consumed by 17-3's runtime graph mutation path (not hyperedge conversion).
  - [x] 6-4. Add `structural_description: str = ""` field to `CausalPrinciple` for `structural_fix` and `redesign` levels. Free-text description of what structural changes are needed (e.g., "add a data validation node before the API call"). This is consumed by the future meta-orchestrator planner.
  - [x] 6-5. Tests: verify reflection output includes `repair_level`, verify classification heuristics in few-shot examples produce correct levels, verify `suggested_parameter_changes` is populated for parameter_fix principles.

- [ ] 7. Authoring surfaces *(deferred — authoring surfaces not yet prioritized; Phase 10 stretch)*
  - [x] 7-1. Builder API: `wf.reflection(name, source="last_run", reflection_model=None, max_principles=10, ...)` → creates `ReflectionNode` with configured source and output format. *(Implemented 2026-03-20 via Plan 40 builder/runtime parity.)*
  - [x] 7-2. Markdown format: `type: reflection` agent file with optional `## Source` section for source config and `## Reflection Prompt` section for custom system prompt. *(Implemented 2026-03-20 for current frontmatter/body contract; richer `## Source` section syntax remains optional polish.)*
  - [x] 7-3. Editor integration: `ReflectionNode` in palette under a new "Learning" or "Advanced" category. Config panel shows source selector, model override, max_principles slider. Output preview shows generated principles in a table. *(Partially implemented 2026-03-20: palette entry and dedicated config fields landed; richer output preview remains open.)*

- [x] 8. Testing
  - [x] 8-1. Unit tests: `CausalPrinciple` model validation, `ReflectionExecutor` prompt construction, source gathering methods (mock `RunStore`/`ErrorMemoryIndex`), deduplication logic.
  - [x] 8-2. Unit tests: `PrincipleStore` CRUD, `expire_principles`, compaction trigger.
  - [ ] 8-3. Integration test: failed run → reflection triggered → principles stored in `PrincipleStore` → `ErrorContextProvider` retrieves both errors and principles for next run's prompt. *(deferred — requires full engine execution with real LLM and multi-run orchestration)*
  - [x] 8-4. Quality test: verify LLM-generated principles are parseable JSON, have non-empty condition/action/reason fields, and confidence is in [0, 1]. Use a mock LLM with known output for deterministic testing.

## Primary Files

| File | Changes |
|------|---------|
| `models/nodes.py` | `ReflectionNode` model |
| `models/graph.py` | Add `ReflectionNode` to discriminated `Node` union |
| `registry.py` | Register `reflection` type for discovery |
| `executors/reflection.py` (new) | `ReflectionExecutor` |
| `executors/__init__.py` | Register `ReflectionExecutor` |
| `engine/error_memory.py` | `CausalPrinciple`, `PrincipleStore` (extends 17-1's module) |
| `server/run_manager.py` | Reflection trigger logic, `_schedule_reflection()` |
| `engine/executor.py` | `EngineConfig.reflection_trigger` field |
| `builder/builder.py` | `wf.reflection()` API |
| `loader/parser.py` | Markdown `type: reflection` support |
| `loader/compiler.py` | Compile reflection agent specs |
| `editor/src/types/graph.ts` | `ReflectionNode` type, descriptions |

## Decisions

- **ReflectionNode is a graph-level construct, not a post-processing script.** Reflection logic can be composed, scheduled, observed, and debugged using existing DAN infrastructure. A `ReflectionNode` inside a while-loop could iteratively refine principles. A `ReflectionNode` after a `ForEach` could compare department-level failures.
- **Principles are structured, not free-text.** `CausalPrinciple` has explicit `condition`/`action`/`reason` fields rather than a prose blob. This enables Tier 3 to programmatically convert principles to hyperedge rules with correct attachment and hook semantics.
- **Reflection is asynchronous and non-blocking.** Reflection runs are scheduled as background tasks after the main run completes. They don't block the user or the next run. Reflection cost is bounded by a single LLM call with constrained output.
- **Deduplication uses embedding similarity by default.** Two principles are merged if their embeddings exceed 0.9 cosine similarity. This avoids accumulating slightly-reworded versions of the same lesson while allowing genuinely distinct but textually similar principles to coexist.
- **Reflection trigger is `disabled` by default.** Users opt in by setting `reflection_trigger` in `EngineConfig` or by explicitly placing `ReflectionNode` in their workflow graph. This prevents unexpected LLM costs.

## Notes

- The reflection prompt is the most critical design artifact in Tier 2. Poor prompts produce generic principles ("avoid errors") instead of actionable ones ("when the CRSP data loader receives dates before 1990, pre-filter to avoid merge_asof tolerance errors"). Few-shot examples should be domain-specific where possible.
- Consider whether `ReflectionNode` should also be invocable manually from the chat panel (e.g., "reflect on the last 5 runs of this workflow"). This would be a natural integration with Phase 7 chat commands.
- Tier 2 is where the system transitions from "retrieve past errors" (mechanical) to "understand past errors" (reasoning). The quality of reflection directly determines the value of Tier 3.
- The `ConsolidationPipeline` (14-3) was designed for conversation memory compaction. Its `SUMMARIZE` strategy could be repurposed for principle consolidation, but the input format (principles vs. chat messages) may need an adapter.
- Recurring error patterns across multiple runs are the highest-value reflection input. `_gather_last_n_runs()` should highlight nodes that fail repeatedly with similar messages — these are the patterns most worth distilling into principles.
- The EvoAgentX survey (backlog) may surface relevant patterns for reflection and self-improvement. Consider reviewing before finalizing the reflection prompt design.
