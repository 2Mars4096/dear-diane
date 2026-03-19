# 12-8: Mode Chat Sidebar Parity

**Parent:** [12-cursor-parity-chat](12-cursor-parity-chat.md)
**Status:** completed
**Goal:** Bring the Research/Development shared chat sidebar much closer to full ChatPanel UX while keeping the mode-specific context enrichment that makes those sidebars useful.

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
- [x] 6. Restore direct thread/history + branch controls in the compact rail
 - [x] 6-1. Load `_scratch` thread history in-place and allow selecting prior chats without maximizing into full Chat
 - [x] 6-2. Add `New chat` controls in the sidebar header/history view
 - [x] 6-3. Reuse full-chat branch semantics for `Edit and resend`, `Regenerate`, and `Explore from here`
 - [x] 6-4. Add focused regressions for sidebar history selection and branch-action visibility

## Decisions
- Sidebar parity targets the shared `ModeChatSidebar.tsx` so Research and Development stay aligned.
- Search/pin/export/delete/branch-tree management still remain richer in full-screen Chat, but the shared sidebar now exposes the key everyday controls users expect: history selection, new-thread creation, and branch actions on prior turns.
- Sidebar message rendering now reuses `ChatMessageBubble` plus shared tool-progress helpers instead of maintaining a second markdown/tool-call renderer.

## Notes
- Development-only code actions from the unused legacy `code/ChatSidebar.tsx` are intentionally out of scope unless the parity pass naturally reuses them.
- Validation: `editor npm run test -- --run src/lib/__tests__/editorChat.test.ts`; `editor npm run build` (build succeeds with the existing local Vite/Node version warning).
- Follow-up hardening patched the remaining review findings: maximize/full-Chat handoff now waits for the active response queue to finish, queued items snapshot their intended mode and only expose `Push` when injection can preserve semantics, and markdown mention parsing now skips literal fenced/inline code.
- Validation: `editor npm run test -- --run src/lib/__tests__/editorChat.test.ts src/components/__tests__/ChatMessage.test.ts`; `editor npm run build`; `ReadLints` clean on edited files.
- 2026-03-19 follow-up: the compact sidebar no longer hides thread history and branch actions behind the maximize handoff. `ModeChatSidebar.tsx` now lists `_scratch` threads directly, can start a new chat in-place, and passes the same `ChatMessageBubble` branch-action hooks used by `ChatPanel`, so Research/Development/Operations sidebars now show `Edit and resend in new branch`, `Regenerate in new branch`, and `Explore from here` on prior messages. Review patch: switching history threads or starting a new sidebar chat now persists the current thread snapshot first so the new controls do not drop debounce-pending updates.
