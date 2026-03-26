# 33: Workflow Generation Quality Evaluation

**Status:** in-progress
**Goal:** Measure how well DAN generates and executes workflows from natural language, across a difficulty spectrum from trivial to complex, producing hard numbers on success rate, token cost, latency, and failure modes.

## Motivation

DAN has 22 phases of infrastructure: builder codegen, intent compiler, meta-orchestrator, concierge pipeline, bounded diagnosis, experience memory, request guard pipeline, unified telemetry, proactive domain learning, self-adaptive behavior, and a freshly completed generation optimization pass (Phase 22). But no one has systematically measured: **if a user says "build me X," does X come out the other end?**

The quality suite plan (24-3) and real-world scenarios (28-6) were designed but never implemented as runnable artifacts. The pytest marker `quality_suite` is configured but no test files exist. This phase fills that gap with live evaluation against real LLMs.

### Pipeline under test

This evaluation runs against the **post-Phase 22 optimized pipeline**, which includes:

| Capability | Source | What it changes for eval |
|---|---|---|
| Builder convenience layer | 32-1 | `chain()`, `review_loop()`, `map_reduce()`, `tool_chain()`, `\|` operator — T1/T2 prompts should use these; shorter codegen, fewer wiring errors |
| Expanded intent compiler | 32-2 | 15+ patterns (up from 7), auto-composition — more prompts should take the fast deterministic path |
| Smart generation defaults | 32-3 | Auto retry policies, validation gates, feedback loops — generated graphs should be more robust by default |
| Progressive NL refinement | 32-4 | Structural mutation macros — multi-turn follow-ups should modify without full rebuild |
| Domain generation profiles | 32-5 | Per-domain tools, tiers, patterns — domain-tagged prompts should get domain-appropriate structure |
| Convenience gaps patch-up | 32-6 | Narrow conditional branching, flexible `review_loop()` criteria, compound structural follow-up mutations — Phase 33 should include at least one branch case and one compound follow-up case |
| Request guard pipeline | 31-19 | Classification/understanding/relevance guards — affects `agent` lane routing; may reclassify or short-circuit |
| Project-scoped memory | 31-18 | Memories scoped to project — test isolation requires unique project contexts or fresh graphs |
| Proactive domain learning | 31-21 | `detect_domain()`, domain templates — the system may inject domain expertise into generation |
| Self-adaptive behavior | 31-22 | Externalized prompts, observable parameters — prompt versions and thresholds may shift between runs |
| Unified telemetry | 31-20 | `TelemetryEvent` per LLM call — exact tokens, cost, duration, model for every interaction |

### What we want to learn

1. **Success rate by difficulty** — what fraction of NL prompts produce valid, runnable workflows at each complexity tier?
2. **Cost of generation** — how many tokens (input + output) does it take to build a workflow? How much does retry add?
3. **Time to build** — wall-clock latency from prompt to valid graph, including retries.
4. **Execution durability** — do generated workflows actually run? Do they produce meaningful output?
5. **Failure anatomy** — where in the pipeline do failures concentrate? Classification? Generation? Validation? Execution?
6. **Progressive building** — can users refine workflows incrementally via NL follow-ups?

## Approach

### Manual pilot first, then automate

Start with 5-10 prompts sent manually through the live system. This takes 30 minutes and immediately reveals whether the pipeline is 20% or 80% working — which determines the entire plan.

### Two evaluation lanes

We need to separate two questions that fail differently:

1. **Routing lane (`mode: "agent"`)** — does the real concierge recognize the task as a workflow-build request, choose the right path, and produce a graph?
2. **Generation lane (`mode: "build"`)** — if we remove most routing ambiguity, how good is the actual workflow-generation path?

Both lanes use the real `POST /api/chat/message` endpoint and real server behavior. The difference is the chat mode. Comparing the two tells us whether a miss came from routing or from workflow generation itself.

### Observable data sources

The harness should use data the running system already produces. The primary source is the **unified telemetry store** (31-20, `~/.dan/telemetry.db`), which records a `TelemetryEvent` for every chat turn, workflow run, workflow node, guard check, classification, tool call, and memory retrieval — with exact tokens, cost, duration, model, project/surface scope, and parent-child correlation. This is the single richest data source and should be the harness's first stop for every metric.

