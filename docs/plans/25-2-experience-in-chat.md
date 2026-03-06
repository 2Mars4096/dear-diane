# 25-2: Experience in Chat

**Parent:** [25-chat-control-plane](25-chat-control-plane.md)
**Status:** completed
**Goal:** Surface ExperienceStore, ExperienceIndex, DiscoveryService, and run history as chat-accessible tools so users can query workflow history, find similar past work, and learn from past runs through natural language.

## Design

### Tool Schemas

| Tool | Subsystem | Purpose | Example User Questions |
|------|-----------|---------|------------------------|
| `search_workflow_history` | ExperienceIndex | Semantic search over past workflows | "Have we done a lit review before?" / "Show workflows similar to supply chain analysis" |
| `get_workflow_details` | ExperienceStore | Detailed info about a specific workflow | "Tell me more about workflow X" / "What's the success rate of the regression pipeline?" |
| `search_run_history` | RunStore | Filter/search past runs by workflow, status, date | "Show failed runs from last week" / "What runs completed for workflow Y?" |
| `get_learned_principles` | PrincipleStore | Query causal principles learned from failures | "What have we learned about LLM failures?" / "Show principles for this workflow" |
| `discover_capabilities` | DiscoveryService | What tools, skills, patterns, and workflows are available | "What can DAN do?" / "What patterns exist for RAG?" |

### Question → Tool Mapping

| User Intent | Tool(s) |
|-------------|---------|
| "Have we done X before?" | `search_workflow_history` |
| "Show similar workflows to Y" | `search_workflow_history` |
| "Tell me about workflow Z" | `get_workflow_details` |
| "What's the success rate of workflow Z?" | `get_workflow_details` |
| "Show failed runs" / "Recent runs" | `search_run_history` |
| "What have we learned from failures?" | `get_learned_principles` |
| "What tools/skills/patterns exist?" | `discover_capabilities` |

### Example Interactions

**User:** "Have we done a literature review workflow before?"

**LLM** → `search_workflow_history(query="literature review", top_k=5)`

**Tool result:** (formatted)
```
Found 2 similar workflows:
1. deep-research-equity-research (score: 0.89) — Workflow with 8 nodes: [research, summarize, ...]. Uses llm_operator, rag_operator. Success rate: 75% (3/4 runs).
2. lit-review-template (score: 0.72) — Chain of 4 LLM nodes. Success rate: 100% (2/2 runs).
```

**User:** "What have we learned about tool failures?"

**LLM** → `get_learned_principles(query="tool failure", workflow_id="")`

**Tool result:**
```
3 principles (global scope):
1. If tool_operator receives malformed JSON → validate input schema before calling; retry with corrected format (confidence: 85%, from workflow regression-pipeline)
2. If timeout in web_search → increase timeout to 30s; add retry with exponential backoff (confidence: 70%, from workflow deep-research)
```

---

## Tasks

### 1. Define experience/discovery tool schemas

- [x] 1-1. Add tool schemas to `ChatCapabilityRegistry` (or `capability_registry.py` from 25-1) for:
  - `search_workflow_history`: `query` (str, required), `top_k` (int, default 5)
  - `get_workflow_details`: `workflow_id` (str, required)
  - `search_run_history`: `workflow_id` (str, optional), `status` (str, optional: "completed"|"failed"|"cancelled"), `limit` (int, default 20), `offset` (int, default 0), `after` (float, optional, unix ts), `before` (float, optional)
  - `get_learned_principles`: `query` (str, optional, for text filter), `workflow_id` (str, optional, scope to workflow or global), `min_confidence` (float, default 0.3), `limit` (int, default 10)
  - `discover_capabilities`: `query` (str, required), `top_k` (int, default 5)
- [x] 1-2. Use JSON Schema format consistent with `MUTATION_TOOL_SCHEMA` in `chat_manager.py` (lines 95–268).
- [x] 1-3. Ensure descriptions guide the LLM: e.g. "Use when user asks about past workflows, similar experiences, or 'have we done X before?'".

### 2. Implement tool handlers

