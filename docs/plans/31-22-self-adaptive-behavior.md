# 31-22: Self-Adaptive Behavior

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** not-started
**Goal:** Make DAN's own prompts, thresholds, classifiers, domain ontology, and tool descriptions the subject of its learning loop — observable by default (tier 0), advisable at tier 1, self-tuning at tier 2 — with a declarative parameter taxonomy, evidence infrastructure, and full audit trail.

## Problem

DAN has a complete learning infrastructure — `AdaptationRegistry` (31-15), `PromptTracker` / `ModelOutcomeTracker` (29-6), `CorrectionStore` (31-15), `TelemetryStore` (31-20), `DomainTemplate` evolution (31-21) — but it's all pointed at **user workflows**, not at DAN's own behavior. The ~50 prompts, ~30 thresholds, ~15 enums, 6 domain keyword maps, and 30-tool catalog are all frozen Python constants. A codebase audit identified six layers of hardcoded behavior:

1. **Prompts (~50)** — `UNIFIED_SYSTEM_PROMPT`, `_CLASSIFICATION_SYSTEM_PROMPT`, `CodegenPromptBuilder._SYSTEM_PROMPT`, etc. All are string constants in `.py` files. No versioning, no A/B testing, no outcome-driven selection despite `PromptTracker` being fully built.
2. **Thresholds/weights (~30)** — reuse cutoffs (0.8/0.4), classifier confidence (0.85), memory ranking weights (0.3+0.4+0.2...), tier promotion gates (95%/100+), context resolver match scores (0.45/0.55). Hand-tuned, never calibrated from telemetry.
3. **Intent taxonomy** — `IntentCategory` enum with 10 fixed categories. New intent patterns (scheduling, project management, computer use) required code changes each time.
4. **Domain ontology** — `_DOMAIN_KEYWORDS` dict with 6 domains in Python code, despite `DomainTemplate` already supporting JSON-based templates with LLM-assisted evolution.
5. **Tool descriptions** — `CAPABILITY_TOOLS_REFERENCE` is a hand-written prompt block duplicating `TOOL_METADATA` from each tool module. Drifts when tools are added/removed.
6. **Model configurations** — `DEFAULT_TIER_MAPS`, `COST_PER_1K_TOKENS` tables in Python, stale when new models ship.

### What exists and where this extends it

| Existing | What it does | Gap this plan fills |
|----------|-------------|---------------------|
| `AdaptationRegistry` (31-15) | Propose/apply/rollback candidates with regression detection | Not wired to DAN's own prompts, thresholds, or taxonomy. Only receives candidates from workflow learning paths. |
| `PromptTracker` (29-6) | Records prompt variants and outcomes per node | Tracks user workflow prompts, not DAN's own system prompts. |
| `ModelOutcomeTracker` (29-6) | Tracks model success rates per node type | Doesn't feed back into tier map selection. |
| `CorrectionStore` (31-15) | Captures user corrections with confidence scoring | Produces preferences/principles, but doesn't adjust the thresholds/weights that caused the bad behavior. |
| `TelemetryStore` (31-20) | Logs every chat turn, tool call, classification with outcomes | Rich outcome data, but no consumer that calibrates internal parameters from it. |
| `DomainTemplate` seed/override (31-21) | JSON seed → user override → LLM upgrade → versioned backup | Pattern only applied to domain templates, not generalized to all behavior artifacts. |
| `LearningHealthCounters` (31-15) | Tracks attempted/succeeded/failed per learning path | Safety gate exists but only guards workflow-level adaptations. |

## Design Principles

1. **Auto-discover, verbose.** Every behavioral change is logged with human-readable explanation and evidence. User sees what changed on all surfaces. `/revert` undoes it.
2. **Observability is the default, adaptability is earned.** Tier 0 collects parameter-outcome evidence silently — no proposals, no changes. Tier 1 surfaces proposals with evidence for user approval. Tier 2 auto-applies bounded changes with regression detection. A parameter graduates from "observable" to "adaptable" only when it has enough evidence samples and a clean rollback path.
3. **Audit trail.** Every change is a versioned entry in `BehaviorChangeLog` with before/after values, evidence that triggered it, and a revert command.
4. **Seed → Override → Learn → Revert.** All behavior artifacts follow the same lifecycle: ship seed defaults in code → user can override in `~/.dan/` → learning loop proposes changes → user can revert any change.
5. **Single source of truth.** Eliminate dual representations (e.g., tool descriptions in both `TOOL_METADATA` and `CAPABILITY_TOOLS_REFERENCE`).

## Adaptable Parameter Taxonomy

Not all parameters are equal. Each category has different evidence requirements, risk profiles, and adaptation mechanics. The `AdaptableParameter` registry makes this declarative — adding a new adaptable dimension is registering an entry, not writing new code.

### Parameter categories

| Category | Key prefix | Examples | Risk | Evidence type | Min samples |
|----------|-----------|----------|------|---------------|-------------|
| **Thresholds** | `heuristics/` | reuse cutoff (0.8), classifier confidence (0.85), match scores | Low | `parameter_decision` telemetry: {param_key, param_value, decision, outcome} | 10 |
| **Prompts** | `prompts/` | system prompt, classifier prompt, codegen prompt | Medium | Correction attribution: which prompt was active when a correction fired | 20 |
| **Taxonomy** | `taxonomy/` | intent categories, handler mappings | High | `pattern_accumulation`: unrecognized request frequency + keyword clusters | 5 occurrences |
| **Domain ontology** | `domains/` | keyword maps, extraction templates | Medium | Domain reflection (31-21) + task success/failure per domain | 3 tasks |
| **Model config** | `models/` | tier maps, cost tables | Medium | `ModelOutcomeTracker`: success/cost/latency per model per task type | 20 runs |
| **Retrieval policy** | `heuristics/memory_ranking.*` | per-type ranking weights, budget allocations | Low | Correlation: retrieved memory type vs. outcome acceptance (no correction) | 15 |