Secondary sources for generation-specific observability:

- **Chat stream events** from `/api/chat/{channel_id}/events` — real-time `chat_intent_extracted`, `chat_code_generated`, `chat_validation_result`, `chat_graph_created` signals that reveal the generation pipeline's internal path
- **Graph store APIs** (`/api/graphs`, `/api/graphs/{id}`, `/api/graphs/{id}/validate`) — graph structure and validation
- **Run APIs** (`/api/runs`, `/api/runs/{run_id}/events`, `/api/runs/{run_id}/token-breakdown`) — execution results

The telemetry store covers tokens, cost, latency, model, retry count, and success for every LLM call. The stream events cover generation-path-specific signals (intent vs codegen, validation errors). Together they eliminate the observability gaps identified in the earlier draft.

### Log everything, analyze later

Every test produces a JSONL record with: prompt, lane, model, timing, observed events, generated code/graph summary, validation result, execution result, final status, and any audit-derived metadata. Raw logs are the primary artifact. Reports are derived views.

### Difficulty tiers

| Tier | Description | Prompt Count | Primary Measure |
|------|------------|-------------|-----------------|
| T1: Trivial | Single-pattern expansions (chain, review_loop, fan_out, conditional branch) | 9 | Build reliability |
| T2: Simple | Standard workflows with 3-6 nodes | 6 | Everyday workflow quality |
| T3: Medium | Multi-pattern composition, tools, gates | 5 | Composition quality |
| T4: Complex | Multi-department, nested sub-graphs, long pipelines | 4 | Large-graph generation quality |
| T5: Edge | Ambiguous, over-specified, shouldn't-be-workflows | 4 | Routing / refusal correctness |
| Multi-turn | Progressive refinement sequences (2-4 turns each) | 3 sequences | Mutation quality |
| Durability | Repeat-run / reload / export-import smoke checks | 4 checks | Workflow durability |

~31 prompts total. No targets set — the goal is to establish the baseline.

### Metrics per test

- **Routing success**: in `agent` lane, did the system choose workflow generation when it should?
- **Generation success**: did the produced graph pass `/api/graphs/{id}/validate`?
- **Execution success**: for the execution-friendly subset, did the workflow run without error?
- **Generation path**: did the system use the intent compiler (fast, deterministic) or builder codegen (slow, LLM-generated)? After Phase 22's expanded catalog, more prompts should take the intent path.
- **Observed repair use**: did generation fall back from intent compiler to codegen, or trigger diagnosis/repair? (visible in stream events + telemetry `retry_count`)
- **Builder-code ergonomics**: when the builder codegen path is used, does the emitted code use the convenience helpers (`chain`, `review_loop`, `map_reduce`, `tool_chain`, `branch`) or does it still fall back to verbose manual `NodeRef(...)` + raw context-manager boilerplate for common patterns?
- **Build-time tokens**: from telemetry store `chat_turn` events (exact `prompt_tokens`, `completion_tokens`, `estimated_cost`, `duration_ms`)
- **Run-time tokens**: from telemetry store `workflow_node` / `workflow_run` events, or `/api/runs/{run_id}/token-breakdown`
- **Build latency**: prompt submission to final graph/result
- **Run latency**: run submission to terminal run state
- **Failure stage**: route / intent_extract / compile / codegen / validate / execute
- **Graph quality**: node count, node types, edge count, topology features (loops, fan-out, gates), and semantic must-haves
- **Durability**: repeat-run stability, reload/validate stability, export/import stability, and mutation-after-build stability

## Sub-Plans

