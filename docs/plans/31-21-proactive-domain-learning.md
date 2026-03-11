# 31-21: Proactive Domain Learning

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** not-started
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
- [ ] 1-1. `detect_domain(message: str, project: Project | None) -> str | None`: keyword + project-tag based domain classifier. Seed domains: `paper_rendering`, `equity_research`, `data_analysis`, `literature_review`, `code_generation`, `workflow_building`. Returns `None` for unrecognized domains. Lives in new `src/dan/server/concierge/domain_learning.py`.
- [ ] 1-2. `Project.domain: str | None = None` field (Pydantic default handles backward compat with existing persisted projects). Auto-set on first substantive task in a project if not explicit. Users can set via `/project set domain <name>`.
- [ ] 1-3. Domain propagation: `ResolvedContext.domain: str | None = None` field. Populated from `project.domain` during `context_resolver.resolve()`, or from `detect_domain()` when the project has no domain set yet. Available to all downstream consumers.

### 2. Post-task domain reflection
- [ ] 2-1. `DomainReflector` class: given a completed task's conversation history, tool calls, and outcome, produces structured domain knowledge items via LLM call.
- [ ] 2-2. Domain-aware reflection prompt: instead of the generic `extract_with_llm()` prompt ("extract facts/preferences/episodes"), use a domain-specific prompt that asks for:
  - Structural patterns observed (e.g., section ordering, data schema, report layout)
  - Formatting conventions used (e.g., LaTeX packages, table styles, chart types)
  - Tool/data patterns that worked (e.g., "used `booktabs` for tables", "CRSP data joined on permno+date")
  - Pitfalls encountered and avoided (even if nothing broke — "used `tabularx` to prevent overflow")
  - Domain-specific best practices applied
- [ ] 2-3. Trigger: runs when a task transitions to `status="completed"` (detected in `_finalize_task()` in runtime.py). This is distinct from per-turn memory extraction — domain reflection operates on the full task conversation, not a single turn. Also triggered by `extract_post_build_memory()` in `build_session.py` for workflow build completions. The reflector receives the full `task.turns` history, not just the last message/response pair.
- [ ] 2-4. Gate: `DAN_DOMAIN_LEARNING` env var. Register in `learning_tiers.py`: add `"domain_learning"` to `_TIER_FEATURES[1]` (advisory tier) and `"domain_learning": "DAN_DOMAIN_LEARNING"` to `_FEATURE_ENV_OVERRIDES`. Users can force-enable with `DAN_DOMAIN_LEARNING=1` at any tier. Costs 1 LLM call per completed task.
- [ ] 2-5. Output: list of `MemoryItem` objects with `tags=["domain_knowledge"]`, `metadata={"domain": "<detected_domain>", "category": "<structure|formatting|tooling|pitfall|best_practice>"}`, stored via `MemoryKernel.store_many()`.

### 3. Domain extraction templates
- [ ] 3-1. `DomainTemplate` Pydantic model: `domain: str`, `categories: list[str]`, `extraction_prompts: dict[str, str]`, `checklist: list[str]`, `version: int`, `created_at: float`, `item_count: int`.
- [ ] 3-2. Seed templates persisted as JSON files in `~/.dan/domain_templates/<domain>.json` (not as MemoryItems — templates are structural metadata, not learned knowledge). Loaded by `DomainReflector` at reflection time. Seed templates shipped with the package in `src/dan/data/domain_templates/`:
  - `paper_rendering`: categories = [section_structure, table_formatting, figure_placement, bibliography, journal_style, math_typesetting]. Checklist: "What section ordering was used?", "What LaTeX packages were needed for tables?", "How were figures sized and placed?", "What bibliography style was used?", "Were there journal-specific formatting rules?"
  - `equity_research`: categories = [data_sources, analysis_structure, report_layout, factor_construction, visualization]. Checklist: "What data sources were joined?", "What was the report section structure?", "How were factors constructed?", "What chart types were used?"
  - `data_analysis`: categories = [data_loading, cleaning_patterns, join_strategies, output_format, tool_usage]. Checklist: "What file formats were handled?", "What column naming conventions were used?", "What join keys were used?", "How were results presented?"
  - `literature_review`: categories = [search_strategy, source_types, synthesis_structure, citation_density, thematic_grouping]. Checklist: "How were papers grouped?", "What was the review structure?", "How many citations per theme?"
