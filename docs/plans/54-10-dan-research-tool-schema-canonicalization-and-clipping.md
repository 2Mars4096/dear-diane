# 54-10: DAN Research Tool Schema Canonicalization And Clipping

**Parent:** [54-dan-control-plane-rewrite-around-universal-agents](54-dan-control-plane-rewrite-around-universal-agents.md)
**Status:** completed
**Goal:** Make DAN Research send canonical local-tool schemas to OpenAI-compatible models and trim the research tool surface so frontier models stop falling into malformed `web_search {}` loops.

## Tasks
- [x] 1. Canonicalize model-facing local tool schemas from runtime metadata
  - [x] 1-1. Replace placeholder request tool schemas with authoritative standalone tool metadata before provider calls
  - [x] 1-2. Prove `web_search` reaches the model with its real `query|url` contract instead of an empty `{properties:{}}` schema
- [x] 2. Narrow the DAN Research default tool surface
  - [x] 2-1. Drop git tools from the default research product tool list
  - [x] 2-2. Clip the bounded research organ to research-relevant read-only tools even when a broader tool bag is supplied upstream
- [x] 3. Strengthen the research-reader first-step contract
  - [x] 3-1. Tell readers to prefer one targeted `web_search` query for live external facts
  - [x] 3-2. Explicitly forbid empty `web_search {}` calls and direct uncertain readers to return `follow_up_queries` instead
- [x] 4. Revalidate locally and with one live OpenRouter GLM probe
- [x] 5. Update tracking docs

## Decisions
- The root cause was not GLM failing to understand the standalone `web_search` schema in isolation; it was DAN sending the model a degraded placeholder schema during real research runs.
- Runtime-side validation is necessary but insufficient when the model-facing schema is weaker than the runtime contract.
- DAN Research should default to a smaller tool surface than generic read-only organisms; git inspection is not part of the normal external-research happy path.

## Notes
- Focused local validation after the patch:
  - `python -m py_compile src/dan/worker/organisms/local_runtime.py src/dan/worker/organs/__init__.py src/dan/cli/research.py tests/test_worker/test_local_organism_runtime.py tests/test_cli/test_research.py`
  - `PYTHONPATH=src pytest -q tests/test_worker/test_local_organism_runtime.py tests/test_cli/test_research.py` (`79 passed`)
- Live GLM verification:
  - A capture provider showed that DAN previously sent `web_search` to the model as a placeholder schema with empty `properties`.
  - After canonicalization, the same capture showed full standalone `web_search` metadata, including `anyOf` for `query` or `url`.
  - A bounded OpenRouter `z-ai/glm-5.1` DAN Research probe then produced concrete first-step calls such as `{"query":"oil prices April 2026 Brent WTI current","search_depth":"thorough","fetch_content":true}` instead of `web_search {}`.