### Adaptation lifecycle per category

Every parameter follows: **Innate → Observable → Advisable → Self-tuning**

| State | What happens | Tier |
|-------|-------------|------|
| **Innate** | Hardcoded, never adapts. Safety bounds, calibration limits, revert mechanism, graph IR. | — |
| **Observable** | System collects parameter-outcome evidence. No proposals, no changes. Evidence accrues silently. | 0 |
| **Advisable** | System generates `AdaptationCandidate` with evidence and rollback path. User sees via `/adaptations`, can `/approve` or `/reject`. | 1 |
| **Self-tuning** | System auto-applies bounded changes (±20% for thresholds, append-only for prompts, additions-only for taxonomy). Regression detection auto-reverts on >15% quality drop. | 2 |

### What never adapts (innate)

| Parameter | Why |
|-----------|-----|
| Calibration bounds (±20% step limit) | The bound on self-modification cannot itself be self-modified |
| Regression detection threshold (15%) | Safety gate for all adaptations |
| Measurement window (10-20 interactions) | Minimum evidence before any decision |
| `BehaviorStore` lifecycle | Self-modifying the self-modifier has no fixed point |
| Graph IR schema, engine execution | External contract — every workflow depends on it |
| PII rules, cost limits, destructive-action confirmation | Safety guardrails |

### Evidence infrastructure requirements

Each category needs specific telemetry to become adaptable:

1. **Parameter-decision events** (new `EventType`): extend `TelemetryEvent` with optional `parameter_key: str | None` and `parameter_value: str | None`. Emitted at every decision point where a threshold/weight is consulted. Zero cost when unused.
2. **Prompt-scoped correction attribution**: when `detect_correction()` fires, trace back which system prompt was active for the LLM call that produced the corrected output. Link the `CorrectionRecord` to the `BehaviorStore` prompt key.
3. **Pattern accumulator**: `BehaviorStore` counter under `taxonomy/unrecognized_patterns` and `domains/unrecognized_clusters`. Incremented when classifier/domain-detector falls through; clustered by keyword overlap.
4. **Adaptation outcome tracking**: after any `AdaptationCandidate` is applied, measure quality (correction rate, task success rate) over the next N interactions scoped to that parameter. Record delta vs. baseline in `AdaptationCandidate.last_outcome`.
5. **Retrieval-outcome correlation**: on each response, record which `MemoryType` items were retrieved and whether the response was accepted. Stored as lightweight counters in `BehaviorStore` under `heuristics/retrieval_correlation.*`.

## Tasks

### 1. Behavior Store — unified externalization layer

- [ ] 1-1. `BehaviorArtifact` Pydantic model: `key: str`, `value: Any`, `version: int`, `evidence: list[str]`, `updated_at: float`, `previous_versions: list[dict]` (each with `value`, `version`, `updated_at`, `reason`). Max 10 previous versions retained for rollback.
- [ ] 1-2. `BehaviorStore` class in new `src/dan/engine/behavior_store.py`: manages `~/.dan/behavior/` directory. Methods: `get(key, default)`, `set(key, value, reason, evidence)`, `revert(key, version)`, `list_keys()`, `list_changes(since_hours)`. Each key is a JSON file: `~/.dan/behavior/<category>/<key>.json`. Categories: `prompts`, `heuristics`, `taxonomy`, `domains`, `models`.
- [ ] 1-3. Seed defaults: `BehaviorStore` constructor accepts `seed_defaults: dict[str, Any]`. On first access, if no user file exists for a key, the seed value is returned without writing a file. Only writes on first mutation.
- [ ] 1-4. Hot-reload: `BehaviorStore.reload(key)` re-reads from disk. `reload_all()` for startup. File watcher is NOT needed — reload happens on `get()` if file mtime changed since last read (stat-based, no inotify).
- [ ] 1-5. Thread safety: `threading.RLock` per category (not global). Same pattern as `MemoryKernel`.
- [ ] 1-6. `AdaptableParameter` Pydantic model: `key: str`, `category: Literal["thresholds", "prompts", "taxonomy", "domains", "models", "retrieval_policy"]`, `evidence_type: Literal["parameter_decision", "correction_attribution", "pattern_accumulation", "model_outcome", "retrieval_correlation"]`, `min_evidence_count: int`, `risk_level: Literal["low", "medium", "high"]`, `bounds: dict | None` (for thresholds: `{min, max, max_step_pct}`), `description: str`. Registered declaratively — adding a new adaptable parameter is one `registry.register()` call.
- [ ] 1-7. `AdaptableParameterRegistry` in `behavior_store.py`: `register(param)`, `get_by_key(key)`, `list_by_category(cat)`, `list_by_tier(tier)`, `has_sufficient_evidence(key) -> bool`. Seed registrations for all extracted parameters happen during `register_seed_prompts()` / `register_seed_heuristics()` startup calls.
- [ ] 1-8. Evidence sufficiency check: `has_sufficient_evidence(key)` queries the relevant evidence source (TelemetryStore, CorrectionStore, pattern counters) and returns True only when `sample_count >= min_evidence_count`. No proposal can be generated for a parameter that hasn't reached its evidence threshold.

