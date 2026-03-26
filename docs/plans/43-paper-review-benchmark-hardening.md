# 43: Paper-Writing Workflow Benchmark Hardening

**Status:** completed
**Goal:** Make the paper-writing workflow (`examples/paper_writing.py`) honest, reproducible, and benchmarkable by fixing provider/model coupling, failure propagation, and degraded-output behavior before treating the workflow as a dependable benchmark target.

## Motivation

The paper-writing workflow is a good stress test because it exercises failure modes that make benchmark results misleading if left unpatched:

- LLM node model selection is implicit (inherited from the engine default `claude-sonnet-4-6`) rather than being explicit per-stage configuration
- the `search_web` tool function hardcodes `perplexity/sonar-pro-search` as a model string
- the engine does **not** auto-skip downstream nodes when upstream nodes fail — `_execute_ready_queue` releases dependents regardless of `FAILED` status, so downstream nodes run with missing inputs
- upstream failures can cascade into late crashes instead of structured failure artifacts

If this workflow is going to be used as a benchmark acceptance path, it needs to be truthful about what failed and produce structured degraded artifacts.

## Scope

This phase covers the execution and reliability layer around `examples/paper_writing.py`, plus the minimum documentation needed to make the benchmark path unambiguous.

In scope:

- provider/model profile decoupling (make model choices explicit and configurable)
- upstream prerequisite gating (prevent downstream nodes from running on missing upstream outputs)
- structured degraded output instead of unhandled crashes
- one reproducible smoke/acceptance path for the workflow

Out of scope:

- redesigning the paper-writing workflow itself as a new product surface
- adding a new benchmark framework outside the existing repo conventions
- broader provider architecture changes beyond what this workflow needs
- execution-mode ambiguity (server vs local) — covered by plan 42

## Sub-Plans

| # | Sub-Plan | Scope | Priority | Status |
|---|----------|-------|----------|--------|
| [43-1](43-1-provider-model-profile-decoupling.md) | Provider / Model Profile Decoupling | Make model choices in `examples/paper_writing.py` explicit and configurable via profile/config instead of implicit engine defaults | P1 | completed |
| [43-2](43-2-upstream-prerequisite-gating.md) | Upstream Prerequisite Gating | Stop downstream nodes from running when upstream nodes fail — requires engine-level or workflow-level gating since the scheduler does not auto-skip on failure | P1 | completed |
| [43-3](43-3-structured-degraded-output-contract.md) | Structured Degraded Output Contract | Emit a structured failed/incomplete artifact instead of crashing when prerequisites are absent; build on `RunResult.partial` and `DEAD_EDGE_WARNING` | P1 | completed |
| [43-4](43-4-paper-review-benchmark-smoke-fixture.md) | Paper-Writing Benchmark Smoke Fixture | Define one reproducible acceptance path that exercises the workflow end to end | P1 | completed |

## Dependencies / Sequencing

Recommended execution order:

```text
43-1 (Provider / Model Profile Decoupling)
  ↓
43-2 (Upstream Prerequisite Gating)
  ↓
43-3 (Structured Degraded Output Contract)
  ↓
43-4 (Paper Review Benchmark Smoke Fixture)
```

Rationale:

- decouple models first so the workflow can run with stable, configurable profiles
- gate prerequisites next so downstream nodes do not produce misleading partial work
- define the degraded-output contract before the smoke fixture so failures are explicit and testable
- write the acceptance fixture last, when the execution path is trustworthy enough to lock down

## Success Criteria

- provider/model choice is configurable instead of inherited implicitly from the engine default
- missing upstream inputs stop the pipeline at the right boundary
- degraded runs produce structured artifacts rather than late crashes from missing data
- the smoke fixture is reproducible from a clean checkout and a known backend environment

## Decisions

- The benchmark acceptance path should favor honest failure over fake completion.
- The workflow should be benchmarkable without requiring undocumented environment tricks.
- Server-vs-local execution-mode concerns are in scope for plan 42, not this plan.

## Notes

- This phase is intentionally narrow: it hardens one workflow because one reliable workflow is enough to prove the execution contract.
- The workflow can remain a benchmark fixture and a product demo at the same time, but only if failure behavior is structured and explicit.
- The real workflow file is `examples/paper_writing.py` (2400+ lines). There is no `workflows/` directory and no `paper_review.py` anywhere in the codebase.
- The existing workflow already uses `EngineConfig(llm_default_model=...)` with env fallback, so the "hardcoded models" concern is narrower than originally stated — the main gap is per-stage model intent, not global default.

## Completion Notes (2026-03-26)

- `examples/paper_writing.py` now exposes explicit per-stage model selection, externalized web-search model config, and artifact-level model profile recording.
- Workflow-level prerequisite guard nodes plus scheduler skip propagation now stop downstream drafting when grounded literature or evidence prerequisites fail.
- The save/package path now emits structured degraded artifacts instead of late crashes.
- The workflow now exports `build()` for `dan-run` and has a documented direct Python smoke path in the README.
