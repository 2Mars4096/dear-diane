# 12-5-1: Global Chat History Discovery

**Parent:** [12-5-conversation-lifecycle](12-5-conversation-lifecycle.md)
**Status:** completed
**Goal:** Make DAN's full-screen chat history discoverable across workflows so persisted conversations do not appear to disappear after switching workflows or promoting `_scratch` into a named workflow.

## Tasks
- [x] 1. Add a backend thread-listing path that can return chat summaries across all workflows.
- [x] 2. Update full-screen `ChatPanel` history/search to use cross-workflow thread results.
- [x] 3. Make full-screen thread actions target the correct `(workflow_id, thread_id)` pair for select/pin/export/delete.
- [x] 4. Show workflow labels in cross-workflow history rows and search results so users can tell where a thread lives.
- [x] 5. Add focused regression coverage and re-run the editor build.

## Decisions
- Full-screen chat history is global across workflows; the compact/sidebar history remains scoped to the active workflow.
- Selecting a thread from another workflow switches the active workflow context before loading the thread so follow-up chat actions keep targeting the right graph.
- Cross-workflow history uses a flat list in full-screen mode instead of the per-workflow branch tree to avoid mixing lineage structures from unrelated workflows.

## Notes
- Former top-level plan `48-global-chat-history-discovery.md` was renumbered into this subplan on 2026-04-04 because this work is a follow-up on conversation lifecycle/history behavior, not a standalone new phase.
- Backend: `GET /api/chats` now returns chat thread summaries across all workflows via `ChatStore.list_all_threads()`.
- Frontend: full-screen `ChatPanel` now uses global history/search, renders workflow labels, and routes thread actions through explicit `{ workflowId, threadId }` targets.
- Validation: `pytest -q tests/test_server/test_chat_mode.py tests/test_server/test_chat_integration.py -k 'thread_create_list_get or thread_list_all_workflows or search_finds_messages or search_empty_query_returns_empty or lists_threads_across_workflows'` (`5 passed, 41 deselected`) and `cd editor && npm run build` (passed; Vite emitted the existing Node-version warning for Node `20.17.0` vs recommended `20.19+`).