- [ ] 3-3. `DomainReflector` loads the matching template and uses its `extraction_prompts` and `checklist` to guide the reflection LLM call. Falls back to a generic reflection prompt when no template file exists for the detected domain.
- [ ] 3-4. New domains are auto-created with a generic template on first encounter. Template starts with the default categories (`structure`, `formatting`, `tooling`, `pitfall`, `best_practice`) and an empty checklist. Grown by task 7.

### 4. Correction-to-domain-knowledge bridge
- [ ] 4-1. Domain tagging happens at the call site in `runtime.py` (inside `_process_inner`, after `detect_correction` + `route_correction`), not inside `route_correction()` itself — because `route_correction()` only receives `CorrectionSignal` and has no access to project/domain context. After `route_correction()` returns actions, the runtime checks `context.domain` and, if present, creates an additional `FACT` item with `tags=["domain_knowledge"]`, `metadata={"domain": context.domain, "category": <inferred_from_correction_type>}`. Category inference: `style` corrections → `formatting`, `override` corrections → `tooling`, `preference` corrections → `best_practice`.
- [ ] 4-2. Extend `_try_memory_extraction` in `runtime.py`: when storing correction-derived preferences/principles, copy the active `context.domain` into `metadata["domain"]` and append `"domain_knowledge"` to `tags` if the domain is non-None.
- [ ] 4-3. Domain knowledge items derived from corrections get `importance=0.8` (higher than default 0.5) because they represent validated user intent.

### 5. Pre-task domain expertise injection
- [ ] 5-1. New `_retrieve_domain_expertise()` method in `runtime.py` (separate from `_retrieve_memory_context()`). Queries `MemoryKernel.retrieve()` with a custom `RetrievalPolicy` that includes all standard types (`FACT`, `PREFERENCE`, `PRINCIPLE`, `WORKFLOW_PATTERN`) but uses a domain-aware ranker. Budget: `fact: 0.30, preference: 0.20, principle: 0.25, workflow_pattern: 0.25`. Does NOT require a new `MemoryType` — the policy queries existing types but the ranker filters by domain tag.
- [ ] 5-2. Domain-aware ranking: existing ranking functions (`_rank_fact`, `_rank_preference`, etc.) have signature `(item: MemoryItem, query: str) -> float` and cannot accept a `domain` parameter. Instead, `_retrieve_domain_expertise()` pre-filters the kernel's index to items with `"domain_knowledge" in item.tags and item.metadata.get("domain") == active_domain` before scoring. This avoids changing the `_TYPE_RANKERS` interface. Items that pass the domain filter are then ranked by the standard per-type rankers with a +0.3 base boost for domain relevance.
- [ ] 5-3. Extend `_retrieve_memory_context()` in `runtime.py`: when `context.domain` is non-None, call `_retrieve_domain_expertise()` and render results as a separate "Domain Expertise" block with structured formatting:
  ```
  [Domain Expertise: paper_rendering]
  - [STRUCTURE] INFORMS papers: Abstract → Intro → Lit Review → Model → Data → Results → Discussion → Conclusion
  - [TABLE] Always use booktabs and tabularx for wide tables; never use hline
  - [FIGURE] Place with [htbp] and \centering; use pgfplots for data charts
  - [PITFALL] BibTeX keys must exist — compile_latex auto-fills missing keys with placeholders
  ```
- [ ] 5-4. Domain expertise block gets a separate budget (up to 600 chars) independent of the general memory context block (800 chars). Total memory injection: up to 1400 chars.
- [ ] 5-5. For workflow generation paths (`WorkflowPlanner`, `BuildSessionManager`), domain expertise is injected as explicit constraints in the planning prompt, not just appended context. Phrased as "MUST" requirements: "Tables MUST use `booktabs`. Figures MUST use `[htbp]` placement."

