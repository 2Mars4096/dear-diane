# 25-13: Conversation Tool Access & Search Providers

**Status:** completed
**Goal:** Wire all 11 built-in tools as LLM-callable capability tools, upgrade web search providers, and fix the `<FunctionCall>` XML leak.

## Problem

25-12 gave the conversation path `web_search`, but: (1) file/system tools weren't available as capability tools — the LLM fabricates content instead of reading actual files, (2) `web_search` wasn't listed in `CAPABILITY_TOOLS_REFERENCE` causing misrouting, (3) DuckDuckGo scraping is unreliable, (4) `<FunctionCall>` XML leaks to messaging surfaces when the tool-calling API fails.

## Tasks

- [x] 1. Register all 11 built-in tools as capability tools
  - [x] 1-1. `handle_file_read` — resolves absolute paths via `_resolve_user_path()`, truncates at 100k chars
  - [x] 1-2. `handle_pdf_read` — resolves absolute paths, extracts metadata, truncates at 12k chars
  - [x] 1-3. `handle_list_directory` — absolute path support, glob filter, recursive, 200-entry cap
  - [x] 1-4. `handle_web_fetch` — fetch URL content as text
  - [x] 1-5. `handle_file_write` — write/append, agent mode only
  - [x] 1-6. `handle_shell_command` — run terminal commands, agent mode only
  - [x] 1-7. `handle_http_request` — REST API calls, agent mode only
  - [x] 1-8. `handle_text_chunk`, `handle_json_extract`, `handle_regex_match` — text processing, all modes
  - [x] 1-9. Read-only tools in all modes; write tools (file_write, shell_command, http_request) in agent/build/mutate only
- [x] 2. Add all tools to `CAPABILITY_TOOLS_REFERENCE` and the then-live conversation prompt path (later superseded by the unified prompt cleanup in `28-5`)
- [x] 3. Web search provider cascade: Tavily → Brave → DuckDuckGo
  - [x] 3-1. `_tavily_search()` via httpx POST to `api.tavily.com` (DAN_TAVILY_API_KEY)
  - [x] 3-2. `_brave_search()` via httpx GET to `api.search.brave.com` (DAN_BRAVE_API_KEY)
  - [x] 3-3. `_ddg_search()` as zero-config fallback (duckduckgo-search)
  - [x] 3-4. Auto-fallback cascade on failure, no new dependencies (uses httpx)
- [x] 4. Improve adapter `_classify_adapter_intent` — catch "summary/summarize/review" + "paper/document/pdf"
- [x] 5. Strip `<FunctionCall>` XML from `_consume_chat_stream_events` via `_strip_function_call_xml()` regex

## Files

| File | Action |
|---|---|
| `src/dan/server/capability_handlers.py` | Add `handle_file_read`, `handle_pdf_read`, register |
| `src/dan/server/chat_manager.py` | Add to `CAPABILITY_TOOLS_REFERENCE` |
| `src/dan/cli/adapter.py` | Improve `_classify_adapter_intent`, strip XML in `_consume_chat_stream_events` |

## Acceptance

- "Summarize this paper at ~/path/paper.pdf" → LLM calls `pdf_read`, returns real content summary
- "Read the file at ~/path/data.csv" → LLM calls `file_read`, returns real content
- `<FunctionCall>` XML never appears in WhatsApp/Telegram messages
