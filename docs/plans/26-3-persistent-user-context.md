# 26-3: Persistent User Context

**Parent:** [26-always-on-service](26-always-on-service.md)
**Status:** completed
**Goal:** Cross-session conversation memory — DAN remembers past requests, preferences, common patterns across chat sessions. Preference extraction (preferred models, output formats, domains). Quick-resume recent workflows on startup.

## Context

Phase 16 makes DAN remember across sessions. This plan adds cross-session memory and preference learning.

**Existing memory infrastructure:**
- `engine/experience.py`: `WorkflowExperience`, `ExperienceStore` (filesystem JSON), `ExperienceIndex` (RAG-based semantic search over past workflows). Has `save_experience()`, `search_similar()`.
- `engine/memory.py`: `MemoryEntry`, `MemoryScope` (global/local/pass_down/emit_up), `WriteMode`, `MemoryWriteRequest`.
- `engine/memory_store.py`: `MemoryStore` protocol, `FileSystemMemoryStore` (atomic JSON, index sidecar).
- `engine/memory_pipeline.py`: `ShortTermMemory` buffer, `CompactionStrategy`, `ConsolidationPipeline`.
- `engine/error_memory.py`: `ErrorMemoryIndex` (RAG for error records), `PrincipleStore`.
- `meta/discovery.py`: `DiscoveryService` — enumerates tools, skills, patterns, past workflows.
- `server/chat_store.py`: Filesystem-based chat persistence per-workflow threads.
- `server/chat_manager.py`: `compact_history()` for context window management, `MODEL_CONTEXT_WINDOWS` lookup.

**Current state:**
- Chat history: per-workflow threads in `ChatStore`. History is within a single workflow, not cross-session.
- Providers: `ProviderRegistry` routes model names to OpenAI/Anthropic/Google. Per-node model selection exists. `TierPolicy` auto-selects model tier per call.
- User config: env vars (`DAN_LLM_*`, `DAN_*`). No user profile model.

## Tasks

- [x] 1. **UserProfile model**
  - [x] 1-1. Create Pydantic model at `src/dan/engine/user_profile.py`. Fields: `user_id` (default `"local"`), `display_name`, `preferred_models` (dict mapping `task_type` → model name, e.g. `{"drafting": "claude-sonnet-4-6", "review": "gpt-4o"}`), `preferred_output_format` (markdown/json/latex), `common_domains` (list of strings like `"supply chain"`, `"equity research"`), `model_overrides` (per-workflow model preferences), `recent_workflows` (last 10 workflow IDs with timestamps), `session_count`, `created_at`, `updated_at`.
  - [x] 1-2. Filesystem persistence at `~/.dan/profile.json`. Load/save utilities (`load_user_profile()`, `save_user_profile()`). Use `DAN_DIR` from CLI for path resolution.

- [x] 2. **Preference extraction pipeline**
  - [x] 2-1. After each chat session (or periodically), scan conversation history for implicit preferences. Heuristics first: detect explicit model mentions (e.g. "use Claude for this"), domain keywords, repeated patterns.
  - [ ] 2-2. Optional LLM-based extraction: structured function call — "Extract user preferences from this conversation". Store deltas to `UserProfile`. *(deferred — heuristics-only for now)*
  - [x] 2-3. Idempotent — same conversation re-scanned produces same profile. Implement in `src/dan/engine/preference_extractor.py` or similar.

- [x] 3. **Cross-session conversation memory**
  - [x] 3-1. New `ConversationMemoryStore` at `~/.dan/conversation_memory/`. Not per-workflow — a global conversation log that captures cross-workflow summaries.
  - [x] 3-2. After each meaningful chat exchange, extract a 1–2 sentence summary and store with timestamp + `workflow_id` + topic tags.
  - [x] 3-3. On new session startup, retrieve the last N relevant summaries and inject into prompt context as a non-authoritative assistant context block ("Recent context") to avoid privilege escalation.
  - [x] 3-4. Keep retrieval separate from `ExperienceIndex` unless an adapter layer proves the schemas line up cleanly. Reuse provider/vector-store patterns where helpful, but do not overload workflow-experience records with conversation-summary data.

- [x] 4. **Preference-aware system prompt injection**
  - [x] 4-1. Modify `ChatManager` system prompt to include user preferences. Example: "The user prefers Claude for drafting and GPT-4o for review. They typically work on supply chain optimization workflows. Their preferred output format is LaTeX."
  - [x] 4-2. Only inject if profile is non-empty. Keep injection concise (<200 tokens).
  - [x] 4-3. Wire `UserProfile` into `ChatManager` constructor or via `build_local_chat_runtime()` / server lifespan.

