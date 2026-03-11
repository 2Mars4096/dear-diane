# 31-21: Proactive Domain Learning

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** completed
**Goal:** Make DAN accumulate domain expertise from successful work — not just fix specific bugs — and use that expertise as structured pre-task guidance that prevents problems before they happen. Close the gap between "learn from mistakes" (reactive, instance-level) and "learn how to be competent in a domain" (proactive, category-level).

## Problem

DAN's learning system (29-6, 31-15) captures corrections, failure patterns, and principles — all reactive. When a LaTeX table breaks, DAN stores the fix. But it never asks: "what do I know about LaTeX tables in general? What patterns work? What should I check before rendering any table?" The system has no mechanism for:

1. **Extracting domain knowledge from success.** After a good paper render, nothing is stored about section structure, figure placement conventions, bibliography formatting, or journal-specific style rules. Only failures leave traces.
2. **Building structured domain competence.** Individual facts/preferences are stored, but there's no organized "what I know about paper rendering" knowledge base. A user who's done 5 equity reports with DAN should get better generation on report #6 — currently they don't.
3. **Pre-task domain guidance.** Memory retrieval (`_retrieve_memory_context`) returns an 800-char block of scored items ranked by keyword overlap. Domain expertise needs stronger injection: structured constraints that the LLM treats as requirements, not optional context.
4. **Improving the learning process itself.** After working in a domain enough, DAN should know *what to look for* in that domain. The first paper render extracts basic facts; the tenth should extract nuanced style rules, common failure modes, and structural conventions. The extraction templates should evolve.

### What exists and where this extends it

| Existing | What it does | Gap this plan fills |
|----------|-------------|---------------------|
| `MemoryExtractor.extract_with_llm()` | Extracts facts, preferences, episodes from each turn | Doesn't extract domain-specific patterns from completed tasks; prompt is generic |
| `extract_post_build_memory()` (build_session.py) | Stores `WORKFLOW_ASSET` + `WORKFLOW_PATTERN` after builds | Shallow: "Topology: 8 nodes." Doesn't capture domain conventions or best practices |
| `PatternExtractor` (29-6 §4) | Finds recurring sub-graph structures across workflows | Structural topology only — no semantic domain knowledge (tables, figures, citations) |
| `CorrectionStore` (31-15 §3) | Captures explicit user corrections | Reactive only — doesn't generalize from corrections to domain rules |
| `_retrieve_memory_context()` | Returns scored memory items as an 800-char context block | No priority distinction — domain expertise treated same as random facts |
| `classify_task_type()` | Coarse 4-way classifier (build/repair/factual/conversation) | Not domain-aware — "paper rendering" and "equity analysis" both map to the same policy |
| `RetrievalPolicy` + `POLICY_REGISTRY` | Per-task-type retrieval weights | No domain-specific policies; no way to prioritize domain expertise items |

## Design: Domains as Project-Level Tags

A **domain** is a labeled category of work, stored as a tag on the `Project` model or inferred from task content. Examples: `paper_rendering`, `equity_research`, `data_analysis`, `literature_review`, `workflow_building`. Domains are not a rigid taxonomy — they emerge from usage and can be user-assigned or auto-detected.

- **Storage**: domain knowledge uses existing `MemoryType` values (`FACT`, `PREFERENCE`, `PRINCIPLE`, `WORKFLOW_PATTERN`) with a `domain` key in `metadata` and a `domain_knowledge` tag.
- **Retrieval**: a new `DOMAIN_EXPERTISE_POLICY` in `POLICY_REGISTRY` prioritizes `domain`-tagged items when the active project or task matches that domain.
- **Injection**: domain knowledge is rendered as a structured "Domain Expertise" block in the system prompt, separate from the general memory context block, with higher priority formatting.

## Tasks

### 1. Domain detection and tagging
- [x] 1-1. `detect_domain(message: str, project: Project | None) -> str | None`: keyword + project-tag based domain classifier. Seed domains: `paper_rendering`, `equity_research`, `data_analysis`, `literature_review`, `code_generation`, `workflow_building`. Returns `None` for unrecognized domains. Lives in new `src/dan/server/concierge/domain_learning.py`.
- [x] 1-2. `Project.domain: str | None = None` field (Pydantic default handles backward compat with existing persisted projects). Auto-set on first substantive task in a project if not explicit. Users can set via `/project set domain <name>`.
- [x] 1-3. Domain propagation: `ResolvedContext.domain: str | None = None` field. Populated from `project.domain` during `context_resolver.resolve()`, or from `detect_domain()` when the project has no domain set yet. Available to all downstream consumers.

