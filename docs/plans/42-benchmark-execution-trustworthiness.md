# 42: Benchmark Execution Trustworthiness

**Status:** completed
**Goal:** Make DAN's benchmark and demo runs trustworthy enough that public benchmark scores, workflow smoke tests, and product runbooks all reflect the same execution reality instead of silently diverging across local, server, and wrapper-based entrypoints.

## Motivation

The benchmark program only helps if the run path is honest and repeatable. Recent test feedback exposed several trust gaps:

- `--local` is documented too broadly even though local workflow execution may not register the built-in tools required by real workflows.
- `--server` is not strict enough and can silently fall back to local execution.
- Thin workflow wrapper entrypoints are fragile when loaded through `dan-run`.
- Provider setup is split across different env-var conventions and can degrade into confusing fallback behavior.
- Runbooks still suggest modes that are fine for development but misleading for benchmark-grade execution.

This phase closes those gaps so benchmark claims and workflow demos are based on the same operational contract.

## Scope

This phase covers the execution path, not benchmark content itself.

Included:

- strict server execution semantics
- local tool-parity policy or explicit de-scope
- workflow loader import-path stability
- provider bootstrap and resolution hardening
- benchmark docs and runbook cleanup

Excluded:

- benchmark scoring logic
- workflow-specific semantic repair
- new benchmark datasets or adapters

## Sub-Plans

| # | Sub-Plan | Scope | Priority | Status |
|---|----------|-------|----------|--------|
| [42-1](42-1-explicit-server-execution-contract.md) | Explicit Server Execution Contract | Make `--server` fail hard when the server is unavailable and stop treating fallback as acceptable for benchmark-grade runs | P0 | completed |
| [42-2](42-2-local-runtime-tool-parity.md) | Local Runtime Tool Parity | Either register built-in tools in local execution or explicitly de-scope local mode for tool-heavy workflows | P0 | completed |
| [42-3](42-3-workflow-loader-import-path-stability.md) | Workflow Loader Import-Path Stability | Make `dan-run` robust for thin wrapper workflows without requiring ad hoc `PYTHONPATH` setup | P0 | completed |
| [42-4](42-4-provider-bootstrap-and-resolution-hardening.md) | Provider Bootstrap & Resolution Hardening | Normalize backend provider env vars, resolution rules, and health diagnostics | P0 | completed |
| [42-5](42-5-benchmark-docs-and-runbook-cleanup.md) | Benchmark Docs & Runbook Cleanup | Update README and benchmark runbooks so they describe the real execution contract | P1 | completed |

## Sequencing

Recommended order:

```text
42-1 (strict server contract)
  → 42-4 (provider hardening)
  → 42-2 (local tool parity or de-scope)
  → 42-3 (loader stability)
  → 42-5 (docs cleanup)
```

Why this order:

1. `42-1` removes the worst source of false confidence.
2. `42-4` makes the backend environment explicit and diagnosable.
3. `42-2` decides whether local mode is actually a supported benchmark path.
4. `42-3` eliminates wrapper fragility that otherwise pollutes `dan-run`.
5. `42-5` updates the user-facing story only after the behavior is real.

## Success Criteria

- Explicit `--server` requests do not silently fall back to local mode.
- Local workflow execution is either tool-parity complete or clearly documented as not benchmark-grade.
- `dan-run` workflows can be loaded without fragile manual `PYTHONPATH` fixes.
- Backend provider readiness is visible and consistent across runtime entrypoints.
- README and runbooks tell users the correct default path for demo and benchmark execution.

## Decisions

- Benchmark-grade execution must be strict even if it is less convenient.
- Local execution is acceptable only if it behaves like the server path for the tools the workflow needs.
- Documentation should follow behavior, not paper over it.

## Notes

- This phase is intentionally boring. The point is to remove ambiguity, not add features.
- If a behavior cannot be made strict in time, the docs must de-scope it instead of implying it works.
- The benchmark plan depends on this phase being trustworthy before public scores are treated as meaningful.

## Audit Notes (2026-03-25)

All five sub-plans verified against the codebase. Each is directionally correct but contains factual inaccuracies in file references, flag semantics, or subsystem assumptions. Per-plan patches applied inline — see individual sub-plan audit sections for details.

## Completion Notes (2026-03-26)

- `dan-run` now fails closed on explicit `--server`, reports execution mode in structured output, and fetches final server-run snapshots for machine-readable results.
- Local workflow execution now gets built-in tool parity by default.
- CLI workflow loading now applies the same workspace path validation contract as the gateway and supports wrapper imports without ad hoc repo-root `PYTHONPATH` hacks.
- Provider readiness and strict prefix resolution are exposed through `/health`.
- README and eval runbooks now document the strict benchmark path and call out `PYTHONPATH=src` as a repo-local bootstrap bridge rather than part of the workflow contract.
