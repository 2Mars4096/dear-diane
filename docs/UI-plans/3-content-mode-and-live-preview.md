# 3: Content Mode & Live Preview

**Status:** in-progress
**Goal:** Add a DAN Content mode for Markdown/Hugo knowledge-base authoring with page-level live editing, Hugo-accurate preview, and safe autosave/validation.

## Context

- `editor/src/store/useAppStore.ts` already defines a disabled `content` mode, and `editor/src/components/shell/AppShell.tsx` still renders a placeholder "Content Mode" panel.
- The initial target is the user's local Hugo knowledge base, but the DAN feature should stay reusable for other local Markdown/Hugo knowledge-base workspaces that keep Markdown files as the source of truth.
- The user explicitly wants page-level live edits plus live preview first, not a Notion-style block model.
- DAN already has the shell primitives this mode needs: workspace tabs, per-mode chat sidebars, Electron file access, file watching, keep-alive mode mounting, and browser-vs-desktop capability gating.
- The critical constraint is rendering fidelity: Hugo shortcodes, MathJax, citations, backlinks, and theme behavior should be previewed through Hugo itself instead of a DAN-side markdown approximation.

## UI Brief

- **Product surface:** DAN desktop Content mode
- **Audience:** users who maintain local Hugo/Markdown knowledge bases and want to edit and preview pages without leaving DAN
- **Primary action:** open an existing page, edit it, and see the real rendered page update in place
- **Dominant concept:** an editorial desk: source on one side, published page on the other, with quiet diagnostics rather than CMS chrome
- **Constraints:** desktop-first, reuse DAN's shell/mode architecture, preserve Markdown/Hugo as the source of truth, and keep browser fallback honest
- **Anti-goals:** reimplement Hugo in the renderer, invent a parallel block schema, or broaden V1 into asset management, publishing, and a full CMS

## No-Change Zones

- Keep Markdown files in the user's content repo as the only source of truth.
- Keep Hugo as the preview/publishing engine; the preview pane should render a real Hugo page, not a DAN-side approximation.
- Keep Phase 1 scoped to editing existing content pages; page creation, media upload, and block-level drag/drop are follow-up work.

## Tasks

- [x] 1. Freeze the phase-1 scope and acceptance contract
  - [x] 1-1. Limit V1 to existing content pages with page-level editing and preview
  - [x] 1-2. Treat the Hugo-rendered iframe preview as the authoritative rendering contract
  - [x] 1-3. Treat Electron desktop as the primary capability surface and keep browser fallback explicit
- [ ] 2. Execute the shell and mode-surface work in [3-1-content-mode-shell](3-1-content-mode-shell.md)
- [ ] 3. Execute the editor/frontmatter work in [3-2-markdown-editor-and-frontmatter](3-2-markdown-editor-and-frontmatter.md)
- [ ] 4. Execute the Hugo preview lifecycle work in [3-3-hugo-preview-bridge](3-3-hugo-preview-bridge.md)
- [ ] 5. Execute the save/autosave/validation work in [3-4-save-autosave-and-validation](3-4-save-autosave-and-validation.md)
- [ ] 6. Execute the rendered-page editing bridge work in [3-5-in-preview-editing-bridge](3-5-in-preview-editing-bridge.md)
- [ ] 7. Prove end-to-end product acceptance
  - [ ] 7-1. Open an existing content page from a pinned workspace and resolve the correct preview target
  - [ ] 7-2. Type into the editor, autosave, and see the rendered page refresh after the Hugo rebuild
  - [ ] 7-3. Click a supported rendered block in preview, edit it, and see the underlying Markdown/frontmatter update safely
  - [ ] 7-4. Surface invalid frontmatter/save errors without losing local draft state
  - [ ] 7-5. Keep hidden Content mode state inert under the same shell-isolation contract as other modes

## User-Facing Acceptance

- Content mode feels like a real DAN workspace, not a detached mini-app.
- The preview matches the site's actual Hugo rendering for the selected page.
- Live editing is fast enough to feel continuous, but save/validation failures are explicit rather than silent.
- A user can keep writing in Markdown, shortcodes, and citations without learning a new content schema.

## Decisions

- Phase 1 is page-level authoring only; block-native editing is deferred.
- The preview bridge is Hugo-first, not markdown-preview-first.
- The initial target is a Hugo knowledge base, but the Content mode contract should stay reusable for other local static-content workspaces.
- On-page editing should treat rendered HTML as the interaction layer and Markdown/frontmatter as the persisted source layer.
- Once the real preview is available, it should dominate the desk; preview status/actions belong as compact in-surface controls rather than a second right-pane card.
- Manual attach should validate that the selected root at least has a `content/` directory before DAN adopts it as the active project; missing Hugo config is allowed, but preview boot must stay honest until the bridge is wired.

