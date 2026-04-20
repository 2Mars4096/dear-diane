# 56: DAN Research Model-Call Observability

**Status:** completed
**Goal:** Make DAN Research control-stage planner/review waits diagnosable by logging richer API-call stats, surfacing visible streamed output in `--show-model-trace`, and reducing overly aggressive inner hedge retries without changing the outer stall/max caps.

## Tasks
- [x] 1. Enrich controller model-call events with request/response stats
  - [x] 1-1. Emit message-count / input-size / timeout / request-mode details on `model.requested`
  - [x] 1-2. Emit usage / output-size / provider metadata summaries on `model.responded`
- [x] 2. Preserve effective OpenAI-compatible request details for postmortem analysis
  - [x] 2-1. Record effective request metadata in `OpenAIProvider` result metadata
- [x] 3. Surface visible streamed output on the research CLI trace path
  - [x] 3-1. Render `model.stream.started|delta|completed` in `ResearchProgressRenderer`
  - [x] 3-2. Keep the stream public-text only; no hidden thinking exposure
- [x] 4. Slow the research-specific inner hedge stagger without changing outer control-stage caps
  - [x] 4-1. Raise the default research controller hedge delay from `2s` to `10s`
- [x] 5. Lock the behavior with focused tests and update tracking docs

## Decisions
- The next step for planner stalls is observability first, not another structural rewrite and not a smaller outer cap.
- Public streamed trace output may show visible text deltas only; hidden chain-of-thought remains out of scope.
- DAN Research should keep the outer `20s` stall / `180s` max control-stage guard unchanged while using a less aggressive inner hedge delay.

## Notes
- Focused validation after implementation:
  - `python -m py_compile src/dan/worker/organisms/coding_conversation.py src/dan/providers/openai_provider.py src/dan/worker/organisms/research_conversation.py src/dan/cli/research.py tests/test_worker/test_coding_conversation.py tests/test_openai_provider.py tests/test_cli/test_research.py`
  - `PYTHONPATH=src pytest -q tests/test_worker/test_coding_conversation.py tests/test_openai_provider.py tests/test_cli/test_research.py` (`72 passed`)
