# 3: Reader and unified side panel

**Status:** in-progress
**Goal:** Read project PDFs without interruption (select → ask → answer in the side panel), and consolidate the workbench's right-side tools into one tabbed side panel.

## Tasks
- [x] 1. Unified side panel
  - [x] 1-1. One header toggle replaces Sidecar chat / Files / Activity / Preview; Notes stays a separate surface switch.
  - [x] 1-2. Tabs: Chat · Files · Preview · Activity · Team; one open at a time, remembered per device.
  - [x] 1-3. Team strip and file selection open the matching tab.
  - [x] 1-4. Phone bottom navigation retains separate pages; the same persistent side tabs work within Files/Preview and the Chat overlay.
- [x] 2. Reader (port of learning-assistant material guide PDF reader)
  - [x] 2-1. Port `interactive-pdf-viewer` (+ CSS module) and its libs (comments, highlight display, reference tags, PDF position) with DAN storage keys; pdfjs-dist 6.2.108 with a bundled worker.
  - [x] 2-2. Reader view in the main area for a project PDF, opened from Preview/Files; keeps its own scroll/zoom/position.
  - [x] 2-3. Ask sends the selection + page text to a per-PDF sidecar conversation; the reader never loses scroll or focus.
  - [x] 2-4. Comment saves a highlight with an optional note; comments list jumps back to the highlight.
- [x] 3. Validation: unit tests for ported libs, browser check with a real PDF (render, Ask staging, Comment/highlight, position restore, dark mode).
- [x] 5. OCR for scanned pages (tesseract.js in-browser, server cache), text-anchored highlights with re-location, annotated-PDF export, themed viewer overlays.
- [x] 6. Main-column tab bar (chat + PDFs), side panel follows the active tab, Notes tab for comments.
- [ ] 4. Send a real Ask and confirm the answer streams in the side chat without moving the reader.

- [x] 7. [Document workspace](../plans/4-6-document-workspace.md): global file drops/Open file, PDFs and editable/viewable document tabs, desktop original paths, and browser copies.

- [x] 8. Unicode file delivery and absolute PDF decoder/font URLs; verify the supplied 411-page Chinese scanned book through the desktop-path route, including cover and page 10.

- [x] 9. Keep side tools mounted after first visit in one frame; preserve drafts/scroll/log expansion, scope state to project/PDF/thread, and verify desktop + 390px phone switching.
- [x] 10. Dedicated active-tab close shortcut (Mac Control–Command–W / other Ctrl+Alt+W), discoverable on × buttons and routed through the unsaved-edit guard.

- [x] 11. Local Repair text toggle with per-document font compatibility, position preservation, and stale OCR cancellation guards; embedded subset-font fixture verified visually and across reopening.
  - [ ] 11-1. User checks their private PDF; screenshot alone cannot establish the cause or confirm repair.

- [x] 12. Signed desktop build installed/launched with rollback; verify packaged features, owned backend, desktop proxy, and unsaved-edit confirmation through the shortcut.

- [x] 13. Enable compatible glyph rendering for every PDF automatically; remove Repair text and its stored preference helper.

- [x] 14. Reinstall automatic font rendering build with rollback; verify installed reader options/button removal, signature, archive equality, and owned backend/proxy health.

- [x] 15. Replace the crowded tool tab strip with a current-tool menu, descriptions, keyboard access, and a running-work shortcut; verify eight tools at 316px/390px and preserve panel state.

- [x] 16. Install the signed tool-switcher build with rollback; verify installed archive, switcher assets, owned backend, and desktop proxy health.

## Decisions
- Reader is a surface (like Work / Notes), not an autonomy mode. Plan/Auto/Full access still govern what the sidecar agent may do.
- Ported later: OCR (in-browser instead of server), passage anchoring (client-side against page text), annotated-PDF export. Still not ported: teaching layers/modes and the tutor model picker; DAN answers through the configured lead. DAN reads PDFs directly from the project folder and answers through the configured lead.

- [x] 17. Reuse PDF reader/notes in the Document sidecar and extend select → annotate to source and rendered Markdown; stage annotation feedback in the main composer. See [4-6](../plans/4-6-document-workspace.md). Verified in source; not installed.

- 2026-10-02 release installed at user request after feature commits/push and idle checks. Signature/archive equality and owned backend/proxy health pass; app PID 46879, backend PID 46899. Rollback retained at `/Applications/.DAN-previous-e50968db-b9e1-4fa8-9963-72f081840563.app`. Existing-session title transfers stay disabled.

- [x] 18. Add OCR-independent rectangular area selection with Comment, Ask, and Add to Ref; render/upload selected crops for explicit chat staging. Native text selections retain text; OCR selections include the source crop.
- [x] 19. Store optional selection anchors on refs, preserve multiple selections per page, show persistent highlights, and position hover previews/page navigation at the selection. Keep whole-page refs and editable labels.
- [x] 20. Validate reference persistence, area notes, crop geometry, sidecar attachment payloads, and browser text/scanned-area flows. Production build/bundle checks pass; desktop installation not performed.

