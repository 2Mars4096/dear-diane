# 29-6: Self-Evolvement Loop

**Parent:** [29-concierge-memory-evolvement](29-concierge-memory-evolvement.md)
**Status:** in-progress
**Goal:** Close the learning loop with both passive learning (extract and file knowledge) and active adaptation (use accumulated knowledge to improve prompts, model choices, skills, and topology). DAN should not just remember what happened — it should change what it does next time.

## Context

DAN has pieces of a self-evolvement system:
- `ReflectionNode` extracts causal principles from failed runs
- `PrincipleStore` persists principles with confidence scores
- `RuleLifecycleManager` manages rule TTL and pruning
- `ExperienceStore` tracks workflow run stats (success/failure counts)

But these pieces don't form a closed loop. Two halves are missing:

**Passive learning gaps** (knowledge accumulation):
- Principles inform repair but don't improve generation
- Successful patterns aren't extracted as reusable templates
- Preferences are only captured in CLI, not server
- No feedback from "this reuse worked" back to the memory ranking

**Active adaptation gaps** (knowledge application — the bigger miss):
- Prompts never change based on outcomes. After 100 runs of a "review draft" node, the prompt is identical to run #1, even though we have 100 (input, output, outcome) triples.
- Model assignments are static (TierPolicy scores structurally, not empirically). After observing that Claude outperforms GPT-4o for a specific node's task, the system doesn't adapt.
- Skills/hyperedges are static text. A skill that doesn't improve outcomes is never demoted or refined.
- Topology improvements are never suggested proactively. If "adding a validator after the LLM" reduces failures by 40%, the system should learn to suggest that.

After 29-1 (memory kernel), 29-3 (iterative building), and 29-4 (experience reuse), the infrastructure exists. This plan closes both halves of the loop.

## Tasks

### 1. Automatic memory extraction
- [x] 1-1. `MemoryExtractor` class: runs after every concierge interaction
- [x] 1-2. Extract `FACT` items: factual statements the user made or confirmed ("data is CSV", "project deadline is Friday")
- [x] 1-3. Extract `PREFERENCE` items: model preferences, output format preferences, domain preferences, behavioral preferences ("don't ask me to confirm every step")
- [x] 1-4. Extract `EPISODE` items: summarized interaction record (what was asked, what was done, outcome)
- [x] 1-5. LLM-based extraction: lightweight call with structured output schema (fact/preference/episode candidates)
- [x] 1-6. Heuristic fast-path: skip LLM for obvious patterns (explicit "I prefer X", file path mentions, tool usage patterns)
- [x] 1-7. `DAN_MEMORY_EXTRACTION=1` env var (default on), `DAN_MEMORY_EXTRACTION_LLM=0` to disable LLM extraction (heuristic only)

### 2. Post-run learning
- [x] 2-1. After every workflow run (success or failure), `_extract_run_learnings(run_result, workflow, goal_context)`
- [x] 2-2. On success: create/update `WORKFLOW_ASSET` item with updated success_rate, increment importance
- [x] 2-3. On success: if workflow topology matches an existing `WORKFLOW_PATTERN`, reinforce the pattern (increment access_count, boost importance)
- [x] 2-4. On failure: create `FAILURE_PATTERN` item with error category, node type, input characteristics
- [x] 2-5. On failure: check if existing `PRINCIPLE` items apply; if so, increment their confidence
- [x] 2-6. On failure (new pattern): trigger `ReflectionNode`-style analysis to extract new principles
- [x] 2-7. On repair success: link the repair strategy to the failure pattern for future reference

