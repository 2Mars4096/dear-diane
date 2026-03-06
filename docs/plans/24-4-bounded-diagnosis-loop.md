# 24-4: Bounded Diagnosis Loop

**Parent:** [24-reliable-generation](24-reliable-generation.md)
**Status:** completed
**Goal:** Add a small, explicit repair loop for failed generations that extracts errors, maps them to responsible artifacts, and attempts targeted corrections within bounded attempts.

## Motivation

When builder codegen or intent compilation fails, the system currently surfaces raw error messages (sandbox stderr, `BuildError` text, validation strings) to the user or to a generic retry loop. This is suboptimal: errors are not attributed to specific artifacts (code lines, nodes, edges, ports), and retries often re-prompt with the full context, wasting tokens and latency. The existing `meta/repair.py` infrastructure targets *runtime* failures (after a workflow runs and fails); generation-time failures need a separate, lighter-weight diagnosis path.

A bounded diagnosis loop addresses this by: (1) extracting structured errors from all generation stages (sandbox, build, validation), (2) mapping each error to the smallest responsible artifact, and (3) applying targeted corrections—either a short allowlist of deterministic auto-fixes or a single focused re-prompt with minimal context. The loop is explicitly bounded (default 2 attempts, matching `DAN_MUTATION_AUTO_RETRY_MAX`) and runs only when generation fails—it does not affect the steady-state success path. Acceptance: diagnosis improves recovery on known failures without making normal generation slower or harder to reason about.

## Current State

| Component | Location | Notes |
|----------|----------|-------|
| `RepairClassifier`, `RepairEscalator`, `StructuralRepairPlanner`, etc. | `meta/repair.py` | RUNTIME failures only (after workflow runs). Levels 0–4: retry, prompt fix, parameter, structural, redesign. This plan must not expand into that runtime scope. |
| `DAN_MUTATION_AUTO_RETRY_MAX` | `ChatManager` | Default 2; used for mutation-path retries, not codegen. |
| `validate_graph()` | `validation/graph.py` | 12 design-time checks; returns `list[str]` errors. Categories: entry/exit, reachability, required ports, sub_graph refs, edge endpoints, schema, context, cycles, hyperedges. |
| `BuildError` | `builder/compiler.py` | Raised with `list[str]` on compilation/validation failure. |
| `SandboxResult` | `sandbox/runner.py` | `exit_code`, `stdout`, `stderr`; no structured error model. |
| `DEFAULT_OUTPUT_PORTS` | `builder/compiler.py` | Node-type → default output port map for port-mismatch suggestions. |

## Tasks

### 1. Error extraction and classification

- [x] 1-1. Define `GenerationError` model: `stage` (sandbox | build | validation), `error_type` enum (syntax_error, import_error, runtime_error, port_conflict, missing_edge_target, duplicate_node, schema_mismatch, reachability, port, edge, cycle, hyperedge, etc.), `message`, `source_line` (optional), `artifact_id` (optional), `recoverable` (bool).
- [x] 1-2. Parse `SandboxResult`: syntax error (line number from traceback), import error (missing module from `ModuleNotFoundError`), runtime error (exception type + traceback snippet). Map to `GenerationError` with `stage=sandbox`.
- [x] 1-3. Parse `BuildError`: extract error strings; classify by keyword (port conflict, missing edge target, duplicate node, schema mismatch, unknown node_type, context edge missing fields). Map to `GenerationError` with `stage=build`.
- [x] 1-4. Parse `validate_graph()` errors: categorize by prefix/pattern (reachability, port, edge endpoint, cycle, schema, hyperedge). Map to `GenerationError` with `stage=validation`.
- [x] 1-5. Implement `ErrorClassifier`: `classify(raw_error: str | BuildError | SandboxResult) -> list[GenerationError]`. Single entry point for all error sources.

### 2. Artifact mapping

