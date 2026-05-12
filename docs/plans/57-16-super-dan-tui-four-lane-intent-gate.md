# 57-16: Super DAN TUI Four-Lane Intent Gate

**Parent:** [57-chat-agent-v2-control-plane](57-chat-agent-v2-control-plane.md)
**Status:** completed
**Goal:** Route every free-text `dan super-tui` input into exactly one `{read-only, write} x {simple, complex}` lane before starting any live planner/build work.

## Tasks
- [x] 1. Define the two routing dimensions
  - [x] 1-1. Define `read-only` as permission to inspect, search, summarize, explain, compare, and answer without mutating workspace files, creating run-local plan files, or updating tracking docs.
  - [x] 1-2. Define `write` as permission to change workspace state, create/update files, run validation/repair, and update project tracking docs when the user asks for mutation.
  - [x] 1-3. Define `simple` as a bounded one-step answer or action that does not need a planner, staged execution, validation loop, or multi-source synthesis.
  - [x] 1-4. Define `complex` as work that needs broad inspection, several files, staged reasoning, planning, validation, repair, or a durable execution trace.
  - [x] 1-5. Treat uncertainty as a clarification result, not a fifth lane; the TUI should ask one short question instead of guessing.
- [x] 2. Add an explicit intent-gate contract
  - [x] 2-1. Introduce a TUI-owned decision record with `permission`, `complexity`, `lane`, `confidence`, `rationale`, and a user-visible mode label.
  - [x] 2-2. Start with deterministic lexical and structural signals: `show/list/read/view/explain/summarize/find/check` bias read-only; `fix/add/update/create/refactor/implement/delete/write/build` bias write.
  - [x] 2-3. Keep the classifier generic: do not hard-code todo-specific behavior or special-case `docs/todo.md`.
  - [x] 2-4. Let selected `$skill-name` mentions affect required context and complexity, but never bypass read-only/write permission.
  - [x] 2-5. Record the chosen lane and rationale in the TUI transcript/debug metadata.
- [x] 3. Route each lane to the correct capability path
  - [x] 3-1. `simple read-only`: answer locally in the TUI when possible, using bounded file/status reads only.
  - [x] 3-2. `complex read-only`: use a read-only model/tool loop with `file_read`, `list_directory`, `workspace_check`, search, and no mutation-capable shell access.
  - [x] 3-3. `simple write`: use a bounded direct patch path without invoking the broad run-local planner unless the target is unclear.
  - [x] 3-4. `complex write`: use the existing Super DAN live planner/build/validate/repair flow.
  - [x] 3-5. Make the chosen lane visible before execution begins so bad routing is easy to notice.
- [x] 4. Enforce capabilities, not only labels
  - [x] 4-1. Read-only lanes must reject `file_write`, `file_edit`, write-capable shell commands, plan-file creation, changelog updates, and todo/tracking-doc updates.
  - [x] 4-2. Read-only shell usage must be limited to inspection commands such as `ls`, `pwd`, `rg`, `sed -n`, `head`, `tail`, `wc`, `find`, `git status`, and `git diff`.
  - [x] 4-3. Write lanes must preserve existing workspace safety, validation, and tracking-doc behavior.
  - [x] 4-4. Raw event logs and transcript metadata should capture the lane decision for later debugging.
- [x] 5. Add regression coverage from the observed failure
  - [x] 5-1. `show me the todo list` routes to `simple read-only` and does not create `.dan-super/runs/**/plans`.
  - [x] 5-2. `summarize docs/todo.md` routes to read-only and cannot write files.
  - [x] 5-3. `update the todo list` routes to write.
  - [x] 5-4. `fix the bug in todo handling` routes to write.
  - [x] 5-5. Ambiguous inputs ask a concise clarification instead of starting a live run.
  - [x] 5-6. `$skill-name` selection does not change read-only into write without explicit mutation intent.
- [x] 6. Document the user-facing contract
  - [x] 6-1. Update TUI help/README text to explain the four lanes in plain language.
  - [x] 6-2. Document that `--raw-events` remains the debug escape hatch for full routing/tool detail.
  - [x] 6-3. Add changelog and todo updates when implementation lands.

## Decisions
- The intent gate chooses exactly one of four lanes: `simple read-only`, `complex read-only`, `simple write`, or `complex write`.
- `read-only` versus `write` is a permission boundary. It must be enforced by available tools and side effects, not just displayed text.
- `simple` versus `complex` is an effort boundary. It decides whether to answer directly, run a small bounded path, or use the full planner/build flow.
- The TUI must not treat every natural-language line as autonomous work.
- The generic classifier should be conservative: when permission or complexity is unclear, ask one short clarification.

## Notes
- This plan captures the `io-similarity` trace lesson: tiny/display-style requests should not be promoted into planner/build runs.
- This plan intentionally replaces the rejected todo-specific shortcut with a reusable front-door routing rule.
- The implementation should stay in the TUI/session/front-door layer first. Super DAN core should change only if a capability boundary cannot be enforced from the surface.
- 2026-05-12: First implementation landed in `src/dan/cli/super_tui.py`: deterministic four-lane classification, local bounded read-only answers, clarification output for unclear requests, and complex-write dispatch to the existing Super DAN runner. Remaining work is the richer complex read-only model/tool loop and bounded simple-write path.
- 2026-05-12: Second implementation slice added bounded complex-read-only search/summarization and exact simple-write file operations (`copy`, `move`/`rename`, `touch`) inside the workspace. The true model-backed complex read-only loop remains open.
- 2026-05-12: Final implementation slice added the live/model-backed complex read-only tool loop behind the TUI read-only lane. It uses only `list_directory`, `file_read`, `workspace_check`, and read-only git tools when available; the broader `shell_command` tool remains excluded from this lane because the local shell runtime is mutation-capable.
