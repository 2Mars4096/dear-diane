# 25-5: Auto-Approve & Undo

**Parent:** [25-chat-control-plane](25-chat-control-plane.md)
**Status:** completed
**Goal:** Auto-apply mutations by default (no `Apply? [Y/n]` prompt), with `/undo` to revert the last mutation and `--confirm` to restore the approval workflow for cautious use.

## Motivation

Today every mutation goes through an approval prompt:

1. LLM generates a `MutationPlan`
2. Dry-run validates it
3. User sees diff preview: "Apply mutation? [Y/n]"
4. Only on "Y" does the mutation apply

This was appropriate during early development when mutations were unreliable. With the validation gate, auto-retry loop, and bounded diagnosis (Phase 14), mutations are now trustworthy enough that the approval prompt creates friction without adding safety. The mental model should be **trust-but-verify**: mutations apply immediately, and the user can undo if the result is wrong.

This matches how modern tools work — Cursor applies code edits immediately with undo available, rather than asking "Apply edit? [Y/n]" for every change.

## Design

### Default Behavior Change

```
Before:  LLM → dry-run → diff preview → "Apply? [Y/n]" → apply
After:   LLM → dry-run → apply → show diff summary → (user can /undo)
```

### Undo Mechanism

Undo already exists in two places:
- **Editor**: Zustand `history` slice with `pushSnapshot`/`undo`/`redo` — full graph snapshots
- **CLI**: No undo currently; mutations are one-way

This plan adds:
- **CLI `/undo`**: Revert last mutation by restoring the pre-mutation graph via `ChatClient.save_graph()` (`PUT /api/graphs/{id}`)
- **Editor auto-apply**: Skip `GraphDiffPreview` accept/reject modal; apply directly, push to history stack

### Confirm Mode

`--confirm` flag (CLI) or `DAN_MUTATION_CONFIRM=1` (env) restores the old approval workflow:

```
dan-chat --confirm          # every mutation shows diff + prompt
DAN_MUTATION_CONFIRM=1      # same via env var
```

Messaging adapters (Telegram, WhatsApp) default to `--confirm` since undo is harder in messaging contexts.

### Why Auto-Apply Must Stay Client-Driven

The server should continue returning `ChatMutationEvent` proposals rather than silently applying mutations inside `ChatManager`.

- **Editor** needs to call `pushSnapshot()` before apply so Cmd+Z restores the pre-mutation graph.
- **CLI** needs the pre-apply graph snapshot to implement `/undo`.
- **Messaging adapters** should stay confirm-first.

So the behavior change for this plan is: **clients auto-apply by default after receiving a successful proposal**. The mutation proposal/response shape stays the same.

## Tasks

### 1. CLI auto-apply

- [x] 1-1. In `cli/chat.py`, update the `chat_mutation` handling branch in the main REPL event loop to auto-apply by default: when `dry_run_result.success == True`, immediately call `ChatClient.apply_mutation()` instead of prompting
- [x] 1-2. After apply, print a compact diff summary (nodes added/removed/edited, edges changed) — reuse or extend existing `_format_mutation_summary()` formatting
- [x] 1-3. Add `--confirm` flag to `dan-chat` argparse: when set, restore old behavior (show full diff, prompt `Apply? [Y/n]`)
- [x] 1-4. Read `DAN_MUTATION_CONFIRM` env var as fallback when `--confirm` not passed

### 2. CLI `/undo` command

- [x] 2-1. Track the pre-mutation graph in `ChatREPL` state: before `apply_mutation()`, snapshot the current graph dict (via `ChatClient.get_graph()`)
- [x] 2-2. Store a stack of `(graph_revision, graph_dict)` tuples (max depth 10)
- [x] 2-3. `/undo` pops the stack and calls `ChatClient.save_graph(workflow_id, previous_graph)` to restore
- [x] 2-4. Print summary of what was undone: "Reverted: removed 3 nodes, 4 edges" (diff between current and restored)
- [x] 2-5. `/redo` is out of scope — undo stack is linear, no redo

### 3. Editor auto-apply

- [x] 3-1. In `ChatPanel.tsx`, when `chat_mutation` event arrives with `dry_run_result.success == true`, auto-trigger the existing apply path (`handleApplyMutation`) instead of waiting for `GraphDiffPreview` confirmation
- [x] 3-2. Keep `pushSnapshot()` in the apply path so undo via Cmd+Z restores the pre-mutation graph
- [x] 3-3. Show a toast notification with compact diff summary instead of the modal: "Applied: +3 nodes, +5 edges, ~1 edit"
- [x] 3-4. Add a user preference (localStorage) `dan_mutation_confirm` that restores the modal workflow when `true`
- [x] 3-5. When `dry_run_result.success == false`, still show the error in chat (no change from current behavior)

