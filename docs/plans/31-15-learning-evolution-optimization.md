# 31-15: Learning & Evolution Optimization

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** completed
**Goal:** Make DAN's learning system practically effective — upgrade signal quality, close the user-correction loop, make learning observable, unify adaptation governance, and prepare storage for scale — so active learning becomes trustworthy enough for a staged default rollout.

## Problem

DAN has a complete 5-layer learning architecture (memory → repair → reuse → self-evolvement → static policies), but several structural weaknesses prevent it from delivering strong autonomous improvement:

- **Active learning is dormant.** The most valuable self-evolvement features (prompt optimization, model learning, topology learning, skill evolution) are all gated OFF by default. `DAN_LEARNING_MODE=1` enables them, but most users never set it.
- **Quality signals are too coarse.** Model learning records `quality = 1.0` for success and `0.0` for failure — a binary at the run level, not the node level. Topology learning only captures the first failure node. This noise makes learned recommendations unreliable.
- **User corrections are the highest-value learning signal, but aren't captured.** When a user says "no, use Stata not Python" or "summarize first," that correction isn't systematically fed back as negative evidence against the prompt/model/topology that produced the bad output.
- **Learning fails silently.** Post-run learning, memory extraction, and outcome tracking all swallow exceptions at debug level. DAN can stop learning for days without anyone noticing.
- **Adaptation outputs are fragmented.** Prompt variants, model recommendations, topology suggestions, skill refinements, and principles all have different lifecycles, confidence models, and promotion rules. No common governance.
- **Storage is still in-process JSON.** `MemoryKernel` loads everything into `_index: dict` and iterates with linear scans. Fine for small scale, but the first bottleneck as experience accumulates.
- **Planning doesn't consume learned priors aggressively enough.** Duration estimates, failure hotspots, and model-per-task patterns exist in memory but aren't systematically fed into plan construction (31-8).

## Current Cost Profile

Most learning is free (local bookkeeping). Only two paths can add LLM calls:
- **Reflection** (`reflection_trigger`): +1 LLM call per run, **disabled by default**
- **LLM memory extraction** (`DAN_MEMORY_EXTRACTION_LLM`): +1 LLM call per chat turn, **disabled by default**

All other learning (prompt tracking, model tracking, topology tracking, RunLearner, reuse scoring, preference evolution, consolidation) is zero extra LLM calls.

## Tasks

- [x] 1. **Tiered learning activation**
  - [x] 1-1. Define three tiers: `baseline` (always on: memory, post-run learning, reuse scoring, preference evolution), `advisory` (topology suggestions, model recommendations, prompt variant proposals — observe and recommend), `active` (A/B prompt promotion, skill refinement promotion, auto-adaptation — actually change behavior)
  - [x] 1-2. Replace `DAN_LEARNING_MODE` with `DAN_LEARNING_TIER`: `0` = baseline only (current default), `1` = baseline + advisory, `2` = baseline + advisory + active. Backward compat: `DAN_LEARNING_MODE=1` maps to tier `1`.
  - [x] 1-3. Staged rollout rule: `check_tier_promotion_gates()` function validates health counters >= 95%/100+ events, model precision > 0.7/15+ samples, no false-positive adaptations. Manual check, not automated.
  - [x] 1-4. Individual env var overrides: `_FEATURE_ENV_OVERRIDES` map in `learning_tiers.py`, checked in `is_feature_enabled()`. `"1"` force-enables, `"0"` force-disables.
  - [x] 1-5. `app.py` lifespan: tier-based activation replaces `DAN_LEARNING_MODE` bundle logic; startup banner shows tier and enabled features.
  - [x] 1-6. `.env.example` updated with `DAN_LEARNING_TIER` and override descriptions.

- [x] 2. **Upgrade quality signals**
  - [x] 2-1. `NodeOutcome` model in `outcome_trackers.py`: node_id, node_type, success, retry_count, schema_valid_first_try, quality_score.
  - [x] 2-2. `compute_retry_quality()`: `1.0 / (1.0 + retry_count * 0.3)` clamped [0.1, 1.0].
  - [x] 2-3. `schema_valid_first_try` flag on `NodeOutcome`, used in `compute_node_quality()` (0.8x multiplier).
  - [x] 2-4. `compute_workflow_quality()`: partial success gradient (quality_sum / total_nodes).
  - [x] 2-5. Wired into `PromptTracker.record()`, `ModelOutcomeTracker.record()`, `TopologyOutcomeTracker.record()` — all accept new quality signal parameters.
  - [x] 2-6. Backward compat: all new params are optional with defaults; old callers with binary quality still work.