### 2. Prompt externalization

- [ ] 2-1. Extract all prompts from their current locations into `BehaviorStore` registration calls. Each prompt gets a `prompts/<module>.<name>` key. Example: `prompts/classifier.classification_system` for `_CLASSIFICATION_SYSTEM_PROMPT` in `classifier.py`. Registration happens at module level, not import time — a `register_seed_prompts(store)` function called during app startup.
- [ ] 2-2. Prioritized extraction order (by adaptation value):
  - **Tier 1 — concierge decision prompts** (highest ROI, directly affect user experience): `_CLASSIFICATION_SYSTEM_PROMPT`, `_SOLVER_SYSTEM_PROMPT`, `_LLM_EXTRACT_PROMPT`, `_LLM_VERIFY_PROMPT`, `PREFLIGHT_QUESTION_PROMPT_TEMPLATE`, `RESULT_SUMMARY_PROMPT_TEMPLATE`.
  - **Tier 2 — main system prompts**: `UNIFIED_SYSTEM_PROMPT`, `SYSTEM_PROMPT_TEMPLATE`, `BUILD_FROM_INTENT_PROMPT`, `SURFACE_HINTS`.
  - **Tier 3 — generation/repair prompts**: `CodegenPromptBuilder._SYSTEM_PROMPT`, `PlanningPromptBuilder.SYSTEM_TEMPLATE`, `StructuralRepairPlanner._SYSTEM_PROMPT`, `INTENT_EXTRACTION_SYSTEM_PROMPT`.
  - **Tier 4 — executor prompts**: `_DEFAULT_REFLECTION_PROMPT`, router/judge/moderator prompts, memory extraction prompt.
- [ ] 2-3. Each prompt consumer changes from `PROMPT_CONSTANT` to `behavior_store.get("prompts/classifier.classification_system", default=PROMPT_CONSTANT)`. The seed constant stays in the source file as the fallback — no file on disk required for the system to work.
- [ ] 2-4. Prompt versioning: `BehaviorStore.set("prompts/classifier.classification_system", new_value, reason="Added scheduling intent recognition", evidence=["5/7 scheduling requests misclassified"])` writes a new version with the previous value in `previous_versions`.

### 3. Threshold and weight externalization

- [ ] 3-1. Extract all thresholds/weights into `BehaviorStore` under `heuristics/` keys. Each gets a structured entry: `{"value": 0.85, "min_bound": 0.5, "max_bound": 0.99, "description": "LLM classifier skip threshold"}`. Bounds prevent the calibration loop from producing degenerate values.
- [ ] 3-2. Priority extraction list (by calibration value):
  - **Classifier**: `_FAST_PATH_CONFIDENCE` (0.85), `_COMPLETION_CHECK_THRESHOLD` (env-override exists)
  - **Context resolver**: project match (0.45), task match (0.55)
  - **Reuse decision**: reuse (0.8), adapt (0.4), min_success_rate (0.5), domain boost (0.15)
  - **Entity grounding**: confidence (0.6), goal similarity (0.15), solver confidence (0.7)
  - **Memory ranking**: all weights in `_rank_preference`, `_rank_fact`, `_rank_workflow_pattern`, `_rank_workflow_asset`, `_rank_failure_pattern`, `_rank_principle`, `_rank_episode` — packaged as `heuristics/memory_ranking.<type>` with per-coefficient entries
  - **Consolidation**: decay days (60), promote hours (24), archive days (30), decay factor (0.9)
  - **Continuity**: handoff threshold (0.3)
  - **Progress UX**: anti-noise threshold (3.0s), heartbeat threshold (300s), plan review step threshold (3)
- [ ] 3-3. Consumer code changes from `THRESHOLD = 0.85` to `behavior_store.get("heuristics/classifier.fast_path_confidence", default={"value": 0.85, ...})["value"]`. Performance: `get()` returns cached value unless file mtime changed — no disk I/O on hot path.

### 4. Evidence infrastructure — parameter-outcome telemetry

- [ ] 4-1. Extend `TelemetryEvent` with optional `parameter_key: str | None = None` and `parameter_value: str | None = None` fields. Emitted at decision points where a threshold/weight is consulted (classifier confidence check, reuse decision, context match scoring). Zero overhead when unused — fields default to `None` and are not indexed.
- [ ] 4-2. `ParameterDecisionLogger` utility in `behavior_store.py`: wraps `TelemetryStore.record()` with parameter context. Usage: `param_logger.log_decision("heuristics/classifier.fast_path_confidence", 0.85, decision="skip_llm", outcome="accepted")`. Called from each threshold consumer after the decision is made and the outcome is known.
- [ ] 4-3. Prompt-scoped correction attribution: extend `CorrectionRecord` (or its metadata) with `active_prompt_key: str | None`. When `detect_correction()` fires in `_process_inner`, look up which `BehaviorStore` prompt key was used for the LLM call that produced the corrected output. Store the key so `PromptTracker` can attribute the failure.
- [ ] 4-4. Pattern accumulator: `PatternAccumulator` class in `behavior_store.py`. Maintains counters under `taxonomy/unrecognized_patterns` and `domains/unrecognized_clusters` in `BehaviorStore`. Methods: `record_unrecognized(text, keywords, category)`, `get_clusters(category, min_count)`. Clustering uses `_keyword_overlap` (existing utility from 31-21). Incremented when classifier falls through to `CONVERSATION` with low confidence, or when `detect_domain()` returns `None`.
- [ ] 4-5. Retrieval-outcome correlation counters: after each response in `_post_process_response`, record which `MemoryType` items were retrieved (from `_memory_context`) and whether the response was accepted (no correction detected in the next turn). Stored as lightweight `{type: {retrieved: N, accepted: M}}` counters in `BehaviorStore` under `heuristics/retrieval_correlation`. Updated incrementally, not per-item.
- [ ] 4-6. Adaptation outcome tracker: extend `AdaptationRegistry` with `record_post_adaptation_outcome(id, quality_metric, interaction_count)`. After an `AdaptationCandidate` is applied, the runtime records quality (correction rate, task success rate) over the next N=20 interactions scoped to that parameter. The delta vs. baseline is stored in `AdaptationCandidate.last_outcome` and feeds `check_regression()`.
- [ ] 4-7. Tier 0 wiring: all evidence collection (4-1 through 4-6) is enabled at tier 0. No gating, no env var override needed. Evidence accrues silently from the first interaction.