- [x] 21. Install area-selection/anchored-ref release, followed by restart-persistence/name cleanup; signed archive and backend/proxy health verified on October 3.

- [x] 22. Split scanned spreads at a detected gutter before OCR, preserve left-page/right-page order and coordinates, and invalidate old full-width OCR caches. Add persisted Auto layout / One page / Two pages override.
- [x] 23. Shorten Add to Ref to Ref; style only Ask as primary, and keep Comment/Ref secondary. Fit scan controls into a second toolbar row in narrow readers.
- [x] 24. Verify the supplied scan yields 27 lines and zero crossing the gutter; check the manual override, saved layout, action colors and 390px toolbar.
- [x] 25. Install and verify this OCR/toolbar correction. Signature/archive equality and backend/proxy health pass; app PID 23902 owns backend PID 23925.

- [x] 26. Replace layout recognition override with Single Page / Two Pages display, remove Select area, and fit the middle pane when Notes opens.
- [x] 27. Add Chinese word geometry and selected-crop vision transcription, preserving Unicode and native text.
- [x] 28. Save comments offline/immediately; retry deferred quotes without changing note bodies or draft typing.
- [x] 29. Apply six source-checked existing quote repairs, install, and verify restart/health. App PID 49135 owns backend PID 49178; all six quotes survive restart, note bodies/geometry match their pre-repair values.

- [x] 30. Reproduce missing embedded-font ligatures in Electron; use matching PDF.js legacy main/worker builds to supply missing runtime APIs. Generated LaTeX fixture visually verified; 24 reader tests, production build and bundle budgets pass. Installed and reopened; signature/archive equality and direct/proxy health pass (app PID 30685, backend PID 30712). Rollback: `/Applications/.Diane-previous-2d699ab3-97db-4273-b25f-5c7a1baa4ad9.app`. Other reader behavior unchanged.

- [x] 31. Remove first-40-page selection cutoff; bound background OCR only, enqueue/reprioritize visible pages after navigation, save incremental results, and discard stale completions. Regression tests cover pages 43/205, cached layers, navigation during/after OCR and unmount. Real Electron drag selection on image-only page 43 exposes Comment/Ask/Ref.

- [x] 32. Install scanned-page selection correction; signature/archive equality and direct/proxy health pass after logged relaunch (app 14227, backend 14233). Rollback: `/Applications/.Diane-previous-44c11ff3-7df4-485b-8fe2-a5ce95f30f2e.app`.

- [x] 33. Repeat requested reinstall and reopen through Launch Services; installed signature/archive and both health endpoints pass (backend PID 15045).

- [x] 34. Reuse OCR across reopen/restart, replace 40-page background batches with visible/neighbor demand, and persist browser-copy pages under content hashes. Verify cache hits, changed-file misses, no duplicate recognition and real Electron process restart (910 spans restored; no OCR worker).

- [x] 35. Install OCR reuse build and verify signature/archive equality, fixture exclusion and direct/proxy health (app 18655, backend 18695). Rollback: `/Applications/.Diane-previous-ebbb9848-39e4-48c5-aff6-b409a586e9c6.app`. Initial health requests timed out; both endpoints then responded without another restart.

- [x] 36. Restore continuous line selection from cached OCR words, filter margin noise, refine extents from rendered pixels and keep printed-page reading order. Validate a real drag across the supplied 12-line passage and exact Comment geometry; preserve existing cache and native text.

- [x] 37. Install continuous line-selection correction; signature/archive equality and health pass (backend 26968); temporary fixture excluded. Rollback: `/Applications/.Diane-previous-6f5a94e1-8050-4b0f-a637-11c2e2ee071a.app`.

- [x] 38. Make Ref save immediately with background quote enrichment, scope/deletion/rename guards and stable preview rasterization. Delayed-response Electron test saves the 12-line anchor in 27 ms and preserves label typing; 35 reader tests pass.

- [x] 39. Install immediate Ref correction; signature/archive equality, fixture exclusion and app-owned direct/proxy health pass (32107/32111). Rollback: `/Applications/.Diane-previous-d7dec842-c8c4-4dc5-91e3-383a7f0d5839.app`.

- [x] 40. Batch resize redraws, reuse selection geometry and preserve reading anchors during expand/restore. Measure 194→21 renders, 4→0 cancellations and 7→0 pixel scans; native maximize/restore retains page 43 and 54 selectable lines. Forty focused tests pass.

- [x] 41. Install resize optimization; archive/signature equality, fixture exclusion and direct/proxy health pass (backend 35759). Rollback: `/Applications/.Diane-previous-bdf8ba1a-7fdb-44a6-860c-a47a574f00c2.app`.