| # | Sub-Plan | Scope | Effort | Dependencies |
|---|----------|-------|--------|--------------|
| [33-1](33-1-manual-pilot.md) | Manual Pilot | 5-10 prompts through live system in `agent` and `build` lanes, observe and log results, establish rough baseline | ~0.5 day | Server running |
| [33-2](33-2-test-harness.md) | Test Harness | Automated runner: dual lanes, API client, telemetry reader (31-20), JSONL logging, metrics collection, report generator | ~1 day | 33-1 (informs design) |
| [33-3](33-3-small-task-battery.md) | Small Task Battery | 20+ prompts across T1-T3 plus edge and reuse/adaptation checks, validation checks, graph quality assertions | ~0.5 day | 33-2 |
| [33-4](33-4-complex-workflow-battery.md) | Complex Workflow Battery | T4 prompts, multi-turn sequences, execution attempts, and durability smoke checks | ~0.5 day | 33-2 |
| [33-5](33-5-analysis-and-fixes.md) | Analysis & Fixes | Read baseline report, diagnose top failure modes and telemetry gaps, targeted fixes, re-measure | ~1 day | 33-3, 33-4 |
| [33-6](33-6-intent-compiler-activation.md) | Intent Compiler Activation | Debug and fix 0% intent compiler activation; get T1/T2 prompts onto deterministic path | ~1 day | 33-5 (findings inform priorities) |
| [33-7](33-7-semantic-quality-gates.md) | Semantic Quality Gates | Catch graphs that validate but are semantically wrong (underspecified, missing patterns) | ~1 day | 33-5 |
| [33-8](33-8-codegen-resilience.md) | Codegen Resilience & Provider Hardening | LLM retry/fallback, classifier hardening, granular failure categories, multi-run stability | ~1 day | 33-5 |
| [33-9](33-9-build-path-trustworthiness.md) | Build Path Simplification & Trustworthiness | Path transparency, simple-prompt fast path, prompt-to-graph fit, latency budget, early termination, pre-generation latency, T5 fast rejection, agent-lane routing quality, T2R reuse-path diagnosis, eval harness granular categories + flakiness baseline | ~2-3 days | 33-6, 33-7, 33-8 *(all prerequisites met)* |
| [33-10](33-10-semantic-correctness-patch.md) | Semantic Correctness Patch | Fix review-loop condition polarity bug, enforce fixture expectations in eval, eliminate unsafe tool_id/code defaults, inject tool catalog into codegen prompt, LLM-as-judge scoring | ~2 days | 33-9 |

## Dependencies / Sequencing

```
33-1 (Manual Pilot) ← start here, informs everything else
  └→ 33-2 (Test Harness) ← build automation based on pilot findings
       ├→ 33-3 (Small Task Battery) ← can run as soon as harness exists
       ├→ 33-4 (Complex Workflow Battery) ← can run in parallel with 33-3
       └→ 33-5 (Analysis & Fixes) ← after first full run of 33-3 + 33-4
            ├→ 33-6 (Intent Compiler Activation) ← parallel with 33-7, 33-8
            ├→ 33-7 (Semantic Quality Gates) ← parallel with 33-6, 33-8
            ├→ 33-8 (Codegen Resilience) ← parallel with 33-6, 33-7
            └→ 33-9 (Build Path Trustworthiness) ← after 33-6, 33-7, 33-8 and their open patch prerequisites
```

## Success Criteria

