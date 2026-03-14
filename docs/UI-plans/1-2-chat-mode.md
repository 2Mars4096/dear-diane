# 1-2: Chat Mode (Default Workspace)

**Parent:** [1-ui-spec](1-ui-spec.md)
**Status:** in-progress
**Goal:** Transform the existing chat panel into a full-screen default workspace with rich output rendering, two-layer workspace navigation, and progressive escalation to specialized modes.

## Context

The existing editor has a chat panel (`ChatPanel.tsx`, `ChatMessage.tsx`, etc.) with streaming, tool calls, @mentions, and slash commands. This plan promotes it to a full-screen mode and adds rich output rendering so simple tasks feel as polished as ChatGPT/Claude while enabling seamless escalation to specialized modes when tasks become complex.

Chat mode is the entry point for all users. It should have zero learning curve.

## Layout

```
┌─ Workspace Tabs ──────────────────────────────────────────────────┐
│ [Supply Chain Paper] [DAN Dev] [Kaggle Comp] [+]                  │
├─ Open Thread Tabs ────────────────────────────────────────────────┤
│ [Supply chain Q&A] [Factor model v2] [Quick question] [+]         │
├──────────────────────┬────────────────────────────────────────────┤
│ Thread List          │ Conversation                               │
│ (collapsible)        │                                            │
│                      │  User: Compare Q1 revenue across           │
│ 🔍 Search threads   │        our portfolio companies             │
│                      │                                            │
│ 📌 Pinned           │  DAN: [Working: 3 steps]                   │
│ ▸ Supply chain Q&A   │       ┌──────────────────────────┐         │
│ ▸ Factor model v2   │       │ Company │ Q1 Rev │ YoY % │         │
│                      │       │ AAPL    │ $94.8B │ +5.2% │         │
│ Today               │       │ MSFT    │ $61.9B │ +13%  │         │
│ ▸ Quick question    │       └──────────────────────────┘         │
│ ▸ Debug middleware   │       AAPL shows slowest growth...         │
│                      │                                            │
│ Yesterday           │       [Chart] [Export] [Expand]            │
│ ▸ Lit review        │                                            │
│ ▸ Paper feedback    ├────────────────────────────────────────────│
│                      │ > Drill deeper into AAPL segments          │
└──────────────────────┴────────────────────────────────────────────┘
```

## Mode Behavior Clarification

The backend behavioral modes (`ask` / internal `conversation` / `agent` / `plan` / `debug`) are **not** shown in the mode bar. They are:
- **Auto-detected** from message content by `detect_chat_mode()` — questions → ask, live-data/tool-enabled chat → conversation, errors → debug, etc.
- **Overridable** via slash commands: `/ask`, `/agent`, `/plan`, `/debug`
- **Persisted** per thread in `ChatStore` metadata
- **Indicated** as a subtle badge on the thread or input area (not a primary navigation element)

`conversation` remains an internal runtime profile for tool-enabled chat; it should not become a separate top-level user mode.

The mode bar at the top shows **layout modes** (Chat/Code/Research/etc.) which control panel arrangement. Chat mode's layout is always the full-screen conversation view regardless of which behavioral mode the backend uses.

## Tasks

### 1. Full-Screen Chat Layout
- [x] 1-1. Full-width conversation view when Chat mode is active
- [x] 1-2. Thread list sidebar (left, collapsible): conversation history grouped by date
- [x] 1-3. Message bubbles: user messages (right-aligned or subtle), assistant messages (left, full-width)
- [x] 1-4. Chat input at bottom: slash commands, @mentions, file drag-and-drop (migrate existing features)
- [ ] 1-5. Optional context panel (right, toggle with Cmd+I): active workspace/project info, recent memory, relevant files
- [x] 1-6. Empty state: welcome message with suggested quick-starts ("What can I help with?")

### 2. Workspace Navigation (Two-Layer Tabs)
- [ ] 2-1. **Workspace tab bar** (top): horizontal tabs for workspaces, each mapped to a `ProjectStore` project
- [ ] 2-2. **New workspace**: `+` button or Cmd+Shift+N creates a new workspace (with optional name + pinned paths)
- [ ] 2-3. **Workspace switching**: click tab or Cmd+Option+Left/Right cycles workspaces. Atomic swap: sidebar + threads + file context change together in < 200ms.
- [ ] 2-4. **Workspace indicators**: color-coded or icon-badged tabs for visual distinction. Active workspace highlighted.
- [ ] 2-5. **Open thread tab strip**: lightweight inner tabs for currently open/recent threads. Fast switching without reopening from history.
- [ ] 2-6. **Thread list scoped to active workspace**: the left sidebar remains the full history/archive for the active workspace
- [ ] 2-7. **Workspace context bar**: below workspace tabs, shows active workspace name + pinned paths summary + active task
- [ ] 2-8. **Workspace tab management**: right-click menu for rename, close, pin, color, duplicate
- [ ] 2-9. **Workspace persistence**: workspace list and active workspace saved to localStorage + synced to `ProjectStore`

