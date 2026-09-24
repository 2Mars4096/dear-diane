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
- Desktop package prepared; replacing the running installation remains an explicit Updates action.
