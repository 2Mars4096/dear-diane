# 4: Lightweight Chat V2

**Status:** completed
**Goal:** Add a lightweight alternate chat frontend that keeps the existing editor intact while proving a smaller chat-first shell against the canonical DAN chat stream endpoints.

## Tasks
- [x] 1. Add a separate V2 route
  - [x] 1-1. Route `#v2` / `#chat-v2` through `App.tsx` without mounting the full classic shell
  - [x] 1-2. Add a classic-shell `V2` entry and a V2 return path back to `#chat`
- [x] 2. Build the lightweight chat shell
  - [x] 2-1. Add thread history, new chat, delete, local filtering, server status, mode selector, streaming composer, stop control, tool chips, and run-event chips
  - [x] 2-2. Persist V2 chat messages through the existing chat-thread store
- [x] 3. Freeze the V2 endpoint adapter
  - [x] 3-1. Add `editor/src/lib/chatV2Api.ts` around `/api/chat/message`, `/api/chat/{channel}/events`, `/api/chat/{channel}/stop`, and `/api/chats`
  - [x] 3-2. Add focused endpoint/history/persistence-shape coverage
- [x] 4. Validate build and bundle impact
  - [x] 4-1. Run focused V2 API tests
  - [x] 4-2. Run editor build and bundle-budget checks

## Decisions
- V2 uses DAN's existing chat portal contract instead of cloning any external site's private endpoints.
- V2 is an alternate entrypoint, not a replacement for the current editor.
- `AppShell` is lazy-loaded so opening `#v2` avoids mounting the classic workspace/mode shell up front.

## Notes
- Public ChatGPT was checked only for broad interaction shape: a left history rail, compact chat surface, centered empty state, and streaming composer. The implementation, styling, copy, branding, and endpoint contract are original to DAN.
