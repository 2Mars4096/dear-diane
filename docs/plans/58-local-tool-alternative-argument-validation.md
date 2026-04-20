# 58: Local Tool Alternative Argument Validation

**Status:** completed
**Goal:** Make the shared local tool loop enforce alternative JSON-schema argument sets so OpenAI-compatible models recover from malformed tool calls instead of silently no-oping.

## Tasks
- [x] 1. Add generic local-runtime validation for alternative argument groups
  - [x] 1-1. Validate `anyOf` / `oneOf` groups that express "one of these argument sets must be present"
  - [x] 1-2. Surface those failures through the existing `tool_arguments_invalid:` repair path
- [x] 2. Tighten `web_search`'s shared schema
  - [x] 2-1. Express that `web_search` requires either `query` or `url`
  - [x] 2-2. Keep the runtime-side alias normalization for `queries -> query`
- [x] 3. Lock the behavior with focused regressions
  - [x] 3-1. Reject empty `web_search` calls directly at the runtime seam
  - [x] 3-2. Prove the tool loop nudges and recovers after an empty `web_search` call
  - [x] 3-3. Prove generic `anyOf` validation with an existing multi-shape tool (`file_edit`)
- [x] 4. Update tracking docs

## Decisions
- This is a shared tool-contract fix, not a provider-specific GLM/OpenRouter workaround.
- The runtime should fail closed on semantically empty tool calls instead of returning a zero-result success payload.
- Tightening the tool schema itself is preferable to only adding downstream repair text because compliant models can learn the contract before the first bad call.

## Notes
- Focused validation after implementation:
  - `python -m py_compile src/dan/worker/organisms/local_runtime.py src/dan/tools/web_search.py tests/test_worker/test_local_organism_runtime.py`
  - `PYTHONPATH=src pytest -q tests/test_worker/test_local_organism_runtime.py` (`39 passed`)
- Follow-up hardening after live GLM evidence:
  - Stop processing the rest of a tool-call batch after the first schema-invalid call so the repair hint arrives immediately instead of after multiple repeated failures from the same assistant response.
  - Strengthen the `web_search` correction nudge with an explicit valid JSON example and a direct instruction not to send `{}` or batch more empty `web_search` calls.
  - Revalidated with the same focused basket after adding a multi-invalid-call recovery regression (`41 passed`).