### 5. Threshold self-tuning loop

- [ ] 5-1. `ThresholdCalibrator` class in `behavior_store.py`: consumes `parameter_decision` telemetry events (task 4-1) to propose threshold adjustments. Runs periodically (after every N=50 relevant events, or on explicit `/calibrate` command). **Tier gating: observation at tier 0, proposals at tier 1, auto-apply at tier 2.**
- [ ] 5-2. Calibration logic per threshold type:
  - **Classifier confidence**: query `parameter_decision` events for `heuristics/classifier.fast_path_confidence`. For cases where confidence was between 0.70 and current threshold and the LLM classifier agreed → threshold could be lower. For cases where confidence was above threshold but outcome was a correction → threshold should be higher. Propose new value = weighted median of "correct decision" confidences.
  - **Reuse/adapt/generate cutoffs**: query `parameter_decision` events for `heuristics/reuse.*`. If ADAPT decisions in the 0.4-0.8 range succeed >80% → propose lowering reuse threshold. If ADAPT decisions fail >40% → propose raising adapt threshold.
  - **Memory ranking weights**: use retrieval-outcome correlation counters (task 4-5). Adjust weights toward `MemoryType`s that correlate with acceptance. Propose via `AdaptationRegistry`.
- [ ] 5-3. Safety bounds: no single calibration step changes a value by more than ±20% of the current value. Minimum samples per `AdaptableParameter.min_evidence_count` (default 10). All changes go through `AdaptationRegistry` with source `"threshold_cal"`. Evidence sufficiency checked via `AdaptableParameterRegistry.has_sufficient_evidence()`.
- [ ] 5-4. Tier behavior:
  - **Tier 0**: `ThresholdCalibrator` runs but only logs what it *would* propose. No `AdaptationCandidate` created. Evidence accrues.
  - **Tier 1**: `ThresholdCalibrator` creates `AdaptationCandidate` with status `"pending"`. Surfaced via `/adaptations`. User must `/approve` to apply.
  - **Tier 2**: `ThresholdCalibrator` creates `AdaptationCandidate` with `auto_apply=True`. Applied immediately within bounds. `BehaviorChangeLog` entry written. Regression detection active.
- [ ] 5-5. Verbose output: calibration results logged as `BehaviorChangeLog` entries and surfaced in `/changes`. Example: `"Reuse threshold adjusted: 0.80 → 0.74. Evidence: 12/15 ADAPT decisions in [0.74, 0.80] range succeeded. /revert t3f2 to undo."`

### 6. Prompt self-tuning integration

- [ ] 6-1. Extend existing `PromptTracker` to also track DAN's own prompts (not just user workflow node prompts). Add a `scope: Literal["workflow", "system"]` field to prompt tracking records. System-scope records use the `BehaviorStore` prompt key as the identifier.
- [ ] 6-2. Wire prompt-scoped correction attribution (task 4-3): when `CorrectionStore` detects a correction, use the `active_prompt_key` to record negative evidence against that prompt version in `PromptTracker`.
- [ ] 6-3. Prompt variant proposal: after N=20 negative signals (per `AdaptableParameter.min_evidence_count`) against a system prompt, `PromptTracker` generates a candidate via `AdaptationRegistry` with source `"prompt_opt"`. The candidate includes: the current prompt, the failure cases, and a description of what should change.
- [ ] 6-4. Tier behavior:
  - **Tier 0**: `PromptTracker` records system-scope effectiveness data silently. No proposals.
  - **Tier 1**: After evidence threshold met, propose variant via `AdaptationCandidate` with status `"pending"`. User `/approve`s. Only additive changes (append-only, no deletion) proposed at this tier.
  - **Tier 2**: Structural rewrites also eligible. LLM-assisted prompt improvement: call the LLM with the current prompt + failure cases → proposed minimal edit. 1 LLM call per proposal. Auto-applied if `risk_level` is not `"high"`. `AdaptationRegistry` governs lifecycle.
- [ ] 6-5. Regression detection: after applying a prompt variant, measure the next 20 interactions (via adaptation outcome tracker, task 4-6). If correction rate increases by >15% vs. baseline → auto-revert via `AdaptationRegistry.check_regression()`.

### 7. Dynamic intent taxonomy

