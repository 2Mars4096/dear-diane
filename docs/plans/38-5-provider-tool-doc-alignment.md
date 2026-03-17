# 38-5: Provider, Tool & Doc Alignment

**Parent:** [38-review-hardening](38-review-hardening.md)
**Status:** not-started
**Goal:** Restore Google provider regression coverage, improve clipboard error messaging, and align README safety claims with actual file-tool behavior.

## Context

Three loosely related alignment issues from the review:
1. Google provider tests reference a removed `_to_gemini_messages()` helper and fail before timeout paths are exercised due to missing `_protos`.
2. Clipboard tool reports empty error strings when the clipboard utility exists but is unavailable (e.g. headless sessions).
3. README claims tools are "all sandboxed to workspace root" but `_workspace.py` allows explicit absolute paths outside the workspace.

Relevant code:
- `src/dan/providers/google_provider.py`: `_to_gemini_messages()` (L78–148, classmethod compat), `_to_gemini_contents()` (L213–276), `complete()` (L396)
- `tests/test_providers/test_google_provider.py`, `tests/test_provider_timeouts.py`
- `src/dan/tools/clipboard.py`: `_find_clipboard_cmd()` (L39–49), error at L69 can produce empty message
- `src/dan/tools/_workspace.py`: `validate_path()` (L15–36) logs but allows absolute paths outside workspace
- `README.md` (L14: "all sandboxed to workspace root")
- `docs/llm-api-guide.md` (L987: "paths outside the workspace are rejected")

## Tasks

- [ ] 1. Confirm Google provider test realignment
  - [x] 1-1. ~~Update tests that reference `_to_gemini_messages`~~ — already done: the 2026-03-17 compat patch restored `_to_gemini_messages()` as a classmethod (L78–148) alongside the newer `_to_gemini_contents()` (L213–276)
  - [x] 1-2. ~~Fix the timeout test~~ — already done: `complete()` now falls back to `start_chat(...).send_message_async(...)` when `generate_content_async` is unavailable, so the timeout test no longer trips on `_protos`
  - [ ] 1-3. Verify all provider tests pass: `pytest -q tests/test_providers/test_google_provider.py tests/test_provider_timeouts.py` (confirmation only)
- [ ] 2. Improve clipboard error messaging
  - [ ] 2-1. When `_find_clipboard_cmd()` finds `pbcopy` but the command fails at runtime, produce a descriptive error ("clipboard utility exists but is unavailable in this session") instead of an empty string
  - [ ] 2-2. Add a capability pre-check that tests clipboard availability before claiming the tool is functional
  - [ ] 2-3. Fix or update `tests/test_tools/test_builtin_tools.py::TestClipboard::test_copy_text` for headless environments
- [ ] 3. Align README safety claims with actual behavior
  - [ ] 3-1. Decide the intended contract: true workspace sandbox vs. "relative paths sandboxed, absolute paths trusted"
  - [ ] 3-2. Update README to accurately describe the chosen contract
  - [ ] 3-3. If the chosen contract is "absolute paths trusted", add a note about what that means for untrusted LLM callers
  - [ ] 3-4. Align `docs/llm-api-guide.md` tool descriptions with the same contract language — currently L987 says "paths outside the workspace are rejected" which is inaccurate (`validate_path()` logs but allows absolute paths outside the workspace)

## Decisions

- (filled in during execution)

## Notes

- The Google provider compat patch from today restored `_to_gemini_messages()` as a classmethod and added a `start_chat` fallback in `complete()`. Code inspection confirms tasks 1-1 and 1-2 are done; task 1-3 (run the tests) is a confirmation step only.
- `_workspace.py` has an explicit test (`TestWorkspaceSandbox::test_absolute_path_outside_workspace_allowed`, L329–339) confirming absolute paths outside the workspace are intentionally allowed — so the README/guide mismatch is a documentation issue, not a code bug.
- The module audit recommends the UI warn when a tool call operates outside trusted roots if absolute paths are allowed. That's a UX enhancement beyond this plan's scope.
