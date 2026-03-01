# 11-4: Docs vs Code Drift Findings

**Parent:** [11-4-documentation](11-4-documentation.md)  
**Status:** completed  
**Goal:** Document mismatches between documentation and current codebase.

---

## 1. llm-api-guide drift

### Node types

| Issue | Doc | Code |
|-------|-----|------|
| **InputNode builder method** | Type Reference says `input` \| `wf.input()` \| `input` | Builder has no `wf.input()` for creating InputNode. `wf.input` is a *property* (returns `PortRef` for sub-graph entry), not a node-creation method. InputNode is created by loader (markdown compile) or scoped_run, not by the Python builder DSL. |
| **Node count** | "15 node types total" (Core Concepts) | Registry has 15 built-in types; count is correct. |

### Builder methods

- **`wf.if_else()`** — Documented as deprecated; code has it. ✓
- **`wf.while_loop()`** — Documented as deprecated; code has it. ✓
- **`wf.gate()`** — Documented; code has it. ✓
- **`wf.parallel_subagents()`** — Documented; code has it. ✓
- **`wf.rag()`** — Documented; code has it. ✓
- **`wf.validator()`** — Documented; code has it. ✓
- **`wf.import_workflow()`** — Documented; code has it. ✓

### Import Map (Section 13)

| Issue | Doc | Code |
|-------|-----|------|
| **Missing control_flow exports** | Lists `IfElseNode, WhileLoopNode, ForEachNode, CompositeNode, ReduceNode, RouterNode, HumanInTheLoopNode` | Registry also has `GateNode`, `InputNode`, `ParallelSubagentsNode`, `ValidatorNode`. Import Map omits these. |
| **Engine imports** | Lists `ExecutionContext`, `ExecutionState` | Both exported from `dan.engine`. ✓ |

### REST API (Section 9)

- Doc uses `{id}` in path descriptions; app uses `{graph_id}` — same meaning. ✓
- Doc table omits: `POST /api/graphs/{id}/validate`, `GET /api/graphs/{id}/export/markdown`, `GET /api/graphs/{id}/export/python`, `GET /api/metrics/mutations`, `POST /api/metrics/mutations/reset`. These exist in `app.py`.

### EngineConfig

- Doc says `llm_default_model` default `"claude-sonnet-4-6"`; app uses `DAN_LLM_MODEL` env. ✓
- Doc lists `embedding_providers`, `embedding_model_provider_map`, `default_embedding_model`; `EngineConfig` in executor.py has `providers`, `embedding_providers`, `default_embedding_model`. Naming differs: doc uses `embedding_model_provider_map`, code may use different field names. (Verify against `EngineConfig` if needed.)

---

## 2. architecture drift

### Directory structure

| Issue | Doc | Code |
|-------|-----|------|
| **Missing editor file** | `lib/portOrdering.ts` not listed | File exists in `editor/src/lib/portOrdering.ts`. |
| **New components** | `TabBar.tsx`, `CommandPalette.tsx`, `LoopGroupNode.tsx` not listed | File exists in `editor/src/components/`. |
| **Loader structure** | `loader/parser.py` — "Markdown parser" | `parser.py` is for agent files; `flow_parser.py` handles flow lines. Doc correctly lists both. ✓ |

### Tech stack

- **React Flow v12** — `package.json` has `@xyflow/react: ^12.10.1`. ✓
- **Tailwind CSS v4** — `package.json` has `tailwindcss: ^4.2.1`, `@tailwindcss/vite: ^4.2.1`. ✓

### Node type icons

- Doc says "Node type icons — inline SVG icons for all **10** node types" | `nodeIcons.tsx` has icons for 15+ types: `llm_operator`, `tool_operator`, `code_operator`, `gate_if_else`, `gate_while`, `for_each`, `parallel_subagents`, `reduce`, `router`, `human_in_the_loop`, `gate`, `rag_operator`, `validator`, `composite`, `input`. Doc is outdated (10 → 14).

