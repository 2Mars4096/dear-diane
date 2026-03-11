# 31-22: Self-Adaptive Behavior

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** not-started
**Goal:** Make DAN's own prompts, thresholds, classifiers, domain ontology, and tool descriptions the subject of its learning loop — auto-discovered, verbose, revertable, and adaptive by default.

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
2. **Adaptability is the default.** Tier 0 includes threshold/prompt self-tuning (bounded). Not gated behind `DAN_LEARNING_TIER >= 2`. Higher tiers add ontology growth and structural adaptation.
3. **Audit trail.** Every change is a versioned entry in `BehaviorChangeLog` with before/after values, evidence that triggered it, and a revert command.
4. **Seed → Override → Learn → Revert.** All behavior artifacts follow the same lifecycle: ship seed defaults in code → user can override in `~/.dan/` → learning loop proposes changes → user can revert any change.
5. **Single source of truth.** Eliminate dual representations (e.g., tool descriptions in both `TOOL_METADATA` and `CAPABILITY_TOOLS_REFERENCE`).

## Tasks

### 1. Behavior Store — unified externalization layer

- [ ] 1-1. `BehaviorArtifact` Pydantic model: `key: str`, `value: Any`, `version: int`, `evidence: list[str]`, `updated_at: float`, `previous_versions: list[dict]` (each with `value`, `version`, `updated_at`, `reason`). Max 10 previous versions retained for rollback.
- [ ] 1-2. `BehaviorStore` class in new `src/dan/engine/behavior_store.py`: manages `~/.dan/behavior/` directory. Methods: `get(key, default)`, `set(key, value, reason, evidence)`, `revert(key, version)`, `list_keys()`, `list_changes(since_hours)`. Each key is a JSON file: `~/.dan/behavior/<category>/<key>.json`. Categories: `prompts`, `heuristics`, `taxonomy`, `domains`, `models`.
- [ ] 1-3. Seed defaults: `BehaviorStore` constructor accepts `seed_defaults: dict[str, Any]`. On first access, if no user file exists for a key, the seed value is returned without writing a file. Only writes on first mutation.
- [ ] 1-4. Hot-reload: `BehaviorStore.reload(key)` re-reads from disk. `reload_all()` for startup. File watcher is NOT needed — reload happens on `get()` if file mtime changed since last read (stat-based, no inotify).
- [ ] 1-5. Thread safety: `threading.RLock` per category (not global). Same pattern as `MemoryKernel`.

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

### 4. Threshold self-tuning loop

- [ ] 4-1. `ThresholdCalibrator` class in `behavior_store.py`: consumes `TelemetryStore` outcome data to propose threshold adjustments. Runs periodically (after every N=50 telemetry events of the relevant type, or on explicit `/calibrate` command).
- [ ] 4-2. Calibration logic per threshold type:
  - **Classifier confidence**: query `TelemetryStore` for `classification` events. For cases where heuristic confidence was between 0.70 and current threshold and the LLM classifier agreed with the heuristic → threshold could be lower. For cases where heuristic was above threshold but the outcome was a correction → threshold should be higher. Propose new value = weighted median of "correct decision" confidences.
  - **Reuse/adapt/generate cutoffs**: query telemetry for `workflow_run` events that started from reuse/adapt decisions. If ADAPT decisions in the 0.4-0.8 range succeed >80% → lower reuse threshold. If ADAPT decisions fail >40% → raise adapt threshold.
  - **Memory ranking weights**: correlation between memory items that were retrieved (by type) and whether the response was accepted (no correction). Adjust weights toward types that correlate with acceptance.
- [ ] 4-3. Safety bounds: no single calibration step changes a value by more than ±20% of the current value. Minimum 10 outcome samples before any adjustment. All changes go through `AdaptationRegistry` with source `"threshold_cal"`.
- [ ] 4-4. Verbose output: calibration results are logged as `BehaviorChangeLog` entries and surfaced in `/changes`. Example: `"Reuse threshold adjusted: 0.80 → 0.74. Evidence: 12/15 ADAPT decisions in [0.74, 0.80] range succeeded. /revert t3f2 to undo."`

### 5. Prompt self-tuning integration

