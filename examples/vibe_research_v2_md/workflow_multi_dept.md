---
name: Vibe Research v2 — Multi-Department Adaptive (State Schema)
description: >
  v2 of the vibe_research workflow. Leverages state_schema (loop-scoped state),
  code node port defaults, and scope injection to eliminate all state-threading
  boilerplate. Runtime behavior is identical to v1.
  Orchestrator oversees 3 fixed departments (MOM, REV, FUND).
  Each iteration the orchestrator assigns themes and halt/continue decisions.
  Departments execute one-by-one (MOM→REV→FUND); results merge and governor
  decides whether to continue. Loop state managed via state_schema.
format_version: 1
tags: [vibe-research, multi-department, orchestrator, strategy-manager, state-schema, v2]
---

## Agents

- [entry](entry.md)
- [iteration](iteration.md)
- [write_csv](write_csv.md)

## Flow

entry | loop(iteration, until: "not try_more", max: 60, state: '{"results":{"type":"array"},"strategies_tried":{"type":"array"},"iteration":{"type":"integer"},"try_more":{"type":"boolean"},"start_year":{"type":"integer"},"end_year":{"type":"integer"},"max_factors":{"type":"integer"}}', defaults: '{"results":[],"strategies_tried":[],"iteration":0,"try_more":true,"start_year":2010,"end_year":2023,"max_factors":20}')
entry_loop_iteration.done → write_csv.input
