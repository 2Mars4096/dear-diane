# 33-7: Semantic Quality Gates

**Parent:** [33-generation-quality-eval](33-generation-quality-eval.md)
**Status:** completed *(all patch tasks done; keyword mapping tuned from eval results)*
**Goal:** Catch graphs that are structurally valid but semantically wrong — underspecified, missing expected patterns, or lacking required features — before they count as "pass" in the eval harness or reach the user.

## Problem

The Phase 33 pilot revealed that `validate_graph()` is a necessary but insufficient quality check:

| Prompt | Expected | Got | Validation |
|--------|----------|-----|------------|
| p02: "review loop" | 3+ nodes with gate/loop edge | 2 nodes, 1 edge | **passed** |
| p08: "equity research with 5 steps + reviewer panel" | 10-15 nodes | 1 node, 0 edges | **passed** |

Both graphs are structurally valid (no orphan nodes, ports match, edges connect). But they're semantically useless — the LLM produced a placeholder instead of a real workflow.

Current validation checks: unreachable nodes, missing edges, port type mismatches, entry/exit node presence. It does **not** check: whether the graph matches the user's intent, whether expected patterns are present, whether the complexity is proportional to the request.

## Approach

Add a **semantic quality scoring layer** that runs after structural validation but before the graph is accepted. This is NOT a binary pass/fail — it produces a quality score (0-100) and a list of concerns. The eval harness uses it for measurement; the build pipeline can optionally reject graphs below a threshold.

### Quality Dimensions

1. **Node count adequacy** — Is the graph roughly the right size for the prompt?
2. **Pattern presence** — Does the graph contain the patterns the prompt requested?
3. **Tool coverage** — Do tool-heavy prompts produce tool nodes?
4. **Topology features** — Does the graph have the structural features it should (loops, fan-out, gates)?
5. **Port utilization** — Are nodes wired together or floating islands?

## Tasks

- [x] 1. **Define quality check primitives** (`src/dan/meta/graph_quality.py`)
  - [x] 1-1. `check_node_count(graph_dict: dict, prompt_text: str, tier: str | None = None) -> QualityCheck` — heuristic based on prompt complexity. When `tier` is provided (eval harness): T1 ≥2, T2 ≥3, T3 ≥4, T4 ≥6. When `tier` is None (build pipeline): infer from prompt word count and structure (e.g., prompts mentioning N steps should have ≥N nodes; prompts with "simple" or "quick" tolerate fewer). Returns score 0-100 with explanation.
  - [x] 1-2. `check_pattern_presence(graph_dict: dict, prompt_text: str) -> QualityCheck` — scan prompt for pattern keywords ("review loop" → expect gate/loop edges, "in parallel" → expect ForEach or fan-out, "RAG" → expect RAG node, "code" → expect Code node). Return which expected patterns are present/missing. Reuse keyword mapping from `_STAGE_TYPE_DESCRIPTIONS` in `intent_extraction.py` where possible.
  - [x] 1-3. `check_tool_coverage(graph_dict: dict, prompt_text: str) -> QualityCheck` — if prompt mentions "web search", "PDF", "file", "code execution", check that corresponding tool/code nodes exist
  - [x] 1-4. `check_topology(graph_dict: dict) -> QualityCheck` — connectivity score. Penalize: isolated nodes, single-node graphs for multi-step prompts, missing entry/terminal nodes
  - [x] 1-5. `GraphQualityReport` — aggregate Pydantic model with overall score, per-check scores, and human-readable concerns list
  - [x] 1-6. **Input format:** All functions take `graph_dict: dict` (the raw graph dictionary returned by codegen/sandbox), NOT a `Graph` model. Parse internally via `Graph.model_validate(graph_dict)` only if needed — this keeps the API consistent with `validate_codegen_output()` which also takes raw dicts.