- [ ] 7-1. Move `IntentCategory` from a fixed `str, Enum` to a registry-backed extensible set. Core intents (the current 10) remain as seed defaults in `BehaviorStore` under `taxonomy/intent_categories`. The `IntentCategory` enum stays in code for type safety on the core set; extended intents use string values.
- [ ] 7-2. Intent frequency tracking: wired into the `PatternAccumulator` (task 4-4). When `classify_intent_llm()` returns `CONVERSATION` with low confidence (< 0.7), or the heuristic classifier falls through all rules, `pattern_accumulator.record_unrecognized(text, keywords, "intent")` is called.
- [ ] 7-3. Tier behavior:
  - **Tier 0**: `PatternAccumulator` records unrecognized patterns silently. No proposals.
  - **Tier 1**: When an unrecognized pattern accumulates 5+ occurrences with similar keyword clusters (overlap > 0.6), propose a new intent category via `AdaptationCandidate` with status `"pending"`. User `/approve`s. Proposal includes: suggested name, example messages, suggested handler mapping.
  - **Tier 2**: Auto-promoted when promotion gate passes. Classifier prompt automatically extended (via task 6). Verbose notification sent.
- [ ] 7-4. Handler wiring: new intents initially map to the `DIRECT_TASK` handler (safe default). The classifier prompt is automatically updated (via task 6) to include the new intent in its valid set. The user or a future adaptation can assign a specialized handler.
- [ ] 7-5. Classifier prompt co-evolution: when a new intent is added to the taxonomy, the classifier's `_CLASSIFICATION_SYSTEM_PROMPT` is automatically extended with the new category name and a one-line description. This uses the prompt externalization (task 2) — no code change needed, just a `BehaviorStore.set()` on the prompt key.

### 8. Domain auto-discovery

- [ ] 8-1. Move `_DOMAIN_KEYWORDS` from `domain_learning.py` to `BehaviorStore` under `domains/keyword_maps`. Seed values are the current 6 domains. `detect_domain()` loads from `BehaviorStore` instead of the module constant.
- [ ] 8-2. Domain emergence: wired into the `PatternAccumulator` (task 4-4). When `detect_domain()` returns `None`, `pattern_accumulator.record_unrecognized(text, keywords, "domain")` is called.
- [ ] 8-3. Tier behavior:
  - **Tier 0**: `PatternAccumulator` records unrecognized domain patterns silently.
  - **Tier 1**: When a project accumulates 3+ tasks with consistent keyword clusters (via `get_clusters("domain", 3)`), propose a new domain via `AdaptationCandidate` with status `"pending"`. User `/approve`s.
  - **Tier 2**: Auto-bootstrapped when emergence gate passes. Creates generic `DomainTemplate` (existing `create_generic_template()` from 31-21), registers keyword map in `BehaviorStore`, sends verbose notification. Example: `"[Discovery] New domain detected: 'kaggle_competition' (keywords: kaggle, submission, leaderboard, oof, ensemble). Template created. /revert d2c1 to remove."`
- [ ] 8-4. Existing domain keyword expansion: when `detect_domain()` matches a domain but with marginal score (only 1 keyword hit), and the task succeeds, propose adding new keywords. Gate: only additions, never removals. Max 3 new keywords per expansion. Tier 1: proposed. Tier 2: auto-applied.

### 9. Tool description single-source generation

