# 3-5: In-Preview Editing Bridge

**Parent:** [3-content-mode-and-live-preview](3-content-mode-and-live-preview.md)
**Status:** in-progress
**Goal:** Let Content mode support direct rendered-page edits without making generated HTML the source of truth.

## Context

- The user wants a Notion-like "edit on the page" workflow, but the knowledge base still stores content as Markdown/frontmatter and renders through Hugo.
- The current preview already comes from a DAN-managed local Hugo server, so the product seam is not rendering fidelity; it is round-tripping a rendered block back to the correct Markdown source.
- The first desktop bridge used Electron `webview`, but the stable contract is now a plain managed `iframe` plus a page-owned `postMessage` bridge gated by `dan_preview=1`.

## No-Change Zones

- Keep Markdown/frontmatter as the only persisted source of truth.
- Keep Hugo as the renderer; do not invent a DAN-side HTML authoring format.
- Keep browser fallback honest: direct in-preview editing is a desktop-only capability.

## Tasks

- [x] 1. Build the preview-host bridge
  - [x] 1-1. Keep the managed preview on an iframe-capable host seam for both desktop and browser
  - [x] 1-2. Add a tiny embedded-page bridge that can highlight/select editable rendered blocks and report them back to DAN
  - [x] 1-3. Keep non-desktop or degraded paths on the simpler preview surface without pretending on-page edit exists there
- [x] 2. Map rendered selections back to source
  - [x] 2-1. Support the first common block families: headings, paragraphs, list items, and blockquotes
  - [x] 2-2. Special-case the common Hugo page-title seam by mapping rendered H1 text back to frontmatter `title`
  - [x] 2-3. Fail honestly when a rendered node cannot be mapped back to Markdown safely
- [x] 3. Apply edits through the existing save/preview lifecycle
  - [x] 3-1. Add a compact on-page edit card inside the preview pane
  - [x] 3-2. Write accepted edits back to Markdown/frontmatter, save through the existing validation/save path, and let Hugo refresh the page
  - [x] 3-3. Disable on-page editing while preview is stale, conflicted, or otherwise no longer source-aligned
- [ ] 4. Prove the product loop on a real knowledge base
  - [x] 4-1. Add focused unit coverage for rendered-selection matching and Markdown/frontmatter rewrites
  - [ ] 4-2. Smoke-test direct click/edit/apply flows inside the installed DAN app against `../my-knowledge-base`
  - [ ] 4-3. Expand the supported mapping set if real pages expose common unmapped shortcode/theme seams

## User-Facing Acceptance

- A user can click a rendered heading/paragraph/list item/quote in preview and edit it without manually finding the source block first.
- The applied edit lands in Markdown/frontmatter, not in generated HTML.
- DAN refuses on-page editing when the preview no longer matches disk, instead of silently editing the wrong source block.

## Decisions

- The correct model is "HTML as interaction surface, Markdown as storage", not "edit generated HTML files".
- The bridge should stay desktop-honest for source writes, but the rendering surface itself should be the same iframe contract in desktop and browser when possible.
- Phase 2 starts with a narrow block family and explicit unsupported states instead of claiming arbitrary HTML round-tripping.

## Notes

- The embedded preview page itself is now the primary guest seam: under `dan_preview=1`, the Hugo theme owns rendered-node tagging, selection highlights, runtime-issue forwarding, and `postMessage` traffic back to DAN.
- `editor/src/components/modes/contentModePreviewEditing.ts` is the round-trip layer that maps rendered selections back to Markdown blocks or frontmatter `title`.
- The preview controls now live as lightweight overlays on the rendered page instead of in a separate preview card, so the on-page editing bridge shares one visual surface with the real site.
- 2026-04-20 hardening: the preview host now treats the iframe guest as an explicitly asynchronous dependency. `ContentMode.tsx` tracks guest readiness off the iframe `load` event, resets it whenever the preview surface is replaced, and only sends `dan-preview:*` messages after the frame is ready so the installed app no longer depends on Electron `webview` attachment semantics.
- 2026-04-20 diagnostics: the preview guest now also forwards resource-load/runtime failures back to the host, so custom client-rendered pages such as `/graph/` can surface an honest `Page script issue` overlay instead of looking like DAN silently cropped the page body.
- 2026-04-20 chrome pass: route/status chips are gone from the live page, preview actions are icon-only and hover-revealed while running, and the guest resets scroll to the top on initial page load.
- 2026-04-20 embedded-layout pass: the guest bridge now also owns DAN-only preview CSS for narrow article panes, forcing the knowledge-base `.three-col` layout into a single visible `.content-area` and hiding TOC/notes/tags/export controls inside the embedded surface.
- 2026-04-20 embedded-mode pass: DAN now marks in-app preview URLs with `dan_preview=1`, and the target Hugo theme uses that mode to skip its article-page mutation script entirely while rendering a lighter single-column reader view. The same embedded mode now also owns the inspect/runtime `postMessage` bridge, which narrows the host seam back down to selection/edit messaging instead of fighting page-owned layout logic from outside.
- 2026-04-20 citation follow-up: the embedded page now also keeps a lightweight citation/reference rewrite in `dan_preview=1` mode, so preview-edit stabilization does not regress core KB behaviors like `@goldsmith2020bartik` citations.
- 2026-04-20 backlinks follow-up: the embedded page now also rebuilds lightweight `Cited By` backlinks in `dan_preview=1` mode and stops hiding `#cited-by-container`, so stripped preview mode keeps inbound-reference context instead of restoring only inline citations.
- Follow-up work can add better preview-context preservation, richer block families, and page-theme-aware root targeting once real knowledge-base smoke reveals the main misses.
