# 10-1: Chat Panel & Backend API

**Parent:** [10-chatbox-nl-workflow](10-chatbox-nl-workflow.md)
**Status:** not-started
**Goal:** Add a chat panel to the visual editor and a backend message endpoint. Users type natural language, the LLM responds with graph-aware answers, and responses stream token-by-token. This is the foundation that all other chatbox sub-plans build on.

## Tasks

- [ ] 1. Chat panel React component
  - [ ] 1-1. `ChatPanel.tsx`: message list (scrollable, auto-scroll-to-bottom), input box (auto-resize textarea), send button, keyboard shortcut (Enter to send, Shift+Enter for newline)
  - [ ] 1-2. `ChatMessage.tsx`: renders user messages (right-aligned, blue) and assistant messages (left-aligned, gray). Markdown rendering for assistant messages (code blocks, inline code, lists, bold/italic) with HTML sanitization / `skipHtml` policy to avoid script injection. Timestamp display.
  - [ ] 1-3. Loading indicator: animated dots or spinner while LLM is generating. Streaming text appears incrementally as tokens arrive.
  - [ ] 1-4. Empty state: welcome message with example prompts ("Add a reviewer node after the writer", "Connect the output of @Planner to @Drafter", "Create a 3-node research pipeline")
  - [ ] 1-5. Error state: if LLM call fails, show error inline in the chat with a retry button

- [ ] 2. Editor layout integration
  - [ ] 2-1. Add chat panel as a resizable pane in `App.tsx`. Position: right side, stacked with or tabbed alongside ConfigPanel. Toggle visibility with a toolbar button or keyboard shortcut (Cmd+Shift+L or similar).
  - [ ] 2-2. Zustand state: `chatOpen: boolean`, `toggleChat()`, `chatMessages: ChatMessage[]`, `chatStreaming: boolean`, `sendMessage(text: string)` action
  - [ ] 2-3. Chat panel respects active tab context — each tab can have its own message thread (store `chatMessages` in `TabSnapshot`)
  - [ ] 2-4. Responsive: chat panel collapses to an icon button on narrow viewports

- [ ] 3. Backend chat endpoint
  - [ ] 3-1. `POST /api/chat/message` — accepts `{ workflow_id: string, thread_id: string | null, message: string, history: ChatMessage[], client_graph_revision: string | null }`. Returns `{ message_id, stream_channel_id }`; tokens stream over WebSocket.
  - [ ] 3-2. `ChatManager` class in `src/dan/server/chat_manager.py`: orchestrates LLM call with graph-aware system prompt, manages token streaming, handles errors
  - [ ] 3-3. Server-authoritative graph context serialization: backend loads graph by `workflow_id` from `GraphStore`, builds `GraphSummary` (node list, edge list, metadata), and serializes it into the system prompt. Client-supplied graph context is ignored.
  - [ ] 3-4. System prompt template: instructs the LLM about DAN's node types, edge types, graph structure, and available operations. Includes server-built graph summary. Tells the LLM it can suggest graph modifications (but 10-3 implements actual mutation).
  - [ ] 3-5. Wire endpoint in `app.py` alongside existing routes
  - [ ] 3-6. Revision mismatch handling: if `client_graph_revision` differs from current server graph revision, include a warning in response metadata so frontend can prompt refresh/retry before apply

- [ ] 4. LLM provider integration
  - [ ] 4-1. Chat uses `ProviderRegistry` from existing multi-provider infrastructure. Model configurable via `DAN_CHAT_MODEL` env var (default: engine's `llm_default_model`).
  - [ ] 4-2. Streaming: use provider's streaming API (same pattern as `LLMExecutor` streaming path). Emit token chunks to chat WebSocket events.
  - [ ] 4-3. Token usage tracking: count prompt + completion tokens per message. Surface in chat UI (subtle token count badge per assistant message, like the run summary bar).

- [ ] 5. WebSocket streaming protocol
  - [ ] 5-1. Extend existing WebSocket infrastructure with chat event types: `chat_token` (incremental text), `chat_complete` (full message + token usage + revision metadata), `chat_error`
  - [ ] 5-2. Frontend: `sendMessage` action subscribes to the chat stream channel and accumulates tokens into the in-flight assistant message
  - [ ] 5-3. Connection strategy: either multiplex chat events onto existing `/api/runs/{run_id}/events`-style channel or provide dedicated `/api/chat/{channel_id}/events` endpoint; pick one and document

- [ ] 6. Basic graph-aware responses
  - [ ] 6-1. The LLM should be able to answer questions about the current graph: "How many nodes are there?", "What does the Planner node do?", "What's connected to the Reviewer?"
  - [ ] 6-2. System prompt includes: full node type catalog (from `NODE_TYPE_CATALOG`), available templates, current graph summary, and conversation context
  - [ ] 6-3. For now, responses are text-only (no graph mutations). The LLM can suggest changes in natural language ("I'd recommend adding a Code node after the Writer to format the output"). Actual mutation comes in 10-3.

- [ ] 7. Tests
  - [ ] 7-1. `ChatManager` unit tests: system prompt generation, graph context serialization, token counting
  - [ ] 7-2. Chat endpoint integration test: send message via httpx test client, verify streaming response
  - [ ] 7-3. Graph context serialization: test compact vs. full serialization, token budget enforcement
  - [ ] 7-4. Frontend: verify ChatPanel renders messages, handles streaming, shows loading/error states (component test or manual verification)

- [ ] 8. Docs sync
  - [ ] 8-1. `architecture.md`: add ChatPanel to UI layout diagram, add chat_manager.py to server section, add chat endpoint to API table
  - [ ] 8-2. `changelog.md`: implementation entry

## Decisions

- Streaming transport is WebSocket-only (no SSE path).
- Graph context is server-authoritative; client does not provide canonical graph structure.
- Rollback pointers are session-scoped and handled in frontend state (cross-reload rollback out of scope).

## Notes

- The chat endpoint is stateless for inference — history is passed with each request. Server-side thread persistence comes in 10-5.
- Graph context serialization is crucial. Too little context and the LLM can't reason about the graph. Too much and we blow the context window. The ~4000 token budget is a starting heuristic.
- The system prompt is the most important piece. It needs to teach the LLM about DAN's node/edge model concisely. Consider including the `llm-api-guide.md` content (or a compressed version) in the system prompt.
- Streaming UX should feel as responsive as ChatGPT/Claude — first token within 1-2s, smooth incremental rendering.
