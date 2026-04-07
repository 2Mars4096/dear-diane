# 51-3: Memory And Evidence Lifecycle

**Parent:** [51-universal-worker-hardening-and-standalone-cell](51-universal-worker-hardening-and-standalone-cell.md)
**Status:** not-started
**Goal:** Give the worker a true evidence and memory lifecycle instead of a one-shot “fetch more context” helper surface.

## Tasks
- [ ] 1. Define the evidence lifecycle
  - [ ] 1-1. Distinguish catalog-level evidence refs from expanded full evidence payloads
  - [ ] 1-2. Preserve provenance, trust labels, and freshness across both forms
- [ ] 2. Separate memory layers
  - [ ] 2-1. Define working memory, episodic task memory, and longer-lived retained memory
  - [ ] 2-2. Clarify which layers are cell-local versus organism-level
- [ ] 3. Add write and compaction behavior
  - [ ] 3-1. Define when a worker can persist new observations or summaries
  - [ ] 3-2. Define compaction/rehydration rules so memory remains useful without context bloat
- [ ] 4. Align memory with continuation hooks
  - [ ] 4-1. Make resumed work prefer refs, summaries, and selectively rehydrated detail over rescanning raw history
- [ ] 5. Validate on representative single-cell tasks
  - [ ] 5-1. Prove that memory improves recoverability and continuation without uncontrolled context growth

## Decisions
- Memory should be layered and inspectable; not every retained fact belongs in the active prompt.
- Provenance and trust labels must survive compaction, not disappear during summarization.

## Notes
- This slice should stay focused on cell-level memory. Global shared memory and organism-wide state remain out of scope until `52-*`.