### 2. Post-task domain reflection
- [x] 2-1. `DomainReflector` class: given a completed task's conversation history, tool calls, and outcome, produces structured domain knowledge items via LLM call.
- [x] 2-2. Domain-aware reflection prompt: instead of the generic `extract_with_llm()` prompt ("extract facts/preferences/episodes"), use a domain-specific prompt that asks for:
  - Structural patterns observed (e.g., section ordering, data schema, report layout)
  - Formatting conventions used (e.g., LaTeX packages, table styles, chart types)
  - Tool/data patterns that worked (e.g., "used `booktabs` for tables", "CRSP data joined on permno+date")
  - Pitfalls encountered and avoided (even if nothing broke — "used `tabularx` to prevent overflow")
  - Domain-specific best practices applied
- [x] 2-3. Trigger: runs when a task transitions to `status="completed"` (detected in `_finalize_task()` in runtime.py). This is distinct from per-turn memory extraction — domain reflection operates on the full task conversation, not a single turn. Also triggered by `extract_post_build_memory()` in `build_session.py` for workflow build completions. The reflector receives the full `task.turns` history, not just the last message/response pair.
- [x] 2-4. Gate: `DAN_DOMAIN_LEARNING` env var. Register in `learning_tiers.py`: add `"domain_learning"` to `_TIER_FEATURES[1]` (advisory tier) and `"domain_learning": "DAN_DOMAIN_LEARNING"` to `_FEATURE_ENV_OVERRIDES`. Users can force-enable with `DAN_DOMAIN_LEARNING=1` at any tier. Costs 1 LLM call per completed task.
- [x] 2-5. Output: list of `MemoryItem` objects with `tags=["domain_knowledge"]`, `metadata={"domain": "<detected_domain>", "category": "<structure|formatting|tooling|pitfall|best_practice>"}`, stored via `MemoryKernel.store_many()`.

### 3. Domain extraction templates
- [x] 3-1. `DomainTemplate` Pydantic model: `domain: str`, `categories: list[str]`, `extraction_prompts: dict[str, str]`, `checklist: list[str]`, `version: int`, `created_at: float`, `item_count: int`.
- [x] 3-2. Seed templates persisted as JSON files in `~/.dan/domain_templates/<domain>.json` (not as MemoryItems — templates are structural metadata, not learned knowledge). Loaded by `DomainReflector` at reflection time. Seed templates shipped with the package in `src/dan/data/domain_templates/`:
  - `paper_rendering`: categories = [section_structure, table_formatting, figure_placement, bibliography, journal_style, math_typesetting]. Checklist: "What section ordering was used?", "What LaTeX packages were needed for tables?", "How were figures sized and placed?", "What bibliography style was used?", "Were there journal-specific formatting rules?"
  - `equity_research`: categories = [data_sources, analysis_structure, report_layout, factor_construction, visualization]. Checklist: "What data sources were joined?", "What was the report section structure?", "How were factors constructed?", "What chart types were used?"
  - `data_analysis`: categories = [data_loading, cleaning_patterns, join_strategies, output_format, tool_usage]. Checklist: "What file formats were handled?", "What column naming conventions were used?", "What join keys were used?", "How were results presented?"
  - `literature_review`: categories = [search_strategy, source_types, synthesis_structure, citation_density, thematic_grouping]. Checklist: "How were papers grouped?", "What was the review structure?", "How many citations per theme?"
- [x] 3-3. `DomainReflector` loads the matching template and uses its `extraction_prompts` and `checklist` to guide the reflection LLM call. Falls back to a generic reflection prompt when no template file exists for the detected domain.
- [x] 3-4. New domains are auto-created with a generic template on first encounter. Template starts with the default categories (`structure`, `formatting`, `tooling`, `pitfall`, `best_practice`) and an empty checklist. Grown by task 7.

### 4. Correction-to-domain-knowledge bridge
- [x] 4-1. Domain tagging happens at the call site in `runtime.py` (inside `_process_inner`, after `detect_correction` + `route_correction`), not inside `route_correction()` itself — because `route_correction()` only receives `CorrectionSignal` and has no access to project/domain context. After `route_correction()` returns actions, the runtime checks `context.domain` and, if present, creates an additional `FACT` item with `tags=["domain_knowledge"]`, `metadata={"domain": context.domain, "category": <inferred_from_correction_type>}`. Category inference: `style` corrections → `formatting`, `override` corrections → `tooling`, `preference` corrections → `best_practice`.
- [x] 4-2. Extend `_try_memory_extraction` in `runtime.py`: add `domain: str | None = None` parameter. When storing correction-derived preferences/principles, copy the domain into `metadata["domain"]` and append `"domain_knowledge"` to `tags` if the domain is non-None. Requires threading the domain through the call chain: `_store_memory_candidates(msg.text, content, None, project_id=..., domain=context.domain)` → `_store_memory_candidates_async(..., domain=domain)` → `_try_memory_extraction(..., domain=domain)`. The `domain` parameter is added to each method signature with default `None` for backward compatibility.
- [x] 4-3. Domain knowledge items derived from corrections get `importance=0.8` (higher than default 0.5) because they represent validated user intent.