- [x] 2-1. Define `ErrorArtifact` model: `artifact_type` (code_line | node | edge | port | config), `artifact_id`, `context` (surrounding code/config snippet).
- [x] 2-2. Sandbox syntax error → `ErrorArtifact(artifact_type=code_line, artifact_id=line_number, context=surrounding_lines)`.
- [x] 2-3. Build error (port conflict) → `ErrorArtifact(artifact_type=port, artifact_id=node_id:port_name, context=node_type)`.
- [x] 2-4. Build/validation error (missing edge target, edge endpoint) → `ErrorArtifact(artifact_type=edge, artifact_id=source_id:target_id:port, context=...)`.
- [x] 2-5. Validation error (reachability) → `ErrorArtifact(artifact_type=node, artifact_id=orphan_node_id)`.
- [x] 2-6. Validation error (cycle) → `ErrorArtifact(artifact_type=node, artifact_id=cycle_node_ids)` (list of IDs in cycle).
- [x] 2-7. Implement `ArtifactMapper`: `map_to_artifact(error: GenerationError, code: str | None, graph: Graph | None) -> ErrorArtifact | None`.
- [x] 2-8. Prioritize artifact attribution order: builder source first, compiled graph second. Only map to graph-level artifacts when the source location cannot explain the failure well enough.

### 3. Targeted correction strategies

- [x] 3-1. Define `CorrectionStrategy` enum: `re_prompt`, `auto_fix`, `suggest_to_user`.
- [x] 3-2. Syntax error → `re_prompt`: compose focused error message (error line + 3 lines before/after), call LLM once for fix.
- [x] 3-3. Missing import → `auto_fix`: add `import X` to code preamble only when `X` is on an explicit allowlist of DAN builder/runtime imports.
- [x] 3-4. Port mismatch → `auto_fix`: correct only obvious default-port mistakes using `DEFAULT_OUTPUT_PORTS` / `default_output_port()` in `compiler.py`; do not invent new topology.
- [x] 3-5. Missing edge target → `suggest_to_user` or `re_prompt`: if node exists with a near-exact typo, auto-fix; otherwise re-prompt with "add missing node or correct target ID".
- [x] 3-6. Unreachable node → `re_prompt`: "wire orphan node X to an upstream node".
- [x] 3-7. Schema mismatch → `suggest_to_user` or `re_prompt`: suggest `output_schema` adjustment.
- [x] 3-8. Implement `CorrectionStrategySelector`: `select(error: GenerationError, artifact: ErrorArtifact) -> CorrectionStrategy`.
- [x] 3-9. Implement `AutoFixApplier`: apply auto-fixes to code string or graph without LLM, but limit Phase 14 auto-fixes to the documented allowlist. Broad graph surgery is out of scope.
- [x] 3-10. Implement `RePromptComposer`: compose focused error context for LLM (error + relevant snippet, not full graph).

### 4. Diagnosis loop controller

- [x] 4-1. Define `DiagnosisResult`: `success: bool`, `final_graph: dict | None`, `attempts: list[DiagnosisAttempt]`, `final_errors: list[GenerationError] | None`.
- [x] 4-2. Define `DiagnosisAttempt`: `attempt_number`, `errors_found`, `corrections_applied`, `strategy_used`, `result` (fixed | failed | partial).
- [x] 4-3. Implement `DiagnosisLoop` class with `diagnose_and_repair(goal, generated_code, errors, sandbox_runner, llm_complete) -> DiagnosisResult`.
- [x] 4-4. Max attempts: configurable via constructor (default 2). Exceeding returns `DiagnosisResult(success=False, ...)`.
- [x] 4-5. Per attempt: map errors to artifacts → select strategy → apply (auto-fix or re-prompt) → re-validate.
- [x] 4-6. If auto-fix applicable: apply without LLM; re-validate; if success, return immediately.
- [x] 4-7. If re-prompt needed: compose focused context, call LLM once, apply returned code, re-validate.

### 5. Integration with codegen pipeline

- [x] 5-1. In 24-1 codegen path: after initial codegen fails validation (or sandbox/build error), invoke `DiagnosisLoop.diagnose_and_repair()`.
- [x] 5-2. Pass: generated code, errors (from `ErrorClassifier`), original goal.
- [x] 5-3. If diagnosis succeeds: use `DiagnosisResult.final_graph` as the generated workflow.
- [x] 5-4. If diagnosis fails: return structured error report to user (last generated code + `final_errors` with artifact attribution).
- [x] 5-5. Latency budget: diagnosis loop completes within 10s (auto-fixes <100ms; re-prompts add one LLM call).

