# 31-15: Learning & Evolution Optimization

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** not-started
**Goal:** Make DAN's learning system practically effective — upgrade signal quality, close the user-correction loop, make learning observable, unify adaptation governance, and prepare storage for scale — so active learning becomes trustworthy enough to run by default.

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

- [ ] 1. **Tiered learning activation**
  - [ ] 1-1. Define three tiers: `baseline` (always on: memory, post-run learning, reuse scoring, preference evolution), `advisory` (topology suggestions, model recommendations, prompt variant proposals — observe and recommend), `active` (A/B prompt promotion, skill refinement promotion, auto-adaptation — actually change behavior)
  - [ ] 1-2. Replace `DAN_LEARNING_MODE` with `DAN_LEARNING_TIER`: `0` = baseline only (current default), `1` = baseline + advisory, `2` = baseline + advisory + active. Backward compat: `DAN_LEARNING_MODE=1` maps to tier `1`.
  - [ ] 1-3. Make tier `1` (advisory) safe enough to be the recommended default for new users. This means advisory outputs appear in logs / `/status` but do not auto-apply changes.
  - [ ] 1-4. Individual env var overrides still work (e.g., `DAN_PROMPT_OPTIMIZATION=1` forces prompt optimization on regardless of tier)
  - [ ] 1-5. Update `app.py` lifespan to implement tiered activation logic
  - [ ] 1-6. Update `.env.example` and docs with tier descriptions

- [ ] 2. **Upgrade quality signals**
  - [ ] 2-1. **Node-level outcome tracking**: record success/failure per node, not just per run. Each node in `record.node_usage` gets its own quality score based on whether that specific node's output was valid (schema-valid, no error, no retry needed).
  - [ ] 2-2. **Retry/repair count signal**: nodes that succeed after self-repair should score lower than nodes that succeed on first try. Encode as `quality = 1.0 - (retry_count * 0.15)` (clamped to [0.1, 1.0]).
  - [ ] 2-3. **Schema-valid-first-try flag**: binary signal — did the node produce structurally valid output on the first attempt? Valuable for model comparison.
  - [ ] 2-4. **Partial success**: for long workflows, encode a gradient (e.g., 7 of 10 nodes succeeded → 0.7) instead of all-or-nothing.
  - [ ] 2-5. Wire upgraded signals into `PromptTracker`, `ModelOutcomeTracker`, `TopologyOutcomeTracker` in `run_manager.py` and `llm.py`
  - [ ] 2-6. Preserve backward compat: old records with binary quality still work

- [ ] 3. **Correction memory loop**
  - [ ] 3-1. **Correction detector**: identify when the user is correcting DAN's behavior vs. continuing the conversation. Signals: negation ("no, don't..."), override ("use X instead"), style correction ("shorter", "summarize first"), explicit redo ("try again with..."). Heuristic-first (keyword + pattern matching), with optional LLM classifier for ambiguous cases.
  - [ ] 3-2. **Correction→learning routing**: corrections produce:
    - `PREFERENCE` when it's stable user taste ("always use Stata", "I prefer concise responses")
    - `PRINCIPLE` when it's a general rule ("summarize results before showing raw tables")
    - Negative evidence against the specific prompt/model/topology that produced the corrected output (decrement quality score retroactively)
  - [ ] 3-3. **Correction context capture**: store what DAN did (the output that was corrected), what the user wanted (the correction), and the link to the prompt/model/node that produced it. This creates a ground-truth dataset for future calibration.
  - [ ] 3-4. Wire into `Concierge._process_message()` — after each user message, check if it's a correction of the previous assistant turn
  - [ ] 3-5. `/corrections` command to list recent correction-driven learning events

- [ ] 4. **Learning health visibility**
  - [ ] 4-1. **Learning event counters**: track `attempted / succeeded / skipped / failed` for each learning path (memory extraction, run learning, prompt tracking, model tracking, topology tracking, skill tracking). Counters reset on server restart, persisted per session.
  - [ ] 4-2. **Surface in `/status`**: add a "Learning" section to the existing `/status` command showing: active tier, enabled features, event counts since startup, last learning event timestamp, any features below minimum sample size.
  - [ ] 4-3. **Minimum-sample warnings**: if a learning feature is enabled but hasn't accumulated enough data to be meaningful (e.g., model recommender needs ~15 runs), surface a warning: "Model learning enabled but only 3/15 samples collected — recommendations not yet active."
  - [ ] 4-4. **Startup banner enhancement**: when `DAN_LEARNING_TIER >= 1`, show which advisory/active features are running in the existing startup feature banner (31-5).
  - [ ] 4-5. Replace bare `logger.debug(...)` exception swallowing in learning paths with counter increment + `logger.warning(...)` on first failure + `logger.debug(...)` on subsequent (rate-limited warning)

