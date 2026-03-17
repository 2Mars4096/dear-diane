# 12-8: Mode Chat Sidebar Parity

**Parent:** [12-cursor-parity-chat](12-cursor-parity-chat.md)
**Status:** completed
**Goal:** Bring the Research/Development shared chat sidebar much closer to full ChatPanel UX without turning the compact rail into a full thread manager.

## Tasks
- [x] 1. Upgrade the shared sidebar session model
 - [x] 1-1. Persist sidebar thread id and selected chat mode alongside messages
 - [x] 1-2. Improve full-chat handoff so the sidebar can reopen the current scratch thread in Chat mode
- [x] 2. Port compact composer parity features
 - [x] 2-1. Add mode pills (`auto` / `agent` / `ask` / `plan` / `debug`) with auto-detected badge support
 - [x] 2-2. Add slash-command affordances plus shared recent-command chips
 - [x] 2-3. Add mention autocomplete and smart-paste hints in the sidebar composer
- [x] 3. Port compact stream-lifecycle controls
 - [x] 3-1. Keep the composer active while streaming, queue follow-up turns, and auto-send queued items
 - [x] 3-2. Add explicit stop-generation control and preserve attachment-aware queued items
- [x] 4. Port richer assistant rendering into the sidebar
 - [x] 4-1. Reuse full-chat message rendering for richer markdown, mention chips, attachments, and copy-as-Markdown
 - [x] 4-2. Surface grouped tool calls, run events, token usage, and detected mode metadata in sidebar messages
- [x] 5. Cover the shared stream helper changes with focused tests

## Decisions
- Sidebar parity targets the shared `ModeChatSidebar.tsx` so Research and Development stay aligned.
- Full thread-history features (history rail, search, pin/export/delete, branch tree) remain full-screen Chat-only.
- Sidebar message rendering now reuses `ChatMessageBubble` plus shared tool-progress helpers instead of maintaining a second markdown/tool-call renderer.

## Notes
- Development-only code actions from the unused legacy `code/ChatSidebar.tsx` are intentionally out of scope unless the parity pass naturally reuses them.
- Validation: `editor npm run test -- --run src/lib/__tests__/editorChat.test.ts`; `editor npm run build` (build succeeds with the existing local Vite/Node version warning).
- Follow-up hardening patched the remaining review findings: maximize/full-Chat handoff now waits for the active response queue to finish, queued items snapshot their intended mode and only expose `Push` when injection can preserve semantics, and markdown mention parsing now skips literal fenced/inline code.
- Validation: `editor npm run test -- --run src/lib/__tests__/editorChat.test.ts src/components/__tests__/ChatMessage.test.ts`; `editor npm run build`; `ReadLints` clean on edited files.