### 3. Cross-section reinforcement
- [x] 3-1. When a `WORKFLOW_ASSET` is successfully reused (29-4), boost its importance and all linked `WORKFLOW_PATTERN` items
- [x] 3-2. When a `PRINCIPLE` prevents a failure (applied during build → no failure), boost its confidence
- [x] 3-3. When a `PREFERENCE` is overridden by the user, demote it (reduce confidence, add override note)
- [x] 3-4. When a `FACT` is contradicted by new information, update it (keep old version in provenance for auditability)
- [x] 3-5. Importance decay: items not accessed for `DAN_MEMORY_DECAY_DAYS` (default 60) lose importance gradually (multiplied by 0.9 per decay period)

### 4. Structural pattern extraction
- [x] 4-1. `PatternExtractor` class: analyzes successful workflow topologies for recurring sub-structures
- [x] 4-2. Run periodically (on consolidation schedule) or when `WORKFLOW_ASSET` count exceeds threshold (default 10)
- [x] 4-3. Compare workflow graphs structurally: node type sequences, branching patterns, loop structures
- [x] 4-4. Extract common sub-graphs as `WORKFLOW_PATTERN` items: "data ingest → analysis → review loop" appears in 5 workflows → extract as named pattern
- [x] 4-5. Extracted patterns are candidates — stored with `lifecycle=ACTIVE`, promoted to `DURABLE` if reused successfully
- [x] 4-6. Link extracted patterns back to source workflows via `related_ids`

### 5. Generation feedback loop
- [x] 5-1. Track generation outcomes: for each workflow generated via intent compiler or codegen, record (method, success/failure, error type, fix needed)
- [x] 5-2. `GenerationStats` model: per-pattern success rates, per-stage-type success rates, common failure modes
- [x] 5-3. Feed generation stats into the intent compiler: patterns with high success rates get priority in coverage checking
- [x] 5-4. Feed generation stats into codegen prompts: include "common mistakes to avoid" derived from failure stats as few-shot negative examples
- [x] 5-5. Feed generation stats into reuse scoring: patterns that generate successfully are less valuable to reuse (generate is cheap); patterns that fail during generation are more valuable to reuse

### 6. Preference evolution
- [x] 6-1. Run `PreferenceExtractor` on every interaction (server + CLI + adapter), not just CLI
- [x] 6-2. Merge extracted preferences into memory kernel as `PREFERENCE` items
- [x] 6-3. Conflict resolution: explicit user statement > inferred from behavior > default
- [x] 6-4. Preference surfacing: periodically (every N sessions) suggest accumulated preferences to user for confirmation: "I've noticed you prefer Claude for writing tasks. Should I always use it?"
- [x] 6-5. Confirmed preferences get maximum importance boost; rejected preferences are archived

### 7. Prompt optimization
- [x] 7-1. `PromptTracker`: after each LLM node execution, record `(node_id, prompt_hash, input_summary, output_summary, outcome, tokens_used, latency)` in memory kernel as structured `EPISODE` items
- [x] 7-2. `PromptAnalyzer`: after N runs of the same node (default 20), analyze (prompt, outcome) pairs to identify which prompt phrasings correlate with success vs. failure
- [x] 7-3. `PromptVariantGenerator`: heuristic variant generation (step-by-step, reinforce-format, simplify) informed by analysis — no LLM needed for v1
- [x] 7-4. A/B testing: `PromptABTest` stores tests as WORKING_STATE, randomly assigns variants (including original baseline), tracks per-variant outcomes
- [x] 7-5. Promotion: after sufficient evidence (default 10 runs per variant), promote the best-performing variant. Old prompt stored in EPISODE provenance for rollback.
- [x] 7-6. Scope: applies to workflow LLM nodes only, not chat system prompts. All classes document this in docstrings.
- [x] 7-7. Safety: `_is_safe_variant()` rejects variants that remove MUST/REQUIRED/schema/DO NOT/NEVER markers or code-fence blocks. Variant generation runs safety check before accepting candidates.
- [x] 7-8. `DAN_PROMPT_OPTIMIZATION=0` env var (default off — opt-in until proven stable)