- [x] 5. **Quick-resume on startup**
  - [x] 5-1. When `dan-chat` starts (or `dan up` drops into chat), show recent workflows: "Recent workflows: (1) equity-research [3h ago], (2) supply-chain-opt [2d ago]. Resume? [1/2/new]".
  - [x] 5-2. Pull from `UserProfile.recent_workflows` + `GraphStore.list()`. Merge and deduplicate by recency.
  - [x] 5-3. Update `recent_workflows` on every `/open`, `/new`, `/saveas`, or workflow switch. Append new entries, trim to last 10.

- [x] 6. **Preference suggestion prompts**
  - [x] 6-1. When the system detects a pattern (e.g. user always picks Claude for drafting), proactively suggest: "You usually use Claude for drafting — want to set that as default?"
  - [x] 6-2. Acceptance updates `UserProfile`. Trigger: after 3+ consistent choices for the same task type.
  - [x] 6-3. Light-touch — at most one suggestion per session. Track suggestion state in session to avoid repeat prompts.

- [x] 7. **Tests** (76 tests, all passing)
  - [x] 7-1. Unit tests for `UserProfile` CRUD: load/save, default values, merge deltas. (32 tests in `test_user_profile.py`)
  - [x] 7-2. Unit tests for preference extraction heuristics: model mentions, domain keywords, idempotency. (20 tests in `test_preference_extractor.py`)
  - [x] 7-3. Unit tests for `ConversationMemoryStore`: store summary, retrieve by relevance, deduplication. (24 tests in `test_conversation_memory.py`)
  - [x] 7-4. Unit tests for system prompt injection: empty profile → no injection; non-empty → concise block.
  - [x] 7-5. Unit tests for quick-resume flow: recent workflows display, `/open` updates profile.
  - [ ] 7-6. Mock LLM for extraction tests where needed. *(deferred — task 2-2 LLM extraction not yet implemented)*

## Files to Touch

| File | Changes |
|------|---------|
| `src/dan/engine/user_profile.py` | New: `UserProfile` Pydantic model, `load_user_profile()`, `save_user_profile()` |
| `src/dan/engine/preference_extractor.py` | New: heuristics + optional LLM extraction, idempotent delta application |
| `src/dan/engine/conversation_memory.py` | New: `ConversationMemoryStore`, summary extraction, relevance retrieval |
| `src/dan/server/chat_manager.py` | Inject user preferences and recent-context summaries into `_build_messages`; accept a profile/memory dependency instead of hard-coding file access |
| `src/dan/cli/chat.py` | Quick-resume prompt on startup; update `recent_workflows` on workflow switches; one-per-session preference suggestions |
| `src/dan/server/chat_factory.py` | Wire `UserProfile` into `build_local_chat_runtime()` (if exists from 26-1) |
| `src/dan/server/app.py` | Load `UserProfile` in lifespan; pass to `ChatManager` |
| `tests/test_engine/test_user_profile.py` | New: CRUD, load/save, merge |
| `tests/test_engine/test_preference_extractor.py` | New: heuristics, idempotency |
| `tests/test_engine/test_conversation_memory.py` | New: store, retrieve, relevance |
| `tests/test_server/test_chat_manager.py` | System prompt injection tests |
| `tests/test_cli/test_chat.py` | Resume-choice and recent-workflow profile update helper tests |

## Decisions

- `DAN_DIR` duplicated locally in each module (`user_profile.py`, `conversation_memory.py`) to avoid circular imports with `dan.cli`.
- LLM-based preference extraction (task 2-2) deferred; heuristic extraction covers the common cases.
- Integration pass completed: `ChatManager` now injects concise profile+memory context and records conversation summaries; both server lifespan and local chat factory now wire profile/memory dependencies.
- Quick-resume and preference suggestions are implemented in `dan-chat` with one-suggestion-per-session guard.
- Recent conversation memory is injected as an assistant-context message (not merged into system instruction text) to reduce cross-session prompt-injection risk.
- `format_recent_workflows()` is a standalone function (not a method) to keep `UserProfile` pure data.

## Notes

- Effort estimate: ~3 days.
- Depends on 26-1 (local mode needs profile too). Loosely depends on Phase 15 (chat capabilities to extract preferences from).
- `~/.dan/` path: use `Path.home() / ".dan"` or `DAN_DIR` from `src/dan/cli/__init__.py` for consistency.
- `recent_workflows` format: list of `{"workflow_id": str, "opened_at": float}`. Sort by `opened_at` desc, keep last 10.
- Preference extraction timing: can run on session end (when user exits chat) or periodically during long sessions. Defer to implementation.
- Keep auth scope explicit: Phase 16 still assumes a single local user (`user_id="local"`). Multi-user identity belongs to the existing user-system backlog item, not this plan.