- [x] 3. **Correction memory loop** (core module done; concierge runtime wiring deferred)
  - [x] 3-1. **Correction detector**: identify when the user is correcting DAN's behavior vs. continuing the conversation. Signals: negation ("no, don't..."), override ("use X instead"), style correction ("shorter", "summarize first"), explicit redo ("try again with..."). Heuristic-first (keyword + pattern matching), with optional LLM classifier for ambiguous cases. **False-positive mitigation:** assign a `confidence: float` to each detection. At tier 1 (advisory), corrections with confidence < 0.7 are logged but not applied. At tier 2 (active), only confidence >= 0.8 triggers automatic quality score adjustments. Low-confidence candidates surface in `/corrections` for manual review.
  - [x] 3-2. **Correction→learning routing**: corrections produce:
    - `PREFERENCE` when it's stable user taste ("always use Stata", "I prefer concise responses")
    - `PRINCIPLE` when it's a general rule ("summarize results before showing raw tables")
    - Negative evidence against the specific prompt/model/topology that produced the corrected output (decrement quality score retroactively)
  - [x] 3-3. **Correction context capture**: store what DAN did (the output that was corrected), what the user wanted (the correction), and the link to the prompt/model/node that produced it. This creates a ground-truth dataset for future calibration.
  - [x] 3-4. Wired into `Concierge._process_inner()` — correction detection, routing, adaptation candidate creation, and MemoryKernel preference/principle storage.
  - [x] 3-5. `/corrections` command to list recent correction-driven learning events

- [x] 4. **Learning health visibility**
  - [x] 4-1. **Learning event counters**: track `attempted / succeeded / skipped / failed` for each learning path (memory extraction, run learning, prompt tracking, model tracking, topology tracking, skill tracking). Counters reset on server restart, persisted per session.
  - [x] 4-2. `/status` Learning section: shows tier, enabled features, per-path event counts, total events, model-learning sample warnings.
  - [x] 4-3. Minimum-sample warnings: if model recommender has < 15 samples, warning shown in `/status`.
  - [x] 4-4. Startup banner: shows tier + all enabled features via `features_enabled_at_tier()`.
  - [x] 4-5. `record_failure()` on `LearningHealthCounters`: `logger.warning` on first failure per path, `logger.debug` on subsequent.

- [x] 5. **Unified adaptation governance** (core module done; wiring into learning subsystems deferred)
  - [x] 5-1. `AdaptationCandidate` model: `source: str` (prompt_opt | model_rec | topology_adv | skill_ref | principle), `evidence: list[str]`, `confidence: float`, `sample_size: int`, `scope: str` (node_type | workflow | global), `auto_apply: bool`, `rollback_path: str | None`, `created_at: datetime`, `last_outcome: str | None`
  - [x] 5-2. `AdaptationRegistry`: central store for all pending/applied/rejected adaptations. Each adaptation type registers its candidates here instead of managing lifecycle independently.
  - [x] 5-3. Governance rules: `auto_apply` only allowed at tier `2` (active); tier `1` stores candidates for inspection; tier `0` doesn't generate candidates at all.
  - [x] 5-4. `/adaptations` command: list pending adaptations with confidence, sample size, and approval status. User can approve/reject from chat.
  - [x] 5-5. Rollback: if an applied adaptation causes quality regression, auto-revert and flag. Concrete parameters: measure over the next **N=10 runs** in the same scope. Regression threshold: **>15% drop in quality score** vs. the pre-adaptation baseline. To avoid attribution ambiguity, limit to **one active adaptation per scope** (node_type, workflow, or global) at a time. If multiple candidates are pending, queue them and evaluate serially.

- [x] 6. **Memory storage backend abstraction**
  - [x] 6-1. `MemoryBackend` protocol: `load() -> dict[str, MemoryItem]`, `save(index: dict[str, MemoryItem])`, `upsert(item: MemoryItem)`, `delete(item_id: str)`, `query(filter: MemoryFilter) -> list[MemoryItem]`
  - [x] 6-2. `JsonFileBackend` — current behavior, extracted into protocol implementation. Default for local/dev.
  - [x] 6-3. `SqliteBackend` — optional, for users who want indexed queries and concurrent access without a full database server. Stores MemoryItems in a single SQLite table with JSON content column + indexed metadata columns (type, lifecycle, importance, created_at).
  - [x] 6-4. `DAN_MEMORY_BACKEND` env var: `json` (default) | `sqlite`. Future: `postgres`, `vector`.
  - [x] 6-5. `MemoryKernel` accepts optional `backend: MemoryBackend` parameter. Backend delegation seam in place.
  - [x] 6-6. In-memory `_type_index` (`dict[str, list[str]]` keyed by memory_type) maintained by `store()`, `store_many()`, `delete()`. `list_by_type()` and `retrieve()` use it to avoid linear scans. Rebuilt on `_load_index()`.

