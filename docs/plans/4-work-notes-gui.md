# 4: Work and Notes GUI

**Status:** in-progress
**Goal:** Keep one desktop/mobile product surface for Super DAN work sessions, task progress, artifacts, previews, and Markdown notes.

## Tasks

- [x] Consolidate the desktop app into the Work/Notes workspace.
- [x] Retain session CRUD, Agent V2 tasks/runs/events, filesystem, note, preview, and learning APIs.
- [x] Retain structured attachments and clipboard screenshot input.
- [x] Remove graph editor, Code/Research/Content modes, marketplace, terminal, debugger, LSP, Git, and legacy shell components.
- [x] Add Notes collection navigation and focused frontend/server regressions.
- [ ] Continue Work/Notes UX refinement without adding parallel product modes.

## Decisions

- `ChunkWorkspaceApp` is the desktop application root.
- Electron owns only backend lifecycle and narrow native filesystem/shell/watch bridges.
- The GUI consumes the same Super DAN control-plane contracts as the terminal UI.

## Workbench refresh
- [x] [4-1-agent-workbench](4-1-agent-workbench.md): conversation-first shell and spatial project/session navigation.
- [ ] [Native worker integration](../UI-plans/2-native-agent-workers.md): persistent mixed Codex/Claude children and scoped controls.

- [x] [4-2-desktop-updates](4-2-desktop-updates.md): local install/restart and configured release updater in Settings; release activation requires signing/feed setup.
