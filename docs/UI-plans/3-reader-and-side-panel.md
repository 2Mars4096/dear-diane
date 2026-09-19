# 3: Reader and unified side panel

**Status:** in-progress
**Goal:** Read project PDFs without interruption (select → ask → answer in the side panel), and consolidate the workbench's right-side tools into one tabbed side panel.

## Tasks
- [x] 1. Unified side panel
  - [x] 1-1. One header toggle replaces Sidecar chat / Files / Activity / Preview; Notes stays a separate surface switch.
  - [x] 1-2. Tabs: Chat · Files · Preview · Activity · Team; one open at a time, remembered per device.
  - [x] 1-3. Team strip and file selection open the matching tab.
  - [ ] 1-4. Phone layout still uses separate pages; decide whether tabs apply there.
- [x] 2. Reader (port of learning-assistant material guide PDF reader)
  - [x] 2-1. Port `interactive-pdf-viewer` (+ CSS module) and its libs (comments, highlight display, reference tags, PDF position) with DAN storage keys; pdfjs-dist 6.2.108 with a bundled worker.
  - [x] 2-2. Reader view in the main area for a project PDF, opened from Preview/Files; keeps its own scroll/zoom/position.
  - [x] 2-3. Ask sends the selection + page text to a per-PDF sidecar conversation; the reader never loses scroll or focus.
  - [x] 2-4. Comment saves a highlight with an optional note; comments list jumps back to the highlight.
- [x] 3. Validation: unit tests for ported libs, browser check with a real PDF (render, Ask staging, Comment/highlight, position restore, dark mode).
- [ ] 4. Send a real Ask and confirm the answer streams in the side chat without moving the reader.

## Decisions
- Reader is a surface (like Work / Notes), not an autonomy mode. Plan/Auto/Full access still govern what the sidecar agent may do.
- Not ported: material import/OCR pipeline, passage anchoring, teaching layers, annotated-PDF export, tutor model picker. DAN reads PDFs directly from the project folder and answers through the configured lead.
