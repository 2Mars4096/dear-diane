# 60: DAN Research Human-Readable Markdown Report

**Status:** completed
**Goal:** Make the default `report.md` artifact read like a human research memo instead of mirroring DAN Research's internal verification/audit envelope.

## Tasks
- [x] 1. Replace the default Markdown artifact shape with the same memo-style structure used by the normal console path
  - [x] 1-1. Keep recommendation / findings / sources / caveats / next steps
  - [x] 1-2. Remove internal appendices and gate dumps from the default Markdown artifact
- [x] 2. Preserve rich model-authored findings blocks in Markdown instead of wrapping headings in bullets
- [x] 3. Revalidate the research CLI markdown tests
- [x] 4. Update tracking docs

## Decisions
- `report.md` should be the human-facing memo artifact by default; machine diagnostics belong in `--json`, `events.jsonl`, and session/control-plane state.
- A blocked run should still say it is incomplete, but the artifact should remain readable and should surface the current best synthesis before the follow-up actions.
- Markdown findings need light formatting preservation because DAN Research often returns already-structured memo blocks (`##`, `###`, embedded bullets).

## Notes
- Focused validation after the patch:
  - `python -m py_compile src/dan/cli/research.py tests/test_cli/test_research.py`
  - `PYTHONPATH=src pytest -q tests/test_cli/test_research.py`