- [ ] 5-1. Extend existing `PromptTracker` to also track DAN's own prompts (not just user workflow node prompts). Add a `scope: Literal["workflow", "system"]` field to prompt tracking records. System-scope records use the `BehaviorStore` prompt key as the identifier.
- [ ] 5-2. When `CorrectionStore` detects a correction linked to a classifiable prompt (e.g., misclassification → classifier prompt, bad build → codegen prompt), record negative evidence against that prompt version in `PromptTracker`.
- [ ] 5-3. Prompt variant proposal: after N=20 negative signals against a system prompt, `PromptTracker` generates a candidate via `AdaptationRegistry` with source `"prompt_opt"`. The candidate includes: the current prompt, the failure cases, and a description of what should change. At tier 0, the candidate is auto-applied if the proposed change is an additive clause (append-only, no deletion). At tier 1+, structural rewrites are also eligible.
- [ ] 5-4. LLM-assisted prompt improvement: when a prompt variant candidate is proposed, call the LLM with: "Here is a system prompt that produced these failures: [cases]. Propose a minimal edit that would fix these cases without breaking the general behavior. Return the full updated prompt." Gate: 1 LLM call per prompt variant proposal. The proposed variant is stored alongside the original; `AdaptationRegistry` governs apply/rollback.
- [ ] 5-5. Regression detection: after applying a prompt variant, measure the next 20 interactions using that prompt. If correction rate increases by >15% vs. baseline → auto-revert via `AdaptationRegistry.check_regression()`.

### 6. Dynamic intent taxonomy

- [ ] 6-1. Move `IntentCategory` from a fixed `str, Enum` to a registry-backed extensible set. Core intents (the current 10) remain as seed defaults in `BehaviorStore` under `taxonomy/intent_categories`. The `IntentCategory` enum stays in code for type safety on the core set; extended intents use string values.
- [ ] 6-2. Intent frequency tracker: when `classify_intent_llm()` returns `CONVERSATION` with low confidence (< 0.7), or the heuristic classifier falls through all rules, record the message pattern in a `taxonomy/unrecognized_intents` counter in `BehaviorStore`.
- [ ] 6-3. Promotion gate: when an unrecognized pattern accumulates 5+ occurrences with similar keyword clusters (keyword overlap > 0.6 across instances), propose a new intent category via `AdaptationRegistry`. The proposal includes: suggested name (derived from common keywords), example messages, and suggested handler mapping.
- [ ] 6-4. Verbose notification: `"[Discovery] New intent pattern detected: 'scheduling_request' (7 occurrences, keywords: schedule, cron, every day, recurring). Added to taxonomy. /revert i8a1 to remove."` User sees this on their active surface.
- [ ] 6-5. Handler wiring: new intents initially map to the `DIRECT_TASK` handler (safe default). The classifier prompt is automatically updated (via task 5) to include the new intent in its valid set. The user or a future adaptation can assign a specialized handler.
- [ ] 6-6. Classifier prompt co-evolution: when a new intent is added to the taxonomy, the classifier's `_CLASSIFICATION_SYSTEM_PROMPT` is automatically extended with the new category name and a one-line description. This uses the prompt externalization (task 2) — no code change needed, just a `BehaviorStore.set()` on the prompt key.

### 7. Domain auto-discovery

- [ ] 7-1. Move `_DOMAIN_KEYWORDS` from `domain_learning.py` to `BehaviorStore` under `domains/keyword_maps`. Seed values are the current 6 domains. `detect_domain()` loads from `BehaviorStore` instead of the module constant.
- [ ] 7-2. Domain emergence: when `detect_domain()` returns `None` but the project accumulates 3+ tasks with consistent keyword clusters (measured by `_keyword_overlap` on task descriptions), propose a new domain. Evidence: the task descriptions and their common keywords.
- [ ] 7-3. New domain bootstrapping: create a generic `DomainTemplate` (existing `create_generic_template()` from 31-21), register the keyword map in `BehaviorStore`, and notify the user. Example: `"[Discovery] New domain detected: 'kaggle_competition' (keywords: kaggle, submission, leaderboard, oof, ensemble). Template created. /revert d2c1 to remove."`
- [ ] 7-4. Existing domain keyword expansion: when `detect_domain()` matches a domain but with marginal score (only 1 keyword hit), and the task succeeds, check if new keywords from the task description should be added to that domain's keyword list. Gate: only propose additions, never removals. Max 3 new keywords per expansion.

### 8. Tool description single-source generation