- [ ] 5. **Unified adaptation governance**
  - [ ] 5-1. `AdaptationCandidate` model: `source: str` (prompt_opt | model_rec | topology_adv | skill_ref | principle), `evidence: list[str]`, `confidence: float`, `sample_size: int`, `scope: str` (node_type | workflow | global), `auto_apply: bool`, `rollback_path: str | None`, `created_at: datetime`, `last_outcome: str | None`
  - [ ] 5-2. `AdaptationRegistry`: central store for all pending/applied/rejected adaptations. Each adaptation type registers its candidates here instead of managing lifecycle independently.
  - [ ] 5-3. Governance rules: `auto_apply` only allowed at tier `2` (active); tier `1` stores candidates for inspection; tier `0` doesn't generate candidates at all.
  - [ ] 5-4. `/adaptations` command: list pending adaptations with confidence, sample size, and approval status. User can approve/reject from chat.
  - [ ] 5-5. Rollback: if an applied adaptation causes quality regression (measured over next N runs), auto-revert and flag.

- [ ] 6. **Memory storage backend abstraction**
  - [ ] 6-1. `MemoryBackend` protocol: `load() -> dict[str, MemoryItem]`, `save(index: dict[str, MemoryItem])`, `upsert(item: MemoryItem)`, `delete(item_id: str)`, `query(filter: MemoryFilter) -> list[MemoryItem]`
  - [ ] 6-2. `JsonFileBackend` — current behavior, extracted into protocol implementation. Default for local/dev.
  - [ ] 6-3. `SqliteBackend` — optional, for users who want indexed queries and concurrent access without a full database server. Stores MemoryItems in a single SQLite table with JSON content column + indexed metadata columns (type, lifecycle, importance, created_at).
  - [ ] 6-4. `DAN_MEMORY_BACKEND` env var: `json` (default) | `sqlite`. Future: `postgres`, `vector`.
  - [ ] 6-5. Migrate `MemoryKernel` to use `MemoryBackend` protocol internally — no API changes to callers.
  - [ ] 6-6. Eliminate linear scans in `retrieve_by_task` hot path: add lightweight in-memory type index (dict keyed by memory_type) even for JSON backend.

- [ ] 7. **Planning-time calibration from experience**
  - [ ] 7-1. `DurationEstimator`: given a task description + node type, query `ExperienceStore` for similar past tasks and return median duration + confidence interval. Fallback to heuristic classification (quick ~2min, medium ~15min, complex ~30min) when no experience data exists.
  - [ ] 7-2. `FailureHotspotPredictor`: given a workflow topology, query `ErrorMemoryIndex` + `PrincipleStore` for failure patterns at similar nodes. Surface "this node type fails ~30% of the time — consider adding a validator" as planning advice.
  - [ ] 7-3. `ModelPreference`: given a node type + task pattern, query `ModelOutcomeTracker` for best empirical model. Feed into 31-8 RCPSP scheduler as a model-assignment prior.
  - [ ] 7-4. Wire all three into `WorkflowPlanner` and 31-8's `PlanScheduler` — these become planning inputs, not just post-run learnings.
  - [ ] 7-5. This is the bridge between "learning from the past" and "planning for the future" — the most important upgrade for making learning operationally useful.

- [ ] 8. **Tests and docs**
  - [ ] 8-1. Unit tests: tiered activation, quality signal computation, correction detection, adaptation governance lifecycle, backend protocol conformance
  - [ ] 8-2. Integration test: correction → PREFERENCE/PRINCIPLE storage → retrieval on similar future task → behavior change verified
  - [ ] 8-3. Integration test: quality signals → model recommender → planning-time model assignment
  - [ ] 8-4. Update architecture.md, changelog, .env.example

## Decisions

- **Tiers over binary flags** — graduated activation is safer than all-or-nothing. Users can start at tier 1 (observe) before committing to tier 2 (act).
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
- Correction memory (task 3) is flagged as highest impact. Even without the other upgrades, capturing user corrections as first-class learning signals would measurably improve DAN's behavior on repeated interactions.
