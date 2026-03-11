# 32: Workflow Generation Quality Evaluation

**Status:** not-started
**Goal:** Measure how well DAN generates and executes workflows from natural language, across a difficulty spectrum from trivial to complex, producing hard numbers on success rate, token cost, latency, and failure modes.

## Motivation

DAN has 21 phases of infrastructure: builder codegen, intent compiler, meta-orchestrator, concierge pipeline, bounded diagnosis, experience memory, 1300+ tests. But no one has systematically measured: **if a user says "build me X," does X come out the other end?**

The quality suite plan (24-3) and real-world scenarios (28-6) were designed but never implemented as runnable artifacts. The pytest marker `quality_suite` is configured but no test files exist. This phase fills that gap with live evaluation against real LLMs.

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
| T1: Trivial | Single-pattern expansions (chain, review_loop, fan_out) | 8 | Build reliability |
| T2: Simple | Standard workflows with 3-6 nodes | 6 | Everyday workflow quality |
| T3: Medium | Multi-pattern composition, tools, gates | 5 | Composition quality |
| T4: Complex | Multi-department, nested sub-graphs, long pipelines | 4 | Large-graph generation quality |
| T5: Edge | Ambiguous, over-specified, shouldn't-be-workflows | 4 | Routing / refusal correctness |
| Multi-turn | Progressive refinement sequences (2-4 turns each) | 3 sequences | Mutation quality |
| Durability | Repeat-run / reload / export-import smoke checks | 4 checks | Workflow durability |

~30 prompts total. No targets set — the goal is to establish the baseline.

### Metrics per test

- **Routing success**: in `agent` lane, did the system choose workflow generation when it should?
- **Generation success**: did the produced graph pass `/api/graphs/{id}/validate`?
- **Execution success**: for the execution-friendly subset, did the workflow run without error?
- **Observed repair use**: did generation fall back from intent compiler to codegen, or trigger diagnosis/repair? (visible in stream events + telemetry `retry_count`)
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
| [32-1](32-1-manual-pilot.md) | Manual Pilot | 5-10 prompts through live system in `agent` and `build` lanes, observe and log results, establish rough baseline | ~0.5 day | Server running |
| [32-2](32-2-test-harness.md) | Test Harness | Automated runner: dual lanes, API client, telemetry reader (31-20), JSONL logging, metrics collection, report generator | ~1 day | 32-1 (informs design) |
| [32-3](32-3-small-task-battery.md) | Small Task Battery | 20+ prompts across T1-T3 plus edge and reuse/adaptation checks, validation checks, graph quality assertions | ~0.5 day | 32-2 |
| [32-4](32-4-complex-workflow-battery.md) | Complex Workflow Battery | T4 prompts, multi-turn sequences, execution attempts, and durability smoke checks | ~0.5 day | 32-2 |
| [32-5](32-5-analysis-and-fixes.md) | Analysis & Fixes | Read baseline report, diagnose top failure modes and telemetry gaps, targeted fixes, re-measure | ~1 day | 32-3, 32-4 |

## Dependencies / Sequencing

```
32-1 (Manual Pilot) ← start here, informs everything else
  └→ 32-2 (Test Harness) ← build automation based on pilot findings
       ├→ 32-3 (Small Task Battery) ← can run as soon as harness exists
       ├→ 32-4 (Complex Workflow Battery) ← can run in parallel with 32-3
       └→ 32-5 (Analysis & Fixes) ← after first full run of 32-3 + 32-4
```

## Success Criteria

- [ ] Baseline numbers exist for all tiers and both lanes (success rate, tokens, latency, failure modes)
- [ ] Durability smoke results exist for repeat-run / reload / export-import checks
- [ ] Top 3 failure modes identified with root-cause analysis
- [ ] At least one measure-fix-measure cycle completed (pre/post comparison)
- [ ] JSONL logs and summary report committed as artifacts
- [ ] Findings feed into a prioritized "streamline generation" backlog

## Decisions

- (filled in during execution)

## Notes

- The quality suite (24-3) and scenarios (28-6) were fully planned but never implemented. This phase subsumes and simplifies them: fewer fixtures, real LLM calls, focus on actionable metrics rather than CI infrastructure.
- No model comparison in v1. Use whichever model the server is configured with (currently deepseek-v3.2). Model comparison is a follow-up.
- Execution testing should not assume arbitrary tool mocking exists in the live server. Most prompts are generation-first; execution is limited to an execution-friendly subset until deterministic test-only tools exist.
