# 57: DAN Research Markdown Report Export

**Status:** completed
**Goal:** Make `dan research` leave behind a human-readable `report.md` for each bounded run and support explicit Markdown export through `--output`.

## Tasks
- [x] 1. Add a Markdown renderer for research reports
  - [x] 1-1. Render the same user-facing memo structure into Markdown
  - [x] 1-2. Include verification, integrity, audit, and quality-gate appendices for human review
- [x] 2. Persist `report.md` next to each bounded run log
  - [x] 2-1. Derive the per-run Markdown path from the run `events.jsonl` path
  - [x] 2-2. Carry the Markdown path through the structured report/transcript payloads
- [x] 3. Support explicit Markdown export through `--output`
  - [x] 3-1. Treat `.md` / `.markdown` output destinations as Markdown instead of JSON
- [x] 4. Lock the behavior with focused CLI/report tests
- [x] 5. Update README / architecture / changelog / todo tracking

## Decisions
- The default operator-facing artifact should be `report.md` inside each turn directory, not a separate top-level workspace file.
- `--output` stays backward compatible: JSON remains the default unless the destination extension is Markdown.
- The Markdown memo should remain human-readable first, with the structured appendices pushed to the end instead of replacing the clean console memo.

## Notes
- Focused validation after implementation:
  - `python -m py_compile src/dan/cli/research.py src/dan/cli/research_product.py tests/test_cli/test_research.py`
  - `PYTHONPATH=src pytest -q tests/test_cli/test_research.py` (`36 passed`)
