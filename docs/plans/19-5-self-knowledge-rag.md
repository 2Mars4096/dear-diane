# 19-5: Self-Knowledge RAG

**Parent:** [19-meta-orchestrator](19-meta-orchestrator.md)
**Status:** in-progress
**Goal:** Ground the meta-orchestrator's planner in DAN's own documentation so it consistently uses correct node types, port names, edge patterns, builder API, and conventions when generating or adapting workflows.

## Problem

The `WorkflowPlanner` (19-2) generates workflows via LLM, but relies on a static system prompt that can drift from the actual API. When DAN adds new node types, edge patterns, or builder methods, the planner doesn't know about them unless someone manually updates the prompt. This causes:

- Hallucinated node types or port names
- Incorrect edge wiring patterns
- Missing use of newer capabilities (e.g., new compaction strategies, tier policies)
- Inconsistent use of builder DSL vs. markdown authoring surface

## Approach

Index DAN's own documentation (`llm-api-guide.md`, `architecture.md`, builder examples, tool schemas) into a dedicated RAG collection. Before every planning invocation, retrieve the most relevant API sections and inject them into the planner's context window. This keeps the planner grounded in the actual current API without requiring prompt maintenance.

## Primary Files

| File | Role |
|------|------|
| `src/dan/meta/self_knowledge.py` (new) | `SelfKnowledgeIndex` — indexing, refresh, retrieval |
| `src/dan/meta/planner.py` | Integrate retrieval into `WorkflowPlanner.plan()` |
| `src/dan/meta/discovery.py` | Extend `DiscoveryService` to include self-knowledge results |
| `docs/llm-api-guide.md` | Primary source document (already maintained) |
| `docs/architecture.md` | Secondary source document |
| `tests/test_meta/test_self_knowledge.py` (new) | Unit tests |

## Tasks

- [x] 1. **Design the indexing pipeline**
  - [x] 1-1. Define which documents to index: `llm-api-guide.md`, `architecture.md`, builder/loader examples from `examples/`, tool docstrings from `dan.tools`
  - [x] 1-2. Choose chunking strategy: section-level for docs (split on `##` headers), function-level for code examples, per-tool for tool schemas
  - [x] 1-3. Define metadata schema: `source_file`, `section_title`, `doc_type` (api_reference | architecture | example | tool_schema), `content_hash`

- [x] 2. **Implement `SelfKnowledgeIndex`**
  - [x] 2-1. Create `src/dan/meta/self_knowledge.py` with `SelfKnowledgeIndex` class
  - [x] 2-2. `index_docs(doc_paths: list[Path])` — read, chunk by `##` headers, embed, upsert into `_dan_self_knowledge` collection
  - [x] 2-3. `index_tool_schemas()` — iterate `ToolRegistry` or `get_all_tools()`, format metadata as text chunks, index
  - [x] 2-4. `index_examples(examples_dir: Path)` — scan for `.py` and `.md` workflow files, chunk and index
  - [x] 2-5. `retrieve(query: str, top_k: int = 8) -> list[RetrievedChunk]` — embed query, vector search, wrap as `RetrievedChunk`
  - [x] 2-6. `refresh()` — SHA-256 hash comparison, re-index only changed files; tools always re-indexed
  - [x] 2-7. Use existing `dan.rag` infrastructure (`Indexer`, `VectorStoreFactory`, `EmbeddingRegistry`) — no new vector store implementation

- [x] 3. **Integrate into planner**
  - [x] 3-1. Add `self_knowledge: SelfKnowledgeIndex | None` parameter to `WorkflowPlanner.__init__()`
  - [x] 3-2. In `WorkflowPlanner.plan()`, before LLM call: retrieve relevant API docs based on the user's goal + discovered tools/skills
  - [x] 3-3. Inject retrieved chunks into planner's system prompt under a `## DAN API Reference (retrieved)` section
  - [x] 3-4. Include chunk source attribution so the LLM knows which doc each section came from
  - [x] 3-5. Cap injected token budget (configurable, default ~4000 tokens) — use `estimate_tokens()` from `dan.utils.tokens`

- [x] 4. **Extend `DiscoveryService`**
  - [x] 4-1. Add `self_knowledge_chunks: list[RetrievedChunk]` field to `DiscoveryResult`
  - [x] 4-2. If `SelfKnowledgeIndex` is available, `DiscoveryService.discover_all()` calls `.retrieve()` and includes results
  - [x] 4-3. Planner prompt builder uses `discovery_result.self_knowledge_chunks` alongside tools/skills/patterns

- [x] 5. **Auto-refresh on server startup**
  - [x] 5-1. In `dan-serve` lifespan (`app.py`), initialize `SelfKnowledgeIndex` and call `refresh()` on startup
  - [x] 5-2. Optionally re-index on file change if `watchfiles` is available (non-blocking background task)
  - [x] 5-3. CLI path: `MetaController` can initialize index lazily on first planning request

- [ ] 6. **Tests**
  - [ ] 6-1. Unit: `SelfKnowledgeIndex` indexes a mock doc, retrieves relevant chunks
  - [ ] 6-2. Unit: `refresh()` is incremental — unchanged files not re-indexed
  - [ ] 6-3. Unit: token budget cap respected — injected context does not exceed limit
  - [ ] 6-4. Integration: planner with self-knowledge produces valid node types and port names
  - [ ] 6-5. Integration: planner without self-knowledge hallucinates more (comparative quality test, optional)

- [ ] 7. **Documentation**
  - [x] 7-1. Update `architecture.md` — document `self_knowledge.py` in meta module
  - [ ] 7-2. Update `llm-api-guide.md` — note that this doc is auto-indexed for the planner

## Decisions

- Used SHA-256 content hashes (not `last_modified` timestamps) for incremental refresh — more reliable across file systems and git checkouts.
- `index_docs` uses `Indexer.create_index()` (creates collection + adds); `index_tool_schemas` and `index_examples` use `Indexer.add_documents()` (append to existing collection). All methods are idempotent via upsert-by-ID.
- Markdown section splitting via regex `(?=^## )` preserves headers in chunk text so embeddings capture the topic.
- Tool schemas always re-indexed on `refresh()` since they have no file path to hash.
- `format_for_prompt()` uses `estimate_tokens()` from `dan.utils.tokens` with greedy packing — adds chunks in retrieval-score order until budget exhausted.

## Notes

- The existing RAG infrastructure (`dan.rag`) handles embeddings, vector stores, and indexing. This plan composes those primitives — no new embedding or vector store code needed.
- The self-knowledge collection is separate from user workflow RAG collections to avoid cross-contamination.
- Chunk quality matters more than quantity. Section-level splits on `##` headers in the API guide naturally produce coherent, self-contained API reference chunks.
