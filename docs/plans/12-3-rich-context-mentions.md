# 12-3: Rich Context & Mentions

**Parent:** [12-cursor-parity-chat](12-cursor-parity-chat.md)
**Status:** in-progress
**Goal:** Extend the `@` mention system from graph-only references (nodes/workflows/subgraphs) to a rich context system covering files, code, docs, past chats, and web — with server-side resolution and token-budget-aware injection.

## Current State

- `MentionAutocomplete.tsx` supports three types: `node`, `workflow`, `subgraph`
- Mention parsing is client-side only (`mentionParser.ts`)
- Server receives raw text with `@Name` strings; no structured resolution
- No file, code, documentation, or external context injection

## Target State

| Mention Type | Syntax | Resolves To | Example |
|---|---|---|---|
| **Node** (existing) | `@NodeName` | Full node definition (type, ports, prompt, connections) | `@SectionWriter` |
| **Workflow** (existing) | `@WorkflowName` | Workflow summary (nodes, edges, entry/exit) | `@paper_writing` |
| **Subgraph** (existing) | `@SubgraphName` | Subgraph topology | `@review_loop` |
| **File** | `@file:path` | File contents (truncated to budget) | `@file:agents/writer.md` |
| **Code** | `@code:NodeName.prompt` | Specific code/prompt snippet | `@code:DataProc.tool_code` |
| **Docs** | `@docs:architecture` | Project documentation section | `@docs:llm-api-guide` |
| **Past Chat** | `@chat:thread-title` | Summarized past conversation | `@chat:build review loop` |
| **Web** | `@web:query` | Web search results (via built-in web tool) | `@web:tiktoken token counting` |

## Tasks

- [x] 1. Server-side mention resolution
  - [x] 1-1. Define two models: `MentionRef` (client payload: `{type, identifier}`) and `ResolvedMention` (server-only: `{type, identifier, resolved_content, token_count}`) — in `mention_resolver.py`
  - [x] 1-2. `ChatMessageRequest`: add `mentions: list[MentionRef]` field (client sends identifiers only; server resolves content)
  - [x] 1-3. `chat_manager.py`: resolve mentions server-side, inject into LLM context as structured blocks via `pack_context()`
  - [x] 1-4. Backward compat: if no structured mentions, fall back to current `compact_history()` handling
- [x] 2. @File mentions
  - [x] 2-1. Frontend: `@file:` trigger opens file autocomplete (fetches from `GET /api/files/list` — scans workspace with safe extensions)
  - [x] 2-2. Backend: `FileResolver` reads file content, truncates to token budget
  - [x] 2-3. Security: restrict to project directory; deny `.env`, credentials, `node_modules`, `__pycache__`, `.git`, `venv`
  - [x] 2-4. Large file handling: show first 100 + last 50 lines with "[truncated]" marker
- [x] 3. @Code mentions
  - [x] 3-1. Frontend: `@code:` trigger autocompletes to `NodeName.field` (via `GET /api/code-refs/{wf}`)
  - [x] 3-2. Backend: `CodeResolver` extracts specific field from node definition in graph
  - [x] 3-3. Code blocks rendered with syntax highlighting in the injected context — @code mention pills styled with monospace/dark bg in chat; tooltip uses hljs
- [x] 4. @Docs mentions
  - [x] 4-1. Backend: `DocsResolver` indexes `docs/*.md` and `README.md`
  - [x] 4-2. Frontend: `@docs:` trigger autocompletes doc names (via `GET /api/docs/list`)
  - [x] 4-3. Backend: `DocsResolver` returns first 200 lines (heading-level chunking deferred)
  - [ ] 4-4. Future: index external docs (LLM provider docs, library docs) — out of scope
- [x] 5. @Past Chat mentions
  - [x] 5-1. Frontend: `@chat:` trigger lists recent threads with titles
  - [x] 5-2. Backend: `ChatHistoryResolver` loads thread, creates extractive summary (user messages + first sentence of assistant responses), truncates to budget
  - [x] 5-3. Inject as "Context from mentions" section appended to system prompt
- [ ] 6. @Web mentions — **deferred** (complex, requires async network calls)
  - [ ] 6-1. Frontend: `@web:query` freeform text after trigger *(deferred — requires async network + loading UX)*
  - [ ] 6-2. Backend: invoke web search tool *(deferred — requires external search API dependency)*
  - [ ] 6-3. Inject top-N search result snippets as context *(deferred — depends on 6-2)*
  - [ ] 6-4. Show source URLs in chat message for attribution *(deferred — depends on 6-2)*
- [x] 7. Context budget management
  - [x] 7-1. `pack_context()` function with budget allocation: system prompt always kept, mentions get up to 35% of remaining, then history
  - [x] 7-2. Priority ordering: system prompt > graph summary > mentions > recent history > older history
  - [x] 7-3. `_build_mention_block()` truncates longest mentions first; `_fit_history()` handles history compaction
  - [ ] 7-4. Frontend: show budget breakdown in expandable panel — deferred (polish)
- [ ] 8. Autocomplete UX enhancements — **deferred** (polish)
  - [x] 8-1. Categorized dropdown: sections for Nodes, Files, Code, Docs, Chats (with category icons)
  - [x] 8-2. Category icons and keyboard navigation (arrow keys + Enter)
  - [x] 8-3. Fuzzy search within categories *(implemented in 20-2)*
  - [x] 8-4. Recently used mentions at top of list — localStorage-backed, "Recent" section above categories, deduped by type+identifier
  - [x] 8-5. Preview tooltip: show first few lines of resolved content on hover — 300ms debounce, positioned right/left of dropdown, type-specific content (node ports/desc, code with hljs, file path, etc.)

## Decisions

- Created standalone `src/dan/server/mention_resolver.py` to avoid bloating `chat_manager.py`
- Used `_estimate_tokens()` in mention_resolver.py (duplicated from chat_manager) to avoid circular imports
- Mention context injected as `## Context from mentions` section appended to system prompt
- Budget: mentions get up to 35% of remaining context after system prompt + user message
- Truncation strategy: longest mentions truncated first (cut in half), then dropped entirely if still over budget
- `@web:` deferred — introduces async latency and non-deterministic content; needs loading indicator and error handling
- Task 8 (autocomplete UX polish) partially done — categorized dropdown with icons works, fuzzy search and preview deferred

## Notes

- The `@web:` mention is the most complex — it introduces latency (network call) and non-deterministic content. Consider making it async with a loading indicator.
- Context budget management (task 7) is critical for all mention types. Without it, injecting file contents + docs + past chats will overflow the context window.
- Server-side resolution (task 1) is the architectural foundation. All other tasks depend on it.
