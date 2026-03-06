# 24-3: Generation Quality Suite

**Parent:** [24-reliable-generation](24-reliable-generation.md)
**Status:** completed
**Goal:** Build the verification harness that gates every generation change with golden intents, dual-path evaluation, and regression tracking.

## Motivation

Workflow generation has two paths: builder codegen (24-1) and intent compiler (24-2). Both produce `dan_graph_v1` graphs through different mechanisms. Without a shared verification harness, changes to either path can silently break known cases. The quality suite ensures that every new generation change proves it did not regress before rollout.

The harness serves three purposes: (1) **deterministic evaluation** — golden intents with expected outcomes, so we know what "pass" means; (2) **dual-path coverage** — both codegen and intent compiler are evaluated independently, with separate pass rates and failure modes; (3) **regression detection** — snapshots and baselines so we catch structural changes and new failures immediately.

This plan distinguishes **durable artifacts** from **ephemeral run outputs**. Golden fixtures, graph snapshots, and approved baselines are source-controlled. Timestamped reports, exploratory runs, and temporary debug snapshots are generated locally or in CI and must not become noisy long-lived repo state.

Acceptance: no generation change ships without the quality suite passing. The suite is the gate, not code review or manual testing.

## Current State

- **`tests/test_server/test_build_from_intent.py`** — ~15 test cases: chain (expand_pattern), rag_qa, wire-two-nodes, auto-retry, review_loop, templates (paper_writing, rag_qa, chain_3), data_ingest, data_analysis, informs_paper_writing, ApplySkill, tool port manifests. Uses mocked LLM responses.
- **`tests/test_builder/test_decompiler.py`** — Round-trip tests: build → decompile → exec → rebuild → compare. Tests chain detection, sub-graphs, gate nodes.
- **`tests/test_builder/test_integration.py`** — Editor round-trip golden test.
- **Example workflows** in `examples/`: paper_writing.py (high complexity), fan_out_fan_in.py, review_revise.py, simple_chain.py, rag_qa.py, react_agent.py.
- **Builder DSL** in `builder/` — full programmatic workflow construction.
- **`validate_graph()`** in `validation/graph.py` — 12 design-time checks.
- **`decompile()`** in `builder/decompiler.py` — Graph → Python code round-trip.
- **`tests/fixtures/`** — Markdown fixtures exist; no `golden_intents/` or `golden_graphs/` yet.

## Tasks

### 1. Golden intent curation

- [x] 1.1. Define the canonical workload families and variants:
  - 5 families: paper_writing, rag_qa, multi_step_analysis, review_loop, fan_out_fan_in
  - 3 variants per family: simple, standard, complex (15 intents minimum)
  - Document expected node types, topology features (loops, fan-out, tools), and port connections per intent
- [x] 1.2. Create JSON schema for golden intent fixtures: `intent` (natural language), `family`, `variant`, `expected_node_types`, `expected_topology` (loops, fan_out, tools), `expected_port_connections`, `edge_cases` (optional)
- [x] 1.3. Implement 15 golden intents in `tests/fixtures/golden_intents/` as JSON files (e.g. `paper_writing_simple.json`, `rag_qa_standard.json`)
- [x] 1.4. Add edge-case intents: empty_inputs, conflicting_requirements, very_large_workflow_spec (3 additional fixtures)
- [x] 1.5. Add loader: `load_golden_intents()` returns list of intent fixtures with validation

### 2. Codegen path evaluation

- [x] 2.1. Create `tests/quality_suite/codegen_runner.py`: for each golden intent, invoke codegen path with mocked LLM (pre-recorded builder code outputs)
- [x] 2.2. Implement validation pipeline: syntax check (Python parses) → build check (Graph compiles) → validation check (`validate_graph` passes) → round-trip check (decompile → rebuild matches)
- [x] 2.3. Record per-intent results: pass/fail, error_type, error_message, latency_ms, generated_code_snippet (truncated), and topology assertions (expected loops, fan-out, required tool nodes)
- [x] 2.4. Create pre-recorded mock responses for deterministic testing: one JSON file per intent with `builder_code` field, or shared mock that returns fixture-based code
- [x] 2.5. Integrate with 24-1 codegen API once available; fallback to direct `exec()` of fixture code for early development

### 3. Intent compiler path evaluation

- [x] 3.1. Create `tests/quality_suite/intent_runner.py`: for each golden intent, invoke intent extraction → coverage check → compilation (24-2 API)
- [x] 3.2. Implement validation pipeline: intent parses → coverage result (supported/unsupported) → compiled code parses → Graph compiles → `validate_graph` passes
- [x] 3.3. Record per-intent results: pass/fail, coverage_result (supported/unsupported/fallback_to_codegen), error_details, latency_ms, and topology assertions
- [x] 3.4. Separate tracking: count intents "covered by intent compiler" vs "fell back to codegen" — report both
- [x] 3.5. Integrate with 24-2 intent compiler API once available; stub with "unsupported" for intents until compiler exists

### 4. Round-trip validation framework