- [ ] 9-1. `generate_capability_reference()` function in `chat_manager.py`: iterates `get_all_tools()` from `dan.tools.__init__`, reads each tool's `TOOL_METADATA` (already contains `tool_id`, `description`, `parameters`, `category`), and formats the reference block programmatically. Groups by category (File, Web, System, Communication, Text, Lookup, Run control, Publish, Browse).
- [ ] 9-2. MCP tool inclusion: `get_mcp_tool_hint()` output is appended to the generated reference (already produces a formatted block).
- [ ] 9-3. Runtime-authored tools: tools registered via `19-6` runtime authoring are included in the generated reference automatically — they already appear in the tool registry.
- [ ] 9-4. Replace the static `CAPABILITY_TOOLS_REFERENCE` constant with a call to `generate_capability_reference()` during `_build_system_content()`. Cache the result per startup (tools don't change mid-session for built-ins; MCP tools re-generate on reconnect).
- [ ] 9-5. Remove the hand-written `CAPABILITY_TOOLS_REFERENCE` string constant. The seed default in `BehaviorStore` is the generated output, not a hand-maintained string.

### 10. Model configuration externalization

- [ ] 10-1. Move `DEFAULT_TIER_MAPS` and `COST_PER_1K_TOKENS` to `BehaviorStore` under `models/tier_maps` and `models/cost_table`. Seed values are the current Python dicts.
- [ ] 10-2. `resolve_tier_map()` reads from `BehaviorStore` instead of the module constant. User override (existing `user_override` parameter) takes precedence over stored values.
- [ ] 10-3. Model tier self-tuning: wire `ModelOutcomeTracker.analyze()` into `AdaptationRegistry`. Tier behavior:
  - **Tier 0**: `ModelOutcomeTracker` records success/cost/latency data per model per task type. No proposals.
  - **Tier 1**: When a model at a tier consistently succeeds (>90% over 20+ runs per `AdaptableParameter.min_evidence_count`), propose a tier map change via `AdaptationCandidate` with status `"pending"`. User `/approve`s.
  - **Tier 2**: Auto-applied. Example: `"[Adaptation] Model tier 'routine' updated: claude-sonnet-4-6 → claude-3-5-haiku for node type 'router'. Evidence: 24/25 router calls succeeded with haiku at 60% lower cost. /revert m1a3 to undo."`
- [ ] 10-4. Cost table refresh: when a model string in `COST_PER_1K_TOKENS` is not found during cost calculation, log a warning and use 0. The user can update via `set_config` or by editing `~/.dan/behavior/models/cost_table.json`.

### 11. Behavior Change Log and user-facing commands

- [ ] 11-1. `BehaviorChangeLog` class in `behavior_store.py`: append-only log of all self-modifications. Stored at `~/.dan/behavior/changelog.jsonl`. Fields per entry: `id: str`, `timestamp: float`, `category: str` (prompts/heuristics/taxonomy/domains/models), `key: str`, `action: str` (set/revert/calibrate/discover), `before_summary: str` (first 200 chars of old value), `after_summary: str` (first 200 chars of new value), `evidence: list[str]`, `source: str` (threshold_cal/prompt_opt/intent_discovery/domain_discovery/model_rec/user).
- [ ] 11-2. `/changes` command: list recent behavioral adaptations. Default: last 24h. `--all` for full history. `--category <cat>` to filter. Output format: one line per change with id, timestamp, category, key, action, reason. Registered in command registry.
- [ ] 11-3. `/revert <id>` command: roll back a specific change. Reads `BehaviorChangeLog` entry, calls `BehaviorStore.revert(key, version)`, appends a revert entry to the changelog, notifies user with before/after summary. Registered in command registry.
- [ ] 11-4. Verbose surface notifications: when `BehaviorStore.set()` is called by a learning subsystem (not by the user), emit a notification to the active surface(s). Format: `"[Adaptation] <category>/<key> updated. <reason>. /revert <id> to undo."` Uses existing `NotificationManager` for non-active surfaces. For active surfaces, appended as a footnote to the next response (same pattern as domain validation warnings in 31-21).
- [ ] 11-5. `/behavior` command: inspect current behavior state. `--key <key>` shows current value, version, and history. `--seeds` compares current values to seed defaults and shows all deviations. `--reset <key>` restores seed default (writes a revert entry).
- [ ] 11-6. `/adaptations` command: list pending adaptation proposals. Shows evidence, proposed change, and `/approve <id>` or `/reject <id>` actions. Only visible at tier 1+.

### 12. Tiered adaptation lifecycle

- [ ] 12-1. Redefine `_TIER_FEATURES` in `learning_tiers.py`:
  - **Tier 0** (observe): existing features + `parameter_outcome_tracking` + `prompt_effectiveness_logging` + `pattern_accumulation` + `retrieval_correlation_tracking`. All evidence infrastructure (task 4) is tier 0. No proposals, no changes.
  - **Tier 1** (advise): existing features + `threshold_proposal` + `prompt_variant_proposal` + `intent_discovery_proposal` + `domain_discovery_proposal` + `model_tier_proposal`. Proposals surface via `/adaptations`. User approves.
  - **Tier 2** (auto-apply): existing features + `threshold_calibration` + `prompt_selection` + `prompt_rewrite` + `intent_auto_promotion` + `domain_auto_discovery` + `model_tier_auto_tuning`. Bounded auto-application with regression detection and auto-revert.
- [ ] 12-2. `_FEATURE_ENV_OVERRIDES` additions: `parameter_outcome_tracking: DAN_PARAM_TRACKING`, `threshold_proposal: DAN_THRESHOLD_PROPOSAL`, `threshold_calibration: DAN_THRESHOLD_CALIBRATION`, `prompt_variant_proposal: DAN_PROMPT_PROPOSAL`, `prompt_selection: DAN_PROMPT_SELECTION`, `intent_discovery_proposal: DAN_INTENT_DISCOVERY`, `domain_discovery_proposal: DAN_DOMAIN_DISCOVERY`, `model_tier_proposal: DAN_MODEL_TIER_PROPOSAL`.
- [ ] 12-3. Safety invariants (innate, never self-modified):
  - Max ±20% per threshold calibration step
  - Append-only for prompt changes at tier 1; structural rewrites only at tier 2
  - Additions-only for taxonomy/domain (never removes)
  - Max 3 new keywords per domain expansion
  - Minimum evidence per `AdaptableParameter.min_evidence_count` before any proposal
  - Regression detection: >15% quality drop → auto-revert
  - Measurement window: 10-20 interactions post-adaptation
- [ ] 12-4. `check_tier_promotion_gates()` updated for 1 → 2 promotion:
  - Gate A: health >= 95% on 100+ events (existing)
  - Gate B: at least 5 tier-1 proposals approved by user with net positive outcome
  - Gate C: no false-positive adaptations in last 50 interactions
  - Gate D: `AdaptableParameterRegistry` shows >= 3 parameter categories with sufficient evidence

### 13. AdaptationRegistry extensions

- [ ] 13-1. Add `AdaptationSource` values: `"threshold_cal"`, `"intent_discovery"`, `"domain_discovery"`, `"model_tier"`, `"tool_ref"` alongside existing `"prompt_opt"`, `"model_rec"`, `"topology_adv"`, `"skill_ref"`, `"principle"`.
- [ ] 13-2. `AdaptationCandidate` gains `before_value: str | None`, `after_value: str | None`, and `parameter_key: str | None` fields for audit and linking to `AdaptableParameterRegistry`.
- [ ] 13-3. Wire `AdaptationRegistry` to `BehaviorChangeLog`: every `approve()` or auto-apply writes a changelog entry. Every `rollback()` writes a revert entry.
- [ ] 13-4. Active adaptation scope enforcement: max 1 active adaptation per `(category, key)` pair. If a new candidate arrives for a key with an active adaptation still being measured, queue it.
- [ ] 13-5. Adaptation outcome measurement: after `approve()` or auto-apply, start a measurement window (N interactions from `AdaptableParameter.min_evidence_count`). Quality is tracked via `record_post_adaptation_outcome()` (task 4-6). At window end, `check_regression()` runs automatically.

### 14. Tests and docs

- [ ] 14-1. Unit tests: `BehaviorStore` CRUD, versioning, revert, seed defaults, mtime-based cache, thread safety.
- [ ] 14-2. Unit tests: `AdaptableParameterRegistry` — registration, evidence sufficiency check, list by category/tier.
- [ ] 14-3. Unit tests: `ParameterDecisionLogger` — telemetry event emission with parameter context.
- [ ] 14-4. Unit tests: `PatternAccumulator` — recording, clustering, threshold gates.
- [ ] 14-5. Unit tests: `ThresholdCalibrator` — mock telemetry outcomes → proposed adjustments within bounds. Verify tier 0 logs-only, tier 1 proposes, tier 2 auto-applies.
- [ ] 14-6. Unit tests: `generate_capability_reference()` — generates from tool metadata, includes MCP tools, matches expected format.
- [ ] 14-7. Unit tests: intent taxonomy extension — frequency tracking, promotion gate, classifier prompt co-evolution.
- [ ] 14-8. Unit tests: domain auto-discovery — keyword clustering, new domain proposal, keyword expansion.
- [ ] 14-9. Integration test: correction on classifier output → prompt variant proposed (tier 1) → user approves → next classification uses updated prompt → no regression.
- [ ] 14-10. Integration test: 50 parameter_decision events with reuse decisions → `ThresholdCalibrator` proposes adjustment (tier 1) → user approves → `/changes` shows entry → `/revert` restores original. Same test at tier 2: auto-applied.
- [ ] 14-11. Integration test: 5 unrecognized intent patterns → new intent proposed (tier 1) → user approves → classifier prompt updated → next similar message classified correctly.
- [ ] 14-12. Integration test: 3 tasks in new domain → domain proposed (tier 1) → user approves → keyword map and template created → next task in domain gets expertise injection.
- [ ] 14-13. Integration test: tier 0 → tier 1 → tier 2 lifecycle: evidence accrues at tier 0, proposals appear at tier 1, auto-apply activates at tier 2.
- [ ] 14-14. `/changes`, `/revert`, `/behavior`, `/adaptations` command tests.
- [ ] 14-15. Updated `.env.example` with new env var overrides.
- [ ] 14-16. Updated `docs/architecture.md` with self-adaptive behavior section.
- [ ] 14-17. Changelog entry.

## Incremental Delivery

Three phases aligned with the tier progression:

### Phase A — Externalize + Observe (tier 0 value)
1. **Task 1** = `BehaviorStore` foundation + `AdaptableParameterRegistry`. Declarative parameter taxonomy in place.
2. **+ Task 2** = prompts externalized. Manual prompt editing in `~/.dan/behavior/prompts/` without code changes.
3. **+ Task 3** = thresholds externalized. Users can tune DAN's decision boundaries via config files.
4. **+ Task 4** = evidence infrastructure. Parameter-outcome telemetry, pattern accumulation, retrieval-outcome correlation all active at tier 0. DAN silently learns what it would change.
5. **+ Task 9** = tool description dedup. Eliminates the most obvious source-of-truth violation.

### Phase B — Advise + Commands (tier 1 value)
6. **+ Tasks 11 + 13** = audit trail, `/changes`, `/revert`, `/behavior`, `/adaptations` commands. Foundation for all proposals.
7. **+ Task 5** = threshold proposals. `ThresholdCalibrator` proposes adjustments. User sees evidence and approves.
8. **+ Task 6** = prompt variant proposals. `PromptTracker` proposes prompt improvements. User approves.
9. **+ Task 7** = intent discovery proposals. Unrecognized patterns surface for user approval.
10. **+ Task 8** = domain discovery proposals. New domains proposed for user approval.
11. **+ Task 10** = model config proposals. `ModelOutcomeTracker` proposes tier map changes.

### Phase C — Auto-apply (tier 2 value)
12. **+ Task 12** = tiered adaptation lifecycle. Tier 2 auto-applies bounded changes with regression detection.
13. **+ Task 14** = full test suite + docs.

Phase A (~3d): externalization + evidence collection. Phase B (~3d): proposal generation + UX commands. Phase C (~2d): auto-apply + tests. Buffer: ~1d.

## What Never Adapts (innate)

| Layer | Examples | Why static |
|-------|----------|-----------|
| Graph IR | `dan_graph_v1` JSON schema, node types, edge types | Every persisted workflow, authoring surface, and executor depends on this contract. |
| Engine execution | Scheduler, checkpoint/resume, retry/backoff, fan-out/fan-in | Deterministic execution must be trustworthy. |
| Safety boundaries | PII rules, destructive-action confirmation, cost limits | The guard rails themselves cannot be the subject of optimization. |
| Calibration bounds | ±20% step limit, 15% regression threshold, min evidence counts | The bounds on self-modification cannot themselves be self-modified. |
| Adaptive loop structure | `BehaviorStore` lifecycle, `AdaptationRegistry` governance, regression detection, revert mechanism | Self-modifying the self-modifier has no fixed point. |
| Tier promotion gates | Gate thresholds for 0→1 and 1→2 promotion | Changing the rules for earning trust requires trust already earned — circular. |

## Dependencies

- `AdaptationRegistry` (31-15) — governance for all proposed changes
- `AdaptationCandidate` (31-15) — extended with before/after values and new source types
- `PromptTracker` (29-6) — extended with system-scope prompt tracking
- `ModelOutcomeTracker` (29-6) — wired to propose tier map changes
- `CorrectionStore` (31-15) — negative evidence source for prompt/threshold calibration
- `TelemetryStore` (31-20) — outcome data for threshold calibration
- `LearningHealthCounters` (31-15) — safety gate for auto-apply
- `DomainTemplate` / `detect_domain()` (31-21) — domain keyword maps externalized
- `get_all_tools()` / `TOOL_METADATA` (7-3) — source for generated tool reference
- `NotificationManager` (26-4) — verbose notifications on non-active surfaces
- `CommandRegistry` (31-16) — `/changes`, `/revert`, `/behavior` registration

## Estimate

8-9 days (Phase A externalize+observe: ~3d, Phase B advise+commands: ~3d, Phase C auto-apply+tests: ~2d, buffer: ~1d)

## Primary Files

- `src/dan/engine/behavior_store.py` — **new**: `BehaviorArtifact`, `BehaviorStore`, `BehaviorChangeLog`, `ThresholdCalibrator`, `AdaptableParameter`, `AdaptableParameterRegistry`, `ParameterDecisionLogger`, `PatternAccumulator`
- `src/dan/engine/adaptation_registry.py` — extended: new source types, before/after/parameter_key values, changelog wiring, adaptation outcome tracking
- `src/dan/engine/outcome_trackers.py` — extended: system-scope prompt tracking
- `src/dan/engine/learning_tiers.py` — extended: tier 0 observe features, tier 1 advise features, tier 2 auto-apply features, updated promotion gates
- `src/dan/server/telemetry.py` — extended: `parameter_key`, `parameter_value` optional fields on `TelemetryEvent`
- `src/dan/engine/correction_memory.py` — extended: `active_prompt_key` on `CorrectionRecord`
- `src/dan/server/chat_manager.py` — `generate_capability_reference()`, prompt loading from `BehaviorStore`
- `src/dan/server/concierge/classifier.py` — intent taxonomy from `BehaviorStore`, wired to `PatternAccumulator`
- `src/dan/server/concierge/domain_learning.py` — `_DOMAIN_KEYWORDS` from `BehaviorStore`, wired to `PatternAccumulator`
- `src/dan/server/concierge/reuse_decision.py` — thresholds from `BehaviorStore`, `ParameterDecisionLogger` wired
- `src/dan/engine/memory_kernel.py` — ranking weights from `BehaviorStore`, retrieval-outcome correlation
- `src/dan/providers/tier_defaults.py` — tier maps from `BehaviorStore`
- `src/dan/providers/costs.py` — cost table from `BehaviorStore`
- `src/dan/server/concierge/runtime.py` — evidence collection wiring, calibration trigger, verbose notification, `/adaptations` integration
- `src/dan/server/capability_handlers.py` — `/changes`, `/revert`, `/behavior`, `/adaptations` commands

## Notes

- **Tier design rationale**: the original plan had adaptability at tier 0 (default). This was revised: self-modification should be *earned*, not granted. Tier 0 observes only — evidence accrues silently. Tier 1 proposes with evidence — user approves. Tier 2 auto-applies within bounds. This progression means DAN needs to prove its evidence models are trustworthy before it changes its own behavior.
- **Parameter taxonomy rationale**: not all parameters are equally safe or valuable to adapt. Thresholds (low risk, easy to bound) are very different from taxonomy (high risk, affects routing). The `AdaptableParameter` registry makes risk/evidence/bounds declarative per parameter, so adding a new adaptable dimension is one registration call rather than new adaptation code.
- **Evidence infrastructure is tier 0 by design**: even users who never enable adaptation benefit from parameter-outcome telemetry. It's the foundation for future `/analytics` dashboards and manual tuning decisions.
- The LLM cost profile is bounded: 1 LLM call per prompt variant proposal (task 6-4), only triggered after 20+ negative signals and only at tier 2. All other operations (evidence collection, threshold calibration, intent tracking, domain discovery, tool reference generation, changelog) are local computation.
- `BehaviorStore` uses the same seed-override-learn pattern as `DomainTemplate` (31-21). The difference: `DomainTemplate` is domain-specific metadata; `BehaviorStore` is DAN's own behavioral parameters.
- Prompt externalization does NOT break existing code. Every consumer keeps its seed constant as the `default` parameter to `behavior_store.get()`. If `~/.dan/behavior/` doesn't exist, DAN behaves identically to today.
- The ±20% calibration bound (task 5-3) is innate — never self-modified, regardless of tier.
- `CAPABILITY_TOOLS_REFERENCE` elimination (task 9) is the simplest change but the most impactful for correctness: it removes the single largest source of stale documentation in the codebase.
- Intent taxonomy extension (task 7) keeps the `IntentCategory` enum for the core 10 categories. Extended intents are string values validated against the registry, not enum members. This avoids breaking type annotations while allowing growth.
- All adaptation sources registered in `AdaptationRegistry` (task 13) follow the same lifecycle: propose → measure for N interactions → auto-revert on >15% regression. The measurement window and regression threshold are innate safety parameters.
- **New experience categories**: this plan doesn't add new `MemoryType` values. Instead, evidence is captured through three lightweight mechanisms: (1) optional fields on `TelemetryEvent` for parameter-outcome linking, (2) `PatternAccumulator` counters in `BehaviorStore` for emergence detection, (3) retrieval-outcome correlation counters in `BehaviorStore`. This avoids expanding the memory model while providing the evidence infrastructure each parameter category needs.
