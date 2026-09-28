# 4-8: Literature side panel and batch ingestion

**Parent:** [4-work-notes-gui](4-work-notes-gui.md)
**Status:** completed
**Goal:** Import batches of PDFs into the Hugo KB from a persistent Literature side-panel tool.

## Tasks
- [x] Add Literature next to Chat/Files with compact Library and Import views and main-reader preview.
- [x] Persist staged PDFs, destination, citation inputs, per-item progress, retries, and selected lead execution.
- [x] Retrieve citation candidates, validate PDF pairing, and generate grounded notes through the existing Agent V2 runtime.
- [x] Apply a validated ready subset with stable keys, verified copies, collision protection, and library refresh.
- [x] Verify backend lifecycle/file safety, frontend flows, production build, and browser layout.
- [x] Update user and architecture documentation.

## Decisions
- Keep source PDFs; staged copies belong to the backend host. No Zotero installation required.
- Preserve supplied BibTeX; retrieved Crossref records are additional citation inputs.
- Lead returns a structured draft; deterministic ingestion owns KB writes. Existing pages are never replaced.
- Papers and explicitly labelled book overviews have different note scopes. No claim of a full book read.
- Interrupted active items retain their inputs for explicit retry; queued items resume when the backend starts.

## Validation
- 19 backend tests pass: PDF staging/delivery, citation parsing, incomplete Crossref fallback, schema validation, selected-lead execution, collision safety/rollback, source tampering, confinement, ready-subset atomicity, corrections, stop, and restart recovery; includes existing paper/document checks.
- 23 frontend tests pass across Literature, paper library/search/reading, global drops, and persistent panels. Production TypeScript/Vite build and bundle budgets pass.
- Live Crossref: complete DOI lookup (`10.1038/nature14539`) and incomplete-record title fallback verified.
- Live native Codex: synthetic three-page PDF matched supplied BibTeX, extracted all pages, visually inspected its table, and returned grounded notes with valid anchors. Browser Apply created the verified PDF copy and Hugo page in a temporary KB; originals remained intact.
- Browser: desktop side-panel navigation, staged main-reader preview, ready-item import, library discovery, reload persistence, and 390px layout pass. Page width and document scroll width both 390px; internal imports do not appear as project folders.
- Hugo build using the user's actual theme and the isolated generated page succeeds (15 generated pages). No user KB files were changed. Installed desktop application was not replaced.

## Limits
- Automatic metadata uses Crossref. Books/working papers without usable records may need supplied BibTeX; book scope is an overview, not an automatic full-book read.
- PDF extraction needs the existing optional `pdf` extra on the execution host. OCR and visual reading depend on the selected lead's available tools.
- Existing pages are flagged for review rather than merged automatically. Staging is removed per document using Remove from this batch; no automatic cleanup deletes user files.

## Installation and publication
- [x] Revalidate 19 backend and 27 frontend tests, production build/budgets, and Electron compilation before packaging.
- [x] Reinstall the signed macOS arm64 build after verifying no app process, backend listener, active chats, or active literature imports. Retain the prior app as rollback and preserve user state.
- [x] Verify installed archive equality, signature, app-owned backend, desktop-proxy health, Literature API availability, and the existing 305-paper catalogue without warnings.
- [x] Commit window appearance, literature backend, and Literature side-panel/docs in separate logical sections.