- [x] 2-1. In `capability_handlers.py` (from 25-1), add handlers for each tool. Each wraps existing subsystem methods; no new business logic.
- [x] 2-2. `search_workflow_history`: Call `ExperienceIndex.search_similar(query, top_k)`. For each `(workflow_id, score)`, optionally load `ExperienceStore.load_experience(workflow_id)` to enrich with name, description, success_rate. If index is unavailable, degrade to lexical fallback (`ExperienceStore.list_experiences()` + simple substring/rank heuristics) instead of hard failure.
- [x] 2-3. `get_workflow_details`: Call `ExperienceStore.load_experience(workflow_id)`. If not found, return "No experience recorded for this workflow." Format `WorkflowExperience` fields for display.
- [x] 2-4. `search_run_history`: Call `RunStore.list_summaries(workflow_id, status=status, after=after, before=before, limit=limit, offset=offset)`. RunStore is in `app.py` as `_run_store`; pass via `CapabilityContext`. Format each summary for chat.
- [x] 2-5. `get_learned_principles`: Call `PrincipleStore.load_principles(workflow_id or "_global", scope="global" if not workflow_id else "workflow")`. If `query` provided, filter results by substring match on `condition`, `action`, `reason`. Limit to `limit` results, sort by confidence desc.
- [x] 2-6. `discover_capabilities`: Call `DiscoveryService.discover_all(query, top_k)`. Format `DiscoveryResult` (tools, skills, patterns, workflows, self_knowledge_formatted) for chat display.
- [x] 2-7. All handlers are async; `ExperienceIndex.search_similar` and `DiscoveryService.discover_all` perform embedding calls — ensure handlers await correctly.

### 3. Rich result formatting

- [x] 3-1. Create `src/dan/server/experience_formatters.py` (or add to `capability_handlers.py`) with formatting helpers.
- [x] 3-2. **Workflow summaries** (search_workflow_history, get_workflow_details): Include `name`, `description` (truncated to ~200 chars), `success_rate` (success_count/run_count), `last_run_at` (human-readable), `node_types_used`, `tools_used`, `failure_patterns` (top 2–3), `success_patterns` (top 2–3).
- [x] 3-3. **Run history** (search_run_history): Per run: `run_id`, `workflow_id`, `status`, `elapsed_seconds`, `total_cost`, `total_tokens`, `error` (if failed). Compact table or bullet list; truncate long errors.
- [x] 3-4. **Principles** (get_learned_principles): Per principle: `condition` → `action` (confidence: X%), source `workflow_id`. Keep each entry to 1–2 lines.
- [x] 3-5. **Discovery** (discover_capabilities): Sections: Tools (id + description), Skills (name + description), Patterns (name + description), Workflows (WorkflowMatch: name, score, success_rate). Omit or truncate `self_knowledge_formatted` if very long (token budget).

### 4. Cross-surface consistency

- [x] 4-1. Tool output is plain text or structured JSON (no HTML, no CLI-specific escape codes). Surfaces (dan-chat, editor ChatPanel, Telegram) can render as-is or with minimal adaptation.
- [x] 4-2. For messaging adapters (Telegram, WhatsApp): add optional `compact=True` to formatters when message length exceeds ~1000 chars — return abbreviated version (e.g. top 3 results instead of 5).
- [x] 4-3. Document user-facing behavior in `docs/cli.md` and `docs/architecture.md`; `docs/llm-api-guide.md` is not the right surface for chat capability docs unless Phase 15 later exposes them as public graph/tool APIs.

### 5. System prompt enrichment

- [x] 5-1. Extend the "Available tools" section (from 25-1) with experience tool descriptions.
- [x] 5-2. Add 2–3 example experience queries to the system prompt: "When the user asks 'have we done X before?', use search_workflow_history. When they ask 'what have we learned from failures?', use get_learned_principles."
- [x] 5-3. Ensure mode-aware: experience tools are available in all modes, including `ask`, because they are read-only and are a core part of conversational Q&A.

### 6. Integration with ChatManager flow

