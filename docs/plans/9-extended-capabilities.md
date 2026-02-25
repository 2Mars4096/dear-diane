# 9: Phase 6 -- Extended Capabilities

**Status:** completed
**Goal:** Add new node types and execution modes following existing engine patterns. Three key missing capabilities for real workflows: first-class RAG retrieval, trustworthy subprocess execution, and schema-validated handoffs between agents.

## Sub-Plans

| # | Sub-Plan | Scope | Primary Files |
|---|----------|-------|---------------|
| [9-1](9-1-rag-knowledge-retrieval.md) | RAG / Knowledge Retrieval Node | EmbeddingProvider protocol, VectorStore abstraction (FAISS/ChromaDB/local), RAGOperator model + executor, index lifecycle, editor/builder integration | new `src/dan/rag/`, `models/nodes.py`, `executors/rag.py` |
| [9-2](9-2-subprocess-sandbox.md) | Subprocess Sandbox | SandboxRunner (asyncio subprocess), resource limits, language adapters, CodeExecutor upgrade, shell_command hardening | new `src/dan/sandbox/`, `executors/code.py`, `tools/shell_command.py` |
| [9-3](9-3-handoff-validator.md) | Handoff Validator Node | ValidatorNode model + executor, rule types (required_keys, schema, expression), boundary auto-insert, editor/builder integration | `models/nodes.py` or `models/control_flow.py`, new `executors/validator.py` |

## Dependencies / Sequencing

All three are independent with no hard dependencies between them. Execution benefits from ordering:

**Recommended execution order:**
1. **9-3** first: smallest scope, quickest win, immediately hardens existing composite workflows
2. **9-2** second: trust/safety foundation; existing `CodeExecutor.exec()` and `shell_command` both need this for production use
3. **9-1** last: largest scope, highest value; benefits from stable sandbox (local embedding models run as subprocesses) and validators (chunk quality gates)

## Shared Decisions

- **New node types follow the established checklist.** Every new node type requires: Pydantic model in `models/`, executor in `executors/`, `registry.py` entry, `Node` discriminated union entry in `graph.py`, `DEFAULT_OUTPUT_PORTS` entry in `compiler.py`, builder method (`wf.xxx()`), decompiler case, `NODE_TYPE_CATALOG` + `NODE_DESCRIPTIONS` + `createDefaultNode` in the editor, ConfigPanel section, node icon, and tests.
- **Optional deps stay optional.** FAISS (`faiss-cpu`), ChromaDB (`chromadb`), and sentence-transformers are optional like `pypdf` and `duckduckgo-search` in Phase 4. Auto-discovery with graceful `ImportError` degradation. `pyproject.toml` gets new optional groups.
- **Sandbox provides operational guardrails, not security isolation.** `CodeExecutor` inline `exec()` fast-path is preserved as default. Subprocess mode is opt-in via `SandboxConfig` and provides timeouts, memory caps, output limits, and env filtering — but no OS-level isolation. Existing code nodes behave identically.
- **Validator is an explicit node, not hidden middleware.** Visible on the canvas with explicit wiring. Users choose where to validate. The boundary auto-insert utility is a convenience action (right-click, "Add Validators"), not automatic injection.
- **Engine-level APIs first, server endpoints second.** Index lifecycle, sandbox config, and validation rules are all usable from `Engine.run()` without a server. FastAPI endpoints are thin wrappers for the editor. Headless users are first-class.
- **Embedding provider supports both API and local models.** `EmbeddingProvider` protocol with `async embed(texts) -> list[list[float]]`. API-based (OpenAI `text-embedding-3-small`) uses HTTP. Local (sentence-transformers) wraps sync inference in `asyncio.to_thread()`. Registry design (extend `ProviderRegistry` vs. separate `EmbeddingRegistry`) deferred to 9-1 implementation.
- **Existing tool-based RAG is preserved.** `rag_qa.py` (file_read, text_chunk, LLM) continues unchanged. RAGOperator is a higher-level alternative for persistent vector indices, not a replacement.
- **Doc sync is explicit in every sub-plan.** Each sub-plan's final task group updates `architecture.md`, `llm-api-guide.md`, `README.md`, `todo.md`, and `changelog.md`.

## Notes

- `CodeOperator` already has `sandbox_config: dict[str, Any]`. 9-2 formalizes this into a typed `SandboxConfig` model while keeping backward compat.
- `shell_command` tool already uses `asyncio.create_subprocess_shell` with timeout. 9-2 extracts and generalizes this into `SandboxRunner`.
- `dan.tools.text_chunk` already handles chunking. 9-1 RAG pipeline reuses it rather than reimplementing.
- `validation/schema.py` already has `check_schema_compatible()` for port schema checking. 9-3 schema_conformance rule type reuses this.
- `conditions.py` already has safe expression evaluation. 9-3 custom_expression rule type reuses this.
- Docker-based isolation is out of scope (zero-dep principle). Can be added as a future `SandboxRunner` backend.