### 5. Pre-task domain expertise injection
- [x] 5-1. New `_retrieve_domain_expertise()` method in `runtime.py` (separate from `_retrieve_memory_context()`). Does NOT use `MemoryKernel.retrieve()` — instead calls `MemoryKernel.list_by_type()` for each relevant type (`FACT`, `PREFERENCE`, `PRINCIPLE`, `WORKFLOW_PATTERN`), filters by domain tag, and scores using the module-level ranker functions from `memory_kernel.py` (`_rank_fact`, `_rank_preference`, etc.) directly. Budget allocation: `fact: 0.30, preference: 0.20, principle: 0.25, workflow_pattern: 0.25`. Does NOT require a new `MemoryType`.
- [x] 5-2. Domain-aware ranking: call `list_by_type(mem_type)` for each type, filter results to items where `"domain_knowledge" in item.tags and item.metadata.get("domain") == active_domain`. This acquires `_write_lock: RLock` only briefly per call (lock is released between calls). Then score each surviving item using the corresponding module-level ranker function (e.g., `_rank_fact(item, query)`) with a +0.3 base boost for domain relevance. Sort by score, take top N per type according to budget allocation. This avoids changing the `_TYPE_RANKERS` interface or `retrieve()` API, and minimizes lock contention with the concurrent general memory retrieval (which also acquires `_write_lock`).
- [x] 5-3. Extend `_retrieve_memory_context()` in `runtime.py`: when `context.domain` is non-None, call `_retrieve_domain_expertise()` and render results as a separate "Domain Expertise" block with structured formatting:
  ```
  [Domain Expertise: paper_rendering]
  - [STRUCTURE] INFORMS papers: Abstract → Intro → Lit Review → Model → Data → Results → Discussion → Conclusion
  - [TABLE] Always use booktabs and tabularx for wide tables; never use hline
  - [FIGURE] Place with [htbp] and \centering; use pgfplots for data charts
  - [PITFALL] BibTeX keys must exist — compile_latex auto-fills missing keys with placeholders
  ```
- [x] 5-4. Domain expertise block gets a separate budget (up to 600 chars) independent of the general memory context block (800 chars). Total memory injection: up to 1400 chars.
- [x] 5-5. For workflow generation paths (`WorkflowPlanner`, `BuildSessionManager`), domain expertise is injected as explicit constraints in the planning prompt, not just appended context. Phrased as "MUST" requirements: "Tables MUST use `booktabs`. Figures MUST use `[htbp]` placement."

### 6. Domain-aware post-generation validation
- [x] 6-1. `DomainValidator` class: given text content (response, generated code, or LaTeX) and the active domain's knowledge items, checks for violations of stored domain rules.
- [x] 6-2. Validation rules derived from domain knowledge items with `category=pitfall` or `category=best_practice`. Rule format: each domain knowledge item's `content` is scanned for "always/never/must/should" patterns; matching items become validation rules. Rule check: substring/keyword presence in the target text. Example: item content "Always use booktabs for tables" → check response text for `booktabs` when it also contains `\begin{table}`.
- [x] 6-3. Validation is advisory (warnings appended as a footnote to the response), not blocking. Insertion point: `_post_process_response()` in runtime.py, after completion guard. The validator operates on `content: str` (the response text), which may contain code blocks, LaTeX, or prose. It does NOT have access to separate artifact files — only the response body. For workflow build paths, the validator can also be called from `BuildSessionManager.diagnose_for_failure()` where the `generated_code` parameter is available.
- [x] 6-4. Gate: runs only when domain knowledge exists for the active domain and `DAN_DOMAIN_VALIDATION=1`. Zero LLM cost — rule matching is string/regex based.

