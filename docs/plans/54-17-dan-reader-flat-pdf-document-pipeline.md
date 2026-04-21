# 54-17: DAN Reader Flat PDF Document Pipeline

**Parent:** [54-dan-control-plane-rewrite-around-universal-agents](54-dan-control-plane-rewrite-around-universal-agents.md)
**Status:** completed
**Goal:** Add a first-class `dan-reader` / `dan reader` product surface for bounded local PDF extraction, section-by-section summarization, and Markdown/LaTeX/JSON conversion without routing through the DAN Research organism.

## Tasks
- [x] 1. Add a flat reader CLI contract.
  - [x] 1-1. Define one CLI surface with `summarize`, `extract`, and `convert` actions plus `md|latex|json` output formats.
  - [x] 1-2. Wire the new surface into `pyproject.toml` console scripts and the top-level `dan` subcommand router.
- [x] 2. Build the direct PDF reader pipeline.
  - [x] 2-1. Read local PDFs deterministically via `pypdf` with simple metadata extraction.
  - [x] 2-2. Detect section headings with lightweight Roman/lettered heading heuristics and synthesize an introduction section when the front matter implies one.
  - [x] 2-3. Fall back to fixed page windows when stable headings are not available.
- [x] 3. Add thin bounded summarization and renderers.
  - [x] 3-1. Summarize sections in parallel with a direct provider call when an LLM is configured.
  - [x] 3-2. Fall back to deterministic snippet summaries when no model is configured or a section summary fails.
  - [x] 3-3. Render extract/summary/convert artifacts to Markdown, LaTeX, or JSON.
- [x] 4. Validate and document the new surface.
  - [x] 4-1. Add focused CLI/parser/render/extraction tests.
  - [x] 4-2. Smoke-test the reader against the Lee 2021 PDF used in the latency discussion.
  - [x] 4-3. Update backlog, changelog, architecture, and README references.
- [x] 5. Keep follow-on reader hardening under the same `54-*` umbrella.
  - [x] 5-1. Land [54-18-dan-reader-structure-and-contract-hardening](54-18-dan-reader-structure-and-contract-hardening.md)
  - [x] 5-2. Land [54-19-dan-reader-vision-assisted-pdf-ingestion](54-19-dan-reader-vision-assisted-pdf-ingestion.md)

## Decisions
- Keep the first implementation stateless. No `.dan-reader/` session/config layer, no planner/reviewer shell, and no reuse of the deep-research organism for PDF summarization.
- Prefer one fat top-level contract over multiple small internal layers so the product stays inspectable and easy to iterate on.
- Support local PDFs first. The product name is `DAN Reader` because the scope can later expand to other document types, but this implementation is intentionally PDF-first.

## Notes
- The heading splitter is intentionally conservative: it prefers Roman numeral and lettered section markers common in papers and avoids aggressively treating numeric footnotes as headings.
- The current deterministic fallback summaries are usable for offline/local smoke tests but are not the final quality target for literature-review workflows.
- The first follow-on reader refinements now live beside this plan as `54-18` and `54-19` rather than as deeper nested plan files, which keeps the repo on the two-level numbering rule.