### 3. Rich Output Rendering
- [x] 3-1. **Tables:** Markdown pipe tables rendered as styled HTML tables with zebra-stripe hover
- [ ] 3-2. **Charts:** Inline chart rendering from tool output using Chart.js or similar
- [x] 3-3. **Code blocks:** Syntax highlighting (highlight.js), copy button, language badge
- [ ] 3-4. **File cards:** Downloadable attachments with type icon, size, preview thumbnail
- [ ] 3-5. **Diff view:** Inline code diffs with accept/reject buttons for proposed code changes
- [ ] 3-6. **Progress cards:** Multi-step task progress with phase indicators, elapsed time
- [ ] 3-7. **Structured responses:** Collapsible sections, tabbed output (Summary | Details | Sources)
- [ ] 3-8. **Image display:** Inline rendering of generated/fetched images with lightbox on click
- [ ] 3-9. **Citation cards:** Author/year/title with link, hover for abstract

### 4. Progressive Escalation
- [x] 4-1. **Mode suggestion banner:** When message matches a specialized layout pattern, show "Switch to Code mode?" with one-click accept
- [x] 4-2. **Auto-detect triggers:** keyword-based detection per layout mode
- [ ] 4-3. **Inline workspace preview:** Miniature preview of what the specialized mode would look like
- [ ] 4-4. **User preference:** Per-trigger "Always switch" / "Always ask" / "Never switch"
- [x] 4-5. **Graceful degradation:** If user declines, chat continues normally

### 5. Conversation Management
- [x] 5-1. New conversation button + Cmd+N
- [ ] 5-2. Conversation search: full-text across all threads in active workspace
- [ ] 5-3. Pin/star conversations for quick access
- [ ] 5-4. Export conversation: Markdown, PDF, JSON
- [ ] 5-5. Conversation context carries to other modes: switch to Code → same conversation in chat sidebar
- [ ] 5-6. Thread metadata: creation date, message count, last active, associated task
- [ ] 5-7. Delete / archive conversations
- [x] 5-8. Background streaming: sessions keep running when switching threads
- [x] 5-9. Message queue: type follow-ups while streaming, auto-send on completion
- [x] 5-10. Graceful shutdown: close all background streams and flush pending saves
- [x] 5-11. Mid-session injection: push queued message into active stream's tool loop

### 6. Quick Actions & Intelligence
- [ ] 6-1. **Suggested follow-ups:** After a response, show 2-3 contextual next steps
- [ ] 6-2. **Recent commands bar:** Horizontally scrollable pills for most-used slash commands
- [ ] 6-3. **Drag-and-drop zone:** Visual indicator when dragging files over the chat
- [ ] 6-4. **Voice input button:** Integrates with existing transcription (Whisper)
- [ ] 6-5. **Smart paste:** URL → auto-fetch; image → auto-describe; code → auto-detect language
- [ ] 6-6. **Typing indicators:** Show when DAN is thinking, executing tools, or waiting

### 7. Migration from Existing Chat Panel
- [x] 7-1. Reuse existing `ChatPanel.tsx` components where possible
- [x] 7-2. Upgrade message rendering for rich output types (tables, code blocks with copy)
- [x] 7-3. Preserve all existing functionality: @mentions, slash commands, streaming
- [x] 7-4. The full ChatPanel in the editor (Operations mode) continues working

## Decisions
- `ChatPanel` gains `fullScreen` prop — when true, renders split layout (thread sidebar + conversation)
- Messages constrained to `max-w-3xl mx-auto` in fullScreen for readability
- Thread sidebar is 288px (`w-72`) and dismisses after selecting a thread
- Backend behavioral modes (`ask` / internal `conversation` / `agent` / `plan` / `debug`) are NOT surfaced in the layout mode bar. They remain as auto-detected internal behavior with slash-command override.
- Workspace tabs are backed by `ProjectStore` projects plus UI-only metadata (pinned paths, color/icon, open-thread cache). Scratch workspace creation auto-creates a lightweight project record.
- The second navigation layer is split intentionally: open/recent threads appear as a tab strip for speed; the sidebar remains the full searchable history.
- There is only one active composer per workspace. In Chat mode the global chat bar is visually integrated into the conversation footer rather than rendered as a second independent input.
- Table rendering detects markdown pipe syntax and renders semantic HTML tables
- Code blocks have header bar with language badge + Copy button
- Escalation detection runs keyword patterns against user messages per layout mode
- Progress/reassurance events reuse the assistant bubble for interim status
- Thread titles are server-owned: readable first-message fallback, then async-generated title
- Streaming persistence is incremental: debounced partial saves, immediate milestone updates
- Tool traces collapse repeated `file_read` hits on same path into one card with merged ranges

## Notes
- Chat mode should feel indistinguishable from a best-in-class chat UI for simple tasks
- The "smart" part is progressive escalation: chat becomes a launchpad for specialized modes
- Rich output rendering is the highest-ROI improvement — makes chat useful for analytical and dev tasks
- Keep message rendering extensible: new output types addable by registering a renderer component
- Workspace navigation adds power without complexity — most users will have 1-3 workspaces, a small open-thread tab strip, and a larger searchable history in the sidebar