### 7. Extraction template evolution (meta-learning)
- [x] 7-1. After a domain accumulates ≥ 10 knowledge items (counted by querying `MemoryKernel.list_by_type()` filtered to items with matching domain tag), run a consolidation pass (`DomainTemplateConsolidator`): group items by `metadata["category"]`, identify coverage gaps (categories with < 2 items), merge near-duplicate items (keyword overlap > 0.8), update the template's checklist with newly discovered categories.
- [x] 7-2. Consolidation trigger: called from `MemoryKernel.run_consolidation()` / `run_consolidation_async()` (which is invoked explicitly — there is no built-in periodic schedule). Add a `_consolidate_domain_templates()` step to `run_consolidation()` alongside existing decay/archive/promote steps. The concierge's `app.py` lifespan or a periodic background task should call `run_consolidation_async()` at a reasonable interval (e.g., daily). Domain template consolidation is gated by `is_feature_enabled("domain_learning")` inside the consolidation method.
- [x] 7-3. After consolidation, if items were merged or new categories discovered, update the `DomainTemplate` version and persist to `~/.dan/domain_templates/<domain>.json`. Old version is renamed to `<domain>.v<N>.json` for rollback.
- [x] 7-4. After ≥ 20 knowledge items in a domain, run LLM-assisted template upgrade: "Given these 20 domain knowledge items, what categories of knowledge am I missing? What additional checklist questions should I ask after completing a task in this domain?" Store the upgraded template with incremented version.
- [x] 7-5. Gate: template evolution at ≥ 10 items is heuristic (free). LLM-assisted upgrade at ≥ 20 items costs 1 LLM call per domain per threshold crossing. Gated behind `DAN_LEARNING_TIER >= 2`.

### 8. Success pattern generalization
- [x] 8-1. After ≥ 5 successful tasks in the same domain (tracked via domain-tagged `EPISODE` items with success markers), run `DomainPatternGeneralizer`: group domain knowledge items by category, extract the most frequently occurring items per category (items appearing in ≥ 3 tasks) as generalized patterns. Similarity metric: keyword overlap on `content` field (same as `_keyword_overlap()` in memory_kernel.py). Store as a high-importance `WORKFLOW_PATTERN` item tagged with the domain.
- [x] 8-2. Generalized patterns are promoted to `importance=0.9` and `lifecycle=DURABLE` — they represent validated expertise, not one-off observations.
- [x] 8-3. Wire generalized patterns into the `reuse_first_decision()` path: when a new task matches a domain with generalized patterns, those patterns influence the REUSE/ADAPT/GENERATE decision.
- [x] 8-4. Gate: `DAN_LEARNING_TIER >= 1`. Heuristic frequency counting (no LLM cost).

