# 50: Mutation Port Alias Repair

**Status:** completed
**Goal:** Keep workflow-preview dry-runs from failing on common stale source-port aliases that the backend can repair deterministically.

## Tasks
- [x] 1. Normalize stale tool output aliases during mutation-preview compilation
  - [x] 1-1. Detect `add_edge` ops that reference missing source ports for known tool nodes.
  - [x] 1-2. Rewrite unambiguous aliases such as `http_request.response -> body` before dry-run.
- [x] 2. Add focused regression coverage
  - [x] 2-1. Cover `replace_body_graph` previews that add `http_request` nodes and wire `response`.
  - [x] 2-2. Re-run the existing mutation-preview repair slice.

## Decisions
- Port-alias repair should stay conservative: only rewrite a small set of known stale aliases when the node/tool shape makes the replacement unambiguous.
- The repair belongs in chat-side mutation-preview normalization, not in user prompts, because the backend already knows the canonical tool manifests.

## Notes
- This specifically unblocks equity-workflow previews that were still failing dry-run on `fetch_* .response` edges even after duplicate-edge repair landed.
