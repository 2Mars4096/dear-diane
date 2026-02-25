# 7-4: Templates + Observability Polish

**Parent:** [7-core-hardening](7-core-hardening.md)
**Status:** completed
**Goal:** Ship 5 workflow templates that showcase the platform's capabilities, and extend per-node observability from run-level aggregates to node-level token counts and estimated cost.

## Tasks

- [x] 1. Workflow templates
  - [x] 1-1. `examples/simple_chain.py` — 3-node linear pipeline (LLM → LLM → Code). Demonstrates basic chaining, f-string data passing, and output normalization.
  - [x] 1-2. `examples/fan_out_fan_in.py` — ForEach + Reduce pattern. LLM generates a list, ForEach processes items in parallel, Code aggregates. Demonstrates `parallelism`, merge strategies.
  - [x] 1-3. `examples/review_revise.py` — WhileLoop review-revise. Draft → Review → WhileLoop(condition) → revise or done. Demonstrates while-loop iteration, condition evaluation, convergence.
  - [x] 1-4. `examples/rag_qa.py` — Tool-based RAG Q&A. Uses `dan.tools.file_read` + `dan.tools.text_chunk` to ingest local documents, pass chunks as context to LLM for question answering. No vector DB required.
  - [x] 1-5. `examples/react_agent.py` — ReAct agent loop. While-loop with LLM (think/act/observe) + ToolOperator dispatch. Uses `dan.tools` (web_search, web_fetch). Demonstrates tool-augmented reasoning.
  - [x] 1-6. Each template: runnable standalone (`python examples/X.py`), saves graph JSON to `graphs/`, includes CLI args for customization
  - [x] 1-7. Each template: includes a `# Architecture` comment block explaining the graph topology and design choices
- [x] 2. Template graph JSON export
  - [x] 2-1. Each template generates a corresponding `graphs/*.json` so the visual editor can load it
  - [ ] 2-2. Templates appear in the editor's "Saved Workflows" palette category — deferred (templates auto-appear when graph JSON is saved)
- [x] 3. Per-node token display
  - [x] 3-1. `handleRunEvent` in `useGraphStore.ts`: on `node_completed`, extract `usage` from event `metadata` and store in `nodeUsage: Record<string, {prompt_tokens, completion_tokens, total_tokens}>`
  - [x] 3-2. `DanNode.tsx`: show token badge on completed LLM nodes (e.g., "1.2k tok") next to existing duration badge
  - [x] 3-3. Badge tooltip shows breakdown: "Prompt: 800, Completion: 400, Total: 1,200"
- [x] 4. Per-node cost estimation
  - [x] 4-1. Duplicated cost table from `dan.providers.costs` into frontend as TypeScript constant `COST_PER_1K`
  - [x] 4-2. Compute cost from `model` + `usage` on node completion — store in `nodeCosts: Record<string, number>`
  - [x] 4-3. `DanNode.tsx`: show cost badge on completed LLM nodes (e.g., "$0.03") — only if cost is computable (known model)
  - [x] 4-4. `RunSummaryBar`: add total estimated cost to existing elapsed time + token display
- [x] 5. LogPanel enhancements
  - [x] 5-1. Per-node log sections show token count and cost for LLM nodes (in NodeGroup header)
  - [ ] 5-2. Add sortable columns: node name, duration, tokens, cost — deferred (low priority, existing grouped view is sufficient)
- [x] 6. Tests
  - [x] 6-1. Each template: unit test verifying graph compilation, node types, entry/exit points, JSON round-trip
  - [x] 6-2. Each template: mock e2e run (like existing `test_paper_writing_e2e.py` pattern)
  - [x] 6-3. `rag_qa` template: verify tool-based RAG pipeline with mock file read + chunking
  - [x] 6-4. `react_agent` template: verify while-loop tool dispatch with mock tools
  - [ ] 6-5. Frontend: verify `nodeUsage` and `nodeCosts` populated from mock events — deferred (no frontend test framework set up)
- [ ] 7. Docs sync — skipped per task instructions (do not update changelog, todo, architecture, README)

## Decisions

- **Cost table duplication**: Duplicated `COST_PER_1K_TOKENS` from `src/dan/providers/costs.py` as a TypeScript constant in `useGraphStore.ts`. Avoids a new API endpoint; trade-off is manual sync when prices change. The `estimateCost` function uses prefix-matching like the Python version.
- **WhileLoop over GateNode for templates**: Used `wf.while_loop()` context manager in `review_revise.py` and `react_agent.py` for clearer topology definition. The engine handles WhileLoop nodes via `while_loop` scheduler.
- **rag_qa setup node**: Added a `setup` CodeOperator entry node to rag_qa to provide default `chunk_size`/`overlap` values rather than requiring graph-level inputs for tool parameters.
- **react_agent multi-tool dispatch**: Both `web_search` and `web_fetch` tools run on every iteration; the dispatch code sends empty strings to unused tools. Tools handle empty inputs gracefully.
- **nodeUsage/nodeCosts reset**: Added `nodeUsage: {}, nodeCosts: {}` reset to every location where `nodeTimings: {}` is reset (startRun, resumeRun, recoverActiveRun, openTab, replaceActiveTabGraph, refreshTab) to keep state consistent.
- **Deferred items**: Sortable log columns (5-2) and editor palette integration (2-2) deferred as low priority.

## Notes

- Templates should be self-contained and runnable with minimal setup — just `DAN_LLM_API_KEY` env var. Multi-provider templates should document which keys are needed.
- `rag_qa.py` deliberately avoids vector DB dependencies — it uses `dan.tools.pdf_read` + `dan.tools.text_chunk` for local document ingestion. This makes it zero-dependency beyond the LLM key. Phase 6's `RAGOperator` adds vector store support later.
- `react_agent.py` is the most complex template and serves as a validation that the platform can express agentic workflows. It should demonstrate tool selection, multi-turn reasoning, and graceful termination.
- Cost estimation is best-effort — unknown models show "—" instead of a cost. The cost table from 7-2 is the source of truth.
- Existing `paper_writing.py` remains as-is — it's a domain-specific example, not a general template.
