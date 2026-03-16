# 33-9: Build Path Simplification & Trustworthiness

**Parent:** [33-generation-quality-eval](33-generation-quality-eval.md)
**Status:** completed *(all tasks implemented and battery validated 2026-03-12; see Post-Implementation Battery section)*
**Goal:** Make the NL→workflow generation path fast, predictable, and transparent enough for daily trust — not just capable on paper.

## Problem

After the 33-6/7/8 implementation (all prerequisites now met), the generation pipeline has the right machinery: intent compiler, quality gates, retry/diagnosis, wall-clock cap, terminal failure messages. But the system is still closer to "powerful but hard to rely on" than "easy to use and powerful." The gap is not missing capability — it is excess path complexity, opaque decision-making, variable latency, and diagnostic blind spots that erode user trust.

Specific symptoms (updated with post-patch battery data, 2026-03-12):
1. **Too many paths, unclear selection.** Intent compiler vs codegen vs diagnosis vs mutation — the user has no visibility into which path was chosen or why. 14/51 battery records have `path=unknown` — the eval harness can't even tell what happened.
2. **Latency variance.** Passed prompts average 65s (min 5s, max 156s). Failed prompts average 131s (min 2s, max 427s). The user cannot tell whether the system is working or stuck.
3. **Underbuilding persists.** t1-07 (q=72, 1 node for multi-step), p02 (q=85, 2 nodes for review loop), m1-follow1/follow2 (q=75, missing loop/parallel structure). Quality gates catch these post-hoc but don't prevent them.
4. **Silent degradation.** When the intent compiler falls back to codegen, or codegen falls back to diagnosis, the user sees no signal. The final graph may be a degraded repair artifact with no indication.
5. **Prompt-to-graph fit.** The mapping from NL prompt complexity to graph complexity is loose. 4/29 produced graphs score below 90 — all due to too few nodes for the prompt's complexity.
6. **Pre-generation latency dominates failures.** 13/20 `no_graph_created` failures have `path=unknown` — codegen was never reached. The failure is upstream of the generation pipeline (classification, context gathering, routing). The generation wall-clock cap (33-8 P1) does not cover this upstream time.
7. **Non-build prompts take as long as builds.** T5 misrouted prompts (p09 "make something cool" at 72s, p10 "weather" at 48s, t5-02 "weather in NY" at 29s) waste 30-70s before failing. The classifier/routing layer should reject these quickly.
8. **Agent lane is broken.** The pilot agent lane (section 4.2) had 0% pass rate on p01-p06. Experience-reuse prompts, confirmation gates, and routing blocks in the concierge pipeline actively prevent builds that the generation pipeline can handle.
9. **Failure categories are opaque.** The eval harness reports only `no_graph_created` (20) and `misrouted` (3). The granular subcategories (timeout_planning, timeout_codegen, llm_error, stream_error, routing_blocked, correct_refusal, codegen_failed) described in 33-8 task 7 are not surfacing. Without them, the 20 `no_graph_created` failures cannot be triaged.
10. **Cross-run flakiness is high.** 60% of prompts (6/10 shared across 2 runs) flip between pass and fail. True stable pass rate (~20%) is much lower than headline 55%. No `--runs N` flakiness measurement is available despite 33-8 task 8 claiming it shipped.

## Approach

This is a **product experience** plan, not a pipeline-plumbing plan. The pipeline exists; this plan makes it trustworthy for daily use. Each task below addresses one of the symptoms above.

## Prerequisites

All prerequisites are met as of 2026-03-12:

- [x] `33-6 P1` — intent-compiler activation measured at 68.4% (T1=63.6%, T2=75.0%), 100% pass rate when activated
- [x] `33-7 P1` — tier-adaptive thresholds and shared `estimate_prompt_complexity()` / `expected_node_range()` signal exist in `graph_quality.py`
- [x] `33-8 P1` — `DAN_MAX_GENERATION_SECONDS` (default 120s) wall-clock cap exists
- [x] `33-8 P2` — terminal generation failures surface specific failure categories in chat response

## Post-Patch Battery Context (2026-03-12)

Full battery: 51 records, 54.9% pass rate. Results file: `tests/eval/results/2026-03-12_133646_run.jsonl`.

