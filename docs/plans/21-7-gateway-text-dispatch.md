# 21-7: Gateway Text Dispatch

**Parent:** [21-author-distribute](21-author-distribute.md)
**Status:** completed
**Goal:** Implement text dispatch in the gateway so `dan-run "write a paper on X"` works when the server is running. Currently the gateway returns 501 for `req.text`.

## Context

The gateway `POST /api/gateway/dispatch` accepts `workflow_id`, `workflow_path`, or `text`. When `text` is provided, it raises 501 ("Text dispatch not yet implemented"). This blocks `dan-run "goal"` in server mode. The CLI falls back to local MetaController when server is unavailable, but when server *is* running, NL goals fail.

This plan implements 23-1 task 1-6: route `req.text` through MetaController, create a run, and stream events.

## Tasks

- [x] 1. **Implement text dispatch in `gateway/router.py`**
  - [x] 1-1. When `req.text` is provided: route to MetaController instead of raising 501
  - [x] 1-2. Create meta session, run planning; approval via `human_input_needed` with `render_mode: "approval"` (or skip if `req.auto_approve`)
  - [x] 1-3. Start run via `RunManager.start_run()` with the planned graph
  - [x] 1-4. Stream meta events (`plan_created`, `approval_needed`, etc.) on the global event bus
  - [x] 1-5. Reuse `POST /api/gateway/submit-input` for plan approval (23-1 task 1-6-1)
  - [x] 1-6. Add `auto_approve: bool = False` to `DispatchRequest`; `DanClient.dispatch()` and `dan-run` pass `--auto-approve` through

- [x] 2. **CLI integration**
  - [x] 2-1. Verify `dan-run "goal"` in server mode — already calls `client.dispatch(text=source, surface_id="cli")`; no CLI changes needed once server returns run_id
  - [x] 2-2. Plan approval: when meta emits `human_input_needed` with `render_mode: "approval"`, CLI TUI prompts user (existing HumanNode path); add `auto_approve` to `DispatchRequest` and pass through so `--auto-approve` skips approval in server mode

## Decisions

- MetaController approval events emit as `human_input_needed` with `render_mode: "approval"` — unified with HumanNode mechanism.
- One-shot execution only — conversational flow is 21-6 (dan-chat).

## Files to Touch

| File | Changes |
|------|---------|
| `src/dan/server/gateway/router.py` | Replace 501 with MetaController text dispatch |
| `src/dan/server/gateway/models.py` | Add `auto_approve` to `DispatchRequest` |
| `src/dan/client/client.py` | Add `auto_approve` param to `dispatch()` |
| `src/dan/cli/run.py` | Pass `args.auto_approve` to `client.dispatch()` in server mode |

## Notes

- Complements 21-6: 21-6 is conversational chat; 21-7 fixes one-shot NL dispatch when server is running.
- DanClient and dan-run already send `text=` when source is NL; server returns 501 — server-side is the only missing piece.
