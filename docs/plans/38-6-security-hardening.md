# 38-6: Security Hardening

**Parent:** [38-review-hardening](38-review-hardening.md)
**Status:** completed
**Goal:** Close the highest-risk security gaps identified in the 2026-03-19 general project health review: workspace sandbox bypass via absolute paths, unsandboxed `exec()` calls, default-open shell tool, missing HTML sanitization, and fragile SQL interpolation.

## Context

- The 2026-03-19 general project health review identified three P1 security issues and two P2 issues.
- `validate_path()` allows any absolute path through with only a log warning — an LLM agent can read/write/delete arbitrary files.
- Three `exec()` call sites run LLM-generated code without `__builtins__` restriction.
- The shell tool defaults to unrestricted mode (`DAN_SANDBOX_SHELL` off, `DAN_SHELL_ALLOW` empty).
- 9 `dangerouslySetInnerHTML` usages in the editor have no DOMPurify sanitization.
- `learning_tiers.py` uses f-string SQL interpolation (regex-mitigated but fragile).

## Tasks

### 1. Workspace sandbox strict mode
- [x] 1-1. Add `DAN_STRICT_SANDBOX` env flag (default off for backward compatibility, documented in `.env.example`). *(implemented in `_workspace.py`; documented in `.env.example` 2026-03-19)*
- [x] 1-2. When enabled, `validate_path()` rejects absolute paths outside the workspace root for write/delete operations (reads can optionally warn). *(implemented for `file_write` / `file_delete` / `file_copy` / `file_move` / `compress`)*
- [x] 1-3. Ensure the Electron bridge's write confirmation dialog and the backend sandbox produce consistent behavior. *(2026-03-19: added `validateWritePath()` in `editor/electron/main.ts` that mirrors the backend `validate_path()` logic — resolves symlinks, checks `DAN_STRICT_SANDBOX` + `DAN_WORKSPACE_ROOT`, and blocks writes/deletes/renames outside workspace. Applied to `fs:writeFile`, `fs:delete`, `fs:rename`, `fs:mkdir`, and `search:replaceInFile`. Vitest coverage in `editor/src/__tests__/validateWritePath.test.ts`.)*
- [x] 1-4. Add tests for sandbox enforcement: relative paths pass, absolute-in-workspace passes, absolute-outside-workspace is rejected, symlink traversal is caught. *(2026-03-19: expanded `tests/test_concierge/test_phase31_review_hardening.py` with strict-sandbox coverage for relative writes, absolute-inside-workspace writes, absolute-outside-workspace rejection, and symlink escape rejection.)*

### 2. `exec()` builtins restriction audit
- [x] 2-1. Update `diagnosis.py:1005` and `diagnosis.py:1084` to pass `{"__builtins__": _ALLOWED_BUILTINS}` matching the pattern in `server/exec.py`.
- [x] 2-2. Update `chat_manager.py:4558` (`_exec_deterministic_builder_code`) to use restricted builtins.
- [x] 2-3. Audit for any other `exec()` / `eval()` calls outside `server/exec.py` and `engine/conditions.py` that lack builtins restriction. *(2026-03-19 audit: remaining `planner.py` harness and `executors/code.py` uses are intentional sandbox/trusted-code paths)*
- [x] 2-4. Add a test or lint rule that flags new `exec(code, ns)` calls without `__builtins__` override. *(2026-03-19: added `tests/test_security/test_exec_builtins_audit.py` — AST-walks `src/dan/` for all `exec()`/`eval()` calls, verifies each has `__builtins__` restriction within 20 lines, and flags stale exemptions. Only `engine/conditions.py` is exempt (uses stricter `_SAFE_BUILTINS`).)*

### 3. Shell tool default to sandbox mode
- [x] 3-1. Flip `DAN_SANDBOX_SHELL` default to `"1"` (sandbox on by default).
- [x] 3-2. Document the opt-out clearly in `.env.example` and README. *(2026-03-19: added `DAN_SANDBOX_SHELL` and `DAN_STRICT_SANDBOX` documentation to `.env.example` Cost & Safety section. README now references `.env.example` for all settings including sandbox.)*
- [x] 3-3. Add a startup warning when sandbox mode is explicitly disabled.
- [x] 3-4. Ensure existing shell tool tests pass with sandbox on. *(2026-03-19: verified — all 206 shell-related tests pass with `DAN_SANDBOX_SHELL=1` across 8 test files.)*