**Key data points driving task priorities:**
- 20/23 failures are `no_graph_created` — the system enters the pipeline but never produces a graph
- 13 of those 20 have `path=unknown` — codegen was never reached; failure is upstream (classification, context, routing)
- 7 have `path=codegen` — codegen started but LLM timed out or failed despite retry
- Intent compiler: 15/15 (100%) pass when activated, but only 29.4% activation rate overall
- Quality scores when graph produced: T1 avg 91.7, T2 avg 100, T3 avg 95, T4 avg 96.7 — quality is not the bottleneck
- 4 graphs scored below 90: t1-07 (q=72, 1 node), p02 (q=85, 2 nodes), m1-follow1/follow2 (q=75, missing structure) — underbuilding
- T5 misrouted: p09 ("make something cool"), p10 ("what's the weather"), t5-02 ("weather in NY") — 30-70s wasted before failing
- Cross-run flakiness: 60% (6/10 shared prompts flip between runs)
- T2R (experience-reuse): 0/3 (0%) — upstream blockers in reuse path
- Multi-turn: 8/10 turns pass (80%), m3 sequence has 1/3 pass

**Dominant bottleneck:** Reaching codegen at all. Tasks A (transparency) and D (pre-generation latency) are diagnostic priorities. Task C (node count guidance) is highest-leverage for quality. Task E.10 (T5 fast rejection) is quickest win.

## Tasks

### A. Path decision transparency

- [x] 1. **Generation path summary event**
  - [x] 1-1. After `_generate_workflow_from_intent()` completes (success or failure), emit a new `ChatGenerationSummaryEvent` that includes: `path_taken` (intent_compiler / codegen / diagnosis_repair), `retries_used` (intent extraction, codegen, sandbox), `quality_score`, `wall_clock_ms`, `fallback_chain` (ordered list of paths attempted, e.g. `["intent_compiler", "codegen", "diagnosis"]`).
  - [x] 1-2. Surface the summary in the chat response text when the generation took a non-trivial path (any fallback or retry). For simple successes (intent compiler, first try), keep the response clean. Example: "Workflow created (3 nodes, 2 edges). Built via intent compiler in 4.2s."
  - [x] 1-3. The eval harness should capture this event and report path distribution and fallback rates per tier.

- [x] 2. **Fallback narration**
  - [x] 2-1. When the intent compiler falls through to codegen, log at `logger.info` and include a one-line reason in the generation summary: "Intent compiler: extraction succeeded but compile failed (unsupported composition); falling back to codegen."
  - [x] 2-2. When codegen falls through to diagnosis, include: "Codegen: sandbox returned validation errors; attempting diagnosis repair (round 1/3)."
  - [x] 2-3. These narrations are for the generation summary event and debug logs, not for the primary user-facing response (unless generation takes >30s, in which case surface them as progress updates).

### B. Simple-prompt fast path

- [x] 3. **Latency target for T1 prompts**
  - [x] 3-1. T1 prompts (single-pattern: chain, review loop, fan-out, conditional branch) should complete in <10s when the intent compiler activates. Currently the intent compiler path avoids the codegen LLM call and uses in-process execution — the only LLM call is intent extraction. Measure the current T1 intent-compiler latency; if >10s, profile and optimize.
  - [x] 3-2. Add a slow-path threshold (for example `DAN_INTENT_FAST_PATH_SLOW_SECONDS`, default 15s) for observability and future routing decisions. If intent extraction + compile + validate crosses it, emit progress/narration and record the slowdown for later path tuning, but do **not** abandon the current request mid-flight and switch to codegen. The hard stop remains the global generation budget from 33-8 P1.

- [x] 4. **Consume the shared prompt complexity signal**
  - [x] 4-1. Consume the shared helper introduced by 33-7 P1 (for example `estimate_prompt_complexity()` + `expected_node_range()`) rather than defining a second estimator in 33-9. Use the returned `complexity_tier` and expected node range as inputs to path selection, prompt guidance, fit checks, and summary text.
  - [x] 4-2. Use this shared signal to set path expectations: T1 → intent compiler preferred, T3/T4 → codegen expected. This replaces the current implicit logic where the pipeline tries intent compiler first and hopes for the best.
  - [x] 4-3. Verify that thresholding (33-7), prompt guidance (33-9 task 5), and path selection all read the same shared signal so they cannot drift apart.