- [ ] 8-1. `generate_capability_reference()` function in `chat_manager.py`: iterates `get_all_tools()` from `dan.tools.__init__`, reads each tool's `TOOL_METADATA` (already contains `tool_id`, `description`, `parameters`, `category`), and formats the reference block programmatically. Groups by category (File, Web, System, Communication, Text, Lookup, Run control, Publish, Browse).
- [ ] 8-2. MCP tool inclusion: `get_mcp_tool_hint()` output is appended to the generated reference (already produces a formatted block).
- [ ] 8-3. Runtime-authored tools: tools registered via `19-6` runtime authoring are included in the generated reference automatically — they already appear in the tool registry.
- [ ] 8-4. Replace the static `CAPABILITY_TOOLS_REFERENCE` constant with a call to `generate_capability_reference()` during `_build_system_content()`. Cache the result per startup (tools don't change mid-session for built-ins; MCP tools re-generate on reconnect).
- [ ] 8-5. Remove the hand-written `CAPABILITY_TOOLS_REFERENCE` string constant. The seed default in `BehaviorStore` is the generated output, not a hand-maintained string.

### 9. Model configuration externalization

- [ ] 9-1. Move `DEFAULT_TIER_MAPS` and `COST_PER_1K_TOKENS` to `BehaviorStore` under `models/tier_maps` and `models/cost_table`. Seed values are the current Python dicts.
- [ ] 9-2. `resolve_tier_map()` reads from `BehaviorStore` instead of the module constant. User override (existing `user_override` parameter) takes precedence over stored values.
- [ ] 9-3. Model tier self-tuning: wire `ModelOutcomeTracker.analyze()` (already built — produces recommendations like "consider cheaper model for node type X") into `AdaptationRegistry`. When a model at a tier consistently succeeds (>90% over 20+ runs), propose a tier map change. Example: `"[Adaptation] Model tier 'routine' updated: claude-sonnet-4-6 → claude-3-5-haiku for node type 'router'. Evidence: 24/25 router calls succeeded with haiku at 60% lower cost. /revert m1a3 to undo."`
- [ ] 9-4. Cost table refresh: when a model string in `COST_PER_1K_TOKENS` is not found during cost calculation, log a warning and use 0. The user can update via `set_config` or by editing `~/.dan/behavior/models/cost_table.json`.

### 10. Behavior Change Log and user-facing commands

- [ ] 10-1. `BehaviorChangeLog` class in `behavior_store.py`: append-only log of all self-modifications. Stored at `~/.dan/behavior/changelog.jsonl`. Fields per entry: `id: str`, `timestamp: float`, `category: str` (prompts/heuristics/taxonomy/domains/models), `key: str`, `action: str` (set/revert/calibrate/discover), `before_summary: str` (first 200 chars of old value), `after_summary: str` (first 200 chars of new value), `evidence: list[str]`, `source: str` (threshold_cal/prompt_opt/intent_discovery/domain_discovery/model_rec/user).
- [ ] 10-2. `/changes` command: list recent behavioral adaptations. Default: last 24h. `--all` for full history. `--category <cat>` to filter. Output format: one line per change with id, timestamp, category, key, action, reason. Registered in command registry.
- [ ] 10-3. `/revert <id>` command: roll back a specific change. Reads `BehaviorChangeLog` entry, calls `BehaviorStore.revert(key, version)`, appends a revert entry to the changelog, notifies user with before/after summary. Registered in command registry.
- [ ] 10-4. Verbose surface notifications: when `BehaviorStore.set()` is called by a learning subsystem (not by the user), emit a notification to the active surface(s). Format: `"[Adaptation] <category>/<key> updated. <reason>. /revert <id> to undo."` Uses existing `NotificationManager` for non-active surfaces. For active surfaces, appended as a footnote to the next response (same pattern as domain validation warnings in 31-21).
- [ ] 10-5. `/behavior` command: inspect current behavior state. `--key <key>` shows current value, version, and history. `--seeds` compares current values to seed defaults and shows all deviations. `--reset <key>` restores seed default (writes a revert entry).

### 11. Tier 0 default shift

- [ ] 11-1. Redefine `_TIER_FEATURES` in `learning_tiers.py`:
  - **Tier 0** (default): existing features + `threshold_calibration` + `prompt_selection` (use best-performing variant from `BehaviorStore`, but don't generate new variants)
  - **Tier 1** (advisory): existing features + `prompt_variant_generation` + `intent_discovery` + `domain_discovery` + `model_tier_learning`
  - **Tier 2** (active): existing features + `prompt_rewrite` (structural changes, not just additive) + `taxonomy_restructure` + `auto_tier_promotion`
- [ ] 11-2. `_FEATURE_ENV_OVERRIDES` additions: `threshold_calibration: DAN_THRESHOLD_CALIBRATION`, `prompt_selection: DAN_PROMPT_SELECTION`, `intent_discovery: DAN_INTENT_DISCOVERY`, `domain_discovery: DAN_DOMAIN_DISCOVERY`, `model_tier_learning: DAN_MODEL_TIER_LEARNING`.
- [ ] 11-3. Safety invariant: tier 0 adaptations are bounded (max ±20% per threshold, append-only for prompts, max 3 new keywords per domain expansion). Tier 1 adaptations are proposed and auto-applied with verbose notification. Tier 2 adaptations can structurally rewrite prompts and reorganize taxonomy.
- [ ] 11-4. `check_tier_promotion_gates()` updated: gate A remains (health >= 95% on 100+ events). Gate B extended: threshold calibrations show net positive outcome (>50% of calibrated thresholds improved measured quality). Gate C remains (no false-positive adaptations).

### 12. AdaptationRegistry extensions

- [ ] 12-1. Add `AdaptationSource` values: `"threshold_cal"`, `"intent_discovery"`, `"domain_discovery"`, `"model_tier"`, `"tool_ref"` alongside existing `"prompt_opt"`, `"model_rec"`, `"topology_adv"`, `"skill_ref"`, `"principle"`.
- [ ] 12-2. `AdaptationCandidate` gains `before_value: str | None` and `after_value: str | None` fields for audit.
- [ ] 12-3. Wire `AdaptationRegistry` to `BehaviorChangeLog`: every `approve()` or auto-apply writes a changelog entry. Every `rollback()` writes a revert entry.
- [ ] 12-4. Active adaptation scope enforcement: max 1 active adaptation per `(category, key)` pair. If a new candidate arrives for a key with an active adaptation still being measured, queue it.

### 13. Tests and docs

- [ ] 13-1. Unit tests: `BehaviorStore` CRUD, versioning, revert, seed defaults, mtime-based cache, thread safety.
- [ ] 13-2. Unit tests: `ThresholdCalibrator` — mock telemetry outcomes → proposed adjustments within bounds.
- [ ] 13-3. Unit tests: `generate_capability_reference()` — generates from tool metadata, includes MCP tools, matches expected format.
- [ ] 13-4. Unit tests: intent taxonomy extension — frequency tracking, promotion gate, classifier prompt co-evolution.
- [ ] 13-5. Unit tests: domain auto-discovery — keyword clustering, new domain proposal, keyword expansion.
- [ ] 13-6. Integration test: correction on classifier output → prompt variant proposed → applied → next classification uses updated prompt → no regression.
- [ ] 13-7. Integration test: 50 telemetry events with reuse decisions → `ThresholdCalibrator` proposes adjustment → applied via `AdaptationRegistry` → `/changes` shows entry → `/revert` restores original.
- [ ] 13-8. Integration test: 5 unrecognized intent patterns → new intent proposed → classifier prompt updated → next similar message classified correctly.
- [ ] 13-9. Integration test: 3 tasks in new domain → domain auto-discovered → keyword map and template created → next task in domain gets expertise injection.
- [ ] 13-10. `/changes`, `/revert`, `/behavior` command tests.
- [ ] 13-11. Updated `.env.example` with new env var overrides.
- [ ] 13-12. Updated `docs/architecture.md` with self-adaptive behavior section.
- [ ] 13-13. Changelog entry.

## Incremental Delivery

1. **Task 1 alone** = `BehaviorStore` foundation. No behavior changes yet, but the externalization substrate is ready.
2. **+ Task 2** = prompts externalized. Immediately enables manual prompt editing in `~/.dan/behavior/prompts/` without code changes.
3. **+ Task 3** = thresholds externalized. Users can tune DAN's decision boundaries via config files.
4. **+ Tasks 10 + 12** = audit trail and commands. Users see `/changes`, can `/revert`. Foundation for all subsequent self-tuning.
5. **+ Task 4** = threshold self-tuning active. DAN starts calibrating its own thresholds from telemetry. First real adaptive behavior.
6. **+ Task 5** = prompt self-tuning active. DAN improves its own prompts from correction signals.
7. **+ Task 8** = tool description dedup. Eliminates the most obvious source-of-truth violation.
8. **+ Task 6** = intent taxonomy growth. DAN discovers new intent patterns from usage.
9. **+ Task 7** = domain auto-discovery. DAN discovers new domains from project patterns.
10. **+ Tasks 9 + 11** = model config externalization + tier 0 default shift. Full adaptive behavior is the default.

Tasks 1-4 are the core loop (~2.5d). Tasks 5-6 are prompt/taxonomy adaptation (~1.5d). Tasks 7-9 are ontology/config (~1d). Tasks 10-12 are UX and governance (~1d). Task 13 is tests (~1d).

## What Never Adapts

| Layer | Examples | Why static |
|-------|----------|-----------|
| Graph IR | `dan_graph_v1` JSON schema, node types, edge types | Every persisted workflow, authoring surface, and executor depends on this contract. |
| Engine execution | Scheduler, checkpoint/resume, retry/backoff, fan-out/fan-in | Deterministic execution must be trustworthy. |
| Safety boundaries | PII rules, destructive-action confirmation, cost limits, calibration bounds (±20%) | The guard rails themselves cannot be the subject of optimization. |
| Adaptive loop structure | BehaviorStore lifecycle, AdaptationRegistry governance, regression detection, revert mechanism | Self-modifying the self-modifier has no fixed point. |

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

6-8 days (tasks 1-4 core loop: ~2.5d, tasks 5-6 prompt/taxonomy: ~1.5d, tasks 7-9 ontology/config: ~1d, tasks 10-12 UX/governance: ~1d, task 13 tests + docs: ~1d, buffer for integration: ~1d)

## Primary Files

- `src/dan/engine/behavior_store.py` — **new**: `BehaviorArtifact`, `BehaviorStore`, `BehaviorChangeLog`, `ThresholdCalibrator`
- `src/dan/engine/adaptation_registry.py` — extended: new source types, before/after values, changelog wiring
- `src/dan/engine/outcome_trackers.py` — extended: system-scope prompt tracking
- `src/dan/engine/learning_tiers.py` — extended: new tier 0 features, updated promotion gates
- `src/dan/server/chat_manager.py` — `generate_capability_reference()`, prompt loading from `BehaviorStore`
- `src/dan/server/concierge/classifier.py` — intent taxonomy from `BehaviorStore`, frequency tracker
- `src/dan/server/concierge/domain_learning.py` — `_DOMAIN_KEYWORDS` from `BehaviorStore`
- `src/dan/server/concierge/reuse_decision.py` — thresholds from `BehaviorStore`
- `src/dan/engine/memory_kernel.py` — ranking weights from `BehaviorStore`
- `src/dan/providers/tier_defaults.py` — tier maps from `BehaviorStore`
- `src/dan/providers/costs.py` — cost table from `BehaviorStore`
- `src/dan/server/concierge/runtime.py` — calibration trigger, verbose notification wiring
- `src/dan/server/capability_handlers.py` — `/changes`, `/revert`, `/behavior` commands

## Notes

- The LLM cost profile is bounded: 1 LLM call per prompt variant proposal (task 5-4), only triggered after 20+ negative signals. All other operations (threshold calibration, intent tracking, domain discovery, tool reference generation, changelog) are local computation.
- `BehaviorStore` uses the same seed-override-learn pattern as `DomainTemplate` (31-21). The difference: `DomainTemplate` is domain-specific metadata; `BehaviorStore` is DAN's own behavioral parameters.
- Prompt externalization does NOT break existing code. Every consumer keeps its seed constant as the `default` parameter to `behavior_store.get()`. If `~/.dan/behavior/` doesn't exist, DAN behaves identically to today.
- The ±20% calibration bound (task 4-3) is conservative. After tier promotion (task 11-4), this could be relaxed. But the bound itself is innate — never self-modified.
- `CAPABILITY_TOOLS_REFERENCE` elimination (task 8) is the simplest change but the most impactful for correctness: it removes the single largest source of stale documentation in the codebase.
- Intent taxonomy extension (task 6) keeps the `IntentCategory` enum for the core 10 categories. Extended intents are string values validated against the registry, not enum members. This avoids breaking type annotations while allowing growth.
- All adaptation sources registered in `AdaptationRegistry` (task 12) follow the same lifecycle: propose → measure for 10-20 interactions → auto-revert on >15% regression. The measurement window and regression threshold are themselves stored in `BehaviorStore` under `heuristics/adaptation.*` — but they are NOT self-tuned (they are innate safety parameters).
