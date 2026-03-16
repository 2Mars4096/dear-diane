# 36-7: Furnace End-to-End

**Parent:** [36-recipe-distillation-spec](36-recipe-distillation-spec.md)
**Status:** not-started
**Goal:** Wire the furnace distillation pipeline end-to-end: API layer connecting frontend to backend, user-facing session naming, recipe variants/branching, upgraded PDF reading, multi-source ingestion (notebooks, blogs, docs), and structural extraction.

## Context

Plans 36-1 through 36-5 built the backend contracts (models, session store, ingredient ledger, corpus memory, recipe compiler, workflow templates) and the frontend UI (FurnacePanel, DistillationTab, TrainingSection). The missing piece is the integration layer that makes the "Start Session" button actually run the five-pass furnace and stream results back.

### Design Decisions (from review)

- **Memory for user preferences**: PDF roots, note roots, notation conventions, and project rules are stored via the existing `MemoryKernel.store_preference()` mechanism. The concierge's domain reflector extracts these from chat. No separate persistence layer needed.
- **Recipe variants**: Same corpus, multiple named recipe branches with different paper subsets. Not just sequential semver.
- **Multi-source**: The furnace should handle papers (PDF), notebooks (Kaggle `.ipynb`), blogs, docs, and other online sources — not just academic PDFs.
- **Engine reuse**: All new workflows compose from existing builder DSL nodes. No new engine primitives.

### Confirmed Decisions

- **Config precedence for roots/rules**: use `workspace researchConfig` > confirmed `MemoryKernel` preferences > env defaults.
- **Progress transport**: use **SSE first** (simpler reconnect semantics). Only add WebSocket later if bi-directional control is needed.
- **Artifact persistence path**: store compiled artifacts under `~/.dan/furnace/artifacts/{session_id}/` and expose them via API.
- **Session start idempotency**: repeated `start` on a running session should be explicitly handled by the API (no-op or `409`), with locking to prevent double-start.

### Model Reconciliation Notes

Backend `FurnaceSession` and frontend `TrainingSession` have mismatched schemas:

| Field | Backend (`FurnaceSession`) | Frontend (`TrainingSession`) |
|-------|---------------------------|------------------------------|
| ID | `session_id` (auto hex) | `id` (string) |
| Name | *(missing — add)* | `name` |
| Topic | *(missing — add)* | `topic` |
| Status | `"active"\|"paused"\|"completed"\|"failed"` | `"idle"\|"running"\|"paused"\|"completed"\|"failed"` |
| Queue | `paper_queue: dict[str, PaperStatus]` | *(not present)* |
| Progress | `checkpoints`, `total_token_usage`, `total_cost_usd` | `processedPapers`, `extractedPatterns`, `extractedTerms` |

Resolution: add `name`, `topic`, `description` to backend model; map backend `"active"` → frontend `"running"`, add frontend-only `"idle"` for pre-start state; frontend store derives `processedPapers` from backend `paper_queue` counts.

### Source Queue Generalization

`FurnaceSession.paper_queue` and `PaperStatus` are paper-specific names. For multi-source ingestion (task 6), these should be generalized:
- `paper_queue` → `source_queue: dict[str, SourceStatus]`
- `PaperStatus` → `SourceStatus` (with backward compat alias)
- This is a model change that happens in task 6 but should be designed up front.

### Two UI Surfaces

`FurnacePanel` (center desk, full-page) and `DistillationTab` (right context drawer, compact) both cover setup/running/results. The intended relationship:
- **FurnacePanel** is the primary authoring surface — session creation, ingredient management, pipeline overview, training history.
- **DistillationTab** is a monitoring/results widget — shows live progress of the active session and distillation outputs (terminology, methods, questions, tensions, concept map). Not for session creation.
- Both read from the same `useResearchStore` state, updated by the same API/event bridge.

## Tasks