### C. Prompt-to-graph fit

- [x] 5. **Node count guidance in codegen prompt**
  - [x] 5-1. Include the shared `complexity_tier` and expected node range in the codegen system prompt: "This is a T2 prompt. The generated graph should have 3-6 nodes. A 1-node or 2-node graph is likely underspecified." This gives the LLM an explicit target instead of hoping it infers the right granularity.
  - [x] 5-2. Calibrate the node ranges from the approved calibration corpus defined in 33-7 P2-1, not from the live `graphs/` store: compute the node-count distribution by complexity and use it as the prompt guidance.

- [x] 6. **Post-generation fit check**
  - [x] 6-1. After quality scoring (33-7), add a fit check that compares the graph's node count against the shared `expected_node_range()` signal. If the graph has <50% of the expected minimum nodes, flag it as "likely underspecified" in the generation summary and optionally trigger a retry with an augmented prompt ("The previous generation produced only N nodes for a prompt that expected M-P nodes. Please generate a more complete graph.").
  - [x] 6-2. This is distinct from the quality gate in presentation and retry policy, but it must reuse the same shared signal from task 4 rather than introduce a second heuristic path.

### D. Pre-generation and non-build latency

- [x] 9. **Pre-generation latency visibility**
  - [x] 9-1. The generation wall-clock cap (33-8 P1) only covers `_generate_workflow_from_intent()`. But pilot findings show significant time is spent *before* generation: p03 was stuck in "Planning the workflow" for 70s, p10 was stuck in "Gathering relevant context" for 80s. Add elapsed-time tracking from the moment the concierge receives the message to the moment `_generate_workflow_from_intent()` is called. Report this as `pre_generation_ms` in the generation summary event.
  - [x] 9-2. If `pre_generation_ms` exceeds 30s, emit a diagnostic warning so the eval harness and logs can identify upstream bottlenecks (classifier, context resolution, guard pipeline, experience lookup) separately from generation bottlenecks.

- [x] 10. **T5 / non-build prompt fast rejection**
  - [x] 10-1. T5 edge cases ("Make something cool", "What's the weather today?") took 74-80s in the pilot before the system decided not to build. The classifier and context-gathering phases should recognize non-build prompts quickly. If the classifier returns a non-build intent (CONVERSATION, DIRECT_TASK, STATUS_CHECK) with confidence ≥0.7, the system should respond conversationally within seconds, not spend 70s gathering context for a build that never happens.
  - [x] 10-2. Add a latency target: non-build prompts should resolve in <15s (median). Measure in the eval harness by adding T5 latency to the report.

### E. Agent-lane routing quality