- [x] 42. Navigate from saved PDF highlight to Notes with painted-line hit testing, drag guards, keyboard activation and focused/scrolled matching entry. Verify empty note and unsaved edit cases; 37 reader tests and Electron interactions pass.
- [x] 43. Install highlight navigation; archive/signature match and direct/proxy health pass (backend 80350). Rollback: `/Applications/.Diane-previous-b780c850-b49c-4fe8-9c01-2592598736dc.app`.

- [x] 44. Dismiss the OCR-ready badge after two seconds; clear timer on page/document changes and unmount.
- [x] 45. Production build/budgets pass; installed badge update, verified signature/archive equality and direct/proxy health (backend 86105). Rollback: `/Applications/.Diane-previous-cffe9601-d50a-4390-8b5e-0b044678ef9d.app`.

- [x] 46. Give sidebar/Reading the Work composer styling and shared attachment/Lead/Team controls with independent execution settings, steering, and a persisted Next slot. Canonical chat surface identity fixes the reported 422; failed admission restores the transcript and draft.
- [x] 47. Identify the open PDF automatically for whole-document requests; retain excerpt-only wording for browser copies and selection context.
- [x] 48. Seven regression tests, server schema check, production build/budgets, and isolated Chrome layout/menu checks at 316/390/680px.
- [x] 49. Install/reopen sidebar composer and request fix; installed signature/archive equality and direct/proxy health pass (app 80117, backend 80157). Rollback: `/Applications/.Diane-previous-6935dd32-212d-412b-b2ef-80f67bc9b18d.app`.
- [ ] 50. Live-check sidebar execution against the user's open PDF; provider execution remains unverified.

- Sidebar Next is one durable local pending message; it starts while the panel remains mounted or after reopening. It is not the main chat's server-managed multi-message queue.

- [x] 51. Start sidebar/Reading input at two lines (60px including padding), retaining growth to six lines. Installed/reopened at user request; production budgets, signature/archive equality and direct/proxy health pass (app 50112, backend 50137). Rollback: `/Applications/.Diane-previous-37e38e54-6a7c-428f-bfaa-5dbcab82ae4d.app`.

- [x] 52. Guard OCR drag endpoints only in short gaps between overlapping line bounds; preserve the native anchor, text-line hits, margins, gutters, and native PDF selection. Correct after Chromium's native update and flush before capture.
- [x] 53. Apply zoom anchor correction before paint and scale the existing canvas with its page frame immediately, removing the old-size intermediate frame.
- Validation: synthetic Chrome forward/reverse drags and per-frame zoom geometry pass; real private PDF acceptance remains pending. Installed/reopened at user request; signature/archive equality and direct/proxy health pass (app 6558, backend 6575). Rollback: `/Applications/.Diane-previous-e22356a5-dbf5-4217-9db9-4a3527445f44.app`.

## October 10 — EPUB reading
- [x] Add a lazy EPUB engine with chapters, reflow/fixed layouts, sizing, theme, navigation and durable positions.
- [x] Connect main/sidecar openings and restart restoration to Reading chat and Notes.
- [x] Persist EPUB text locations for selection Ask, editable comments/highlights, clickable notes and references with previews.
- [x] Support illustrated passages and annotated EPUB export; retain PDF behavior.
- [x] Verify EPUB fixtures, persistence, interactions, malformed files, production build and bundle budgets.
- Validation: 69 reader/document/tab tests; isolated Electron reflow and fixed-layout fixtures, chapter links, Ask staging, notes/highlights, reference previews, font changes, annotated ZIP contents, restart restoration, local OCR/cropped PNG attachment (mock upload), and standalone 390px reader pass. No live AI request sent.
- EPUB exports use native EPUB highlights/notes; illustrated EPUBs recognize text when clicked. Existing Papers/Literature discovery remains PDF-only. DRM books display a clear unsupported message.
- Production build/budgets pass; desktop app not reinstalled.

## October 11 — unified controls and blank EPUB
- [x] Reproduce/fix hidden-pane blank EPUB using the supplied Thinking, Fast and Slow book; validate production rendering.
- [x] Share toolbar styling/order for PDF and EPUB, preserving format-specific sizing and reference behavior.
- [x] Add PDF embedded outlines and labelled heading detection from existing text/OCR, without automatically OCRing the whole book.
- [x] Verify 73 reader/document tests, TypeScript, real-book rendering/chapter navigation, responsive toolbar, production build and budgets.
- Exact book: 56 navigation options including the placeholder, valid cover image, Introduction navigation; hidden initial mount reproduces zero iframes before the fix and renders after it. Installed app's file endpoint returns identical bytes; ordinary installed-UI opening also works in an isolated profile. Original live-window state remains unverified.
- Synthetic PDF: detected chapter jumps to page 8; 430px EPUB/PDF controls fit, and references open below the wrapped bar. Private fixtures removed. Source changes only; running installed app unchanged.

