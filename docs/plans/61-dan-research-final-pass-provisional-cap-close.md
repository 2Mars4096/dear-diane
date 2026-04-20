# 61: DAN Research Final-Pass Provisional Cap Close

**Status:** completed
**Goal:** Let DAN Research close as a provisional report when an explicitly final/no-more-pass objective reaches the supervision cap with a caveated, materially grounded report, instead of always forcing an `incomplete` blocker artifact.

## Tasks
- [x] 1. Inspect the recurring continuation loop seam in the DAN Research review/controller path.
  - [x] 1-1. Confirm where `done` was still normalized back to `continue`.
  - [x] 1-2. Confirm where capped `continue` decisions always became `incomplete`.
- [x] 2. Patch the review-layer normalization for explicit final-pass objectives.
  - [x] 2-1. Add a broader best-effort final-closure detector above the older explicit-unverifiable-only path.
  - [x] 2-2. Allow a narrow set of caveated non-pass gates (`scope_boundary` in addition to the earlier provisional set) for that explicit final-pass path.
- [x] 3. Add a CLI safety net at the supervision cap.
  - [x] 3-1. Convert capped `continue` into a provisional close when the objective/report pair explicitly authorizes that outcome.
  - [x] 3-2. Downgrade `final_status` to `warn` and emit a `provisional_report` instead of forcing `blocker_report`.
- [x] 4. Lock the behavior with focused tests.
  - [x] 4-1. Add a worker-level review-normalization regression for the explicit final-pass scenario.
  - [x] 4-2. Add a CLI regression proving the cap path now closes provisionally instead of returning `incomplete`.

## Decisions
- The broader close path stays objective-driven. It only activates when the objective explicitly says this is the final/no-more-pass attempt and also asks for a provisional/caveated synthesis.
- The CLI still keeps the old honest `incomplete` path as the default cap behavior. The new override is intentionally narrow and acts as a safety net on top of the review-layer change.
- `scope_boundary` is now allowed to remain non-pass for this explicit final-pass close path because the evidence-integrity layer can surface a real scope caveat even when the report is still good enough for an honest provisional memo.

## Notes
- Validation: `python -m py_compile src/dan/worker/organisms/research_conversation.py src/dan/cli/research.py tests/test_worker/test_research_conversation.py tests/test_cli/test_research.py`
- Validation: `PYTHONPATH=src pytest -q tests/test_worker/test_research_conversation.py tests/test_cli/test_research.py`
