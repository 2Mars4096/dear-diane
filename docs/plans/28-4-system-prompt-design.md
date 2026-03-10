# 28-4: System Prompt Design

**Parent:** [28-llm-first-chat](28-llm-first-chat.md)
**Status:** completed
**Goal:** Design a unified system prompt that replaces 5 mode-specific prompts with one that guides the LLM to use tools correctly across all surfaces.

## Problem

Today there are 5 separate prompts: `ASK_PROMPT`, `CONVERSATION_PROMPT`, `PLAN_PROMPT`, `DEBUG_PROMPT`, `BUILD_FROM_INTENT_PROMPT` + `SYSTEM_PROMPT_TEMPLATE`. Each assumes a specific mode and tool subset. With one path and all tools, we need one prompt that:
- Describes ALL tools clearly (when to use, when NOT to use)
- Adapts to the surface (WhatsApp = concise, editor = verbose)
- Prevents fabrication (always tool-call for live data, always read files before summarizing)
- Handles workflow building AND conversation AND file ops in one prompt

## Tasks

- [x] 1. Design the unified prompt structure
  - [x] 1-1. **Identity block**: who you are, what you can do
  - [x] 1-2. **Tool catalog**: each tool with 1-line description + when-to-use + when-NOT-to-use
  - [x] 1-3. **Anti-fabrication rules**: "NEVER guess live data — call web_search", "NEVER summarize a file you haven't read — call pdf_read/file_read", "NEVER guess the date — call current_datetime"
  - [x] 1-4. **Surface adaptation**: injected based on surface type ("You're on WhatsApp. Keep replies to 1-3 sentences. No markdown formatting. No numbered option lists.")
  - [x] 1-5. **Context block**: project summary, task history, user preferences (dynamic, assembled by Context Assembly layer)
  - [x] 1-6. **Workflow context**: if a workflow is linked, include graph summary (existing `build_graph_summary`)
- [x] 2. Replace the 5 mode-specific prompts with the unified prompt
  - [x] 2-1. `_build_messages()` uses one template, parameterized by surface + context
  - [x] 2-2. Remove `ASK_PROMPT`, `CONVERSATION_PROMPT`, `PLAN_PROMPT`, `DEBUG_PROMPT` constants *(completed later in 28-5)*
  - [ ] 2-3. `BUILD_FROM_INTENT_PROMPT` merges into the unified prompt's workflow section (deferred to 28-5)
- [x] 3. Tool catalog optimization
  - [x] 3-1. Group tools by category with clear "use this, not that" guidance
  - [x] 3-2. Example: "For file operations: use `list_directory` to browse, `file_read` for text files, `pdf_read` for PDFs. Do NOT guess file contents."
  - [x] 3-3. Example: "For live data: ALWAYS call `web_search`. Do NOT answer from training data for prices, weather, scores, dates."
  - [x] 3-4. Keep the prompt under 2000 tokens — concise tool descriptions, not verbose (1719 chars)
- [ ] 4. A/B test prompt quality
  - [ ] 4-1. Curate 10 test messages spanning: file request, web search, conversation, workflow build, date question, send email
  - [ ] 4-2. Compare old multi-prompt vs new unified prompt on correct tool selection
  - [ ] 4-3. Iterate on wording until unified prompt matches or exceeds old prompts on all test cases

## Files

| File | Action |
|---|---|
| `src/dan/server/chat_manager.py` | Major — replace 5 prompts with 1, update `_build_messages()` |

## Decisions

- The temporary `DAN_LLM_FIRST_CHAT` fallback and old mode-specific prompt constants were removed later in `28-5`, leaving the unified prompt path as the only supported `_build_messages()` route.
- `surface` parameter threaded through `send_message()` → `send_message_with_tools()` → `_build_messages()` → handlers → `app.py`.
- 4 existing tests assert old-prompt content and fail under unified prompt (expected); 28-6 updates them.
- Prompt is 1719 characters (well under 2000-token target).

## Notes

- The prompt must work well with the models DAN supports (MiniMax-M2.5, Claude, GPT-4o, Gemini). Different models respond differently to tool instructions — test across providers.
- Surface hints are critical: WhatsApp users expect short replies; editor users expect detailed explanations. This must be in the system prompt, not in routing.
- **WhatsApp formatting**: use `*bold*` for headers (not markdown `**bold**`), `_italic_` for emphasis. No markdown code blocks. URLs on their own line. The prompt should tell the LLM about these formatting rules per surface.
- **Long responses**: the prompt should tell the LLM to organize long outputs into clearly separated sections so the Response Actions layer (28-3) can split at section boundaries for WhatsApp.
- **Tool use guidance must be strong**: "If the user mentions a file → call file_read/pdf_read. If the user asks for current data → call web_search. If the user asks for the date → call current_datetime. Do NOT answer from memory for anything that can be verified with a tool."