## October 11 — fractional-width EPUB startup and local install
- [x] Reproduce blank pages using saved app layout and fractional-width reader (871.25px); zero iframes despite display resolving.
- [x] Wait for the first relocated page before enabling controls/resizing; retain timeout until that event. Early resize clears EPUB.js views before a location exists to restore.
- [x] Use −/+ and add ← current/total → navigation. EPUB's counter is the current chapter's reflowed pages; the chapter menu remains available.
- [x] Verify new lifecycle regression, existing 73 tests, exact-book rendering, production build/budgets and Electron compile.
- [x] Install locally at user request; signature/archive match, saved-layout exact-book rendering, and app-owned backend/direct+proxy health pass.

- [x] Restore EPUB References hover/focus opening, delayed close and click-to-pin; hover saved locations for previews, show pointer cursor, and keep editing pinned.
- [x] Float the reference panel above the book to avoid repagination on hover; lifecycle/hover regression passes.
- [x] Install the reference follow-up locally. Real Electron against installed assets and the saved layout confirms hover-open, pointer cursor, delayed close, pinning and unchanged book height; exact EPUB renders. Signature/archive equality and backend/proxy health pass. Temporary saved-profile copy removed.

## October 11 — EPUB reference parity and file drops
- [x] Forward file drag events from EPUB iframe documents into the existing FileOpener, preserving native DataTransfer objects and leaving text/reference drags untouched.
- [x] Reuse PDF tray/chip styles for EPUB References: + Line/+ Keep, editable lines, pill chips, hover/hold previews, dragging, Alt-arrow moves, rename/remove/open.
- [x] Verify 75 reader/document tests, production build/budgets, browser file drop through the iframe, line grouping and hover previews.
- [x] Installed and relaunched locally. Installed-asset Electron checks pass for pill styling, chip drag rearrangement, Alt-arrow moves, hover previews, and opening a file dropped inside the EPUB iframe. Signature/archive match and direct/proxy health pass; rollback retained.

- [x] Repeat local reinstall at user request; latest tested archive/signature and direct/proxy backend health verified after relaunch.

## October 11 — stable selection and whole-book page navigation
- [x] Move EPUB selection/OCR actions out of flow; remove the 700-character selection rejection and bottom-left section/page footer.
- [x] Measure whole-book pages with a separate offscreen EPUB rendition using the current viewport/font/layout; retain page CFIs for direct jumps and recalculate on size changes.
- [x] Share full-width page entry and a hover/focus page slider between PDF/EPUB. Preview while dragging and commit on release; leave the slider open during capture.
- [x] Verify 76 reader/document tests; exact-book 1,071-character selection and real mouse drag preserve page/viewport/CFI; page 194 jump is exact; font increase recalculates 324 to 375 pages. PDF 205 is fully visible and slider jumps to 100.
- [x] Verify final production package and install locally. Build/budgets, installed signature/archive equality and direct/proxy health pass. Installed exact-book check: 410 total pages at the app viewport, stable 1,071-character/native mouse selection, slider jump to 246, and font resize recalculates 469 pages. Rollback: `/Applications/.Diane-previous-8b02db16-a558-4d88-be07-949f78027956.app`.

## October 11 — EPUB keyboard and scrolling
- [x] Handle navigation keys in the toolbar as well as book iframes, preserving editing and selection-extension keys.
- [x] Add continuous vertical scrolling and retain single/two-page layouts with throttled wheel navigation.
- [x] Prioritize visible input over background pagination; yield between every measured page and prevent queued page-turn bursts.
- [x] Verify the Part 1 divider with native keyboard/wheel input, production build, and reinstall after this round. Installed checks pass for toolbar Right, native vertical scroll, horizontal wheel, single/two-page layout and stable two-page advancement (45→47). 54 reader tests, production budgets, signature/archive equality and direct/proxy health pass. Rollback: `/Applications/.Diane-previous-f7918e63-611d-4f91-9842-3c298cda68d7.app`.

## October 12 — page counter sizing
- [x] Keep the navigation group and total from shrinking; grow number entry by current/total digits and allow other toolbar controls to wrap.
- [x] Replace ambiguous loading ellipsis with Counting… and distinguish an unavailable count.
- [x] Verify existing navigation test and Chromium widths 320/430/1000 with loading and 3–7 digit totals; all labels fit without toolbar overflow.
- [x] Build and reinstall locally after verification. Production budgets, signature/archive equality and both health endpoints pass after restarting the stalled automatic launch; rollback retained at `/Applications/.Diane-previous-c68bb1df-3aa2-4f50-9a71-323f43e5c50b.app`.
