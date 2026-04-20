# 55: DAN Research Evidence Integrity Pass

**Status:** completed
**Goal:** Add one generic pre-synthesis evidence-integrity layer for deep research and align grounded web fetch concurrency across research and chat/capability surfaces.

## Tasks
- [x] 1. Define one generic evidence-integrity artifact above verification and audit appendices
  - [x] 1-1. Normalize claim-level status (`accepted`, `accepted_with_proxy`, `conflicted`, `unverifiable`, `pending`)
  - [x] 1-2. Canonicalize source identity, metric identity, unit/scale, time alignment, scope alignment, and same-source consistency
- [x] 2. Thread the integrity layer through DAN Research product/report rendering
  - [x] 2-1. Derive integrity rows from verification facts plus audit issues
  - [x] 2-2. Use integrity state to downgrade readiness/gates before memo synthesis
  - [x] 2-3. Surface blocker/provisional caveats from integrity rows in the console renderer
- [x] 3. Align grounded web fetch behavior across surfaces
  - [x] 3-1. Keep `fetch_content=true` grounding batched in parallel at the capability layer instead of serial fetches
- [x] 4. Lock the behavior with focused tests
  - [x] 4-1. Research report derivation + downgrade coverage
  - [x] 4-2. Capability-level parallel grounding coverage
- [x] 5. Update docs/tracking

## Decisions
- The next generic DAN Research quality step is a canonicalization/reconciliation seam, not another domain-specific verifier.
- `verification_facts` and `audit_issues` remain useful operator-facing appendices, but they are not sufficient on their own to prevent candidate facts from leaking into final memo prose.
- Capability-surface grounded fetches should match the same parallelism expectations already used by the lower-level search path.

## Notes
- This stays at the DAN Research product/report seam and the shared web capability seam; it does not add more reader agents or change the bounded deep-research organ topology.
- Focused validation after implementation:
  - `python -m py_compile src/dan/cli/research_product.py src/dan/cli/research.py src/dan/worker/organisms/research_conversation.py src/dan/server/capabilities/web.py tests/test_cli/test_research.py tests/test_concierge/test_live_data.py`
  - `PYTHONPATH=src pytest -q tests/test_cli/test_research.py tests/test_concierge/test_live_data.py tests/test_worker/test_research_conversation.py` (`104 passed, 1 skipped`)