### 6. Integration with intent compiler

- [x] 6-1. In 24-2 intent path: after intent compilation fails (build/validation error on compiled builder code), invoke `DiagnosisLoop` on the compiled code.
- [x] 6-2. Most intent compiler errors expected to be auto-fixable (deterministic compilation bugs).
- [x] 6-3. If intent is fundamentally unsupported: skip diagnosis, fall back to codegen directly (per 24-2 design). Diagnosis only runs after an actual generation attempt, never as a substitute for coverage checking.

### 7. Metrics and observability

- [x] 7-1. Define `DiagnosisMetrics`: `total_invocations`, `successes`, `failures`, `total_attempts`, `error_type_counts`, `strategy_counts`, `success_rate` property.
- [x] 7-2. Track: diagnosis invocations, success rate, total attempts, error type distribution, auto-fix vs re-prompt ratio via `record()`.
- [x] 7-3. Log each diagnosis attempt with structured metadata (attempt number, errors, strategy, outcome) via `logger.debug`.
- [x] 7-4. Quality suite (24-3): add diagnosis success rate as a tracked metric.

### 8. Tests

- [x] 8-1. Error extraction: parse real sandbox/build/validation error messages into `GenerationError` instances. (58 tests in `test_diagnosis.py`)
- [x] 8-2. Artifact mapping: each error type maps to correct `ErrorArtifact`. (in `test_diagnosis.py`)
- [x] 8-3. Auto-fix: missing import → code fixed; port mismatch → corrected. (in `test_diagnosis.py`)
- [x] 8-4. Re-prompt: syntax error → LLM called with focused context → fixed code returned. (in `test_diagnosis_loop.py`)
- [x] 8-5. Loop bounded: exceeding max attempts returns failure result. (in `test_diagnosis_loop.py`)
- [x] 8-6. Integration: codegen fails → diagnosis invoked → repaired graph returned (mock or real LLM). (9 tests in `test_planner_codegen_integration.py`)
- [x] 8-7. Metrics: counters increment correctly on success/failure. (4 tests in `test_diagnosis_loop.py`)

## Files to Touch

| File | Changes |
|------|---------|
| `src/dan/meta/diagnosis.py` (new) | `GenerationError`, `ErrorArtifact`, `ErrorClassifier`, `ArtifactMapper`, `CorrectionStrategy`, `CorrectionStrategySelector`, `AutoFixApplier`, `RePromptComposer`, `DiagnosisLoop`, `DiagnosisResult`, `DiagnosisAttempt`, `DiagnosisMetrics` |
| `src/dan/meta/__init__.py` | Export `DiagnosisLoop`, `DiagnosisResult` |
| `src/dan/meta/planner.py` (or codegen entry point from 24-1) | Invoke `DiagnosisLoop` after codegen validation failure |
| Intent compiler entry (24-2) | Invoke `DiagnosisLoop` after intent compilation failure |
| `src/dan/server/chat_manager.py` | Add `DAN_DIAGNOSIS_MAX_ATTEMPTS` env var (optional, or in diagnosis module) |
| `tests/test_meta/test_diagnosis.py` (new) | Unit + integration tests for error extraction, artifact mapping, auto-fix, re-prompt, loop bounded, metrics |
| Quality suite (24-3) | Add diagnosis success rate metric |

## Decisions

- (filled in during execution)

## Notes

- 2026-03-06 hardening pass: port auto-fix now handles real `edge_endpoint` validation errors (`has no output/input port ... (available: [...])`) instead of only synthetic `missing_port` cases.
- 2026-03-06 hardening pass: when auto-fix is selected but no deterministic patch can be inferred, diagnosis degrades to one focused re-prompt in the same attempt (when LLM completion is available), preventing repeated no-op auto-fix attempts.
- 2026-03-06 residual patch: port-name rewrite is now context-scoped (port wiring expressions only) to avoid accidental replacement inside unrelated quoted literals.
- 2026-03-06 residual patch: quote-style handling in diagnosis parsing now accepts both single-quoted and double-quoted IDs/ports for artifact extraction and available-port inference.
