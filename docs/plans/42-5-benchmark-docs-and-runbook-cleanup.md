# 42-5: Benchmark Docs & Runbook Cleanup

**Parent:** [42-benchmark-execution-trustworthiness](42-benchmark-execution-trustworthiness.md)
**Status:** completed
**Goal:** Update benchmark-facing docs so they describe the actual execution contract, the strict server path, and the correct debugging workflow instead of the convenience path.

## Problem

The docs currently encourage patterns that are fine for quick local development but misleading for benchmark-grade runs:

- `--local` is documented too casually
- fallback behavior is not obvious
- debug commands do not emphasize health checks or environment parity
- benchmark runs are not presented with enough execution-mode discipline
- the eval-run-guide (`docs/eval-run-guide.md`) uses `PYTHONPATH=src` in every command — exactly the fragile pattern that 42-3 aims to fix

If the docs are wrong, users will reproduce the wrong path and benchmark claims become hard to trust.

## Tasks

- [ ] 1. Update benchmark runbooks
  - [ ] 1-1. Prefer server-based commands for benchmark and demo runs.
  - [ ] 1-2. Make strict server mode the documented default for trustworthy runs.
  - [ ] 1-3. Include the health-check and environment-check steps in the recommended debug flow.

- [ ] 2. Clean up README guidance
  - [ ] 2-1. Stop recommending `--local` as the normal path for workflow runs if it remains tool-incomplete.
  - [ ] 2-2. Make fallback behavior explicit where it still exists.
  - [ ] 2-3. Point users at the supported server path for benchmark-grade execution.

- [ ] 3. Publish a benchmark-grade runbook
  - [ ] 3-1. Document the environment variables the backend expects (align with 42-4's normalization outcome).
  - [ ] 3-2. Document how to verify `/health` from the same shell namespace used for runs (depends on 42-4 making readiness visible).
  - [ ] 3-3. Document the `PYTHONPATH` workaround only as a temporary bridge, not the desired state (depends on 42-3's resolution).
  - [ ] 3-4. Update `docs/eval-run-guide.md` — currently every command uses `PYTHONPATH=src` and assumes a running server; update to reflect 42-1 strict mode and 42-3 loader fix.

## Likely Files

| File | Why |
|------|-----|
| `README.md` | User-facing run guidance — check for `--local` recommendations |
| `docs/eval-run-guide.md` | Primary benchmark runbook — uses `PYTHONPATH=src` everywhere, assumes server mode |
| `docs/llm-api-guide.md` | LLM-facing API reference — may reference execution modes |
| `docs/todo.md` | Cross-reference cleanup after 42-1 through 42-4 are resolved |

## Notes

- The docs should reflect the strictest supported execution path, not the most forgiving one.
- If a path is explicitly not benchmark-grade, say so directly.
- This is the last step in the trustworthiness chain, not the first.

## Audit Notes (2026-03-25)

- **`docs/eval-run-guide.md` is the primary target** — it uses `PYTHONPATH=src` in 12+ commands and always assumes a running server (`dan-up` or `dan-serve`). This aligns with 42-1 (strict server) but the `PYTHONPATH=src` prefix is exactly the fragility 42-3 addresses.
- **Cross-dependencies:** This plan should be sequenced last (as the parent plan already recommends) because its content depends on the outcomes of 42-1 (strict flag semantics), 42-3 (loader fix), and 42-4 (env var normalization).
- **`docs/benchmark-plans/*` does not exist** as a directory — removed from likely files. Benchmark docs are consolidated in `docs/eval-run-guide.md`.

## Completion Notes (2026-03-26)

- `[README.md](/Volumes/data/Dropbox/Projects/deep-agent-network/README.md)` now prefers strict server execution for benchmark-grade runs and includes a canonical paper-writing smoke path.
- `[docs/eval-run-guide.md](/Volumes/data/Dropbox/Projects/deep-agent-network/docs/eval-run-guide.md)` now includes backend health/provider checks and explicitly labels `PYTHONPATH=src` as a repo-local bootstrap bridge, not part of the workflow contract.