- [x] 2. **Integrate into build pipeline (companion step after validation)**
  - [x] 2-1. Call quality checks after `validate_codegen_output()` succeeds in `_generate_workflow_from_intent()` — both on the codegen path and the intent compiler path (same call site pattern for both)
  - [x] 2-2. Add `ChatGraphQualityEvent` stream event with score and concerns (so the eval harness can capture it)
  - [x] 2-3. **Do NOT reject graphs below threshold by default** — log a warning and emit the event. Rejection is opt-in via `DAN_GRAPH_QUALITY_THRESHOLD` env var (default: 0, meaning accept all valid graphs). This avoids breaking existing workflows while enabling measurement.
  - [x] 2-4. When rejection is enabled and score < threshold, fall back to codegen retry or diagnosis loop (same as validation failure path)
  - [x] 2-5. **Post-mutation quality check** — run quality checks after structural mutation succeeds and validation passes (from 33-8 task 12). Mutations can reduce graph quality (e.g., removing too many nodes). Emit `ChatGraphQualityEvent` for mutations too.
  - [x] 2-6. **Post-diagnosis quality check** — when the diagnosis loop repairs code and produces a graph, run quality checks on the repaired graph (from 33-8 task 11). The repair may produce a structurally valid but semantically degraded graph.

- [x] 3. **Integrate into eval harness**
  - [x] 3-1. Add `quality_score` and `quality_concerns` fields to `EvalRecord` in `tests/eval/__init__.py`
  - [x] 3-2. In `runner.py`, after graph validation passes, run quality checks and record the score
  - [x] 3-3. In `report.py`, add quality score distribution per tier (min/avg/max) and top concerns
  - [x] 3-4. Distinguish "structural pass" from "semantic pass" in pass rate reporting: a prompt with score < 30 counts as "valid but underspecified" rather than "passed"

- [x] 4. **Calibrate thresholds**
  - [x] 4-1. Run quality checks against all existing `graphs/*.json` files (the 22+ existing workflows) to establish an initial baseline *(historical baseline only; follow-up tuning should use a curated golden corpus rather than the live graph store state)*
  - [x] 4-2. Run quality checks against the pilot results (p01, p02, p06, p08) to verify expected scoring: p01 should score high (~80+), p02 should score low (~30-40), p08 should score very low (~10-20)
  - [ ] 4-3. Tune keyword→pattern mapping based on false positives/negatives from calibration
  - [ ] 4-4. Document recommended threshold ranges per tier in the plan

- [x] 5. **Tests**
  - [x] 5-1. Unit tests for each quality check primitive (high-quality graph, underspecified graph, single-node graph, multi-department graph)
  - [x] 5-2. Integration test: quality checks run correctly after validation in the build pipeline
  - [x] 5-3. Eval harness test: quality scores appear in JSONL output and report

## Key Files

| File | Action |
|------|--------|
| `src/dan/meta/graph_quality.py` | **Create** — quality check primitives and `GraphQualityReport` |
| `src/dan/server/chat_manager.py` | **Modify** — call quality checks after validation, emit event |
| `tests/eval/__init__.py` | **Modify** — add quality fields to `EvalRecord` |
| `tests/eval/runner.py` | **Modify** — run quality checks after validation |
| `tests/eval/report.py` | **Modify** — add quality score to report |

## Success Criteria

- [ ] p02 (review loop → 2 nodes) scores ≤40 (currently counts as "passed")
- [ ] p08 (equity research → 1 node) scores ≤20 (currently counts as "passed")
- [ ] Well-formed graphs from an approved calibration corpus score ≥70
- [ ] Quality scores appear in eval harness reports with per-tier distribution
- [ ] No existing workflow builds are rejected (threshold defaults to 0)

## Patch Tasks (post code-review 2026-03-12)

- [x] P1. **Tier-adaptive quality thresholds**
  - [x] P1-1. `tier_quality_threshold(tier, prompt_text)` returns per-tier thresholds: T1→30, T2→40, T3→50, T4→60. Infers tier from prompt when `tier=None`.
  - [x] P1-2. `is_acceptable_simple_graph(graph_dict, prompt_text)` — single-pattern match (chain, fan_out, review_loop, conditional) + correct topology + proportional node count (2-6) → quality gate skipped.
  - [x] P1-3. Extracted `estimate_prompt_complexity()` and `expected_node_range()` as shared primitives. `GraphQualityReport` now includes `complexity_tier`, `expected_node_range_min`, `expected_node_range_max`. `check_node_count()` refactored to use `expected_node_range()` internally.