- [ ] 1. **API integration layer**
  - [ ] 1-1. REST endpoint: `POST /api/furnace/sessions` — creates `FurnaceSession` + `IngredientLedger`, accepts `name`, `topic`, `description`, `target_count`, returns session info
  - [ ] 1-2. REST endpoint: `POST /api/furnace/sessions/{id}/sources` — add papers/sources to session (by paper_id, PDF path, or URL)
  - [ ] 1-3. REST endpoint: `POST /api/furnace/sessions/{id}/start` — triggers the read→distill pipeline, begins execution
  - [ ] 1-4. REST endpoint: `POST /api/furnace/sessions/{id}/pause` / `resume` / `cancel`
  - [ ] 1-5. REST endpoint: `GET /api/furnace/sessions` — list sessions (filterable by corpus, recipe, status)
  - [ ] 1-6. REST endpoint: `GET /api/furnace/sessions/{id}` — session detail + progress + source queue status
  - [ ] 1-7. REST endpoint: `GET /api/furnace/sessions/{id}/recipe` — returns compiled `recipe.md` / `skill.md` from `RecipeCompiler`
  - [ ] 1-8. WebSocket or SSE progress stream: per-source status changes, phase transitions, extraction counts, cost running total
  - [ ] 1-9. Wire frontend `handleStartSession` (FurnacePanel) → `POST /sessions` + `POST /sessions/{id}/sources` + `POST /sessions/{id}/start`
  - [ ] 1-10. Wire frontend `DistillationTab` progress/results → `GET /sessions/{id}` + SSE stream
  - [ ] 1-11. Wire frontend "Export Recipe" button → `GET /sessions/{id}/recipe`
  - [ ] 1-12. Bridge backend progress events → `researchEventRouter` → `useResearchStore` updates
  - [ ] 1-13. Idempotency and lock semantics: prevent double-start and concurrent mutating operations on the same session
  - [ ] 1-14. Path/security guardrails for `POST /sessions/{id}/sources`: only allow configured roots and validated URLs
  - [ ] 1-15. Persist compiled artifacts (`recipe.md`, `skill.md`) to disk and return stable artifact references

- [ ] 2. **User-facing session naming & identity** *(do alongside task 1)*
  - [ ] 2-1. Add `name: str`, `topic: str`, `description: str` fields to `FurnaceSession` model
  - [ ] 2-2. Add `name` param to `FurnaceSessionStore.create_session()`
  - [ ] 2-3. Add `find_by_name(name)` to `FurnaceSessionStore`
  - [ ] 2-4. Frontend: session name input in FurnacePanel setup (pre-filled from topic, editable)
  - [ ] 2-5. Session list UI: show name, status, paper count, progress — sortable and searchable
  - [ ] 2-6. Chat-referenceable: concierge can look up sessions by name (e.g. "open my supply-chain distillation")
  - [ ] 2-7. Preference capture path: when user states roots/notation rules in chat, persist confirmed preferences to `MemoryKernel`
  - [ ] 2-8. Config hydration at session create: resolve roots/rules by precedence (`workspace` > `memory` > `env`) and snapshot into session metadata

- [ ] 3. **Recipe variants / branching** *(defer until first full run works)*
  - [ ] 3-1. `recipe_id` naming convention: `{domain}-pill-{N}` (e.g. `supply-chain-pill-10`, `supply-chain-pill-50-empirical`)
  - [ ] 3-2. Add `variant_label` field to `FurnaceSession` (defines the variant identity — which paper subset and why)
  - [ ] 3-3. Multiple sessions can share the same `corpus_id` but produce different recipes with different ingredient subsets
  - [ ] 3-4. `RecipeVersion` inherits `variant_label` from its session for display purposes
  - [ ] 3-5. Variant comparison view: side-by-side recipe outputs from different pill sizes
  - [ ] 3-6. Frontend: "Create Variant" action from existing session — fork with subset or superset of papers
  - [ ] 3-7. Ingredient ledger supports cross-variant queries (which papers are in pill-10 but not pill-20)
  - [ ] 3-8. Variant lineage fields: `parent_session_id`, `forked_from_version`, and diff summary on creation

