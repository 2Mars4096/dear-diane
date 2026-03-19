# 38-6: Security Hardening

**Parent:** [38-review-hardening](38-review-hardening.md)
**Status:** not-started
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
- [ ] 1-1. Add `DAN_STRICT_SANDBOX` env flag (default off for backward compatibility, documented in `.env.example`).
- [ ] 1-2. When enabled, `validate_path()` rejects absolute paths outside the workspace root for write/delete operations (reads can optionally warn).
- [ ] 1-3. Ensure the Electron bridge's write confirmation dialog and the backend sandbox produce consistent behavior.
- [ ] 1-4. Add tests for sandbox enforcement: relative paths pass, absolute-in-workspace passes, absolute-outside-workspace is rejected, symlink traversal is caught.

### 2. `exec()` builtins restriction audit
- [ ] 2-1. Update `diagnosis.py:1005` and `diagnosis.py:1084` to pass `{"__builtins__": _ALLOWED_BUILTINS}` matching the pattern in `server/exec.py`.
- [ ] 2-2. Update `chat_manager.py:4558` (`_exec_deterministic_builder_code`) to use restricted builtins.
- [ ] 2-3. Audit for any other `exec()` / `eval()` calls outside `server/exec.py` and `engine/conditions.py` that lack builtins restriction.
- [ ] 2-4. Add a test or lint rule that flags new `exec(code, ns)` calls without `__builtins__` override.

### 3. Shell tool default to sandbox mode
- [ ] 3-1. Flip `DAN_SANDBOX_SHELL` default to `"1"` (sandbox on by default).
- [ ] 3-2. Document the opt-out clearly in `.env.example` and README.
- [ ] 3-3. Add a startup warning when sandbox mode is explicitly disabled.
- [ ] 3-4. Ensure existing shell tool tests pass with sandbox on.

### 4. HTML sanitization
- [ ] 4-1. Add `dompurify` as an editor dependency.
- [ ] 4-2. Create a shared `sanitizeHtml()` utility in `editor/src/lib/`.
- [ ] 4-3. Replace all 9 `dangerouslySetInnerHTML` usages with the sanitized wrapper: `ChatMessage.tsx` (3), `ChatSidebar.tsx`, `ExplainPopup.tsx`, `CodebaseQA.tsx`, `ExtensionsPanel.tsx`, `WritingPane.tsx`, `MentionAutocomplete.tsx`.
- [ ] 4-4. Add a test that verifies XSS vectors (event handlers, SVG, javascript: protocol) are stripped.

### 5. SQL parameterization in learning_tiers.py
- [ ] 5-1. Replace the f-string `json_extract(data, '$.{key}')` with parameterized column access or add an explicit comment explaining why the regex is security-critical.
- [ ] 5-2. Add a test with an adversarial key that would exploit unvalidated interpolation, confirming the regex blocks it.

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

## Decisions

- Strict sandbox is opt-in (`DAN_STRICT_SANDBOX=1`) to avoid breaking existing deployments, but shell sandbox flips to on-by-default because the opt-out is documented and less disruptive.
- Builtins restriction uses the existing `_ALLOWED_BUILTINS` pattern from `server/exec.py` for consistency.

## Estimate

~1.5 days