### 6. Domain-aware post-generation validation
- [ ] 6-1. `DomainValidator` class: given text content (response, generated code, or LaTeX) and the active domain's knowledge items, checks for violations of stored domain rules.
- [ ] 6-2. Validation rules derived from domain knowledge items with `category=pitfall` or `category=best_practice`. Rule format: each domain knowledge item's `content` is scanned for "always/never/must/should" patterns; matching items become validation rules. Rule check: substring/keyword presence in the target text. Example: item content "Always use booktabs for tables" → check response text for `booktabs` when it also contains `\begin{table}`.
- [ ] 6-3. Validation is advisory (warnings appended as a footnote to the response), not blocking. Insertion point: `_post_process_response()` in runtime.py, after completion guard. The validator operates on `content: str` (the response text), which may contain code blocks, LaTeX, or prose. It does NOT have access to separate artifact files — only the response body. For workflow build paths, the validator can also be called from `BuildSessionManager.diagnose_for_failure()` where the `generated_code` parameter is available.
- [ ] 6-4. Gate: runs only when domain knowledge exists for the active domain and `DAN_DOMAIN_VALIDATION=1`. Zero LLM cost — rule matching is string/regex based.

### 7. Extraction template evolution (meta-learning)
- [ ] 7-1. After a domain accumulates ≥ 10 knowledge items (counted by querying `MemoryKernel.list_by_type()` filtered to items with matching domain tag), run a consolidation pass (`DomainTemplateConsolidator`): group items by `metadata["category"]`, identify coverage gaps (categories with < 2 items), merge near-duplicate items (keyword overlap > 0.8), update the template's checklist with newly discovered categories.
- [ ] 7-2. Consolidation trigger: called from `MemoryKernel.run_consolidation()` / `run_consolidation_async()` (which is invoked explicitly — there is no built-in periodic schedule). Add a `_consolidate_domain_templates()` step to `run_consolidation()` alongside existing decay/archive/promote steps. The concierge's `app.py` lifespan or a periodic background task should call `run_consolidation_async()` at a reasonable interval (e.g., daily). Domain template consolidation is gated by `is_feature_enabled("domain_learning")` inside the consolidation method.
- [ ] 7-3. After consolidation, if items were merged or new categories discovered, update the `DomainTemplate` version and persist to `~/.dan/domain_templates/<domain>.json`. Old version is renamed to `<domain>.v<N>.json` for rollback.
- [ ] 7-4. After ≥ 20 knowledge items in a domain, run LLM-assisted template upgrade: "Given these 20 domain knowledge items, what categories of knowledge am I missing? What additional checklist questions should I ask after completing a task in this domain?" Store the upgraded template with incremented version.
- [ ] 7-5. Gate: template evolution at ≥ 10 items is heuristic (free). LLM-assisted upgrade at ≥ 20 items costs 1 LLM call per domain per threshold crossing. Gated behind `DAN_LEARNING_TIER >= 2`.

### 8. Success pattern generalization
- [ ] 8-1. After ≥ 5 successful tasks in the same domain (tracked via domain-tagged `EPISODE` items with success markers), run `DomainPatternGeneralizer`: group domain knowledge items by category, extract the most frequently occurring items per category (items appearing in ≥ 3 tasks) as generalized patterns. Similarity metric: keyword overlap on `content` field (same as `_keyword_overlap()` in memory_kernel.py). Store as a high-importance `WORKFLOW_PATTERN` item tagged with the domain.
- [ ] 8-2. Generalized patterns are promoted to `importance=0.9` and `lifecycle=DURABLE` — they represent validated expertise, not one-off observations.
- [ ] 8-3. Wire generalized patterns into the `reuse_first_decision()` path: when a new task matches a domain with generalized patterns, those patterns influence the REUSE/ADAPT/GENERATE decision.
- [ ] 8-4. Gate: `DAN_LEARNING_TIER >= 1`. Heuristic frequency counting (no LLM cost).

### 9. Tests and docs
- [ ] 9-1. Unit tests: `detect_domain()` classifier, `DomainReflector` extraction, `DomainTemplate` CRUD, `DomainValidator` rule matching, `DomainTemplateConsolidator` merge/gap logic.
- [ ] 9-2. Integration test: complete a paper-rendering task → domain reflection extracts 3+ items → next paper task retrieves domain expertise block → expertise block contains previous items.
- [ ] 9-3. Integration test: correction in equity_research domain → domain knowledge item created → available in domain expertise block on next equity research task.
- [ ] 9-4. Integration test: 10+ domain items → consolidation runs → template updated → next reflection uses updated template checklist.
- [ ] 9-5. Updated `.env.example` with `DAN_DOMAIN_LEARNING`, `DAN_DOMAIN_VALIDATION` descriptions.
- [ ] 9-6. Updated `docs/architecture.md` with domain learning section.
- [ ] 9-7. Changelog entry.

