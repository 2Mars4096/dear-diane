# 29-8: Practical Research Quality & Audit

**Parent:** [29-concierge-memory-evolvement](29-concierge-memory-evolvement.md)
**Status:** completed
**Goal:** Make DAN reliable on daily research/report tasks through the chat interface and persist enough end-to-end provenance to debug, evaluate, and improve those behaviors.

## Context

The current system already has the right raw ingredients:

- chat-level tools for web search/fetch, PDF reading, file access, and current date/time
- Phase 28 real-world scenario docs for literature review, equity research, and deep research
- Phase 29 stateful concierge/memory/reuse work that can carry goals across turns

But the acceptance layer is still too weak for daily use:

- success on practical research tasks is implied rather than enforced
- chat behavior is only partially auditable compared with workflow-run observability
- when a research answer is weak, it is too hard to reconstruct the exact prompt/tool/source chain that produced it
- gated-paper handoff behavior exists as a scenario expectation, but not yet as a durable, regression-tested contract

This sub-plan does **not** create a separate "research mode" or force everything through a workflow. It hardens the existing chat/concierge path so daily research requests work well and are explainable after the fact.

## Tasks

### 1. Practical daily-task acceptance flows
- [x] 1-1. Treat equity research as a first-class acceptance flow: multi-query search, fetched source pages, source-backed financial/news claims, structured report output
- [x] 1-2. Treat general deep research as a first-class acceptance flow: multiple search angles, breadth of synthesis, explicit source list, and graceful handling of inaccessible material
- [x] 1-3. Treat PDF literature summary as a first-class acceptance flow: `pdf_read` before summarization, grounded summary/review, and clear truncation/page-window behavior
- [x] 1-4. Treat literature-search-plus-review as a first-class acceptance flow: search online, fetch available metadata/content, read local PDFs when present, and ask the user for gated papers instead of fabricating

### 2. End-to-end chat audit model
- [x] 2-1. Define a durable `ChatAuditRecord` / equivalent model with stable IDs linking `surface_id`, `project_id`, `task_id`, `turn_id`, optional `workflow_id`, optional `run_id`, and optional memory item IDs
- [x] 2-2. Capture the normalized user request and the concierge/chat decision metadata for the turn (intent, mode, reuse decision, follow-up state if any)
- [x] 2-3. Persist the assembled prompt/message bundle used for the decisive LLM call, with secret redaction rather than dropping the content entirely
- [x] 2-4. Persist ordered tool activity for the turn: tool name, args, result summary, status, duration, and source artifacts (URLs, file paths, PDF paths)
- [x] 2-5. Persist the final assistant answer plus user-facing provenance metadata (which sources/files materially informed the answer)

### 3. Audit persistence and retrieval
- [x] 3-1. Add a filesystem-backed audit store under `~/.dan/audit/` (or adjacent DAN storage) with append-friendly, per-turn records
- [x] 3-2. Keep audit writes atomic and cheap enough for normal chat usage; failures must never break the user-facing response
- [x] 3-3. Add internal query helpers to load audit records by project/task/workflow/run/turn so tests and debugging code can reconstruct a conversation path
- [x] 3-4. Link audit records to existing `RunStore`, `ProjectStore`, and memory-kernel provenance instead of duplicating their full contents

### 4. Research-quality behavior hardening
- [x] 4-1. Strengthen chat behavior for research/report asks so the model performs multiple searches/fetches rather than one shallow tool call followed by synthesis
- [x] 4-2. Enforce sourced-data discipline for equity/deep-research flows: unsupported numeric or live claims must trigger either more tool use or an explicit limitation statement
- [x] 4-3. Enforce gated-paper handoff behavior: when high-value papers are found but not accessible, the assistant must ask the user for the PDF/path instead of pretending to have read it
- [x] 4-4. Standardize research outputs into durable section shapes (summary, findings/themes, risks/gaps, sources) so evaluation is less brittle

### 5. Scenario regression coverage
- [x] 5-1. Add or harden a chat-level regression scenario for equity research
- [x] 5-2. Add or harden a chat-level regression scenario for general deep research
- [x] 5-3. Add or harden a chat-level regression scenario for PDF literature summary
- [x] 5-4. Add or harden a chat-level regression scenario for literature search + gated-paper follow-up
- [x] 5-5. Add audit assertions for each scenario family so tests verify both behavior quality and provenance persistence

### 6. Phase 29 plan reconciliation
- [x] 6-1. Reconcile stale Phase 29 plan statuses/checklists with the code already landed for `29-1`, `29-2`, `29-3`, `29-4`, and `29-7`
- [x] 6-2. Update Phase 19 todo text so the daily research/report acceptance target is visible at the roadmap level
- [x] 6-3. Keep `docs/changelog.md` and parent plan notes in sync as implementation lands

## Decisions

- Reuse the existing chat/concierge path instead of introducing a separate research-only runtime.
- Keep the audit store append-only and link outward to runs/memory/projects rather than duplicating all existing persisted artifacts.
- Optimize for real task success first, but require enough raw provenance to debug weak outputs later.

## Notes

- [28-6](28-6-real-world-test-scenarios.md) already defines the right user-facing scenario family. This plan upgrades those expectations into Phase 29 acceptance criteria with durable auditability.
- This plan is intentionally cross-cutting: it depends on Phase 29 orchestration/reuse work, but it is the practical bar for whether that work helps with daily use.
- The audit record is primarily an internal/debug artifact. A cleaner user-facing provenance view can be layered on top later without changing the underlying persistence model.
