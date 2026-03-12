# 1-2: Chat Mode (Default Workspace)

**Parent:** [1-ui-spec](1-ui-spec.md)
**Status:** in-progress
**Goal:** Transform the existing chat panel into a full-screen default workspace with rich output rendering and progressive escalation to specialized modes.

## Context

The existing editor has a chat panel (`ChatPanel.tsx`, `ChatMessage.tsx`, etc.) with streaming, tool calls, @mentions, and slash commands. This plan promotes it to a full-screen mode and adds rich output rendering so simple tasks feel as polished as ChatGPT/Claude while enabling seamless escalation to specialized modes when tasks become complex.

Chat mode is the entry point for all users. It should have zero learning curve. The complexity of DAN's workflow engine, memory, and multi-agent capabilities should be invisible unless the task demands it.

## Layout

```
┌─ Thread List (collapsible) ─┬─ Conversation ──────────────────────┐
│                              │                                     │
│  🔍 Search conversations    │  User: Compare Q1 revenue across    │
│                              │        our portfolio companies      │
│  📌 Pinned                  │                                     │
│  ▸ Supply chain research     │  DAN: [Working: 3 steps]            │
│  ▸ Factor model v2          │       ┌──────────────────────────┐  │
│                              │       │ Company │ Q1 Rev │ YoY % │  │
│  Today                      │       │ AAPL    │ $94.8B │ +5.2% │  │
│  ▸ Quick question           │       │ MSFT    │ $61.9B │ +13%  │  │
│  ▸ Debug auth middleware    │       └──────────────────────────┘  │
│                              │       AAPL shows slowest growth...  │
│  Yesterday                  │                                     │
│  ▸ Lit review planning      │       [Chart] [Export] [Expand]     │
│  ▸ Paper draft feedback     │                                     │
│                              │                                     │
│                              ├─────────────────────────────────────│
│                              │ > Drill deeper into AAPL segments   │
└──────────────────────────────┴─────────────────────────────────────┘
```

## Tasks

### 1. Full-Screen Chat Layout
- [x] 1-1. Full-width conversation view when Chat mode is active
- [x] 1-2. Thread list sidebar (left, collapsible): conversation history grouped by date
- [x] 1-3. Message bubbles: user messages (right-aligned or subtle), assistant messages (left, full-width)
- [x] 1-4. Chat input at bottom: slash commands, @mentions, file drag-and-drop (migrate existing features)
- [ ] 1-5. Optional context panel (right, toggle with Cmd+I): active project info, recent memory, relevant files
- [x] 1-6. Empty state: welcome message with suggested quick-starts ("What can I help with?")

### 2. Rich Output Rendering
- [x] 2-1. **Tables:** Markdown pipe tables rendered as styled HTML tables with zebra-stripe hover, alignment support
- [ ] 2-2. **Charts:** Inline chart rendering from tool output using Chart.js or similar. Bar, line, pie, scatter.
- [x] 2-3. **Code blocks:** Syntax highlighting (highlight.js), copy button, language badge
- [ ] 2-4. **File cards:** Downloadable attachments with type icon, size, preview thumbnail (PDF, image, CSV, code)
- [ ] 2-5. **Diff view:** Inline code diffs (unified or split) with accept/reject buttons for proposed code changes
- [ ] 2-6. **Progress cards:** Multi-step task progress with phase indicators, elapsed time, and "Show pipeline" toggle
- [ ] 2-7. **Structured responses:** Collapsible sections, tabbed output (e.g., "Summary | Details | Sources")
- [ ] 2-8. **Image display:** Inline rendering of generated/fetched images with lightbox on click
- [ ] 2-9. **Citation cards:** When sources are referenced, show author/year/title with link, hover for abstract

### 3. Progressive Escalation
- [x] 3-1. **Mode suggestion banner:** When user message matches a specialized mode pattern, show "This looks like a [research/analytics/dev] task. Switch to [Mode]?" with one-click accept
- [x] 3-2. **Auto-detect triggers:** keyword-based detection — research/academic terms → Research; code/error/debug → Development; CSV/chart/data → Analytics; blog/marketing/draft → Content; workflow/pipeline → Operations
- [ ] 3-3. **Inline workspace preview:** For complex tasks, show a miniature panel preview of what the specialized mode would look like before switching
- [ ] 3-4. **User preference:** Per-trigger "Always switch" / "Always ask" / "Never switch" settings, stored in user profile
- [x] 3-5. **Graceful degradation:** If user declines mode switch (dismiss), chat continues normally

