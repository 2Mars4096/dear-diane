# 14-1: Session / Conversation Memory

**Parent:** [14-memory-cross-run-state](14-memory-cross-run-state.md)
**Status:** not-started
**Goal:** Provide the smallest useful cross-run memory layer: durable session-linked conversation history and key-value state that survives multiple `Engine.run()` invocations.

## Existing Baseline

| Component | Location | What exists | Gap |
|---|---|---|---|
| `SharedContextStore` | `engine/context_runtime.py` | In-memory KV; `snapshot()`/`restore()` used by checkpoint flow; key validation against `graph.shared_context` | Run-scoped — fresh instance per `Engine.run()`, restored only by `Engine.resume()`. No cross-run load. |
| `LocalStateManager` | `engine/context_runtime.py` | Scopes keyed by `node_id`; used for while-gate `state_schema` loop state | Run-scoped; dies after run completes |
| `ArtifactStore` | `engine/context_runtime.py` | In-memory dict; `save()`/`load()` by ref; checkpointed | In-memory only; no persistent backend |
| `Engine.run()` | `engine/scheduler.py` | Creates fresh `ExecutionState`, `SharedContextStore`, `ArtifactStore`, `LocalStateManager` every invocation | No `session_id` parameter; no pre-loading of prior state |
| `Engine.resume()` | `engine/scheduler.py` | Loads state from `FileSystemCheckpointStore.load(run_id)` | Resume is run-scoped; no session continuity |
| `RunManager` | `server/run_manager.py` | In-memory `RunRecord` with `snapshot()`, event callback, subscriber fan-out | No disk persistence; `RunRecord` has no `session_id` |
| `ChatStore` | `server/chat_store.py` | Durable per-workflow thread persistence (`{base_dir}/chats/{workflow_id}/{thread_id}.json`); `save_checkpoint()` | Not tied to engine execution context; no session identity model |
| `POST /api/runs` | `server/app.py` | Accepts `graph_id`, `inputs`; starts `RunManager.start_run()` | No `session_id` parameter |
| `EngineConfig` | `engine/executor.py` | `checkpoint_enabled`, `checkpoint_dir`, provider settings | No session/memory config fields |
| Phase 8 (13-1) | `docs/plans/13-1-run-observability-history.md` | Planned `RunStore` (filesystem, per-workflow), `EventLog` (append-only JSONL) | Not yet implemented; 14-1 storage must coordinate or self-contain |

## Tasks

- [ ] 1. Define persistent memory contracts and identity model
  - [ ] 1-1. Define `session_id` contract: creation (auto on first run within a thread/workflow), reuse (same thread = same session), expiration (configurable TTL or explicit close). Bind to `workflow_id` + optional `thread_id`.
  - [ ] 1-2. Define memory namespace schema (`global`, `workflow`, `session`, optional `agent` scope) and key naming rules. Clarify relationship to existing `SharedContextStore` key namespace.
  - [ ] 1-3. Define memory entry model: `key`, `value`, `scope`, `created_at`, `updated_at`, `source_run_id`, `writer_node_id`, `write_mode`.
  - [ ] 1-4. Define mutation semantics (`set`, `append`, `merge`, `delete`) and conflict behavior (last-write-wins default, optional CAS for critical keys).

- [ ] 2. Build persistence layer (filesystem-first)
  - [ ] 2-1. Add `MemoryStore` protocol (async `read`, `write`, `delete`, `list_keys`, `list_sessions`) and `FileSystemMemoryStore` implementation. Storage layout: `memory/{workflow_id}/{session_id}/{key}.json`. Coordinate with 13-1 `RunStore` layout if available; self-contained otherwise.
  - [ ] 2-2. Add atomic write strategy (temp file + rename) and corruption safeguards (schema validation on read, graceful fallback for malformed entries).
  - [ ] 2-3. Add optional retention/cleanup policy via `DAN_MEMORY_RETENTION_DAYS` env var (default: keep-all). Cleanup runs on server startup.
  - [ ] 2-4. Add lightweight indexing: `_index.json` per session with key metadata for fast `list_keys`/`list_sessions` without reading all entries.