### 8. Per-node model selection learning
- [x] 8-1. `ModelOutcomeTracker`: record `(node_id, node_type, task_description, model, quality_score, cost, latency)` per execution
- [x] 8-2. Quality scoring heuristic: output schema validation pass rate, downstream node success, normalizer retry count, user acceptance (if human-in-the-loop follows)
- [x] 8-3. After N executions per node (default 15), compute per-model empirical score: `quality_score * weight_quality + (1 - normalized_cost) * weight_cost`
- [x] 8-4. `ModelRecommender.suggest(node_id)` — returns best empirical model for this node, or None if insufficient data
- [x] 8-5. Integration: when `TierPolicy` resolves a model and `ModelRecommender` has a suggestion with sufficient confidence, prefer the empirical recommendation
- [x] 8-6. Override hierarchy: explicit per-node model > empirical recommendation > TierPolicy > default
- [x] 8-7. Store model recommendations as `PREFERENCE` items in memory kernel (scope=WORKFLOW) so they persist across sessions
- [x] 8-8. `DAN_MODEL_LEARNING=0` env var (default off — opt-in)

### 9. Skill and hyperedge evolution
- [x] 9-1. `SkillEffectivenessTracker`: compare node outcomes with vs. without each skill/hyperedge attached
- [x] 9-2. After N runs (default 20), compute effectiveness delta: does the skill measurably improve output quality, reduce retries, or prevent specific error categories?
- [x] 9-3. Ineffective skills (delta < threshold after sufficient runs): flag for review, reduce injection priority
- [x] 9-4. Effective principles → skill promotion: if a `PRINCIPLE` consistently prevents failures (confidence > 0.8, applied > 10 times), promote it to a skill hyperedge for more prominent injection
- [x] 9-5. Skill refinement: heuristic-based (v1, no LLM) analysis of effective vs. ineffective skill applications proposing reworded skill text
- [x] 9-6. Refinement is proposed, not auto-applied. Stored as candidate with `lifecycle=ACTIVE` until validated by usage.
- [x] 9-7. Link skill lineage: original → refined version → further refined, with effectiveness stats at each stage

### 10. Topology learning
- [x] 10-1. `TopologyOutcomeTracker`: record (workflow topology signature, outcome, failure node, failure type) per run
- [x] 10-2. Topology signature: ordered sequence of (node_type, edge_type) pairs, normalized for node naming
- [x] 10-3. After accumulating topology outcomes, identify structural correlations: "workflows with a validator after the LLM node have 40% fewer schema failures"
- [x] 10-4. `TopologyAdvisor.suggest(graph)` — given a workflow graph, suggest structural improvements based on accumulated evidence (e.g., "consider adding a validator after node X")
- [x] 10-5. Suggestions are advisory, presented to the concierge during build sessions (29-3). Not auto-applied.
- [x] 10-6. Track whether suggestions are accepted and whether they improve outcomes → reinforce or demote the suggestion pattern

### 11. Monitoring and observability
- [x] 7-1. `/memory-stats` command: show memory kernel stats (item counts by type, storage size, consolidation last run)
- [x] 7-2. `/memory-search <query>` command: search memory and display results with scores
- [x] 7-3. Log self-evolvement events: memory extraction, reinforcement, decay, pattern extraction
- [x] 7-4. `DAN_EVOLVEMENT_LOG_LEVEL` env var (default INFO)