### 4. Conversation Management
- [ ] 4-1. New conversation button + keyboard shortcut (Cmd+N)
- [ ] 4-2. Conversation search: full-text across all threads
- [ ] 4-3. Pin/star conversations for quick access
- [ ] 4-4. Export conversation: Markdown, PDF, JSON
- [ ] 4-5. Conversation context carries to other modes: switch to Research → conversation available in research chat bar
- [ ] 4-6. Thread metadata: creation date, message count, last active, associated project/task
- [ ] 4-7. Delete / archive conversations

### 5. Quick Actions & Intelligence
- [ ] 5-1. **Suggested follow-ups:** After a response, show 2-3 contextual next steps ("Drill deeper into...", "Export as...", "Schedule this...")
- [ ] 5-2. **Recent commands bar:** Horizontally scrollable pills showing most-used slash commands
- [ ] 5-3. **Drag-and-drop zone:** Visual indicator when dragging files over the chat (glow border, file type detection)
- [ ] 5-4. **Voice input button:** Integrates with existing transcription (Whisper)
- [ ] 5-5. **Smart paste:** URL → auto-fetch and summarize; image → auto-describe; code → auto-detect language and format
- [ ] 5-6. **Typing indicators:** Show when DAN is thinking, executing tools, or waiting for external services

### 6. Migration from Existing Chat Panel
- [x] 6-1. Reuse existing `ChatPanel.tsx` components where possible (message rendering, streaming, tool display)
- [x] 6-2. Upgrade message rendering pipeline to support new rich output types (tables, code blocks with copy)
- [x] 6-3. Preserve all existing functionality: modes (Ask/Agent/Plan/Debug), @mentions, slash commands, streaming
- [x] 6-4. The full ChatPanel in the editor (Operations mode) continues working — this is a sibling, not a replacement

## Decisions
- `ChatPanel` gains `fullScreen` prop — when true, renders split layout (thread sidebar + conversation) instead of toggle behavior
- In fullScreen, `showThreadList` controls sidebar visibility alongside conversation, not a full-panel toggle
- Messages and input constrained to `max-w-3xl mx-auto` in fullScreen for readability
- Thread sidebar is 288px (`w-72`) and dismisses after selecting a thread
- EmptyState has two layouts: compact (sidebar mode) and spacious (fullScreen) with grid of suggested prompts
- Table rendering detects markdown pipe syntax (`|` + `---` divider) and renders semantic HTML tables
- Code blocks have header bar with language badge + Copy button via event delegation on `data-copy-code` attribute
- Escalation detection is keyword-based on user messages (regex patterns per mode); runs only in fullScreen
- `EscalationBanner` component is separate from ChatPanel — reusable in other modes later
- Progress/reassurance `progress_ack` events reuse the assistant bubble for interim status but must not terminate the chat stream; the real final response arrives on a later terminal event
- Queued turns may be redirected onto a replacement `stream_channel_id`; the chat renderer must follow `chat_queued` handoffs so the same thread bubble continues streaming instead of stalling on an empty placeholder
- Thread titles are server-owned: use a readable first-message fallback immediately, then replace it asynchronously with a micro-tier generated title from the first user message only; manual renames lock the title against future auto-updates
- Renaming should be explicit, not hidden: expose a visible pencil action on the active thread header and in each history row instead of relying only on clicking title text

## Notes
- Chat mode should feel indistinguishable from a best-in-class chat UI (Claude/ChatGPT) for simple tasks
- The "smart" part is progressive escalation: chat becomes a launchpad for specialized modes
- Rich output rendering (tables, charts, diffs) is the highest-ROI improvement — it makes chat useful for analytical and development tasks without requiring a mode switch
- Keep message rendering extensible: new output types should be addable by registering a renderer component for a content type, not by modifying the core message component