- [ ] 4. **PDF reading upgrade**
  - [ ] 4-1. Hybrid reading strategy: text-mode first pass on all pages, detect pages needing vision, vision-mode second pass on flagged pages only
  - [ ] 4-2. Page classification heuristic: low text density, "Figure"/"Table" markers, equation indicators (`\begin{`, unicode math blocks)
  - [ ] 4-3. Equation-aware vision prompt: explicitly request LaTeX notation for equations (not natural language descriptions)
  - [ ] 4-4. Table-aware vision prompt: request markdown table format or structured JSON
  - [ ] 4-5. Add `mode="hybrid"` to `pdf_read` tool (runs text first, then vision on flagged pages, merges results)
  - [ ] 4-6. Evaluate `marker` / `docling` as a third `mode="structured"` option (cheaper at scale, native equation/table extraction)

- [ ] 5. **Structural extraction with per-section summaries**
  - [ ] 5-1. Add `structure` key to extraction output: `[{heading, level, one_sentence_summary}]`
  - [ ] 5-2. Update extraction prompt in `build_paper_acquisition_workflow` and `build_batch_distillation_workflow`
  - [ ] 5-3. Use structure in aggregate pass: detect dominant section patterns across papers in a field
  - [ ] 5-4. Store structure as `knowledge_kind = "source_metadata"` in corpus memory (compact — just headings + summaries)
  - [ ] 5-5. Token budget management: for long papers (40+ pages), chunk by section headers, extract per-chunk, merge
  - [ ] 5-6. Chunked extraction: split paper text by detected section boundaries, run extraction LLM per-chunk, combine into single paper-level output

- [ ] 6. **Multi-source ingestion**
  - [ ] 6-1. Generalize `paper_queue` → `source_queue`, `PaperStatus` → `SourceStatus` (keep backward compat aliases)
  - [ ] 6-2. Source-type-specific extraction prompts: different prompt templates for papers, notebooks, blogs, docs
  - [ ] 6-3. Notebook reader: `web_fetch` or browser automation to grab `.ipynb` content; parse code cells + markdown cells
  - [ ] 6-4. Notebook extraction focus: problem solved, libraries/methods, tricks/heuristics, pipeline structure, evaluation metric + score
  - [ ] 6-5. Blog/doc reader: `web_fetch` with HTML-to-markdown; extract key claims, methods, code patterns
  - [ ] 6-6. Source type auto-detection in normalize pass: classify by URL pattern, file extension, or content heuristics
  - [ ] 6-7. Knowledge kind weighting by source type: notebooks weight `method`/`dataset`/`measure` higher; papers weight `rhetorical_move`/`citation_norm` higher
  - [ ] 6-8. Frontend: source input in setup supports URLs (Kaggle, blog) alongside PDF paths and paper IDs
  - [ ] 6-9. Backward-compatible migration for persisted session files (`paper_queue` → `source_queue`, `PaperStatus` aliases)

- [ ] 7. **Read pipeline (ingestion → extraction bridge)**
  - [ ] 7-1. Build a `for_each` batch reader step that reads all sources in the queue before the 5-pass furnace starts
  - [ ] 7-2. For PDFs: call `pdf_read` (hybrid mode) per source, store extracted text in session artifacts
  - [ ] 7-3. For URLs: call `web_fetch` or browser tools per source, store extracted content
  - [ ] 7-4. Feed the collected texts as input to the normalize → extract → aggregate → infer → project pipeline
  - [ ] 7-5. Track per-source read status (`PENDING` → `INGESTED` → `EXTRACTED`) in the session store