### 12. Tests
- [x] 12-1. Unit tests for MemoryExtractor (fact, preference, episode extraction)
- [x] 12-2. Unit tests for post-run learning (success path, failure path, repair path)
- [x] 12-3. Unit tests for cross-section reinforcement (boost, demote, decay)
- [x] 12-4. Unit tests for PatternExtractor (structural comparison, sub-graph extraction)
- [x] 12-5. Unit tests for PromptTracker and PromptAnalyzer (outcome recording, correlation analysis)
- [x] 12-6. Unit tests for ModelOutcomeTracker and ModelRecommender (scoring, recommendation)
- [x] 12-7. Unit tests for SkillEffectivenessTracker (delta computation, promotion criteria)
- [x] 12-8. Unit tests for TopologyOutcomeTracker and TopologyAdvisor (signature, correlation, suggestion)
- [x] 12-9. Integration test: 5 similar workflows built → pattern extracted → 6th build reuses pattern
- [x] 12-10. Integration test: preference extracted from conversation → applied in next build → user confirms
- [x] 12-11. Integration test: 20 runs of same node → prompt variant generated → A/B tested → best promoted
- [x] 12-12. Integration test: model recommendation overrides TierPolicy after sufficient evidence

## Decisions

- (to be filled during execution)

## Notes

- LLM-based memory extraction adds latency and cost to every interaction. The heuristic fast-path handles the common cases cheaply; LLM extraction is for subtle inferences. Users can disable LLM extraction entirely.
- Pattern extraction is computationally expensive (graph comparison). It runs on the consolidation schedule, not on every interaction.
- The generation feedback loop (task 5) is the most speculative part. It requires enough generation data to be statistically meaningful. Start with simple counting and add sophistication as data accumulates.
- Importance decay prevents the memory from growing unboundedly. Unused items naturally fade. Actively used items stay important.
- **Active adaptation (tasks 7-10) is opt-in.** All four mechanisms are off by default (`DAN_PROMPT_OPTIMIZATION=0`, `DAN_MODEL_LEARNING=0`, etc.). They require sufficient run data to be meaningful and should be validated incrementally.
- Prompt optimization (task 7) is inspired by DSPy and AFlow (ICLR 2025). The key insight: after N runs we have (prompt, input, output, outcome) triples — that's a training signal for prompt improvement.
- Model selection learning (task 8) is distinct from parameter tuning. We are not tuning temperature/max_tokens — we are learning which model works best for which task based on empirical outcomes.
- Topology learning (task 10) is the most ambitious. It starts advisory-only. The concierge presents suggestions; the user decides. Auto-application is future work.
- 2026-03-09 (d): Implemented monitoring commands (§11). `/memory-stats` and `/memory-search` in concierge runtime. `DAN_EVOLVEMENT_LOG_LEVEL` env var controls `dan.evolvement` logger for store/consolidation events. 14 tests added.
- 2026-03-09 (e): Implemented prompt optimization pipeline (§7-2..7-7). `PromptAnalyzer` (threshold-gated analysis of prompt/outcome pairs), `PromptVariantGenerator` (heuristic: step-by-step, reinforce-format, simplify), `PromptABTest` (create/select/record/check_promotion/promote with original baseline tracking), `_is_safe_variant()` safety check, all gated by `DAN_PROMPT_OPTIMIZATION=1`. 53 tests in `tests/test_engine/test_prompt_optimization.py`.
- 2026-03-09 (e): Implemented LLM-based memory extraction (§1-5) and reflection-based principle extraction for new failure patterns (§2-6). `extract_with_llm()` uses OpenAI-compatible API gated by `DAN_MEMORY_EXTRACTION_LLM=1` (default off); falls back to heuristic when no API key or on error. `_parse_llm_response()` handles JSON/markdown-fenced output. `_maybe_extract_new_principle()` checks existing principles; if no coverage for the error category, creates a low-confidence (0.3) principle with workflow context. Wired into both sync `_on_failure()` and async `extract_run_learnings_async()`. 21 new tests.
- 2026-03-09 (e): Implemented skill/hyperedge evolution (§9). `SkillEffectivenessTracker` records per-execution skill telemetry and computes effectiveness deltas (with vs. without). `SkillPromoter` promotes high-confidence principles to skill hyperedges. `SkillRefiner` proposes heuristic refinements with lineage tracking via `related_ids`. All opt-in (`DAN_SKILL_LEARNING=1`). 18 tests.