### 4. Messaging adapter behavior

- [x] 4-1. Messaging adapters (Telegram, WhatsApp, email) default to confirm mode: show diff summary and ask for approval before applying
- [x] 4-2. Rationale: messaging surfaces don't have easy undo; requiring approval is safer
- [x] 4-3. Adapter config: `auto_approve: bool = False` on `AdapterConfig` — set to `True` to enable auto-apply for advanced users

### 5. Shared client policy

- [x] 5-1. Keep the server mutation protocol unchanged: `ChatManager` continues to return `ChatMutationEvent` with `mutation_plan` + `dry_run_result`
- [x] 5-2. Add a shared client-side policy helper: if `dry_run_result.success == True` and confirm mode is off, apply immediately; otherwise open the existing confirm/reject UI
- [x] 5-3. CLI defaults to auto-apply; `--confirm` forces the old prompt flow
- [x] 5-4. Editor defaults to auto-apply; local preference can force the old diff-preview modal
- [x] 5-5. Messaging adapters default to confirm mode and should not auto-apply unless explicitly enabled

### 6. Graph revision tracking for undo

- [x] 6-1. `apply_mutation` endpoint already returns `graph_revision` and `new_graph` — CLI uses these to track undo stack
- [x] 6-2. Ensure `ChatClient.apply_mutation()` returns the full `new_graph` (or at least `graph_revision`) so CLI can update its local state
- [x] 6-3. On `/undo`, CLI updates its `client_graph_revision` to match the restored graph

### 7. Tests

- [x] 7-1. CLI test: auto-apply on successful dry-run (no prompt shown)
- [x] 7-2. CLI test: `--confirm` flag restores approval prompt
- [x] 7-3. CLI test: `/undo` restores previous graph state
- [x] 7-4. CLI test: `/undo` with empty stack shows "Nothing to undo"
- [x] 7-5. Server regression test: chat mutation flow still returns proposal events and does not apply automatically server-side
- [x] 7-6. Server regression test: proposal payload shape (`mutation_plan`, `dry_run_result`, `graph_revision`) remains backward compatible for CLI/editor clients
- [x] 7-7. Editor test: auto-apply pushes to history stack, Cmd+Z undoes

## Files to Touch

| File | Changes |
|------|---------|
| `src/dan/cli/chat.py` | Auto-apply logic, `--confirm` flag, `/undo` command, undo stack |
| `src/dan/server/chat_manager.py` | No protocol change required; keep returning mutation proposals |
| `src/dan/server/app.py` | No API shape change required unless Phase 15 later adds explicit per-surface policy metadata |
| `editor/src/components/ChatPanel.tsx` | Auto-apply on mutation event, toast instead of modal, preference toggle |
| `editor/src/store/useGraphStore.ts` | No change (undo already works via history stack) |
| `src/dan/adapters/base.py` | `auto_approve` field on `AdapterConfig` |
| `tests/test_cli/test_chat.py` | Auto-apply, confirm flag, undo tests |
| `tests/test_server/test_chat_manager.py` | Regression tests to ensure mutation proposal flow is unchanged |

## Decisions

- **Default is auto-apply, but still proposal-based.** The approval prompt was a development-era safety net. Clients should apply successful proposals immediately by default, but the server should keep emitting proposals so local undo/history remains correct.
- **Undo stack is client-side.** The server doesn't maintain undo history — each client (CLI, editor) tracks its own. This is necessary because the editor's history stack and the CLI's `/undo` snapshot both live client-side.
- **Messaging adapters default to confirm.** Undo in Telegram/WhatsApp is impractical. Requiring approval in messaging contexts is the safer default.
- **No redo.** Undo is a linear stack. Redo adds complexity for minimal benefit — the user can just re-issue the instruction.

## Notes

- The editor already has full undo/redo via Zustand `pushSnapshot`. This plan only changes the *trigger* (auto-apply instead of modal) — the undo mechanism is unchanged.
- `client_graph_revision` must be updated after auto-apply so subsequent mutations don't hit stale-revision errors. Both CLI and editor already do this after manual apply; auto-apply must follow the same pattern.
- The `/undo` command is CLI-only. Editor uses Cmd+Z. Messaging adapters don't support undo (confirm mode avoids the need).
- Effort estimate: ~1 day.