- [ ] 8. **Quality & cost controls**
  - [ ] 8-1. Pre-run cost estimate: count sources × estimated pages × mode cost, show in setup UI before starting
  - [ ] 8-2. Budget ceiling: user-set max cost; pause session when `total_cost_usd` exceeds limit
  - [ ] 8-3. Dedup at extract time: lightweight exact-match term dedup before aggregation
  - [ ] 8-4. Incremental re-extraction: skip sources already extracted in a previous session (check `SourceStatus.EXTRACTED`)
  - [ ] 8-5. Citation graph extraction: extract reference lists, match against corpus, weight highly-cited-within-corpus sources higher in aggregation

- [ ] 9. **Testing, migration, and rollout gates**
  - [ ] 9-1. Backend unit tests: session API CRUD/start/pause/resume/cancel, source add validation, artifact export
  - [ ] 9-2. Backend integration tests: end-to-end single session run with mocked tools and streamed progress
  - [ ] 9-3. Migration tests: load old session/ledger JSON and verify aliases + forward save behavior
  - [ ] 9-4. Frontend tests: FurnacePanel start flow, DistillationTab live progress, export recipe, error/reconnect states
  - [ ] 9-5. Rollout guard: feature flag (`DAN_FURNACE_API_ENABLED`) with fallback to current local-only UI behavior

## Dependencies

- [36-1](36-1-recipe-training-lifecycle.md) through [36-5](36-5-paper-acquisition-workflow.md) — all completed
- [29-concierge-memory-evolvement](29-concierge-memory-evolvement.md) — memory kernel (for preference persistence)
- Existing server router infrastructure (`server/routers/`)

## Likely Files

### Backend (new or modified)
- `src/dan/server/routers/furnace.py` — new API router
- `src/dan/server/app.py` and/or `src/dan/server/startup.py` — register furnace router and feature flag wiring
- `src/dan/engine/recipe/workflows.py` — update extraction prompts, add batch reader step, hybrid PDF reading
- `src/dan/engine/recipe/models.py` — add `name`/`topic`/`description`/`variant_label` to `FurnaceSession`; generalize `PaperStatus` → `SourceStatus`
- `src/dan/engine/recipe/session_store.py` — add `find_by_name()`, name-based lookup
- `src/dan/engine/recipe/recipe_compiler.py` — no changes needed, already works
- `src/dan/tools/pdf_read.py` — add `mode="hybrid"`, page classification heuristic, equation/table-aware prompts

### Frontend (new or modified)
- `editor/src/components/modes/ResearchMode.tsx` — wire FurnacePanel to API, session name input
- `editor/src/components/research/DistillationTab.tsx` — wire to SSE stream for live progress, wire Export to API
- `editor/src/store/useResearchStore.ts` — add API call actions, map backend model to frontend model
- `editor/src/lib/researchEventRouter.ts` — handle furnace progress events from SSE
- `editor/src/lib/api.ts` — add typed furnace API client methods

## Execution Order

1. **Tasks 1 + 2** (API + naming) — first, because nothing works without the integration layer
2. **Task 7** (read pipeline) — second, because the distillation workflow needs source texts as input
3. **Tasks 4 + 5** (PDF upgrade + structure extraction) — third, improves quality of what gets read
4. **Task 8** (quality/cost) — fourth, operational controls
5. **Task 6** (multi-source) — fifth, extends beyond PDFs (with migration)
6. **Task 9** (tests + rollout) — sixth, enforce release gates before broad enablement
7. **Task 3** (variants) — last, needs at least one full run before variant branching is useful

## Notes

- Sub-plans will be created just-in-time when starting each task group.
- Tasks 1+2 are one unit — you can't create a useful session without a name.
- Task 7 fills a real gap: the existing `build_batch_distillation_workflow` takes `{input}` text but nothing reads the PDFs and feeds them in.
- The `PaperStatus` → `SourceStatus` rename (task 6-1) should be designed up front even if implemented later, to avoid double-migration.
- Release recommendation: ship behind a feature flag until task 9 test gates pass.
