# 54-18: DAN Reader Structure and Contract Hardening

**Parent:** [54-dan-control-plane-rewrite-around-universal-agents](54-dan-control-plane-rewrite-around-universal-agents.md)
**Status:** completed
**Goal:** Harden DAN Reader so the flat PDF pipeline preserves real section hierarchy, emits reusable section packets, strips extraction noise, redacts secrets, and exposes a shorter `read` alias without introducing a heavier organism shell.

## Tasks
- [x] 1. Fix reader-surface safety and entry-point ergonomics.
  - [x] 1-1. Stop serializing resolved API keys in JSON output.
  - [x] 1-2. Add `dan read` plus `dan-read` / `danread` aliases alongside the existing reader entry points.
- [x] 2. Preserve document structure on real PDFs.
  - [x] 2-1. Keep empty parent headings instead of dropping them when the body text lives under child sections.
  - [x] 2-2. Tolerate PDF spacing drift such as `IV .` while still avoiding sentence/table false positives.
  - [x] 2-3. Carry parent/child/path metadata through the section payload.
- [x] 3. Strengthen the reusable reader contract.
  - [x] 3-1. Normalize extracted text by stripping running download/header boilerplate and repairing common line-break hyphenation.
  - [x] 3-2. Enrich section summary packets with `span_id`, `section_path`, `quotes`, `claims`, `confidence`, and follow-up flags instead of only summary prose.
- [x] 4. Revalidate against synthetic and live PDF cases.
  - [x] 4-1. Add focused tests for hierarchy preservation, redaction, heading drift, and alias exposure.
  - [x] 4-2. Re-smoke-test the Lee 2021 PDF and confirm the recovered section tree.

## Decisions
- Keep the reader surface flat and stateless. The reusable seam is the richer section packet, not a new nested reader-specific organism.
- Use one section dataclass as the fat top-level contract instead of introducing separate fragment/result model layers just for the reader CLI.
- Bias heading recovery toward paper-style title-case structure while tolerating PDF spacing drift, because outline fidelity matters more than aggressively extracting every possible heading.

## Notes
- The Lee 2021 Journal of Finance PDF remains the live regression fixture for hierarchy preservation (`I` through `VII`, plus `A/B/C` children under the correct Roman parents).
- The later `54-19` follow-up keeps this hardened structure contract and adds the shared `pdf_read` hybrid/vision seam on top; `hybrid` remains the recommended mode for scholarly PDFs when you want vision assistance without losing the recovered outline.