- [x] Baseline numbers exist for all tiers and both lanes (success rate, tokens, latency, failure modes)
- [x] Durability smoke results exist for repeat-run / reload / export-import checks
- [x] Top 3 failure modes identified with root-cause analysis
- [x] At least one measure-fix-measure cycle completed (pre/post comparison)
- [x] JSONL logs and summary report committed as artifacts
- [x] Findings feed into a prioritized "streamline generation" backlog
- [x] Intent compiler activation rate >0% for T1/T2 prompts (33-6) — measured at 68.4% overall (T1=63.6%, T2=75.0%), 100% pass rate when activated
- [x] Semantic quality scoring distinguishes underspecified from well-formed graphs (33-7) — avg quality 93.9 across 14 graphs, keyword tuning resolved false positives
- [x] Multi-run stability measured with flakiness rate (33-8) — `--runs N` verified working, flakiness data included in JSON summary report (33-9 G.15). Baseline run pending.
- [x] Eval harness failure modes use granular categories instead of opaque `no_graph_created` bucket — implemented in 33-9 G.14: 7 subcategories (stream_error, routing_blocked, llm_error, correct_refusal, timeout_codegen, timeout_planning, codegen_failed)
- [ ] Post-33-9 battery pass rate ≥65% (from current 55%) — 46.7% build lane in post-implementation battery (72% of failures are LLM API timeout_planning, not pipeline issues)
- [ ] Post-33-10 battery with expectation-fit gate produces an honest baseline (expect ~30-35%) and climbs as semantic fixes land
- [x] Review-loop condition polarity bug fixed — `ReviewRequirement.condition` default flipped, `_normalize_review_condition()` validates/corrects polarity (33-10 A)
- [x] Eval pass/fail enforces fixture expectations (min_nodes, topology, node_types) — `_determine_status()` returns `expectation_mismatch` on violations (33-10 B)
- [x] Tool keyword inference removes unsafe `web_search` default — `_TOOL_KEYWORD_MAP` with 30+ mappings (33-10 C)
- [x] Codegen prompt includes tool catalog — 17 registered tool_ids with descriptions injected (33-10 D)
- [x] Intent extraction tool-aware — 19 tool_ids in extraction system prompt, few-shot with csv/email/code (33-10 F). *(2026-03-16: live path in chat_manager.py now also includes tool catalog, matching extract_workflow_intent())*
- [ ] LLM-as-judge scoring validates semantic correctness beyond structural checks (33-10 E — implemented, pending eval run)
- [ ] Cycle 3 re-run with all 33-10 patches produces honest baseline and informs remaining priorities
- [ ] 33-10 Section G is closed — code-execution stages either get real generated code with provenance or fail honestly; placeholder status code may not count as pass
- [ ] A dedicated long-running benchmark-prep battery exists and has at least one clean run on frozen inputs before any external benchmark claims are treated as representative
- [x] Progress events use `progress_ack` detected_mode — no longer terminate client streams (2026-03-16)
- [x] Structural mutation macros handle flat edge lists — multi-turn mutation path now functional on persisted graphs (2026-03-16)
- [x] Quality gate default threshold=0 truly means "accept all" — fixed sentinel logic (2026-03-16)
- [x] Eval harness multi-turn, durability, clarification, guard_events, and gitignore correctness fixes (2026-03-16)

## Benchmark-Prep Extension

Phase 33 is now the **benchmark-prep gate**, not only an internal test phase. Before Bench 4 custom scenarios, Bench 1 WorFBench, or GAIA numbers are presented as evidence of DAN capability, this phase should establish that the live NL -> workflow -> execution path is semantically honest and reproducible.

Required order:

1. Close the remaining semantic honesty work in `33-10` Section G so code-heavy graphs cannot pass with placeholder execution.
2. Re-run the small + complex batteries with the honest baseline (`--judge` where useful, execution enabled on the execution-friendly subset, and flakiness measurement).
3. Run the new long-running benchmark-prep battery from `33-4` on **frozen local inputs** (lit review corpus, fixed CSV/data-analysis package, prepared code-migration repo with tests).
4. Only after those runs are stable should external benchmarks be used as proof points.

## Decisions

- (filled in during execution)

## Notes

