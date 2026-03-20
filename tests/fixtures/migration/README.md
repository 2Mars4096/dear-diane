# Legacy Graph Fixtures

These files preserve representative workflow graph shapes that predate the
`dan_graph_v1` runtime IR.

They are intentionally **not** part of the canonical `graphs/*.json` corpus and
are **not** expected to load via `Graph.model_validate()` or the normal graph
API without a dedicated importer.

Use these fixtures for:

- regression tests that preserve historical pre-IR shapes,
- migration/importer work,
- documentation of older workflow JSON contracts.

Canonical `dan_graph_v1` samples live under `graphs/`.