### 9. Active context assembly and sufficiency check
- [x] 9-1. `ContextPackage` Pydantic model: structured pre-execution context assembled incrementally across both prep phases. Fields: `domain: str | None`, `domain_expertise: str` (task 5 block), `project_summary: str`, `recent_artifacts: list[dict]` (file paths, workflow IDs, data sources from recent tool calls and project-scoped facts), `unresolved_references: list[str]` (phrases like "the paper", "that dataset" that couldn't be matched to known artifacts), `task_state: dict` (from cross-session resume state), `memory_context: str` (existing 800-char block), `auto_read_content: dict[str, str]` (path → summary for auto-read files). Assembly: phase 1 produces raw inputs (memory context, domain expertise, resolved context); `_build_context_package()` runs at the start of phase 2, combining phase 1 results and performing artifact resolution + sufficiency check. Full package is available before handler dispatch.
- [x] 9-2. **Artifact reference resolution**: scan the user message for implicit references ("the paper", "the report", "that file", "the dataset", "the table"). Resolve against: (a) project-scoped `FACT` items with `file_location` or `data_format` tags, (b) recent `tool_call` telemetry events for file paths, (c) task artifacts (`task.artifacts` dict), (d) recent assistant turns mentioning specific files. Matched references are included in `recent_artifacts`; unmatched go to `unresolved_references`.
- [x] 9-3. **Context sufficiency check**: after `ContextPackage` is assembled but before handler dispatch, evaluate whether critical information is missing. Heuristic rules:
  - If message references a file/document but `recent_artifacts` has no match and no explicit path → mark insufficient, generate clarification: "Which file do you mean? I found these in your project: [list]"
  - If the task requires data but no data source is known (no `data_format`/`file_location` facts for this project) → ask
  - If the domain is detected but zero domain expertise items exist and the task is complex (multi-step plan) → proceed but note: "I don't have previous experience with [domain] in this project — I'll do my best but may ask follow-up questions"
  - At most ONE clarification question per turn (existing policy from 25-7). If multiple gaps exist, ask about the most critical one and note the others.
- [x] 9-4. **Auto-read on file reference**: when a file path IS resolved (from artifact resolution or explicit mention), and the file hasn't been read in the current task, automatically invoke `file_read` or `pdf_read` capability tool before the main LLM call. Extends the existing file auto-read (31-4) with project-scoped artifact awareness. Gate: `DAN_AUTO_READ=1` (default on).
- [x] 9-5. **Enriched prompt context**: replace `_augmented_prompt_context()` in handlers.py with `_build_prompt_from_package(context_package)` that renders the full `ContextPackage` as structured prompt sections: project state, recent artifacts with summaries, domain expertise, task continuity state. Each handler receives the richer prompt instead of the current 2-line project summary.
- [x] 9-6. Wire into `_process_inner()`: `_build_context_package()` runs at the start of phase 2 (after phase 1 fan-out completes), consuming phase 1 results (memory context, domain expertise, resolved context) and performing artifact resolution + sufficiency check. If sufficiency check triggers a clarification, short-circuit the handler and yield the clarification event (same pattern as guard pipeline clarifications). The full `ContextPackage` is passed to the handler via the enriched prompt context (task 9-5).

### 10. Parallel pipeline orchestration
- [x] 10-1. **Audit existing parallelism and define the concurrency map.** Current state:
  - **Pre-response (parallel prep)**: `fan_out_dict` in `_process_inner()` already runs `memory_retrieval`, `context_resolution`, `reuse_decision` concurrently. Gated by `DAN_CONCIERGE_PARALLEL_PREP=1` with `DAN_CONCIERGE_PREP_TIMEOUT=5.0s`.
  - **Post-response (background)**: `_store_memory_candidates()` fires a background `asyncio.Task` that fans out `episode`, `preferences`, `memory_extraction` concurrently via `fan_out_dict`. Non-blocking (fire-and-forget, tracked in `_bg_memory_tasks` set).
  - **Post-build (background)**: `_run_post_build_followups()` fans out `post_build_memory`, `link_workflow` concurrently.
  - **Missing**: domain expertise retrieval (task 5), context package assembly (task 9), artifact resolution (task 9-2), domain reflection (task 2), domain validation (task 6), auto-read (task 9-4) are all defined as sequential today.
- [x] 10-2. **Expand the pre-response parallel prep fan-out (phase 1).** Add to the existing `prep_tasks` dict in `_process_inner()`:
  - `"domain_expertise"`: `_retrieve_domain_expertise()` (task 5) — independent of memory retrieval (different query, different budget). Note: both acquire `MemoryKernel._write_lock: RLock` from different threads so they serialize on lock acquisition, but each hold is brief (in-memory operations). Net latency is still lower than sequential because other phase 1 tasks (context resolution, reuse decision) run truly in parallel.
  - `"auto_read_explicit"`: if an explicit file path is detected in the message (regex match for path-like strings), start reading it immediately. Independent of everything else.
  All share the existing `DAN_CONCIERGE_PREP_TIMEOUT`. Exception handling: same pattern — `isinstance(result, Exception)` → log and degrade gracefully.
- [x] 10-3. **Two-phase pre-response pipeline.** Some new tasks depend on phase 1 results:
  - **Phase 1** (fully parallel): memory retrieval, context resolution, reuse decision, domain expertise retrieval, explicit file reads. These have no data dependencies on each other.
  - **Phase 2** (after phase 1 completes): artifact reference resolution (needs ResolvedContext from context resolution for `task.artifacts` and recent turns, needs MemoryKernel for project-scoped FACTs, needs TelemetryStore for recent `tool_call` events — all inputs produced or accessible after phase 1), sufficiency check (needs all of phase 1 + artifact resolution), auto-read for *implicit* refs (needs artifact resolution to know which files to read).
  Implementation: two sequential `fan_out_dict` calls. Phase 2 tasks are only created if phase 1 produced results that warrant them (e.g., artifact resolution only runs if message contains implicit references like "the paper", "that file"). Total wall-clock: phase 1 time + phase 2 time, but phase 2 tasks are typically fast (artifact resolution is local lookup against in-memory data, sufficiency check is heuristic, implicit-ref auto-read hits local files).
- [x] 10-4. **Expand the post-response background fan-out.** Two separate fire-and-forget sites:
  - **Domain reflection** — spawned from `_finalize_task()` (NOT from `_store_memory_candidates_async()`). Reason: `_store_memory_candidates()` is called at line 2142 *before* `_finalize_task()` at line 2143, so it doesn't know the task completed. `_finalize_task()` has the correct trigger (`status == "completed"`) and access to `context.task.turns` (full conversation). Spawn as `loop.create_task(_domain_reflect_async(context, msg))`, tracked in `_bg_memory_tasks`. The reflection method fans out internally if needed.
  - **Domain validation log** — remains in `_store_memory_candidates_async()`: if domain validation (task 6) produced warnings during `_post_process_response()`, log them as `FACT` items with `tags=["domain_validation_warning"]`. The warnings are passed via a new `_pending_domain_warnings: list[str]` instance attribute set by `_post_process_response()` and consumed by `_store_memory_candidates_async()`. Lightweight, no LLM.
  Both use the same error handling pattern (`isinstance(result, Exception)` → log).
- [x] 10-5. **Concurrency safety rules.** Document and enforce:
  - **No shared mutable state between fan-out tasks.** Each task operates on its own copy of inputs. `MemoryKernel.store()` and `MemoryKernel.retrieve()` are thread-safe (they use `threading.RLock` internally). `ProjectStore` reads are safe; writes go through `append_turn` / `update_task_status` which are serialized.
  - **`asyncio.to_thread` for all blocking calls.** Every task in `fan_out_dict` must be wrapped in `asyncio.to_thread()` to avoid blocking the event loop. Existing pattern already does this.
  - **Timeout per task.** The existing `timeout_per` parameter in `fan_out_dict` handles hung tasks. Set to `DAN_CONCIERGE_PREP_TIMEOUT` (default 5s) for pre-response, no timeout for post-response background (they're non-blocking anyway).
  - **Global concurrency cap.** Add `asyncio.Semaphore(N)` (env var `DAN_PARALLEL_TASK_LIMIT`, default 8) in `fan_out.py` to cap total concurrent `to_thread` calls across all fan-outs. Prevents thread pool exhaustion if multiple concurrent user messages arrive. The semaphore wraps `_run_task()`.
  - **Graceful degradation.** Every fan-out result is checked with `isinstance(result, Exception)`. Failed tasks produce empty/default results, never crash the pipeline. Log at `DEBUG` level.
- [x] 10-6. **Telemetry for parallel tasks.** Emit `tool_call` or custom `parallel_task` telemetry events for each fan-out task with `duration_ms`, `success`, `task_name`. This makes it visible in `/analytics` which tasks are slow or failing, enabling future optimization.

### 11. Tests and docs
- [x] 11-1. Unit tests: `detect_domain()` classifier, `DomainReflector` extraction, `DomainTemplate` CRUD, `DomainValidator` rule matching, `DomainTemplateConsolidator` merge/gap logic.
- [x] 11-2. Unit tests: `_build_context_package()` assembly, artifact reference resolution, context sufficiency heuristics.
- [x] 11-3. Unit tests: two-phase fan-out ordering (phase 2 tasks receive phase 1 results), global semaphore cap, timeout behavior.
- [x] 11-4. Integration test: complete a paper-rendering task → domain reflection extracts 3+ items → next paper task retrieves domain expertise block → expertise block contains previous items.
- [x] 11-5. Integration test: correction in equity_research domain → domain knowledge item created → available in domain expertise block on next equity research task.
- [x] 11-6. Integration test: 10+ domain items → consolidation runs → template updated → next reflection uses updated template checklist.
- [x] 11-7. Integration test: user says "update the report" in a project with one known report file → file is auto-resolved and context package includes it. User says "update the report" with no known files → sufficiency check triggers focused clarification.
- [x] 11-8. Integration test: parallel prep fan-out runs domain expertise + artifact resolution + memory retrieval concurrently → all results available for handler dispatch. Verify via telemetry events that tasks overlapped in time.
- [x] 11-9. Updated `.env.example` with `DAN_DOMAIN_LEARNING`, `DAN_DOMAIN_VALIDATION`, `DAN_AUTO_READ`, `DAN_PARALLEL_TASK_LIMIT` descriptions.
- [x] 11-10. Updated `docs/architecture.md` with domain learning, active context, and parallel pipeline sections.
- [x] 11-11. Changelog entry.

## Incremental Delivery

The plan is designed for progressive value — each task group is independently useful:

1. **Task 1 + 2 alone** = domain detection + post-task reflection. Immediately starts accumulating domain knowledge from successful tasks. Value: knowledge stops being lost.
2. **+ Task 3** = better extraction quality via domain-specific prompts. Value: more relevant knowledge per extraction.
3. **+ Task 4** = corrections feed domain knowledge. Value: user corrections become reusable domain rules, not one-off fixes.
4. **+ Task 5** = domain expertise injected pre-task. Value: accumulated knowledge actually changes behavior. This is where the user first feels the difference.
5. **+ Task 6** = post-generation validation against domain rules. Value: prevents violations of known domain conventions.
6. **+ Task 7** = templates improve over time. Value: extraction gets better with experience.
7. **+ Task 8** = success patterns generalized. Value: expertise compounds across projects.

8. **+ Task 9** = active context assembly with artifact resolution and sufficiency check. Value: the concierge understands full background before acting, asks focused clarifications only when truly needed, auto-reads referenced files. This is the "less user effort" layer — users stop having to re-explain context.
9. **+ Task 10** = parallel pipeline orchestration. Value: all the new work (domain expertise, artifact resolution, context assembly, domain reflection, auto-read) runs concurrently instead of sequentially. No added latency for the user. Two-phase pre-response fan-out, expanded post-response background fan-out, global concurrency cap for safety.

Tasks 1-5 are the core domain loop. Task 9 is the active context layer. Task 10 is the parallelization layer. Tasks 9 and 10 can ship together and in parallel with tasks 1-5 since they touch different code paths — `_process_inner` prep phase vs. `_finalize_task` reflection phase. Tasks 6-8 are domain enhancements that can ship later.

## Dependencies

- `MemoryKernel` (29-1) — storage and retrieval
- `MemoryExtractor` (29-6 §1) — extended with domain-aware prompts
- `CorrectionStore` (31-15 §3) — correction-to-domain bridge
- `extract_post_build_memory()` (29-3) — extended trigger point
- `_retrieve_memory_context()` (runtime.py) — extended with domain expertise block
- `RetrievalPolicy` + `POLICY_REGISTRY` (memory_kernel.py) — new domain expertise policy
- `ProjectStore` / `Project` model — domain field
- `_post_process_response()` (runtime.py) — domain validation insertion point
- `DAN_LEARNING_TIER` (31-15 §1) — tier gating for expensive features
- `_augmented_prompt_context()` (handlers.py) — replaced by enriched context rendering
- Entity grounding (31-19) — extended with artifact reference resolution
- File auto-read (31-4) — extended with project-scoped artifact awareness

## Estimate

5-6 days (tasks 1-5 domain core: ~2d, tasks 9-10 active context + parallelization: ~1.5d, tasks 6-8 enhancements: ~1-2d, task 11 tests: ~0.5d)

## Decisions

- Post-review patch: domain reflection now uses a provider-backed adapter and persists extracted items via `MemoryKernel.store_many()`.
- Post-review patch: reflected domain items are stamped with `scope=PROJECT` and `metadata["project_id"]` before storage so learned rules do not bleed across projects.
- Post-review patch: domain expertise retrieval now reserves per-type slots (`FACT` / `PREFERENCE` / `PRINCIPLE` / `WORKFLOW_PATTERN`) and uses `_rank_principle()` for `PRINCIPLE` items.
- Post-review patch: domain validation now reads `PRINCIPLE`/`PREFERENCE` items, domain warnings are request-scoped via `ContextVar`, and the domain-expertise block is injected into handler prompt context through message metadata.
- Post-review patch: project-scoped domain knowledge is filtered by `project_id` during expertise retrieval and validation so one project's domain rules do not leak into another.
- Post-review patch: pending confirm/clarify replays now carry `resolved_domain` / `resolved_project_id` metadata so request-scoped validation and prompt injection keep working on replay paths too.
- Post-review patch: `DomainTemplate` gained a real `metadata` field, consolidation now records coverage gaps, merges near-duplicates, and `MemoryKernel` deletes duplicate domain items during template consolidation.
- Completion: `DomainTemplateUpgrader` uses provider-backed LLM to discover missing categories and checklist questions at ≥20 items. Consolidation marks templates with `needs_llm_upgrade=True` metadata for the runtime to execute (sync thread has no provider access).
- Completion: `reuse_first_decision()` gains `domain_patterns` parameter — generalized `WORKFLOW_PATTERN` items tagged `generalized_pattern` for the resolved domain boost matching workflow assets by +0.15 score.
- Completion: Two-phase pre-response pipeline implemented — phase 1 (memory, context, reuse, domain expertise, explicit auto-read) fully parallel via `fan_out_dict`; phase 2 (artifact resolution, implicit auto-read, context package, sufficiency check) runs after phase 1 completes.
- Completion: Auto-read covers both explicit file paths in phase 1 and implicit references from artifact resolution in phase 2. Gated by `DAN_AUTO_READ=1` (default on). Files >100KB and PDFs are skipped; content truncated at 2000 chars.
- Completion: `_build_context_package()` assembles `ContextPackage` from all phase 1 + phase 2 results, stored in `msg.metadata["context_package"]` for handler consumption via `_build_prompt_from_package()`.
- Completion: 50 tests in `tests/test_domain_learning.py` — 35 core + 10 unit (context package, artifact resolution, sufficiency, fan-out, reuse patterns, template upgrader) + 5 integration (reflection E2E, correction bridge, consolidation threshold, recent-turn resolution, concurrent timing).

## Primary Files

- `src/dan/server/concierge/domain_learning.py` — **new**: `detect_domain()`, `DomainReflector`, `DomainTemplate`, `DomainTemplateConsolidator`, `DomainValidator`, `DomainPatternGeneralizer`
- `src/dan/data/domain_templates/` — **new**: seed JSON templates for 4 domains
- `src/dan/server/concierge/runtime.py` — `_retrieve_domain_expertise()`, correction-to-domain wiring in `_process_inner`, domain reflection trigger in `_finalize_task`
- `src/dan/server/concierge/models.py` — `Project.domain` field
- `src/dan/server/concierge/context_resolver.py` — `ResolvedContext.domain` field, `domain` resolution in `resolve()`
- `src/dan/engine/memory_kernel.py` — `_consolidate_domain_templates()` in `run_consolidation()`
- `src/dan/engine/learning_tiers.py` — `domain_learning` feature registration
- `src/dan/server/concierge/handlers.py` — `_build_prompt_from_package()` replaces `_augmented_prompt_context()`
- `src/dan/server/concierge/fan_out.py` — global `Semaphore` wrapper, telemetry hooks

## Notes

- The LLM cost profile is bounded: 1 domain reflection call per completed task (task 2), 1 template upgrade call per domain per 20-item threshold (task 7). All other operations (detection, retrieval, validation, consolidation, generalization) are local computation.
- Domain templates are persisted as standalone JSON files in `~/.dan/domain_templates/`, NOT as MemoryItems. Templates are structural metadata (schemas for what to extract), not learned knowledge. Keeping them separate avoids polluting the memory index with items that serve a fundamentally different purpose.
- The 600-char domain expertise block (task 5) is deliberately separate from the 800-char general memory block. This prevents domain knowledge from being crowded out by unrelated episodes or working state.
- Domain expertise retrieval (task 5-2) uses `list_by_type()` + external scoring instead of `retrieve()`. This avoids changing the `retrieve()` API or the `_TYPE_RANKERS` interface signature `(MemoryItem, str) -> float`. The module-level ranker functions (`_rank_fact`, `_rank_preference`, etc.) are imported and called directly, keeping the scoring logic consistent with general retrieval. `list_by_type()` acquires `_write_lock` briefly per call, releasing between types — this reduces lock contention vs. a single `retrieve()` call that holds the lock for the entire iteration.
- Post-generation validation (task 6) operates on the response text body. It catches keyword-level violations ("forgot `booktabs`") but cannot check separate artifact files. The workflow build path additionally validates against `generated_code` via `BuildSessionManager.diagnose_for_failure()`.
- This plan does NOT add a new `MemoryType`. Domain knowledge uses existing types (`FACT`, `PREFERENCE`, `PRINCIPLE`, `WORKFLOW_PATTERN`) with structured `metadata` and `tags`. This avoids expanding the type enum and keeps backward compatibility with all existing retrieval/ranking code.
- The domain reflection trigger (task 2-3) is task-level, not turn-level. It runs when `_finalize_task()` sets `task.status = "completed"`, giving the reflector access to the full conversation history for that task. This is complementary to the per-turn `MemoryExtractor` which handles fact/preference/episode extraction.
- **Active context (task 9)**: `ContextPackage` assembly spans both prep phases — phase 1 produces raw inputs concurrently, phase 2 combines them into the package (artifact resolution + sufficiency check). Phase 2 adds minimal latency (local lookups, heuristic checks). The sufficiency check is a fast heuristic pass (no LLM call) — it checks whether implicit references resolve and whether critical context types are present. The auto-read extension reuses existing file-read tool infrastructure.
- **Artifact reference resolution** extends entity grounding with a new resolution layer. Entity grounding currently matches project/task names; artifact resolution matches "the paper", "the dataset", "that file" against project-scoped facts and recent tool call history. These are distinct resolution types — entity grounding answers "which project?" while artifact resolution answers "which file/resource within this project?"
- **Prompt enrichment**: the current `_augmented_prompt_context()` produces a 2-line string (project label + task label). `_build_prompt_from_package()` produces structured sections (project state, artifacts, domain expertise, task continuity). Each handler gets the full package — the handler decides which sections are relevant for its system prompt construction.
- **Parallelization safety**: `fan_out_dict` already handles exceptions per-task via `return_exceptions=True`. Each task receives immutable inputs (message text, project ID, etc.) — no shared mutable state. `MemoryKernel` uses internal `threading.RLock` (reentrant lock) for thread safety. When two fan-out tasks both access `MemoryKernel` from different `to_thread` workers, they serialize on the `RLock` — the lock is reentrant only for the *same* thread. This means `_retrieve_memory_context()` and `_retrieve_domain_expertise()` don't truly parallelize against each other, but each lock-hold is brief (in-memory operations) and they do parallelize against non-MemoryKernel tasks (context resolution, reuse decision, file reads). The global `asyncio.Semaphore` (task 10-5) prevents thread pool exhaustion under concurrent user load — without it, N concurrent users × M fan-out tasks could spawn N×M threads against the default `ThreadPoolExecutor` (typically ~32 workers). The semaphore caps this at `DAN_PARALLEL_TASK_LIMIT` total concurrent blocking calls.
- **Two-phase fan-out** (task 10-3) is a pragmatic trade-off: fully parallel would require speculative execution for dependent tasks (artifact resolution without knowing the project context yet), which adds complexity and wasted work. Two sequential `fan_out_dict` calls are simple, correct, and the phase 2 tasks are fast (local lookups, heuristic checks). If profiling shows phase 2 is a bottleneck, individual tasks can be made speculative later.
