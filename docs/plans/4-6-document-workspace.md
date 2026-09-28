# 4-6: Open and edit files in DAN

**Parent:** [4-work-notes-gui](4-work-notes-gui.md)
**Status:** completed
**Goal:** Drop PDFs and common files into document tabs, read them, and safely edit text alongside conversations.

## Tasks
- [x] Connect file drops and Open file to existing main tabs and PDF reader.
- [x] Add text editing, Markdown preview, media viewing, explicit save and dirty-close protection.
- [x] Preserve original desktop paths; browser selections open as downloadable local copies.
- [x] Verify file confinement, UTF-8/size guards, concurrent-change checks, UI, and build.

## Decisions
- Reuse the current reader and flat workbench tabs; preserve the selected theme and conversation.
- Desktop/project text saves require an unchanged content revision; never save truncated or binary input.
- Browser-selected files remain local to the browser until explicitly downloaded, with no implicit remote upload.

## Validation
- 8 backend document/remote tests and 91 frontend document/workbench/reader tests pass; production bundle budgets and Electron compilation pass.
- Live browser: PDF render, file drop, Markdown preview, draft retention across tabs/Notes, canceled dirty close, actual file save, stale-revision conflict, and 390px layout verified.
- Updated desktop package installed on September 24 after explicit user approval; app launch and owned backend health verified.

- September 24 follow-up: fix non-Latin preview headers and worker-relative PDF decoder assets. Five backend file/document tests and twelve reader tests pass; exact user book renders on cover and page 10 with JBIG2 HTTP 200. Signed update prepared for automatic local discovery; applying it restarts the backend to load the server fix.

## September 28 — conversation file links
- [x] Preserve relative/absolute Markdown file links and explicit inline-code paths from Codex/Claude replies; resolve against the project root.
- [x] Native click opens actual files/folders; right-click offers Open, Reveal, installed Cursor, Copy path, text-content copying, and Save as.
- [x] Validate sender/path/existence, surface missing paths, and prevent local opening of remote-host links.
- [x] Twenty-three focused tests, Electron compilation, frontend build and bundle budgets pass. Move shared Markdown rendering into the content chunk after initial shell budget failure.
- [x] Install signed desktop file-link update after idle/ownership checks, retaining rollback. Installed archive equals prepared build; signature, desktop-proxy health and app-owned backend pass.
- [ ] User acceptance: click/right-click a file or folder in an existing Codex/Claude reply.
