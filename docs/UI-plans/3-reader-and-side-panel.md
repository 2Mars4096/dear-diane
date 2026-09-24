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

## Decisions
- Reader is a surface (like Work / Notes), not an autonomy mode. Plan/Auto/Full access still govern what the sidecar agent may do.
- Ported later: OCR (in-browser instead of server), passage anchoring (client-side against page text), annotated-PDF export. Still not ported: teaching layers/modes and the tutor model picker; DAN answers through the configured lead. DAN reads PDFs directly from the project folder and answers through the configured lead.