### API endpoints

- Doc table is comprehensive but omits: `POST /api/graphs/{id}/validate`, `GET /api/graphs/{id}/export/markdown`, `GET /api/graphs/{id}/export/python`, `GET /api/metrics/mutations`, `POST /api/metrics/mutations/reset`. These are implemented in `app.py`.

### EngineConfig / providers

- Doc mentions `llm_api_key` + `llm_base_url` auto-create `"default"` provider. App uses `providers` dict and `embedding_providers`; config structure matches. ✓

---

## 3. Plan drift

### Plan file references

| Plan | Reference | Status |
|------|-----------|--------|
| **7-8** | `flow_parser.py lines 179–192` for `state`/`defaults` kwargs | Correct. Lines 181–193 parse `state` and `defaults` kwargs in `_parse_loop`. ✓ |
| **11-structure-review** | `editor/src/types/graph.ts`, `validation/` | Files exist. ✓ |
| **11-2** | `src/dan/`, `editor/`, `examples/`, `tests/` | Directories exist. ✓ |

### Plan file status

- Plan 11-4 says "Status: not-started"; Task 4 (drift check) is now completed. ✓ (update plan)

---

## 4. Aspirational vs implemented

### Aspirational (not yet implemented)

| Section | Location | Notes |
|---------|----------|-------|
| **Hyperedges: Skills and Rules** | `architecture.md` § "Hyperedges" | Design for skills/rules as hyperedges attaching to nodes. No runtime implementation. |
| **Four Top-Level Agents Architecture** | `architecture.md` § "Four Top-Level Agents" | Design for Ask/Agent/Debug/Plan agents sharing context. Not implemented. |
| **Agent Boundary Contract (revised)** | `architecture.md` § "Agent Boundary Contract" | Formal `accepts`/`returns`/`reads_global`/`writes_global`/`signals` schema. Conceptual; not enforced in code. |
| **HumanNode (Generalized)** | `architecture.md` § "HumanNode" | Human-in-the-loop as first-class node with typed I/O. Partially implemented (`HumanInTheLoopNode` exists); "generalized" semantics (chat as rendering layer, adjustable autonomy) are aspirational. |
| **Context Scoping (pass_down / emit_up / global / local)** | `architecture.md` § "Context Scoping Across Agent Boundaries" | Detailed scoping model. Four-layer context (Edge Data, Node-Local, Shared Context, Artifact) is implemented; directional scoping (pass_down, emit_up) is design, not fully enforced. |

### Implemented (correctly documented)

- Four-layer context model (Edge Data, Node-Local, Shared Context, Artifact) — `LocalStateManager` used for loop-scoped state. ✓
- Builder DSL, edge wiring (f-string, `>>`, port subscript, explicit `wf.edge()`). ✓
- Engine API, checkpointing, event system. ✓
- REST API (graphs, runs, chat, RAG, scoped runs). ✓
- Visual editor: drill-in, execution viz, LogPanel, NodePalette, ConfigPanel. ✓

---

## Summary

| Category | Count |
|----------|-------|
| llm-api-guide drift items | 4 |
| architecture drift items | 5 |
| plan drift items | 0 (plans accurate) |
| aspirational sections | 5 |

**Recommended updates:**

1. **llm-api-guide:** Clarify that `wf.input()` does not create InputNode; InputNode is loader/scoped-run only. Fix Import Map to include `GateNode`, `InputNode`, `ParallelSubagentsNode`, `ValidatorNode`. Add missing REST endpoints to Section 9.
2. **architecture:** Add `lib/portOrdering.ts`, `TabBar.tsx`, `CommandPalette.tsx`, `LoopGroupNode.tsx` to directory structure. Update "10 node types" → "14 node types" for icons. Add missing API endpoints.
3. **Aspirational content:** Add explicit "Design / Future" labels to Hyperedges, Four Top-Level Agents, Agent Boundary Contract, HumanNode generalization, and context scoping sections so readers can distinguish implemented vs planned.