- [x] 4.1. Create `tests/quality_suite/graph_equivalence.py`: `GraphEquivalenceChecker` class
  - Compare two Graphs: node IDs, node types, edge connections, port names
  - Ignore: positions, timestamps, metadata (or normalize for comparison)
  - Return: `EquivalenceResult(equivalent, mismatches)`
- [x] 4.2. Implement round-trip pipeline: generated code → `build()` → Graph → `decompile()` → `exec()` → rebuild → compare to original
- [x] 4.3. Add regression detection: if a previously-passing round-trip breaks, flag and categorize (syntax, build, validation, structure_mismatch)
- [x] 4.4. Create `tests/fixtures/golden_graphs/`: store reference graph JSON snapshots for known-good workflows (from examples or manual builds)
- [x] 4.5. Add `round_trip_check(generated_code) -> Result` helper used by both codegen and intent runners

### 5. Pass-rate tracking and reporting

- [x] 5.1. Define `GenerationQualityReport` model (Pydantic or dataclass): path (codegen|intent), total_intents, passed, failed, failure_modes (dict: error_type -> count), latency_stats (p50, p95, p99)
- [x] 5.2. Define failure mode taxonomy: `syntax_error`, `build_error`, `validation_error`, `round_trip_mismatch`, `timeout`, `wrong_topology`, `coverage_unsupported` (intent path)
- [x] 5.3. Create CLI/script: `python -m tests.quality_suite` runs all golden intents through both paths, prints report to stdout
- [x] 5.4. Store the approved baseline summary in a committed fixture location; write timestamped run reports to an ephemeral local/CI artifacts directory (not git-tracked)
- [x] 5.5. Implement regression detection: compare current report to the committed baseline; flag if pass count dropped, topology expectations regress, or new failure modes appeared; exit non-zero on regression

### 6. Regression snapshot infrastructure

- [x] 6.1. Commit deterministic golden graph snapshots for approved fixtures only; write per-run generated graphs to an ephemeral artifacts directory for debugging
- [x] 6.2. On each run, compare current outputs to the committed golden graphs and baseline summary: detect new failures (regression), new passes (improvement), changed graph structure
- [x] 6.3. Make committed snapshots git-friendly: deterministic JSON (sorted keys, no timestamps in structure), so diffs are reviewable
- [x] 6.4. Add `--update-baseline` flag: when run intentionally, overwrite the committed baseline summary and golden snapshots
- [x] 6.5. Document in plan: only durable baselines are committed; timestamped reports stay ephemeral even when CI archives them

### 7. Integration with existing tests

- [x] 7.1. Extend `test_build_from_intent.py`: add parametrized tests that load golden intents and assert expected outcomes (when mocked)
- [x] 7.2. Extend `test_decompiler.py`: add round-trip tests for graphs produced by codegen (use golden_graphs fixtures)
- [x] 7.3. Add pytest marker: `@pytest.mark.quality_suite` for tests that run the full suite (slower, may use mocks or real LLM)
- [x] 7.4. Add `pytest.ini` or `pyproject.toml` config: `-m "not quality_suite"` for fast unit runs; `-m quality_suite` for CI/full validation
- [x] 7.5. Ensure quality suite can run without network/LLM: all golden intents use pre-recorded mocks by default

## Files to Touch

| File | Changes |
|------|---------|
| `tests/fixtures/golden_intents/*.json` | New: 18+ intent fixtures |
| `tests/fixtures/golden_intents/schema.json` | New: JSON schema for intent format |
| `tests/fixtures/golden_graphs/*.json` | New: reference graph snapshots |
| `tests/fixtures/generation_quality_baseline.json` | New: committed baseline summary for regression comparison |
| `tests/quality_suite/__init__.py` | New: package init |
| `tests/quality_suite/codegen_runner.py` | New: codegen path evaluation |
| `tests/quality_suite/intent_runner.py` | New: intent compiler path evaluation |
| `tests/quality_suite/graph_equivalence.py` | New: GraphEquivalenceChecker |
| `tests/quality_suite/report.py` | New: GenerationQualityReport model, failure taxonomy |
| `tests/quality_suite/__main__.py` | New: `python -m tests.quality_suite` entrypoint |
| `.artifacts/generation-quality/` | New gitignored location for timestamped reports, transient snapshots, and CI-exported run artifacts |
| `.gitignore` | Add the generation-quality artifacts directory if it does not already exist |
| `tests/test_server/test_build_from_intent.py` | Extend: parametrized golden intent tests |
| `tests/test_builder/test_decompiler.py` | Extend: round-trip tests with golden graphs |
| `pyproject.toml` or `pytest.ini` | Add: quality_suite marker, default exclude |

## Decisions

- (filled in during execution)

## Notes

- 2026-03-06 hardening pass: both runners now enforce fixture-level `expected.min_nodes` and `expected.node_types` as required gates (in addition to topology + round-trip).
- 2026-03-06 hardening pass: baseline regression comparison now also enforces fixture total-count equality (`report.total_intents` vs baseline `total`) to catch missing/extra fixture drift.
- 2026-03-06 residual patch: mismatch classification now emits explicit `error_type` values (`topology_mismatch`, `structure_mismatch`, `round_trip_mismatch`, `multiple_mismatch`) instead of a single coarse `"topology_or_roundtrip"` bucket.