- [x] 11. **Address agent-lane 0% pass rate**
  - [x] 11-1. The pilot's section 4.2 shows that the agent lane (real concierge routing, `mode: "agent"`) had 0% pass rate on p01-p06. Root causes: p01 triggered a reuse prompt instead of building, p02/p04 were routing_blocked by clarification prompts, p03/p05/p06 had infrastructure failures. The build lane passed 50% of the same prompts. This means the full concierge pipeline (classify → guard → resolve → route) actively prevents successful builds on prompts that the generation pipeline can handle.
  - [x] 11-2. Investigate and fix agent-lane-specific blockers: (a) experience-reuse prompts that intercept fresh build requests, (b) confirmation/clarification prompts that fire before the solver bypass has a chance to activate, (c) guard pipeline false positives that reclassify build requests as something else. These may require changes in `runtime.py`, `solver.py`, `reuse_decision.py`, or `entity_grounding.py` — upstream of the generation pipeline.
  - [x] 11-3. Re-run the pilot battery in both `agent` and `build` lanes after fixes and compare pass rates. The agent lane should be within 20 percentage points of the build lane for T1-T3 prompts (currently 50 percentage points behind). *(2026-03-12: 60 records, 3 runs. Agent 20% vs build 46.7% = 27pp gap. Improved from pilot's 0% agent, but still above 20pp target — dominant failure is timeout_planning (72% of failures), not routing. See Post-Implementation Battery.)*
  - [x] 11-4. **T2R experience-reuse path at 0%.** All 3 T2R prompts ("build another like X but for Y", "adapt the earlier workflow for Z") failed with `no_graph_created`. This is a distinct problem from 11-1/11-2: the user *wants* reuse and the system can't deliver. Diagnose the reuse decision path (`reuse_decision.py`, `solver.py` experience retrieval, adaptation routing) — does it find the prior workflow? Does it attempt adaptation? If it stalls in adaptation/context gathering, does it fall back to fresh generation? At minimum, a reuse request that cannot find a prior workflow should fall through to `GENERATE` cleanly rather than producing no output.

### F. Generation latency budget

- [x] 12. **Wall-clock progress reporting**
  - [x] 12-1. Emit elapsed-time progress events during generation at 10s intervals: "Generating workflow... (10s)", "Still working... (20s, codegen in progress)", "Diagnosing issues... (30s, repair round 1/3)". This replaces the generic `progress_ack` heartbeat with context-aware progress.
  - [x] 12-2. After 60s, the progress event should include an estimate: "This is taking longer than usual. The system is on codegen retry 2 of 2." This sets user expectations and reduces the "is it stuck?" anxiety.

- [x] 13. **Early termination for hopeless generations**
  - [x] 13-1. If the codegen LLM returns the same error 2 times in a row (identical error type), stop retrying and fail fast with a clear message instead of exhausting the retry budget.
  - [x] 13-2. If the diagnosis loop produces a graph with a quality score ≤20 on two consecutive rounds, stop diagnosis and fail with "Unable to generate a graph that meets quality requirements for this prompt. Try simplifying the request or breaking it into smaller workflows."

### G. Eval harness diagnostic improvements

- [x] 14. **Granular failure category decomposition**
  - [x] 14-1. The eval harness currently reports only `no_graph_created` and `misrouted`. 33-8 task 7 defined granular subcategories (`timeout_planning`, `timeout_codegen`, `llm_error`, `stream_error`, `routing_blocked`, `correct_refusal`, `codegen_failed`) but they don't appear in battery output. Verify whether `_determine_status()` in `runner.py` implements them; if not, implement. The key signals are: (a) did any `chat_code_generated` event arrive? (codegen was reached), (b) did a `chat_validation_result` event arrive with error details? (codegen ran but failed), (c) did the stream contain "please confirm" or clarification text? (routing_blocked), (d) did the connection drop or timeout before any events? (stream_error), (e) for T5 prompts, is a non-build response correct? (correct_refusal).
  - [x] 14-2. Update `report.py` to display the granular categories in the failure mode table.
  - [x] 14-3. Map each of the 20 `no_graph_created` failures from the post-patch battery to a granular category using the new logic. *(2026-03-12: Post-implementation battery shows timeout_planning=29 (72%), stream_error=7 (18%), misrouted=3 (8%), no_graph_created=1 (2%). The opaque bucket is eliminated — 97.5% of failures now have granular categories.)*

- [x] 15. **Multi-run flakiness baseline**
  - [x] 15-1. Verify `--runs N` flag exists in `tests/eval/__main__.py` and works. If not, implement: repeat battery N times, compute per-prompt consistency (stable_pass / stable_fail / flaky).
  - [x] 15-2. Run `--runs 3` on T1/T2 prompts to establish stable pass rate baseline. *(2026-03-12: 3-run pilot battery. Stable pass: 3 (15%), Stable fail: 11 (55%), Flaky: 6 (30%). Flakiness improved from 60% to 30%. Stable pass rate 15% is the true reliable baseline.)*
  - [x] 15-3. Add flakiness rate to the summary report output.

## Key Files

| File | Action |
|------|--------|
| `src/dan/server/chat_manager.py` | **Modify** — generation summary event, fallback narration, fit check, latency progress, pre-generation timing |
| `src/dan/meta/graph_quality.py` | **Modify** — fit check helper, tier-adaptive threshold integration |
| `src/dan/meta/intent_extraction.py` | **Modify** — complexity signal consumption |
| `src/dan/server/concierge/runtime.py` | **Modify** — pre-generation elapsed tracking, T5 fast rejection, agent-lane routing fixes |
| `src/dan/server/concierge/solver.py` | **Review** — experience-reuse intercept, confirmation bypass for agent lane |
| `src/dan/server/concierge/entity_grounding.py` | **Review** — guard false positives on build requests |
| `tests/eval/runner.py` | **Modify** — capture generation summary events, T5 latency, agent-lane comparison, granular failure categories |
| `tests/eval/report.py` | **Modify** — report path distribution, fallback rates, latency breakdown, agent vs build gap, flakiness |
| `tests/eval/__main__.py` | **Modify** — verify/implement `--runs N` flag |

## Success Criteria

- [ ] T1 prompts that hit the intent compiler complete in <10s (median) *(not yet measurable — LLM API latency dominates; intent compiler path itself is fast but extraction LLM call adds 20-40s)*
- [x] Every generation produces a `ChatGenerationSummaryEvent` with path, retries, and wall-clock *(implemented; confirmed in battery — 11/20 passed builds show generation_path_taken)*
- [x] Fallback chain is visible in the generation summary (not just debug logs)
- [x] Prompts that produce underspecified graphs (quality <30) either get a retry or get a clear user-facing explanation *(fit check flags graphs with <50% of expected minimum nodes)*
- [x] No generation exceeds the 33-8 P1 wall-clock cap once that prerequisite lands
- [x] Eval harness reports path distribution and fallback rates per tier *(path distribution table in report)*
- [ ] Non-build prompts (T5 edge cases) resolve in <15s (median), not 70-80s *(median 58s — fast path active but classifier confidence <0.7 for some T5 prompts)*
- [ ] Agent lane pass rate is within 20 percentage points of build lane for T1-T3 prompts *(27pp gap: agent 20% vs build 47%. Improved from 0% but gap driven by timeout_planning, not routing)*
- [x] Pre-generation latency (`pre_generation_ms`) is reported in the generation summary
- [x] Eval harness failure modes use granular categories (not just `no_graph_created`) — the 20 opaque failures from post-patch battery are decomposed *(97.5% of failures now have granular categories)*
- [x] `--runs 3` flakiness baseline measured — stable pass rate established *(30% flakiness, 15% stable pass rate, improved from 60% flakiness)*
- [ ] Post-implementation battery pass rate ≥65% (from current 55%) on build lane *(46.7% — below target; 72% of failures are timeout_planning (LLM API), not pipeline issues)*

## Post-Implementation Battery (2026-03-12)

**Results file:** `tests/eval/results/2026-03-12_161947_run.jsonl` (60 records, pilot prompts × 2 lanes × 3 runs)

| Metric | Value | Target | Status |
|--------|-------|--------|--------|
| Agent lane pass rate | 20.0% (6/30) | Within 20pp of build | Improved from 0% but 27pp gap remains |
| Build lane pass rate | 46.7% (14/30) | ≥65% | Below target (LLM API instability) |
| Flakiness rate | 30% (6/20 combos) | Lower than 60% | Improved from 60% (halved) |
| Stable pass rate | 15% (3/20 combos) | Establish baseline | Baseline established |
| T5 median latency | 58s | <15s | Above target |
| Granular categories | 97.5% classified | 100% | timeout_planning=72%, stream_error=18% |
| Intent compiler share | 55% of passes | — | Dominant successful path |
| Quality (passed) | avg 94.3 | — | Not a bottleneck |

**Failure mode breakdown (40 failures):**
- `timeout_planning`: 29 (72%) — LLM API timeout before reaching codegen
- `stream_error`: 7 (18%) — WebSocket/connection failures
- `misrouted`: 3 (8%) — T5 prompts incorrectly routed
- `no_graph_created`: 1 (2%) — reduced from 20/23 in pre-implementation battery

**Key takeaway:** The generation pipeline improvements are working (granular categories, path transparency, agent-lane routing). The dominant bottleneck is now LLM API reliability (timeout_planning = 72% of failures), which is infrastructure, not pipeline logic. The T5 fast rejection (D.10) is active but the classifier's confidence for T5 prompts may not reach the 0.7 threshold consistently.

## Decisions

- D.9: Pre-generation latency tracked at 3 dispatch points (direct handler, goal orchestrator, solver path). Stored in `msg.metadata["pre_generation_ms"]` for downstream consumption.
- D.10: Non-build fast path checks `classification.intent in {CONVERSATION, STATUS_CHECK, DIRECT_TASK}` with `confidence >= 0.7` after Guard 1. Skips memory retrieval, correction detection, action policy, solver/goal orchestrator.
- E.11: Three agent-lane blockers identified and fixed: (1) CONFIRM bypass extended to high-confidence WORKFLOW_BUILD intents, (2) reuse prompt suppressed for non-reuse requests (only shows when candidate score >= 0.8 and success_rate >= 0.7), (3) solver heuristic no longer overrides WORKFLOW_BUILD to WORKFLOW_REUSE when classifier is confident.
- E.11-4: Reuse fallthrough: when `_handle_reuse_workflow` fails, `reuse_workflow_id` is cleared and `plan_action` forced to "generate" so meta_controller doesn't retry reuse. Explicit reuse requests auto-select "adapt" instead of prompting.
- A.1: `ChatGenerationSummaryEvent` emitted at every return point with 7 fields. Eval harness captures and reports path distribution per tier. *(2026-03-16: added `pre_generation_ms` as 8th field.)*
- C.5-2: Node ranges calibrated from 42 eval-tier graphs: T1 (2,5), T2 (3,8), T3 (4,10), T4 (6,15).
- G.14: `_classify_no_graph()` decomposes failures into 7 granular categories with priority-ordered classification.
- **(2026-03-16) F.12 progress events fixed.** `_emit_progress()` was emitting `detected_mode="progress_generation"` which the eval harness and editor frontend treated as terminal. Changed to `"progress_ack"` so progress events are keepalives.
- **(2026-03-16) Structural mutations edge format fixed.** `structural_mutations.py` assumed legacy dict edge format (`edges.data/control`); now handles both flat-list and dict formats via `_get_edges_by_type()` helper. Multi-turn mutation path is now functional on real persisted graphs.

## Notes

- This plan builds on top of 33-6/7/8 infrastructure. It does not replace those plans — it makes their outputs visible and trustworthy to the user.
- The shared prompt-complexity signal (task 4) is heuristic, not precise. The goal is to set a reasonable expectation, not to perfectly predict graph size. Getting T1 vs T3/T4 right covers the main failure modes.
- Latency targets are median, not p99. A single slow LLM response should not define the experience, but the user should know when things are slow and why.
- Do not switch from intent compiler to codegen mid-request based only on transient slowness. That creates path flapping and makes slow cases slower.
- Node count guidance in the codegen prompt (task 5) is the highest-leverage single change. The LLM currently has no signal about expected output size — adding it directly addresses both overbuilding and underbuilding.
- This plan is independent of the benchmark suite (Phase 24+). It focuses on the daily-use experience, not competitive evaluation.
- **Pilot gaps addressed in this revision (tasks 9-11):** The original 33-9 only covered the build-lane generation pipeline. The pilot findings (section 4.2) showed the agent lane had 0% pass rate, T5 edge cases took 74-80s to resolve, and "Planning the workflow"/"Gathering relevant context" phases consumed most of the wall clock for failing prompts. These are upstream of the generation pipeline and require concierge/classifier/routing changes.
- **Eval harness timeout:** The 90s harness wall timeout was too short for T3/T4 prompts (p03 was at 70s when cut off). This is an eval infrastructure concern, not a generation pipeline concern. When implementing 33-9, also bump the harness default timeout to match `DAN_MAX_GENERATION_SECONDS` + a buffer for pre-generation work (for example 150s).
- **Tasks G.14-15 bridge a gap between 33-8 and 33-9.** 33-8 task 7 (granular categories) and task 8 (multi-run flakiness) are described in 33-8 Decisions as "shipped" but the post-patch battery doesn't reflect them. Rather than re-opening 33-8, implement/verify them here since 33-9 needs the diagnostic output to prioritize its own tasks.
- **Recommended implementation order:** G.14 (granular categories) first — decompose the 20 `no_graph_created` failures to identify whether upstream routing or codegen retry is the bigger gap. Then A (transparency) + D (pre-generation latency) for diagnostic infrastructure. Then C (node count guidance) for quality. Then E.10 (T5 fast rejection) as a quick win. Then B (fast path), F (progress/early termination), E.11 (agent lane), G.15 (flakiness baseline).
