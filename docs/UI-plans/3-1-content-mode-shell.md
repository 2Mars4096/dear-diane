# 3-1: Content Mode Shell

**Parent:** [3-content-mode-and-live-preview](3-content-mode-and-live-preview.md)
**Status:** in-progress
**Goal:** Replace the current Content-mode placeholder with a real workspace surface for knowledge-base browsing, editing, previewing, and mode-specific chat.

## Context

- `editor/src/store/useAppStore.ts` already reserves the `content` mode id, label, icon, and shortcut, but keeps it disabled.
- `editor/src/components/shell/AppShell.tsx` currently treats Content mode as a placeholder panel alongside Analytics.
- The rest of the shell already supports the right lifecycle model: per-workspace mode persistence, keep-alive hidden mounts, shared notifications/settings, and `ModeChatSidebar` for non-Chat modes.
- Content mode should feel distinct from Research and Development: page navigation and preview should dominate, while diagnostics and AI assistance stay secondary.

## UI Brief

- **Surface:** shell-level Content mode workspace
- **Audience:** users editing local knowledge-base pages rather than source code or workflow graphs
- **Primary action:** choose a content page, keep the editor and rendered page visible together, and stay in one focused workspace
- **Dominant concept:** a tidy editorial cockpit with one library rail, one writing desk, and one live preview
- **Constraints:** preserve the current shell architecture, reuse per-workspace state, and keep hidden modes inert
- **Anti-goals:** clone Research mode wholesale, hide preview status, or overload the first slice with publishing and CMS management

## No-Change Zones

- Reuse the shared shell primitives instead of creating a parallel app shell for content.
- Keep the mode-specific chat sidebar as a sidecar, not the primary authoring surface.
- Keep browser preview truthful: no fake "fully working" shell when managed local preview is unavailable.

## Tasks

- [x] 1. Activate Content mode in the shared shell
  - [x] 1-1. Enable the `content` mode entry in `MODE_CONFIGS`, mode shortcuts, and title/favicons
  - [x] 1-2. Replace the current placeholder panel with a lazy-loaded `ContentMode` component
  - [x] 1-3. Preserve the same per-workspace mount/isolation rules used by Chat, Research, Development, and Operations
- [ ] 2. Build the Content mode shell layout
  - [x] 2-1. Left rail: content-project selector plus page/library navigation
  - [x] 2-2. Center desk: editor and preview as the primary paired surfaces
  - [x] 2-3. Secondary rail/status strip: preview status, diagnostics summary, save state, and compact page metadata
  - [ ] 2-4. Responsive behavior: degrade cleanly on narrower widths without hiding the current task
- [x] 3. Add workspace-scoped content project selection
  - [x] 3-1. Detect candidate Hugo/content roots inside pinned workspace paths
  - [x] 3-2. Allow explicit attach/select when auto-detection is ambiguous
  - [x] 3-3. Persist the active content project and last-open page per workspace
- [x] 4. Integrate Content mode with the shared shell systems
  - [x] 4-1. Reuse `ModeChatSidebar` with content-specific context assembly
  - [x] 4-2. Surface content-specific notifications/status text without duplicating shell chrome
  - [x] 4-3. Keep shell shortcuts and listeners gated on `activeMode === "content"`
- [ ] 5. Validate shell behavior
  - [ ] 5-1. Add focused tests for Content mode activation, workspace restore, and hidden-mode inactivity
  - [ ] 5-2. Manually verify mode switching, sidebar toggles, and keyboard shortcuts once the first shell slice lands

## User-Facing Acceptance

- Switching into Content mode feels native to DAN's existing shell.
- The page list, editor, and preview can stay visible together without competing chrome.
- Reopening a workspace restores the same content project and page instead of dropping the user back into an empty placeholder.

## Decisions

- Content mode should be a first-class top-level mode, not a tab inside Research or Development.
- The shell should prefer a single active content project per workspace in Phase 1, even if multiple Hugo roots are discoverable.
- The preview/status strip belongs in the mode surface, not in the global shell header.
- The preview should not get its own second "card" once a real rendered page exists; route/status/actions should stay compact and ride on the preview surface itself.
- The shell slice now includes explicit Save + `Cmd+S` and a managed preview rail, but autosave, deeper validation, and recovery still belong to the dedicated follow-on plans.
- The shell should accept manually attached content roots that lack a Hugo config file, but it must surface that state explicitly so users do not mistake route inspection for a running preview server.

## Notes

- The current disabled shell placeholder is useful because it already reserves the product surface; this plan replaces that placeholder rather than inventing a new navigation seam.
- If the sidebar gets too dense, keep the initial scope narrow: project selector, page tree/list, recents, and lightweight filters are enough for V1.
- 2026-04-19 implementation slice:
  - Content mode now mounts through `AppShell` as a first-class workspace panel.
  - `useContentStore` plus session persistence now restore the active project, active page, and local drafts per workspace.
  - `ContentMode.tsx` now discovers Hugo/content roots from pinned paths, can auto-bootstrap empty workspaces from `DAN_DEFAULT_CONTENT_ROOTS` or the common sibling `../my-knowledge-base` layout, renders the page rail as a nested collapsible folder tree for tiered knowledge bases, replaces the old cramped right sidebar with a larger preview-first split desk, supports manual attach, lists editable pages, exposes a Monaco markdown desk, supports explicit Save + `Cmd+S`, and shows a real Hugo-preview/status rail plus content-specific chat context.
  - Focused coverage exists for content-mode model helpers, content chat context assembly, and content session persistence; shell mount/inactivity tests still remain.
- 2026-04-20 refinement:
  - `ContentMode.tsx` now removes the redundant standalone preview header card.
  - Live preview actions and status chips now sit as compact overlay controls on the rendered page itself, so the preview owns the right pane.
  - Browser/degraded fallback no longer shows that extra preview chrome when desktop preview is unavailable.
  - The left-rail intro card and the editor's standalone `Draft` strip were also collapsed into a tighter shell so the page tree, editor, and preview read as one workspace instead of stacked panels.
  - The browser fallback copy was then removed entirely, leaving that preview surface visually quiet instead of explanatory.
  - The browser fallback now also stops rendering a dead `Attach` button in the Projects header; it shows a compact `Desktop only` badge instead so local-folder attach does not look broken.
  - Packaged-app bootstrap now checks stronger local `my-knowledge-base` roots too, including the operator's Dropbox Projects path, so DAN.app can auto-adopt the real knowledge base instead of only working from repo-relative heuristics.
  - Packaged-app Electron bridge no longer depends on `node:path` inside the preload entrypoint, so `window.electronAPI` can come up cleanly in sandboxed desktop runs instead of silently dropping Content mode into browser fallback.
  - The host `BrowserWindow` now also explicitly disables Electron's renderer sandbox in packaged runs, because the preload cleanup alone was not sufficient: installed DAN.app could still render Content mode as browser-only and show no pages until the host bridge itself was made reliable.