- [ ] 3. Integrate memory into engine runtime
  - [ ] 3-1. Add `session_id` parameter to `Engine.run()` and `EngineConfig`. On run start, pre-load selected memory keys from `MemoryStore` into `SharedContextStore` (read-only snapshot) so nodes can access prior state through existing context APIs.
  - [ ] 3-2. Add `MemoryWriteRequest` model and explicit write path: executors call `context.write_memory(key, value, mode)` → validated → queued for persistence. No implicit writes — all memory mutations go through this API.
  - [ ] 3-3. Persist memory deltas incrementally: write queued entries to `MemoryStore` at each checkpoint boundary (leveraging existing `_save_checkpoint` hook in `scheduler.py`). Finalize on `RUN_COMPLETED`/`RUN_FAILED`.
  - [ ] 3-4. Ensure `Engine.resume()` loads both checkpoint state and session memory. Deduplicate writes already persisted before the checkpoint.

- [ ] 4. Integrate with chat/session lifecycle and server APIs
  - [ ] 4-1. Map chat thread lifecycle to session memory: thread creation → new session (or reuse existing for same workflow), thread deletion → archive session memory (not delete).
  - [ ] 4-2. Extend `POST /api/runs` to accept optional `session_id`. Extend `RunManager.start_run()` to pass it through to `Engine`. Default: infer from active chat thread if present.
  - [ ] 4-3. Add REST endpoints: `GET /api/memory/{workflow_id}/{session_id}` (list keys), `GET /api/memory/{workflow_id}/{session_id}/{key}` (read entry), `DELETE /api/memory/{workflow_id}/{session_id}` (reset session).
  - [ ] 4-4. Add audit trail: memory entries include `source_chat_message_id` when written from chat-triggered runs, enabling traceability from memory back to conversation.

- [ ] 5. Validation, tests, and docs
  - [ ] 5-1. Unit tests: `FileSystemMemoryStore` CRUD, atomic writes, corruption recovery, retention cleanup, concurrent writes (file locking).
  - [ ] 5-2. Integration tests: cross-run continuity (`run1` writes key, `run2` reads it via pre-loaded context), thread/session reuse, resume consistency.
  - [ ] 5-3. Update `docs/architecture.md` §Context Scoping with session memory model, storage layout, and `MemoryStore` protocol.
  - [ ] 5-4. Update `docs/llm-api-guide.md` with new `session_id` run parameter and memory REST endpoints.
  - [ ] 5-5. Update `docs/changelog.md`, `docs/todo.md`, and this plan as implementation progresses.

## Primary Files

- `src/dan/engine/context_runtime.py` — extend `SharedContextStore` with memory pre-load; add `MemoryWriteRequest`
- `src/dan/engine/scheduler.py` — `Engine.run()` and `Engine.resume()` accept `session_id`; checkpoint hook writes memory deltas
- `src/dan/engine/executor.py` — `EngineConfig` gains `session_id` and memory config; `ExecutionContext` gains `write_memory()` API
- `src/dan/engine/checkpoint.py` — coordinate memory persistence with checkpoint lifecycle
- `src/dan/server/run_manager.py` — `start_run()` / `resume_run()` pass `session_id`
- `src/dan/server/chat_store.py` — thread↔session mapping
- `src/dan/server/app.py` — new memory REST endpoints; `POST /api/runs` gains `session_id`
- `tests/test_engine/` — cross-run memory tests
- `tests/test_server/` — memory API endpoint tests

## Decisions

- **Smallest useful first:** prioritize reliable persistence and explicit APIs over advanced retrieval. Memory is key-value; semantic search is 14-3's scope.
- **Session identity is explicit:** callers can pass or inspect `session_id`; auto-inference from thread is a convenience, not a hidden coupling.
- **Append/audit-friendly writes:** preserve provenance (`source_run_id`, `writer_node_id`, `source_chat_message_id`) for future debugging and reflection features.
- **Coordinate with 13-1, don't block on it:** `MemoryStore` is self-contained. If 13-1 `RunStore` lands first, storage layout aligns; if not, memory persistence stands alone.

## Notes

- This plan is the foundation for 14-3 long-chain memory; retrieval quality and compaction are out of scope here.
- Multi-user isolation is out of scope; remains a later product/system decision.
- The self-evolving orchestrator (backlog) Tier 1 needs this: persistent error memories + RAG retrieval into orchestrator prompts.