- [x] 6-1. Register experience tools in `ChatCapabilityRegistry` (25-1). Handlers receive `CapabilityContext` with: `experience_store`, `experience_index`, `discovery_service`, `run_store`, `principle_store`.
- [x] 6-2. Wire dependencies in `app.py`: ensure `_get_experience_store()`, `_get_experience_index()`, `_get_discovery_service()`, `_run_store`, `_get_principle_store()` are passed to the registry or `CapabilityContext`.
- [x] 6-3. Handle async results: `ExperienceIndex.search_similar` and `DiscoveryService.discover_all` are async; handlers must `await`. Emit `ChatToolCallStartEvent` before handler, `ChatToolCallResultEvent` after (reuse 25-1 pattern).
- [x] 6-4. Graceful degradation: if ExperienceIndex is None (embedding not configured), fall back to lexical search over stored experiences and clearly label confidence as heuristic; if DiscoveryService is unavailable, return partial results from direct stores.

### 7. Tests

- [x] 7-1. Unit test `search_workflow_history` with mocked `ExperienceIndex.search_similar` and `ExperienceStore.load_experience` — assert output format.
- [x] 7-1b. Unit test fallback path when `ExperienceIndex` is unavailable — ensure lexical fallback returns deterministic ranked results.
- [x] 7-2. Unit test `get_workflow_details` with existing and missing workflow_id.
- [x] 7-3. Unit test `search_run_history` with mocked `RunStore.list_summaries` — assert filtering and formatting.
- [x] 7-4. Unit test `get_learned_principles` with mocked `PrincipleStore.load_principles` — assert query filter and confidence ordering.
- [x] 7-5. Unit test `discover_capabilities` with mocked `DiscoveryService.discover_all`.
- [x] 7-6. Integration test: send chat message "Have we done a literature review?" — assert `search_workflow_history` is called and result is streamed.
- [x] 7-7. Integration test: send "What have we learned about tool failures?" — assert `get_learned_principles` is called.

---

## Files to Touch

| File | Action |
|------|--------|
| `src/dan/server/capability_registry.py` | Modify — add experience tool schemas (or create if 25-1 not done) |
| `src/dan/server/capability_handlers.py` | Modify — add 5 experience handlers |
| `src/dan/server/experience_formatters.py` | Create — optional; or inline in handlers |
| `src/dan/server/chat_manager.py` | Modify — system prompt enrichment (if not in 25-1) |
| `src/dan/server/app.py` | Modify — wire ExperienceStore, ExperienceIndex, DiscoveryService, RunStore, PrincipleStore into CapabilityContext |
| `tests/test_server/test_capability_handlers.py` | Create/Modify — unit tests for experience handlers |
| `tests/test_server/test_chat_experience.py` | Create — integration tests |
| `docs/cli.md` | Update — document new chat-visible history/discovery commands/behaviors if surfaced in `dan-chat` |
| `docs/architecture.md` | Update — mention experience tools in chat flow |

---

## Decisions

- **PrincipleStore scope:** `get_learned_principles` with no `workflow_id` uses `scope="global"` to include principles from all workflows. With `workflow_id`, scope to that workflow only.
- **PrincipleStore search:** No semantic search today. Use `load_principles` + client-side substring filter on `condition`/`action`/`reason` when `query` is provided. Future: add `search_principles` if needed.
- **RunStore vs RunManager:** `search_run_history` uses `RunStore.list_summaries` for rich filtering (status, date range, workflow_id). `RunManager.list_runs` is in-memory only; RunStore has persisted history. App has both; pass RunStore to context.
- **Experience tools in ask mode:** allow them in `ask` mode because they are read-only and directly support factual Q&A such as "have we done X before?" and "what patterns exist for Y?".

---

## Notes

- `ExperienceIndex.search_similar` requires embedding provider; if `DAN_EXPERIENCE_STORE_BACKEND` or RAG is not configured, the index may be None. Handlers must check and return a friendly message.
- `DiscoveryService.discover_all` is async and calls `discover_workflows` which uses `ExperienceIndex`. Same dependency chain.
- `PrincipleStore` is workflow-scoped in storage but supports `scope="global"` in `load_principles` by scanning all workflow dirs. May be slow with many workflows; consider caching or limiting scan in future.
- Effort estimate: ~2 days.
