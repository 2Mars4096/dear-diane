# 4-7: Paper library and reading sessions

**Parent:** [4-work-notes-gui](4-work-notes-gui.md)
**Status:** completed
**Goal:** Find papers by their ideas and metadata, then resume reading and discussion across projects.

## Tasks
- [x] Index the existing Hugo paper collection, reuse Notes discovery, and expose configurable sources without moving originals.
- [x] Add keyboard paper search and a persistent library tab with filters, sorting, details, citations, pins, and chat references.
- [x] Persist reading sessions, position, annotations, and conversation links on the execution host; show recent sessions in the sidebar.
- [x] Verify retrieval, source confinement, session reopening, keyboard/mobile UI, and production build.
- [x] Update user and architecture documentation with behavior and limitations.

## Decisions
- One resumable reading session per library paper; closing a tab does not end or delete it.
- Library and reading sessions span projects on the same DAN host. Remote hosts keep their own sources and state.
- Reuse Hugo title/pageID/BibTeX/tags/abstract and paperPDF links. Source Markdown/PDFs remain in place and unmodified.
- Cmd/Ctrl+K opens paper search; Cmd+Shift+P continues to switch projects.
- Initial search covers metadata and reading notes; PDF full-text/semantic retrieval remains future work.

## Validation
- 9 backend and 29 frontend focused tests pass: metadata refresh, confinement/missing links, durable sessions/revision conflicts, retrieval, keyboard search, reader state, documents, and sidecar run-link ordering.
- Production build and existing bundle budgets pass; Electron compilation passes.
- Browser checks on an isolated store: 304 papers discovered, typo search, list/table, filters, pinning, source settings, sidebar hiding, BibTeX copy, staged references, and 390px layout without horizontal overflow.
- Navigating to page 3 survives reload. Clearing local reader/sidecar data still restores server-backed page, annotation, and saved conversation fixtures. No live model answer was generated for this change.
- Signed macOS arm64 update prepared; signature, packaged paper modules, and prepared-update detection verified. Installation remains available through DAN settings; the running app was not restarted.

## Limits
- Existing project-scoped reading conversations remain in their original scope; library papers get a dedicated host-wide conversation.
- Source updates use focus/30-second polling plus explicit Refresh. Full PDF text/semantic retrieval and cross-host synchronization remain future work.

## Installation follow-up
- [x] Install the signed build after idle-work and exact app/backend ownership checks; keep the prior app bundle as rollback.
- [x] Verify installed archive matches the prepared build, signature is valid, and both owned backend and desktop proxy return healthy status with all 304 PDFs available.
- The initial post-install backend exited before listening. A logged normal relaunch succeeded; the first exit cause was not captured. No source/config changes were needed.

## Search keyboard follow-up
- [x] Reproduce pointer movement overriding Up/Down while the search input keeps focus; select rows on pointer entry instead of every movement.
- [x] Add a regression covering Down, pointer jitter, Up, and Enter (fails before the fix; all three search tests pass after).
- [x] Production build/bundle budgets, Electron packaging/signature, and installation pass. User confirms “now it works well.”
- Plain Up/Down passed in browser and isolated Electron before the fix; pointer jitter reproduced the selection reset. Input source is ABC. User confirmed success after installation. The isolated diagnostic browser was no longer available for a post-install replay.