- [x] P2. **Tune keyword→pattern mapping from real results**
  - [x] P2-1. Ran 19 T1/T2 prompts through eval with quality scoring. Stored graphs in `tests/eval/results/2026-03-12_130201_run_graphs/`. Identified 1 false positive: t2-03 scored 63 because bare `"file"` keyword in `_TOOL_KEYWORDS` matched "code file" (code review prompt, not file I/O). No false negatives found.
  - [x] P2-2. Updated `_TOOL_KEYWORDS`: replaced bare `"file"` with specific phrases (`"read a file"`, `"write a file"`, `"from a folder"`, `"ingest"`). Added `"search the web"` for web search. Result: t2-03 63→100. Average quality 89.3→93.9 across 14 graphs.

- [x] P3. **Document recommended threshold ranges**
  - [x] P3-1. Based on calibration (task 4 results) and the tier-adaptive work above, document in architecture.md: recommended `DAN_GRAPH_QUALITY_THRESHOLD` value and per-tier ranges

## Decisions

- Quality gates are advisory in the build pipeline (threshold defaults to 0) and primary in the eval harness. Shipped and tested.
- Post-mutation and post-diagnosis quality checks implemented (tasks 2-5, 2-6).
- P1: Tier-adaptive thresholds (T1→30, T2→40, T3→50, T4→60) and `is_acceptable_simple_graph()` exemption now wired into `_quality_error_for_graph()`. `estimate_prompt_complexity()` and `expected_node_range()` are the shared primitives for 33-9.
- P3: `DAN_MAX_GENERATION_SECONDS` and `DAN_GRAPH_QUALITY_THRESHOLD` with tier-adaptive behavior documented in architecture.md.
- P2 (2026-03-12): `_TOOL_KEYWORDS` bare `"file"` was a false-positive source (matched "code file" in code-review prompts). Replaced with specific file-operation phrases. `_PATTERN_KEYWORDS` bare `"code"` was already fixed in prior session. Post-tuning quality: avg=93.9, min=66, max=100 across 14 graphs.

## Notes

- The quality checks are heuristic, not precise. "review loop" → "needs gate edge" is a reasonable proxy but won't catch every case. The goal is to catch obvious failures (1-node equity research), not achieve perfect semantic validation.
- Keyword→pattern mapping should reuse `_STAGE_TYPE_DESCRIPTIONS` from `intent_extraction.py` and `COVERAGE_CATALOG` pattern names from the intent compiler where possible. Avoids maintaining two parallel keyword vocabularies.
- Quality scoring is advisory in the build pipeline but primary in the eval harness. The eval harness is the main consumer.
- Future calibration should use a curated golden corpus, not the live `graphs/` directory, because the graph store contains experimental and auto-generated workflows rather than stable ground truth.
- This is independent of 33-6 (intent compiler) and 33-8 (codegen resilience). All three can proceed in parallel, but all three modify `_generate_workflow_from_intent()` — coordinate merges. Tasks 2-5 and 2-6 depend on 33-8 tasks 12 and 11 being done first (post-mutation and post-diagnosis validation must exist before quality scoring can chain after them). If 33-7 ships before 33-8, these two tasks are deferred.
- Future extension: use the quality score as a signal for codegen retry — if the first attempt scores below threshold, retry with a more explicit prompt. This bridges into 33-8 territory.
- 33-9 should reuse the same prompt-complexity and expected-node-range signal exposed here rather than creating a second heuristic path for prompt-to-graph fit.
- **Companion step chain:** Quality scoring slots into the deterministic chain as: ... → structural validation → semantic quality check → outcome recording → save/return. Every path that produces a graph (codegen, intent compiler, diagnosis repair, mutation) should pass through this gate. See 33-8 notes on the full companion chain.