### 4. HTML sanitization
- [x] 4-1. Add `dompurify` as an editor dependency.
- [x] 4-2. Create a shared `sanitizeHtml()` utility in `editor/src/lib/`.
- [x] 4-3. Replace all 9 `dangerouslySetInnerHTML` usages with the sanitized wrapper: `ChatMessage.tsx` (3), `ChatSidebar.tsx`, `ExplainPopup.tsx`, `CodebaseQA.tsx`, `ExtensionsPanel.tsx`, `WritingPane.tsx`, `MentionAutocomplete.tsx`.
- [x] 4-4. Add a test that verifies XSS vectors (event handlers, SVG, javascript: protocol) are stripped.

### 5. SQL parameterization in learning_tiers.py
- [x] 5-1. Replace the f-string `json_extract(data, '$.{key}')` with parameterized column access or add an explicit comment explaining why the regex is security-critical.
- [x] 5-2. Add a test with an adversarial key that would exploit unvalidated interpolation, confirming the regex blocks it.

## Primary Files

- `src/dan/tools/_workspace.py`
- `src/dan/tools/shell_command.py`
- `src/dan/meta/diagnosis.py`
- `src/dan/server/chat_manager.py`
- `src/dan/server/exec.py`
- `src/dan/engine/learning_tiers.py`
- `editor/src/components/ChatMessage.tsx`
- `editor/src/components/code/ChatSidebar.tsx`
- `editor/src/components/code/ExplainPopup.tsx`
- `editor/src/components/code/CodebaseQA.tsx`
- `editor/src/components/code/ExtensionsPanel.tsx`
- `editor/src/components/research/WritingPane.tsx`
- `editor/src/components/MentionAutocomplete.tsx`
- `editor/src/lib/sanitizeHtml.ts`

## Decisions

- Strict sandbox is opt-in (`DAN_STRICT_SANDBOX=1`) to avoid breaking existing deployments, but shell sandbox flips to on-by-default because the opt-out is documented and less disruptive.
- Builtins restriction uses the existing `_ALLOWED_BUILTINS` pattern from `server/exec.py` for consistency.

## Notes

- Added focused regressions in `tests/test_concierge/test_phase31_review_hardening.py` for strict sandbox enforcement, shell sandbox defaults, startup warnings, placeholder API key detection, and adversarial SQLite filter keys.
- Strict sandbox coverage now explicitly locks in the four main path cases: relative in-workspace pass, absolute in-workspace pass, absolute outside-workspace reject, and symlink traversal reject.
- Shell sandbox env parsing now treats unknown `DAN_SANDBOX_SHELL` values as the safe default (`on`) and only disables sandboxing for explicit false values like `0` / `false`.
- Added `editor/src/lib/sanitizeHtml.ts` plus `editor/src/lib/__tests__/sanitizeHtml.test.ts`, and routed every current editor `dangerouslySetInnerHTML` sink through the shared sanitizer so chat/research/marketplace HTML rendering strips unsafe handlers and protocols while preserving the app's `data-*` hooks.

- Electron IPC write handlers now share the same workspace-root + strict-sandbox path validation as the backend `validate_path()`. `validateWritePath()` in `main.ts` resolves symlinks via `fs.realpathSync`, checks `DAN_STRICT_SANDBOX` + `DAN_WORKSPACE_ROOT`, and blocks writes/deletes/renames outside the workspace. This also fixed `search:replaceInFile` which was not even using `expandHome()` before.
- `tests/test_security/test_exec_builtins_audit.py` provides a permanent AST-level guard: any new `exec()`/`eval()` call in `src/dan/` without `__builtins__` restriction within 20 lines of context will fail CI. The exemption list must be explicitly maintained.
- All 206 shell-related tests pass with `DAN_SANDBOX_SHELL=1` across 8 test files (5 core + 3 scenario/policy).

## Estimate

~1.5 days