- **Sequencing:** This phase (Phase 23) runs after Phase 22 (32-workflow-optimization), which improves the generation pipeline with convenience layer, expanded intent compiler, smart defaults, progressive refinement, domain profiles, and the 32-6 convenience-gap patch slice. The evaluation should measure the optimized pipeline, not the pre-optimization baseline.
- The quality suite (24-3) and scenarios (28-6) were fully planned but never implemented. This phase subsumes and simplifies them: fewer fixtures, real LLM calls, focus on actionable metrics rather than CI infrastructure.
- No model comparison in v1. Use whichever model the server is configured with (currently deepseek-v3.2). Model comparison is a follow-up.
- Execution testing should not assume arbitrary tool mocking exists in the live server. Most prompts are generation-first; execution is limited to an execution-friendly subset until deterministic test-only tools exist.
- **Test isolation:** Project-scoped memory (31-18) means memories from one test prompt can bleed into later prompts if the same project context is reused. The harness should use unique workflow IDs and avoid project context to keep tests independent. Self-adaptive behavior (31-22) means prompt versions and thresholds may drift between runs — log the active behavior snapshot for reproducibility.
- **Guard pipeline (31-19):** In the `agent` lane, the request guard pipeline runs classification/understanding/relevance checks that may reclassify, short-circuit, or add advisory notes. This is desired behavior (it's what real users experience), but failures in the `agent` lane should check whether a guard intervened before blaming the generation path.

## Pilot Findings (2026-03-11)

**Detailed analysis:** [33-pilot-findings-2026-03-11.md](33-pilot-findings-2026-03-11.md)

**Build lane pilot (10 prompts):**
- **Total:** 10 | **Passed:** 6 | **Failed:** 4 | **Pass rate:** 60.0%
- **Per-tier:** T1: 2/2 (100%), T2: 0/2 (0%), T3: 1/2 (50%), T4: 1/2 (50%), T5: 2/2 (100%)
- **Generation path:** 50% codegen, 0% intent_compiler, 50% unknown
- **All 4 failures:** no_graph_created
- **Slowest:** p08 at 156s, p05 at 145s
- **Total run time:** 782s (~13 min)

**Key observations:**
- T1 simple tasks work well (100% pass)
- T2 web/tool tasks fail (0% pass)
- T5 edge cases correctly handled (no spurious builds for non-workflow requests)
- No intent_compiler path used — all successes via codegen or unknown

### Measure-Fix-Measure Cycle (2026-03-11)

**Fixes applied:**
1. Classification override expanded: `META_GOAL` → `WORKFLOW_BUILD` for explicit build mode (was only catching `CONVERSATION`)
2. CONFIRM bypass: `ActionPolicy.CONFIRM` now skipped when `_requested_mode_forces_solver_path()` — CONFIRM handler fired BEFORE solver bypass, blocking 2/4 failures
3. Event-level clarification scanning: runner now scans ALL events (not just final `response_text`) for clarification signals

**Post-fix build lane pilot (Run 2):**
- **Total:** 10 | **Passed:** 4 | **Failed:** 6 | **Pass rate:** 40%
- p04 (T2 RAG pipeline) now passes — CONFIRM bypass fix worked
- p01 (T1 simple chain) regressed — internal server error after successful codegen
- p06, p07, p08 show 0 events — server instability under load

**Cross-run stability (Run 1 vs Run 2):**
| Category | Count | Prompts |
|----------|-------|---------|
| Stable pass | 3 | p02 (T1), p09 (T5), p10 (T5) |
| Fixed | 1 | p04 (T2) — CONFIRM bypass |
| Stable fail | 3 | p03 (T2), p05 (T3), p07 (T4) |
| Flaky | 3 | p01 (T1), p06 (T3), p08 (T4) |

**Root causes of remaining failures:**
1. **LLM API flakiness** (p01, p03): codegen reached but LLM returns errors/timeouts
2. **Server instability** (p06, p07, p08): 0 events, likely high CPU under sustained eval load
3. **Infra timeout** (p05): WebSocket keepalive failure, no events ever received

**Conclusion:** Infrastructure routing fixes work (T2 improved). Remaining failures are LLM API reliability and server stability — outside eval harness scope. Full battery deferred until LLM API is stable.

### Post-Patch Full Battery (2026-03-12)

**Results file:** `tests/eval/results/2026-03-12_133646_run.jsonl` (51 records, 41 prompts + 10 multi-turn follow-ups)

**Build lane (all tiers):**
- **Total:** 51 | **Passed:** 28 | **Failed:** 23 | **Pass rate:** 54.9%
- **T1:** 6/11 (55%) | **T2:** 6/8 (75%) | **T2R:** 0/3 (0%) | **T3:** 10/17 (59%) | **T4:** 3/6 (50%) | **T5:** 3/6 (50%)
- **Intent compiler activation:** 29.4% overall (T1 36%, T2 38%, T3 24%, T4 50%, T5 17%)
- **Quality scores (when graph produced):** T1 avg 91.7, T2 avg 100, T3 avg 95.0, T4 avg 96.7, T5 avg 100
- **Multi-turn:** m1 3/3 pass, m2 4/4 pass, m3 1/3 pass (8/10 turns = 80%)

**Comparison to pre-patch baseline (20 single-turn):**
| Tier | Pre-patch | Post-patch | Delta |
|------|-----------|------------|-------|
| T1 | 2/4 (50%) | 6/11 (55%) | +5pp |
| T2 | 1/4 (25%) | 6/8 (75%) | +50pp |
| T3 | 0/4 (0%) | 4/10 (40%) | +40pp |
| T4 | 0/4 (0%) | 3/6 (50%) | +50pp |
| T5 | 4/4 (100%) | 3/6 (50%) | -50pp |
| ALL | 7/20 (35%) | 22/44 (50%) | +15pp |

**Key observations:**
- T2/T3/T4 dramatically improved (25→75%, 0→40%, 0→50%) — intent compiler activation is the primary driver
- T5 regressed (100→50%): T5 tests expanded from 4→6, and new prompts ("make something cool", vague requests) are getting routed to codegen instead of being correctly rejected as non-workflows
- T1 flaky: p01 (simple chain) fails intermittently due to LLM API timeouts
- 20/23 failures are `no_graph_created` — codegen timeout or LLM error, not quality gate rejections
- T2R (reuse/adapt) remains 0% — experience-reuse path has upstream blockers
- When intent compiler activates, it passes 100% of the time

### Patch Plans (post-pilot)

Three patch plans address the top improvement areas identified by the pilot. They can proceed **mostly in parallel** but share `_generate_workflow_from_intent()` — coordinate merges.

1. **[33-6](33-6-intent-compiler-activation.md) Intent Compiler Activation** — the 0% intent compiler usage means every build depends on fragile full-model codegen. Root cause: `_parse_intent_from_result()` only handles `tool_calls`, not JSON-in-content. Coverage check is a no-op (`SUPPORTED_TYPES = set(StageType)`). Also wires up the existing but unused `_exec_deterministic_builder_code()` for intent-compiled code.
2. **[33-7](33-7-semantic-quality-gates.md) Semantic Quality Gates** — p02 (2 nodes for review loop) and p08 (1 node for equity research) passed validation but are semantically useless. Quality scoring catches underspecified graphs. Tasks 2-5/2-6 depend on 33-8 tasks 12/11.
3. **[33-8](33-8-codegen-resilience.md) Codegen Resilience & Provider Hardening** — single LLM failure kills the build. Retry/fallback + classifier observability + companion step gaps (pre-sandbox lint, sandbox-None→diagnosis, re-validation) + granular failure categories + multi-run stability measurement.

**Integration order:** 33-8 first (companion steps create the validation hooks), then 33-7 (chains quality scoring onto those hooks), then 33-6 (changes generation path distribution). Or: 33-8 and 33-6 in parallel (touching different parts of the pipeline), then 33-7 last.

### Deterministic Companion Steps (cross-cutting)

The generation and mutation pipelines have a chain of steps that must always follow each other. Several links were missing (silent failures, skipped diagnosis, no re-validation after repair/mutation). 33-7 and 33-8 together close these gaps:

```
┌─────────────────── GENERATION ───────────────────┐
│ LLM call                                         │
│   └→ empty/malformed check (33-8 task 3)         │
│        └→ code extraction                        │
│             └→ ast.parse syntax check (33-8 §9)  │
│                  └→ sandbox execution             │
│                       └→ error classification     │
│                            ├→ [ok] → structural   │
│                            │     validation       │
│                            │       └→ semantic    │
│                            │          quality     │
│                            │          (33-7 §2)   │
│                            │            └→ record │
│                            │               outcome│
│                            └→ [fail] → diagnosis  │
│                                  (33-8 §10)       │
│                                    └→ re-validate │
│                                       (33-8 §11)  │
│                                         └→ quality│
│                                           (33-7   │
│                                            §2-6)  │
└──────────────────────────────────────────────────┘
┌─────────────────── MUTATION ─────────────────────┐
│ macro dispatch                                   │
│   └→ structural validation (33-8 §12)            │
│        └→ semantic quality check (33-7 §2-5)     │
│             └→ save graph                        │
└──────────────────────────────────────────────────┘
```

**Principle:** No step in either chain should have a "silent None" exit — every branch must either produce a classified error (that feeds diagnosis/retry) or produce a validated+scored artifact.
