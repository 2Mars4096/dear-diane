# 52-7: Durable DAN Code Conversation Loop

**Parent:** [52-multicellular-composition](52-multicellular-composition.md)
**Status:** completed
**Goal:** Move `dan code` onto a durable orchestrator conversation loop so the shell can chat, clarify, launch bounded coding runs, and decide `continue / done / clarify` after validator-driven execution without leaving the universal-agent substrate.

## Tasks
- [x] 1. Add a reusable durable coding-conversation controller on top of the 52-6 runtime
  - [x] 1-1. Keep the orchestrator as a universal-worker agent backed by `DurableAgentRunner`
  - [x] 1-2. Teach the orchestrator to classify user turns as `respond`, `clarify`, or `code`
- [x] 2. Route bounded coding results back into the durable orchestrator
  - [x] 2-1. Review each bounded coding report through the same orchestrator session
  - [x] 2-2. Let the orchestrator decide `done`, `continue`, or `clarify` after validation instead of treating validator output as the final authority
- [x] 3. Update the DAN Code product shell to persist the durable conversation state
  - [x] 3-1. Persist conversation messages and pending clarification in `.dan-code/session.json`
  - [x] 3-2. Add `/clear` as a real conversation reset alias
- [x] 4. Lock the new shell behavior with focused CLI regressions
- [x] 5. Tighten the module seams so the shell stays stackable as building blocks
  - [x] 5-1. Move transient LLM API retry logic into the shared provider layer instead of the `dan code` control loop
  - [x] 5-2. Replace loose CLI/orchestrator dict payloads with explicit conversation-context and bounded-run-summary contracts

## Decisions
- `dan code` still uses the dedicated bounded coding organism for heavy implementation work; the new durable layer wraps that organism rather than replacing it.
- Workspace/result meta queries remain local shell shortcuts, but ordinary natural-language turns now go through the durable orchestrator instead of a canned local fast-path.
- The orchestrator is the public voice and decision-maker; worker/validator outputs are evidence routed back into it.
- The intended module stack is explicit and thin: `provider wrapper -> durable orchestrator -> bounded coding organism`. Each layer should only know the narrow contract of the layer below it.

## Notes
- `src/dan/worker/organisms/coding_conversation.py` is the new reusable durable control-plane helper for the coding shell.
- `src/dan/cli/code.py` now persists conversation state, routes ordinary turns through the durable orchestrator, and only launches the bounded coding organism when the orchestrator chooses `code`.
- After each bounded coding run, the CLI sends a structured report summary back to the same orchestrator session so it can decide whether to stop, continue with one more bounded pass, or ask a clarifying question.
- `src/dan/providers/retrying_provider.py` now keeps transient LLM API retries in the shared provider layer, so Kimi/OpenAI-compatible failures can recover without baking retry loops into the orchestrator or CLI.
- The CLI/orchestrator seam is now typed through `CodingConversationContext` and `CodingConversationReportSummary` instead of ad hoc dict payloads, which keeps the control-plane diagrammable as small reversible modules.
- Validation:
  - `PYTHONPATH=src pytest -q tests/test_provider_retries.py tests/test_provider_timeouts.py tests/test_openai_provider.py tests/test_cli/test_code.py tests/test_worker/test_core_executor.py tests/test_worker/test_coding_organism.py tests/test_worker/test_local_organism_runtime.py` (`53 passed`)
  - `PYTHONPATH=src python -m py_compile src/dan/providers/retrying_provider.py src/dan/providers/factory.py src/dan/llm_core/factory.py src/dan/worker/organisms/coding_conversation.py src/dan/worker/organisms/__init__.py src/dan/worker/__init__.py src/dan/cli/code.py tests/test_provider_retries.py`
