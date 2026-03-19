# 12-10: Workflow Capability Follow-Ups

**Parent:** [12-cursor-parity-chat](12-cursor-parity-chat.md)
**Status:** completed
**Goal:** Keep workflow mutation/delete capabilities exposed accurately on follow-up chat turns and remove obsolete equity workflow fixtures from the local graph catalog.

## Tasks
- [x] 1. Add first-class workflow deletion capability to chat.
 - [x] 1-1. Register `delete_graph` in the chat capability registry for write-capable modes.
 - [x] 1-2. Return user-facing success/error messages for missing, invalid, and `_scratch` workflow deletes.
- [x] 2. Preserve workflow mutation access on follow-up turns.
 - [x] 2-1. Detect recent workflow activity from chat history.
 - [x] 2-2. Keep `plan_graph_mutations` visible for anaphoric workflow follow-ups like "does it work?" / "run it again".
- [x] 3. Add regression coverage for capability registration/handler behavior and follow-up mutation-tool exposure.
- [x] 4. Remove obsolete equity workflow graph JSONs that the user explicitly marked obsolete.

## Decisions
- `delete_graph` is exposed only in write-capable chat modes; read-only modes keep browse/query semantics.
- Follow-up mutation-tool exposure uses recent workflow-history signals plus workflow-oriented follow-up cues instead of enabling graph mutation for every workflow-linked conversation.

## Notes
- Validation: `python -m pytest tests/test_concierge/test_live_data.py -q`
- Validation: `python -m pytest tests/test_concierge/test_tiered_dispatch.py -q`
- Removed obsolete graphs: `684e883a8fbf.json`, `88940af5f7e5.json`, `95772eb73256.json`, `d91bcedb221a.json`, `deep-research-equity-research.json`, `equity_data_gatherer.json`, `equity_multi_ticker_comparison.json`, `equity_report_orchestrator.json`, `equity_section_analyst.json`, `f13047ccb312.json`
