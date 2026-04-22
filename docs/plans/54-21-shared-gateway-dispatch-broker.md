# 54-21: Shared Gateway Dispatch Broker

**Parent:** [54-dan-control-plane-rewrite-around-universal-agents](54-dan-control-plane-rewrite-around-universal-agents.md)
**Status:** completed
**Goal:** Add one organism-agnostic API dispatch broker behind `ModelGateway` so queueing, in-flight caps, request pacing, and live CLI provider resolution are shared across gateway-backed surfaces instead of being patched per product shell.

## Tasks
- [x] 1. Add the shared dispatch broker at the `llm_core` boundary
  - [x] 1-1. Introduce a loop-local shared dispatcher behind `ModelGateway`
  - [x] 1-2. Let real provider attempts, retries, and fallback attempts all pass through the same broker
  - [x] 1-3. Surface queue wait / dispatch metadata on `GatewayCall`
- [x] 2. Cut live CLI provider resolution over to the gateway-backed seam
  - [x] 2-1. Add one shared `src/dan/cli/live_gateway.py` helper
  - [x] 2-2. Route the tracked live CLI surfaces in this slice (`dan code`, `dan research`, and `dan organism`) through that helper
- [x] 3. Lock the slice with focused validation and operator docs
  - [x] 3-1. Add gateway dispatch regressions for shared concurrency, queue rejection, telemetry, and rate pacing
  - [x] 3-2. Add CLI tests for the shared live-gateway helper and wrapper entry points
  - [x] 3-3. Document the `DAN_LLM_GATEWAY_*` knobs and the shared live-provider path in README/architecture/todo/changelog/bugs

## Decisions
- The broker lives at the shared `llm_core` boundary, not inside any one organism or product shell.
- The first slice is loop-local and process-shared for gateway users, which keeps async coordination simple and matches the current CLI/server runtime model.
- Request pacing is optional and env-driven; queueing and in-flight caps are shared defaults rather than product-specific policy.

## Notes
- New env knobs: `DAN_LLM_GATEWAY_DISPATCH_ENABLED`, `DAN_LLM_GATEWAY_DISPATCH_GROUP`, `DAN_LLM_GATEWAY_MAX_IN_FLIGHT`, `DAN_LLM_GATEWAY_MAX_QUEUE_SIZE`, `DAN_LLM_GATEWAY_MAX_REQUESTS_PER_SECOND`, and `DAN_LLM_GATEWAY_QUEUE_TIMEOUT_SECONDS`.
- `src/dan/llm_core/gateway/dispatch.py` is the new broker, while `src/dan/cli/live_gateway.py` keeps the committed live CLI surfaces on the gateway-backed provider adapter seam instead of raw registry resolution.
- Focused validation: `python -m py_compile src/dan/llm_core/config.py src/dan/llm_core/gateway/dispatch.py src/dan/llm_core/gateway/__init__.py src/dan/llm_core/types.py src/dan/cli/live_gateway.py src/dan/cli/code.py src/dan/cli/research.py src/dan/cli/organism.py tests/test_llm_core/test_gateway.py tests/test_cli/test_live_gateway.py` and `PYTHONPATH=src:. pytest -q tests/test_llm_core/test_gateway.py tests/test_cli/test_live_gateway.py tests/test_cli/test_organism.py tests/test_cli/test_code.py tests/test_cli/test_research.py -k 'live or gateway or dispatch'` (`32 passed, 72 deselected`).