## Incremental Delivery

The plan is designed for progressive value — each task group is independently useful:

1. **Task 1 + 2 alone** = domain detection + post-task reflection. Immediately starts accumulating domain knowledge from successful tasks. Value: knowledge stops being lost.
2. **+ Task 3** = better extraction quality via domain-specific prompts. Value: more relevant knowledge per extraction.
3. **+ Task 4** = corrections feed domain knowledge. Value: user corrections become reusable domain rules, not one-off fixes.
4. **+ Task 5** = domain expertise injected pre-task. Value: accumulated knowledge actually changes behavior. This is where the user first feels the difference.
5. **+ Task 6** = post-generation validation against domain rules. Value: prevents violations of known domain conventions.
6. **+ Task 7** = templates improve over time. Value: extraction gets better with experience.
7. **+ Task 8** = success patterns generalized. Value: expertise compounds across projects.

Tasks 1-5 are the core loop. Tasks 6-8 are enhancements that can ship later.

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

## Estimate

3-4 days (tasks 1-5 core: ~2d, tasks 6-8 enhancements: ~1-2d)

## Decisions

- (filled in during execution)

## Primary Files

- `src/dan/server/concierge/domain_learning.py` — **new**: `detect_domain()`, `DomainReflector`, `DomainTemplate`, `DomainTemplateConsolidator`, `DomainValidator`, `DomainPatternGeneralizer`
- `src/dan/data/domain_templates/` — **new**: seed JSON templates for 4 domains
- `src/dan/server/concierge/runtime.py` — `_retrieve_domain_expertise()`, correction-to-domain wiring in `_process_inner`, domain reflection trigger in `_finalize_task`
- `src/dan/server/concierge/models.py` — `Project.domain` field, `ResolvedContext.domain` field
- `src/dan/server/concierge/context_resolver.py` — `domain` resolution in `resolve()`
- `src/dan/engine/memory_kernel.py` — `_consolidate_domain_templates()` in `run_consolidation()`
- `src/dan/engine/learning_tiers.py` — `domain_learning` feature registration

## Notes

- The LLM cost profile is bounded: 1 domain reflection call per completed task (task 2), 1 template upgrade call per domain per 20-item threshold (task 7). All other operations (detection, retrieval, validation, consolidation, generalization) are local computation.
- Domain templates are persisted as standalone JSON files in `~/.dan/domain_templates/`, NOT as MemoryItems. Templates are structural metadata (schemas for what to extract), not learned knowledge. Keeping them separate avoids polluting the memory index with items that serve a fundamentally different purpose.
- The 600-char domain expertise block (task 5) is deliberately separate from the 800-char general memory block. This prevents domain knowledge from being crowded out by unrelated episodes or working state.
- Domain expertise retrieval (task 5-2) pre-filters the kernel index by domain tag instead of adding a `domain` parameter to ranking functions. This avoids changing the `_TYPE_RANKERS` interface signature `(MemoryItem, str) -> float` which all 8 existing rankers implement.
- Post-generation validation (task 6) operates on the response text body. It catches keyword-level violations ("forgot `booktabs`") but cannot check separate artifact files. The workflow build path additionally validates against `generated_code` via `BuildSessionManager.diagnose_for_failure()`.
- This plan does NOT add a new `MemoryType`. Domain knowledge uses existing types (`FACT`, `PREFERENCE`, `PRINCIPLE`, `WORKFLOW_PATTERN`) with structured `metadata` and `tags`. This avoids expanding the type enum and keeps backward compatibility with all existing retrieval/ranking code.
- The domain reflection trigger (task 2-3) is task-level, not turn-level. It runs when `_finalize_task()` sets `task.status = "completed"`, giving the reflector access to the full conversation history for that task. This is complementary to the per-turn `MemoryExtractor` which handles fact/preference/episode extraction.