## Notes

- This mode should sit closer to "knowledge-base studio" than to Research mode or Development mode, even though it will reuse shell pieces from both.
- The mode-specific chat sidebar should help with editing, citations, and structure, but it should not become the primary writing surface.
- Follow-up work can add new-page scaffolding, media management, publish/deploy actions, cross-page link tooling, and richer block affordances after the page-editing loop is solid.
- 2026-04-19: the main Phase 1 authoring loop is now landed. Content mode is enabled as a real workspace surface with project discovery/attach, empty-workspace bootstrap from `DAN_DEFAULT_CONTENT_ROOTS` or sibling `../my-knowledge-base`, a nested collapsible page tree for tiered folder layouts, a preview-first split desk that gives the rendered page substantially more space, Monaco markdown drafting, per-page local draft memory, per-workspace session restore, managed Hugo preview in an iframe, debounced autosave plus explicit Save/`Cmd+S`, validation, and external-change conflict recovery.
- 2026-04-19: direct smoke against the real target knowledge base now proves both the bootstrap path and the managed Hugo preview lifecycle at the Electron seam. Remaining gaps for this track are full in-app rebuild/recovery verification plus deeper knowledge-base-specific citation/reference checks.
- 2026-04-19: the first Phase 2 on-page editing slice is now landed in source. `ContentMode.tsx` exposes an `Edit on Page` workflow in the preview pane, and `contentModePreviewEditing.ts` maps supported rendered blocks back to Markdown/frontmatter so the saved file, not generated HTML, remains canonical. Remaining gaps are installed-app smoke against the real knowledge base and widening the supported block/theme cases as real pages demand it.
- 2026-04-20: the preview chrome was simplified again in source. The separate top preview card is gone, route/status/start-stop/open/edit controls now live as lightweight overlays on the real page surface, and browser fallback no longer wastes vertical space on redundant preview-only chrome.
- 2026-04-20: the surrounding shell chrome was tightened too. The left workspace explainer card and editor-side `Draft` strip were removed as standalone boxes, and the browser build now shows a quiet inactive preview surface instead of a big `Desktop runtime required` warning card.
- 2026-04-20: Content mode now surfaces guest-page runtime/resource failures inside the preview surface. This keeps special client-rendered pages such as the knowledge-base `/graph/` route from failing as an unexplained blank white pane when an in-page script or CDN asset breaks.
- 2026-04-20: the rendered page is now cleaner again. Route/status chips were removed from the preview surface, running-preview actions were compressed into a hover-only icon dock, and the guest bridge resets scroll to the top on load so selected articles do not appear to open at the footer.
- 2026-04-20: the embedded article view now gets a DAN-only narrow-pane layout override. The guest bridge forces the knowledge-base theme's multi-column article layout into one readable column and hides TOC/notes/tags/export chrome inside DAN so the lower body is not pushed out of view by full-browser page furniture.
- 2026-04-20: the preview contract is now explicit across both codebases. DAN appends `dan_preview=1` only for the embedded guest URL, and the target Hugo theme now honors that flag by disabling its heavy article-page mutation script and switching to a stripped single-column reader mode for embedded preview.
- 2026-04-20: the final stabilization pass removed Electron `webview` from the preview surface entirely. Desktop and browser now both render through the same iframe surface, while the embedded knowledge-base page owns the `postMessage` inspect/runtime bridge under `dan_preview=1`. This avoids the partial-paint/attachment failures that kept showing up in the installed app.
- 2026-04-20: embedded preview now also restores the lightweight citation transform under `dan_preview=1`. The full browser-page mutation script stays off, but inline `@pageID` citations still rewrite to linked citations/references in DAN preview instead of rendering as raw tokens.
- 2026-04-20: embedded preview now also restores `Cited By` backlinks under `dan_preview=1`. The same lightweight guest mode now derives inbound references from `citations.json` and keeps `#cited-by-container` visible, so paper/article pages do not lose backlink context just because the heavier full-browser page script stays disabled.
- 2026-04-20: the embedded-mode gate is now standalone-safe too. The KB theme only enters DAN preview mode when `dan_preview=1` is present and the page is actually running inside an embedded surface, so independent Hugo/browser use keeps the normal full site behavior even if that query param is copied around manually.
