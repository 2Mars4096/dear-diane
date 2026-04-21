# 54-19: DAN Reader Vision-Assisted PDF Ingestion

**Parent:** [54-dan-control-plane-rewrite-around-universal-agents](54-dan-control-plane-rewrite-around-universal-agents.md)
**Status:** completed
**Goal:** Let `DAN Reader` reuse the shared `pdf_read` text/hybrid/vision path with page-window controls so local PDF testing can use vision assistance without falling back to the heavier DAN Research organism.

## Tasks
- [x] 1. Expose reader-level PDF ingestion controls.
  - [x] 1-1. Add `--pdf-mode text|hybrid|vision`.
  - [x] 1-2. Add page-window flags plus optional vision model/prompt overrides.
- [x] 2. Reuse the shared PDF tool path instead of building a second vision stack.
  - [x] 2-1. Route reader ingestion through `dan.tools.pdf_read.read_pdf_file(...)` for hybrid/vision runs.
  - [x] 2-2. Preserve page-level extraction payloads and surface extraction warnings/mode notes on the reader document artifact.
- [x] 3. Revalidate and document the new seam.
  - [x] 3-1. Add focused reader tests for parser defaults and the shared hybrid handoff.
  - [x] 3-2. Smoke-test the Lee 2021 PDF in text, hybrid, and pure-vision slices and record the recommended default.

## Decisions
- Keep `DAN Reader` flat. Reuse the shared `pdf_read` tool seam instead of introducing a reader-specific vision organism or planner layer.
- Default to `text` for speed and offline stability, but make `hybrid` the recommended mode for scholarly PDFs because it preserves structure better than pure vision on the current live fixture.
- Keep page-range scoping at the top-level reader contract so latency experiments and partial document extraction stay deterministic.

## Notes
- On `/Users/lizhi/Downloads/lee2021information-JF.pdf`, full-document `--pdf-mode hybrid` preserved the Roman-numeral hierarchy through `I` to `VII` while carrying the extraction mode into document notes.
- Pure `--pdf-mode vision` worked on a bounded page slice, but heading recovery fell back to page-window sections rather than the stronger paper outline recovered by `text`/`hybrid`.