- [x] 7. **Planning-time calibration from experience**
  - [x] 7-1. `DurationEstimator`: given a task description + node type, query `ExperienceStore` for similar past tasks and return median duration + confidence interval. Fallback to heuristic classification (quick ~2min, medium ~15min, complex ~30min) when no experience data exists.
  - [x] 7-2. `FailureHotspotPredictor`: given a workflow topology, query `ErrorMemoryIndex` + `PrincipleStore` for failure patterns at similar nodes. Surface "this node type fails ~30% of the time — consider adding a validator" as planning advice.
  - [x] 7-3. `ModelPreference`: given a node type + task pattern, query `ModelOutcomeTracker` for best empirical model. Feed into 31-8 RCPSP scheduler as a model-assignment prior.
  - [x] 7-4. `WorkflowPlanner._inject_calibration()`: injects `DurationEstimator`, `FailureHotspotPredictor`, `ModelPreference` into `plan_context["calibration_hints"]`. `PlanningPromptBuilder.build_user_prompt()` renders hints under "Experience-Based Calibration" section.
  - [x] 7-5. Bridge documented in architecture.md: planning_calibration.py wired into WorkflowPlanner.plan() at plan-construction time.

- [x] 8. **Tests and docs**
  - [x] 8-1. Unit tests: tiered activation, quality signal computation, correction detection, adaptation governance lifecycle, backend protocol conformance
  - [x] 8-2. Integration test in `test_learning_integration.py`: `TestCorrectionIntegration` — correction → preference/principle storage → retrieval on similar task.
  - [x] 8-3. Integration test in `test_learning_integration.py`: `TestQualitySignalsPipeline` — node quality signals → model recommender → planning calibration.
  - [x] 8-4. Updated architecture.md (learning section), .env.example (tier descriptions), changelog.

## Decisions

- **Tiers over binary flags** — graduated activation is safer than all-or-nothing. Users can start at tier 1 (observe) before committing to tier 2 (act).
- **Staged default rollout.** Existing installs keep tier 0 unless the user opts in. Fresh-install default only moves to tier 1 after health counters and precision signals show advisory mode is trustworthy.
- **Correction memory is the #1 priority** — user corrections are the highest-fidelity learning signal and are currently completely unused. This alone would improve DAN's practical evolution more than all other changes combined.
- **Storage abstraction now, migration later** — formalize the backend seam so future scale-up is a config change, not a refactor. Don't over-invest in storage engine until scale demands it.
- **Node-level signals before algorithm upgrades** — upgrading the quality of data feeding the learners is more impactful than making the learners themselves more sophisticated. Garbage in, garbage out.

## Primary Files

- `src/dan/server/app.py` — tiered activation logic in lifespan
- `src/dan/engine/memory_kernel.py` — `MemoryBackend` protocol, backend abstraction
- `src/dan/engine/outcome_trackers.py` — upgraded quality signals, adaptation candidate model
- `src/dan/server/run_manager.py` — node-level quality recording
- `src/dan/executors/llm.py` — prompt tracker signal upgrade
- `src/dan/server/concierge/runtime.py` — correction detection wiring
- `src/dan/engine/correction_memory.py` — correction detector, routing, context capture (new)
- `src/dan/engine/adaptation_registry.py` — unified adaptation governance (new)
- `src/dan/engine/planning_calibration.py` — duration estimator, failure predictor, model preference (new)
- `src/dan/server/capability_handlers.py` — `/status` learning section, `/corrections`, `/adaptations` commands

## Dependencies

- `MemoryKernel` (29-1) — storage abstraction target
- `RunLearner` (29-6) — quality signal upgrade target
- `PromptTracker`, `ModelOutcomeTracker`, `TopologyOutcomeTracker` (29-6) — signal consumers
- `WorkflowPlanner` (19-2) — planning-time calibration consumer
- `PlanScheduler` (31-8) — planning-time calibration consumer (when implemented)
- `ExperienceStore` (19-1) — duration estimation source
- `ErrorMemoryIndex`, `PrincipleStore` (17-1, 17-2) — failure hotspot source
- `/status` command (31-2) — learning health display target

## Estimate

3-4 days

## Notes

- This plan directly addresses the four findings from the [Learning evolution review](7a3db302-7cd6-4856-97c7-2bf68cc2acee): (1) active learning is dormant, (2) signals are too coarse, (3) no correction feedback, (4) learning fails silently. It also adds the two structural improvements: unified governance and storage abstraction.
- The cost profile of learning remains near-zero extra LLM calls. All new features (tiering, quality signals, correction detection, health counters, adaptation governance, storage backend, planning calibration) are local computation, not additional LLM calls. The only LLM-consuming paths remain reflection and LLM memory extraction, both still gated.
- Task 7 (planning-time calibration) bridges this plan to 31-8 (plan dependency optimization). The duration estimator feeds directly into RCPSP time estimates; the model preference feeds into model-tier assignment by slack. This connection turns "learning from the past" into "planning for the future."
- Tasks 1-6 are independent hardening work and can ship before 31-8. Task 7's `PlanScheduler` integration is intentionally a blocked follow-on slice once 31-8 lands.
- Correction memory (task 3) is flagged as highest impact. Even without the other upgrades, capturing user corrections as first-class learning signals would measurably improve DAN's behavior on repeated interactions.
