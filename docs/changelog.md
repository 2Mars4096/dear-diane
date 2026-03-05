# Changelog

## 2026-03-05

- [docs] Synced `docs/todo.md` with the new cross-phase wave plans by adding a dedicated section for [22-deferred-wave-1](plans/22-deferred-wave-1.md) and [22-deferred-wave-2](plans/22-deferred-wave-2.md), so the 22-* work is tracked in the main roadmap list (not only backlog cross-links).

- [fix] **Code review round 2 — 12 fixes patched** (1 critical, 11 important):
  - *Backend (6 fixes):*
  - **C1**: TierScorer cache key regression — `(len(nodes), len(edges))` collides across subgraphs with same counts; replaced with content-based `_graph_fingerprint()` using sorted node/edge IDs
  - **I1**: Budget advisory mutated graph nodes permanently via `setattr`; changed to pass advisory through `inputs["__advisory_target_tokens__"]`
  - **I2**: DIFF_BASED compaction was no-op for AgentTeam (same-length output); added `has_structural_changes` guard for `__summary__`/`__unchanged__` entries
  - **I3**: `SemanticCache._save_payload` used bare `write_text()`; replaced with atomic `tempfile.mkstemp` + `os.replace`
  - **I4**: State externalization `except Exception: pass` blocks now log at debug level
  - **I5**: `_safe_async` logging raised from `debug` to `warning` so background learning failures are visible in production
  - *Frontend (6 fixes):*
  - **I-R2-1**: `ChatMessage.tsx` still imported full highlight.js; created shared `lib/hljs.ts` with core + 15 languages, both components now import from it
  - **I-R2-2**: `VALID_TIERS` Set hoisted to module scope (was allocated on every WS event)
  - **I-R2-3**: `nodeTiers` spread made conditional — same reference preserved when no tier change, avoiding unnecessary re-renders
  - **I-R2-4**: Dead per-node effectiveness calculation removed (was immediately overwritten by global recalculation)
  - **I-R2-5**: Added "Re-enable" button for disabled optimization rules
  - **I-R2-6**: Edge token computation reduced from O(E²) to O(E) using pre-built lookup maps
  - Full suite: 2734 passed, 0 regressions. Editor build: 0 new errors.

- [fix] **Code review round 1 — 15 fixes patched** (1 critical, 12 important, 2 minor):
  - *Frontend (7 fixes):*
  - **I-1**: `highlight.js` full bundle (1MB+) replaced with core + 9 language imports in `MentionAutocomplete.tsx`
  - **I-2**: `RulesDashboard` disabled-rules state lifted to parent so it survives tab switches
  - **I-3**: Dead `formatTokenCount` branch (>= 10k) replaced with `>= 1M` branch in `AnimatedEdge` + `TokenAnalyticsPanel`
  - **I-4**: Tier event payload validated against `VALID_TIERS` set before `as` cast in `useGraphStore`
  - **I-5**: IIFE in `DanNode.tsx` tier badge extracted to local variables, avoiding per-render function allocation
  - **I-7**: Unused `SAVINGS_FACTORS.factor` field removed, type simplified to `Record<string, string>`
  - **I-8**: `statusColors` hoisted from `.map()` callback to module-scope `STATUS_COLORS` constant
  - *Backend (8 fixes):*
  - **C1**: `AgentTeamExecutor._apply_turn_compaction` crash when `LoopCompactor` produces synthetic `__summary__`/`__unchanged__` dicts; now filters and converts to system messages.
  - **I1**: `ModelSelector.select()` polymorphic return (str | tuple) replaced with `SelectionResult` dataclass; all callers updated.
  - **I2**: 4 `asyncio.ensure_future` fire-and-forget patterns in `run_manager.py` replaced with `get_running_loop().create_task()` + `_safe_async` wrapper; dead `run_until_complete` branches removed.
  - **I3**: Duplicate `TOKEN_BUDGET_ADVISORY` / `BUDGET_ADVISORY` event types unified — `TOKEN_BUDGET_ADVISORY` is now an alias for `BUDGET_ADVISORY`.
  - **I4**: `_should_merge` Jaccard thresholds tightened from 0.5→0.7 (action) and 0.5/0.3→0.7/0.5 (condition+action); added exact-match requirement for short (<5 word) texts.
  - **I5**: `TierScorer` cache key changed from `id(graph)` (identity) to `(len(nodes), len(edges))` (content-based).
  - **M1**: Late runtime imports moved to module level in `scheduler.py` (`NodeExecutionSummary`) and `control_flow.py` (`LoopIterationState`, `TeamTurnState`, `LoopCompactor`).
  - **M3**: `_merge_principles` text growth capped at 2000 chars per field.
  - Tests updated to match new `SelectionResult` return type and `TOKEN_BUDGET_ADVISORY` alias. Full suite: 2734 passed, 0 regressions. Editor build: 0 new errors.

- [feat] **Deferred Completion Wave 2 (Plan 22-2)** — 3 parallel streams: S1 closes 18-5 docs/editor (TierPolicy docs, tier badge, tier analytics), S2 adds 4 runtime features (state externalization, advisory token budget, tool DSL, principle compaction), S3 adds 3 frontend analytics features (edge token labels, rule dashboard, before/after estimates). 25 new tests, 2734 total, 0 regressions. Phase 10 fully closed.
- [feat] **Deferred Runtime Features Wave 2** — 4 features with 25 new tests (2734 total, 0 regressions):
  - **Automatic state externalization** (18-3 task 1-3): Scheduler writes `NodeExecutionSummary` (with `duration_ms`, `cost`, `output_keys`) to `StateStore` after each node. `WhileLoopExecutor`, `ForEachExecutor`, and `AgentTeamExecutor` write `LoopIterationState`/`TeamTurnState` per iteration/branch/turn. All emit `STATE_EXTERNALIZED` events. Graceful no-op when `state_store` is None.
  - **Run-level advisory token budget** (18-3 task 4-1): Engine emits `BUDGET_ADVISORY` event at run start with total budget and per-node allocation. Logs warning if actual usage exceeds budget at run end. Purely advisory — never blocks.
  - **Builder DSL for tools** (7-3 task 8-3): Added `config` alias parameter to `WorkflowBuilder.tool()` for convenience (`wf.tool("step", tool_id="file_read", config={"path": "/tmp/data.txt"})`).
  - **Principle compaction** (17-2 task 3-4): `PrincipleStore.compact()` groups by first tag, merges principles with >50% action/condition word overlap (combined text, max confidence, union tags). Wired into `_persist_reflection_principles` in `run_manager.py`.
  - Tests: `test_state_externalization.py` (9), `test_tool_dsl.py` (7), `test_principle_compaction.py` (9)

- [feat] **18-5 task 7: Documentation and editor integration for model tiering** — completes Plan 18-5 tasks 7-1, 7-2, 7-3:
  - **docs/llm-api-guide.md**: New Section 7d "Task-Level Model Tiering" covering TierPolicy usage, 4 tiers with default model mappings, scoring formula (difficulty/impact/recoverability), custom weights, `task_tier` override, custom tier maps, per-tier params, escalation behavior, events, and EngineConfig fields. Updated event types list, EngineConfig table, and import map.
  - **DanNode.tsx**: Tier badge (L0/L1/L2/L3) with color-coded styling (green/blue/orange/red) shown in the status bar during/after runs. Hover tooltip shows full tier scoring breakdown (score, difficulty, impact, recoverability, model).
  - **TokenAnalyticsPanel.tsx**: New "Model Tiering" section showing tier distribution counts, per-node tier table (sortable by score, with D/I/R columns), and cost comparison vs uniform-model baseline.
  - **Store/types**: `TierInfo` type, `nodeTiers` state tracked from `model_selected` events.

- [fix] **18-5 code review round 2 — 5 issues patched** (2 important, 3 suggestions):
  - **Escalation gate now compares params** (important): `esc_model != model or esc_tier_params != tier_params` — Anthropic L2→L3 escalation (same model, different params like `extended_thinking`) now fires correctly
  - **TierScorer cache key includes weights** (important): cache keyed on `(graph_id, weights_key)` so per-node policy overrides with different weights get fresh scorers
  - **Plan decisions updated** to reflect floor-enforcement semantics for explicit `task_tier`
  - **BFS uses `deque.popleft()`** instead of `list.pop(0)` for O(1) queue ops in ImpactScorer
  - **Removed dead 2-tuple branch** in LLM executor tuple unpacking
- [feat] **Deferred Completion Wave 1 (Plan 22)** — 3 parallel streams completing highest-ROI deferred tasks across Phase 10 and Phase 7.2. 77 new tests (46 tier scorer + 31 token runtime), 3 frontend features. Full suite: 2709 passed, 0 failed.
  - **S1 (18-5 Tiering)**: Fixed `select_sync` bug in OrchestratorExecutor, floor enforcement, escalation cap verified, plan checkboxes synced, 46 scorer tests
  - **S2 (Token Runtime)**: Loop compaction (5 strategies) wired into WhileLoop/ForEach/AgentTeam executors, persistent cross-run cache, memory-aware invalidation, unified cross-source token budget, 31 tests
  - **S3 (Frontend UX)**: Recently used mentions, preview tooltip on hover, code syntax highlighting in mentions

- [feat] **Token Optimization Runtime (Plan 22 S2)** — deferred tasks from Plans 18-2 and 18-3:
  - **Loop compaction runtime**: Enhanced `LoopCompactor` with graceful fallback (logs warning + falls back to `none` instead of raising when persistent recall unavailable), `persist_evicted()` public method, structural summarize format (default cadence 5), diff_based `__unchanged__` markers
  - **WhileLoopExecutor**: Refactored `_apply_compaction` to delegate to `LoopCompactor`, removing inline compaction logic
  - **ForEachExecutor**: Added post-processing compaction via `_apply_compaction` — applies `CompactionRule` to accumulated branch results after fan-in
  - **AgentTeamExecutor**: Added `_apply_turn_compaction` — applies `LoopCompactor` to conversation messages after each turn with persistence + event emission
  - **Persistent cross-run cache**: `NodeResultCache` gains `persistent: bool` flag (defaults to `~/.dan/cache/`), `cache_ttl` default TTL, file mtime-based TTL check on disk reads
  - **Memory-aware cache invalidation**: `put()` accepts `memory_snapshot_hash`; `lookup()` compares stored hash with current — returns `memory_changed` reason on mismatch
  - **Unified cross-source token budget**: `TokenBudgetAdvisor.estimate_source_tokens()` estimates per-source-category tokens (edge_data, system_prompt, context_injection, hyperedge, memory, RAG); `compute_variable_budget()` subtracts fixed sources before allocating variable budget
  - 31 new tests (21 loop compactor + 10 cache); 2709 total tests pass, 0 regressions

- [feat] **Mention autocomplete UX polish** (12-3 tasks 3-3, 8-4, 8-5):
  - **Recently used mentions** (8-4): localStorage-backed "Recent" section at top of autocomplete dropdown; tracks last 10 selections as `{type, identifier, label}`; deduped by type+identifier; filtered by current query; recent items excluded from their regular sections to avoid visual duplication
  - **Preview tooltip on hover** (8-5): 300ms-debounced tooltip positioned to right (or left if constrained) of dropdown; @node shows type label, description, input/output port names (from graph data + NODE_DESCRIPTIONS fallback); @code shows hljs syntax-highlighted reference; @file shows truncated path; @docs/@chat/@workflow show names
  - **Code syntax highlighting in mentions** (3-3): @code mention pills in chat messages rendered with monospace font + dark background (`bg-gray-800 text-gray-200 font-mono`) via `<code>` wrapper; code mention tooltip uses highlight.js for syntax coloring
  - Files: `MentionAutocomplete.tsx` (rewritten with recent mentions, tooltip, shared `renderItemButton`), `ChatMessage.tsx` (code mention pill styling)

- [fix] **18-5 code review — 9 issues patched** (2 critical, 4 important, 3 suggestions):
  - **`_last_tier_result` concurrency hazard** (critical): `ModelSelector.select()` returns `(model, TierResult, tier_params)` tuple for tier strategy instead of storing on shared mutable instance state; eliminates race when parallel `asyncio.gather` LLM nodes overwrite each other's tier result
  - **`tier_params` wired end-to-end** (critical): `_select_tier()` resolves `resolve_tier_params()` and returns params; `LLMExecutor` threads them through `_call_llm` → `_call_via_provider` → `provider.complete()`/`stream()`, overriding `temperature`/`max_tokens` and passing extras like `extended_thinking`; escalation resolves fresh tier_params for the escalated tier
  - **TierScorer caching** (important): `ModelSelector` caches `TierScorer` by graph identity so `ImpactScorer._precompute()` runs once per graph instead of O(N) per LLM call
  - **ImpactScorer sub-graph penalty** (important): -0.15 now only applies to loop bodies (`__body` sub-graph keys), not all sub-graphs
  - **ImpactScorer transitive `feeds_human`** (important): uses reverse BFS from human nodes for multi-hop reachability, not just direct edge targets
  - **`_detect_providers` OpenAI false positive** (important): removed `getattr(config, "llm_api_key", "")` check; requires explicit `providers.openai` config or model names starting with `gpt`/`o1`/`o3`/`o4`
  - **`task_tier` Literal validation** (suggestion): changed from `str | None` to `Literal["micro", "routine", "reasoning", "critical"] | None` on 6 node types for Pydantic v2 validation
  - **`resolve_tier_map`/`resolve_tier_params` unit tests** (suggestion): 11 new tests for provider preference, partial overrides, fallback, and param merging
  - **Explicit `task_tier` bypass restored**: reverted floor-enforcement approach back to full scoring bypass as specified in plan — 2632 tests pass, 0 regressions

## 2026-03-04

- [feat] **Task-level model tiering (18-5)** — automatic cost-appropriate model assignment per LLM call:
  - `TierPolicy` strategy with `TaskTier` enum (micro/routine/reasoning/critical), `TierWeights`, per-provider tier maps
  - `TierScorer` with 3 sub-scorers: `DifficultyScorer` (node type + prompt/schema complexity), `ImpactScorer` (graph topology: terminal/fan-out/HumanNode), `RecoverabilityScorer` (retry/validator/loop signals)
  - `DEFAULT_TIER_MAPS` for Anthropic/OpenAI/Google ecosystems with `resolve_tier_map()` provider detection
  - `ModelSelector` gains `"tier"` branch, provider detection, tier map validation, `_last_tier_result` for event enrichment
  - `LLMExecutor` escalation: bumps tier on normalizer exhaustion, emits `tier_escalation` event, caps at 1 escalation per call
  - `task_tier` field on 6 LLM-using node types for explicit override
  - `ExecutionContext.graph` field + `EngineConfig.tier_map`/`tier_params` fields
  - 54 new tests (scorers, integration, escalation, backward compat); 2617 total tests pass, 0 regressions

## 2026-03-04

- [fix] **Phase 12 hardening — WebSocket rate limiting + adapter session isolation**
  - **WebSocket rate limiting**: `start` messages in the published WS handler now enforce `_check_rate_limit()`, matching the REST endpoints; returns `{"type": "error", "detail": "Rate limit exceeded"}` on 429
  - **Adapter renderer concurrency**: `_run_adapter_message_handler` now creates a **per-session** `MessagingHumanRenderer` from the adapter's transport, eliminating the race on shared `active_session_id` across concurrent conversations

- [fix] **Phase 12 code-review fix pass — all Critical + Important + Minor issues resolved**
  - **Publish router double-prefix (C1)**: Routes in `http_server.py` now use relative paths; router mounted once at `/api/published`
  - **Rate limiter bypass (C6)**: Per-workflow rate limiters cached in `PublishRegistry._per_workflow_limiters` instead of created per-request
  - **WebSocket auth bypass (C7)**: Auth check before `websocket.accept()` with 4001/4004 close codes
  - **Blocks API contract (C2)**: Frontend handles raw array; backend unchanged
  - **Adapter status contract (C3)**: Frontend uses `running`/`session_count` field names matching backend
  - **Block import/export interop (C4)**: Frontend sends JSON `{path}` for import, query params for export
  - **Adapter execution wiring (C5)**: `start_adapter` loads workflow, wires `MessagingHumanRenderer`, message callback runs engine; graceful fallback when workflow not found
  - **Block subgraph on drop (I1)**: `body_graph: "block:{name}@{version}"` sentinel; engine resolves via `BlockRegistry`; input/output ports derived from block schema
  - **Publish toolbar endpoints (I2)**: Corrected to `/graphs/{id}/publish-status`; added `GET /api/graphs/{id}/mcp-config` endpoint
  - **Adapter session scope (I3)**: Workflow loading optional (warning not error)
  - **Adapter protocol (I-new)**: Added `set_message_callback()` to `MessagingAdapter` protocol and all 3 implementations; replaced `hasattr` hack in server
  - **Telegram instance maps (M)**: `_session_map`/`_chat_map` moved to `__init__`
  - **Block export temp cleanup (M)**: `BackgroundTask` cleans temp dirs
  - **Palette CLI hint (M)**: `dan blocks install` → `dan-blocks install`
  - **Block list enrichment**: Backend now includes `input_schema`/`output_schema` in block listings
  - **Type safety**: `EngineConfig.block_registry` typed as `BlockRegistry | None`; `_on_new_message` typed as `Callable` on all adapters
  - Full test suite: **2563 passed, 0 failed**

## 2026-03-04

- [feat] **Task-level model tiering — core models & tier scorer (18-5, tasks 1 + 2-1..2-4)** — Added `TierWeights`, `TaskTier` enum, `TierPolicy` to `providers/model_policy.py`; extended `ModelPolicy` union. Created `providers/tier_defaults.py` with `DEFAULT_TIER_MAPS` (anthropic/openai/google), `DEFAULT_TIER_PARAMS`, `resolve_tier_map()`, `resolve_tier_params()`. Created `providers/tier_scorer.py` with `DifficultyScorer` (node-type base + schema/tool/prompt adjustments), `ImpactScorer` (precomputed graph topology walk), `RecoverabilityScorer` (retry/schema/loop/validator/cascade signals), and `TierScorer` (weighted combination → `TierResult` with tier + breakdown).

## 2026-03-04

- [docs] **Task-level model tiering plan (18-5)** — new sub-plan under Phase 10 (Token Optimization). Introduces `TierPolicy` strategy with 3-dimension scoring (difficulty, impact, recoverability) mapping to 4 model tiers (micro/routine/reasoning/critical). Includes adaptive escalation on failure and de-escalation over repeated success. Patches parent plan 18-token-optimization.md with fifth optimization lever, updated dependency graph, and success criteria.

## 2026-03-04

- [fix] **Adapter/workflow wiring, block drop, MCP config, and cleanup** — five targeted fixes:
  - **Adapter start → workflow execution (CRITICAL)**: `POST /api/adapters/start` now wires `MessagingHumanRenderer` and `AdapterSessionStore`; loads workflow from `workflow_path` (graph_id, JSON, .md, .py); sets `_on_new_message` callback to run engine on incoming messages; stores renderer+graph in `_adapter_renderers`; `_load_workflow_for_adapter()` helper for source detection
  - **GraphCanvas block drop**: Block drops now set `body_graph: "block:{name}@{version}"` as sentinel; engine scheduler resolves via `BlockRegistry` when `sub_graph_key.startswith("block:")`; added `block_registry` to `EngineConfig`; `input_mappings`/`output_mappings` on composite node
  - **Telegram adapter**: Moved `_session_map` and `_chat_map` from class-level to instance attributes in `__init__`
  - **Block export temp cleanup**: `export_block_endpoint` and `export_composite_block_endpoint` use `BackgroundTask` to run `shutil.rmtree` on temp dir after `FileResponse` is sent
  - **MCP config endpoint**: Added `GET /api/graphs/{id}/mcp-config`; frontend `getMcpConfig` now calls dedicated endpoint instead of publish-status

## 2026-03-04

- [fix] **Frontend-backend API contract alignment** — resolved 7 mismatches between editor and server:
  - **Blocks**: `fetchBlocks` now expects raw array (backend returns `Block[]`); `NodePalette` handles both array and wrapped responses
  - **Adapters**: `AdapterInfo` uses `running`/`session_count` (backend shape); `getAdapterStatus` returns raw array; `stopAdapter` calls `POST /api/adapters/stop` with `{adapter_id}` body; `EditorToolbar` uses `adapter.running` and `adapter.session_count`
  - **Publish**: `getPublishStatus` calls `/graphs/{id}/publish-status`; `PublishStatus` interface matches backend (`graph_id`, `published`, `workflow_id`, `config`)
  - **MCP config**: `getMcpConfig` uses publish-status endpoint and extracts `config` (no dedicated mcp-config endpoint)
  - **Block import**: `importBlock` sends JSON `{path}` (backend expects path); accepts `File | string` (File uses `file.name` as path)
  - **Block export**: `exportBlock` sends `name`/`version` as query params (backend reads from query)
  - **NodePalette**: CLI hint typo `dan blocks install` → `dan-blocks install`

## 2026-03-04 (server integration)

- [feat] **Deferred server integration endpoints** — wired publish, blocks, and adapters into `dan-serve` (Plans 21-3/21-4/21-5):
  - **Publish router mount** (21-3 task 7): `PublishRegistry` initialized in lifespan, router mounted at `/api/published/`. New endpoints: `POST /api/graphs/{id}/publish`, `POST /api/graphs/{id}/unpublish`, `GET /api/graphs/{id}/publish-status`. Auto-registers previously published workflows from `*.publish.json` on startup.
  - **Publish state persistence** (21-3 task 7-3): `graphs/{id}.publish.json` with `{enabled, api_key, rate_limit}` saved on publish, deleted on unpublish, scanned on startup.
  - **SSE streaming** (21-3 task 3-2): `GET /api/published/{wf_id}/events?session_id=X` — `StreamingResponse` with `text/event-stream`. `PublishSessionStore.subscribe_events()` async generator with auto-termination on session completion.
  - **WebSocket** (21-3 task 3-3): `WS /api/published/{wf_id}/ws` — bidirectional. `start` message creates session + runs workflow, `submit_input` message delivers HumanNode input. Events forwarded to client.
  - **Rate limiting** (21-3 task 4-6): `RateLimiter` class with sliding-window counter (`deque` of timestamps). Per-workflow configurable via publish config `rate_limit` field. Returns 429 when exceeded.
  - **Blocks API** (21-5 task 6-6): `GET /api/blocks` (list), `GET /api/blocks/{name}` (info + README), `POST /api/blocks/import` (from path/URL), `POST /api/blocks/export/{graph_id}` (workflow), `POST /api/blocks/export/{graph_id}/{node_id}` (composite), `DELETE /api/blocks/{name}/{version}` (uninstall). `BlockRegistry` initialized in lifespan with workspace scan.
  - **Adapter management** (21-4 task 7): `POST /api/adapters/start` (email/telegram/whatsapp), `POST /api/adapters/stop`, `GET /api/adapters/status` (running state, session count, uptime). Adapters run as asyncio background tasks, cleaned up on shutdown.
  - **Email retry handling** (21-4 task 3-8): `EmailAdapter.wait_for_response()` validates replies against expected schema. Malformed replies trigger "please try again" email with original prompt, max 3 retries per prompt.
- [test] **Server integration tests** — `tests/test_server/test_server_integration.py` (15 tests): publish/unpublish/status, persistence, blocks list/export/import/remove, adapter start/stop/status
- [test] **Publish rate limiter + session events** — `tests/test_publish/test_rate_limiter.py` (9 tests): sliding window, per-workflow isolation, event emission, subscribe/terminate
- [test] **Email adapter retry** — `tests/test_adapters/test_email_retry.py` (7 tests): reply validation, retry counter, schema matching

## 2026-03-04

- [feat] **Frontend block/publish/adapter integration** — deferred editor integration tasks for Plans 21-5, 21-3, 21-4, and 13-2:
  - **Blocks palette** (21-5 tasks 6-1→6-5): "Installed Blocks" collapsible section in `NodePalette` fetches blocks via `GET /api/blocks`, renders with type icon/version badge/description; draggable blocks with `application/dan-block` data transfer; `GraphCanvas` drop handler creates `CompositeNode` with `metadata.block_name/block_version`; `DanNode` shows purple package badge on block-sourced nodes; `ContextMenu` adds "Export as Block…" (composite nodes) and "Import Block…" (canvas); new `BlockExportDialog` modal with name/version/description/author fields
  - **Publish button** (21-3 task 7-2): toolbar dropdown with "Publish as MCP", "Publish as HTTP API", "Unpublish" (only when published), "Copy MCP Config"; green dot indicator when published; calls `publishGraph/unpublishGraph/getMcpConfig` API functions
  - **Adapter status** (21-4 task 7-2): toolbar indicator with message bubble icon + count badge; click opens panel listing active adapters (type icon, status, session count, stop button); polls `GET /api/adapters/status` every 10s
  - **API client** (`lib/api.ts`): added `fetchBlocks`, `importBlock`, `exportBlock`, `deleteBlock`, `publishGraph`, `unpublishGraph`, `getPublishStatus`, `getMcpConfig`, `getAdapterStatus`, `stopAdapter` with full TypeScript types (`Block`, `PublishStatus`, `AdapterInfo`)
  - **Checkpoint entry points** (13-2 task 5-2): confirmed already implemented via `CheckpointSection` in `RunHistoryPanel` (Plan 20-3)

- [feat] **Markdown loader block references (21-5 task 5-3)** — flow parser and compiler now support `name@version` block references in chain flow lines (e.g. `-> [my-block@0.1.0] ->`). When the compiler encounters a `@` reference, it resolves via `BlockRegistry`, loads the block graph, and inlines it as a `CompositeNode` with `metadata.block_name` and `metadata.block_version`. Emits warning (or error in strict mode) if block is not installed.

- [test] **Recovery/Debug Workbench tests (Plan 13-2)** — `tests/test_server/test_recovery_workbench.py` with 13 tests covering: variable inspector (`compute_upstream_variables` — connected, unconnected, no-edges, missing-node cases), test case CRUD (create, list, delete via `TestCaseStore`), and checkpoint rerun (scope models, staleness detection, no-store error).

- [test] **Blocks integration tests (21-5 task 8-5)** — `tests/test_blocks/test_integration.py` with 6 tests: export workflow → verify directory structure, export → import → registry scan → load graph (full pipeline), tarball export/import, `BlockResolver` caching verification, missing block error.

- [test] **Adapter integration tests (21-4 task 8-7)** — `tests/test_adapters/test_integration.py` with 18 tests: protocol compliance, full render cycle (approval/text/selection modes, state transitions, no-session error), prompt formatting and response parsing, trigger matching, session store concurrency, `as_callback` bridge.

- [docs] **Architecture updates for Plan 13-2** — added Variable Inspector, Node Test Cases, and `NodeTestCase` schema documentation to `docs/architecture.md`.

- [feat] **Phase 12 — Author & Distribute (Plans 21-1 through 21-5)** — complete implementation of 5 sub-plans. 2495 tests pass (284 new), 15 skipped, 0 failures. All sub-plans implemented with parallel subagents.

- [feat] **PyPI Package (Plan 21-1)** — public API surface cleanup and package structure: version set to 0.1.1, added `authors`/`license`/`readme`/`keywords`/`classifiers`/`project.urls` to pyproject.toml. Added 6 new CLI entry points (`dan-run`, `dan-status`, `dan-logs`, `dan-publish`, `dan-adapter`, `dan-blocks`). Created `src/dan/cli/` package with placeholder modules. Added optional dependency groups: `[cli]` (rich), `[mcp]` (mcp SDK), `[messaging]` (telegram, smtp). Exported `HumanRenderer`/`HumanRenderRequest`/`HumanRenderResponse`/`AutoRenderer`/`ProgrammaticRenderer` from `dan.engine`. Exported `HumanNode` from `dan`. Populated `dan.meta.__init__.py` with `MetaController`, `WorkflowPlanner`, `DiscoveryService`, `RepairEscalator`. Created `LICENSE` (MIT), `docs/PACKAGE_SPLIT.md`, added `editor/dist/` to `.gitignore`.

- [feat] **Publish Workflow as API/MCP (Plan 21-3)** — turn any DAN workflow into a callable service via MCP or HTTP REST. Key features:
  - **Session management** (`src/dan/publish/session.py`): `PublishSession` model (session_id, workflow_id, status, pending_request, result, error), `PublishSessionStore` (in-memory, lock-protected CRUD), `PublishedHumanRenderer` implementing `HumanRenderer` protocol — parks HumanNode prompts in session, waits on `asyncio.Event` until input submitted, configurable timeout with default_action fallback
  - **Schema derivation** (`src/dan/publish/schema.py`): `workflow_to_mcp_tools()` generates MCP tool descriptors (run + status + submit_input for human workflows), `workflow_to_openapi_paths()` and `workflow_to_openapi_spec()` generate OpenAPI 3.1 specs. Reuses `derive_workflow_interface()` from `dan.utils.workflow_interface`
  - **MCP server** (`src/dan/publish/mcp_server.py`): `build_mcp_server()` creates a FastMCP server from workflow graphs, each workflow registers as MCP tools + metadata resource. Supports stdio and streamable HTTP transports. Multi-workflow support via `--dir`. Conditional `mcp` import (optional dep)
  - **HTTP REST server** (`src/dan/publish/http_server.py`): FastAPI router with endpoints — `POST /run` (sync), `POST /run-async` (returns session_id), `GET /runs/{sid}` (poll), `POST /runs/{sid}/submit-input` (HumanNode), `GET /schema`, `GET /` (list), `GET /health`. Optional `X-API-Key` auth. `PublishRegistry` manages workflows. `create_publish_app()` for standalone
  - **Portal helpers** (`src/dan/publish/portal.py`): `generate_mcp_config()` produces copy-pasteable Cursor/Claude Desktop JSON, `generate_api_docs()` creates markdown documentation, `generate_openapi_spec()` outputs OpenAPI 3.1 spec
  - **CLI** (`src/dan/cli/publish.py`): replaces placeholder with full argparse — `dan-publish <wf> --type mcp|http|both`, `--port`, `--name`, `--api-key`, `--dir`, `--generate-config`, `--docs`, `--openapi`. Rich TUI startup banner. HTTP + MCP dual-mode via threading
  - **76 tests** across 6 test files: session lifecycle (create/update/remove/list), PublishedHumanRenderer (submit/timeout/default/transitions), schema derivation (MCP tools, OpenAPI paths/spec), MCP server building (mock FastMCP, tool registration, multi-workflow), HTTP endpoints (health, list, schema, run-async, auth), portal generation (MCP config, API docs, OpenAPI), CLI argument parsing

- [feat] **Shareable Blocks (Plan 21-5)** — packaging, exporting, importing, and managing reusable workflow components as versioned blocks. Key features:
  - **Shared utility** (`src/dan/utils/workflow_interface.py`): `WorkflowInterface` Pydantic model and `derive_workflow_interface(graph)` — extracts input/output schemas from a workflow graph (InputNode variables, prompt `{placeholder}` detection, exit-node output ports, HumanNode detection, multi-entry/multi-exit merging). Shared by 21-5 and 21-3.
  - **Block models** (`src/dan/blocks/models.py`): `DanBlock` manifest (name, semver version, description, author, license, tags, block_type, entry_point, dependencies, input/output schemas), `BlockDependency`, `InstalledBlock` with install path tracking. Semver validation, name validation.
  - **Export** (`src/dan/blocks/export.py`): `export_workflow_block()`, `export_composite_block()` (extracts sub-graph), `export_agent_collection_block()` (bundles .md files), `pack_block()` (creates `.dan-block.tar.gz`). Auto-derives input/output schemas via `derive_workflow_interface()`. Generates `dan-block.json` manifest, `graph.json`, and `README.md`.
  - **Import** (`src/dan/blocks/importer.py`): `import_block()` from local directory, `.dan-block.tar.gz` tarball, or HTTP(S) URL. User-level (`~/.dan/blocks/`) and workspace-level scopes. Version conflict warnings, dependency checking, force overwrite.
  - **Registry** (`src/dan/blocks/registry.py`): `BlockRegistry` class — scans user and workspace block directories, workspace-level overrides user-level (same name+version), `list_blocks()`, `get_block()` (latest version if unspecified), `remove_block()`, cached `_index.json` index.
  - **Executor/resolver** (`src/dan/blocks/executor.py`): `BlockResolver` with in-memory cache, `load_block_as_graph()`, `resolve_node_block()` for node metadata with `block_name`/`block_version`.
  - **CLI** (`src/dan/cli/blocks.py`): full argparse with 6 subcommands — `list`, `install`, `export`, `remove`, `pack`, `info`. Rich table output with plain-text fallback.
  - **67 tests** across 7 test files: model validation (semver, name, enums), workflow interface derivation (InputNode, placeholders, human detection, multi-entry/exit, inferred topology), export (workflow, composite, agent collection, tarball), import (directory, tarball, version conflict, force), registry (scan, override, get latest, remove, index), resolver (cache, missing block), CLI parsing.

- [feat] **Messaging Adapters (Plan 21-4)** — email, Telegram, and WhatsApp adapters that render HumanNode I/O through messaging channels. Key features:
  - **Adapter protocol & framework** (`src/dan/adapters/base.py`): `MessagingAdapter` protocol (5 async methods), `AdapterConfig` Pydantic model with trigger modes (always/keyword/pattern), `MessagingHumanRenderer` bridging any adapter to the engine's `HumanRenderer` protocol, `AdapterSessionStore` with state machine (idle→running→awaiting_human→completed/failed), prompt formatting and response parsing helpers for all render modes (text/approval/selection/form)
  - **Email adapter** (`email_adapter.py`): IMAP polling via stdlib `imaplib` + `asyncio.to_thread()`, async SMTP sending via `aiosmtplib`, email thread tracking (Message-ID / In-Reply-To), HTML result formatting
  - **Telegram adapter** (`telegram_adapter.py`): `python-telegram-bot` with long-polling or webhook, inline keyboards for selection/approval, /start /status /cancel commands, 4096-char message splitting, throttled progress updates, session-per-chat_id, allowed chat whitelist
  - **WhatsApp adapter** (`whatsapp_adapter.py`): WhatsApp Business Cloud API via `httpx`, FastAPI webhook sub-app with signature verification, interactive button/list messages, session-per-phone-number, documented Business API prerequisites
  - **CLI** (`dan-adapter`): argparse with `email`/`telegram`/`whatsapp` subcommands, `--config <json>` file loading, Rich TUI status panel (graceful fallback), async runner with signal handling
  - **78 tests** across 4 test files: protocol compliance, renderer bridge, session concurrency, email IMAP/SMTP mocks, Telegram command handlers + callback queries, WhatsApp webhook payload parsing + signature verification

- [feat] **CLI Mode (Plan 21-2)** — full `dan-run` CLI for executing DAN workflows from the terminal. Key features:
  - **Argparse-based CLI** with flags: `--api-key`, `--model`, `--base-url`, `--workspace`, `--input key=value`, `--input-json`, `--interactive`/`--headless`, `--quiet`/`--verbose`, `--output-format json`, `--output`, `--artifacts-dir`, `--background`/`--bg`, `--human-timeout`, `--auto-approve`, `--goal`
  - **Workflow source detection**: JSON file → `Graph.model_validate()`, markdown → `dan.loader.load()`, Python file → `importlib.util.spec_from_file_location` (looks for `graph` attr or `build()` callable), NL goal → `MetaController`
  - **Rich TUI progress display**: live status table (node name, type, status, duration, tokens), progress counter, final summary (time/tokens/cost/node breakdown), graceful fallback to plain text when rich not installed
  - **CLIHumanRenderer**: implements `HumanRenderer` protocol for interactive terminal prompts — approval (y/n), selection (numbered list), form (key-value), text (free text), with timeout and default fallback
  - **Background/supervisor mode**: `--background`/`--bg` spawns via `subprocess.Popen`, writes PID + events to `~/.dan/runs/{run_id}.*`
  - **dan-status**: lists active/recent background runs with status, duration, progress; `--kill` sends SIGTERM
  - **dan-logs**: tails event logs for a run, `--follow` for streaming, Rich-formatted output
  - **Output handling**: `--output <path>` writes JSON, `--quiet` outputs only final JSON, `--output-format json` streams JSONL events, artifact manifest on completion
  - **Signal handling**: SIGINT/SIGTERM triggers graceful shutdown
  - **Engine enhancement**: added `human_renderer` parameter to `Engine.__init__()` (passed through to `ExecutionContext`) so CLI can inject its renderer directly without legacy callback wrapping
  - **63 tests** across 5 test files: argument parsing, config resolution, source detection, HumanRenderer protocol conformance, TUI display classes, end-to-end integration with mocked engine

- [docs] **Phase 12 plan final review** — second review pass fixed 6 remaining stale references: `dan publish` → `dan-publish` (2 occurrences), `AdapterHumanInputResolver` → `MessagingHumanRenderer`, task 2-3 "one MCP tool" clarified to include HumanNode multi-tool pattern, `derive_workflow_interface()` location aligned between 21-3 and 21-5 (both now reference `dan.utils.workflow_interface`), 21-5 task 7-8 deduplicated with 21-1 entry points, 21-2 task 4-5 corrected Engine parameter passing (`human_renderer=` on constructor, not `EngineConfig`).

- [docs] **Phase 12 plan review and patch** — audited all 6 plan files against the actual codebase. Key fixes: (1) replaced non-existent "HumanInputResolver" references with the real `HumanRenderer` protocol from `dan.engine.executor` across 21-2, 21-3, 21-4; (2) redesigned MCP HumanNode interaction in 21-3 from broken notification pattern to multi-call pattern (run→status→submit_input) since MCP tools are request/response; (3) fixed MCP transport from "SSE" to "streamable HTTP" per current SDK; (4) added missing entry points to 21-1 (`dan-status`, `dan-logs`, `dan-adapter`); (5) noted version downgrade 0.2.0→0.1.1 is intentional; (6) fixed package rename approach (pyproject.toml + directory rename, not Python constant); (7) removed duplicate tasks between 21-1 and 21-2; (8) noted `dan.meta.__init__.py` is empty and needs populating; (9) promoted `HumanRenderer`/`HumanRenderRequest`/`HumanRenderResponse` to public API exports; (10) extracted `derive_workflow_interface()` to shared `dan.utils.workflow_interface` to avoid 21-5→21-3 dependency; (11) replaced unmaintained `aioimaplib` with stdlib `imaplib` + `asyncio.to_thread()`; (12) added WhatsApp Business API complexity warnings; (13) fixed Python file execution in CLI from `exec()` to `importlib`; (14) added block nesting decision.

- [docs] **Phase 12 — Author & Distribute plans** — created top-level plan (`21-author-distribute.md`) and five sub-plans: `21-1-pypi-package.md` (public API surface, package structure, version 0.1.1, entry points), `21-2-cli-mode.md` (Rich TUI, interactive HumanNode, meta-orchestrator NL path, background/supervisor mode), `21-3-publish-api-mcp.md` (MCP server generation, HTTP REST fallback, stateful streaming, portal UX), `21-4-messaging-adapters.md` (email + Telegram + WhatsApp as HumanNode renderers), `21-5-shareable-blocks.md` (block package format, export/import, versioning, local registry). Updated `todo.md` with Phase 12 section and sub-plan links.

- [fix] **Phase 11.5 code review fixes (Part 2)** — fixed `Cmd/Ctrl+Shift+M` chat mode keyboard shortcut to not trigger when an input or textarea is focused; removed redundant chat mode string normalization in frontend (trusting the backend API); set correct MIME types (`text/markdown`, `text/x-python`) for exported graph blob downloads.

- [fix] **Phase 11.5 code review fixes** — fixed duplicate `const tid` in `ChatPanel.tsx` `handleApplyMutation`; refined `detect_chat_mode` heuristic to avoid false positives (questions with "error"/"fix" now route to ask, not debug; stem matching for "crash"/"fail" variants); added "auto" to `ChatStore._VALID_MODES` and `loadThread` valid modes for consistent persistence; added click-outside handler to export dropdown in `EditorToolbar.tsx`. 48/48 tests pass.

- [feat] **Per-thread chat mode persistence (Plan 20-1, Task 4)** — chat mode (ask/agent/plan/debug) is now persisted to thread metadata (`*.meta.json`). Switching mode via the segmented control or `Cmd/Ctrl+Shift+M` saves to the backend. Loading/switching threads restores the stored mode. Backward compat: missing/legacy modes (`build`, `mutate`) normalize to `agent`.

- [feat] **Keyboard shortcut to cycle chat modes (Plan 20-1, Task 5)** — `Cmd+Shift+M` (Mac) / `Ctrl+Shift+M` (Windows) cycles Agent → Ask → Plan → Debug with a toast notification. Mode is also persisted to the active thread.

- [feat] **Debug diff tag (Plan 20-1, Task 6)** — mutation plans generated in debug mode are tagged with `source: "debug-fix"` in metadata. `GraphDiffPreview` renders an amber `[debug-fix]` badge next to "Proposed Changes" when this tag is present.

- [docs] **Docs sync for retry, multi-provider, and built-in tools (Plan 20-1, Tasks 1–3)** — added `RetryPolicy` field table and executor behavior to `architecture.md`; expanded provider registry with env var scanning and key management details; enhanced `dan.tools` section with per-category tool list and TOOL_METADATA pattern. Added new sections 7b (Retry Policy), 7c (Multi-Provider), and 7d (Built-in Tools) to `llm-api-guide.md` with usage examples. Updated `README.md` feature list with retry/fallback, multi-provider support, and built-in tools.

- [test] **Chat mode persistence tests (Plan 20-1, Task 7)** — 16 tests in `test_chat_mode.py` covering mode set/get, normalization, fallback to agent, legacy alias handling, coexistence with pinned metadata.

- [feat] **Checkpoint UI & Auto-Mode Detection (Plan 20-3)** — three features completing Phase 11.5 patch plan:
  - **Run History Checkpoint UI**: each completed/failed run in RunHistoryPanel now has an expandable "CP" button. Clicking it fetches checkpoints from `GET /api/runs/{run_id}/checkpoints` and displays timestamp, completed node count, and staleness status (green Compatible / amber Stale / red Incompatible badges). Compatible checkpoints show inline scope-picker (downstream-of / single-node / subgraph) + target node input to trigger `POST /api/runs/{run_id}/rerun`. Stale checkpoints show amber warning + "Run Full" fallback. Loading placeholder while fetching. `CheckpointEntry` type extracted in `api.ts` with staleness fields.
  - **Auto-Mode Detection**: new heuristic `detect_chat_mode()` in `chat_manager.py` classifies messages as debug/ask/plan/agent by keyword patterns and recent run failure status. `ChatMessageRequest.mode` extended with `"auto"` literal. When `mode="auto"`, server resolves to concrete mode and includes `detected_mode` in `chat_complete`/`chat_mutation` stream events. Frontend adds "Auto" option (PencilLine icon) to mode selector; shows effective mode badge ("→ Debug", "→ Ask", etc.) after detection. `ChatStreamEvent`, `ChatMode`, store types all updated. 28 pytest tests in `tests/test_server/test_auto_mode.py` cover detection heuristics, priority ordering, and `normalize_chat_mode` pass-through.
  - **Multi-Tab Checkpoint Consistency audit**: confirmed no-op — test cases, variable inspector, and checkpoint data are all API-fetched on demand, not stored in `TabSnapshot`.

- [feat] **Chat & Editor UX Polish (Plan 20-2)** — implemented five deferred UX features:
  - **"Fix This" shortcut**: one-click from run error or validation badge to Debug mode with error context pre-filled in chat input. Store action `openDebugWithError` switches mode, opens chat panel, and pre-fills input while preserving thread context. Available in LogPanel (wrench button on error node groups) and DanNode (clickable error badge).
  - **Collapse verbose run output**: `RunOutputBlock` now defaults to collapsed; auto-expands on error. Collapsed view shows "`N nodes — Status`" summary.
  - **Fuzzy mention autocomplete**: replaced substring `.includes()` with subsequence fuzzy scoring (gap penalty, start bonus). Results sorted by score, threshold 0.3 filters noise. Highlight shows individual matched characters for non-contiguous matches.
  - **Frontend export buttons**: Export dropdown in toolbar with JSON/Markdown/Python options. Markdown and Python call `GET /api/graphs/{id}/export/markdown|python`; results shown in ExportPreviewModal with file sidebar, copy-to-clipboard, download actions, and diagnostics banner.
  - **Sortable log columns**: clickable header row (Node, Duration, Tokens, Cost) above log node groups. Click toggles null→asc→desc→null cycle with ▲/▼ indicators. Resets on new run.

- [docs] **Phase 11.5 plan patch-up** — corrected execution-critical details in `20-*` plans after review: fixed built-in tool IDs in `20-1`, aligned per-thread mode persistence with chat thread metadata APIs, updated `20-3` checkpoint work to target `RunHistoryPanel` + scope-picker rerun flow, and replaced `auto_detect` request flag with explicit `mode: "auto"` contract + `detected_mode` response metadata. Added validation task blocks to `20-1`, `20-2`, and `20-3`.

- [docs] **Todo promotion style normalization** — updated promoted backlog items in `todo.md` from `[ ] ~~item~~ → promoted...` to `[x] ~~item~~ → promoted...` for consistency with existing promotion conventions.

- [docs] **Phase 11.5 — Patch & Polish plan** — created top-level plan (`20-patch-polish.md`) and three sub-plans: `20-1-docs-quick-wins.md` (docs sync + trivial chat UI), `20-2-chat-editor-polish.md` ("Fix this" shortcut, collapse run output, fuzzy mentions, export buttons, sortable logs), `20-3-checkpoint-ui-automode.md` (checkpoint history UI, multi-tab consistency, auto-mode detection). Updated `todo.md` with Phase 11.5 section and annotated 12 backlog items as promoted.

- [docs] **Backlog consolidation** — organized all deferred items from 25+ plan files into categorized backlog sections in `todo.md`: Infrastructure/CI, Integration tests requiring real LLM, Frontend polish, Docs sync, Requires new architecture, Deferred runtime features, Stretch goals. Each item references its source plan and task number.

- [feat] **Checkpoint portal UX integration (Plan 13-2, Task 5)** — surface checkpoint rerun, variable inspection, and test case creation in the editor.
  - Context menu: "Rerun from Here" (downstream_of scope) and "Rerun This Node" (single_node scope) actions on canvas nodes, visible when a run exists.
  - LogPanel: node group header now shows action buttons on hover — "Inspect inputs" (selects node), "Add test case from this run" (opens modal with prefill), "Rerun from here" (triggers downstream rerun).
  - Store: `rerunFromNode(nodeId, scopeType)` action connects to `POST /api/runs/{run_id}/rerun` endpoint, resets run state, and connects WebSocket for the new rerun.
  - API client: `listCheckpoints()` and `rerunFromCheckpoint()` functions in `api.ts`.
  - Error handling: stale checkpoints (409) and scope validation errors surface as toast notifications.

- [fix] Fix `test_error_memory_retrieved_event_emitted` test failure — mock's `get_context` was missing `cross_workflow` kwarg added when cross-workflow learning landed.

- [docs] **Category 4: Plan deferral annotation audit** — reviewed all 25+ plan files across Phases 4–11. All `[ ]` items already had proper `*(deferred — reason)*` annotations. Fixed 18-1 Tests parent checkbox (`[ ]` → `[x]` — all sub-tasks were already complete).

- [docs] **Category 1: Plan reconciliation** — updated 10 plan files (1-5, 6, 6-1, 6-2, 6-3, 6-5, 11-4, 17, 17-1, 17-2) to match actual implementation status. Updated todo.md Phase 9D checkboxes.

- [feat] **Token Analytics Frontend (Plan 18-4, Task 3) — editor visualization for token usage, waste detection, and optimization recommendations.**
  - **Token heatmap on canvas:** nodes color-coded by token consumption (green -> yellow -> red gradient), normalized to max usage in the run. Toggle via toolbar "Heatmap" button. Background color applied to node body when enabled.
  - **Per-node token tooltip:** hover over the token badge on any completed node to see a detailed breakdown popup — input/output tokens, system/user/context/hyperedge/memory/RAG token split, model name, cost, cache hit status.
  - **Waste warning badges:** nodes with detected token waste show a yellow triangle warning badge (top-left corner). Hover to see the waste findings with category, description, and estimated saveable tokens.
  - **Run-level token summary:** expanded the RunSummaryBar in LogPanel with a clickable "details" section showing the top 3 most expensive nodes (with token counts and costs) and a waste summary (finding count, saveable tokens, waste categories).
  - **Optimizations tab:** new "Optimizations" bottom panel tab (alongside Logs, Output, History) with a badge showing the count of waste findings. Panel shows: summary bar with waste stats, category filter buttons, finding cards sorted by saveable tokens (descending), and one-click "Apply" buttons that call the existing `/api/runs/{run_id}/optimization-mutations` endpoint to apply graph mutations. Each card shows category label, node name, description, saveable tokens, and suggestion text.
  - **Zustand store additions:** `tokenBreakdowns`, `wasteFindings`, `optimizationMutations`, `tokenHeatmapEnabled`, `analyticsLoading` state; `fetchTokenAnalytics()` action (called auto on run completion), `applyOptimizationMutation()` action, `setTokenHeatmapEnabled()` toggle. TabSnapshot extended to persist analytics across tab switches. Analytics data cleared on new run start.
  - **API client additions:** `fetchTokenBreakdown()`, `fetchOptimizationReport()`, `fetchOptimizationMutations()` in `api.ts`.
  - **TypeScript types:** `TokenBreakdown`, `WasteFinding`, `OptimizationReport`, `OptimizationMutation`, and response types in `types/graph.ts`.
  - Files: `TokenAnalyticsPanel.tsx` (new), `DanNode.tsx` (heatmap + tooltips + waste badge), `LogPanel.tsx` (enhanced RunSummaryBar), `EditorToolbar.tsx` (heatmap toggle), `App.tsx` (optimizations tab), `useGraphStore.ts` (store slice), `api.ts` (API calls), `types/graph.ts` (types).
  - Remaining deferred: token flow edge labels (3-4), before/after estimation (4-4), analytics dashboard for self-evolving rules (5-6).

- [feat] **Checkpoint portal backend (Plan 13-2, Tasks 1-2)** — checkpoint-based partial rerun infrastructure.
  - Extended `CheckpointData` model in `engine/checkpoint.py` with `graph_revision` (deterministic hash of graph nodes + edges), `completed_node_ids`, and `node_outputs` fields. Updated `Engine._save_checkpoint()` to populate these fields on every checkpoint write.
  - `RerunScope` Pydantic model: supports `downstream_of`, `single_node`, and `subgraph` scope types for targeted partial reruns.
  - `compute_graph_revision_hash()`: deterministic SHA-256 of graph nodes + edges (excludes metadata so cosmetic changes do not invalidate checkpoints). Accepts Graph model or dict.
  - `check_checkpoint_staleness()`: compares checkpoint's `graph_revision` to current graph, returns `StalenessResult` with `compatible`, `stale`, `missing_nodes`, and `message` fields.
  - `compute_downstream_nodes()`: BFS traversal from target node to find all downstream dependents for `downstream_of` scope.
  - `compute_subgraph_node_ids()`: looks up sub-graph by key and returns node IDs for `subgraph` scope.
  - `RunManager.rerun_from_checkpoint()`: validates scope against checkpoint, checks staleness (rejects with `RuntimeError` on stale), rehydrates `PortDataStore` with checkpoint outputs for skipped nodes, marks skipped nodes as `SKIPPED`, creates new `run_id` with provenance tags.
  - `RunManager.get_checkpoint_info()` and `list_checkpoint_runs()`: query checkpoint metadata for API consumption.
  - Three new API endpoints: `GET /api/runs/{run_id}/checkpoints` (list with staleness check), `GET /api/runs/{run_id}/checkpoints/{checkpoint_id}` (detail with completed_node_ids, node_output_keys), `POST /api/runs/{run_id}/rerun` (accepts `RerunScope`, returns 409 on stale checkpoint, 422 on invalid scope).
  - `RERUN_STARTED` event type: emitted before partial rerun execution with full provenance (`source_checkpoint_id`, `rerun_scope`, `nodes_to_rerun`, `nodes_skipped`). Result metadata tagged with `__rerun_provenance__`.
  - Fixed latent `NameError` in `_execute_with_cycles` halt-branch checkpoint call (referenced undefined `cost_tracker` variable).
  - Tests: `test_checkpoint_portal.py` — 30 tests covering CheckpointData model, RerunScope model, graph revision hashing (deterministic, metadata-insensitive, edge-sensitive), staleness detection (compatible/stale/missing nodes), downstream computation (linear/diamond/branch), subgraph node IDs, RunManager checkpoint info, and rerun validation.
- [feat] **Node test cases (Plan 13-2, Task 4)** — define and run test cases on individual nodes in isolation.
  - Backend schema: `NodeTestCase` and `TestCaseRunResult` Pydantic models in `src/dan/server/test_cases.py`. Fields: id, name, node_id, inputs, expected_outputs, assertions, tags, notes, timestamps.
  - Backend persistence: `TestCaseStore` class with file-based storage at `{graphs_dir}/test_cases/{workflow_id}/{node_id}.json`. CRUD: `list_cases`, `get_case`, `save_case` (upsert), `delete_case`. Follows RunStore pattern (atomic write via .tmp rename).
  - REST API: 4 endpoints in `app.py` — `GET /api/test-cases/{wf}/{node}` (list), `POST /api/test-cases/{wf}/{node}` (create/update), `DELETE /api/test-cases/{wf}/{node}/{id}` (delete), `POST /api/test-cases/{wf}/{node}/{id}/run` (execute). Run endpoint builds synthetic single-node graph and executes via `RunManager.start_run()`, compares outputs against expected values.
  - Frontend component: `TestCasePanel.tsx` — collapsible "Test Cases" section in ConfigPanel with test case list, per-case Run/Edit/Delete actions, pass/fail indicators, output diff display, and modal for creating/editing cases with JSON input editors per port.
  - Context menu: "Add Test Case" right-click action on canvas nodes dispatches custom event to open the test case modal.
  - API client: `listTestCases`, `createOrUpdateTestCase`, `deleteTestCase`, `runTestCase` functions + `NodeTestCase`/`TestCaseRunResult` types in `api.ts`.
  - Tests: `test_test_cases.py` — 11 tests covering save/list, get, upsert, delete, multiple cases, node isolation, empty list, corrupted file recovery, persistence format, optional expected outputs.
- [feat] **Variable inspector (Plan 13-2, Task 3)** — upstream inputs diagnostic for any selected node.
  - Backend: `compute_upstream_variables()` utility in `src/dan/server/variable_inspector.py` — walks incoming edges (data + context), infers types from port schemas, detects unconnected required ports.
  - REST API: `GET /api/graphs/{graph_id}/nodes/{node_id}/inputs?run_id=` endpoint in `app.py` — returns static wiring + optional runtime values from persisted events.
  - Frontend: `UpstreamInputsSection` collapsible in `ConfigPanel.tsx` — shows variable name, source provenance, type hint, "likely missing" amber diagnostics, and read-only runtime value preview (live from `nodeOutputs` or persisted from `RunStore`).
  - API client: `getNodeInputs()` + `UpstreamVariable`/`NodeInputsResponse` types in `api.ts`.
- [docs] **Plan file reconciliation: fixed stale checkboxes and status markers across 10 plan files.**
  - Plan 1-5 (Builder API): marked all 44 sub-task checkboxes as [x] — all were implemented but never checked off.
  - Plan 6 (Phase 3.75 parent): status `in-progress` → `completed` — all 14 sub-plans shipped.
  - Plans 6-1, 6-2, 6-3, 6-5: status `not-started`/`in-progress` → `completed` — all features implemented and working.
  - Plan 6-1 (History/Multi-Select): marked all 28 sub-task checkboxes as [x].
  - Plan 11-4 (Documentation): status `in-progress` → `completed`.
  - Plans 17-1 (Error Memory), 17-2 (Reflection Node), 17 (parent): status `in-progress` → `completed`. Core implementations are done; explicit deferred items (integration tests, authoring surfaces) preserved as [ ] in plan files.
  - todo.md Phase 9D: marked 17-1, 17-2, 17 parent as [x] with deferred-item annotations.
  - Plans 19-1 through 19-4: already marked `completed` — no changes needed (verified).

## 2026-03-03
- [feat] **Phase 11 — final two deferred tasks completed: builder-code path + engine pipeline tests.**
  - Builder-code path (`GenerateCodePlan`): LLM can generate Python builder DSL code; executed in `SandboxRunner` subprocess with 30s timeout. Harness wraps code to serialize compiled `Graph` to `_result.json`. `_validate_plan()` warns on missing `build()` call. 5 new tests.
  - Engine pipeline tests: `test_parameter_mutation_changes_model_at_runtime` — builds a workflow, runs it through `Engine`, applies `_apply_parameter_mutations()` to change temperature, runs again; `test_graph_mutator_edit_node_roundtrip` — `GraphMutator.apply()` with `EditNode` mutates prompt+system_prompt, then runs mutated graph. Both use real LLM.
  - Total Phase 11 test count: 135 (117 unit + 18 integration), all passing.

- [feat] **Phase 11 — Meta-Orchestrator: all deferred items completed, phase fully shipped.**
  - Cross-workflow principle sharing: `ErrorMemoryIndex.query_similar(scope="global")` searches across all `dan_errors_*` collections; `PrincipleStore.load_principles(scope="global")` iterates all workflow directories with de-duplication by principle id (keeps highest confidence). New `EngineConfig.cross_workflow_learning` flag threads scope through `ErrorContextProvider` → `LLMExecutor`.
  - Few-shot examples added to `PlanningPromptBuilder.SYSTEM_TEMPLATE`: three concrete examples (REUSE/paper_writing, ADAPT/equity_research+beamer, GENERATE/RAG QA) guiding LLM output format.
  - Meta-event WebSocket: `_build_meta_controller()` now wires `_emit_meta_event()` callback that fans out to `_meta_subscribers` dict. New endpoint `WS /api/meta/sessions/{id}/events/ws` streams live meta-orchestrator events.
  - 11 LLM integration tests in `test_integration_llm.py`: planner (generate/reuse/adapt/compilable spec), structural repair, cross-workflow sharing (principles global scope, de-dup, ErrorMemoryIndex global), experience consolidation (roundtrip, incremental), meta-controller end-to-end. All use real LLM via `.env` credentials; marked `@pytest.mark.integration` for CI gating.
  - Registered `integration` marker in `pyproject.toml`.
  - All 128 meta-related tests pass (101 unit + 11 integration + 16 API).
  - All four plan files (19-1 through 19-4) marked **completed**.

- [feat] **Wire analytics event emission end-to-end for 4 declared EventTypes.**
  - `TOKEN_BREAKDOWN_RECORDED`: emitted per-LLM-node after completion in scheduler `_execute_node`, carrying the node's `TokenBreakdown.to_dict()`.
  - `WASTE_DETECTED`: emitted per-finding at run completion in scheduler `_emit_post_run_analytics`, carrying each `WasteFinding.to_dict()`.
  - `OPTIMIZATION_REPORT_READY`: emitted once at run completion with the full `TokenOptimizationReport.to_dict()`.
  - `OPTIMIZATION_APPLIED`: emitted from `apply_mutation` API endpoint when `source="optimization"`, tracked via `RunManager.emit_optimization_applied()`.
  - Added `source: str | None` field to `ApplyMutationRequest` (backward-compatible); optimization-mutations endpoint now includes `source: "optimization"` in `apply_request` envelopes.
  - 4 new integration tests in `test_token_analytics.py` verifying emission (breakdown, report, waste, no-analytics-on-code-nodes). All 2100 tests pass.

- [fix] **Phase 10 Wave 3 patch-up after code review: runtime contracts aligned, actionable mutations fixed.**
  - Fixed `TokenWasteAnalyzer` event matching to real runtime events (`iteration_started`, `tool_call_started`, `node_started`) with backward-compatible aliases.
  - Added `input_hash` to `node_started` event payloads in scheduler so memoization-opportunity detection works on real runs.
  - Added `context_tokens` emission for loop iteration events (`WhileLoopExecutor` + gate-cycle iteration) to enable loop-growth detection.
  - Fixed optimization mutation payload schema from ad-hoc keys to `GraphMutator`-compatible operations (`op`, `updates`, `edge_id`).
  - Strengthened reference-opportunity handling: restrict to context edges, carry `edge_id` in findings, and emit valid `edit_edge` mutations.
  - Wired optimization endpoints with real graph context (`node_configs`, `graph_edges`) so analyzer categories are active in production.
  - Added ready-to-apply mutation envelopes in `/api/runs/{id}/optimization-mutations` response (`mutation_plan`, `apply_request`).
  - Updated breakdown accounting to accumulate per-node breakdowns across repeated node executions.
  - Fixed context-selection ordering in `LLMExecutor` to preserve preprocessing (reference/history transforms) before token-budget selection.

- [feat] **Phase 10 Wave 3: 18-4 token analytics, waste detection, evolving playbooks. Full test suite: 2094 passed.**
  - Added `TokenBreakdown` and `TokenSaving` dataclasses to `cost_tracker.py`. `CostTracker` now tracks per-node token composition and optimization savings with `record_breakdown()`, `record_saving()`, `all_breakdowns()`, `all_savings()`, `savings_summary()`.
  - Added `TokenWasteAnalyzer` with 8 waste categories: `unused_context`, `duplicate`, `loop_growth`, `oversized_system`, `unused_memory_rag`, `jit_opportunity`, `memoization_opportunity`, `reference_opportunity`. Produces `TokenOptimizationReport` sorted by saveable tokens.
  - Added `OptimizationPlaybook` bridging waste findings to Plan 17 rules: record → mark_applied → evaluate_effectiveness → promote_effective (generates `CausalPrinciple` dicts for `RuleLifecycleManager`). Supports `generate_mutation()` for one-click-apply graph changes.
  - `LLMExecutor._record_token_breakdown()` decomposes messages by role and inputs by source type (context, memory, RAG).
  - Token savings recorded at optimization sites: `context_deferred`, `payload_pruned`, `input_summarized`.
  - Scheduler snapshots `__cost_tracker__` data (breakdowns + savings) into `RunResult.metadata`.
  - New API endpoints: `GET /api/runs/{run_id}/token-breakdown`, `GET /api/runs/{run_id}/optimization-report`, `GET /api/runs/{run_id}/optimization-mutations`.
  - `EngineConfig` gains `optimization_rule_approval_mode` (`always_approve` | `auto_accept`).
  - New event types: `TOKEN_BREAKDOWN_RECORDED`, `WASTE_DETECTED`, `OPTIMIZATION_REPORT_READY`, `OPTIMIZATION_APPLIED`.
  - New test files: `tests/test_engine/test_token_analytics.py` (34 tests), `tests/test_engine/test_token_integration.py` (9 cross-plan integration tests).
  - All 2094 tests pass, 15 skipped.

- [feat] **Phase 11 wrap-up: remaining code gaps closed, tests brought to 116 across meta modules.**
  - Added `RedesignResult` model to `meta/repair.py` — captures redesign outcomes (new_graph, old_workflow_id, reason, changes_summary).
  - Enhanced `MetaController._update_experience()` — now creates experience from graph on first save, consolidates run history and failure patterns; works on both success and failure paths.
  - Added `MetaController._enrich_goal_with_prior_experience()` — cross-session learning queries ExperienceIndex at session start, injecting context about similar past workflows (including failed ones).
  - Failed meta-sessions now persist experience with repair history and failure patterns, ensuring the planner avoids repeating mistakes.
  - Added `TestCompileGenerateSpec` (11 tests) — validates generate-spec compiler: basic compilation, Graph model validity, all 4 node types, edge name resolution, passthrough, unsupported type rejection, source_id fallback, defaults for tool/code/gate nodes.
  - Added controller integration tests: `test_full_loop_fail_then_succeed` (goal → plan → fail → diagnose → repair → succeed in 2 iterations), `test_experience_feedback_roundtrip` (successful session saves experience), `test_failed_session_saves_experience`.
  - Added `test_record_outcome_marks_success` and `TestRedesignResult` to repair tests.
  - Added 16 API integration tests (`test_meta_api.py`): experience CRUD lifecycle (list/get/refresh/delete), search validation, meta session management (list/get/events/pause/delete), input validation (run/plan goal required), discover endpoint, validate-plan rejection.
  - Updated all four plan files (19-1 through 19-4) — marked completed tasks `[x]`, annotated deferred items.

- [feat] **Phase 10 Wave 2 implemented: memoization + semantic cache + smart context assembly + advisory budgets.**
  - Added `src/dan/engine/cache.py` with `NodeResultCache` (deterministic hash key, in-memory LRU, optional disk persistence, TTL) and `SemanticCache` (query normalization + embedding/vector lookup + TTL).
  - Wired scheduler cache flow end-to-end: pre-dispatch lookup, cache events (`CACHE_HIT`/`CACHE_MISS`/`CACHE_INVALIDATED`/`SEMANTIC_CACHE_HIT`), post-success writeback, and per-run cache summaries.
  - Extended config/model surface for caching and context assembly:
    - `EngineConfig`: `cache_enabled`, `cache_max_size_mb`, `cache_dir`, `semantic_cache_threshold`, `semantic_cache_ttl_hours`
    - `NodeBase`: `memoize`, `cache_ttl`
    - `LLMOperator`: `semantic_cache`, `history_policy`
    - `CompactionRule`: `summarize_every_n`, `summary_model`, `target_tokens`, `require_persistent_recall`
  - `LLMExecutor` now performs smart input assembly in runtime: deferred-context selection, payload pruning/format compaction, optional summarization (with memory persistence), lazy reference resolution, JIT tool-schema loading (`ToolSchemaResolver`), and built-in context tools (`search_context`, `read_context`, `read_state`, `list_available_context`).
  - Added context-architecture helpers in `token_optimization.py`: `HistoryManager`, `LoopCompactor`, `TokenBudgetAdvisor`.
  - Scheduler now initializes and applies advisory token budgets (`BUDGET_ADVISORY`) and externalizes node execution summaries to `StateStore` (`STATE_EXTERNALIZED`).
  - While-loop compaction now enforces persistent-recall safety for lossy strategies and emits `LOOP_COMPACTION_APPLIED`.
  - Added cache management endpoints: `POST /api/cache/clear`, `GET /api/cache/stats`.

- [test] Added Wave 2 test coverage.
  - New test files: `tests/test_engine/test_cache.py`, `tests/test_engine/test_jit_context_tools.py`, `tests/test_engine/test_context_architecture.py`.
  - Full regression run passes: `2018 passed, 15 skipped`.

- [docs] Updated Phase 10 tracking docs for Wave 2 completion.
  - Updated `docs/plans/18-1-prompt-compression.md`, `18-2-caching-layer.md`, `18-3-context-window-management.md`, `18-token-optimization.md`, and `docs/todo.md` wording/progress.

- [feat] **Phase 11 hardening: functional meta runtime + incremental experience consolidation.**
  - Experience memory is now incrementally consolidated with run-level dedupe (`processed_run_ids`) and auto-indexing in `ExperienceStore.save_experience()`.
  - `RunManager` now performs post-run experience consolidation in `_enrich_and_persist()` using configured triggers (interval + first-success/first-failure), and exposes `engine_config/tool_registry/run_store` accessors for API wiring.
  - Planner/repair/controller runtime gaps were closed: Generate specs now compile to executable graphs, structural-repair prompt schema uses `edit_node.updates`, repair actions persist with `action_id`, and repair outcomes are recorded for escalation history.
  - `MetaController` now resumes the same session, supports checkpoint pause requests, applies parameter/structural mutations to real workflow graphs, and persists session events for diagnostics.
  - `/api/experiences/*` and `/api/meta/*` endpoints are now wired end-to-end (`search`, `refresh`, `plan`, `validate-plan`, `run`, `pause`, `resume`, `events`), replacing prior stubs/undefined config references.
  - Added/updated tests for incremental dedupe, resume semantics, and repair-action persistence; meta test suite passes (`83 passed`).

- [fix] **Phase 9D patch: parameter-fix mutation wiring, origin-run event routing, RULE_DISABLED emission, stale effectiveness score, rlm guard.**
  - `run_manager._persist_reflection_principles()` now checks `repair_level` and calls `rlm.create_mutation()` for `parameter_fix` principles (previously only called `create_rule()`, making parameter mutations a no-op).
  - Reflection-related events (`reflection_completed`, `rule_generated`) are now emitted to both the reflection run record AND the originating run record, so the full learning loop is traceable from the original run's event log.
  - Added `RunManager.emit_rule_lifecycle_event()` public method for API-driven lifecycle events. `POST /api/rules/.../disable` now emits `RULE_DISABLED` with `reason: "manual_api"`.
  - `_track_rule_effectiveness()` now reloads the rule from disk after `record_application`/`record_outcome` so the `rule_effectiveness_update` event payload contains fresh `apply_count` and `effectiveness_score`.
  - `scheduler._execute()` initializes `rlm = None` before the try block and guards mutation lookup with `if rlm is not None:` to prevent `NameError` if rule loading fails.
  - Added 9 focused tests covering mutation wiring, origin-run routing, stale score fix, RULE_DISABLED event, and rlm guard. Full suite: 1991 passed, 15 skipped.

- [fix] **18-1 Task 0 hardening pass: runtime tool registry wiring + provider fallback robustness.**
  - Wired `ExecutionContext.tool_registry` end-to-end (`executor.py` + `scheduler.py`) by injecting the active `ToolExecutor.registry` when building execution context. This fixes a runtime gap where LLM tool-calling worked in tests but could not resolve tools in normal engine runs.
  - Hardened `LLMExecutor` provider path: stream call now tolerates awaitable/coroutine-style provider `stream()` implementations without coroutine warnings; fallback-model call now preserves tool schemas so tool-calling behavior survives retries/fallbacks.
  - Added deterministic tool schema ordering in `LLMExecutor` for cache-stable request shaping and made tool result JSON serialization robust for non-primitive dict/list values.
  - Added regression tests: `TestExecutionContextToolRegistry` in `test_llm_tool_calling.py` and new `test_scheduler_tool_registry.py` validating scheduler context injection.
  - Full suite green after patch: `1982 passed, 15 skipped`.

- [feat] **Phase 11 Meta-Orchestrator — core implementation (Plans 19-1 through 19-4).**
  - **19-1 Workflow Experience Memory** (`engine/experience.py`): `WorkflowExperience` model, `extract_experience_from_graph()`, `consolidate_experience()` with running averages and pattern extraction, `ExperienceStore` (MemoryStore-backed CRUD), `ExperienceIndex` (embedding + vector search over composite text). REST endpoints: `GET/DELETE /api/experiences/{id}`, `POST /api/experiences/search`, `POST /api/experiences/{id}/refresh`.
  - **19-2 Workflow Planner** (`meta/discovery.py`, `meta/planner.py`): `DiscoveryService` enumerates tools/skills/patterns/past workflows with reuse-fit scoring. `PlanningPromptBuilder` constructs system/user prompts. `WorkflowPlanner` orchestrates discovery→prompt→LLM→parse→validate loop with retry. Declarative `PlanIR` output: `ReusePlan`/`AdaptPlan`/`GeneratePlan`. `execute_plan()` dispatches to `GraphStore` load or `GraphMutator.apply()`.
  - **19-3 Structural Repair Engine** (`meta/repair.py`): `RepairLevel` enum (PROMPT→PARAMETER→STRUCTURAL→REDESIGN) with `should_escalate()`. `RepairClassifier` uses keyword matching + escalation history. `ParameterRepairGenerator` produces whitelisted `EditNode` mutations. `StructuralRepairPlanner` drives LLM to produce validated `MutationPlan` with dry-run. `RedesignTrigger` heuristics. `RepairEscalator` orchestrates graduated dispatch. `RepairActionStore`/`RepairActionRecord` for lifecycle tracking.
  - **19-4 Autonomous Execution Controller** (`meta/controller.py`): `MetaSession` state machine (planning→executing→diagnosing→repairing→paused→completed→failed). `MetaSessionStore` (MemoryStore-backed). `MetaController.run()` loop: plan→execute→observe→diagnose→repair. Human override protocol (`HumanOverride`, `OverrideType`), checkpoint-based pause/resume. Experience feedback on success/failure.
  - **Integration wiring**: 12 new `EventType` values (experience + meta session lifecycle). 16 new `EngineConfig` fields across all 4 subplans. REST endpoints for discovery (`GET /api/meta/discover`), planning (`POST /api/meta/plan`), and session management (`GET/DELETE /api/meta/sessions/{id}`).
  - **79 new tests** across 4 test files (`test_experience.py`, `test_discovery_planner.py`, `test_repair.py`, `test_controller.py`). All passing.

- [feat] **18-2 Task 1: Provider-level prompt caching.**
  - Added `cached_input_tokens` and `cache_write_tokens` fields to `CompletionResult`. Added standalone `apply_cache_hints()` helper (duck-typed, no Protocol change).
  - Anthropic: `apply_cache_hints()` inserts `cache_control: {"type": "ephemeral"}` on long system messages (>1024 est. tokens). `_split_system()` returns content blocks when hints are present. `_extract_usage()` captures `cache_read_input_tokens`/`cache_creation_input_tokens`.
  - OpenAI: `apply_cache_hints()` ensures system messages lead for stable prefix caching. `_extract_usage()` reads `prompt_tokens_details.cached_tokens`.
  - Google: stub `apply_cache_hints()` (no-op; `CachedContent.create()` deferred).
  - `CostTracker.record()` extended with `cached_input_tokens`/`cache_write_tokens` kwargs. Added `cache_summary()` method. `snapshot()`/`restore()` include cache state.
  - `LLMExecutor._call_via_provider()` applies cache hints when `EngineConfig.prompt_caching_enabled` (default True). Both `cost_tracker.record()` call sites pass cache metrics.
  - 24 new tests in `tests/test_providers/test_prompt_caching.py`. Full suite green (1901 passed).

- [feat] **18-1 Task 0: Generic tool-calling loop support in LLMOperator.**
  - Added `tools: list[dict[str, Any]]` and `max_tool_rounds: int` fields to `LLMOperator` in `nodes.py` with backward-compatible defaults (empty list, 10).
  - Extended `LLMExecutor.execute()` with full tool-calling loop: model emits tool_calls → executor resolves tools from `context.tool_registry` → tool results appended → model called again → repeat until plain text or max rounds.
  - Added `_run_tool_loop()` and `_execute_tool_call()` helper methods to `LLMExecutor`.
  - Updated `_call_llm()` return signature to include `tool_calls` (4-tuple). Updated `_call_via_provider()` to pass `tools` kwarg and use `complete()` (not streaming) when tools are active.
  - Widened `messages` type annotation from `list[dict[str, str]]` to `list[dict[str, Any]]` in `LLMProvider` protocol and all three provider implementations (OpenAI, Anthropic, Google) for tool result message compatibility.
  - Emits existing `TOOL_CALL_STARTED` and `TOOL_CALL_RESULT` events for each tool call. Cumulative usage and `CostTracker` recording cover all rounds.
  - 17 new tests in `tests/test_engine/test_llm_tool_calling.py`: backward compat (no tools), single/multi-round tool calling, parallel tool calls, max_tool_rounds safety, tool-not-found handling, event emission, cumulative usage, cost tracker, message structure, model field serialization, tool exception handling.
  - Full suite green (1876 passed, 1 pre-existing deselected).

- [feat] **18-1 Tasks 1, 2, 6, 8: Smart Context Assembly — model layer + utility classes.**
  - Added 6 new fields to `LLMOperator` in `nodes.py`: `target_input_tokens`, `summarize_inputs`, `jit_tool_loading`, `agent_context_tools`, `prune_fields`, `input_format` — all with backward-compatible defaults.
  - Added `pass_by_reference` field to `ContextEdge` in `edges.py` (default `False`).
  - Added 6 new event types to `EventType` enum: `TOKEN_BUDGET_ADVISORY`, `CONTEXT_DEFERRED`, `INPUT_SUMMARIZED`, `JIT_SCHEMA_LOADED`, `PAYLOAD_PRUNED`, `CONTEXT_TOOL_CALLED`.
  - Added `token_budget: int | None` to `EngineConfig` in `executor.py`.
  - Created `src/dan/engine/token_optimization.py` (new module): `SummarizationConfig` (Pydantic), `PromptAnalyzer` + `PromptAnalysis` (static template analysis), `ContextSelector` (relevance scoring + inline/deferred partitioning), `PayloadPruner` (glob-pattern field stripping + JSON/YAML/compact serialization).
  - 56 new tests in `tests/test_engine/test_token_optimization.py` covering all utility classes, model field round-trips, event types, backward compatibility. Full suite green (1873 passed).

- [feat] **18-3 Task 1: StateStore abstraction (externalized structured state).**
  - Created `src/dan/engine/state_store.py` with `StateStore` protocol, `FileSystemStateStore` (atomic writes, path traversal protection, prefix query), `NullStateStore` (no-op).
  - Typed Pydantic schemas: `LoopIterationState`, `TeamTurnState`, `NodeExecutionSummary` — all with `ConfigDict(extra="allow")` for forward compatibility.
  - Added `STATE_EXTERNALIZED`, `LOOP_COMPACTION_APPLIED`, `BUDGET_ADVISORY` to `EventType` enum in `events.py`.
  - Added `state_store_enabled` and `state_store_dir` fields to `EngineConfig` in `executor.py`.
  - Registered all exports in `engine/__init__.py` and `__all__`.
  - 46 tests covering: CRUD, scope isolation, Pydantic round-trips (all 3 schemas), prefix query, path traversal, NullStateStore, concurrent writes, atomic write safety, protocol conformance, EngineConfig compat, event types, exports.

- [feat] **Phase 9D remaining subplans implemented (17-2 extension, 17-3 extension, 17-4).**
  - **17-2 task 6 — Graduated repair classification**: Extended `CausalPrinciple` with `repair_level` (5-level Literal: retry/prompt_fix/parameter_fix/structural_fix/redesign), `suggested_parameter_changes` (dict for parameter_fix), `structural_description` (text for structural_fix/redesign). Updated reflection prompt with classification guidelines and few-shot examples for each level. Updated `_normalize_principle()` and `_make_principle()` to handle new fields.
  - **17-3 task 6 — Parameter mutations (runtime graph mutation)**: Defined `ParameterMutation` model with whitelisted fields (`model`, `temperature`, `max_tokens`, `timeout_seconds`, `tool_config`) and topology-field rejection. Added `RuleLifecycleManager.create_mutation()` and `get_active_mutations()`. Applied mutations in `Engine._execute()` via temporary graph copy (`_apply_parameter_mutations()`). `RuleGenerator.generate_rule()` now returns `None` for `parameter_fix` principles.
  - **17-4 — Observability event wiring**: Added `RunManager._emit_learning_event()` helper for post-run event emissions. Wired all 11 `EventType` values: `ERROR_MEMORY_INDEXED` (in `_index_run_errors`), `ERROR_MEMORY_RETRIEVED` (in `LLMExecutor.execute`), `REFLECTION_STARTED`/`COMPLETED` (in RunManager), `RULE_GENERATED` (after principle persistence creates rules), `RULE_ACTIVATED` (in scheduler `_execute`), `RULE_EXPIRED`/`RULE_PRUNED`/`RULE_EFFECTIVENESS_UPDATE` (in `_track_rule_effectiveness`).
  - **Tests**: 37 new tests covering all new functionality (1758 total, 0 failures). Tests cover: CausalPrinciple extended fields, RuleGenerator parameter_fix skip, ParameterMutation model/validation, mutation CRUD/lifecycle, reflection repair_level parsing, scheduler parameter mutation application, _emit_learning_event helper, LLMExecutor ERROR_MEMORY_RETRIEVED event.

- [docs] **Todo-sync patch (Phase 9D ↔ Phase 11 handoff clarity).**
  - `17-5-workflow-experience-summaries.md`: status changed to **completed (bridge spec only)** with explicit execution guard; confirms implementation is deferred to `19-1`.
  - `17-self-evolving-orchestrator.md`: sub-plan table wording updated so `17-5` is clearly archival/handoff-only and not an executable implementation track.

- [docs] **Plan style consistency cleanup (17-x + todo).**
  - Updated Tier 3 phrasing in `17` and `17-3` to consistently reflect the canonical `parameter_fix` path: runtime graph mutation only.
  - Updated `todo.md` Tier 3 wording to match (hyperedge guidance + runtime parameter mutations).
  - Kept 9D/Phase-11 boundaries consistent across 17 parent and subplans while preserving existing scope decisions.

- [docs] **Plan cleanup pass (17-x consistency).**
  - `17-1`: normalized task order (`5` before deferred `6`) and aligned decision wording to defer cross-workflow/global reuse to Phase 11.
  - `17-4`/`17`/`todo.md`: corrected stale wording from "10 self-evolving EventType values" to **11**.
  - `17-3`: kept canonical `parameter_fix` path as runtime graph mutation and fixed subsection numbering consistency.

- [docs] **Plan 19 review patch — reuse-first policy and contract hardening.**
  - Resolved 17↔19 duplication: marked `17-5` as deferred bridge spec and promoted implementation ownership to `19-1` (also reflected in `todo.md` and `17-self-evolving-orchestrator.md`).
  - Hardened planner contract in `19-2`: declarative `PlanIR` is now primary; code generation is optional behind `planner_allow_code_generation=False` default and must run out-of-process if enabled.
  - Corrected adaptation contract in `19-2`: uses `GraphMutator.apply(graph_dict, plan)` shape explicitly.
  - Enforced canonical repair path in `19-3`: `parameter_fix` is runtime graph mutation only; removed parameter-fix hyperedge path.
  - Split tracking responsibilities in `19-3`: structural repairs now tracked via dedicated `RepairActionStore` (not `RuleLifecycleManager`, which remains hyperedge-specific).
  - Clarified `19-4` pause semantics: v1 pause is inter-step checkpoint-based only; hard mid-run interrupt remains deferred.
  - Aligned roadmap numbering in `development-plan.md`: Phase 11 = Meta-Orchestrator, Phase 12 = Author & Distribute.

- [docs] **Plan boundary patch for 9D vs Phase 11 + parameter-fix path.**
  - **17-3 clarified:** `parameter_fix` now has one canonical implementation path: **runtime graph mutation only** (no hyperedge-based parameter patching). Updated task breakdown and numbering consistency.
  - **17-1 and 17-2 rescope:** cross-workflow/global error/principle sharing work moved out of 9D and explicitly deferred to Phase 11 (`19-1-workflow-experience-memory`).
  - **17-5 rescope:** kept as workflow-local experience summaries in 9D; replaced global search/indexing tasks with a Phase-11 handoff export payload contract.
  - Updated parent plan (`17-self-evolving-orchestrator.md`) and `todo.md` descriptions to match the new scope boundaries.

- [docs] **Phase 11 Meta-Orchestrator — full plan suite (19, 19-1 through 19-4).** New phase for autonomous workflow planning, execution, and self-repair. Four subplans:
  - **19-1 Workflow Experience Memory:** `WorkflowExperience` model, `ExperienceStore`/`ExperienceIndex` (global RAG over past workflow summaries), cross-workflow principle sharing, experience consolidation pipeline.
  - **19-2 Workflow Planner:** `DiscoveryService` (tools, skills, patterns, past workflows), `WorkflowPlanner` LLM agent with three action modes (REUSE/ADAPT/GENERATE), builder DSL code generation sandbox, plan validation and safety checks.
  - **19-3 Structural Repair Engine:** `RepairClassifier` (prompt/parameter/structural/redesign levels), `ParameterRepairGenerator` (level 2), `StructuralRepairPlanner` (level 3 via GraphMutator), `RedesignTrigger` (level 4 via planner), `RepairEscalator` with bounded auto-escalation.
  - **19-4 Autonomous Execution Controller:** `MetaSession` state machine, `MetaController` outer loop (plan→execute→observe→diagnose→repair), pause-based human override protocol, experience feedback for both successes and failures, 10 new `EventType` values.
  - Renumbered old Phase 11 (Author & Distribute) to Phase 12.

- [docs] **Phase 9D extended: cross-workflow learning, graduated repair, workflow experience summaries.**
  - **17-1 extended (task 6):** Cross-workflow error memory — `error_memory_scope` config, dual-write to global collection, cross-workflow retrieval in ErrorContextProvider, global search endpoint.
  - **17-2 extended (tasks 5–6):** Cross-workflow principle sharing — `scope` field on CausalPrinciple, global PrincipleStore writes, `principle_scope` config. Graduated repair classification — `repair_level` field (retry/prompt_fix/parameter_fix/structural_fix/redesign), `suggested_parameter_changes`, `structural_description`. Reflection prompt updated with repair-level few-shot examples.
  - **17-3 extended (task 6):** Parameter mutations (level 2 repair) — RuleGenerator handles `parameter_fix` principles, model override rules, `get_active_mutations()`, safe-field guards.
  - **17-5 (new):** Workflow Experience Summaries — `WorkflowExperience` model, `ExperienceAggregator`, `ExperienceIndex` (global RAG), auto-describe/auto-tag workflows, REST endpoints for experience CRUD + semantic search. Bridge to future meta-orchestrator.

- [docs] **Plan 18 philosophy rework — no brute-force, agent-directed, externalized state.** Major reframe of all five token-optimization plans after competitive landscape review (Cursor, MemGPT/Letta, Beads, OpenAI Agents SDK, CrewAI, LangChain ACE, GPTCache, LLMLingua, production patterns). Key changes:
  - **18 (high-level):** Added "Design Philosophy" section establishing three principles: (1) agent-controlled context (MemGPT pattern), (2) externalized structured state (Beads pattern), (3) mechanical format efficiency. Renamed sub-plans: 18-1 → "Smart Context Assembly", 18-3 → "Agent-Directed Context Architecture". Updated success criteria to ban brute-force truncation.
  - **18-1:** Renamed from "Prompt Compression" to "Smart Context Assembly." Reframed `max_input_tokens` as advisory `target_input_tokens` (never blocks). Replaced `ContextPruner` with `ContextSelector` (defers low-relevance inputs to tool-accessible storage, never deletes). Added 3 new tasks: (7) JIT tool/schema loading — 20-40% context savings on tool-heavy workflows; (8) Schema/format pruning — mechanically strip unused fields, optional compact serialization; (9) Agent context tools (MemGPT pattern) — `search_context`, `read_context`, `read_state`, `list_available_context`.
  - **18-2:** Added query normalization before semantic cache embedding (GPTCache pattern — 61-68% hit rates).
  - **18-3:** Renamed from "Context Window Management" to "Agent-Directed Context Architecture." Replaced entire task 1 (brute-force truncation policies) with externalized structured state (`StateStore`, typed schemas: `LoopIterationState`, `TeamTurnState`, `NodeExecutionSummary`). Replaced task 2 (conversation windowing) with agent-directed history management (older messages to memory, manifest + context tools for retrieval). Reframed budget allocation as advisory `TokenBudgetAdvisor` (never blocks). Loop compaction preserved (smart, not brute force).
  - **18-4:** Added task 5 — evolving context playbooks (Microsoft ACE integration with Plan 17): validated optimizations → `CausalPrinciple` → `RuleGenerator` → persistent optimization rules with effectiveness tracking. Updated recommendations to remove all truncation suggestions; added JIT loading, agent context tools, field pruning, state externalization suggestions.

- [docs] **Plan 18 review patch pass (consistency hardening).** Applied a focused follow-up patch across token-optimization plans to align assumptions with current runtime contracts:
  - **18-4:** Corrected event-contract wording: `RETRIEVAL_COMPLETED` is already available for RAG attribution; memory attribution now explicitly calls for adding/finalizing a canonical memory-retrieval event (e.g., `MEMORY_RECALL`) during 14-3 implementation.
  - **18-2:** Corrected primary file mapping for session-memory cache coordination from `engine/memory_pipeline.py` to `engine/memory_store.py` (+ `engine/context_runtime.py`) to reflect actual persistence responsibilities.
  - **18-3:** Refined unified budget guidance so memory/RAG reservation is token-based (percentage + telemetry), while `retrieve -> rerank -> inject` remains retrieval-shape guidance rather than direct token reservation constants.
  - **18-1:** Added dual-write guardrails for `pass_by_reference` so artifact persistence remains default, but memory mirroring is policy-gated (`auto`/threshold/explicit policy) to avoid write amplification.

- [docs] **Plan 17-4: Observability & Event Wiring subplan.** Added `docs/plans/17-4-observability-event-wiring.md` — pure instrumentation pass to wire the 11 self-evolving `EventType` enums (currently dead) into their natural emission sites across `run_manager.py`, `executors/llm.py`, and `scheduler.py`. Covers error memory indexed/retrieved, reflection started/completed, rule generated/activated/expired/disabled/auto-disabled/pruned/effectiveness. Includes `_emit_learning_event()` helper design for post-run hooks and consistent data shape convention.

- [fix] **Phase 9D code review patches — 5 critical bugs + 1 suggestion.**
  - C1: Wired `ErrorContextProvider` + `_workflow_id` into `ExecutionContext` so `LLMExecutor` prompt augmentation actually fires at runtime (`scheduler.py`, `run_manager.py`)
  - C2: Fixed `extract_error_records` snapshot shape mismatch — errors are at top level in `RunRecord.snapshot()`, not nested under `result` (`error_memory.py`)
  - C3: Reflection principles now persisted after reflection runs — `ReflectionExecutor` includes principles in metadata, `RunManager._persist_reflection_principles()` extracts and stores via `PrincipleStore` (`reflection.py`, `run_manager.py`)
  - C4: Fixed effectiveness tracking — `record_application()` now called for all active rules; outcome tracking limited to rules with explicit `attach_to` node targets to prevent score inflation (`run_manager.py`)
  - C5: Path traversal fix — `_safe_segment()` validation in `RuleLifecycleManager` filesystem paths + `_validate_path_segment()` on all `/api/rules/` endpoints (`rule_generator.py`, `app.py`)
  - S1: Removed duplicate `node_started`/`node_completed` event emissions from `ReflectionExecutor` (scheduler already emits these) (`reflection.py`)

- [docs] **Plan 18 memory & RAG integration pass.** Audited all five token-optimization plans (`18`, `18-1`, `18-2`, `18-3`, `18-4`) to wire them into the existing memory (14-1/14-2/14-3) and RAG (9-1) infrastructure. Key additions:
  - **18 (high-level):** Added "Integration with Memory & RAG Systems" section with 7 cross-cutting integration points (embedding-based relevance, encode-to-memory pattern, compaction-to-memory pipeline, unified prompt budget, memory-aware cache coordination, memory/RAG token decomposition, summarization sharing). Updated baseline table with memory pipeline, session memory, RAG, and `estimate_tokens()` components. Upgraded dependency/sequencing item 5 from "independent" to "tightly integrated." Added memory/RAG success criteria.
  - **18-1 (prompt compression):** Task 1-4 now reuses existing `dan.utils.tokens.estimate_tokens()` (no new utility). Task 2-2 strengthened RAG embedding link for semantic relevance scoring. Task 4 expanded with encode-to-memory pattern (store large outputs in long-term memory, retrieve via `MemoryQuery` instead of inline passing). Task 5 now feeds summaries into `ShortTermMemory` and shares LLM summarization with 14-3's `ConsolidationPipeline`.
  - **18-2 (caching):** Task 2-3 adds session memory coordination — cross-run memoized results stored as 14-1 session memory entries. Task 2-5 adds memory-aware cache invalidation (`memory_dependency_keys`). Task 3-2 explicitly references `EmbeddingProvider`, `VectorStore`, and `VectorStoreFactory` from 9-1.
  - **18-3 (context window):** Task 1 adds truncation-to-memory persistence (`persist_dropped`). Task 2 adds windowed-out messages to memory + shared summarization with 14-3. Task 3 adds compaction-to-memory pipeline and delegates to `ShortTermMemory` buffer for unified compaction implementation. Task 4 adds unified budget accounting across all token sources (direct edges + memory + RAG) with configurable memory/RAG token reservation.
  - **18-4 (analytics):** `TokenBreakdown` now includes `memory_tokens` and `rag_tokens` fields. Waste detection adds unused memory/RAG context and redundant retrieval categories. Recommendations include encode-to-memory, disable unused retrieval, and memoize RAG nodes.

- [feat] **Phase 9D Self-Evolving Orchestrator — full implementation (17-1, 17-2, 17-3).** Implemented all three tiers of the self-evolving orchestrator:
  - **Tier 1 (Error Memory & Prompt Augmentation):** `ErrorRecord`, `ErrorCategory`, `extract_error_records()`, `ErrorMemoryIndex` (RAG indexing of run errors), `ErrorContextProvider` (prompt injection) in new `engine/error_memory.py`. Wired error capture → indexing → retrieval pipeline into `RunManager._enrich_and_persist()`. Prompt injection in `LLMExecutor.execute()` adds error-memory context as a system message before LLM call, scoped by node tags/types, composing before hyperedge pre_prompt. REST endpoints: `GET/DELETE /api/errors/{workflow_id}`, `GET /api/errors/{workflow_id}/search`.
  - **Tier 2 (Reflection Node):** `ReflectionNode(NodeBase)` model, `CausalPrinciple` and `PrincipleStore` in `engine/error_memory.py`, `ReflectionExecutor` in new `executors/reflection.py`. Gathers error data, builds structured LLM prompt with few-shot examples, parses JSON array, filters by confidence, deduplicates (exact_key). Reflection scheduling in `RunManager` with `on_failure`/`on_every_run`/`manual`/`disabled` triggers; `run_id` prefix `reflection-` prevents infinite loops.
  - **Tier 3 (Self-Generating Rules):** `RuleGenerator` (principle → hyperedge: negation→guardrail, preference→skill, interception→override), `GeneratedRule` with `effectiveness_score`, `RuleLifecycleManager` (filesystem-backed, TTL expiry, max cap eviction, pruning, rollback) in new `engine/rule_generator.py`. Runtime injection in scheduler `_execute()` deep-copies hyperedges + appends generated rules. Effectiveness tracking in `RunManager`. REST endpoints: `GET/DELETE /api/rules/{workflow_id}`, `POST .../disable|enable|approve`, `POST .../rollback`, `GET .../stats`.
  - **Shared:** 11 new `EventType` values, `EngineConfig` fields for all 3 tiers. All features opt-in/disabled by default.
- [test] **83 tests for self-evolving orchestrator.** `tests/test_engine/test_self_evolving.py`: Tier 1 (30), Tier 2 (11), Tier 3 (24). All pass in 0.25s; full suite 1703 tests pass.
- [fix] **Phase 9C code review patches + second-pass test review.**
  - C1: `AgentTeamExecutor` now returns `FAILED` when every turn errors (tracked `error_turns`/`total_turns_executed`)
  - C2: `OrchestratorExecutor` — explicit `halt_orchestrator` now overrides `all_failed` status
  - C3: `OrchestratorExecutor` — safety-bound exit without dispatching any work now returns `FAILED`
  - C3b: Fixed backward-compat condition (`and` → `or`) so prompt-only or model-only correctly falls back to static fan-out
  - S1: `VoteExecutor` now enforces `timeout_seconds` via `asyncio.wait_for`
  - S2: Updated `_KNOWN_NODE_TYPES` (+human, vote, agent_team) and `_COMPOSITE_NODE_TYPES` (+agent_team) in `validation/graph.py`
- [test] **Second-pass test hardening.**
  - Added `status == FAILED` assertions to safety-bound tests; strengthened timeout test assertions
  - Replaced all `status.value == "completed"` string comparisons with `NodeStatus.COMPLETED` enum in human node tests
  - Documented private `_store` access in shared-context test with `has()` guard
  - Added consensus completion condition tests for `AgentTeamNode` (triggers + no-trigger paths)
  - Added `FeedbackSelector.include` with nonexistent/mixed keys and exclude-nonexistent edge cases
  - Added `file_upload` and `rich` render mode tests for `HumanNode` (mode propagation + metadata)
  - Added `output_json_schema` roundtrip and default-none tests for `VoteNode`
  - Added vote timeout enforcement tests (slow provider + no-timeout baseline)
  - Total: 184 Phase 9C tests, 1721 suite-wide, 0 failures
- [feat] **ReflectionNode model & ReflectionExecutor (17-2, tasks 1-1, 1-3, 2-1 through 2-5).** Added `ReflectionNode(NodeBase)` in `models/nodes.py` with `reflection_prompt`, `reflection_model`, `source` (last_run/last_n_runs/error_index), `source_config`, `output_format`, `max_principles`, `min_confidence`, `dedup_strategy`. Registered in `NodeTypeRegistry`, added to discriminated `Node` union in `graph.py`. Created `executors/reflection.py` with `ReflectionExecutor`: gathers error data from upstream inputs, builds structured LLM prompt with few-shot examples, calls provider via registry, parses JSON array output with fallback extraction (```json fences, first [...] block), filters by min_confidence, deduplicates against existing principles (exact_key strategy; embedding_similarity falls back to exact_key as TODO). Returns `principles`, `principle_count`, `source`, `text` summary. Registered in scheduler `_register_defaults()`. All 1620 existing tests pass.
- [docs] **Plan 18 review-and-patch alignment pass.** Audited and corrected newly created token-optimization plans (`18`, `18-1`, `18-2`, `18-3`, `18-4`) against current runtime APIs and file layout. Patched provider file references (`*_provider.py`), validation/mutation route names, control-flow class names (`GateNode`/`WhileLoopNode`/`GateExecutor`), context model references (`CompactionRule` in `models/context.py`), and baseline assumptions (existing token/cost badges and run totals already implemented). Also tightened plan semantics: generic memoization key design, non-hardcoded cache discount handling, and consistency between loop compaction strategy names and current `CompactionStrategy` enum.
- [feat] Implement Phase 9C execution primitives (all 5 sub-plans):
  - 16-1: AgentTeamNode with group-chat multi-agent coordination, 4 turn strategies, @-routing, handoff protocol
  - 16-2: VoteNode with 5 voting strategies, cross-model ensemble, cost tracking
  - 16-3: HumanNode generalization with typed I/O schemas, 6 render modes, HumanRenderer protocol, backward-compat aliases
  - 16-4: FeedbackSelector for loop context management, artifact_ports, selective feedback filtering in scheduler and WhileLoopExecutor
  - 16-5: Async loop design — OrchestratorExecutor rewritten with LLM-driven event loop, dispatch_to_team/halt_orchestrator tools
- [test] Added 166 new tests across 5 test files (test_agent_team, test_vote, test_human_node, test_feedback_selector, test_orchestrator_async)
- [refactor] HumanInTheLoopNode → HumanNode with backward-compat subclass; HumanInTheLoopExecutor → HumanNodeExecutor with alias

- [feat] **HumanNode generalization — core runtime (16-3, tasks 1–3, 6, 9, 11).** Promoted `HumanInTheLoopNode` to a first-class `HumanNode` with typed I/O schemas, multiple render modes, and a rendering surface protocol. Changes:
  - **Model** (`models/control_flow.py`): New `HumanNode` class with `node_type: "human"`, `input_schema`, `output_schema`, `render_mode` (text/approval/form/selection/file_upload/rich), `options`, `instructions`, `render_target` (dialog/chat/both). `HumanInTheLoopNode` kept as subclass with `node_type: "human_in_the_loop"` for backward compat. Both in `Node` discriminated union.
  - **Rendering protocol** (`engine/executor.py`): `HumanRenderRequest`/`HumanRenderResponse` dataclasses, `HumanRenderer` protocol, `LegacyCallbackRenderer` (wraps old callbacks), `AutoRenderer` (headless/default), `ProgrammaticRenderer` (scripted responses for testing). `ExecutionContext` auto-wraps legacy callback.
  - **Executor** (`executors/control_flow.py`): `HumanNodeExecutor` replaces old executor — builds `HumanRenderRequest`, uses renderer protocol, validates `output_schema` with up to 2 retries, emits `human_input_needed` + `human_input_received` events. `HumanInTheLoopExecutor = HumanNodeExecutor` alias.
  - **Scheduler** (`engine/scheduler.py`): Registers same executor instance for both `"human"` and `"human_in_the_loop"` node types.
  - **Events** (`engine/events.py`): Added `HUMAN_INPUT_RECEIVED` event type.
  - **Tests**: 40 tests in `tests/test_engine/test_human_node.py` covering model serialization (4), backward compat (7), render models (4), renderers (7), executor integration (14), scheduler registration (1), context auto-wrap (3). All pass.

- [feat] **AgentTeamExecutor hardening & test expansion (16-1, tasks 3/9).** Fixed two gaps in `AgentTeamExecutor`: (1) `handoff_policy="moderator_only"` now suppresses agent-initiated handoffs (was ignored), (2) `sequential` turn strategy now stops after all agents have spoken once regardless of `completion_condition` (was running stuck on last agent until `max_turns`). Extracted `_parse_mentions()` static method from inline regex for testability. Expanded test suite from 26 to 41 tests covering: model serialization (4), @-mention regex parsing (10), round-robin strategy (3), sequential strategy (2), handoff processing including moderator_only blocking + handoff context + self-handoff rejection (4), max_turns safety bound (2), all_responded completion (2), event emission (3), edge cases — <2 agents, subgraph error recovery, shared context persistence, conversation output (4), static helpers (7). All 41 pass; existing orchestrator/parallel-subagents suites unaffected.

- [feat] **AgentTeamExecutor scheduler registration (16-1, task 4-1).** Registered `AgentTeamExecutor` in scheduler `_register_defaults()` for `node_type="agent_team"`, completing engine integration.

- [docs] **Phase 10 — Token Optimization (Plan 18).** Promoted backlog "Optimize token usage" to a full phase. Created high-level plan `18-token-optimization.md` and four sub-plans: `18-1-prompt-compression.md` (context pruning, reference passing, input summarization), `18-2-caching-layer.md` (provider prompt caching, node memoization, semantic cache), `18-3-context-window-management.md` (truncation policies, conversation windowing, loop compaction, token budgets), `18-4-token-analytics.md` (per-node breakdown, waste detection, optimization recommendations, editor dashboard). Renamed old Phase 10 "Author & Distribute" to Phase 11. Updated `todo.md`, `development-plan.md` roadmap.

- [feat] **OrchestratorExecutor clean rewrite (16-5).** Replaced the full `OrchestratorExecutor` class in `executors/control_flow.py` with a clean implementation separating LLM-driven orchestration from static fan-out. Key structure: `execute()` dispatches to `_execute_llm_driven()` or `_execute_static_fanout()` based on presence of `orchestrator_prompt`/`orchestrator_model`. LLM path: builds conversation with system prompt + initial inputs, calls LLM with `dispatch_to_team`/`halt_orchestrator` tool schemas, processes tool calls, waits for team completions via `asyncio.wait(FIRST_COMPLETED)`, formats results as user messages, loops. Static path: spawns all teams, polls event queue, respects `completion_condition`. Shared helpers: `_build_team_inputs`, `_call_orchestrator_llm` (provider registry + OpenAI fallback), `_collect_remaining`, `_format_completion_events`, `_drain_events`. Added `CompletionResult` top-level import. Created `tests/test_engine/test_orchestrator_async.py` with 21 async tests: static fan-out backward compat (5), LLM dispatch sequential/parallel/halt/unknown-team/duplicate/nudge (6), safety bounds max_llm_calls/max_iterations (2), timeout (2), conversation flow system-msg/user-msg/tools/events (4), input mappings (1), orchestrator log (1). All 11 existing tests in `test_orchestrator.py` pass unchanged.

- [feat] **Feedback filtering runtime for loops (16-4, tasks 1–4).** Implemented `FeedbackSelector` model in `models/context.py` with `include`/`exclude`/`rename`/`transform` fields and mutual-exclusion validation. Added `feedback_selector` and `artifact_ports` fields to `GateNode` and `WhileLoopNode`. Built `_apply_feedback_selector` helper in `executors/control_flow.py`. Integrated feedback filtering in scheduler `_iterate_cycle` (lines 802–816, 867–874) and `WhileLoopExecutor.execute()` (lines 287–308, 337–343). Artifact ports extracted before feedback filtering, accumulated via `LocalStateManager`, merged into final output. Both mechanisms cooperate with `state_schema`. Backward compatible by default.

- [test] **FeedbackSelector test suite (16-4).** Created `tests/test_engine/test_feedback_selector.py` with 20 tests covering: FeedbackSelector model serialization/validation/defaults (3), `_apply_feedback_selector` helper — include/exclude/rename/transform/combined/noop/non-dict-wrap (7), gate while-loop engine-level integration — include/exclude/artifact_ports/backward-compat (4), WhileLoopExecutor — include/exclude/rename/artifact_ports/backward-compat (5), state_schema + feedback_selector cooperation (1). All 20 pass; full engine test suite (636 tests) passes with no regressions.

- [feat] **OrchestratorExecutor rewrite with LLM dispatch (16-5).** Rewrote `OrchestratorExecutor` in `executors/control_flow.py` with clean separation between static fan-out fallback and LLM-driven event loop. Key improvements: emits `parallel_branch_completed` events when individual teams finish, fixes timeout remaining calculation to avoid negative values, adds debug logging via module-level logger, extracts `_resolve_model()` helper, handles `TypeError` in JSON argument parsing. Added 27 new async tests in `tests/test_engine/test_orchestrator_async.py` covering LLM-driven dispatch (sequential + parallel + error correction + duplicate rejection), backward-compat static fan-out (8 tests), safety bounds (max_llm_calls + max_iterations), timeout handling, concurrent team management (parallel dispatch + redispatch after completion), halt_orchestrator (immediate + cancel running + mixed batch), tool schema validation, event callback restoration (including on error), and no-tool-call nudge behavior. All 11 existing orchestrator tests pass unchanged.

- [fix] **OrchestratorExecutor bug fixes (16-5).** Fixed two bugs in the LLM-driven orchestrator:
  - **Backward-compat condition** — changed `use_llm` gate from `not prompt and model is None` (AND) to `bool(prompt) and model is not None`, so that setting only `orchestrator_prompt` without `orchestrator_model` correctly falls back to static fan-out.
  - **Halt overrides all_failed** — when the LLM explicitly calls `halt_orchestrator`, the result is now `COMPLETED` regardless of team failures, since the orchestrator intentionally decided to stop. `all_failed → FAILED` only triggers when the LLM did not halt.
  Expanded test suite from 19 to 25 tests: added backward-compat model-only fanout, `all_teams_fail_without_halt` (verifying FAILED when LLM doesn't halt), duplicate dispatch in same batch, static fanout events/failures.

- [feat] **LLM-driven OrchestratorExecutor (16-5).** Rewrote `OrchestratorExecutor` in `executors/control_flow.py` as a true event-driven LLM orchestrator. The orchestrator LLM is invoked reactively when teams complete, using two tool calls: `dispatch_to_team(team_name, inputs)` to start team sub-graphs and `halt_orchestrator(reason, final_result)` to cleanly exit. Conversation history accumulates system prompt + initial inputs + team completion events as user messages. Concurrency management prevents re-dispatching already-running teams (error feedback to LLM). Safety bounds via `max_llm_calls` and `max_iterations`. Falls back to static fan-out when `orchestrator_prompt`/`orchestrator_model` are unset (backward compat). All 11 existing orchestrator tests pass unchanged.

- [feat] **AgentTeamExecutor implementation (16-1).** Built `AgentTeamExecutor` in `executors/control_flow.py` — group-chat style multi-agent coordination with turn-taking loop, four turn strategies (`round_robin`, `sequential`, `moderator`, `free_form`), `@agent_name` mention parsing for routing, structured `HandoffRequest` processing (transfer + consult with return-to-source), conversation compaction via `CompactionRule`, `TeamConversation` state stored in shared context, and event lifecycle (`TEAM_TURN_STARTED`, `TEAM_TURN_COMPLETED`, `TEAM_HANDOFF`, `TEAM_COMPLETED`). Added 26 tests in `tests/test_engine/test_agent_team.py` covering model serialization (4), @-routing regex parsing (5), round-robin turn order + history + roster (3), sequential strategy (1), free-form @-routing (1), handoff processing with transfer/consult-return/self-handoff (3), completion conditions: max_turns/all_responded/moderator_halt (3), event emission (2), edge cases: <2 agents/exception recovery/shared context/input mappings (4).

- [feat] **VoteExecutor registration & expanded test suite (16-2).** Registered `VoteExecutor` in scheduler's default executor table (`engine/scheduler.py`). Expanded `tests/test_engine/test_vote.py` from 32 to 44 tests: added model serialization with VoteConfig round-trip (5), prompt rendering edge cases (4), candidate round-robin wrap-around (3), majority tie-break and whitespace normalization (4), weighted voting with custom expressions and default fallback (3), judge fallback on unparseable index (3), unanimous threshold partial agreement (3), partial failure at exact threshold boundary (3), same-model 5-vote verification (2), cross-model 5-vote round-robin distribution (3), event data field assertions (3), cost tracking with unknown model (2), output/metadata structure validation (3), system prompt inclusion/exclusion (2), semaphore parallelism concurrency tracking (1).
- [feat] **VoteExecutor implementation (16-2).** Built `VoteExecutor` in `executors/control_flow.py` — fans out prompt to N LLM calls (round-robin across candidates, respecting parallelism via semaphore), collects results, applies configurable voting strategy (`majority`, `weighted`, `best_of_n`/`judge`, `unanimous`), handles partial failures (proceeds with ≥ceil(N/2) successes), tracks per-vote costs, and emits `VOTE_STARTED`/`VOTE_CAST`/`VOTE_COMPLETED` events.

- [docs] **Phase 9D plan review-and-patch pass (17-1/17-2/17-3).** Verified newly scaffolded plans against current runtime APIs and corrected implementation-level mismatches:
  - **17-1 injection path corrected:** moved Tier 1 prompt augmentation from a proposed dynamic `Hyperedge` callable field to `LLMExecutor.execute()` prompt assembly, because prompt-time retrieval needs `rendered_prompt` + `inputs` + `ExecutionContext` (not available in `HyperedgeResolver.apply_pre_prompt()` signature).
  - **17-1 dedup made backend-safe:** replaced metadata pre-scan dedup with deterministic document IDs (`error:{run_id}:{node_id}`) to rely on backend upsert/replace behavior across memory/faiss/chroma stores.
  - **17-2 node registration completeness:** added explicit requirement to update discriminated `Node` union in `models/graph.py` (in addition to `NodeTypeRegistry`) so `Graph.model_validate()` can deserialize `ReflectionNode`.
  - **17-2 schema terminology correction:** changed `output_schema` wording to JSON Schema + `OutputNormalizer` aligned with existing `LLMOperator.output_json_schema` behavior.
  - **17-3 hook/validator alignment:** corrected hyperedge hook baseline to match current `VALID_HOOKS_BY_TYPE`, fixed `evaluate_condition()` example to use output-variable namespace (not `output.get(...)` object access), and replaced nonexistent scheduler `_prepare()` reference with `_execute()` setup path.
  - **Plan consistency updates:** synchronized top-level `17-self-evolving-orchestrator` primary-file references with 17-1 implementation path and refreshed affected task/decision bullets.

- [fix] **Phase 9B second review-pass fixes (15-3).** Three additional issues caught and patched during follow-up code review:
  - **Unconditional ModelSelector/CostTracker instantiation** — `_execute()` in `scheduler.py` now always creates `CostTracker` and `ModelSelector`, even when no global `run_budget` or `default_model_policy` is set. Previously, node-level `model_policy` definitions were silently ignored without a global config.
  - **CostTracker state checkpointing** — `_save_checkpoint()` now snapshots `CostTracker` state; `resume()` restores it. Previously, pausing and resuming a run reset accumulated cost to zero, bypassing budget limits.
  - **Restored `_should_skip` back-edge logic** — re-added gate/back-edge handling in `scheduler.py` that was inadvertently removed by a prior commit. Without it, while-loop data propagation failed (counter and artifact ports not fed back to loop body).

- [fix] **Phase 9B post-review bug fixes (15-1/15-2/15-3).** Patched 7 issues found during code review:
  - **model_policy dict crash** — `ModelSelector.resolve_effective_policy()` now deserializes raw dicts into `ModelPolicy` via Pydantic `TypeAdapter`, preventing `AttributeError` when `model_policy` comes from JSON.
  - **attach_to_subgraph propagation** — `HyperedgeResolver` accepts `parent_scope_node_id`; child resolvers created in `_run_subgraph` now know which composite they're inside, so parent `attach_to_subgraph` hyperedges correctly match child nodes.
  - **ModelSelector/CostTracker wired into execution** — `Scheduler._execute()` creates `CostTracker` + `ModelSelector` when `run_budget`/`default_model_policy` are set; passed through `_make_context` → `_run_subgraph` → child contexts. `LLMExecutor` and `RouterExecutor` call `model_selector.select()` before LLM calls and `cost_tracker.record()` after.
  - **Decompiler round-trip** — `_render_hyperedge_line()` now slugifies names (hyphens, lowercase); compiler regex relaxed from `\S+\.md` to `.+?\.md` to accept both slugified and hand-written filenames.
  - **Validation warning classification** — added `"warning:"` to `_VALIDATION_WARNING_PATTERNS` in both `scheduler.py` and `compiler.py`, so hyperedge validation warnings are no longer misclassified as fatal errors.
  - **Deterministic inline IDs** — replaced `hash()` (PYTHONHASHSEED-dependent) with `hashlib.sha256` in `compiler.py` for reproducible hyperedge IDs across runs.
  - **Unused variable** — removed stale `has_source` from `_render_hyperedge_line()`.
  - **Validation:** 1454 passed, 0 failed, 0 regressions.

- [fix] **Run history / compare post-review patch pass (13-1 hardening):**
  - **Route precedence fix:** moved `GET /api/runs/compare` above `GET /api/runs/{run_id}` in `app.py` to prevent dynamic-route shadowing.
  - **Compare UX correctness fix:** `RunHistoryPanel` now compares the user-selected source run (`compareSource`) against the clicked target run, instead of auto-picking the first non-target run.
  - **Deep-link parity fix:** `RunRefBlock` now includes a "View history" action (in addition to "View logs"), matching `RunOutputBlock` deep-link behavior.
  - **New regression tests:** added `tests/test_server/test_run_api_compare.py` for route shadowing + compare delta behavior, and `editor/src/lib/__tests__/runHistory.test.ts` for compare selection logic.
  - **Validation:** backend full suite `1454 passed`; editor Vitest suite `35 passed`.

- [docs] **Phase 9C final plan review patch.** One last consistency/factual pass across `16-*` plans:
  - Added and linked **16-5 Async Loop Design** cleanly across plan tree and `todo.md`.
  - Corrected `16-3` executor task inconsistency (`strict_mode` reference removed; failure/default-action behavior clarified after schema retries).
  - Refined `16-5` baseline/task wording to match actual runtime behavior: current orchestrator is static fan-out + event polling (not sequential iterations); `_run_subgraph` row now reflects missing redispatch loop in executor rather than missing core mechanism.
  - Added `RouterExecutor._call_router_llm()` as concrete precedent for LLM calls in control-flow executors within `16-5`.
  - Repaired malformed backlog link in `todo.md` (`7-8-workflow-node-api-hardening`) caused by an accidental line break.

- [feat] **Phase 9B — Behavior Modifiers fully implemented.** Hyperedges and dynamic model selection now ship as working code (1452 tests pass, 0 regressions). Implementation was parallelized across 4 agents:
  - **15-1 Hyperedge Engine Runtime:** `models/hyperedges.py` (Hyperedge, ValidationResult, HyperedgeViolation), `engine/hyperedge_runtime.py` (HyperedgeResolver with resolve, apply_pre_prompt, apply_post_output, apply_tool_call, apply_validation), hooks in scheduler (_execute_node post-output/validation, _run_subgraph propagation), LLMExecutor (pre_prompt injection), ToolExecutor (tool_call interception), 7 new event types, EngineConfig.hyperedge_enforcement, graph validation for hyperedges, SKILL_LIBRARY → Hyperedge migration, add/remove/edit_hyperedge mutation ops. 45 new tests.
  - **15-2 Hyperedge Markdown Syntax:** HyperedgeSpec IR in loader/models.py, load_hyperedge() parser for skill/rule .md files, ## Skills/## Rules workflow sections with scope overrides (@nodes, @type, @tags, @subgraph, @global) and inline definitions, decompiler emission, builder wf.skill()/wf.rule() API, builder compiler/decompiler, 2 file-based skills in src/dan/skills/. 27 new tests.
  - **15-3 Dynamic Model Selection:** ModelPolicy discriminated union (static/budget/cascade/capability/router) in providers/model_policy.py, ModelSelector with 5-strategy dispatch + 4-level precedence resolution, CostTracker with per-node/per-run accumulation + budget enforcement + snapshot/restore, ModelCapabilityRegistry covering 9 models with filter-by-capability and 3 sort preferences (cheapest/fastest/strongest), CascadeHandler for multi-step fallback, BudgetExceededError. 52 new tests.
  - **15-1/15-2 Validation & Migration:** validate_graph() extended with 5 hyperedge checks (dangling attach_to, unknown types, non-composite subgraph refs, duplicate IDs, hook/type compat), get_builtin_hyperedges() conversion, ApplySkill now creates graph-level hyperedges (legacy fallback preserved). 26 new tests.

- [docs] **Phase 9D plans scaffolded — Self-Evolving Orchestrator.** Promoted from Backlog to Phase 9D. Added top-level plan `17-self-evolving-orchestrator` with three sub-plans; converted `docs/todo.md` Phase 9D bullets into linked checklist items. All plans grounded against actual codebase state (OrchestratorExecutor, RAG subsystem, MemoryStore, RunStore, HyperedgeResolver):
  - **17-self-evolving-orchestrator:** Top-level plan with 16-component baseline table, dependency analysis (Tier 1 immediately feasible, Tier 2 benefits from 13-2, Tier 3 needs stable hyperedge runtime — already partially landed), success criteria, decisions. Critical finding: `OrchestratorExecutor` never calls LLM despite declaring `orchestrator_prompt`/`orchestrator_model` — Tier 1 targets LLM nodes via dynamic hyperedge `pre_prompt` instead.
  - **17-1 (Error Memory & Prompt Augmentation — Tier 1):** `ErrorRecord` model with 11 fields + `ErrorCategory` enum (7 categories), `extract_error_records()` heuristic classifier, `ErrorMemoryIndex` wrapping `Indexer`/`VectorStore` with per-workflow collections, `ErrorContextProvider` for dynamic prompt generation, dynamic `content_provider` extension on `HyperedgeResolver.apply_pre_prompt()`, `error_aware` tag for opt-in injection, REST endpoints, `EngineConfig` flags. 10-component baseline table. Primary files: `engine/error_memory.py` (new), `server/run_manager.py`, `engine/hyperedge_runtime.py`.
  - **17-2 (Reflection Node — Tier 2):** `ReflectionNode(NodeBase)` model with source/output_format/dedup config, `CausalPrinciple` model (condition/action/reason/confidence/tags), `ReflectionExecutor` with 3 source-gathering methods, `PrincipleStore` helper wrapping `MemoryStore`, auto-trigger logic in `RunManager` (on_failure/on_every_run/manual/disabled), builder `wf.reflection()`, markdown `type: reflection`, editor palette integration. Reflection runs use `run_id` prefix `reflection-` to prevent infinite trigger loops. 11-component baseline table.
  - **17-3 (Self-Generating Rules — Tier 3):** `RuleGenerator` (principle → hyperedge conversion with semantic classification), `GeneratedRule` model with `effectiveness_score`, `RuleLifecycleManager` (filesystem-backed, CRUD, effectiveness tracking, TTL expiry, max-cap eviction), runtime injection at engine startup, auto-disable on regression, human approval gate (opt-in), 7 new `EventType` values for audit trail, REST endpoints for rule management, editor "Generated Rules" panel. 11-component baseline table.

- [feat] **Plan 13-1 Tasks 3-3, 3-4, 4-1–4-4 — Run history comparison & editor panel:**
  - **Task 3-3 — Comparison endpoint:** Added `GET /api/runs/compare?run_a={id}&run_b={id}` to `app.py`. Aligns two runs by node execution order, computes per-node diffs (status changes, token deltas) and aggregate summary (elapsed/token/cost deltas).
  - **Task 3-4 — Frontend API helpers:** Added `listRuns(filters)`, `getRunEvents(runId)`, `compareRuns(runA, runB)` to `editor/src/lib/api.ts` with full TypeScript interfaces (`RunSummary`, `RunListResponse`, `NodeDiff`, `CompareRunsResponse`).
  - **Task 4-1 — Run history panel:** New `RunHistoryPanel.tsx` component added as "History" tab in the bottom panel. Filterable run list with status badge, elapsed time, token count, cost, and "Compare…" action per row.
  - **Task 4-2 — Replay mode:** Click any history entry to load its persisted event stream into an in-panel replay view with node grouping and event-type/node-id filters.
  - **Task 4-3 — Comparison view:** Side-by-side diff view with aggregate delta header (time/tokens/cost) and per-node comparison table (status change highlighting, token deltas).
  - **Task 4-4 — Deep-links:** Added "View in History" link to `RunOutputBlock.tsx`. New `historyFocusCounter`/`historyFocusRunId` in `useGraphStore` to trigger tab switch + auto-replay from chat.
  - **Tests:** 2 new comparison tests (`TestRunComparison`). All 17 run_store tests pass.
  - **Docs:** Updated `architecture.md` (new `run_store.py`, `RunHistoryPanel.tsx` entries).

- [docs] **Phase 9C plans scaffolded.** Added top-level plan `16-execution-primitives` with five sub-plans; converted `docs/todo.md` Phase 9C bullets into linked checklist items. All plans grounded against actual codebase state (models, executors, scheduler, editor components):
  - **16-execution-primitives:** Top-level plan with baseline table (15 components), dependency/sequencing analysis (all 5 sub-plans independent, parallelizable), success criteria, decisions.
  - **16-1 (Agent Teams):** `AgentTeamNode` model, `TeamMessage`/`HandoffRequest` protocol, `AgentTeamExecutor` with 4 turn strategies (round_robin, sequential, moderator, free_form), `@`-routing syntax, handoff processing (transfer/consult), conversation compaction, builder `wf.team()`/`wf.group_chat()`, markdown `## Team` section, editor integration. Grounded against existing `OrchestratorNode`/`OrchestratorExecutor` patterns. 28 baseline components audited.
  - **16-2 (Voting / Ensemble):** `VoteNode` model with 5 vote strategies (majority, weighted, best_of_n, judge, unanimous), multi-model fan-out via `ProviderRegistry`, structured output field-level voting, partial failure tolerance, builder `wf.vote()`/`wf.ensemble()`, markdown `type: vote` agent, editor vote results view. Grounded against `ParallelSubagentsNode`/`ForEachNode`/`ReduceNode` fan-out patterns.
  - **16-3 (HumanNode Generalization):** Rename to `HumanNode` (with alias), typed I/O schemas, 6 render modes (text/approval/form/selection/file_upload/rich), `HumanRenderer` protocol, `LegacyCallbackRenderer`/`CLIHumanRenderer`/`AutoRenderer`/`ProgrammaticRenderer`, chat-as-renderer pattern, multi-mode `HumanInputDialog`, builder `wf.human()`/`wf.approval()`/`wf.form()`, migration path. Grounded against `HumanInTheLoopNode`/`HumanInTheLoopExecutor`/`HumanInputDialog.tsx`.
  - **16-4 (Loop Context Manager):** New `FeedbackSelector` model with `include`/`exclude`/`rename`/`transform`, `feedback_selector` and `artifact_ports` on `GateNode`/`WhileLoopNode`, scheduler `_execute_with_cycles()` inline filtering, `WhileLoopExecutor` per-iteration filtering, artifact accumulation, builder `feedback`/`artifacts` params on `wf.while_loop()`, markdown loop flow syntax. Grounded against actual `ContextProjection` fields (determined NOT reusable — has `context_keys`/`local_state_keys`/`artifact_uris`, not `include`/`exclude`/`rename`/`transform`), `CompactionRule`, `state_schema` mechanics, `_execute_with_cycles` feedback injection inline block.
  - **16-5 (Async Loop Design):** Promoted from backlog. Re-architects `OrchestratorNode`/`OrchestratorExecutor` into a true event-driven LLM process. Adds `dispatch_to_team` tool calling, dynamic sub-graph restarts via `_run_subgraph` isolation, and max LLM calls safety bounds.
- [docs] **Phase 9C plan review — 7 factual corrections.** Verified all baseline claims against actual codebase; found 7 errors:
  - **`ContextProjection` fields are NOT `include`/`exclude`/`rename`/`transform`** — actual model has `name`/`context_keys`/`local_state_keys`/`artifact_uris`. Corrected 16-4: `FeedbackSelector` is a new model, not a reuse. Updated all task/decision/note references.
  - **`_inject_feedback` function does not exist** — feedback injection is inline in `_execute_with_cycles` (~line 774). Corrected 16-4 tasks 3-1 and Primary Files.
  - **`_gate_meta` dict does not exist** — gate cycles use `cycle_regions`/`back_edges` local variables. Corrected 16-4 baseline.
  - **Builder method is `wf.parallel_subagents()`, not `wf.parallel()`** — corrected 16-1 baseline.
  - **`register_human_input_handler()` does not exist on RunManager** — actual methods: `_make_human_input_callback()` (private), `submit_human_input()`, `get_pending_human_inputs()`. Corrected 16-3 baseline.
  - **`human_input_callback` return type is `Awaitable[dict[str, Any]]`, not `Awaitable[Any]`** — corrected 16-3 baseline.
  - **Event types count is 26, not "14+"** — corrected 16-1 baseline.

- [feat] **Plan 13-1 Tasks 1–2, 3-1, 3-2 — Run observability & history foundation:**
  - **Task 1 — Enrich RunRecord + RunStore persistence:** Extended `RunRecord` with `total_prompt_tokens`, `total_completion_tokens`, `total_tokens`, `total_cost`, `elapsed_seconds`, `node_usage`, `model`. Added `from_summary()` for startup hydration. New `_enrich_and_persist()` in `RunManager` wires `RunResult.metadata` + `estimate_cost()` into the record at completion. New `RunStore` (`server/run_store.py`): filesystem persistence with atomic writes, one JSON per run (`runs/{workflow_id}/{run_id}.json`), `DAN_RUNS_DIR` env var.
  - **Task 1-3 — Startup hydration:** `RunManager.__init__` loads historical summaries from `RunStore` so `list_runs()` survives server restarts.
  - **Task 1-4 — Retention:** `DAN_RUN_RETENTION_DAYS` env var; age-based cleanup on startup.
  - **Task 2-1 — EventLog writer:** `_event_callback` appends every event to `{run_id}.events.jsonl` via `RunStore.append_event()`.
  - **Task 2-4 — EventLog reader:** `RunStore.load_events()` with `node_id`/`event_type` filters.
  - **Task 3-1 — Enriched `GET /api/runs`:** Added `workflow_id`, `status`, `after`/`before`, `limit`/`offset` query params.
  - **Task 3-2 — REST events endpoint:** `GET /api/runs/{run_id}/events` loads persisted events for completed runs; falls back to memory for live runs.
  - **Tests:** 15 new tests (`test_run_store.py`). Full suite: 1300 passed, 0 failures.

## 2026-03-03
- [fix] **Phase 9A review-patch pass — 7 findings fixed (1 critical, 3 high, 3 medium).** All 1300 tests pass (0 regressions).
  - **Critical — path traversal:** Added `_safe_path_segment()` in `memory_store.py` and `_validate_path_segment()` regex guard in REST endpoints to prevent `../` escapes in `workflow_id`/`session_id`.
  - **High — empty-allowlist bypass:** `ScopedContextView` now uses `is not None` checks so `reads_global=[]` / `writes_global=[]` correctly means "deny all" instead of falling through to full passthrough.
  - **High — subgraph memory propagation:** `_run_subgraph` now threads `session_id`, `memory_writes`, and `short_term_memory` through to child `_make_context` calls.
  - **High — DELETE mode + scope routing:** `FileSystemMemoryStore.write()` delegates to `delete()` for `WriteMode.DELETE`; `_flush_memory_writes` routes `GLOBAL` → `_global/_global`, `WORKFLOW` → `<wf>/_default`, `SESSION` → `<wf>/<sess>`.
  - **Medium — signal semantics:** Non-sticky signals now store in `LocalStateManager.__signals__` (parent-only); only sticky signals emit global engine events.
  - **Medium — API memory dir:** `_get_memory_store()` now reads from `RunManager.engine_config.memory_dir` instead of an independent env var.
  - **Medium — preloaded keys:** `_preload_memory` writes the raw key to declared context (for context-edge reads) and `memory:<key>` as a secondary namespace.

- [docs] **Phase 9B planning review-and-patch pass:** Added and linked `15-*` plan scaffold for Phase 9B (`15-behavior-modifiers`, `15-1-hyperedge-engine-runtime`, `15-2-hyperedge-markdown-syntax`, `15-3-dynamic-model-selection`) in `docs/todo.md`, then patched for consistency and implementability.
  - Aligned model-policy strategy naming across plans (`capability` in scope; learned assignment explicitly deferred pending 13-1/14-* signals).
  - Clarified hyperedge attachment semantics with explicit global scope (`attach_globally`) and child-scope re-matching during propagation.
  - Tightened scheduler/executor hook split (`pre_prompt` and `tool_call` executor-level; shared post-output/validation scheduler-level).
  - Corrected `15-3` file ownership for `model_policy` fields (`LLMOperator` in `models/nodes.py`; `OrchestratorNode`/`RouterNode` in `models/control_flow.py`) and documented budget-precedence behavior.

## 2026-03-02
- [feat] **Phase 9A — Memory & Cross-Run State complete.** Implemented all three sub-plans (14-1, 14-2, 14-3) with 65 new tests (1285 total, 0 regressions).
  - **14-1 (Session Memory):** `MemoryEntry`/`MemoryWriteRequest` models in `engine/memory.py`. `MemoryStore` protocol + `FileSystemMemoryStore` in `engine/memory_store.py` (atomic writes, index sidecar, CRUD, APPEND/MERGE modes). `Engine.run()`/`resume()` accept `session_id`/`workflow_id`; pre-loads memory into SharedContextStore. `ExecutionContext.write_memory()` queues writes flushed at checkpoint boundaries. `RunManager.start_run()`/`resume_run()` pass `session_id` through. REST endpoints: `GET/DELETE /api/memory/{workflow_id}/{session_id}`, `GET /api/memory/{workflow_id}/{session_id}/{key}`, `GET /api/memory/{workflow_id}`. `EngineConfig.memory_dir`/`memory_enabled` flags. 22 tests.
  - **14-2 (Context Scoping):** `BoundaryContract`/`SignalSpec` models in `models/context.py`. `boundary_contract` field on all composite node types (Composite, WhileLoop, ForEach, ParallelSubagents, Orchestrator). `ScopedContextView` in `context_runtime.py` — scoped read/write isolation with `reads_global`/`writes_global` whitelists and `propagate_to_parent()`. `_run_subgraph` creates scoped views when `boundary_enforcement=True` and `boundary_contract` is present. `ExecutionContext.emit_signal()` for sticky/non-sticky upward signals. `EngineConfig.boundary_enforcement` flag. 16 tests.
  - **14-3 (Long-Chain Memory):** `MemoryPolicyConfig`/`MemoryItem`/`ShortTermMemory` in `engine/memory_pipeline.py`. Activated all `CompactionStrategy` variants (sliding_window, keep_last, diff_based, summarize→fallback). `apply_compaction()` standalone function. `ConsolidationPipeline` for short-term → long-term transfer. `ExecutionContext.remember()`/`recall()` API. Extracted `estimate_tokens()` to `utils/tokens.py`. `EngineConfig.memory_pipeline_enabled` flag. 27 tests.

## 2026-03-02
- [fix] **Test suite stabilization:** Fixed 7 failing tests related to while-loop execution and intent building. Updated `test_compiler.py` to include `orchestrator` in output ports. Fixed `test_build_from_intent.py` to handle prompt fallback properly and updated `ChatMessageRequest` default mode from `mutate` to `agent`. Corrected graph structure in `TestWhileLoopRealExecutors` and `TestSimpleWhileGateLoop` to properly emulate `while-do` loop evaluation where the body is skipped on pass 0, aligning with the engine's intentional cycle scheduling design. All 1220+ tests now passing.

## 2026-03-02
- [docs] **Phase 9A plans second review — factual corrections and structural improvements.** Verified 14 baseline claims against actual codebase; found 2 factual errors and 1 phantom reference:
  - **CompactionRule is NOT dead code** — `WhileLoopExecutor._apply_compaction()` (`control_flow.py:307–324`) already implements `keep_last`/`sliding_window`/`diff_based`. Corrected all four `14-*` plans: baseline tables, success criteria, decisions, and task descriptions now say "partially active" and "extend activation" instead of "dead code" / "activate".
  - **MergeStrategy used by both ParallelSubagentsExecutor AND ForEachExecutor** — corrected `14-2` baseline table (was "only ParallelSubagentsExecutor").
  - **"14 memory policy defaults from architecture.md" don't exist there** — they were originally in `todo.md` (removed during restructuring). Inlined the full defaults list into `14-3` task 1-5 as the authoritative location.
  - **VectorStore protocol** — corrected method name from `delete` to `delete_by_ids` in `14-3` baseline.
  - **14-1:** Clarified `session_id` contract for non-chat runs (workflow-scoped default session); noted eager pre-load strategy.
  - **14-2:** Added Implementation Phasing section (Phase A models+runtime → Phase B executor rollout → Phase C authoring surfaces → Phase D tests); added backward-compat note for existing while-loop compaction.
  - **14-3:** Added `estimate_tokens()` extraction as cross-cutting prerequisite in top-level plan notes.

## 2026-03-02
- [docs] **Phase 9A plans scaffolded and review-patched.** Added top-level plan `14-memory-cross-run-state` with three sub-plans (`14-1-session-conversation-memory`, `14-2-context-scoping-boundaries`, `14-3-long-chain-memory-system`); converted `docs/todo.md` Phase 9A bullets into linked checklist items. Post-creation review grounded all four plans against actual codebase state:
  - **Top-level plan:** Added Existing Infrastructure baseline table (12 components with gaps). Added Phase 8 (13-1) as explicit dependency. Added `ContextProjection`/`CompactionRule` dead-code activation to success criteria and decisions.
  - **14-1 (Session Memory):** Replaced thin baseline with 10-row table covering `SharedContextStore`, `Engine.run/resume`, `RunManager`, `ChatStore`, `EngineConfig`, and Phase 8 dependency. Tasks now reference specific code paths (`Engine.run()`, `_save_checkpoint`, `POST /api/runs`). Added REST endpoint specifications. Added Primary Files section. Added 13-1 coordination decision.
  - **14-2 (Context Scoping):** Replaced thin baseline with 12-row table exposing dead code (`ContextProjection` unused, `CompactionRule` unused, `_run_subgraph` shares parent `SharedContextStore`, `OrchestratorExecutor` ad-hoc keys). Reframed task 1-1 from "introduce" to "activate and extend existing models into unified `BoundaryContract`". Added critical task 2-1: `_run_subgraph` scoped view isolation. Added `boundary_enforcement` config flag. Added Primary Files section (12 files). Added note on `OrchestratorExecutor` migration to formal signals.
  - **14-3 (Long-Chain Memory):** Replaced thin baseline with 14-row table covering RAG classes, `compact_history()`, `estimate_tokens()`, `CompactionRule`, all vector store backends. Reframed task 1-2 as "activate `CompactionRule`". Added `estimate_tokens()` extraction task. Added cost-aware consolidation design (heuristic fallback vs LLM summarization). Added feature flags for staged rollout. Added Primary Files section (10 files).
- [fix] **Phase 4 follow-up patch (post-review):** Fixed a parallel-agent race where `examples/vibe_research_v2_md/plot_one.md` and `write_csv.md` were copied before 7-5's code-node migration landed, leaving stale `tool_id` references (`plot_backtest` / `save_grid_csv`). v2 now uses the same inline code-node pattern as v1 (with v2 output paths). Also corrected `docs/plans/7-6-node-state-simplification.md` task parent checkbox for docs (`9`) to match completed sub-tasks.
- [refactor] **Vibe v2 runner cleanup:** Removed unused legacy tool imports/registrations (`plot_backtest`, `save_grid_csv`) from `examples/vibe_research_v2_md/run_multi_dept.py` now that v2 `plot_one.md`/`write_csv.md` are code nodes. Tool registry now includes only required runtime tools (`run_backtest`, `run_strategy_script` + builtins).
- [fix] **CSV/path hardening for code-node post-processing:** Updated v1/v2 `write_csv.md` to use `csv.writer` (safe escaping for commas/quotes/newlines) and support optional `csv_path` override. Updated v1/v2 `plot_one.md` to support optional `out_dir` override while keeping stable default output paths.

## 2026-03-02
- [feat] **Phase 4 — Core Hardening complete.** All 9 sub-plans finished. Remaining work completed in parallel:
  - **Plan 7-5 (General Tool Design):** Converted `plot_one.md` and `write_csv.md` from domain-specific tool nodes (`plot_backtest`, `save_grid_csv`) to inline code nodes with matplotlib/CSV logic. Added `DAN_USE_LEGACY_PLOT_CSV` env var for backward-compat deprecation path. Plan status → completed.
  - **Plan 7-6 (Node State Simplification):** Created `examples/vibe_research_v2_md/` — removed 14 `try/except NameError` blocks (79 lines of boilerplate, −22% total code), eliminated 4 state-threading edges via scope injection. Governor reduced from 71→37 code lines (−48%). Plan status → completed.
  - **Plan 7-7 (Editor Navigation & Layout Hardening):** Set up vitest for editor unit tests. 32 tests across 3 files (`resolveGraphAtStack`, `deepSetSubGraph`, `orderPorts`, integration fixtures). Docs synced. Plan status → completed.
  - **Plan 7-8 (Workflow Node API Hardening):** All tasks already implemented; status updated to completed. Architecture docs updated with strict parse mode, mode-aware gate defaults, mutator diagnostics.

## 2026-03-02
- [docs] **Roadmap reorganization — Phase 9/10 swap + Deep Systems clustering:** Dissolved flat Phase 10 (Deep Systems, 11 items + 14 memory sub-bullets) into 3 thematic clusters: 9A Memory & Cross-Run State, 9B Behavior Modifiers, 9C Execution Primitives. Swapped with Author & Distribute (now Phase 10) since API-level changes should land before packaging. Collapsed memory policy defaults from 14 inline bullets to plan-file reference. Updated self-evolving orchestrator backlog refs. Earlier same session: dissolved Phase 9 (Application Layer) — agent teams→Deep Systems, messaging→Author & Distribute, user system→Backlog.

## 2026-03-02
- [feat] **Plan 7-7: Editor Navigation & Layout Hardening — tests & docs:** Set up vitest for editor unit tests. Added tests for `resolveGraphAtStack`, `deepSetSubGraph`, `orderPorts`. Integration test fixtures for nested drill-in at depth 2-3. 32 tests across 3 files, all passing.

## 2026-03-02
- [docs] Reordered roadmap phases in `docs/todo.md`: moved former "Phase 7.5 — Author & Distribute (Remaining)" to after Application Layer as "Phase 10 — Author & Distribute", renumbered downstream phases (`Deep Systems` is now Phase 11), and aligned related phase-number references.
- [docs] Planned Phase 8 with offset numbering: added top-level plan [13-observe-recover](plans/13-observe-recover.md) and sub-plans [13-1-run-observability-history](plans/13-1-run-observability-history.md), [13-2-recovery-debug-workbench](plans/13-2-recovery-debug-workbench.md); updated `docs/todo.md` to link Phase 8 to the new `13-*` plan structure.
- [docs] **Phase 8 plan review patch:** Audited existing infrastructure (RunManager, FileSystemCheckpointStore, event streaming, resume endpoint, OutputPreview, LogPanel, DanNode badges, providers/costs) and grounded all three `13-*` plans in actual codebase state. Top-level plan now has full baseline inventory table. 13-1 tasks reframed as "extend RunRecord/persist to RunStore/enrich GET /api/runs" instead of generic "define schemas". 13-2 tasks reference existing resume endpoint, checkpoint store, and OutputPreview; marked tasks 3–4 (variable inspector, node test cases) as parallelizable with 13-1. Added concrete file paths, line references, and existing-gap analysis throughout.

## 2026-03-02
- [fix] **Chat review follow-up hardening:** Fixed advisory stale-revision false positives by switching frontend `client_graph_revision` to backend-compatible canonical SHA-256 hashing; made Stop effective for tool-calling responses by racing `provider.complete()` with cancel events; scoped Stop endpoint to LLM chat streams only (`chat-*`); expanded mention chip rendering to include `@file/@code/@docs/@chat`; tightened workspace mention path safety checks for secret-like filenames.
- [docs] **Phase 7.2 — Cursor-Parity Chat Experience complete (core).** All 6 sub-plans implemented across 3 parallel waves. Deferred stretch items: @Web mentions, autocomplete UX polish, per-op approval gates, sandbox display, message queuing, thread branching, checkpoint restore, latency benchmarks, E2E browser tests.
- [test] **Plan 12-6: Chat Quality Harness — testing and evaluation framework (tasks 1-4):**
  - **Task 1 — Expanded chat integration tests:** Added 13 new tests to `test_chat_integration.py` (24 total). Mode tests (Ask text-only, Agent mutation, Plan no-tools, Debug context), streaming tests (chat_token ordering, accumulated growth, chat_complete termination), mention resolution, stop generation (slow mock + cancel endpoint → chat_interrupted), export (markdown + JSON format), and search (query + empty query). Three mock provider variants: default, tool-calling, slow-streaming.
  - **Task 2 — NL→mutation regression suite:** New `test_mutation_regression.py` with 37 tests. 10 golden prompts covering add_node, add_edge, remove, edit, expand_pattern, gate, multi-op builds. Parametrized tests for `plan_validity`, `operation_match`, `dry_run_success`. Error handling: invalid node type, missing fields, nonexistent node, stale revision. JSON fallback extraction: markdown blocks, raw JSON, non-plan text. Full pipeline integration test (mock LLM → app endpoint).
  - **Task 3 — Provider compatibility matrix:** New `test_provider_compat.py` with 19 tests. Provider interface validation (OpenAI, Anthropic, Google instantiation with mocks). Registry routing (model → provider). Tool-calling fallback extraction (structured tool_calls, text JSON, raw JSON, wrong function name, multiple blocks, nested JSON, malformed). Provider-specific output simulation documenting OpenAI (structured), Anthropic (text JSON fallback), Google (raw JSON fallback) known behaviours.
  - **Task 4 — Mutation metrics enhancement:** Enhanced `MutationMetrics` with `retry_count`, `stale_plan_count`, `failed_applies` counters plus `record_retry()` and `record_stale_plan()` methods. Integrated into `chat_manager.py` auto-retry and stale-plan re-planning paths. `summary()` dict expanded with new fields. New `test_mutation_metrics.py` with 13 tests (9 unit + 4 endpoint integration). Total: 92 new/expanded tests across 4 files, all passing in <10s.
  - Deferred: task 5 (latency benchmarks) and task 6 (E2E browser tests) — require Playwright + CI.

## 2026-03-02
- [feat] **Plan 12-4: Tool Display & Execution — tool calls as first-class chat events (tasks 1-4):**
  - **Task 1 — Tool call event protocol:** Added `ChatToolCallStartEvent` and `ChatToolCallResultEvent` Pydantic models in `chat_manager.py`. Events emitted in `send_message_with_tools` around mutation plan dry-run: `chat_tool_call_start` (tool_call_id, tool_name, args_preview) → dry-run processing → `chat_tool_call_result` (status, output_preview, duration_ms). Added `_build_args_preview()` and `_build_dry_run_preview()` helpers. Updated `ChatStreamEvent` union to include new types.
  - **Task 2 — Inline tool call rendering:** New `ToolCallCard.tsx` component: compact expandable card showing tool name, status icon (spinner/check/X), collapsible args section, collapsible output section, duration badge, error styling (red border/bg). Added `ToolCallInfo` interface and `toolCalls`/`runEvents` fields to `ChatMessage` in `chat.ts`. Updated `ChatStreamEvent` with new event type fields. `ChatPanel.tsx` handles `chat_tool_call_start`/`chat_tool_call_result` WebSocket events, tracking tool calls on assistant messages. Serialization added to `toBackendMessage`/`fromBackendMessage` for thread persistence.
  - **Task 3 — Mutation plan as tool trace:** `ToolCallCard` for `plan_graph_mutations` shows operation sub-items (op type, node name/id, node type) up to 8 with "+N more". "Preview Changes" button opens existing `GraphDiffPreview`. After apply/reject, card shows "Applied"/"Rejected" status badge. `MutationBadge` hidden when tool call card is present to avoid duplication.
  - **Task 4 — Run output streaming:** New `RunOutputBlock.tsx` component: derives per-node state (running/completed/failed) from structured `RunEventPayload` array. Shows collapsible node list with status icons, node IDs, elapsed time, expandable output/error sections. Overall run status header with "View full logs" link to LogPanel. `ChatPanel.tsx` now accumulates structured `runEvents` array on messages (in addition to text summaries). `RunOutputBlock` replaces simple `RunRefBlock` when structured events are available.
  - Deferred: task 5 (per-operation approval gates — requires bidirectional WS), task 6 (sandbox display — depends on Phase 6 sandbox runner).

## 2026-03-02
- [feat] **Plan 12-2: Multi-Mode Chat Architecture — Ask, Agent, Plan, Debug modes:**
  - **Backend mode routing:** Extended `ChatMessageRequest.mode` to accept `"ask" | "agent" | "plan" | "debug"` with backward compat (`"build"` / `"mutate"` normalized to `"agent"` via `normalize_chat_mode()`). Updated `_build_messages()` to select system prompt per mode.
  - **System prompts:** Added `ASK_PROMPT` (read-only graph explanation, no tools), `PLAN_PROMPT` (two-step: NL plan first, mutations after approval), `DEBUG_PROMPT` (error diagnosis with auto-injected run failure context). Agent mode reuses existing `SYSTEM_PROMPT_TEMPLATE` / `BUILD_FROM_INTENT_PROMPT` based on empty graph detection.
  - **Mode routing in app.py:** Ask and Plan modes route through `send_message` (text-only, no tool calling). Agent and Debug route through `send_message_with_tools`. Debug mode auto-builds context via `build_debug_context()` from `RunManager.list_runs()`.
  - **Frontend mode selector:** Replaced build/mutate toggle with 4-button segmented control (Agent | Ask | Plan | Debug) with mode-specific icons (Sparkles, HelpCircle, FileText, Bug). Mode selector appears in chat header below thread title.
  - **Plan mode two-step flow:** Plan-mode assistant responses show "Approve Plan" / "Revise" buttons (`PlanApprovalButtons` component). Approval sends follow-up with `mode: "agent"` for tool-enabled mutation generation.
  - **Ask mode:** Disabled diff preview rendering (onPreviewMutation=undefined). Text-only responses.
  - **Debug mode:** `build_debug_context(runs, workflow_id)` extracts last failed run's errors, events, outputs. Injected into DEBUG_PROMPT's `{debug_context}` placeholder.
  - **Backward compat:** `"build"` → `"agent"`, `"mutate"` → `"agent"` via `CHAT_MODE_ALIASES`. Build-from-intent detection changed from `mode == "build"` to `is_empty_graph` check. `openBuildWithAI` sets `chatMode: "agent"` + `chatFocusTrigger`.
  - **EmptyState update:** Mode-aware prompts and descriptions for all 4 modes. Agent mode shows build prompts for empty graphs, mutation prompts for non-empty.
  - Deferred: per-thread mode persistence (2-2), keyboard shortcut (2-4), debug diff tag (6-4), "Fix this" shortcut (6-5), auto-mode detection (task 7).
- [feat] **Plan 12-1 tasks 4 & 7 — Run events in chat timeline + integration tests:**
  - **Task 4 — Run events in chat:** Extended `map_run_event_to_chat_block()` in `scoped_run.py` to handle `node_started`, `node_output`, `tool_call_started`, `tool_call_result` (was only `run_started/completed/failed`, `node_completed/failed`). New return shape: `{type, event_type, node_id, summary, detail}`. Modified `_handle_run_command()` in `app.py` to create a chat stream channel, subscribe to `RunManager` events, and pipe them as `chat_run_event` WebSocket messages — catchup-aware (exits immediately if run already finished). Added `RunEventPayload` interface and `chat_run_event` to `ChatStreamEvent` union in `chat.ts`. `ChatPanel.tsx` now opens a WebSocket for run events when `/run` returns `stream_channel_id`, accumulates status lines in the assistant message, and updates `runRef.status` on completion/failure.
  - **Task 7 — Integration tests:** Created `tests/test_server/test_chat_integration.py` (11 tests): chat message round-trip (stream channel, event delivery), thread CRUD (create/list/get/update/delete), stale revision mismatch detection, `/run` command dispatch (full scope, node scope, nonexistent graph, stream channel). Uses mock LLM provider to avoid real API calls.

## 2026-03-02
- [feat] **Plan 12-3: Rich Context & Mentions — server-side resolution with context budget:**
  - **New module `mention_resolver.py`:** `MentionRef` / `ResolvedMention` Pydantic models, `MentionResolver` orchestrator dispatching to per-type resolvers (`FileResolver`, `CodeResolver`, `DocsResolver`, `ChatHistoryResolver`). `pack_context()` for token-budget-aware message list construction.
  - **Task 1 — Server-side resolution:** `ChatMessageRequest` now accepts `mentions: list[{type, identifier}]`. `ChatManager._build_messages()` resolves mentions via `MentionResolver` and injects them as a "Context from mentions" block in the system prompt. Backward compat: empty mentions list → existing `compact_history()` path.
  - **Task 2 — @File mentions:** `FileResolver` reads workspace files; security via path sandboxing + denied patterns (`.env`, `node_modules`, `.git`, `venv`, etc.); truncates large files to first 100 + last 50 lines. `GET /api/files/list` endpoint scans workspace for safe extensions (`.md`, `.py`, `.json`, `.yaml`, etc.). Frontend: `@file:` prefix triggers file autocomplete.
  - **Task 3 — @Code mentions:** `CodeResolver` extracts `NodeName.field` (prompt_template, system_prompt, tool_code, output_schema, config, condition) from active graph. `GET /api/code-refs/{workflow_id}` returns available refs. Frontend: `@code:` prefix triggers code ref autocomplete.
  - **Task 4 — @Docs mentions:** `DocsResolver` reads `docs/*.md` + `README.md`; truncates to 200 lines. `GET /api/docs/list` returns available doc names. Frontend: `@docs:` prefix triggers doc autocomplete.
  - **Task 5 — @Past Chat mentions:** `ChatHistoryResolver` loads thread from `ChatStore`, builds extractive summary (user messages + first sentence of assistant responses, capped to 2000 tokens). Frontend: `@chat:` prefix triggers thread title autocomplete.
  - **Task 7 — Context budget:** `pack_context()` allocates: system prompt always full → mentions get up to 35% of remaining budget (truncated longest-first) → history compacted with sliding window. Integrated into `_build_messages()` when mentions present.
  - **Frontend:** Extended `MentionRef` type and regex in `mentionParser.ts` with 4 new types. `MentionAutocomplete.tsx` rewritten with prefix-based category filtering, lazy data fetching per category, per-type icons (lucide-react). `ChatPanel.tsx` extracts structured mentions from message text via `parseMentions()` and sends in API request. `api.ts` gains `listWorkspaceFiles()`, `listDocs()`, `listCodeRefs()`.

## 2026-03-02
- [feat] **Plan 12-5: Conversation Lifecycle — stop, export, search, checkpoints, pin:**
  - **Stop generation (task 1):** Added `ChatInterruptedEvent` model and `ChatStreamEvent` union update in `chat_manager.py`. Added per-stream cancellation via `asyncio.Event` — `register_stream()`, `cancel_stream()`, `unregister_stream()` on `ChatManager`. All three streaming paths (`send_message`, `_stream_with_json_fallback`, `send_message_with_tools`) now accept and check `cancel_event`. Added `POST /api/chat/{channel_id}/stop` endpoint in `app.py`. Frontend: Stop button (red Square icon) replaces Send while streaming; handles `chat_interrupted` event to mark message with `*[generation stopped]*` suffix and save to thread.
  - **Auto-checkpoints (task 3, basic):** Added `save_checkpoint()` to `ChatStore` — writes `{thread_id}_{message_id}_{ts}.json` to `checkpoints/` subdirectory. Added `POST /api/chats/{wf}/{tid}/checkpoint` endpoint. Frontend calls it after successful mutation apply in `handleApplyMutation`.
  - **Export conversation (task 4):** Added `export_thread_markdown()` and `export_thread_json()` to `ChatStore`. Added `GET /api/chats/{wf}/{tid}/export?format=md|json` endpoint. Frontend: Download icon button in chat header triggers Markdown export as file download. Copy-as-Markdown button on each message bubble (`ChatMessage.tsx`).
  - **Cross-thread search (task 5):** Added `search_threads(query, workflow_id=None)` to `ChatStore` — case-insensitive substring match across all messages. Added `GET /api/chats/search?q=...&workflow_id=...` endpoint (placed before `{workflow_id}` route for correct matching). Frontend: search bar in thread list with results showing message preview; click to open thread.
  - **Thread pin (task 6-3):** Added `set_pinned()`, `get_thread_meta()`, `set_thread_meta()` to `ChatStore` — stores `pinned` flag in `.meta.json` sidecar. Updated `list_threads()` to include `pinned` field. Added `POST /api/chats/{wf}/{tid}/pin` endpoint. Frontend: Pin icon on thread rows; pinned threads sort to top. `ChatThreadSummary` type updated with optional `pinned` field.
  - **API additions (`api.ts`):** `stopChatStream`, `exportChatThread`, `searchChatThreads`, `pinChatThread`, `saveChatCheckpoint`.
  - **Type update (`chat.ts`):** Added `"chat_interrupted"` to `ChatStreamEvent.type` union.

## 2026-03-02
- [docs] **Plan 12-1 task 8 — Reconcile plan documentation with actual scope:**
  - Audited plans 10-5, 10-6, 10-2 for unchecked tasks vs. actual implementation.
  - **10-5** (Chat History & Session Integration): Checked off tasks 1 (data model) and 2 (persistence backend) — both fully implemented in `chat_store.py`, `app.py`, `chat.ts` but checkboxes were never ticked. Also checked 5-1 (mutation metadata in ChatMessage). Tagged 10 deferred items: cascade delete (→ 12-5), offline resilience (→ 12-1), delta tracking UI (→ 12-4), export thread (→ 12-5), execution block helpers (→ 12-4), tests (→ 12-6), docs. Thread timeline moved to Backlog.
  - **10-6** (Scoped Run from Chat): Checked off 18 sub-tasks across tasks 1–6 — `scoped_run.py` (build_scoped_graph, parse_run_command, map_run_event_to_chat_block), POST `/api/runs/scoped` endpoint, RunManager integration all implemented but checkboxes never ticked. Tagged 9 deferred items: catch-up payloads (→ 12-1), output mapping (→ 12-4), input-required dialog (→ 12-4), NL intent (→ 12-2), deep-links (→ 12-4), event fan-out safety (→ 12-1), rate guard (→ 12-1), tests (→ 12-6), docs.
  - **10-2** (Mention & Co-Navigation): Core UI was correctly checked. Tagged 5 individual deferred polish items (debounce, atomic backspace, deleted-ref rendering, auto-drill-in, workflow-mention navigation) → 12-3. Tagged full task blocks: canvas→chat suggestions (→ 12-3), backend mention resolution (→ 12-3), tests (→ 12-6), docs. Canvas drag-to-mention moved to Backlog.
  - Updated status lines in all three plan files with deferral summaries. Updated `todo.md` lines 89, 92, 93 with inline notes. Added deferral mapping to Notes sections. Added 2 stretch items to Backlog.
- [fix] **Plan 10-10 post-review patch — critical runtime bugs:**
  - `_rag_index_documents`: fixed indexer payload to pass `list[dict]` with `"text"` key (was `list[str]`); added workspace sandboxing (rejects absolute paths, verifies resolved path stays under `DAN_WORKSPACE_ROOT`).
  - `TOOL_PORT_MANIFESTS`: aligned `compile_latex`, `save_paper`, `package_submission` with real function signatures — added `title` input ports, structured output ports (`pdf_path`, `tex_path`, `bib_path`, `compile_success`).
  - `data_ingest` pattern: removed broken `list_directory` intermediate node (tool already discovers files internally); added `topic` variable to InputNode for RAG query; wired control edge from indexer to RAG operator for execution ordering.
  - `informs_paper_writing` template: added `Paper Config` InputNode with `title` variable; declared explicit `context` input port on Outline Planner (was auto-created); rewired LaTeX pipeline to fan out content + title to `compile-latex` and `save-paper` directly; wired structured ports (`tex_path`, `bib_path`, `title`) from `save-paper` to `package-submission`.
  - Tests: updated `data_ingest` assertions (3 nodes, 3 edges); added `test_compile_latex_has_title_port`, `test_save_paper_has_structured_ports`; all 56 intent-builder tests pass.
- [feat] **Plan 12-1 task 2 — History compaction / context window management:**
  - 2-1: Added `estimate_tokens()` utility using `tiktoken` (cl100k_base fallback for non-OpenAI models, `len//4` when tiktoken unavailable). Added `tiktoken>=0.7` to `pyproject.toml` dependencies.
  - 2-2: Added `MODEL_CONTEXT_WINDOWS` lookup table (20 models: OpenAI, Anthropic, Google, DeepSeek) with 128k default. Added `DAN_CHAT_MAX_CONTEXT_RATIO` env var (default 0.8) and `DAN_CHAT_RECENT_MESSAGES` (default 10). `_get_context_window()` resolves model name with prefix fallback.
  - 2-3: Added `compact_history()` with 4-phase sliding window: (1) system prompt always kept, (2) recent N messages kept in full, (3) older assistant messages truncated to first + last sentence via `_truncate_assistant_message()`, (4) oldest dropped if still over budget. Called automatically in `_build_messages()`. Transparent — user sees full history in UI, LLM receives compacted version.
  - 2-4: Added `context_window` field to `ChatCompleteEvent` and `ChatMutationEvent`. Frontend `ChatPanel.tsx` shows "~Xk / Yk" context usage in header (last response prompt tokens vs model context window). Added `formatTokenCount()` helper for human-friendly token display. All 233 server tests pass.
- [fix] **Plan 12-1 task 3 — Fix dead CTAs and UI gaps:**
  - 3-1: Wired "View logs" button in `RunRefBlock` — added `logFocusCounter` / `focusLogPanel()` to store; `App.tsx` watches the counter and switches bottom tab to Logs; button onClick calls the store action.
  - 3-2: Fixed `retryLast` stale closure — added `messagesRef` (always-current ref), `sendMessage` now accepts optional `historyOverride` and reads `messagesRef.current` instead of stale closure `messages`; `retryLast` computes trimmed history and passes it directly.
  - 3-3: Send button now shows spinning `Loader2` icon while streaming (was already disabled, now has visual feedback).
  - 3-4: Unexpected WebSocket disconnect during streaming now shows error toast + inline error with retry hint. Uses `wsClosedIntentionally` flag to distinguish intentional closes from network drops; `ws.onerror` sets the flag to avoid duplicate errors from `onclose`.
- [feat] **Plan 12-1 tasks 1, 5, 6 implemented — Chat Reliability & Polish (partial):**
  - Task 1 (client_graph_revision end-to-end): `ChatPanel.tsx` now computes a quick hash of `danGraph` and sends it as `client_graph_revision` in chat requests. Backend already compared revisions and set `revision_mismatch` on response events — frontend now reads it and shows an amber "Graph changed since your last message" banner. Stale `chat_error` events with `revision_mismatch` in the error string also trigger the banner.
  - Task 5 (strict edge safety in retry/replan): `_coerce_strict_edges` now runs on both the auto-retry and stale-plan replan paths in `send_message_with_tools`, not just the first attempt. Build-mode edge typos are now caught on every LLM attempt.
  - Task 6 (env var reconciliation): Standardized on `DAN_LLM_MODEL` (execution default) and `DAN_CHAT_MODEL` (chat override, falls back to `DAN_LLM_MODEL`). Replaced all `DAN_LLM_DEFAULT_MODEL` references in `examples/equity_research.py`, `examples/paper_writing.py`, `docs/llm-api-guide.md`, `README.md`. Updated `chat_manager.py` fallback chain. Added `DAN_CHAT_MODEL` to `.env.example`.
- [docs] **Phase 7.2 plans reviewed and tightened:** Patched `12-cursor-parity-chat` and sub-plans for implementation clarity and safety: normalized new stream event names to `chat_*`, clarified Plan-mode two-step flow, split mention models into client `MentionRef` vs server `ResolvedMention`, scoped stop-generation API to `stream_id`/`message_id`, and deduplicated NL mutation quality backlog items by consolidating them under `12-6-chat-quality-harness`.
- [feat] **Plan 10-10 implemented — Domain NL Authoring.** Added `data_ingest` and `data_analysis` patterns to `PATTERN_LIBRARY`; `ApplySkill` mutation op with `SKILL_LIBRARY` (management_science_writing, informs_latex_style); `TOOL_PORT_MANIFESTS` for 10 common tools; `rag_index_documents` server tool; InputNode variable-to-port generation. Upgraded `BUILD_FROM_INTENT_PROMPT` with dual-branch composition, intent→pattern mapping, path-policy guidance. Added `informs_paper_writing` (19-node fully-wired template) and `rag_research` workflow templates. Added `clarify_intent()` to `ChatManager`. New test coverage for all features.
- [docs] **Phase 7.2 — Cursor-Parity Chat Experience planned:** Created top-level plan `12-cursor-parity-chat.md` and 6 sub-plans: `12-1-chat-reliability-polish` (trust gaps, revision concurrency, history compaction, integration tests), `12-2-multi-mode-chat` (Ask/Agent/Plan/Debug modes), `12-3-rich-context-mentions` (@Files/@Code/@Docs/@Web/@Past Chats, server-side resolution, context budget), `12-4-tool-display-execution` (inline tool cards, approval gates, run output streaming), `12-5-conversation-lifecycle` (stop generation, message queue, checkpoints, export, search), `12-6-chat-quality-harness` (regression suite, provider matrix, mutation metrics, E2E smoke tests). Updated `todo.md` with new phase section.
- [docs] **Plan 10-10 patched toward ultimate goal:** Revised `docs/plans/10-10-domain-nl-authoring.md` to close critical execution gaps: explicit RAG indexing step in `data_ingest`, operational `data_path` branch (`data_analysis`), sub-graph-safe generation requirements for control-flow templates, strict-mode/tool-port compatibility tasks, workspace path policy handling, and runtime artifact-level acceptance gates (`.tex`/compile/package) instead of plan-shape-only checks. Updated `docs/todo.md` summary line for 10-10 accordingly.
- [feat] **paper_writing_md example aligned with core:** (1) Workflow: for_each output wired via `outline_planner_each_section_writer → assembler`; loop uses `state:` and `defaults:` in `loop(...)` for gate state_schema/state_defaults; `outline_planner.sections | each(...)` for explicit array port. (2) Loader: flow syntax `source.port | each(body, ...)` and `EachStatement.source_port`; compiler wires that port to for_each items; PIPE_RE allows dot in source segment. (3) Agents: assembler accepts `results` (array) and `text`, returns `result` (code sets `result`); outline_planner returns `sections` (array), `title` (string) for each() items. (4) Tests: `test_each_with_source_port` in test_flow_parser; paper_writing_parity 17 passed, 2 skipped. (5) Docs: llm-api-guide 12b ForEach guidance updated with source.port and each-node wiring.
- [fix] **Chat: "async_generator can't be used in 'await' expression":** `provider.stream()` is an async generator; it must not be awaited. Updated `ChatManager.send_message` and `_stream_with_json_fallback` to use `async for chunk in provider.stream(...)` instead of `stream = await provider.stream(...)` then `async for chunk in stream`.
- [fix] **Beamer presentation overflows (vibe_research_presentation.tex):** Eliminated all overfull hbox/vbox. Step 2 diagram scaled to 0.965; “From Manual to Autonomous” diagram scaled to 0.90; Autonomous Architecture diagram scaled to 0.93; Step 2 caption text shortened and set in \tiny; Dept pipeline caption abbreviated to “Dept: track→…→save”; Phase text and Rule line shortened; “Is Vibe Research Valid?” frame line spacing and itemsep reduced. Top 4 backtest subfigures (2×2) already present; no overlap/overflow from subfigures.

## 2026-03-01 (plan)
- [docs] **Plan 10-10 (Domain NL Authoring):** Created sub-plan `docs/plans/10-10-domain-nl-authoring.md` under `10-chatbox-nl-workflow`. Covers: `data_ingest` pattern for PDF/file ingestion into RAG, full INFORMS paper-writing domain template (~15 nodes vs current 6-node stub), lightweight skill injection via `apply_skill` graph op, `BUILD_FROM_INTENT_PROMPT` quality improvements (composition examples, journal→template+skill mapping, HumanInTheLoop guidance), and multi-turn clarification in build mode (deferred from 10-9). Wired into `todo.md` and parent plan table.

## 2026-03-01 (gap patches)
- [fix] **`sendChatMessage` in `api.ts` missing `mode` param:** Added `mode: "mutate" | "build" = "mutate"` parameter so the API helper is consistent with the raw fetch call in `ChatPanel` and callers can pass build mode without bypassing the helper.
- [fix] **Build-mode strict validation server-side:** Added `_coerce_strict_edges` helper; in `send_message_with_tools` when `mode="build"`, all `add_edge` ops in the extracted plan have `strict=True` coerced before dry_run. Fail-fast on port typos during intent-driven builds.
- [fix] **`_pattern_review_loop` back-edge now `edge_type: "control"`:** The Gate→Writer loop-back edge was typed as data (default), causing `_recompute_entry_exit_points` to exclude Writer from entry points (cycle: no node lacked incoming data edge). Changing it to `control` makes Writer correctly identified as entry, matching execution semantics (the gate routes control flow, not raw data, on the loop-back).
- [test] **End-to-end build-from-intent tests (mocked LLM):** Added 12 new tests in `test_build_from_intent.py`: `TestCoerceStrictEdges` (6 cases: strict coercion, no mutation of original list) and `TestBuildIntentEndToEnd` (6 cases: chain_3/rag_qa/paper_writing/review_loop/strict-coerce/revision-injection flows through mocked LLM → dry_run → ChatMutationEvent). Covers the full pipeline without requiring a live LLM.

## 2026-03-01
- [fix] **Code review patches (4 fixes):** (1) Flow parser: `each()`, `loop()`, `parallel()` now catch `ValueError` on non-integer `parallel`/`max` kwargs and raise `FlowParseError`. (2) ConfigPanel: `updateInputMapping` no longer deletes mapping row when inner port is cleared mid-edit. (3) Loader: unknown `merge:` strategy in `parallel()` now emits diagnostic error instead of silently defaulting to APPEND. (4) Decompiler: `_invert_until_condition` checks for trailing content after closing paren to avoid truncation.
- [feat] **Runtime async orchestrator (Plan 10-9 Task 7):** Added `OrchestratorNode` model and `OrchestratorExecutor` — async runtime orchestrator that runs concurrently with subgraph teams. Unlike `ParallelSubagentsNode` (fire-and-forget gather), the orchestrator spawns teams as `asyncio.create_task`, then runs its own event-processing loop concurrently. Features: (1) `teams` dict mapping team names to sub_graph keys, (2) routing callback wraps event callback to feed events to `asyncio.Queue`, (3) configurable `completion_condition` (all_done/any_done/orchestrator_halt), (4) `timeout_seconds` for overall timeout, (5) `input_mappings` and `team_inputs` for per-team input routing, (6) bidirectional communication via `SharedContextStore` keys. Registered across all layers: `Node` union, `NodeTypeRegistry` (16 types), `ExecutorRegistry`, `NODE_TYPES`, `_default_ports`, `_default_node_config`, builder DSL (`wf.orchestrator()` with `orch.team()`), builder compiler/decompiler, loader compiler/decompiler. 11 pytest tests covering team completion, failure, any_done, timeout, events, input mapping.
- [test] **Build-from-intent integration tests (Plan 10-9 Task 6-1):** Added 24 pytest tests (`tests/test_server/test_build_from_intent.py`): (1) build-mode prompt selection — `mode="build"` uses `BUILD_FROM_INTENT_PROMPT`, `mode="mutate"` uses `SYSTEM_PROMPT_TEMPLATE`, empty graph forces build prompt regardless of mode. (2) Empty-graph bootstrap — `build_graph_summary` on empty graph returns valid `GraphSummary` with `node_count=0`, deterministic non-empty 16-char hex revision. (3) Template registry — `WORKFLOW_TEMPLATES` has expected keys (paper_writing, rag_qa, chain_3), each is a non-empty list of dicts with valid `op` fields and correct pattern references. (4) Mode parameter in API — `ChatMessageRequest` accepts `mode="build"|"mutate"`, defaults to `"mutate"`. (5) Empty-graph revision injection — `compute_graph_revision` on empty dict returns stable 16-char hex hash, matches model-based revision.
- [docs] **Build-from-intent docs (Plan 10-9 Task 6-2):** Updated `llm-api-guide.md` section 12f with build-from-intent API usage (`mode="build"` in chat endpoint), available templates, and the full workflow. Updated `architecture.md` chat/server section with build-from-intent mode and template registry.
- [feat] **Editor UX for build-from-intent (Plan 10-9 Task 5):** (1) Added `chatMode: "build" | "mutate"` to Zustand store with `setChatMode` and `openBuildWithAI` (auto-creates empty graph, sets build mode). (2) TabBar "+ New" picker: added "Build with AI" option with Sparkles icon — creates workflow, opens chat in build mode. (3) ChatPanel: build-mode banner (gradient indigo/purple), build-specific empty state with intent-focused example prompts ("Create a research pipeline…"), `mode` field sent in chat API request, build-mode placeholder text. (4) After mutation applied in build mode: auto-switches to mutate mode, shows "Workflow built successfully" CTA with "Run this workflow" button (triggers `startRun`). (5) `openTab` resets `chatMode` to "mutate" for tab isolation.
- [test] **Plan 7-9 Task 6 — ParallelSubagentsExecutor unit tests + docs:** Added 10 pytest-asyncio tests (`tests/test_engine/test_parallel_subagents.py`): APPEND/LAST_WRITE_WINS/REDUCER merge, all-fail, partial-fail, timeout, event emission (PARALLEL_BRANCH_STARTED/COMPLETED, PARALLEL_FAN_IN_COMPLETED with branch_key), empty branch_graphs error, input_mappings, branch_inputs. Updated `docs/architecture.md` (ParallelSubagentsNode in directory listing, control-flow primitives table, 15 node types). Updated `docs/llm-api-guide.md` (section 3k Parallel Subagents config table, section 5d builder example). Plan 7-9 marked completed (Task 3 checkpoint/resume and 5-3 visualization deferred).
- [feat] **Template registry + API mode flag (Plan 10-9 Tasks 3-4):** (1) Added `WORKFLOW_TEMPLATES` dict (paper_writing, rag_qa, chain_3) — pre-built mutation operation sequences using `expand_pattern` ops. (2) Updated `BUILD_FROM_INTENT_PROMPT` with "Available templates" section listing template names and descriptions. (3) Added `mode: Literal["mutate", "build"]` parameter to `ChatManager.send_message` and `send_message_with_tools`; mode="build" forces build prompt even on non-empty graphs. (4) Added `mode` field to `ChatMessageRequest` in `app.py`, passed through to chat manager. Post-build mode switch is client-side.
- [feat] **Phase 7.1 structure review implemented via parallel subagents:** Ran 4 parallel agents for 11-1 (object), 11-2 (file), 11-3 (cross-cutting), 11-4 Task 4 (docs drift); then Phase 2 agent for 11-4 Tasks 1–3 (doc updates). Outputs: docs/plans/11-1-findings.md, 11-2-findings.md, 11-3-findings.md, 11-4-drift-findings.md. Plans 11-1 through 11-4 marked complete; 11-structure-review completed.
- [docs] **Phase 2 documentation updates (11-4 Tasks 1–3):** (1) architecture.md: added lib/portOrdering.ts, TabBar.tsx, CommandPalette.tsx, LoopGroupNode.tsx to directory structure; Import and API Conventions section; Object Design Principles (NodeBase); fixed "10 node types" → "14 node types" for icons; added API endpoints (validate, export/markdown, export/python, metrics/mutations). (2) llm-api-guide.md: clarified InputNode (wf.input is property, not node creator; loader/scoped_run only); fixed Type Reference input row; added REST endpoints; Import Map now includes GateNode, InputNode, ParallelSubagentsNode, ValidatorNode. (3) bugs.md: added Known Limitations from audits — reactFlowEdgeToDan edge metadata loss, DEFAULT_OUTPUT_PORTS inconsistency, TS NodeBase read_set/write_set gap, loader feature gaps. Marked 11-4 Tasks 1–3 complete; 11-structure-review in-progress.
- [docs] **Plan 11-3 Cross-Cutting Audit completed:** Audited data flow, naming, validation, schema evolution, legacy code, feature parity, config, trust boundaries. Findings in `docs/plans/11-3-findings.md`. Key findings: snake_case everywhere; validation warning patterns duplicated; graphAdapter edge fallback can lose edge_type/context; Loader gaps (ControlEdge, ContextEdge, RAG, Validator, Reduce); sandbox config in graph, trust policy in env.
- [docs] **Plan 11-2 File Audit completed:** Audited module boundaries, directory layout, import structure, separation of concerns, editor structure, test layout, examples. No circular imports. Findings in `docs/plans/11-2-findings.md`. Refactor candidates: move graph_mutator to dan.mutator, exec.py to shared layer. Updated architecture.md with server/layout.py and server/mutation_metrics.py.
- [docs] **Plan 11-4 Task 4 — Docs vs code drift check:** Compared `llm-api-guide`, `architecture`, and plan files to current code. Wrote findings to `docs/plans/11-4-drift-findings.md`: llm-api-guide (InputNode/wf.input mismatch, Import Map gaps, missing REST endpoints), architecture (missing editor files, node icon count, API table gaps), plan drift (none), aspirational vs implemented (Hyperedges, Four Top-Level Agents, Agent Boundary Contract, HumanNode, context scoping). Checked off 4-1, 4-2 in 11-4-documentation.md.
- [feat] **Plan 7-9 Tasks 1-2 — ParallelSubagentsNode model + executor:** (1) Added `ParallelSubagentsNode` with `branch_graphs`, `parallelism`, `merge_strategy`, `reducer`, `input_mappings`, `branch_inputs`, `failure_policy`. Registered in Node union, NodeTypeRegistry, NODE_TYPES, graph_mutator. (2) Added `ParallelSubagentsExecutor` — async `asyncio.gather` for concurrent branches, emit events with `branch_key`, merge with APPEND/LAST_WRITE_WINS/REDUCER. Added `EventType` PARALLEL_BRANCH_STARTED/COMPLETED, PARALLEL_FAN_IN_COMPLETED. Added `evaluate_expression` to conditions for REDUCER. Fixed test_builtins_registered (15 types).
- [feat] **Plan 7-9 Task 5 — Editor support for Parallel Subagents:** (1) Palette: added `parallel_subagents` to NODE_TYPE_CATALOG, createDefaultNode, nodeIcons, NODE_DESCRIPTIONS. (2) Config panel: dedicated ParallelSubagentsConfigSection for branch_graphs (sub_graph keys with add/remove, createEmptySubGraph on add), input_mappings, merge_strategy (append/last_write_wins/reducer), reducer (when merge_strategy=reducer), parallelism, branch_inputs (collapsible JSON), failure_policy (max_iterations, timeout_seconds, stagnation_threshold). (3) DanNode: TYPE_COLORS, hasBranchGraphs, parallel badge with branch count. (4) Drill-in: GraphCanvas and useGraphStore support double-click drill into first branch_graph. (5) PortMappingOverlay, ContextMenu, MentionAutocomplete, ExecutionTimeline updated for parallel_subagents.
- [feat] **Plan 7-9 Task 4 — Builder/loader for parallel subagents:** (1) Builder: `wf.parallel_subagents(node_id, ...)` context manager with `parallel.branch(key)` for each branch; `parallel.define_branch(key, graph)` for pre-built graphs. Compiler maps to `ParallelSubagentsNode`. (2) Loader: flow syntax `source | parallel(team_a, team_b, merge: append, parallel: 2)` in `flow_parser.py`; `ParallelStatement` model; loader compiler creates `ParallelSubagentsNode` with branch sub_graphs. (3) Decompiler: builder decompiler emits `wf.parallel_subagents(...)` with nested `parallel.branch()` blocks; loader decompiler emits `source | parallel(...)` flow lines. Added `TestParallelParsing` tests.
- [feat] **Build-from-intent mode (Plan 10-9 Tasks 1-2):** Added `BUILD_FROM_INTENT_PROMPT` for intent-first workflow creation. When graph is empty (0 nodes, 0 edges), chat uses build mode: task decomposition, pattern library reference (chain, review_loop, fan_out, rag_qa), intent→pattern mapping (paper writing, RAG QA), ambiguity defaults. `build_graph_summary` explicitly handles empty graph; `base_graph_revision` injected from empty graph before apply. Recommend `strict=True` for add_edge in build-from-intent. New test `test_empty_graph_uses_build_from_intent_prompt`.
- [docs] **Phase 7.1 structure review plans added:** Created [11-structure-review](plans/11-structure-review.md) and sub-plans 11-1 (object audit), 11-2 (file audit), 11-3 (cross-cutting audit), 11-4 (documentation). Intermediate review-and-patch phase for files + objects. In todo.md as Phase 7.1; backlog items for full structure review removed. Phase 8 Observe & Recover renumbered to 14.
- [docs] **Final review patches for 7-9 and 10-9:** (1) 7-9: Task 4-1 — builder must define sub_graphs per branch (e.g. `define_branch` or nested context); 4-2 — loader syntax for branch refs. (2) 10-9: Task 7-8 — completion condition examples (all done, max_iterations, halt, timeout); Primary Files split (build-time vs Task 7 runtime: engine, executors); Decision on orchestrator completion.
- [docs] **Plans 7-9 and 10-9: async as unifying primitive, work together:** Async includes concurrency; more flexible than ForEach (list-based, block-until-all). 7-9: "Async as the Primitive" section — heterogeneous branches, event streaming, foundation for orchestrator. 10-9: runtime mode table + Dependencies — 7-9 event stream + branch_key enables "receive at any time"; 10-9 extends with bidirectional orchestration. ForEach remains for list iteration; 7-9/10-9 replace rigid patterns with flexible async orchestration.
- [docs] **Plans 7-9 and 10-9: goal-alignment patches:** (1) 7-9: Event metadata — include `branch_key` in PARALLEL_BRANCH_* event data for orchestrator routing. Note: engine events + layer_path enable "receive at any time" per team. (2) 10-9 Task 7: Split implementation into 7-6 (event routing), 7-7 (send mechanism — queue/context/inject API), 7-8 (concurrency model — orchestrator runs alongside, not blocking on children). Makes runtime async orchestrator buildable.
- [docs] **Plans 7-9 and 10-9: async guarantees + runtime orchestrator:** (1) 7-9: Added "Async Guarantee" section — all subagent execution async, non-blocking fan-out via asyncio.gather, no await before gather. Task 2-2: all coroutines start immediately. (2) 10-9: Added "Runtime async orchestrator" (Task 7) — orchestrator runs concurrently with subgraphs; receives from any subgraph at any time; sends to any subgraph at any time; all teams work simultaneously. Event-driven design. Depends on 7-9. Two-mode table (build-time vs runtime).
- [docs] **Plans 7-9 and 10-9 comprehensively reviewed and patched:** (1) 7-9: Concrete node model (`ParallelSubagentsNode` with `branch_graphs`, `merge_strategy`, `failure_policy`), executor-first design, Sequencing table, MergeStrategy alignment, EventType additions, checkpoint/resume notes, Primary Files, ReduceNode distinction. (2) 10-9: Context updated (empty-graph already supported), build-mode prompt, empty-graph bootstrap with revision injection, template registry, Sequencing, Dependencies (10-8, 7-8), Out of Scope, Primary Files.
- [docs] **Subplans 7-9 and 10-9 added:** Created `7-9-async-parallel-subagents.md` (parallel fan-out/fan-in for heterogeneous sub-workflows) and `10-9-meta-orchestrator.md` (zero-to-workflow from natural language intent). Moved corresponding backlog items into Phase 4 and Phase 7. Manager vs worker remains in backlog.
- [feat] **Mutator port auto-create — warn, optional strict (Plan 7-8 Task 5):** When `add_edge` targets a non-existent port, mutator now (1) auto-creates the port (permissive default) and adds a diagnostic: "Auto-created input port 'X' on node 'Y' (port not declared). Verify spelling." (2) Returns `diagnostics` in `MutationResult` and apply-mutation API response so chat/UI can surface them. (3) Added optional `strict: bool = False` to `AddEdge` — when `strict=True`, fails with clear error listing available ports instead of auto-creating. ChatPanel logs diagnostics to console on apply. Updated `chat_manager.py` tool schema with `strict` field. Added 3 unit tests and 1 API test.
- [docs] **Decompiler control/context semantics documented (Plan 7-8 Task 7):** In `docs/llm-api-guide.md` section 12b (Markdown Authoring Guidance), strengthened the context-heavy workflows bullet: control and context edges are emitted as `<!-- SKIPPED -->` comments; round-trip is not lossless for those workflows. LLM callers should keep a Python/JSON canonical source for full round-trip of context-heavy or control-heavy workflows.
- [feat] **Flow parse strict mode + ambiguous bare-edge fail (Plan 7-8 Tasks 4 & 6):** Added `strict: bool = False` to `compile_workflow()` and `load()`. When `strict=True`: (1) parse_warnings become fatal errors and compilation stops with `graph=None`; (2) ambiguous bare-edge auto-wire (multiple matching ports) emits error instead of warning. Non-strict keeps backward compatibility. Improved hint: "Use explicit 'A.port_x → B.port_y' to wire all intended ports." Updated `docs/llm-api-guide.md` 12a: recommend `strict=True` for LLM-generated and new workflows. Added `TestStrictMode` tests.
- [fix] **Gate while-mode default output safe (Plan 7-8 Task 3):** `default_output_port(node_type, gate_mode)` now returns `"continue"` when `node_type=="gate"` and `gate_mode=="while"`, enabling `gate >> body` for loop body chains. NodeRef stores `gate_mode`; builder `wf.gate()` passes it. Decompiler `_is_default_data_edge` passes gate_mode for gate nodes. Updated `docs/llm-api-guide.md` 12a rule 3: `gate >> body` works; explicit `gate["continue"]` still recommended for clarity.
- [fix] **ContextEdge read validation aligned with runtime (Plan 7-8 Task 2):** `_check_context_edge_permissions` now validates READ mode against the **target** node's `read_set` (consumer), not the source's. WRITE/APPEND still validate the source's `write_set`. Added `test_read_validates_target_not_source` to lock in correct behavior. Updated `docs/llm-api-guide.md` with context-edge semantics: read = target consumes, write = source produces.

## 2026-02-28
- [fix] **Code review patches for Plan 7-8:** (1) `_invert_until_condition` docstring documents unclosed-parens behavior; added test. (2) Extracted `_escape_json_for_flow_string` helper for state/defaults JSON embedding. (3) `default_output_port` gate logic made explicit. (4) control_flow `read_set`/`write_set` override comments. (5) Mutator `_op_add_edge` docstring. (6) ChatPanel shows mutation diagnostics via toast instead of console.
- [feat] **Plan 7-8 workflow node API hardening implemented:** (1) Markdown loop round-trip: decompiler inverts `gate.condition` → `until`, emits `state:`/`defaults:`; round-trip tests pass. (2) ContextEdge read validation: now validates `target_node_id`'s `read_set` for READ mode. (3) Gate while-mode default: builder `default_output_port` mode-aware (`while` → `continue`). (4) Flow parse strict mode: `compile_workflow(path, strict=True)` treats parse_warnings and ambiguous bare-edge as errors. (5) Mutator: diagnostic on port auto-create; optional `strict=True` on add_edge. (6) NodeBase: added `read_set`/`write_set` so atomic operators can declare context; builder `code()` accepts read_set; decompiler emits read_set/write_set. (7) Docs: llm-api-guide context semantics, playbook relaxations, control/context limitation. Resolved 5 bugs in docs/bugs.md.

## 2026-02-27
- [docs] **LLM generation playbook added to API guide:** Expanded `docs/llm-api-guide.md` with a dedicated workflow/script generation section (explicit typing/wiring rules, gate-loop authoring guidance, markdown vs Python generation caveats, and a recommended generate→compile→validate→smoke-test loop) to reduce silent miswiring and improve reproducibility for LLM-authored workflows.
- [feat] **Custom strategy script artifacts are now persisted before execution:** `_run_strategy_script` now saves incoming code to `examples/vibe_research_md/output/scripts/{strategy}.py` before running it, and returns `script_saved_path`/`script_save_error`. Workflow nodes were updated so these fields survive error normalization (`bug_fixer`) and are passed through persistence/output (`save_tracking`), making custom-factor runs directly reproducible.
- [fix] **CRSP formation-universe price filter enforced (`|price| >= 1`)**: Added canonical CRSP price aliases (`close_price_month`/`prc`), threaded `min_price` through builtin backtest CLI/tool path, and applied the same threshold in custom-factor backtests by auto-attaching CRSP formation prices by `(date, permno)` when missing. This removes sub-$1 names at portfolio formation while keeping runs reproducible via saved factor/result artifacts.
- [fix] **Vibe-research plotting restored on normal department runs:** `department.md` no longer routes the `skip` gate directly into `bug_fixer` (which could mark `bug_fixer` inactive on `skip=false`). Added dedicated halt sink `dept_halt.md` and hardened `bug_fixer.md` to return `{}` when no backtest payload is present. This preserves halt short-circuiting without suppressing per-factor plot generation on active branches.
- [fix] **Custom FUND merge tolerance crash hardened:** `_run_strategy_script` safe `merge_asof` wrapper now normalizes unsupported `tolerance` inputs (notably `pd.DateOffset(months=...)`) to timedelta-compatible values, and retries without tolerance when pandas still reports incompatible tolerance. This prevents failures like `incompatible tolerance <DateOffset: months=12>` in generated factor scripts.
- [fix] **Custom strategy runtime now captures build logs and rejects empty factor frames:** `_run_strategy_script` now captures stdout/stderr during `build_factor` execution (so generated-script debug prints are attached to result payload instead of leaking to terminal), and treats empty `factor_df` as an explicit failure before persistence/backtest. This prevents misleading “factor file exists but no backtest series/plots” outcomes from silently-empty generated factors.
- [fix] **Department output contract aligned for merge/results/plots visibility:** `department` composites exit via `save_tracking`, but downstream `merge.md` expects backtest-shaped payloads (`strategy_name`, `quintiles`, `spread_q5_q1_bps`, series fields). Updated `save_tracking.md` to pass through backtest fields (including `plot_saved_path`/`plot_error`) in its `result` object while still persisting tracking and result snapshots. This restores downstream result accumulation and immediate artifact observability from the composite output path.
- [docs] **Backtest tool return schema corrected in markdown specs:** `backtest_runner.md` and `run_strategy_or_backtest.md` now correctly declare `quintiles` as an `array` (runtime returns list of quintile rows), matching `quant_lib/backtest.py`.
- [fix] **`max_factors` now caps attempts and results are persisted immediately:** `governor.md` now also stops when `len(strategies_tried) >= max_factors` (not only when successful `results` hit the cap). `merge.md` now keeps per-strategy outcomes (including failures) when `strategy_name` is present so users can see what happened each try. `save_tracking.md` now writes per-strategy result snapshots to `examples/vibe_research_md/output/results/{strategy}.json` plus rolling `latest_result_{dept}.json`.
- [fix] **Builtin strategy naming aligned with department IDs:** `_run_backtest` now passes item `name` into `quant_lib/run_backtest.py` (`--name`) and the CLI returns/uses that as `strategy_name` (and factor filename label). This prevents mismatches like `momentum_*` vs `MOM_*` in tracking, and improves immediate status stamping in `tracking_{dept}.json`.
- [feat] **One-by-one department backtesting + immediate result visibility:** Updated `iteration.md` to serialize departments as `MOM → REV → FUND` using `pass_assignment_after_prev.md`, instead of parallel fan-out. Updated `save_tracking.md` to consume `backtest_result` and immediately stamp strategy status (`success/failed`) and spread into `tracking_{dept}.json` right after each backtest. `bug_fixer` plotting remains immediate per-factor, so plots and tracking updates are available as each factor finishes.
- [fix] **FUND custom strategy path hardened (factors now persist):** `_run_strategy_script` now (1) monkey-patches `pd.merge_asof` to a safe sorting wrapper during strategy execution to prevent `left keys must be sorted`, (2) normalizes common Compustat field aliases in params (`ceq→ceqq`, `at→atq`, etc.), (3) saves custom factor DataFrames to `examples/vibe_research_md/output/factors/<strategy_name>.parquet` (matching builtin path), and (4) returns `factor_saved_path` (plus non-fatal `factor_save_error`).
- [fix] **Code sandbox exception coverage expanded:** added common exception classes (`ValueError`, `TypeError`, `KeyError`, `IndexError`, `AttributeError`) to `_ALLOWED_BUILTINS` so generated strategy scripts with standard `try/except` blocks no longer fail with `name 'ValueError' is not defined`.
- [fix] **Compustat loader backward-compat aliases:** `load_compustat()` now exposes legacy alias columns (`ceq`, `at`, `sale`, etc.) mapped from canonical quarterly names (`ceqq`, `atq`, `saleq`, ...), improving robustness for stale tracking params and older generated code.
- [feat] **Department-level plotting inside dept pipeline:** `bug_fixer.md` now generates plots immediately after each department backtest result (when `dates`, `cumulative_quintiles`, and `ls_cumulative` are present). This makes plotting happen within each department manager flow, not only in the final top-level plotting pass. Added non-fatal plot metadata (`plot_saved_path`, optional `plot_error`) to the department result payload for observability.
- [fix] **Plan 7-7 review patch — 5 findings fixed:** (1) `computePortReorder` crossing detection rewritten: reads actual port indices from node `data.output_ports`/`input_ports` arrays instead of assigning dummy sequential indices. Now correctly detects crossings and reorders target ports to match source-side order. (2) Wired `computePortReorder` into `DanNode.tsx` via `useMemo` — results passed as `portReorder` hint to `orderPorts` for input ports. (3) `orderPorts` scoring refactored to clamped bands: P0 gate-pins (0–9), P1 connected (1000–1999 with `clampPeerY`), P2 unconnected (10000+). Bands guaranteed non-overlapping regardless of peer Y values. Accepts optional `portReorder` hint that takes precedence within P1 band. (4) `saveGraph` error path now resets `nodes`/`edges` to root graph (via `danGraphToReactFlow`) in addition to clearing `layerStack` — prevents stale UI on invalid layer path. (5) Data-edge label deduplication now only tracks edges that have a label (`rfEdge.label` truthy); label-less edges no longer block subsequent labeled edges from showing their label. Files: `layout.ts`, `portOrdering.ts`, `DanNode.tsx`, `useGraphStore.ts`, `graphAdapter.ts`.
- [feat] **Plan 7-7 implemented — editor navigation & layout hardening (tasks 1-9):** (1) `resolveGraphAtStack` + `deepSetSubGraph` helpers in `graphAdapter.ts` — single source of truth for nested sub-graph traversal (fail-closed, returns `null` on invalid path, callers auto-reset to root). (2) Fixed `drillIn`/`drillOut`/`jumpToLayer` to traverse full layer stack instead of root-only lookup — enables drilling into `dept_REV` from iteration body. (3) Fixed `saveGraph` to use `deepSetSubGraph` for immutable nested updates at arbitrary depth. (4) Fixed `PortMappingOverlay` to resolve parent node from correct intermediate graph. (5) Depth-3 cap enforced in `drillIn` (toast) and `BreadcrumbBar` (visual indicator). (6) Deterministic port ordering via `orderPorts()` in new `portOrdering.ts` — scoring pipeline: P0 gate-pins > P1 connected-by-peer-Y > P2 unconnected, alphabetical tie-breaker. Display-only in `DanNode.tsx`; `ConfigPanel` keeps raw order. (7) Port-aware dagre layout weights (same-name ports get weight 3). (8) Per-port smoothstep offset in `AnimatedEdge.tsx` to separate multi-edge bundles. (9) Data-edge label deduplication in `danGraphToReactFlow`. (10) `computePortReorder` crossing minimization (post-layout, cached). Files: `graphAdapter.ts`, `portOrdering.ts` (new), `useGraphStore.ts`, `DanNode.tsx`, `PortMappingOverlay.tsx`, `BreadcrumbBar.tsx`, `layout.ts`, `AnimatedEdge.tsx`.
- [refactor] **Factor sign convention enforced at construction time:** Moved reversal negation from `run_backtest()` (backtest time) into `build_momentum(negate=True)` (factor construction time). Output column standardized from `mom` to `factor`. All factors now monotonic at birth: higher value = stronger signal = Q5 = long side. Updated `strategy_coder.md` prompt with explicit sign convention rule for LLM-generated custom factors.
- [fix] **I/O alignment audit — 3 wiring bugs:** (1) `merge → governor` used bare edge which only wired `new_results` (alphabetically first match) and dropped `new_strategies` — `strategies_tried` never accumulated across iterations. Fixed with explicit two-port edges. (2) `dept_gate.theme` never wired to `strategy_manager` — orchestrator's per-iteration theme was silently dropped. Fixed by adding edge and prompt reference. (3) `dept_gate.skip` never consumed — halted departments still ran full LLM+backtest pipeline. Fixed by adding `if("skip")` gate that short-circuits to `bug_fixer` with empty result.
- [fix] **Builtin backtest always ran momentum, ignoring reversal:** `run_backtest()` hardcoded `build_momentum()` and `strategy_name="momentum_..."` regardless of `factor_name`. REV department computed 1-month momentum and labeled it momentum. Fixed: (1) negate signal for reversal so Q5 = past losers; (2) label output as `reversal_Nm_skipK`; (3) `strategy_to_item.md` now passes `factor_type` in item dict; (4) `_run_backtest` reads `factor_type` from item.
- [fix] **Merge contract mismatch — REV/FUND results silently dropped:** `merge.md` expected department outputs to have nested `backtest_result` and `strategy_id` keys, but department composites output the backtest dict directly (with `strategy_name`). So `c.get("backtest_result")` was always `None` and `c.get("strategy_id")` was always `""` — zero results from any department made it into the loop state. Fixed merge to treat department output as the backtest result itself and use `strategy_name` as the identifier. Regenerated compiled JSON and Python twin.
- [docs] **Plan 7-7 editor navigation & layout hardening:** Created `docs/plans/7-7-editor-navigation-layout-hardening.md` — 11 task groups covering: (1) `resolveGraphAtStack` helper for nested sub-graph traversal, (2) fix `drillIn`/`drillOut`/`jumpToLayer` to traverse full layer stack instead of root-only lookup, (3) fix `saveGraph` with `deepSetSubGraph` for nested immutable updates, (4) fix `PortMappingOverlay` nested parent resolution, (5) deterministic port ordering via logic+rules (connection-aware Y-sort, name-match alignment, gate pinning, unconnected-last), (6) edge routing polish (port-aware dagre weights, per-port smoothstep offset, label dedup, crossing minimization), (7) depth-3 cap enforcement. Motivated by vibe-research multi-dept workflow where dept_REV drill-in fails because body key exists only in iteration sub-graph.
- [refactor] **Vibe research: 3 departments for testing** — Reduced from 6 to 3 departments (MOM, REV, FUND) for easier testing. CASH, OPS, ML removed from iteration, orchestrator, merge, strategy_manager, strategy_coder.
- [fix] **Compustat data dictionary + strategy coder hardening:** LLM-generated code used academic-style column names (`at`, `ceq`, `sale`) but actual Compustat quarterly data uses `q`-suffix (`atq`, `ceqq`, `saleq`). (1) Expanded `load_compustat.py` to load 45 key quarterly columns. (2) Rewrote `strategy_coder.md` with full Compustat column reference from user-provided DATA-DICTIONARY.md. (3) Injected `load_compustat()` and `safe_merge_asof()` into code sandbox namespace — LLM code can now call these helpers directly. (4) Updated `strategy_manager.md` with concrete column examples per department.
- [docs] **Async loop design added to backlog** — Orchestrator-on-its-own + departments-in-tandem pattern; target: streaming/event-driven model vs current sequential-iteration design.
- [refactor] **Vibe research v2 — fixed 6 departments, state_schema loop:** Complete rewrite of `examples/vibe_research_md/`. (1) 6 fixed departments (MOM, REV, FUND, CASH, OPS, ML) as instances of one generic `department.md` composite. (2) Orchestrator dynamically assigns themes and halt/continue per department. (3) While-gate `state_schema` manages loop state (results, strategies_tried, iteration, start/end years, max_factors) — eliminated all unpack/repack/persist boilerplate nodes. (4) Deleted 12 legacy files, created 6 new ones. Iteration composite: 9 nodes, ~20 edges (was ~15 nodes, ~50 edges). Compiles cleanly to 76+ nodes across all sub-graphs.
- [fix] **`state_schema` key convention normalization:** `GateNode` and `WhileLoopNode` now auto-normalize full JSON Schema objects (`{"type":"object","properties":{...}}`) to flat key→schema maps (`{field: schema}`) in `model_post_init`. Runtime iterates top-level keys, so both input formats now work correctly. Updated 6 builder tests to expect normalized format.
- [fix] **Plan 7-6 post-review fixes (4 items):** (1) `active_loop_scope_id` leak on early-halt — wrapped `_iterate_cycle` loop body in `try/finally` to guarantee cleanup. (2) Spread validation enforces `object`-type on both ports when schemas present, instead of bypassing all checks. (3) Markdown flow `loop()` now supports `state: '{...}'` and `defaults: '{...}'` kwargs — `LoopStatement` model, `_parse_loop`, and compiler updated to pass `state_schema`/`state_defaults` to generated `GateNode`. (4) Spread tool schema description corrected from "list" to "dict". Files: `scheduler.py`, `graph.py`, `flow_parser.py`, `models.py`, `compiler.py`, `chat_manager.py`, `llm-api-guide.md`.
- [feat] **Plan 7-6 implemented — node state simplification (3 features):** (1) Loop-scoped state: `GateNode.state_schema` + `state_defaults` with scope injection in `_execute_node` for all cycle nodes. (2) Code node port defaults: auto-fill missing optional ports from `json_schema` type. (3) Spread edges: `DataEdge.spread=True` destructures dicts into individual inputs. 69 new tests across 3 files, 1023 total passing. Files changed: `models/control_flow.py`, `models/edges.py`, `engine/executor.py`, `engine/scheduler.py`, `engine/state.py`, `executors/code.py`, `executors/control_flow.py`, `validation/graph.py`, `builder/builder.py`, `builder/compiler.py`, `builder/decompiler.py`, `server/graph_mutator.py`, `server/chat_manager.py`, `editor/src/types/graph.ts`.
- [test] **Plan 7-6 loop-scoped state tests (7-1, 7-5, 7-9, 7-10, 7-14, 7-15, 7-16, 7-17):** Rewrote `tests/test_engine/test_loop_scoped_state.py` — 14 tests, all passing. Fixed 6 failures in the original draft: (1) shared executor call counting inflated by src/sink — introduced `_DispatchExecutor` to route by `node.id`; (2) no-schema gate fails because body is skipped on first pass and gate gets no counter — added direct src→gate edge; (3) unreachable-node validation error — removed gate from graph when testing scope injection isolation; (4) sequential body test had `counter < 1` condition that exited immediately — changed to `counter < 2`; (5) back-edge targeted `node_a.start` but `continue_data` contains `{items, counter}` — retargeted to `items`; (6) scope tracking included sink call with `None` scope — filtered by `node.id`.
- [docs] **Plan 7-6 design upgrade — direct scope injection (3rd review round):** Replaced "spread edges from entry node" with "direct scope injection in `_execute_node` for all cycle nodes." The old approach was fundamentally broken for multi-node sequential bodies: spread edges are snapshots, so node B can't see node A's state update via the entry's spread. New mechanism: scheduler injects scope fields into every cycle node's `inputs`, merges outputs back — ~15 lines in `_execute_node`, zero executor changes. Edge target updated from ~15 to ~5. Added `active_loop_scope_id` to `ExecutionContext`, 4 new tests (7-14 through 7-17), decisions on parallel level safety/nested loops/first-pass semantics.
- [docs] **Plan 7-6 reviewed and hardened (2 review rounds):** Resolved 8 findings: (1) scope init timing — moved from `_iterate_cycle` to `GateExecutor.execute` first call; (2) body output merge source — reads from gate's `continue_data`, no new collection mechanism; (3) iteration counter sync — scheduler writes into scope; (4) spread edge invariant — requires target port ref and landing port; (5) spread validation — skip per-field schema check; (6) markdown integration corrected — gate nodes are synthesized from `loop()` flow syntax, not standalone files; (7) `GraphMutator` + validation tasks added for spread edges; (8) 4 new tests for iteration counter, spread validation, spread resolve_inputs, flow parser.
- [docs] **Plan 7-6 node state simplification:** Created `docs/plans/7-6-node-state-simplification.md` — three backward-compatible engine changes (loop-scoped state bag, code node port defaults, struct/spread edges) to eliminate state-threading boilerplate in iterative workflows. Motivated by vibe_research analysis: 87 edges → ~5, ~500 lines of unpack/repack boilerplate eliminated. Registered as sub-plan of 7-core-hardening.
- [fix] **`edit_edge` added to `_STRUCTURAL_OPS`:** Edge endpoint rewiring via `edit_edge` now triggers entry/exit point recomputation. Previously `edit_edge` was excluded, leaving stale `entry_points`/`exit_points` when an edge was rewired.
- [fix] **Plan 10-8 post-review fixes (4 bugs from code review):** (1) Gate while-mode default ports now emit `continue`/`done` instead of `true`/`false`; `_default_ports` accepts optional `config` to select mode; `review_loop` pattern uses `continue` port and `True` condition. (2) Idempotency key scoped by `(graph_id, key)` tuple instead of global key — prevents cross-graph false hits. (3) Entry/exit point recomputation gated behind `_STRUCTURAL_OPS` set — `edit_node`/`set_position` no longer silently change entry/exit points. (4) `record_apply` metric moved after validation gate — only counts as success when the graph is actually persisted.
- [feat] **Plan 10-8 NL mutation hardening — complete.** All 11 tasks implemented (Tasks 1-10 code-complete, Task 11 metrics/flags deployed; 11-2/11-3/11-5 are operational and await deployment time). 300 tests pass (114 new across 4 test files). Files changed: `graph_mutator.py` (validate-before-save, port-aware AddEdge, entry/exit recompute, pattern macros), `chat_manager.py` (typed oneOf schema, port/config prompt reference, auto-retry, stale-plan re-plan), `app.py` (validation gate, idempotency key, feature flags, metrics endpoints), `mutation_metrics.py` (new). Docs updated: `llm-api-guide.md`, `architecture.md`, `10-3-nl-graph-mutation.md`.
- [feat] **Acceptance metrics and feature flags (Plan 10-8, Task 11-1/11-4):** Added `mutation_metrics.py` with in-memory `MutationMetrics` collector tracking `apply_success_rate`, `post_validate_pass_rate`, and `avg_user_turns_to_success`. Added `GET /api/metrics/mutations` and `POST /api/metrics/mutations/reset` endpoints. Wrapped validation gate in `apply_mutation` with `DAN_STRICT_MUTATION_VALIDATION` env flag and auto-retry in `chat_manager.py` with `DAN_MUTATION_AUTO_RETRY` env flag for staged rollout. Both default to `true`. Added 6 unit tests for the metrics collector.
- [test] **Mutation quality CI suite (Plan 10-8, Task 9-1):** Added `tests/test_server/test_mutation_quality.py` with 18 deterministic end-to-end mutation scenarios. Each test applies a `MutationPlan` via `GraphMutator.apply()`, asserts success, parses the result through `Graph.model_validate()`, and runs `validate_graph()` filtering only fatal errors. Covers all node types (llm, code, gate, rag, validator, input, for_each, router), edge operations (add/remove/rewire), edit/position ops, and complex multi-node workflows.
- [feat] **Pattern macros for graph shapes (Plan 10-8, Task 7):** Added `ExpandPattern` operation and `PATTERN_LIBRARY` with 4 patterns (`chain`, `review_loop`, `fan_out`, `rag_qa`) to `graph_mutator.py`. Patterns expand into primitive add_node/add_edge ops that go through the same validation and dry-run flow. Updated `chat_manager.py` tool schema with `expand_pattern` op and system prompt with pattern reference. Added 6 tests covering all patterns, unknown-pattern errors, and composition with manual ops.
- [feat] **Stale-plan recovery and apply idempotency (Plan 10-8, Task 10):** In `chat_manager.py`, `send_message_with_tools` now auto-replans against the latest graph revision when dry-run returns `stale_plan: true`. In `app.py`, the `apply-mutation` endpoint accepts an optional `idempotency_key` to prevent duplicate applies, and returns a clear user message on stale-plan errors. Added 3 tests covering stale-plan detection and idempotency key model validation.
- [docs] **Plan 10-8 final review refinement:** Reworked `docs/plans/10-8-nl-mutation-hardening.md` to match numbered task/sub-task format used by prior plan files (removed A-I headings). Added final hardening scope: stale-plan auto-recovery, apply idempotency guard, and explicit acceptance-metric/rollout tasks (`apply_success_rate`, `post_validate_pass_rate`, `avg_user_turns_to_success`).
- [docs] **Plan 10-8 NL mutation hardening:** Created `docs/plans/10-8-nl-mutation-hardening.md` — 9 sub-plans (A-I) covering validate-before-save gate, port-aware AddEdge, entry/exit recompute, typed per-op tool schema, system prompt enrichment, auto-retry loop, pattern macros, llm-api-guide sync, mutation quality CI. Addresses 7 failure classes found in two review rounds of the chat→mutate→save pipeline. Supersedes unchecked items in `10-3-nl-graph-mutation.md`.
- [fix] **Checkpoint circular-reference crash in vibe strategy runs:** Removed self-referential `out["result"] = out` from `_run_strategy_script`. This prevents `ValueError: Circular reference detected` during checkpoint JSON serialization.
- [fix] **Code sandbox builtin coverage for generated scripts:** Added `iter` and `next` to `_ALLOWED_BUILTINS` so agent-generated `build_factor` code using iterator idioms no longer fails with `NameError: name 'next' is not defined`.
- [fix] **Strategy coder reliability guardrails:** Updated `strategy_coder.md` with strict rules to preserve `ret`, sort both sides before `merge_asof`, and return empty-but-valid factor frames on data failures.
- [fix] **Engine input injection for InputNode variables:** Scheduler now maps virtual run inputs (`__input__<node_id>`) into `InputNode.variables` (not only `input_ports`). This fixes workflows where `Engine.run(inputs=...)` was silently lost at input nodes and downstream code fell back to defaults.
- [fix] **While-gate body pre-run skip logic:** Scheduler no longer treats all `gate.continue/loop` edges as always-runnable. Loop bodies now wait for the gate's initial continue signal, while iteration re-entry still works via virtual injected loop feedback.
- [fix] **CRSP loader contract for custom strategy scripts:** `load_crsp()` now guarantees canonical aliases (`ret`, plus `mktcap`/`market_cap_month` parity). Fixes runtime `KeyError: ['ret']` in agent-generated `build_factor` code that drops/uses `ret`.
- [refactor] **Vibe research legacy file cleanup:** Removed obsolete single-department workflow remnants (`strategy_creation_multi.md`, `unpack_strategy_creation_multi.md`, `aggregator.md`) from `examples/vibe_research_md/`. Updated `WORKFLOW.md` to reflect current `output_per_dept` + `merge_dept_results` flow.
- [test] **Regression coverage verified:** Re-ran targeted engine tests covering input injection and while-gate scheduling behavior to confirm both fixes.

## 2026-02-26
- [fix] **Remove redundant `{"result": payload}` wrapping from workflow files:** `strategy_to_item.md`, `bug_fixer.md`, and `workflow_multi_dept_builder.py` updated to return dicts directly. The CodeExecutor now auto-exposes the full dict on `.result`, so the manual wrapping caused double-nesting.
- [fix] **Suppress dead-edge warnings for optional target ports:** `DEAD_EDGE_WARNING` no longer fires when the target input port has `required=False`. Missing data on optional ports is expected behavior. Added test.
- [test] **Plan 6-14 complete (904 pass, 0 fail).** All 6 batches landed plus review fixes: CodeExecutor `.result` contract fix, edge port diagnostics, dead-edge warnings, GateExecutor input tests, compiler semantics tests, real-executor integration tests.
- [test] **Plan 6-14 Batch 6 — Real executor integration tests:** Created `tests/test_engine/test_real_executor_integration.py` with 9 tests using REAL `CodeExecutor` + `GateExecutor` (no mocks): if/else routing, while loop with counter, `.result` port wiring end-to-end.
- [test] **Plan 6-14 Batches 4 & 5 — GateExecutor input handling + compiler semantics:** 9 tests in `test_gate_executor.py` (dict flattening, single-value mapping, `try_more` default, done unwrapping, `gate_evaluated` event). 3 tests in `test_compiler.py` (`until:` negation, loop edge structure, if-gate edges).
- [feat] **Plan 6-14 Batch 3 — Runtime dead-edge warnings:** Added `DEAD_EDGE_WARNING` event type. Scheduler emits warning when data edge source port has no value, suppressed for gate inactive branches. 3 tests in `test_dead_edge_warnings.py`.
- [feat] **Plan 6-14 Batch 2 — Edge port diagnostics:** `_check_edge_endpoints` and `_resolve_chain_ports` error messages now list available ports. Tests in `test_validator.py` and `test_compiler.py`.
- [fix] **Plan 6-14 Batch 1 — CodeExecutor `.result` contract parity:** `CodeExecutor` now exposes both flattened keys and full dict on `"result"` port for dict outputs (inline + subprocess), matching `ToolExecutor`. Also fixed `sandbox/adapters.py` bootstrap preamble (was wrapping scalars). Updated 5 existing tests, added 2 new regression tests.
- [docs] **Plan 6-14 patched with batched structure:** Rewrote plan into 6 independent batches after code-level audit. Removed redundant sub-tasks (2-4 already done, 6-2 duplicates 2-3), added precise file/line references and test counts per batch, added baseline coverage table with gap column. Order: Batch 1 CodeExecutor `.result` fix → Batch 2 edge diagnostics → Batch 3 dead-edge warnings → Batch 4 GateExecutor input tests → Batch 5 compiler semantics → Batch 6 real-executor integration.
- [docs] **Plan 6-14 reframed to seam hardening first:** Updated `docs/plans/6-14-gate-test-suite.md` from gate-only testing to a priority-ordered robustness plan: (1) code executor `.result` parity with tool executor, (2) stricter explicit edge-port validation with clearer diagnostics, (3) runtime dead-edge warnings, then expanded compiler/gate/builder regression coverage. Updated `docs/todo.md` wording accordingly.
- [fix] **Decompiler cycle & nesting:** Fixed three bugs in `decompiler.py`: (1) topological sort now detects while-loop back edges and excludes them so cycle nodes (gate + body) appear in output; (2) sub-graph bodies now emit explicit edges (not just `>>` chains); (3) nested sub-graphs (composite inside composite) are recursed into, producing correct Python DSL for the full graph depth. Added `_resolve_sub_graph` for recursive sub-graph lookup.
- [feat] **--from-json flag:** `run_multi_dept.py` now supports `--from-json <path>` to load a pre-compiled graph JSON directly, skipping markdown compilation. Enables reliable testing from the exact serialized graph. JSON round-trip verified: .md compile → JSON → load produces identical nodes, edges, and sub-graphs.
- [fix] **use_builtin restricted to MOM/REV:** Strategy manager prompt now enforces that `use_builtin=true` is only valid for MOM and REV departments (momentum with lookback/skip). FUND, CASH, OPS, ML must use `use_builtin=false` (custom code path). Added department theme descriptions and data source guidance. Updated `strategy_coder.md` with department-specific guidance for FUND (Compustat fundamentals), CASH (cash flow signals), OPS (operating efficiency), ML (ensemble/PCA), and namespace docs (`load_crsp`, `pd`, `np`, `Path` available).
- [feat] **Per-department tracking list:** Each strategy manager now maintains a persistent `output/tracking_{dept_code}.json` roadmap containing tried strategies (with status/results), planned strategies (with priority/rationale), and department notes. Loaded before each strategy decision and saved after. Added `load_tracking.md`, `save_tracking.md`, wired into `department_run.md`. The LLM updates the tracking list every iteration, making progress resumable across reloads.
- [fix] **Per-department isolation:** `expand_departments` now filters `results` and `strategies_tried` by department code prefix (e.g. MOM_*) so each department only sees its own history. `params_hint` is derived from the department's last strategy, not the global last. Added `n_total_results` for global progress awareness. Updated `strategy_manager_dept` prompt to reflect scoped inputs.
- [fix] **Unpack fallback:** `unpack_multi_dept_input` fallback for `active_departments` changed from `["MOM"]` to the full fixed 6-department list, consistent with all other nodes.
- [fix] **Multi-dept stop policy:** Replaced early-stop behavior with hard constraints in example workflow — loop max raised to 60, governor now enforces `max_factors` plus safety cap (`max_factors + 10`), and orchestrator prompt no longer asks to stop at iteration 5.
- [fix] **Fixed department set mode:** Multi-dept example now runs a fixed list (`MOM, REV, FUND, CASH, OPS, ML`) and ignores dynamic add/delete for now; persist/expand path no longer collapses to single-department MOM fallback.
- [fix] **Keep failed backtests visible:** `merge_dept_results` now preserves error backtest outputs instead of dropping them, so CSV/debug output reflects failed attempts instead of appearing empty.
- [feat] **Parallel departments:** Multi-dept workflow now runs departments in parallel via `expand_departments | each(department_run, parallel: 6)`. No longer restricted to MOM or first department — each active department runs its own strategy creation flow concurrently. Added expand_departments.md, department_run.md, unpack_item.md, output_per_dept.md, merge_dept_results.md.
- [fix] **Loader each-node referenceability:** ForEach nodes are added to nodes_by_id when created so subsequent flow lines (e.g. each.results → merge) can reference them. Post-flow duplicate check now skips nodes already added during flow.
- [feat] **run_multi_dept --emit-python:** Writes a twin Python builder script (`workflow_multi_dept_builder.py`) via `decompile(graph)`, so the workflow exists in both markdown and Python DSL for testing/CI.
- [feat] **run_multi_dept --debug-events:** Prints gate_evaluated (including try_more) and iteration_started/iteration_completed to stderr for loop exit debugging.
- [feat] **gate_evaluated condition_vars:** GateExecutor includes full condition_vars in gate_evaluated event data so debug output shows why a while loop exited (no example-specific hardcoding).
- [fix] **Gate done output:** While-gate now passes body value directly to "done" (inputs.get("input", inputs)) instead of full inputs wrapper, so write_csv receives governor output without extra nesting.
- [fix] **save_grid_csv input unwrapping:** Loop gate passes through full inputs `{"input": composite_output}`; governor wraps in `{"result": {results, ...}}`. _save_grid_csv now unwraps both layers before extracting results. Fixes empty grid_summary when loop output structure was misparsed.
- [fix] **save_grid_csv with workflow_inputs:** When workflow_inputs.results=[] shadows loop output, _save_grid_csv now falls back to extracting from input when results is empty. Fixes empty grid_summary.csv when loop produces data but results port gets [] from InputNode.
- [fix] **Tool executor result port:** When a tool returns a dict, the executor now also exposes it on the "result" output port (in addition to individual keys). Fixes empty grid_summary.csv — backtest_runner.result → bug_fixer was never populated because run_backtest returns quintiles/dates/etc. keys only; edges wiring tool.result → next need the full object.
- [feat] **Templates always visible:** TabBar "+ New" picker now shows built-in starters (Blank, ReAct Agent, Plan-Execute) at the top, independent of saved graphs. Added `openTabFromTemplate` store action to open a new tab from a predefined template. Fixes "not seeing any templates" when graph list is empty or API fails.
- [fix] **if(use_builtin) gate:** LLM nodes with output_schema now emit a "result" port with the full structured object. Compiler wires strategy_manager.result → if gate (when source has "result") so the gate receives use_builtin for condition evaluation. GateExecutor flattens dict inputs for conditions. Fixes "name 'use_builtin' is not defined" when running vibe research.
- [fix] **Runs with flattened graphs:** Validation no longer flags if_else gates inside a while-loop body as "creates cycle" — only gates that receive a back-edge are cycle creators. Disabled flatten by default (DAN_FLATTEN_LOOP_BODIES=0) so saving doesn't persist a flattened graph that fails validation. Rebuild with run_multi_dept.py --build-only to restore working graph.
- [refactor] **Layout decoupled from examples:** Removed `_inject_orchestrator_department_groups` from layout.py — it hardcoded vibe research node names. Project no longer caters to specific examples. Vibe research example injects its own loop_groups in run_multi_dept.py --build-only.
- [feat] **Flatten loop bodies at top level:** `flatten_loop_bodies()` in layout.py inlines while-loop body composites into the main graph. Enabled by default; set `DAN_FLATTEN_LOOP_BODIES=0` to disable.
- [feat] **Vibe research flattened:** iteration_multi_dept inlines department_strategy — unpack, orchestrator, persist, unpack_strategy, strategy_manager, coder, backtest, run_strategy, bug_fixer, aggregator, governor all visible in one drill. Layout injects Orchestrator (unpack+orchestrator+persist) and Departments (unpack_strategy through aggregator) loop groups.
- [feat] **Plan 6-13 Multi-dept visualization completed:** Layout injects `expand_loop_groups_by_default` metadata for key composites (orchestrator + department_strategy). Editor drillIn/drillOut/jumpToLayer sync `loopGroups` from layer metadata so group toggles persist; saveGraph persists loop_groups to active sub_graph when editing drilled-in layer. Verified layout + loop_groups injection on vibe_research_multi_dept.
- [feat] **Plan 7-5 general tool design:** Shared executor `execute_python()` in `dan.server.exec`; `run_python(code, **context)` tool registered. Refactored `_run_strategy_script` to use shared executor. Deprecated `plot_backtest`, `save_grid_csv` (kept registered). Added `run_python.md` tool spec.
- [docs] **Plan restructuring:** Removed 7-multi-department-adaptive from project plans (example-specific). Moved to examples/vibe_research_md/WORKFLOW.md. Phase 7.5 dropped; vibe research → Backlog.
- [docs] **Plan 7-5 general tool design (improved):** Added state-of-the-art alignment (Cursor/Claude patterns), shared executor extraction, run_python interface spec, safety/sandbox notes, migration tasks, and comparison table.
- [fix] **write_csv → each(plot_one):** Compiler prefers `results` port for tool_operator when present, so write_csv.results flows to each(plot_one).items correctly.
- [fix] **Orchestrator/department visibility:** Loop groups in sub_graph metadata (injected by layout) now apply when drilling in. drillIn, drillOut, jumpToLayer inject loop_groups from the current layer's metadata.
- [refactor] **Vibe research cleanup:** Removed department_state.py (logic in persist_dept_state + app.py), quant_lib/strategy_registry.py (unused), quant_lib/strategy_schema.md (superseded by factor_schema.py), quant_lib/run_grid_backtest.py (superseded by run_grid.py + workflow_grid).
- [feat] **Multi-dept simplify + visibility:** Removed extract_results; write_csv accepts loop output directly, extracts results, returns results for each(plot_one). Renamed iteration→orchestrator_and_departments, strategy_creation_multi→department_strategy. Loop groups for Orchestrator and Departments in iteration body.
- [feat] **Backend dynamic layout:** `GET /api/graphs/{id}?layout=true` or `DAN_LAYOUT_ON_LOAD=1` applies topological layout to nodes (top-level + sub_graphs). Editor requests layout by default.
- [feat] **Plan 7-1:** Multi-dept visualization plan (orchestrator visible, departments, coder retry gate).
- [fix] **Multi-dept run_strategy "No strategy code provided":** run_strategy and backtest_runner were running on both gate branches. Added gate.false→run_strategy.branch_trigger and gate.true→backtest_runner.branch_trigger so each runs only on its branch. Compiler: add if_else gate to nodes_by_id so flow can reference it.
- [feat] **dan-serve auto-reload:** `--reload` is now default; server restarts on Python/workflow changes under project root. Use `--no-reload` for production.
- [feat] **vibe_research_md cleanup:** Removed 24 unused .md files and 3 run scripts (run.py, run_grid.py, run_adaptive.py). Kept only workflow_multi_dept and its 18 agent files; run_multi_dept.py remains.
- [feat] **Orchestrator context:** Prompt now states project purpose, data/output paths, and strategy manager responsibilities (create strategies, reflect on results, improve).
- [feat] **Multi-department max_factors:** Hard constraint as workflow input. When len(results) >= max_factors, orchestrator and governor set try_more=false. entry, unpack, persist, governor updated; run_multi_dept.py --max-factors (default 20).
- [feat] **Multi-department adaptive workflow:** workflow_multi_dept.md — orchestrator (max 6 depts, delete for good), strategy_manager_dept (naming: dept_code+numbering+desc), strategy_coder (script-as-param), run_strategy_script tool, department state persistence. Factor schema (factor_schema.py) with validate_factor_df; run_backtest_from_factor_df; Compustat 6-month lag (apply_compustat_lag). run_multi_dept.py script; json/Path in code executor builtins.
- [fix] **Code executor:** Added `NameError` and `Exception` to `_ALLOWED_BUILTINS` so governor/bug_fixer `try/except NameError` works in restricted exec namespace (was: `NameError: name 'NameError' is not defined`).
- [fix] **Adaptive workflow nodes:** Added defensive `try/except NameError` in aggregator and merge_planner for all inputs (results, strategies_tried, iteration, backtest_result, planner_output, etc.) so missing upstream outputs don't break the loop.
- [fix] **Loop gate condition:** GateExecutor flattens body output `{"result": {...}}` into condition_vars so `until: "not try_more"` can access try_more; adds try_more=True default when missing (was: `name 'try_more' is not defined`).
- [feat] **Adaptive strategy workflow:** `workflow_adaptive.md` — LLM-driven loop with central planner, strategy writer, backtest, governor. `adaptive_iteration` composite: unpack → central_planner → merge_planner → strategy_creation (unpack → strategy_writer → strategy_to_item → backtest_runner → aggregator → governor). Loop `entry_adaptive | loop(adaptive_iteration, until: "not try_more", max: 5)`. Governor overrides try_more when all backtests fail. Standardized nodes: `unpack_loop_input`, `unpack_adaptive_input`, `merge_planner` (single result object), `aggregator` (pass-through start_year/end_year), `governor` (result object for gate).
- [feat] **Adaptive ↔ grid integration:** Adaptive workflow now outputs to same CSV and plots as grid. `extract_adaptive_results` extracts results from loop; `gate.done → extract_results`; `extract_results.results → write_csv`; `extract_results | each(plot_one)`. Compiler: add loop gate to `nodes_by_id` immediately so flow can reference `gate.done`. `run_adaptive.py` script (mirrors `run_grid.py`).
- [fix] **Editor graph list:** TabBar awaits `loadGraphList()` before opening picker; added refresh button in picker header; toolbar Refresh now reloads both graph list and current tab (no dan-serve restart needed).
- [feat] **Adaptive workflow — bug fixer:** Added `bug_fixer.md` between backtest_runner and aggregator; catches malformed/failed backtest output so the loop never breaks. Updated workflow description to "revolving, auto-expanding"; tags include governor, bug-fixer. README notes drill path: adaptive_iteration → strategy_creation to see governor and bug fixer.
- [fix] **Grid workflow diagram:** Compiler no longer creates `workflow_inputs.item → plot_one` — ForEach body agents used only in `each()` are excluded from the main graph so they receive items from the ForEach at runtime, not from Workflow Inputs. Agents used elsewhere (e.g. `processor` in if/else) remain in the main graph.
- [fix] **Grid plots:** Compiler now uses composite "results" (not "result") for ForEach items when composite has both, so plot_one receives all 10 backtest results → 10 plots. Reverted cumulative prepend; time axis uses actual data dates.
- [feat] **Grid-search workflow:** `workflow_grid.md` — 10 strategies via ForEach nodes (no hardcoded loops). strategies_config → each(backtest_one) → write_csv + each(plot_one). Saves factors, CSV summary, cumulative quintile + LS plots. Tools: `run_backtest` (item, lookback, skip, return_series), `plot_backtest`, `save_grid_csv`.
- [fix] **Vibe research summarizer:** Handle missing `results` when upstream tool fails (NameError); README notes to restart `dan-serve` if "Unknown tool: run_backtest" appears.
- [feat] **Vibe research example:** `examples/vibe_research_md/` — simple factor research workflow in markdown format. Nested composite (data_loader → compute_factor), mock data (no CRSP/Compustat), code-only summarizer for demo without LLM API. `run.py` for CLI execution; `--build-only` saves `graphs/vibe_research.json` for the editor.
- [feat] **quant_lib:** CRSP/Compustat loaders, momentum factor, quintile backtest. Data from `AUTO_QUANT_ROOT` (default: `~/Dropbox/CUHK-phd/projects/auto-quant`). `run_backtest.py` CLI; `run_backtest` tool registered in server. `workflow_real.md` uses real data via tool.

## 2026-02-25 (Phase 3.75 — 6-12 control flow consolidation)
- [feat] `NODE_TYPE_CATALOG`: removed legacy `if_else` and `while_loop`; added `gate_if_else` (If/Else Gate) and `gate_while` (While Gate); removed generic `gate`
- [feat] `graphAdapter.ts`: `createDefaultNode` for `gate_if_else` and `gate_while` creates `GateNode` with appropriate `gate_mode`; added default case that throws for unknown types
- [feat] `paletteTemplates.ts`: removed redundant IfElse Gate and While Gate templates; ReAct template now uses CompositeNode with GateNode(while) body (LLM → Tool → Gate with back-edge) instead of WhileLoopNode
- [feat] `app.py`: `DAN_GATE_MIGRATION_ENABLED` defaults to `true` so legacy graphs are migrated on load
- [feat] `nodeIcons.tsx`: replaced `if_else`/`while_loop` with `gate_if_else`/`gate_while` icons
- [feat] `MentionAutocomplete.tsx`: added `gate` fallback to typeLabel for display
- [docs] Created `docs/plans/6-12-control-flow-consolidation.md`; updated `todo.md`
- [test] TypeScript clean, 870 backend tests passing

## 2026-02-25 (Phase 3.75 — 6-12 ReAct cyclic subgraph fix)
- [fix] `scheduler.py`: `_run_subgraph` now uses `_topological_levels_with_backedges` and `_execute_with_cycles` when the sub-graph contains gate(while) back-edges, fixing ReAct palette template (Composite with cyclic body) which previously executed no nodes
- [fix] `_execute_with_cycles`: added `skip_checkpoint=True` parameter so sub-graph execution does not overwrite parent checkpoints
- [test] Added `TestCompositeCyclicSubgraph::test_composite_with_gate_loop_in_body` — verifies Composite with gate loop in body runs correctly
- [test] 871 backend tests passing

## 2026-02-25 (Phase 6 — Extended Capabilities — backend implementation complete)
- [feat] **9-3 Handoff Validator:** `ValidatorNode` model (5 rule types: required_keys, non_empty, schema_conformance, type_check, custom_expression) with valid/invalid output port routing. `ValidatorExecutor` with `resolve_dotpath()` utility, `ValidationViolation` dataclass, `on_failure` modes (route/warn/halt), `strict_mode` early stop, `VALIDATION_RESULT` event emission. Boundary auto-insert utility (`generate_entry_validator`, `generate_exit_validator`, `insert_boundary_validators`) for composite nodes. 49 new tests.
- [feat] **9-2 Subprocess Sandbox:** `SandboxConfig` Pydantic model with `pass_env` glob matching, `SandboxRunner` (asyncio subprocess, timeout enforcement, output truncation, env filtering, memory limits via `resource.setrlimit`), `PythonAdapter` + `ShellAdapter` language adapters. `CodeExecutor` upgraded with subprocess routing (inline `exec()` fast-path preserved as default). `shell_command` tool upgraded with optional sandbox mode via `DAN_SANDBOX_SHELL` env var. `SANDBOX_STARTED`/`SANDBOX_COMPLETED` events. 50 new tests.
- [feat] **9-1 RAG Knowledge Retrieval:** `EmbeddingProvider` protocol with `OpenAIEmbeddingProvider` and `LocalEmbeddingProvider` (sentence-transformers via `asyncio.to_thread()`). `EmbeddingRegistry` with exact/prefix/default resolution. `VectorStore` protocol with 3 backends: `MemoryVectorStore` (pure-Python stdlib `math`, zero deps), `FAISSVectorStore` (faiss-cpu, L2-normalized inner product, persistence), `ChromaVectorStore` (native metadata filtering). `RAGExecutor` (embed → search → filter → return chunks/scores, store caching, events). `Indexer` for batch index lifecycle. 61 new tests (48 pass, 13 skip for optional deps).
- [infra] **Shared foundation:** Added `RAGOperator` and `ValidatorNode` to `Node` discriminated union, `NodeTypeRegistry`, `DEFAULT_OUTPUT_PORTS`, `_build_node()`, `_register_defaults()`. Added `wf.rag()` and `wf.validator()` builder DSL methods. Decompiler support for both new node types. 5 new `EventType` entries. `pyproject.toml`: optional dep groups `faiss`, `chroma`, `embeddings`, `jsonschema`, `all-rag`. Test suite: 771 passed, 15 skipped.
- [fix] **Boundary + chaining correctness:** `insert_boundary_validators()` now preserves arbitrary composite input/output port mappings (not hardcoded to `input`/`result`) by rewiring per-port and generating passthrough validator ports. `ValidatorExecutor` now mirrors input ports on successful validation, enabling boundary passthrough without breaking `valid/invalid` routing. Builder `>>` chaining aligned for new nodes: `validator` now defaults to target port `data`, `rag_operator` defaults to `query` (with decompiler default-edge detection updated accordingly). Added regression tests for custom composite ports and default chaining behavior.
- [feat] **Option B embedding-provider plumbing:** `EngineConfig` now supports first-class RAG embedding settings (`embedding_providers`, `embedding_model_provider_map`, `default_embedding_model`). Scheduler builds an `EmbeddingRegistry` during engine startup and threads it through `ExecutionContext`, so `RAGExecutor` resolves providers from runtime config by default (no ad-hoc context mutation required). Server `_get_engine_config()` now wires embedding provider defaults from env (`DAN_EMBEDDING_API_KEY`, `DAN_EMBEDDING_BASE_URL`, `DAN_DEFAULT_EMBEDDING_MODEL`, optional local provider toggle).

## 2026-02-25
- [docs] **Phase 6 planning complete:** Created top-level plan `9-extended-capabilities.md` and 3 sub-plans for the Extended Capabilities phase. 9-1: RAG / Knowledge Retrieval Node (EmbeddingProvider protocol supporting API + local models, VectorStore abstraction with FAISS/ChromaDB/memory backends, RAGOperator model + executor, index lifecycle management, editor/builder integration; 12 task groups, ~45 sub-tasks). 9-2: Subprocess Sandbox (SandboxRunner with asyncio subprocess, resource limits, language adapters for Python/shell, CodeExecutor upgrade preserving inline exec() fast-path, shell_command tool hardening; 11 task groups, ~35 sub-tasks). 9-3: Handoff Validator Node (ValidatorNode with 5 rule types, valid/invalid port routing, boundary auto-insert utility for composites, GateNode-style colored handles; 8 task groups, ~30 sub-tasks). Recommended execution order: 9-3 first (smallest), 9-2 second (safety foundation), 9-1 last (largest, benefits from sandbox + validators). Updated `todo.md` with plan links.
- [docs] **Phase 6 plan review fixes:** (1) 9-3 `schema_conformance` rule now validates runtime data payloads via `jsonschema.validate()` (optional dep) with pure-Python fallback, NOT design-time `check_schema_compatible()` which is schema-vs-schema. (2) 9-3 `on_failure` renamed `"skip"` to `"warn"` to avoid semantic collision with `RetryPolicy.on_failure="skip"` (which maps to `NodeStatus.SKIPPED`). (3) 9-2 goal reframed from "secure subprocess" to "operational guardrails" (timeouts, memory caps, output limits, env filtering) — explicitly NOT a security sandbox. (4) 9-2 added `pass_env: list[str]` to `SandboxConfig` for selective env var forwarding (supports exact names and glob prefixes like `"DAN_*"`), replacing blanket `DAN_*` removal that would break existing workflows needing `DAN_LLM_API_KEY`. (5) 9-2 removed `network_access` field (was promising unimplemented feature). (6) 9-1 `MemoryVectorStore` corrected from "numpy cosine similarity" to pure-Python stdlib `math` (zero external deps), numpy as future optimization. (7) 9 parent plan sandbox shared decision updated to match.

## 2026-02-25
- [docs] **Phase 6 planning complete:** Created top-level plan `9-extended-capabilities.md` and 3 sub-plans for the Extended Capabilities phase. 9-1: RAG/knowledge retrieval node (embedding provider protocol with API + local support, vector store abstraction with FAISS/ChromaDB backends, `RAGOperator` model + executor, index lifecycle via `VectorStoreManager`, editor/builder integration; 13 task groups, ~50 sub-tasks). 9-2: subprocess sandbox (`SandboxConfig` model replacing placeholder dict, async subprocess runner with timeout/resource limits, `CodeExecutor` upgrade from `exec()` to subprocess mode, `shell_command` hardening, security policies; 12 task groups, ~40 sub-tasks). 9-3: handoff validator (`ValidatorNode` with 5 rule types — required_keys/schema/non_empty/expression/type_check — `ValidatorExecutor` with dotpath traversal, composite boundary auto-insert utility; 8 task groups, ~35 sub-tasks). Recommended order: 9-3 → 9-2 → 9-1 (all independent). Updated `todo.md` with plan links.
- [test] **Loader advanced fixtures + tests:** Added `tests/fixtures/markdown/composite/` (inner_a, inner_b, outer_composite, workflow) and `tests/fixtures/markdown/schemas/outline.json`, `schema_agent.md`, `schema_workflow.md`. New `tests/test_loader/test_advanced.py` with 9 tests: composite agent parsing, compilation, subgraph structure, inner nodes/edges; linked JSON Schema port parsing, workflow compilation, schema loaded into port, missing-schema diagnostic.
- [docs] **Phase 5 planning complete:** Created top-level plan `8-markdown-agent-format.md` and 4 sub-plans for the Markdown Agent Format phase. 8-1: format design + parser (agent/workflow file formats, flow notation, port type inference, versioning). 8-2: markdown→graph compiler (`dan.loader.compile()`, auto-wiring, diagnostics). 8-3: validation + parity + advanced features (paper-writing rewrite, builder parity checklist, coexistence policy, composite agents, linked JSON Schema). 8-4: round-trip decompiler (graph→markdown, visual editor export, conformance tests). Updated `todo.md` with plan links.
- [docs] **Phase 5 plan review fixes:** Validated all 8-* plans against runtime models and compiler interfaces. Fixed: `gate_type`→`gate_mode` (matches `GateNode` model), loop compilation uses gate-style flat back-edges (not deprecated `WhileLoopNode` sub-graphs), `tool_arguments`→`tool_config`, `HumanInTheLoopNode.prompt_template`→`.prompt`, `RouterNode` uses `model`+`route_descriptions` (not freeform prompt), `input_mapping`→`input_mappings` (plural), gate default output port `true`/`done` (not `result`). Added: `InputNode` strategy for graph-level variables, best-effort decompilation policy with `DecompileResult.diagnostics`, type-specific frontmatter fields for router/human/tool. Fixed YAML dependency note (PyYAML is transitive via Pydantic, not stdlib).


## 2026-02-25 (Phase 5 — markdown loader compiler unblock)
- [feat] Added `src/dan/loader/compiler.py` with end-to-end markdown workflow compilation: agent→node mapping (`llm`/`tool`/`code`/`human`/`router`/`composite`), flow→edge/control-node compilation (`chain`, `each`, `loop`, `if`), `InputNode` inference for unresolved required inputs, context declaration mapping, and graph validation integration.
- [fix] Updated `src/dan/loader/__init__.py` to expose a real `compile_workflow()` export and make `load()` raise with formatted diagnostics on compile errors instead of returning `None`.
- [fix] Updated `src/dan/loader/parser.py` to stop swallowing flow parser import failures; flow-line parse failures are now handled per-line without aborting full workflow parse.
- [fix] Updated `examples/paper_writing_md/section_writer.md` and `examples/paper_writing_md/assembler.md` port contracts/code block output so `examples/paper_writing_md/workflow.md` compiles without errors (warnings remain for intentionally untyped control-flow edges).
- [test] Added/validated loader test coverage for compiler + parser/type/flow modules; `pytest tests/test_loader -q` now passes (`67 passed`), and the compiler-specific suite passes (`16 passed`).
- [docs] Updated Phase 5 tracking docs (`docs/todo.md`, `docs/plans/8-markdown-agent-format.md`, `docs/plans/8-1-format-design-parser.md`, `docs/plans/8-2-markdown-graph-compiler.md`, `docs/plans/8-3-validation-parity-advanced.md`) and updated `docs/architecture.md` to include `src/dan/loader`, `examples/paper_writing_md`, `tests/test_loader`, and current test count (`570` collected).

## 2026-02-25 (Phase 5 — decompiler, validation, parity — Phase 5 complete)
- [feat] Added `src/dan/loader/decompiler.py`: graph→markdown round-trip decompiler. Generates one `.md` per node + `workflow.md`. Supports all node types (LLM, Tool, Code, Human, Router, Composite), control-flow decompilation (`ForEach` → `| each()`, `GateNode(while)` → `| loop()`, `GateNode(if_else)` → `| if()`), chain detection, port→blockquote generation, system prompt sections, retry policy in frontmatter, slugified file naming with collision handling, best-effort output for unsupported features.
- [feat] Added export API endpoints: `GET /api/graphs/{id}/export/markdown` (JSON preview with all generated files + diagnostics) and `GET /api/graphs/{id}/export/python` (Python builder code).
- [feat] Composite agent compilation validated end-to-end: `type: composite` → `CompositeNode` with recursive `body_graph` sub-graph from internal `## Agents` + `## Flow` sections.
- [feat] Linked JSON Schema validated: `> Returns: outline (schema: schemas/outline.json)` loads external schema into `OutputPort.schema`. Missing/malformed schema files produce compiler diagnostics.
- [docs] Builder ↔ Markdown parity checklist in `docs/plans/8-3-validation-parity-advanced.md` — full feature matrix covering all node types, control flow, edges, graph-level features, and meta fields. Documented 4 gaps: `reduce`, `import_workflow`, `control_edge`, `artifact_ref`.
- [docs] Coexistence policy defined: peers-not-layers, no mixed workflows, migration paths via decompilers, usage guidance (markdown for authoring, Python for CI, visual editor for exploration).
- [test] Added `tests/test_loader/test_decompiler.py` (22 tests): simple/complex workflow decompilation, forward+backward round-trip conformance, file naming.
- [test] Added `tests/test_loader/test_advanced.py` (9 tests): composite agent parsing+compilation (5), linked JSON Schema loading+validation (4).
- [test] Added `tests/test_loader/test_paper_writing_parity.py` (17 passed, 2 skipped): structural comparison between `paper_writing_md/` markdown workflow and `paper_writing.py` Python builder workflow. Known gaps documented (GateNode vs WhileLoopNode, foreach granularity).
- [test] Full loader suite: `115 passed, 2 skipped`. Total project test count: `620 collected`.
- [docs] Phase 5 marked complete in `docs/todo.md`, `docs/plans/8-markdown-agent-format.md` (completed), `docs/plans/8-3-validation-parity-advanced.md` (completed), `docs/plans/8-4-round-trip-decompiler.md` (completed). Architecture updated with `compiler.py`, `decompiler.py`, `diagnostics.py`.

## 2026-02-25 (Phase 5 — decompiler review fixes)
- [fix] Decompiler now recursively writes inner agent files for `CompositeNode` sub-graphs and `ForEachNode` body sub-graphs. Previously only top-level node files were written, causing composite markdown to reference missing files and fail to recompile.
- [fix] Decompiler synthesizes a composite agent file for `ForEach` nodes with multi-node body sub-graphs (`>1` non-input nodes). The `| each()` line references the synthetic composite instead of only the first body node, preventing topology loss.
- [fix] Decompiler port emission now preserves schema and `required` values that differ from compiler defaults. Previously, default-named input/output ports were always suppressed, losing `required=True` and non-string schemas on round-trip.
- [fix] Compiler `_create_input_node` now infers output port schemas from actual target port schemas (e.g. `object`, `array`) instead of always using `string`. `InputVariable.type` remains constrained to `string|number|boolean` per the Pydantic model; the output port schema is now inferred separately.
- [fix] Decompiler escapes double-quote characters in gate conditions when emitting `| loop()` and `| if()` flow lines, preventing parse failures on recompile.
- [fix] Parser `parse_workflow_file` now collects `FlowParseError` exceptions as `parse_warnings` on `WorkflowSpec` instead of silently skipping malformed flow lines. Compiler converts these to `Diagnostic(level="warning")` with source location.
- [fix] Removed duplicate `compiler.py`/`diagnostics.py` entries in `docs/architecture.md` loader section.

## 2026-02-25 (Roadmap reprioritization — conversational workflow authoring)
- [docs] Updated `docs/todo.md` to move "Chatbox for NL flow creation" from Phase 9 to the immediate next phase (Phase 7) and expanded it with concrete sub-features: `@` mentions for nodes/workflows, chat history persistence, chat↔canvas co-navigation, graph-diff confirmation UX, and shared execution context.

## 2026-02-25 (Phase 7 — conversational workflow authoring planning)
- [docs] Created top-level plan `10-chatbox-nl-workflow.md` and 5 sub-plans for conversational workflow authoring (Phase 7 lead feature). 10-1: Chat Panel & Backend API (chat UI component, FastAPI message endpoint, LLM integration, graph-aware system prompt, streaming responses; 8 task groups, ~25 sub-tasks). 10-2: `@` Mention & Co-Navigation (trigger detection, autocomplete dropdown for nodes/workflows/sub-graphs, mention chips, click→canvas selection, canvas→chat suggestion, mention resolution for backend; 9 task groups, ~30 sub-tasks). 10-3: NL→Graph Mutation Engine (GraphOperation discriminated union, GraphMutator apply engine, LLM function-calling schema, multi-step planning, validation + error recovery, dry-run mode; 8 task groups, ~35 sub-tasks). 10-4: Graph Diff & Confirmation UX (before/after diff computation, visual diff preview dialog, accept/reject/partial-accept, undo stack integration, post-apply animations, conversation-level rollback; 8 task groups, ~30 sub-tasks). 10-5: Chat History & Execution Integration (ChatMessage/ChatThread models, filesystem persistence, thread list UI, auto-restore on reload/tab-switch, graph delta tracking, run-from-chat commands, execution streaming in thread, error diagnosis; 10 task groups, ~40 sub-tasks). Build order: 10-1→10-2→10-3→10-4→10-5 (mostly linear). Updated `docs/todo.md` with plan links.

## 2026-02-25 (Phase 6 — Plan 9-3: Handoff Validator Node — backend complete)
- [feat] Added `src/dan/executors/validator.py`: `ValidatorExecutor` with five rule types (`required_keys`, `non_empty`, `schema_conformance`, `type_check`, `custom_expression`), `resolve_dotpath()` utility for nested dict/list access, `ValidationViolation` dataclass, three `on_failure` modes (`route`/`warn`/`halt`), `strict_mode` early-stop, `VALIDATION_RESULT` event emission. Uses `jsonschema.validate()` with pure-Python fallback.
- [feat] Added `src/dan/validation/boundaries.py`: `generate_entry_validator()`, `generate_exit_validator()`, and `insert_boundary_validators()` for auto-inserting ValidatorNodes at composite node boundaries based on `external_input_schema`/`external_output_schema`.
- [test] Added `tests/test_engine/test_validator.py` (49 tests): `resolve_dotpath` unit tests, per-rule-type evaluation tests, `ValidatorExecutor` integration tests (routing, strict_mode, multi-rule), `on_failure` mode tests, event emission tests, edge cases (empty rules, non-dict data, data unwrapping), boundary auto-insert tests, builder/decompiler round-trip test, validator chaining test.
- [docs] Updated `docs/architecture.md` with `ValidatorNode` in control_flow.py, `validator.py` in executors, `boundaries.py` in validation. Updated plan `9-3-handoff-validator.md` (tasks 1-4, 6-8 checked off).

## 2026-02-25 (Phase 6 — subprocess sandbox, Plan 9-2)
- [feat] Added `src/dan/sandbox/__init__.py`: `SandboxConfig` (Pydantic BaseModel) with `mode`, `timeout_seconds`, `memory_mb`, `pass_env`, `filesystem_paths`, `max_output_bytes`; `SandboxResult` dataclass with `stdout`, `stderr`, `exit_code`, `output_files`, `duration_ms`, `memory_peak_mb`, `truncated`.
- [feat] Added `src/dan/sandbox/adapters.py`: `LanguageAdapter` protocol, `PythonAdapter` (bootstrap preamble with `_inputs.json`/`_result.json` round-trip), `ShellAdapter` (executable script, env var inputs), `ADAPTERS` registry.
- [feat] Added `src/dan/sandbox/runner.py`: `SandboxRunner` class with `async run()` → `(SandboxResult, dict | None)`. Features: unique temp dir per run, `asyncio.create_subprocess_exec`, timeout enforcement via `asyncio.wait_for`, output truncation, `fnmatch`-based env filtering (`pass_env`), memory limits via `resource.setrlimit` (best-effort), structured output from `_result.json`, temp dir cleanup.
- [feat] Upgraded `src/dan/executors/code.py`: `CodeExecutor` now routes `mode="inline"` to existing `exec()` fast-path (unchanged), `mode="subprocess"` to `SandboxRunner`. Emits `SANDBOX_STARTED`/`SANDBOX_COMPLETED` events around subprocess runs. Maps `SandboxResult` to `NodeResult` with structured output. Preserves `on_failure` handling (error/skip/halt). Invalid `sandbox_config` falls back to inline with warning.
- [feat] Upgraded `src/dan/tools/shell_command.py`: optionally routes through `SandboxRunner` + `ShellAdapter` when `DAN_SANDBOX_SHELL=true` env var is set. Resource limits from `DAN_SANDBOX_TIMEOUT`/`DAN_SANDBOX_MEMORY_MB` env vars. Default behavior (raw `create_subprocess_shell`) unchanged. `DAN_SHELL_ALLOW` allowlist enforced regardless of mode.
- [test] Added `tests/test_engine/test_sandbox.py` (50 tests): `SandboxConfig`/`SandboxResult` models, `PythonAdapter`/`ShellAdapter` preparation, env filtering (`_filter_env`), `SandboxRunner` unit tests (success, timeout, truncation, input injection, structured output, stderr, exit codes, shell execution, unsupported language, temp dir cleanup), resource limits (platform-dependent skip), `CodeExecutor` inline regression (6 tests), `CodeExecutor` subprocess (6 tests), sandbox event emission (4 tests), `shell_command` tool backward compat + sandbox mode (5 tests), backward compat (5 tests including `on_failure` skip/halt).
- [docs] Updated `docs/architecture.md` with `src/dan/sandbox/` package. Updated plan `9-2-subprocess-sandbox.md` (tasks 1-7, 10 checked off; tasks 8-9, 11 remain for editor integration, builder DSL, and remaining docs).

## 2026-02-25 (Phase 6 — Plan 9-1: RAG / Knowledge Retrieval Node)
- [feat] Added `src/dan/rag/__init__.py`: `EmbeddingProvider` protocol, `EmbeddingResult` dataclass, `OpenAIEmbeddingProvider` (wraps `AsyncOpenAI`, default `text-embedding-3-small`, batch support), `LocalEmbeddingProvider` (wraps `sentence-transformers` via `asyncio.to_thread()`), `EmbeddingRegistry` (exact override → prefix match → default fallback, mirrors `ProviderRegistry` pattern).
- [feat] Added `src/dan/rag/stores/__init__.py`: `VectorStore` protocol (7 methods: `create_collection`, `delete_collection`, `list_collections`, `add`, `query`, `delete_by_ids`, `count`), `DocumentRecord`, `QueryResult`, `VectorStoreConfig` dataclasses, `VectorStoreFactory` routing on backend field with graceful fallback.
- [feat] Added `src/dan/rag/stores/memory.py`: `MemoryVectorStore` — pure-Python in-memory store using stdlib `math` for cosine similarity. O(n) linear scan, zero external deps, full protocol compliance, upsert support, metadata filtering.
- [feat] Added `src/dan/rag/stores/faiss_store.py`: `FAISSVectorStore` — wraps `faiss.IndexFlatIP` with L2-normalized vectors (cosine sim via inner product). On-disk persistence (`save`/`load` with JSON metadata sidecar), post-retrieval metadata filtering, batch add, index rebuild for deletes. Optional dep `faiss-cpu`.
- [feat] Added `src/dan/rag/stores/chroma_store.py`: `ChromaVectorStore` — wraps `chromadb.PersistentClient` (or in-memory `Client`). Native metadata filtering via `where` clause, collection management, automatic embedding bypass when pre-computed. Optional dep `chromadb`.
- [feat] Added `src/dan/executors/rag.py`: `RAGExecutor` implementing `NodeExecutor` protocol. Flow: render query template → resolve embedding provider → embed query → query vector store → apply similarity threshold → format chunks → return. Emits `RETRIEVAL_STARTED`/`RETRIEVAL_COMPLETED` events with collection, query preview, chunk count, latency, top score. Module-level store cache keyed by backend+directory+collection.
- [feat] Added `src/dan/rag/indexer.py`: `Indexer` class — `create_index` (chunk documents via `text_chunk` tool logic, batch embed, store), `delete_index`, `list_indices`, `add_documents`, `get_index_stats`. Configurable batch size (default 100), word/character chunking modes.
- [test] Added `tests/test_engine/test_rag.py` (61 tests: 48 passed, 13 skipped for optional deps): `EmbeddingProvider` unit tests (mock provider, batch, determinism, error, protocol), `EmbeddingRegistry` tests (default, override, prefix, error, listing), `MemoryVectorStore` tests (16: CRUD, query ordering, top-k, filters, upsert, cosine sim correctness, protocol), `FAISSVectorStore` tests (7: skip if faiss-cpu missing — CRUD, metadata filtering, persistence, protocol), `ChromaVectorStore` tests (6: skip if chromadb missing — CRUD, where filter, protocol), `VectorStoreFactory` tests, `RAGExecutor` integration tests (7: end-to-end query, threshold filtering, error paths, template rendering, metadata exclusion, registry resolution), `Indexer` tests (9: chunking, empty, delete, add, stats, batching, word mode, metadata), builder/decompiler round-trip tests (2: compile/decompile/recompile).
- [fix] Updated `tests/test_validation/test_graph_validation.py` `test_builtins_registered` count from 12 to 14 (added `rag_operator`, `validator`).
- [fix] Updated `tests/test_builder/test_compiler.py` `test_all_node_types_covered` expected set to include `rag_operator` and `validator`.
- [docs] Updated `docs/architecture.md`: added `src/dan/rag/` package tree (6 files), `src/dan/executors/rag.py`, updated executor count to 13, test count to 780.
- [docs] Updated `docs/plans/9-1-rag-knowledge-retrieval.md`: checked off tasks 1-7, 9, 11 (embedding, stores, executor, indexer, builder, tests). Remaining: server endpoints (7-5/7-6), editor integration (8), migration/parity (10), some docs (12-2/12-3).
- [docs] Updated `docs/todo.md`: marked 9-1 as complete (backend); noted follow-up for server/editor.

## 2026-02-25 (Phase 6 — follow-up: editor integration, server endpoints, builder helper)
- [feat] **Editor TypeScript integration** for `rag_operator` and `validator` node types: `RagOperator`/`ValidatorNode` interfaces in `graph.ts`, added to `DanNode` union, `NODE_TYPE_CATALOG` (operator/control categories), `NODE_DESCRIPTIONS` with port info, SVG icons in `nodeIcons.tsx`, `TYPE_COLORS` in `DanNode.tsx` (purple/emerald), `createDefaultNode` factory cases in `graphAdapter.ts`, dedicated `RAGConfigSection` (collection, top_k, query_template, store backend, embedding_model, threshold) and `ValidatorConfigSection` (rule list editor, on_failure select, strict_mode toggle) in `ConfigPanel.tsx`.
- [feat] **RAG collection CRUD endpoints** on FastAPI server: `GET /api/rag/collections`, `POST /api/rag/collections` (create with documents + chunking), `GET /api/rag/collections/{name}/stats`, `POST /api/rag/collections/{name}/documents` (add docs), `DELETE /api/rag/collections/{name}`. Lazy-initialized `Indexer` backed by `EngineConfig` embedding settings and configurable vector store backend (`DAN_RAG_STORE_BACKEND`, `DAN_RAG_PERSIST_DIR` env vars).
- [feat] **`wf.validated_composite()` builder helper**: context manager wrapping `composite()` that auto-generates entry and/or exit `ValidatorNode` nodes from JSON Schema (auto-derives `required_keys` + `schema_conformance` rules). Wires entry validator `valid` port → composite `input`, composite `result` → exit validator `data` port. Stores schemas on `external_input_schema`/`external_output_schema` for runtime `insert_boundary_validators` compatibility.
- [feat] **"Add Boundary Validators" context menu action**: server endpoint `POST /api/graphs/{graph_id}/nodes/{node_id}/add-boundary-validators` (calls `insert_boundary_validators`, persists updated graph). Editor `api.ts` client function, `useGraphStore.addBoundaryValidators` action (save → call → reload cycle), `ContextMenu.tsx` entry shown for composite/while_loop/for_each nodes with external schemas.

## 2026-02-25 (Phase 7 — conversational planning review refinements)
- [docs] Refined `docs/plans/10-chatbox-nl-workflow.md` with explicit trust and execution decisions: server-authoritative graph context, optimistic concurrency guard for mutation plans, WebSocket-only chat streaming, and session-scoped rollback policy.
- [docs] Updated `docs/plans/10-1-chat-panel-backend.md` to remove SSE ambiguity, require server-loaded graph summaries (`workflow_id` source of truth), and add markdown sanitization requirements for assistant-rendered content.
- [docs] Updated `docs/plans/10-2-mention-co-navigation.md` so mention expansion is resolved server-side from authoritative graph/workflow state rather than client-expanded context.
- [docs] Updated `docs/plans/10-3-nl-graph-mutation.md` with `base_graph_revision`/`base_graph_hash` plan preconditions, stale-plan rejection, and transactional-by-default apply semantics (partial apply only explicit user opt-in).
- [docs] Updated `docs/plans/10-4-graph-diff-confirmation.md` to define conversation rollback as active-session-only with in-memory history cursors and graceful disable behavior after reload.
- [docs] Refocused `docs/plans/10-5-history-execution.md` to chat persistence/session metadata, and split run-from-chat backend/runtime complexity into a new detailed sub-plan `docs/plans/10-6-scoped-run-from-chat.md`.
- [docs] Updated `docs/todo.md` to include `10-6` under Phase 7 and resolved top-level numbering collision by renaming the remaining Phase 7 umbrella item to `10-R`.

## 2026-02-25 (Phase 7 — Plan 10-2: `@` Mention & Co-Navigation)
- [feat] Added `editor/src/lib/mentionParser.ts`: mention serialization/parsing utilities — `serializeMention`, `parseMentions` (regex-based segment splitter), `findMentionQuery` (detect `@` trigger at cursor), `insertMention` (replace `@query` with `@[name](type:id)`), `navigateToMention` (co-navigation dispatcher: node→`setSelectedNode`, subgraph→`drillIn`, workflow→no-op), `mentionTypeColor` (Tailwind classes by mention type).
- [feat] Added `editor/src/components/MentionAutocomplete.tsx`: floating autocomplete dropdown for `@` mentions. Three sections (Nodes, Workflows, Sub-graphs) populated from Zustand store. Case-insensitive substring filtering with bold match highlight. Keyboard navigation (ArrowUp/Down, Enter, Escape). Node type icons from `nodeIcons.tsx`, type badge pills, folder/layers icons for workflows/sub-graphs. Fixed positioning with viewport flip-up logic, max 300px height scroll.
- [feat] Updated `editor/src/components/ChatPanel.tsx`: integrated mention system — `checkMention` detects `@` trigger on input/keyup/click, `handleMentionSelect` inserts serialized mention and repositions cursor, `dismissMention` hides dropdown. Textarea `onKeyDown` suppresses Enter/Arrow when autocomplete is active. `MentionAutocomplete` rendered inside input container.
- [feat] Updated `editor/src/components/ChatMessage.tsx`: mention chip rendering in message bubbles — pre-processes `@[name](type:id)` tokens before HTML escaping (sentinel-swap technique), renders as inline `<button>` pills with colored backgrounds (blue/green/amber by type). Click delegation via `data-mention-*` attributes dispatches to `navigateToMention` for canvas co-navigation. Both user and assistant messages support mention chips.

## 2026-02-25 (Phase 7 — Plan 10-3: LLM Function-Calling for Graph Mutations)
- [feat] Extended `CompletionResult` with `tool_calls: list[dict] | None` field (`providers/__init__.py`). OpenAI provider (`openai_provider.py`) captures `tool_calls` from response messages (id, type, function name/arguments).
- [feat] Added `MUTATION_TOOL_SCHEMA` constant in `chat_manager.py`: OpenAI function-calling tool definition for `plan_graph_mutations` covering all 6 operation types (add_node, remove_node, edit_node, add_edge, remove_edge, set_position).
- [feat] Added `ChatMutationEvent` stream event: `message_id`, `content` (reasoning text), `mutation_plan`, `dry_run_result`, `token_usage`, `graph_revision`, `revision_mismatch`. Updated `ChatStreamEvent` union to include it.
- [feat] Updated system prompt: instructs LLM to use `plan_graph_mutations` tool for modification requests, plain text for questions/explanations.
- [feat] Added `ChatManager.send_message_with_tools()`: non-streaming `complete()` call with `tools=[MUTATION_TOOL_SCHEMA]`, extracts mutation plans from native tool_calls or text JSON fallback, runs `GraphMutator.dry_run()`, yields `ChatMutationEvent` and returns. Graceful fallback via `_stream_with_json_fallback()` when provider doesn't support tools kwarg.
- [feat] Added `_try_parse_mutation_json()` module-level helper: regex-based extraction of mutation plans from markdown JSON code blocks or raw JSON text.
- [feat] Original `send_message()` preserved as text-only streaming path (no function calling).
- [feat] Updated `app.py` `/api/chat/message` endpoint: routes to `send_message_with_tools` when graph exists, falls back to `send_message` for missing graphs.
- [fix] Relaxed `use_tools` guard in `/api/chat/message`: previously required `len(nodes) > 0`, now triggers for any existing graph (even empty). Consistent with system prompt that instructs the LLM to build from scratch for empty workflows.
- [feat] Updated `editor/src/types/chat.ts`: added `chat_mutation` to `ChatStreamEvent.type` union, added `mutation_plan` and `dry_run_result` optional fields.
- [feat] Updated `editor/src/components/ChatPanel.tsx`: handles `chat_mutation` WebSocket events — stores combined `{ plan, dryRunResult }` on assistant message, sets `mutationStatus: "proposed"`. Added `GraphDiffPreview` integration: clicking "Proposed changes" badge computes diff between current graph and `dryRunResult.new_graph`, shows diff dialog. "Apply All" saves new graph via `updateGraph` + reloads store. "Reject" marks message status as `rejected`.
- [feat] Updated `editor/src/components/ChatMessage.tsx`: "Proposed changes" badge changed from `<span>` to clickable `<button>` with `onViewMutation` callback. Badge text reflects status (Applied/Rejected/Proposed changes).

## 2026-02-25 (Phase 7 — Plan 10-5: Chat Thread Management & Session Rollback)
- [feat] **Thread list sidebar** in `ChatPanel.tsx`: "Chat History" view with thread rows sorted by `updated_at` desc, message count badge, relative timestamp, delete button (confirm prompt), empty state ("No conversations yet"), and "New Chat" button. Active chat header has back arrow, inline-editable title, token count.
- [feat] **Thread persistence via API**: auto-create thread on first message (with auto-title from content), save thread messages via PUT after each assistant response completion, save current thread on back-to-list and workflow switch. Uses `listChatThreads`, `getChatThread`, `createChatThread`, `updateChatThread`, `deleteChatThread` API functions.
- [feat] **Auto-restore on load**: when chat panel opens with a workflow, fetches threads and auto-loads the most recent one. On workflow tab switch, saves current thread for old workflow and loads threads for new workflow.
- [feat] **Session-scoped rollback markers**: `sessionMarkers` state tracks `{ historyCursor }` per message ID. `_recordMutationMarker(messageId)` records current undo stack position. "Revert to here" button walks undo stack back to marker position. After reload (markers empty), shows disabled "Revert" with tooltip "Available in current session only". Markers cleared on thread/workflow switch.
- [feat] **ChatMessage mutation badges**: full status-aware rendering — proposed (amber chip + chevron), applied (green chip + check), rejected (gray chip), reverted (gray chip + strikethrough). Replaces previous simple `mutationPlan` presence check.
- [feat] **ChatMessage run reference blocks**: `runRef` rendered as inline status blocks — running (blue spinner + scope), completed (green check + "View logs"), failed (red X + "View logs").
- [feat] Added `updateChatThread` API function in `api.ts` (PUT `/chats/{workflowId}/{threadId}` with title and/or messages).
- [feat] Extended backend PUT `/api/chats/{workflow_id}/{thread_id}` to accept `messages` array for full thread saves (previously title-only). Uses `StoreChatMessage.model_validate` for each message, updates `updated_at`, persists via `ChatStore.save_thread`.
- [feat] Backend/frontend message format conversion helpers (`toBackendMessage`/`fromBackendMessage`) handle camelCase↔snake_case mapping and timestamp number↔ISO string conversion.
- [fix] `ChatMessage.tsx` mutation badge now triggers on `mutationPlan` presence (not just `mutationStatus`), defaulting to "proposed" when plan exists without explicit status.

## 2026-02-25
- [feat] 10-1: Chat panel & backend — ChatPanel.tsx, ChatMessage.tsx, ChatManager, graph-aware system prompt, WebSocket streaming, chat endpoints in app.py
- [feat] 10-2: @ mention system — MentionAutocomplete.tsx, mentionParser.ts, Cursor-style autocomplete, mention chips, click→canvas co-navigation
- [feat] 10-3: NL→Graph mutation engine — GraphMutator with 8 operation types, MutationPlan, LLM function-calling schema, dry-run mode, optimistic concurrency
- [feat] 10-4: Graph diff preview — graphDiff.ts computation, GraphDiffPreview.tsx modal with accept/reject/partial-accept, per-operation checkboxes
- [feat] 10-5: Chat history & session — ChatStore persistence, thread list UI, auto-restore, session-scoped rollback markers, mutation/run badges in messages
- [feat] 10-6: Scoped run execution — scoped_run.py, POST /api/runs/scoped, build_scoped_graph, /run commands, run event→chat block mapping
- [test] Added test suites: test_chat_manager.py, test_graph_mutator.py, test_chat_store.py

## 2026-02-25 (Phase 6 — review fixes: five critical bugs)
- [fix] **`validated_composite` rewiring**: The builder helper now returns a `_ValidatedCompositeRef` proxy. When used with `>>`, inbound edges route to the entry validator and outbound edges originate from the exit validator, ensuring data always flows *through* validators instead of bypassing them.
- [fix] **RAG CRUD `_get_indexer()` uses `EmbeddingRegistry`**: Extracted shared `build_embedding_registry(config)` function in `dan/rag/__init__.py`. Both the engine's `_build_embedding_registry` and the server's `_get_indexer` now use it, honouring local and API embedding provider configurations (Option B parity).
- [fix] **Boundary validator insertion is idempotent**: `insert_boundary_validators()` now checks if `{node_id}__entry_validator` or `{node_id}__exit_validator` already exist and returns the graph unchanged, preventing duplicate node IDs and broken graphs on repeated calls.
- [fix] **Editor `addBoundaryValidators` guards on save**: The Zustand action now checks `saveGraph()` return value and aborts if the save failed, preventing API calls against stale server state.
- [fix] **Validator rule JSON editor UX**: Replaced direct `JSON.parse` on every keystroke with a `RuleConfigEditor` component using local `useState` for the textarea. Parsing and validation happen on blur, with a red border + error message for invalid JSON. Intermediate edits are preserved.
- [refactor] Consolidated `_create_embedding_provider` + `_build_embedding_registry` from `engine/scheduler.py` into `dan/rag/__init__.py` as `_create_embedding_provider` + `build_embedding_registry`. Engine delegates to the shared helper. Test monkeypatches updated.
- [test] 866 passed, 15 skipped.

## 2026-02-25 (Phase 6 — review follow-ups: custom ports, tests)
- [fix] **`validated_composite` custom ports**: Internal validator wiring now derives the composite's first input/output port from `input_ports`/`output_ports` or `input_mappings`/`output_mappings` instead of hardcoding `"input"`/`"result"`. Composites with custom ports (e.g. `payload`, `answer`) are correctly wired.
- [fix] **`_ValidatedCompositeRef` delegation**: Added `__getattr__` so builder methods (llm, code, etc.) inside the block delegate to the sub-workflow. Users can write `with wf.validated_composite(...) as block: block.llm(...)`.
- [test] **`test_validated_composite_flows_through_validators`**: Asserts `a >> block >> b` produces edges through entry/exit validators.
- [test] **`test_validated_composite_with_custom_ports`**: Asserts custom `input_ports`/`output_ports` are used for validator wiring.
- [test] **`test_insert_boundary_validators_idempotent`**: Asserts repeated calls return the same graph; no duplicate validators.
- [test] 870 passed, 15 skipped.

## 2026-02-25 (Phase 7 — review bug-fixes)
- [fix] **Chat mutation events dropped by UI**: `ChatPanel.tsx` `ws.onmessage` now handles `chat_mutation` stream events — updates the assistant message with `mutationPlan`, `mutationStatus: "proposed"`, and `mutationId`, then saves the thread and stops streaming.
- [fix] **`/run` chat commands not wired**: `app.py::chat_message` now calls `parse_run_command()` on each incoming message; `/run`, `/run-node`, `/run-subgraph` commands dispatch through `build_scoped_graph` + `RunManager.start_run`, returning `run_started`/`run_error` responses that the frontend handles inline.
- [fix] **Token usage key mismatch**: Added `_normalize_usage()` in `chat_manager.py` — maps provider keys (`prompt_tokens`/`completion_tokens`) to frontend keys (`prompt`/`completion`). Applied to all three emission paths (stream, tool-call, JSON-fallback).
- [fix] **`_chat_streams` memory leak**: Changed `_chat_streams` from `dict[str, Queue]` to `dict[str, (Queue, float)]` with monotonic timestamps. `_reap_stale_chat_streams()` evicts entries older than 120s on each POST, guarding against leaked queues when clients never connect.
- [fix] **Markdown link XSS via unsafe URL schemes**: `ChatMessage.tsx` `applyInlineMarkdown` now rejects `javascript:` hrefs and only allows `https:`, `http:`, `mailto:`, and `#` schemes; added `rel="noreferrer"`.
- [fix] **Mention trigger fires mid-word**: `mentionParser.ts::findMentionQuery` now requires `@` to be preceded by whitespace or at position 0, preventing false triggers like `email@foo`.
- [fix] **Workflow mention click was a no-op**: `navigateToMention` now calls `store.openTab(mention.id)` for `workflow` mention type.

## 2026-02-25 (Phase 7 — 10-7 Apply Mutation Flow)
- [feat] **Sub-plan 10-7**: Wire chat mutation proposal → diff preview → apply pipeline. Users can now apply LLM-generated graph changes.
- [feat] **Backend**: `POST /api/graphs/{graph_id}/apply-mutation` — accepts `MutationPlan`, calls `GraphMutator.apply()`, persists via GraphStore, returns `{ success, new_graph?, errors?, stale_plan? }`. Concurrency check via `base_graph_revision`.
- [feat] **Frontend**: `api.applyMutation()` client; "Proposed changes" badge in ChatMessage is clickable → opens GraphDiffPreview modal with `computeGraphDiff(currentGraph, dry_run_result.new_graph)`.
- [feat] **Apply flow**: Apply All → `applyMutation` API → `pushSnapshot` + `loadGraph` → update message `mutationStatus: "applied"` → record session marker for "Revert to here" → persist thread.
- [feat] **Reject flow**: Close modal, set `mutationStatus: "rejected"`, persist thread.
- [feat] **ChatMessage**: Added `dryRunResult` to message model and backend conversion for diff computation.
- [test] `test_apply_mutation_success`, `test_apply_mutation_nonexistent` in test_api.py.

## 2026-02-25 (Phase 7 — 10-7 review fixes)
- [fix] **Apply Selected misleading**: GraphDiffPreview now accepts `allowPartialApply` (default true). ChatPanel passes `allowPartialApply={false}` so "Apply Selected" is hidden until partial apply is implemented.
- [fix] **Unrelated gate migration default**: Reverted `DAN_GATE_MIGRATION_ENABLED` default from `"true"` to `""` (was accidentally changed).
- [fix] **Loading state during apply**: Added `isApplying` state; GraphDiffPreview accepts `disabled` prop. Apply/Reject buttons disabled while API call in flight.
- [fix] **Modal closes on error**: On apply failure, modal stays open so user can retry or close manually. `setPreviewingMessage(null)` only on success.

## 2026-02-25 (Phase 7 — 10-7 second review fixes)
- [fix] **Apply error hidden behind modal**: Added `applyError` state and `applyError` prop to GraphDiffPreview. Failed apply shows error banner inside modal with "Try again" button.
- [fix] **Close button during apply**: GraphDiffPreview Close (X) button now disabled when `disabled` is true.
- [fix] **Stale isApplying guard**: Use `applyingRef` for the guard to avoid stale closure; `isApplying` state remains for UI disable.

## 2026-02-25 (Phase 7 — 10-7 third review fixes)
- [fix] **dry_run_result not persisted**: Added `dry_run_result` to `ChatMessage` in `chat_store.py` so mutation preview survives thread reload.
- [fix] **No fallback when preview unavailable**: When user clicks "Proposed changes" but `dryRunResult.new_graph` is missing (old thread or failed persistence), show fallback modal with "Preview unavailable" and Close button instead of leaving user stuck.
- [fix] **apply-mutation gate migration parity**: Applied gate migration to graph before mutation in `apply_mutation` endpoint when `DAN_GATE_MIGRATION_ENABLED` is set, matching `get_graph` behavior.

## 2026-02-25 (Phase 7 — 10-7 fourth review fixes)
- [fix] **Run error message wrong field**: Backend `ScopedRunError` uses `message`, not `detail`. Frontend now reads `resBody.error?.message` for run_error display.
- [fix] **Run commands not persisted**: `/run` and `/run-node` responses (run_started/run_error) now save the assistant message to the thread via `updateChatThread`.
- [fix] **TypeScript cast**: `danGraph as Record<string, unknown>` → `danGraph as unknown as Record<string, unknown>` to satisfy strict cast.

## 2026-02-25 (Phase 4 — Core Hardening — implementation complete)
- [feat] **7-1 Runtime Reliability:** Added `RetryPolicy` Pydantic model (`max_retries`, `backoff`, `backoff_max`, `fallback_model`, `on_failure`) on `NodeBase`. Replaced hardcoded retry in `LLMExecutor` with configurable policy. Added retry loop to `ToolExecutor` (transient exceptions: `TimeoutError`, `ConnectionError`, `OSError`). `CodeExecutor` honors `on_failure` (skip/halt) without retry. Scheduler checks `metadata.halt` flag after each level, saves checkpoint and stops. `RETRY_ATTEMPTED` event type added. `EngineConfig.max_concurrency` for graph-wide semaphore. Frontend: `RetryPolicyEditor` in ConfigPanel, `RetryPolicy` TypeScript interface. 27 new tests.
- [feat] **7-2 Multi-Provider LLM Registry:** Created `src/dan/providers/` package with `LLMProvider` protocol, `CompletionResult`, `StreamChunk`, `ProviderConfig` dataclasses. Built-in providers: `OpenAIProvider` (wraps AsyncOpenAI), `AnthropicProvider` (wraps AsyncAnthropic, system prompt extraction), `GoogleProvider` (wraps google.generativeai). `ProviderRegistry` with 3-tier resolution (exact override → prefix pattern → default fallback). `EngineConfig` extended with `providers` dict and `model_provider_map`. `LLMExecutor` and `RouterExecutor` refactored to use provider dispatch. Static cost table (`costs.py`) covering 17 models. Server auto-scans `DAN_OPENAI_API_KEY`, `DAN_ANTHROPIC_API_KEY`, `DAN_GOOGLE_API_KEY`. Frontend: `LLMConfigSection` with basic/advanced pattern, model datalist, provider badges. 128 new tests (38 new provider tests).
- [feat] **7-3 Built-in Tool Library:** Created `src/dan/tools/` package with 11 tools: `file_read`, `file_write`, `list_directory` (file category, workspace-root sandboxed), `web_search` (DuckDuckGo, optional dep), `web_fetch`, `http_request` (web category), `shell_command` (system, allowlist-enforced), `pdf_read` (document, optional dep), `text_chunk` (document), `json_extract`, `regex_match` (utility). Auto-discovery via `get_all_tools()` with graceful `ImportError` handling. `ToolRegistry.register_builtin_tools()` method. `httpx` promoted to main dependency. Optional deps: `pypdf`, `duckduckgo-search`. 60 new tests.
- [feat] **7-4 Templates + Observability:** 5 workflow templates: `simple_chain` (LLM→LLM→Code), `fan_out_fan_in` (ForEach+Reduce), `review_revise` (GateNode while-loop), `rag_qa` (tool-based RAG), `react_agent` (ReAct loop with tools). Frontend: per-node token badges ("1.2k tok"), cost badges ("$0.03") on `DanNode`, total cost in `RunSummaryBar`, per-node usage in LogPanel headers. `nodeUsage` and `nodeCosts` state in Zustand store. 37 new tests.
- [infra] Test suite: 341 → 503 tests (162 new), all passing. `pyproject.toml`: added optional dependency groups (`pdf`, `search`, `anthropic`, `google`, `all-providers`, `all-tools`, `all`).

## 2026-02-25 (Phase 4 plans — review fixes)
- [docs] **7-2 provider routing redesign:** replaced prefix-only routing with 3-tier resolution (exact model→provider map → prefix match → `default` fallback). Added `ProviderConfig` dataclass, named provider registration (`EngineConfig.providers`), and `model_provider_map` override. Current vectorengine `claude-sonnet-4-6` setup preserved via `default` provider. Fixes backward-compat break where `claude-*` prefix would route to Anthropic native SDK.
- [docs] **7-2 RouterExecutor:** added task 5-6 to refactor `RouterExecutor` (currently creates its own `AsyncOpenAI` client directly, bypassing provider abstraction). Added test 8-6 for Router dispatch and test 8-10 for model override map.
- [docs] **7-2 basic + advanced UI:** redesigned config panel from model-only dropdown to basic (model, temperature, system prompt) + collapsible advanced (base_url override, api_key override, max_tokens, extensible extra kwargs). Keeps common case clean, power users can customize per-node.
- [docs] **7-1 on_failure=halt semantics (decided):** halt stops scheduling new topological levels, lets already-running parallel nodes finish, writes checkpoint at halt point, returns `RunResult(success=False)`. Added tasks 2-4, 3-5, scheduler halt-flag check, and 3 new integration tests (7-4, 7-8, 7-9).
- [docs] **7-1 retry_policy naming:** renamed `backoff_base` → `backoff` to match architecture.md contract. `backoff_max` is additive (caps exponential growth). Updated architecture.md.
- [docs] **7-1 retry_attempted event prerequisite:** added task 1-5 to register `RETRY_ATTEMPTED` in `EventType` enum before executors can emit it (emit_event validates via enum).
- [docs] **7-1 CodeExecutor retry scoped:** replaced timeout-retry plan with explicit "no retry loop for code" decision — `exec()` is synchronous/deterministic with no preemption. `on_failure` (skip/halt) still honored. Retry becomes meaningful when Phase 6 adds subprocess sandboxing.
- [docs] **7-3 workspace-root sandboxing:** added shared `_workspace_root()` utility (task 1-5) used by all file tools (`file_read`, `file_write`, `list_directory`). Rejects `../` escapes, symlink escapes, absolute paths outside root. Configurable via `DAN_WORKSPACE_ROOT`.
- [docs] **7-3 graceful SDK import:** `get_all_tools()` catches `ImportError` per tool module (task 1-4 updated). Missing optional SDKs log warning and skip tool, not crash server.
- [docs] Updated `7-core-hardening.md` shared decisions with all resolved items (halt semantics, routing safety, code retry, basic+advanced UI, workspace sandboxing).
- [docs] Updated `architecture.md` — expanded retry_policy description with `backoff_max` field and halt semantics.

## 2026-02-25 (Phase 4 core hardening — detailed planning)
- [docs] Created `docs/plans/7-core-hardening.md` — parent plan for Phase 4 with 4 sub-plans, dependency graph, shared decisions, and execution order recommendation
- [docs] Created `docs/plans/7-1-runtime-reliability.md` — `RetryPolicy` model on `NodeBase`, wire retry into LLM/Tool/Code executors, fallback model, `on_failure` modes, `max_concurrency` audit, Config panel UI (8 task groups, 27 sub-tasks)
- [docs] Created `docs/plans/7-2-multi-provider-llm.md` — `LLMProvider` protocol, OpenAI/Anthropic/Google built-in providers, `ProviderRegistry` with prefix routing, per-provider key management, `LLMExecutor` refactor, static cost table, model picker UI (10 task groups, 30 sub-tasks)
- [docs] Created `docs/plans/7-3-built-in-tools.md` — `dan.tools` package with ~10 tools (file read/write, list_directory, web_search, web_fetch, http_request, shell_command, pdf_read, text_chunk, json_extract, regex_match), `TOOL_METADATA` schema, auto-registration, ACI quality inline (11 task groups, 32 sub-tasks)
- [docs] Created `docs/plans/7-4-templates-observability.md` — 5 workflow templates (simple chain, fan-out/fan-in, review-revise, RAG Q&A, ReAct agent), per-node token/cost badges, `RunSummaryBar` cost extension, LogPanel enhancements (7 task groups, 22 sub-tasks)
- [docs] Updated `docs/todo.md` — replaced flat Phase 4 bullet list with linked sub-plan hierarchy (7 parent + 7-1 through 7-4)
- [docs] Noted `ForEachNode.parallelism` + semaphore already implements per-node concurrency control — `max_concurrency` todo item is largely done, 7-1 audits whether a graph-wide ceiling is needed

## 2026-02-25 (roadmap tightening — dependency fixes)
- [docs] Phase 4 `dan.tools`: clarified HTTP request is a built-in tool (no separate `HTTPOperator` node type needed), expanded PDF/paper ingestion description for tool-based local RAG
- [docs] Phase 4 templates: clarified RAG Q&A template uses tool-based RAG via `dan.tools` (local PDF reading), no dependency on Phase 6 `RAGOperator`
- [docs] Phase 6: removed `HTTPOperator` (covered by `dan.tools.http_request` in Phase 4), clarified `RAGOperator` is upgrade from tool-based approach
- [docs] Updated `development-plan.md` roadmap table to match Phase 6 scope change

## 2026-02-25 (roadmap reorganization)
- [docs] Refactored `docs/todo.md` — replaced old Phases 4-6 (Memory, Markdown, Marketplace) with new Phases 4-10 based on "furnish, don't renovate" principle: (4) Core Hardening, (5) Markdown Agent Format, (6) Extended Capabilities, (7) Author & Distribute, (8) Observe & Recover, (9) Application Layer, (10) Deep Systems
- [docs] Redistributed all backlog items into appropriate phases; backlog now contains only aspirational/exploratory items (coding assistant PoC, science-cursor rebuild, EvoAgentX survey, cross-graph copy)
- [docs] Marked Phase 3.75 parent item as completed (all 11 sub-tasks were already `[x]`)
- [docs] Memory & context scoping moved from Phase 4 to Phase 10 — build when real workflows demand it, not speculatively
- [docs] Markdown agent format moved up from old Phase 5 to new Phase 5 (immediately after core hardening)
- [docs] Added new phases: (6) RAG/HTTP/sandbox, (7) CLI/publish-as-API/MCP/PyPI, (8) run history/audit/checkpoints, (9) agent teams/messaging/user system
- [docs] Updated `docs/development-plan.md` roadmap table — marked Phases 3.5 and 3.75 as Done with test counts, added Phases 4-10 with new descriptions
- [docs] Updated `docs/development-plan.md` recommendation section — struck through completed Phase 3 milestone, added forward-looking summary of Phases 4-10

## 2026-02-24 (6-9 multi-tab workflow sessions implementation)
- [feat] `TabInfo` and `TabSnapshot` types in `useGraphStore.ts` — per-tab state model covering all workflow-scoped slices (graph, nodes, edges, selection, layers, validation, run state, logs, history, iterations, streaming, human input)
- [feat] `_snapshotActiveTab()` / `_restoreTab()` internal helpers — deep-clone all per-tab state into/from a `TabSnapshot` using `structuredClone` and `Set` copies
- [feat] `_persistTabState()` — writes `{ tabs, activeTabId, runs }` to `sessionStorage` under `dan_open_tabs` key (lightweight metadata only, no nodes/edges/logs)
- [feat] `openTab(graphId)` — deduplicates by `graphId` (switches to existing tab), snapshots active tab to cache, creates new tab with fresh state, loads graph via API
- [feat] `switchTab(tabId)` — snapshots active tab, closes active WebSocket, restores target from cache (or loads from API), reconnects WS if target has an active run (`running`/`pending`)
- [feat] `closeTab(tabId)` — blocks closing last tab with toast warning, switches to neighbor before removing active tab, cleans `tabCache`
- [feat] Cross-tab event guard in `handleRunEvent` — ignores WS events whose `run_id` doesn't match active tab's `runId`, preventing log/status contamination during fast tab switches
- [feat] `restoreTabs()` startup action — reads `dan_open_tabs` from `sessionStorage`, rebuilds tab list, loads active tab's graph, reconnects active run via API + WS catch-up
- [feat] Legacy migration in `restoreTabs()` — detects old `dan_active_run` key, converts to tab-aware schema, clears legacy key
- [feat] `startRun` / `resumeRun` / `handleRunEvent` (terminal events) call `_persistTabState()` for session persistence
- [feat] `loadGraphList` creates initial tab from `last_opened` when no tabs exist
- [feat] `createGraph` routes through `openTab` after creation; `deleteGraph` closes matching tab
- [feat] Created `editor/src/components/TabBar.tsx` — horizontal tab bar with graph name (truncated), run status badge (colored dot), close button (×), active tab indigo border styling, "+" button with dropdown picker of unopened graphs
- [feat] Updated `EditorToolbar.tsx` — replaced graph `<select>` dropdown with `<TabBar />` component, kept create/delete controls, import routes through `openTab`
- [feat] Updated `App.tsx` — startup calls `restoreTabs()` after `loadGraphList()` instead of `recoverActiveRun()`
- [infra] TypeScript compiles cleanly (`npx tsc --noEmit` — 0 errors)

## 2026-02-24 (6-10 cycle-aware scheduling + gate validation — phase 2)
- [feat] `PortDataStore.clear_node(node_id)` — removes all port data for a node, replacing raw `_data` dict manipulation in cycle iteration
- [fix] `_should_skip()` back-edge exemption — while-gate continue/loop ports no longer cause downstream cycle nodes to be skipped during initial pass or iterations; only forward gate branches (true/false/done) trigger skip logic
- [fix] Virtual input priority in `_execute_node()` — virtual inputs (from `_inject_inputs` and `_iterate_cycle`) now override stale data-edge values via direct assignment instead of `setdefault`, fixing cycle nodes receiving outdated non-cycle predecessor data
- [fix] `_iterate_cycle()` refactored to use `clear_node()` instead of raw `_data` access
- [feat] Multi-gate cycle validation in `_validate_gate_cycles()` — computes per-gate cycle regions via bidirectional reachability; rejects overlapping regions from two while-gates in the same cycle
- [test] `tests/test_engine/test_cycle_scheduling.py` — 14 tests: PortDataStore.clear_node (3), DAG fast-path (2), while-gate loop execution (2), max_iterations enforcement (1), if_else branch skip (2), gateless cycle rejection (1), gated cycle acceptance (1), if_else-mode cycle rejection (1), multi-gate cycle rejection (1)
- [fix] Updated `test_gate_scheduling.py::test_loop_executes_multiple_iterations` stop_at from 3→5 to match corrected cycle behavior where inc node now properly executes

## 2026-02-24 (6-10 cycle-aware scheduling + gate validation)
- [feat] `_topological_levels_with_backedges(graph)` — extended topo sort that identifies gate-controlled back-edges and computes cycle regions, enabling flat visible-loop scheduling without sub-graph containers
- [feat] Cycle-aware execution in `Engine._execute_with_cycles()` and `_iterate_cycle()` — re-executes cycle region nodes in bounded iterations when a gate(while) node outputs on its continue port; respects `max_iterations` guard
- [feat] Gate branch-port skip logic in `_should_skip()` — nodes downstream of an inactive gate branch are skipped (complements existing ControlEdge-based branching for legacy if_else)
- [feat] `_validate_gate_cycles()` in `validation/graph.py` — validates that gate-controlled cycles use while mode with valid iteration bounds; rejects if_else-mode gates in cycles
- [feat] `_check_data_cycles()` now exempts gate nodes alongside while_loop/for_each so gate-controlled back-edges don't trigger false cycle errors
- [feat] DAG fast-path preserved — when no back-edges exist, `_execute()` runs the original level-by-level scheduling with zero overhead
- [test] `tests/test_engine/test_gate_scheduling.py` — 15 tests covering gate if_else branching, while-loop iteration, max_iterations enforcement, iteration events, DAG fast-path, topo sort back-edge detection, cycle validation, and helper functions

## 2026-02-24 (import_workflow + equity research example)
- [feat] `wf.import_workflow(node_id, graph)` — Python builder method to embed a pre-built Graph as a composite node, enabling progressive workflow wrapping (build A, import into B, import B into C)
- [feat] `namespace_graph(graph, prefix)` — prefixes all internal IDs to avoid collisions when importing
- [feat] `derive_ports(graph)` — auto-derives composite input/output ports from entry/exit nodes, using `node_id::port_name` mapping format (matches editor's `graphAsCompositeNode()`)
- [feat] New file `src/dan/builder/importer.py` with import utilities
- [feat] `examples/equity_research.py` — 5-level progressive wrapping demo (Data Gatherer → Section Analyst → Report Orchestrator + Scenario Analysis + Multi-Ticker Comparison) using all 3 edge types and most node types
- [feat] Parallel imported nodes: Section Analyst imports `data_gatherer` graph twice (`dg_primary` + `dg_news`) running concurrently with no data dependency, merged before the draft-review loop — demonstrates reusing the same workflow as multiple parallel composite nodes
- [docs] Updated `docs/llm-api-guide.md` with `import_workflow` API, progressive wrapping pattern, and import map
- [docs] Updated `docs/architecture.md` with new `importer.py` file

## 2026-02-24 (6-8 patch-up implementation)
- [feat] Loop feedback arrows: `drillIn` injects synthetic dashed edges from exit-point output ports back to entry-point input ports (name-matched), with generic fallback arrow when names don't match; edges tagged `data.synthetic=true`
- [fix] Save-leak prevention: `saveGraph()` filters out `edge.data?.synthetic` edges before passing to `reactFlowToDanGraph()`, preventing phantom edges from persisting when saving while drilled into a loop body
- [feat] Smart port derivation: `derivePorts()` in `graphImporter.ts` now skips autonomous entry nodes (zero input ports), uses node-aware mapping format (`nodeId::portName`), reverses exit-node order so primary exit ports appear first
- [feat] Entry/exit validation: `graphAsCompositeNode()` validates that all namespaced entry/exit point IDs exist in the body graph before deriving ports
- [feat] Node-aware input routing: `CompositeExecutor` parses `nodeId::portName` mapping values into per-node `targeted_inputs`, forwarded through `run_subgraph` → `_run_subgraph` for precise per-entry-node injection (backward compatible with legacy flat mappings)
- [feat] Node-aware output routing: `CompositeExecutor` parses `nodeId::portName` mapping keys, extracting port names for lookup in body output
- [feat] Targeted injection in `_run_subgraph`: new optional `targeted_inputs` parameter injects values to specific entry-point nodes instead of broadcasting; `ExecutionContext.run_subgraph` signature updated to forward the parameter
- [fix] Existing mock test signatures updated for new `targeted_inputs` parameter in `test_composite_executor.py`
- [test] 9 new tests in `tests/test_engine/test_68_patchup.py`: node-aware input routing, legacy fallback, mixed mappings, duplicate port collision, node-aware output mapping, zero-input-port entries, partial input coverage, targeted injection
- [docs] Updated `docs/architecture.md` with feedback-arrow strategy, node-aware mapping format, autonomous-entry filtering, test count 265→274

## 2026-02-24 (6-8 patch-up plan)
- [docs] Created `docs/plans/6-8-patch-up.md` — targeted fixes: (1) virtual feedback arrows in loop drill-in views, (2) smart port derivation for workflow-as-node to skip autonomous entry nodes, (3) multi-entry composite run readiness, (4) synthetic edge save-leak prevention, (5) node-aware port mapping collisions
- [docs] Added 6-8 row to parent plan `6-phase-3.75-visual-editor-editing.md`, re-opened parent status to `in-progress`
- [docs] Added 6-8 entry to `docs/todo.md`

## 2026-02-24 (6-7 workflow-as-node implementation)
- [feat] Created `editor/src/lib/graphImporter.ts` — `graphAsCompositeNode()` factory with recursive ID namespacing (`wf_{graphId}__` prefix), deterministic port derivation from entry/exit points, collision-safe naming, and input/output mapping generation
- [feat] Added "Saved Workflows" category in `NodePalette.tsx` — lists all saved graphs (excluding current), drag/drop with `workflow:{graphId}` payload, click-to-insert, emerald styling, search filtering
- [feat] Extended `GraphCanvas.tsx` `onDrop` to handle `workflow:` prefix and delegate to `addGraphAsNode` store action
- [feat] Added `addGraphAsNode(graphId, position)` async action in `useGraphStore.ts` — fetches graph via API, validates payload, runs importer factory, merges sub_graphs, adds CompositeNode atomically with undo snapshot, self-import guard, error toast on failure
- [docs] Marked `6-7-workflow-as-node.md` and parent plan as completed

## 2026-02-24 (6-6 execution UX implementation)
- [feat] Added `ITERATION_STARTED`, `ITERATION_COMPLETED`, `HUMAN_INPUT_NEEDED` event types in `events.py`
- [feat] `WhileLoopExecutor` emits iteration_started/completed events with iteration index, max_iterations, condition per loop turn
- [feat] `ForEachExecutor` emits iteration_started/completed events per item with index and total count
- [feat] `HumanInTheLoopExecutor` generates request_id, emits `human_input_needed` event, passes structured metadata dict to callback
- [feat] Updated `human_input_callback` signature from `Callable[[str], ...]` to `Callable[[dict], ...]` in `executor.py` and `scheduler.py`
- [feat] `LLMExecutor._call_llm` now streams by default (`stream=True`), emits `intermediate_text` every 5 chunks with delta/accumulated text, falls back to non-streaming on failure
- [feat] `RunManager` — added pending-input registry (`_pending_human_inputs`), `submit_human_input()`, `get_pending_human_inputs()`, `_make_human_input_callback()`, wired callback into engine creation
- [feat] `RunManager._event_callback` — coalesces `intermediate_text` events (replaces in-place, preserves `done` events), increased `_max_event_buffer` to 2000
- [feat] `RunManager.subscribe` — catch-up includes `pending_human_inputs` for reconnect-safe dialog recovery
- [feat] Added `POST /api/runs/{run_id}/human-input` endpoint in `app.py` — validates request_id, delegates to `submit_human_input`
- [feat] Added `submitHumanInput` API client in `api.ts`
- [feat] Added `nodeIterations`, `streamingOutputs`, `pendingHumanInput` state slices in `useGraphStore.ts` with event handlers in `handleRunEvent`
- [feat] `DanNode.tsx` — loop indicator icon (↻) in header for while_loop/for_each nodes, condition badge for while_loop, live iteration counter badge
- [feat] `LogPanel.tsx` — added `intermediate_text` icon and data preview, iteration/human-input event colors
- [feat] `OutputPreview.tsx` — shows streaming text with pulsing cursor for running nodes, swaps to finalized output on completion
- [feat] Created `HumanInputDialog.tsx` — modal popup with prompt display, textarea response, Cmd+Enter submit, dismiss, error handling
- [feat] Mounted `HumanInputDialog` in `App.tsx`
- [test] Added 10 new tests: WhileLoop/ForEach iteration events, human-input events, streaming coalescing, event buffer sizing, event type existence (265 total)
- [docs] Marked `6-6-execution-ux.md` as completed; Phase 3.75 parent plan fully completed

## 2026-02-24 (6-7 workflow-as-node planning)
- [docs] Created `docs/plans/6-7-workflow-as-node.md` — new Phase 3.75 sub-plan for wrapping saved workflows as reusable composite nodes via palette/canvas insertion
- [docs] Reviewed/tightened `docs/plans/6-6-execution-ux.md` and `docs/plans/6-7-workflow-as-node.md` — added reconnect-safe human-input catch-up requirement, explicit stream coalescing/throttling task, deterministic port-collision policy, and invalid-import negative-path coverage
- [docs] Updated `docs/plans/6-phase-3.75-visual-editor-editing.md` — added 6-7 sub-plan row, expanded parent goal, and updated sequencing notes
- [docs] Updated `docs/todo.md` — added `6-7-workflow-as-node` under Phase 3.75 tracking

## 2026-02-24 (paper-writing LaTeX workflow hardening)
- [fix] Updated `compile_latex` in both `src/dan/server/app.py` and `examples/paper_writing.py` to auto-bootstrap `informs3.cls` into `output/` (copy from project root if present, otherwise fetch from a public template mirror), reducing first-run template failures
- [fix] Added LaTeX compatibility normalization in `compile_latex`: enforce `\\usepackage{hyperref}`, add `\\providecommand{\\newblock}{}`, and rewrite `\\bibliographystyle{informs2014}` to `\\bibliographystyle{plainnat}` before compilation
- [fix] Added citation/key safety in `compile_latex`: parse cited BibTeX keys from TeX, detect missing entries, and auto-append placeholder BibTeX entries so missing references no longer break/bottleneck compile runs
- [fix] Updated `check_latex_deps` semantics to treat `informs3.cls` as auto-bootstrap-capable (non-blocking warning) instead of hard-failing on missing local template files
- [fix] Updated `examples/paper_writing.py` assembly template and regenerated `graphs/paper_writing.json` so the workflow defaults include `hyperref`, `\\newblock` compatibility, `plainnat`, and review loop `max_iterations=3`
- [test] Verified with targeted suites: `tests/test_examples/test_paper_writing_e2e.py` (8 passed) and `tests/test_server` (27 passed)

## 2026-02-24 (6-6 execution UX planning)
- [docs] Created `docs/plans/6-6-execution-ux.md` — new Phase 3.75 sub-plan for loop visualization, streaming LLM output visibility, and human-in-the-loop popup/submit flow
- [docs] Reviewed and tightened `docs/plans/6-6-execution-ux.md` — added explicit callback-contract update tasks (`ExecutionContext`/`Engine`), request-id concurrency safety for human input, and in-place streaming log update strategy to prevent log/event explosion
- [docs] Added additional 6-6 safeguards — catch-up-safe streaming buffer policy in `run_manager.py`, non-deterministic `for_each` progress handling (`completed/total`), and fallback behavior for ambiguous loop drill-in feedback arrows
- [docs] Updated `docs/plans/6-phase-3.75-visual-editor-editing.md` — added 6-6 sub-plan row, expanded goal, and set status to `in-progress`
- [docs] Updated `docs/todo.md` — added `6-6-execution-ux` under Phase 3.75 and re-opened parent 6 plan as pending

## 2026-02-24 (6-5 InputNode, graph I/O, command palette, sub-graph grouping)
- [feat] Added `InputVariable` and `InputNode` Pydantic models in `src/dan/models/control_flow.py` — typed variables (string/number/boolean) with defaults
- [feat] Added `InputNode` to `Node` discriminated union in `graph.py`, registered in `registry.py`
- [feat] Created `InputExecutor` in `src/dan/executors/input.py` — pass-through executor that reads variable values from inputs, falls back to defaults
- [feat] Registered `InputExecutor` in `scheduler.py` `_register_defaults` and `executors/__init__.py`
- [feat] Added `InputNodeType` TS interface, `"input"` to `NODE_TYPE_CATALOG` (category `"io"`), and `NODE_DESCRIPTIONS` in `types/graph.ts`
- [feat] Added `createDefaultNode` case for `"input"` in `graphAdapter.ts` with one default string variable
- [feat] Added play-triangle SVG icon for `"input"` in `nodeIcons.tsx`
- [feat] Added `"io"` (Input / Output) category to `NodePalette.tsx` category order and labels
- [feat] Added `inputNodeValues: Record<string, Record<string, unknown>>` ephemeral state to Zustand store with `setInputNodeValue` action
- [feat] In `DanNode.tsx`, InputNode renders editable fields per variable (text/number/checkbox) on the node body
- [feat] Updated `startRun` to collect `inputNodeValues` from InputNode and pass as run inputs, bypassing RunInputsDialog
- [feat] Added Export button in `EditorToolbar.tsx` — serializes `danGraph` as formatted JSON, triggers browser download
- [feat] Added Import button in `EditorToolbar.tsx` — file picker validates `dan_graph_v1` structure, creates new graph via API, hard-resets ephemeral state
- [feat] Created `CommandPalette.tsx` — modal overlay with search input, arrow-key navigation, Enter to select, Escape to close; filters nodes by name/type substring match; on select centers viewport and selects node
- [feat] Added `commandPaletteOpen` state to store, bound `Cmd/Ctrl+K` in `useKeyboardShortcuts.ts`, mounted in `App.tsx`
- [feat] Added `groupIntoComposite()` store action — groups multi-selected nodes into a CompositeNode with auto-generated `in_`/`out_` ports, port collision handling, sub-graph creation, entry/exit point detection, edge remapping, and undo snapshot
- [feat] Bound `Cmd/Ctrl+Shift+G` for grouping in `useKeyboardShortcuts.ts`

## 2026-02-24 (6-4 validation)
- [feat] Extended `isValidConnection` in `connectionValidation.ts` with port-existence, single-incoming-edge, and JSON Schema type-level compatibility checks
- [feat] Added `POST /api/graphs/{graph_id}/validate` endpoint in `app.py` — runs `validate_graph()` and returns structured `{errors, warnings}` JSON with extracted `node_id`/`edge_id`
- [feat] Added `validateGraph` API client function in `api.ts` with `ValidationIssue`/`ValidationResult` types
- [feat] Added `validationErrors: Record<string, string[]>` to Zustand store — auto-populated after every successful `saveGraph()`, cleared on load/save-start
- [feat] Added validation error badge on `DanNode` — red dot in top-right corner with tooltip showing error messages
- [feat] Toast summary after validation — "N errors" warning or "Validation passed" info toast
- [fix] Added missing `BaseModel` import in `src/dan/models/control_flow.py` (pre-existing bug)

## 2026-02-24 (6-3 node & port editing)
- [feat] Port editor in `ConfigPanel.tsx` — replaced read-only comma-separated port display with editable rows (name input, required checkbox for input ports, delete button, "Add Port" button) for both input and output port lists
- [feat] Added `renamePort` and `deletePort` store actions in `useGraphStore.ts` — atomic port rename updates node ports + all connected edges' `source_port`/`target_port` + React Flow handles in one undo snapshot; delete removes port + all referencing edges
- [feat] Inline node rename in `DanNode.tsx` — double-click name span enters edit mode with transparent input; Enter/blur commits, Escape reverts, stopPropagation prevents drill-in, auto-select text via ref + useEffect
- [feat] Output schema visual editor (`SchemaEditor`) in `ConfigPanel.tsx` — for `llm_operator` and `router` nodes; Visual mode renders property rows (name, type dropdown, required checkbox, delete); Raw JSON mode with textarea; toggle between modes; invalid JSON blocks visual switch; empty schema auto-initializes as `{type:"object", properties:{}}`
- [feat] Port name validation — no duplicates, no empty names; inline red border + tooltip on violation
- [docs] Updated `docs/plans/6-3-node-port-editing.md` — checked off tasks 1–3 (code), noted 3-6 (nested objects) deferred to v2
- [docs] Updated `docs/architecture.md` — documented port editor, inline rename, SchemaEditor capabilities; updated directory descriptions

## 2026-02-24 (6-2 clipboard, context menu, edge reconnection)
- [feat] Added clipboard slice to Zustand store (`copySelected`, `pasteClipboard`, `duplicateSelected`) with UUID remapping, position offsetting, and internal-edge preservation
- [feat] Added keyboard shortcuts `Cmd/Ctrl+C` (copy), `Cmd/Ctrl+V` (paste), `Cmd/Ctrl+D` (duplicate) in `useKeyboardShortcuts.ts`, respecting text input focus
- [feat] Created `ContextMenu.tsx` — right-click context menu with canvas (Paste), node (Copy/Duplicate/Delete), and edge (Delete/Change Type) actions; dismisses on click-away or Escape
- [feat] Wired context menu in `GraphCanvas.tsx` via `onPaneContextMenu`, `onNodeContextMenu`, `onEdgeContextMenu` callbacks
- [feat] Enabled edge reconnection: `edgesReconnectable` prop + `onReconnect` handler that updates both React Flow edge and embedded DAN edge data, with `isValidConnection` guard
- [docs] Updated `docs/plans/6-2-clipboard-context-menu.md` — checked off tasks 1-5, recorded decisions
- [docs] Updated `docs/architecture.md` — documented ContextMenu component, updated store/hooks/canvas descriptions

## 2026-02-24 (4-1 grounded paper-writing upgrade)
- [feat] Rewrote `examples/paper_writing.py` into an INFORMS-oriented, internet-grounded workflow with parallel literature-aspect fan-out, citation verification, claim-evidence gating, human interview loop, evidence-aware section drafting, multi-role review panel, iterative revision, LaTeX compilation, and submission packaging
- [feat] Added example tool suite: `check_latex_deps`, `search_papers`, `search_web`, `citation_verifier`, `compile_latex`, `save_paper`, `package_submission`
- [feat] Expanded built-in server tool registry in `src/dan/server/app.py` to support search/verification/LaTeX/submission tools for editor-run workflows
- [refactor] Updated `src/dan/executors/control_flow.py` HumanInTheLoop executor to prefer dynamic prompt text from `user_prompt`/`prompt` input ports when provided
- [test] Replaced `tests/test_examples/test_paper_writing_e2e.py` with an updated deterministic suite for the new topology (8 passing tests)
- [test] Regression checks passed: `python -m pytest tests/test_examples/test_paper_writing_e2e.py -q` and `python -m pytest tests/test_server -q`
- [docs] Added and completed `docs/plans/4-1-grounded-paper-writing-upgrade.md`; updated `docs/todo.md` and `docs/architecture.md` to track the finished upgrade

## 2026-02-24 (Phase 3.75 planning)
- [docs] Created `docs/plans/6-phase-3.75-visual-editor-editing.md` — parent plan for Phase 3.75 (Visual Editor Full Editing) with 5 sub-plans, shared decisions (run-state isolation, layer-aware mutations, multi-select ripple effects, grouping scope lock, InputNode value persistence, import hard-reset), and dependency graph
- [docs] Created 5 sub-plan files with hierarchical task breakdowns:
  - `6-1-history-multiselect.md` — undo/redo history stack (GraphSnapshot, push/undo/redo, drag debounce, run-state exclusion, depth cap) + multi-select (lasso, shift-click, selectedNodeIds, bulk delete, ConfigPanel summary)
  - `6-2-clipboard-context-menu.md` — copy/paste/duplicate (clipboard slice, UUID remapping, cross-layer paste, edge preservation) + context menu (canvas/node/edge zones, 3 trigger callbacks) + edge reconnection (edgesReconnectable, onReconnect)
  - `6-3-node-port-editing.md` — port editor (add/remove/rename with atomic edge updates, schema, required toggle) + inline node rename (double-click, stopPropagation) + output schema visual builder (tree editor, raw JSON toggle)
  - `6-4-validation.md` — port-aware connection validation (port existence, schema compatibility, single-incoming-edge) + visual drag feedback (CSS handle classes) + validation API endpoint (POST /api/graphs/{id}/validate) + inline badges + toast summary
  - `6-5-graph-io-input-node.md` — InputNode (backend model/executor/registry + frontend catalog/adapter/icon/palette + ephemeral value persistence) + import/export JSON (hard-reset on import) + Cmd+K command palette + sub-graph creation from selection (cut-edge analysis, deterministic port naming, data-edges-only scope lock)
- [docs] Updated `docs/todo.md` — added plan links for Phase 3.75 (6 parent + 5 sub-plans), renumbered future phase plan references (Markdown 6→7, Marketplace 7→9, Memory stays at 8)

## 2026-02-24 (LLM API guide)
- [docs] Created `docs/llm-api-guide.md` — comprehensive LLM-facing API reference covering builder DSL, all 10 node types with full parameter signatures, four edge wiring mechanisms, sub-graph context managers (WhileLoop, ForEach, Composite), engine setup (EngineConfig, ToolRegistry, ExecutorRegistry, checkpointing, event callbacks), complete paper-writing example, REST API endpoints, type reference tables, patterns/recipes, and full import map
- [docs] Updated `.cursor/rules/project-tracking.mdc` — added `docs/llm-api-guide.md` to document inventory (read before writing API code; update when nodes, edges, builder, engine, executors, or examples change) and "After Each Modification" checklist (item 8)

## 2026-02-24 (README + tracking rule)
- [docs] Created `README.md` — project overview, quick start, builder DSL examples, visual editor features, API endpoints, roadmap
- [docs] Updated `.cursor/rules/project-tracking.mdc` — added `README.md` to document inventory and "After Each Modification" checklist (update on new features, setup changes, CLI commands, roadmap milestones)

## 2026-02-24 (Phase 3.75 roadmap + MVP fixes)
- [docs] Added Phase 3.75 — Visual Editor Full Editing to `todo.md`: 13 items covering port editor, inline rename, edge reconnection, copy/paste, undo/redo, multi-select, context menu, sub-graph creation from selection, schema editor, validation feedback, import/export, node search
- [fix] Sub-graph editing: removed read-only guards from GraphCanvas, ConfigPanel, NodePalette, BreadcrumbBar; `saveGraph` is now layer-aware (writes to correct `sub_graphs[key]`)
- [fix] Auto-layout on graph load: detects degenerate positions (all nodes at 0,0) and applies dagre layout automatically
- [fix] Server `.env` loading: added `load_dotenv()` to `app.py` so `DAN_LLM_API_KEY` is picked up from `.env`
- [fix] `RunInputsDialog`: replaced `useEffect`-based variable detection with synchronous `useMemo` to prevent premature auto-run before variables are computed

## 2026-02-24 (MVP — end-to-end runnable from UI)
- [feat] **Server-side tool registry:** `RunManager` now accepts a `ToolRegistry` parameter; engines created for runs use it. `app.py` lifespan registers `save_paper` as a built-in tool. Paper-writing workflow now runs end-to-end from the visual editor.
- [feat] **Run-inputs dialog:** `RunInputsDialog.tsx` — modal that detects `{variable}` template placeholders from entry node prompts + unconnected input ports. Shows a form before execution so users can provide workflow inputs (e.g. `topic` for paper writing). Graphs with no inputs run immediately. Cmd/Ctrl+Enter shortcut to submit.
- [refactor] `EditorToolbar.tsx`: Run button now opens `RunInputsDialog` instead of calling `startRun()` directly
- [infra] 256 backend tests passing, 0 TypeScript errors

## 2026-02-24 (Phase 3.5 — full implementation)
- [feat] **5-1 Multi-Layered Graph Navigation:**
  - Backend: Added `is_blackbox: bool = False` to `CompositeNode`; created `CompositeExecutor` (input/output mapping + single `run_subgraph` call); registered in scheduler
  - Frontend: `layerStack` + `drillIn`/`drillOut`/`jumpToLayer` in Zustand store; double-click drill-in on composite/while_loop/for_each nodes; read-only guards when drilled in; `BreadcrumbBar.tsx` (Root > Node > Node navigation); `PortMappingOverlay.tsx` (input/output port mapping display); deleted `CompositePreview.tsx` modal; CSS fade-in animation
  - Tests: `test_composite_executor.py` (4 tests, all passing)
- [feat] **5-2 Live Execution Visualization:**
  - `nodeTimings` + `activeExecutionPath` in store; `handleRunEvent` tracks start/end timestamps per node
  - `DanNode.tsx`: CSS pulse animation on active nodes, completion flash, duration badges (e.g. "123ms", "1.2s"), opacity dimming for inactive nodes during runs
  - `AnimatedEdge.tsx`: custom React Flow edge with SVG particle flow on active edges (source completed → target started), dimming for inactive edges; registered as `smoothstep` override
  - `ExecutionTimeline.tsx`: horizontal timeline bar with colored segments per node, click-to-select; mounted above bottom tabs
- [feat] **5-3 Rich Logging Window:**
  - Backend: 5 new `EventType` values (`LLM_THINKING`, `TOOL_CALL_STARTED`, `TOOL_CALL_RESULT`, `CODE_OUTPUT`, `INTERMEDIATE_TEXT`); `emit_event` on `ExecutionContext`; unified parent `run_id` for sub-graph events; rolling latest-500 event buffer (was first-500)
  - LLMExecutor emits `LLM_THINKING` (model, prompt preview); ToolExecutor emits `TOOL_CALL_STARTED`/`TOOL_CALL_RESULT`; CodeExecutor captures stdout/stderr via redirect and emits `CODE_OUTPUT`
  - Frontend: `EVENT_CATEGORY` mapping; rebuilt `LogPanel.tsx` with grouped-by-node sections, sub-grouped by category, inline SVG icons (brain, wrench, terminal, X), color coding, text/node/type filtering, click-to-select, auto-scroll
- [feat] **5-4 Build Palette:**
  - `paletteTemplates.ts`: extensible template factory (`TemplateResult` with `subGraphs: Record<string, DanGraph>`); ReAct template (WhileLoop + LLM→Tool body); Plan-Execute template (Composite + Planner→Executor body)
  - `selectedEdgeType` + `addTemplateNode` in store; `onConnect` uses selected edge type with proper styling (color, label, animation for context edges)
  - Rebuilt `NodePalette.tsx`: search input, collapsible categories (Operators, Control Flow, Pre-defined Agents, MCP/Wrapped Agents, Composite), template drag-drop with `template:` prefix, disabled MCP placeholders, compact edge type selector, `NODE_DESCRIPTIONS` hover tooltips
- [feat] **5-5 UI/UX Polish:**
  - Toast system: `ToastContainer.tsx` (fixed bottom-right, slide-in, auto-dismiss 4s/6s); `addToast`/`removeToast` in store; all async actions wrapped with success/error toasts
  - Loading states: `Spinner.tsx`; `loadingGraph`/`savingGraph` flags in store
  - Connection validation: `connectionValidation.ts` (no self-connect, no duplicates, port existence); `isValidConnection` prop on ReactFlow
  - Merged toolbar: `EditorToolbar.tsx` combining GraphSwitcher + RunPanel (DAN branding, graph selector, save/run/resume/disconnect, status badge, auto-layout button); replaced both components in App.tsx
  - Node icons: `nodeIcons.tsx` (inline SVG for all 10 types); added to `DanNode.tsx` header
  - Keyboard shortcuts: `useKeyboardShortcuts.ts` (Cmd/Ctrl+S → save)
  - Edge labels: data edges now show `source_port → target_port`
  - Auto-layout: `layout.ts` using dagre (LR, nodesep 40, ranksep 60); `applyAutoLayout` in store
  - Favicon: updated title + `favicon.svg`; ConfigPanel: larger textareas, editable edge config
- [infra] All changes validated: 256 backend tests passing, 0 TypeScript errors, 0 lint errors

## 2026-02-24 (Phase 3.5 plan consistency pass)
- [docs] Normalized all 5 sub-plans for consistent formatting: bold-keyword Notes (5-1), standardized "Stretch:" label for deferred items (all plans), unified "Docs sync" task naming with architecture.md → todo.md → changelog.md order (all plans), removed V1/V1.1 version labels (5-4), aligned test paths to existing `tests/test_*` layout (5-1, 5-3)

## 2026-02-24 (Phase 3.5 plan refinements — decision lock)
- [docs] Updated `plans/5-phase-3.5-frontend-design.md` shared decisions: locked unified parent `run_id` event stream for sub-graphs, navigation-only/read-only scope for 5-1 drill-in, and simple-first-but-extensible template strategy for 5-4
- [docs] Updated `plans/5-1-multi-layered-graph.md`: clarified read-only drill-in MVP, added explicit UI guardrails to disable edits while inside nested layers, and updated test scope accordingly
- [docs] Updated `plans/5-3-rich-logging.md`: added tasks for parent `run_id` reuse in `_run_subgraph`, hierarchy metadata tags (`graph_key`, `layer_path`, `parent_node_id`), and rolling latest-500 event buffer validation
- [docs] Updated `plans/5-4-build-palette.md`: changed template factory contract to support multi-level sub-graphs via extensible `subGraphs` payload while keeping V1 template implementations simple

## 2026-02-24 (Phase 3.5 — detailed sub-plans)
- [docs] Created top-level plan `plans/5-phase-3.5-frontend-design.md` — dependency graph, sequencing recommendation, shared decisions across all 5 sub-plans
- [docs] Created `plans/5-1-multi-layered-graph.md` — CompositeExecutor, `is_blackbox`, canvas drill-in replacing modal, breadcrumb bar, animated transitions, port mapping visualization (7 tasks, 23 sub-tasks)
- [docs] Created `plans/5-2-live-execution-viz.md` — pulse/glow CSS animations, animated edges with particles, execution path dimming, timeline/playback scrubber, duration badges (7 tasks)
- [docs] Created `plans/5-3-rich-logging.md` — 5 new backend event types, executor emission, structured collapsible log UI, icons/colors, click-to-select, filtering (6 tasks, 22 sub-tasks)
- [docs] Created `plans/5-4-build-palette.md` — searchable categorized sidebar, ReAct/Plan-Execute agent templates, MCP placeholder, edge type selector, hover preview tooltips (7 tasks)
- [docs] Created `plans/5-5-ui-polish.md` — error handling/toasts, loading states, connection validation, toolbar merge, resizable panels, keyboard shortcuts, auto-layout, syntax highlighting (13 task groups)
- [docs] Updated `todo.md` — replaced inline Phase 3.5 bullet lists with linked sub-plan references

## 2026-02-24 (todo restructure — Phase 3.5 Frontend Design)
- [docs] `todo.md`: added Phase 3.5 — Frontend Design with 5 sub-groups (A. Multi-Layered Graph Navigation, B. Live Execution Visualization, C. Rich Logging Window, D. Build Palette, E. UI/UX Polish)
- [docs] `todo.md`: absorbed old Phase 4 (Composite Nodes) into Phase 3.5-A, old Phase 5 (Execution Visualization) into Phase 3.5-B, old Phase 2.5 non-bug-fix items into Phase 3.5-E
- [docs] `todo.md`: removed Phase 2.5 section (completed bug fixes retained in changelog; remaining items moved to Phase 3.5-E); renumbered Phase 6 → Phase 4
- [docs] `architecture.md`: updated Composite Node Preview section — replaced "Phase 4" reference with Phase 3.5-A drill-in navigation plan
- [docs] Plan file created: `phase_3.5_frontend_design_99e66334.plan.md` with full breakdown, review fixes (sub-plan split guidance, typed event contract lockstep note, `is_blackbox` field), and documentation checklist

## 2026-02-24 (editor build cleanup — TypeScript fixes)
- [fix] `App.tsx`, `DanNode.tsx`, `CompositePreview.tsx`: replaced unsafe `Record<string, unknown>` casts with explicit node-type narrowing for `body_graph` access
- [fix] `NodePalette.tsx`: replaced `Object.groupBy` with typed `reduce` grouping to remove ES2024 dependency and fix strict TypeScript inference errors
- [fix] `useGraphStore.ts`: removed unused `portHandleId` import and unused `EMPTY_GRAPH` constant to satisfy strict compile checks
- [test] `editor/`: `npm run build` now passes (TypeScript + Vite build successful); only Node.js version warning remains (20.17 vs Vite recommended 20.19+)
- [docs] `bugs.md`: moved `Object.groupBy` issue out of Known Limitations and into Resolved Bugs
- [docs] `todo.md`: added and checked off "Fix editor TypeScript build blockers" item in Phase 2.5

## 2026-02-24 (Phase 2.5 — fix 5 open editor bugs)
- [fix] `GraphCanvas.tsx`: replaced manual `clientX - bounds.left` drop position calculation with `screenToFlowPosition()` from `useReactFlow()` — nodes now land correctly when canvas is zoomed/panned
- [fix] `graphAdapter.ts`: replaced module-level `_counter` with `Date.now()` + random suffix for node IDs — eliminates collisions after page refresh
- [fix] `GraphCanvas.tsx`: removed wrapper `onKeyDown` handler and `tabIndex`, added `deleteKeyCode={["Delete", "Backspace"]}` prop to `<ReactFlow>` — delete key now fires reliably via React Flow's native handling
- [fix] `useGraphStore.ts`: extracted `Date.now()` to a single `edgeId` const in `onConnect` — React Flow edge ID and `danEdge.id` are now always identical
- [fix] `App.tsx`: changed `hasBodyGraph` from `useCallback` to `useMemo`, updated call site to use the memoized boolean directly — avoids redundant recomputation on every render
- [fix] `GraphCanvas.tsx`: removed unused `danNodeToReactFlow` import
- [docs] `bugs.md`: moved all 5 bugs from "Open Bugs" to new "Resolved Bugs" section
- [docs] `todo.md`: checked off 5 bug-fix items in Phase 2.5

## 2026-02-24 (visual editor review — bugs + polish backlog)
- [docs] `bugs.md`: added 5 open bugs found during frontend code review — drop position wrong when zoomed/panned, node ID collisions after page refresh, delete key unreliable, onConnect edge ID mismatch, hasBodyGraph useCallback/useMemo
- [docs] `bugs.md`: added 9 known limitations — no error feedback, no loading states, no connection validation, ConfigPanel generic field dump, fixed-height bottom panel, two header bars, no keyboard shortcuts, app title/favicon defaults
- [docs] `todo.md`: added Phase 2.5 (Visual Editor Polish) with 19 MVP-critical items covering bug fixes, UX improvements, and visual polish

## 2026-02-24 (Phase 3)
- [feat] Phase 3 — Paper-Writing Proof of Concept (Extended):
  - `examples/paper_writing.py`: end-to-end workflow using builder DSL (~200 lines)
    - 8 nodes: LLMOperator x3 (idea_gen, lit_survey, outline_planner), ForEach (section_writers with parallel section writing), CodeOperator x2 (assembler, format_output), WhileLoop (review_loop with structured review-and-revise), ToolOperator (save_paper)
    - Structured output normalization on outline_planner (JSON schema → title, abstract, sections) and review_and_revise (verdict, feedback, draft)
    - Explicit ToolRegistry wiring: custom ExecutorRegistry with pre-registered save_paper tool function
    - Configurable CLI: topic, max review iterations, verbose logging
    - Event callback for live progress tracking
    - Saves compiled graph JSON to `graphs/paper_writing.json` and paper output to `output/`
  - `tests/test_examples/test_paper_writing_e2e.py`: 9 tests covering 3 categories
    - Happy-path mock e2e (6 tests): graph compilation, node types, entry/exit, JSON round-trip, full mock run, section count
    - Tool-failure recovery (2 tests): tool exception → FAILED status, unknown tool_id → FAILED status
    - Checkpoint/resume (1 test): full run with filesystem checkpointing, verify checkpoint files created, resume succeeds
  - Live-tested against vectorengine.ai with claude-sonnet-4-6: all 8 nodes completed, outline needed 2 normalization attempts, review loop ran 2 iterations, paper saved to output/
- [test] 9 new tests (252 total)
- [docs] Created `docs/plans/4-phase-3-paper-writing.md` with task breakdown
- [docs] Updated `todo.md`: marked Phase 3 complete, added 3 reliability backlog items (tool retry, per-node retry policy, handoff validators)
- [docs] Updated `architecture.md`: added `examples/` directory to tree

## 2026-02-24 (architecture expansion — hyperedges, HumanNode, context scoping, applications)
- [docs] `architecture.md`: added "Context Scoping Across Agent Boundaries" — four scopes (global, local, pass_down, emit_up) with explicit schemas at agent boundaries, upward signals (sticky/non-sticky), revised agent boundary contract (accepts, returns, reads_global, writes_global, signals)
- [docs] `architecture.md`: added "Hyperedges: Skills and Rules" — skills and rules modeled as hyperedges attaching to multiple nodes. Four types (skill, guardrail, style, override), attachment scope (node ID, type, tags, subgraph), precedence rules, execution hooks (pre_prompt, tool_call, post_output, validation)
- [docs] `architecture.md`: added "HumanNode (Generalized)" — human as a first-class node type, not external to the graph. Chat UI as renderer for HumanNode I/O. Adjustable autonomy via topology. Background mode = zero HumanNodes.
- [docs] `architecture.md`: added "Four Top-Level Agents Architecture" — Ask, Agent, Debug, Plan as independent DAN networks sharing a common context layer. Mode switching via shared context serialization.
- [docs] `development-plan.md`: added Section 6 "Applications: Rebuilding Real Systems on DAN" with two subsections:
  - 6.1 Coding Assistant (Cursor-like): ~15 node types, every mode as a graph template, four top-level agents with shared context
  - 6.2 Research IDE (science-cursor): scholar engines as DAN agents, PaperOrchestrator as a DAN network, rebuild strategy
- [docs] `todo.md`: added 5 backlog items — hyperedges, HumanNode generalization, context scoping, coding assistant PoC, science-cursor rebuild

## 2026-02-24 (docs review fixes)
- [docs] `architecture.md`: moved "Workflows as Code" out of Phase 1 section into its own "Phase 1.5 — not yet built" section (was misleading — `dan.builder` doesn't exist yet)
- [docs] `architecture.md`: fixed checkpointing description — "per topological level" not "per node"; removed stale "Targeted for Phase 1"
- [docs] `architecture.md`: removed duplicate frontend tech stack subsection (already in top-level Tech Stack)
- [docs] `architecture.md`: added missing files to directory tree (`__init__.py` files, `editor/package.json`, `vite.config.ts`, `tsconfig.json`)
- [docs] `architecture.md`: removed hardcoded test count from directory tree
- [docs] `changelog.md`: reordered to consistent newest-first (Phase 0 was at top despite being oldest)
- [docs] `development-plan.md`: fixed stale "Policies (to be defined in Phase 0)" → "(defined in Phase 0)"
- [docs] `development-plan.md`: updated Section 4 recommendation — struck through completed items, changed tense to past
- [docs] `bugs.md`: moved Node.js version warning and `Object.groupBy` from "Open Bugs" to "Known Limitations" (environment notes, not code bugs)
- [docs] `todo.md`: renumbered Phase 1.5 plan ID from `8:` to `1-5:` so future plan file sorts correctly between Phases 0 and 1

## 2026-02-24 (post-Phase 2 docs sync)
- [docs] Updated `architecture.md` tech stack — added FastAPI, Zustand, Tailwind CSS, pytest/httpx to explicitly list all dependencies
- [docs] Updated `development-plan.md` roadmap table — replaced "Est. Effort" with "Status" column, marked Phases 0/1/2 as Done with test counts
- [docs] Populated `bugs.md` — added Node.js version warning, `Object.groupBy` ES2024 requirement, known limitations (no editor-side validation, single-user, no undo/redo)

## 2026-02-24 (Phase 2)
- [feat] Phase 2 — Visual Editor full-stack implementation:
  - Engine event system: 9 typed events (`EngineEvent`, `EventType`), opt-in `event_callback` on `Engine`, backward compatible
  - Run manager (`server/run_manager.py`): background task execution, event pubsub via async queues, catch-up snapshots on subscriber reconnect
  - FastAPI backend (`server/app.py`): graph CRUD (list, create, get, update, delete), run endpoints (start, resume, status, list), WebSocket live event stream
  - Graph store (`server/graph_store.py`): filesystem-based JSON persistence in `./graphs/`, `last_opened` tracking
  - CLI entry point: `dan-serve` / `python -m dan.server` starts backend on localhost:8000
  - React Flow editor (`editor/`): Vite + React + TypeScript + React Flow v12 + Zustand + Tailwind CSS v4
  - Bidirectional DAN <-> React Flow adapter (`graphAdapter.ts`): port handles, edge type colors, node factory for all 10 types
  - Custom `DanNode` component: per-type color coding, port labels, execution status rings
  - `NodePalette`: draggable + click-to-add for all 10 node types grouped by category
  - `ConfigPanel`: dynamic form-based editing of all node/edge properties
  - `GraphCanvas`: React Flow canvas with drag-and-drop from palette, minimap, controls
  - `RunPanel`: save, run, resume, disconnect controls with live status badge
  - `LogPanel`: scrolling timestamped event log with color-coded event types
  - `OutputPreview`: per-node output viewer for selected node
  - `CompositePreview`: read-only sub-graph modal for body_graph-backed nodes
  - `GraphSwitcher`: graph list dropdown, create new, delete, auto-load last opened
- [infra] Added `fastapi`, `uvicorn[standard]`, `websockets`, `httpx` dependencies to `pyproject.toml`; bumped version to 0.2.0
- [infra] Added `dan-serve` script entry point in `pyproject.toml`
- [infra] Vite proxy config: `/api` routes to backend at localhost:8000 during development
- [fix] Replaced `assert _run_manager` in API endpoints with proper `_require_run_manager()` returning HTTP 503
- [test] 27 new tests (167 total): server API integration tests (CRUD + runs), run manager unit tests, engine event instrumentation tests
- [docs] Created `docs/plans/3-phase-2-visual-editor.md` with full task breakdown
- [docs] Updated `architecture.md` with Phase 2 modules, server architecture, API endpoints, frontend layout
- [docs] Updated `todo.md` Phase 2 line with plan link

## 2026-02-24 (Phase 1)
- [feat] Phase 1 complete — async execution engine built on Phase 0 type system:
  - `engine/state.py`: NodeStatus enum, PortDataStore (port data routing), ExecutionState (run-level aggregate)
  - `engine/context_runtime.py`: SharedContextStore (Layer 3), ArtifactStore (Layer 4, immutable versioning), LocalStateManager (Layer 2, scoped to composites)
  - `engine/executor.py`: EngineConfig, NodeExecutor protocol, ExecutionContext (scoped access for executors), ExecutorRegistry
  - `engine/conditions.py`: safe Python expression evaluator with restricted builtins for IfElse/WhileLoop conditions
  - `engine/normalizer.py`: OutputNormalizer pipeline — JSON extraction (fenced/inline), schema validation, re-prompt message builder
  - `engine/checkpoint.py`: CheckpointStore protocol, FileSystemCheckpointStore, NullCheckpointStore
  - `engine/scheduler.py`: Kahn's topological sort with parallel-level detection, asyncio.gather dispatch, sub-graph recursion, Engine.run()/resume() public API
  - `executors/llm.py`: LLMExecutor using AsyncOpenAI SDK (vectorengine.ai default, claude-sonnet-4-6), output normalization loop, transient API retry with backoff
  - `executors/tool.py`: ToolRegistry + ToolExecutor for function-based dispatch
  - `executors/code.py`: CodeExecutor with sandboxed Python exec and restricted builtins
  - `executors/control_flow.py`: IfElseExecutor, WhileLoopExecutor (with compaction + stagnation detection), ForEachExecutor (semaphore-based parallelism), ReduceExecutor, RouterExecutor (LLM-powered), HumanInTheLoopExecutor (callback-based with timeout)
- [infra] Added `openai>=1.0` and `pytest-asyncio` dependencies to `pyproject.toml`
- [infra] Added `.env` and `.env.example` for LLM provider config (vectorengine.ai endpoint)
- [test] 49 new engine tests (140 total): unit tests for state stores, expression evaluator, normalizer; integration tests for linear chain, IfElse branching, WhileLoop with condition exit, ForEach parallel fan-out, checkpoint/resume
- [docs] Added `docs/plans/2-phase-1-orchestration-engine.md` with full task breakdown
- [docs] Updated `architecture.md` with engine module layout and execution engine section
- [docs] Marked Phase 1 complete in `todo.md`
- [docs] Added "Workflows as Code" as first-class design principle — every workflow must be definable in Python code, not just visually. Added to `development-plan.md` Section 5, `architecture.md`, and key decisions.
- [docs] Added Phase 1.5 (Workflow Builder API) to roadmap — fluent Python DSL (`dan.builder`) that compiles to `dan_graph_v1` JSON, round-trips with visual editor. Inserted before Phase 2 in `todo.md` and `development-plan.md`.

## 2026-02-24 (Phase 0 + project setup)
- [docs] Created project tracking structure: `docs/` directory with `architecture.md`, `changelog.md`, `todo.md`, `bugs.md`, and `plans/`
- [docs] Moved `development-plan.md` from project root to `docs/`
- [docs] Scaffolded `architecture.md` from development plan sections 4–5
- [infra] Added `.cursor/rules/project-tracking.mdc` — always-apply rule governing doc maintenance workflow
- [docs] Changed plan naming from flat sequential (`plan-01-name`) to hierarchical (`1-name`, `1-1-name`, `1-1-1-name`) to mirror the task tree
- [docs] Added `docs/plans/1-phase-0-formal-spec.md` and linked Phase 0 in `docs/todo.md`
- [docs] Resequenced roadmap: visual editor moved to Phase 2, paper-writing proof shifted to Phase 3, advanced execution visualization moved to Phase 5
- [docs] Updated architecture to lock language split (Python core + TypeScript editor) and shared `dan_graph_v1` JSON contract
- [feat] Phase 0 complete — implemented formal spec as Python Pydantic types:
  - Port models (`InputPort`, `OutputPort`) with JSON Schema type declarations
  - 10 node types: 3 operators (LLM, Tool, Code), 6 control-flow (IfElse, WhileLoop, ForEach, Reduce, Router, HumanInTheLoop), 1 composite
  - 3 edge types (Data, Control, Context) with discriminated-union deserialization
  - Four-layer context model (edge data, node-local state, shared context store, artifact store)
  - Composite-node contract (external schemas, control state, local state, read/write sets, compaction rules, projections)
  - `dan_graph_v1` JSON serialization contract with UI metadata (position, ui dict) for lossless editor round-trips
  - Graph with recursive sub-graphs, entry/exit points, shared context declarations
  - Node type registry for extensibility
  - Validation: port schema compatibility (MVP structural), graph well-formedness (7 checks), cycle detection
  - 75 tests passing including full paper-writing motivating example
- [docs] Added four-layer context management model to `development-plan.md` Section 5 and `architecture.md`: edge data (bounded), node-local state (scoped), shared context store (blackboard), artifact store (by reference)
- [docs] Added context projection pattern — scope boundary functions that extract minimal views per consumer (loop controller vs. reviser vs. parent graph)
- [docs] Defined composite node contract (external schemas, control state, local working set, read/write sets, compaction rule) and context policies (mutation, parallel merge, compaction, failure exits)
- [docs] Expanded Phase 0 plan with context management tasks (3-1 through 3-6), context-related validation rules (5-3, 5-4), and context scoping tests (6-3)
- [docs] Added memory system concept to `development-plan.md` Section 5 (short-term vs. long-term, encoding/consolidation/retrieval) and backlog item in `todo.md`. Parked as backlog — not yet designed.
- [docs] Added output normalization (batch-norm analogy — deterministic parse → validate → re-prompt → retry on every LLM operator) to `development-plan.md`, `architecture.md`, and Phase 0 plan task 2-5
- [docs] Added operator-level retry policy (`max_retries`, `backoff`, `fallback_model`, `on_failure`) to `development-plan.md`, `architecture.md`, and Phase 0 plan task 2-6
- [docs] Added checkpointing/resumability to Phase 1 scope (`development-plan.md` roadmap, `todo.md`, `architecture.md`)
- [infra] Added root `.gitignore` with Python runtime/build/test ignores and future TypeScript editor artifacts (`node_modules`, JS package-manager logs)
- [docs] Added backlog items: dynamic `model_policy` (budget-aware + learned assignment for cases beyond strategies 1-4), `max_concurrency` on For-Each/Map
- [fix] Validation: edge endpoint checks (node + port existence) now apply to all edge types, not just DataEdge
- [fix] Validation: ContextEdge mode enforcement — read edges require key in node's `read_set`, write/append edges require key in `write_set`
- [fix] Validation: empty-schema data edges now emit a warning ("schema safety bypassed") instead of silently passing
- [fix] Model: added `control_state_schema` to `ForEachNode` and `CompositeNode` to match composite-node contract in docs
- [fix] Registry docstring corrected — registry is for programmatic discovery, not JSON deserialization (which uses Pydantic discriminated union)
- [test] Test suite expanded from 75 to 91 tests: added edge endpoint tests (all edge types), context permission enforcement, empty schema warnings, registry discovery

## 2026-02-24 (Phase 1.5)
- [feat] Phase 1.5 complete — fluent workflow builder DSL (`dan.builder`):
  - `builder/refs.py`: `NodeRef` and `PortRef` compile-time proxies with `__format__` (marker emission), `__rshift__` (>> chaining), `__getitem__` (port subscript), sanitized alias generation
  - `builder/builder.py`: `WorkflowBuilder` class with `workflow()` factory, node creation methods (`.llm()`, `.tool()`, `.code()`, `.if_else()`, `.reduce()`, `.router()`, `.human_in_the_loop()`), `.edge()` explicit wiring, context-manager sub-graphs (`.while_loop()`, `.for_each()`, `.composite()`), `.to_json()` / `.to_dict()` serialization
  - `builder/compiler.py`: compiles builder state → `Graph` model. Resolves f-string markers (`<<dan:node_id:port>>`), auto-generates InputPort/OutputPort, auto-generates DataEdge objects, node-type output contract map (matches runtime executor ports), entry/exit point detection, `validate_graph()` integration
  - `builder/decompiler.py`: `decompile(graph) -> str` produces executable Python. Topological sort with deterministic ordering, chain detection (>> sugar), context-manager emission for sub-graphs, `NodeRef` wrappers for sub-graph wiring, lossless preservation of metadata/context/edge types
- [feat] Four connection mechanisms: (1) f-string magic auto-wiring, (2) >> operator chaining, (3) PortRef passing, (4) explicit wf.edge()
- [feat] Node-type output contract map: `DEFAULT_OUTPUT_PORTS` maps each node_type to its actual runtime output port name (e.g. `llm_operator` → `text`, `for_each` → `results`, `if_else` → `branch`)
- [test] 70 new builder tests (237 total): unit tests for NodeRef/PortRef, marker resolution, compiler (each node type), sub-graph context managers, decompiler round-trip; integration tests for paper-writing workflow, engine execution (code chain, while-loop, for-each), editor round-trip golden test
- [docs] Added `docs/plans/1-5-builder-api.md` with full task breakdown
- [docs] Updated `architecture.md` with builder module layout, DSL design, connection mechanisms, decompiler details
- [docs] Marked Phase 1.5 complete in `todo.md`

## 2026-02-24 (Phase 4 roadmap — review fixes)
- [docs] `todo.md`: reworded Phase 4 description — markdown is a third authoring surface alongside Python DSL and visual editor, not a replacement
- [docs] `todo.md`: added plan file link placeholder (`6:`) for Phase 4; renumbered Phase 5 marketplace to `7:` to avoid collision with Phase 3.5's `plans/5-*` prefix
- [docs] `todo.md`: clarified backlog overlap — split hyperedge items into "engine runtime" (execution hooks, attachment logic) vs "markdown authoring syntax" (`.md` file references in workflow)
- [docs] `todo.md`: added 5 backlog items — markdown/Python coexistence policy, `dan.loader` ↔ `dan.builder` parity checklist, compiler diagnostics + source maps, markdown round-trip conformance tests, markdown format versioning
- [docs] `architecture.md`: replaced "Code-first, visual-second" key decision with "Three authoring surfaces, one IR" (Python DSL, markdown, visual editor all compile to `dan_graph_v1`)
- [docs] `development-plan.md`: replaced "Workflows as Code" section with "Three Authoring Surfaces, One IR" — table comparing Python / markdown / visual editor strengths and use cases
- [docs] `development-plan.md`: updated roadmap table — marked Phases 1.5 and 3 as Done with test counts, added Phase 3.5 and Phase 4, removed stale old Phases 4/5 (composite + visualization, now in 3.5)

## 2026-02-24 (Phase 4 Memory & Context Scoping)
- [docs] Promoted memory & context scoping from backlog to Phase 4 — context scoping across agent boundaries (global/local/pass_down/emit_up) and memory system for long chains (encoding, consolidation, retrieval)
- [docs] Renumbered Markdown Agent Format → Phase 5, Shareable Blocks / Marketplace → Phase 6

## 2026-02-24 (Phase 4 roadmap — now Phase 5)
- [docs] Added Phase 4 — Markdown Agent Format to `todo.md`: agent file format (YAML frontmatter + natural language), workflow file format (arrow notation), flow notation parser, port type inference, auto-wiring, `dan.loader` compiler, and paper-writing rewrite as validation
- [docs] Renumbered Shareable Blocks / Marketplace to Phase 5
- [docs] Added backlog items: composite agents in markdown, skills/rules as markdown hyperedges, markdown round-trip from visual editor, linked JSON Schema files

## 2026-02-24 (Phase 3.5 post-review bug fixes)
- [fix] Read-only drill-in guard: disabled click-to-add, drag, and editing in `NodePalette` and `ConfigPanel` when drilled into sub-graph layer
- [fix] Template port contracts: LLM nodes output port renamed `output` → `text` (matching `LLMExecutor` output key); ReAct loop condition changed `"not done"` → `"True"` (avoids `ConditionError`); Plan-Execute composite given proper `input_mappings`/`output_mappings`
- [fix] `startRun()` stale-graph guard: `saveGraph()` now returns `boolean`; `startRun` aborts if save fails
- [fix] Edge type styling sync: `updateEdgeData` now updates top-level `animated`, `label`, and `style.stroke` when `edge_type` changes (previously only updated `data.danEdge`)
- [fix] Subgraph event hierarchy tags: added `layer_path: tuple[str, ...]` to `ExecutionContext`; `emit_event` injects `layer_path` into event data; `run_subgraph` accepts `parent_node_id` and threads it to child contexts via scheduler
- [fix] LogPanel node names: `nodeNameMap` now reads `data.name` (matching DanNode model) with `data.label` as fallback
- [test] Fixed composite executor test mock to accept new `parent_node_id` parameter; 256 tests pass, TypeScript zero errors

## 2026-02-24 (Phase 1.5 post-review fixes)
- [fix] Builder/compiler idempotence: repeated `build()` calls now produce stable output (no in-place mutation of pending node kwargs/ports during compilation)
- [fix] Sub-graph entry refs: `body.input` / `body.item` now compile to usable entry input placeholders (`{input}` / `{item}`) with proper input-port generation for sub-graph entry nodes
- [feat] Builder typed-edge support: added `wf.control_edge(...)` and `wf.context_edge(...)`; compiler now materializes `ControlEdge` and `ContextEdge` (not only `DataEdge`)
- [fix] Decompiler edge fidelity: emits executable `control_edge`/`context_edge` calls instead of comments; round-trip now preserves edge types
- [fix] Decompiler chain safety: `>>` sugar is emitted only for true default-port chains; custom port wiring is preserved via explicit `wf.edge(...)`
- [fix] Decompiler metadata fidelity: preserves node `position`/`ui`/`metadata`, graph `created_at`/`updated_at`, and `artifact_refs`
- [feat] Builder artifact support: added `wf.artifact_ref(...)` and compiler support for graph-level `artifact_refs`
- [test] Added 6 regression tests for post-review issues (build idempotence, sub-graph entry refs, control/context edge round-trip, custom-port chain fidelity, UI/metadata/artifact preservation); total test suite now 243 passing

## 2026-02-24 (Phase 3.75 — 6-7 Workflow as Reusable Node, partial)
- [feat] Created `editor/src/lib/graphImporter.ts` — `graphAsCompositeNode()` converts a saved DAN graph into a CompositeNode insertion payload with recursive ID namespacing, port derivation from entry/exit points, collision-safe naming, and flattened sub_graphs
- [feat] Updated `NodePalette.tsx` — added "Saved Workflows" category listing all saved graphs (excluding current), with search filtering, drag/drop (`workflow:{graphId}`), and click-to-insert support
- [feat] Updated `GraphCanvas.tsx` — `onDrop` handler routes `workflow:` prefixed payloads to `addGraphAsNode` store action
- [docs] Updated `architecture.md` — added `graphImporter.ts` to directory structure, updated NodePalette description
- [docs] Plan 6-7 marked in-progress; tasks 2 (palette UX), 3 (import utility), and 4-4 (canvas drop) checked off. Remaining: store action `addGraphAsNode` (4-1–4-3, 4-5–4-6), validation/tests (6), docs sync (7)

## 2026-02-24 (Fix: log truncation + run recovery on refresh)
- [fix] Backend: raised event truncation limits — LLM prompt preview 200→2000 chars, tool args 100→2000, tool result 500→5000/2000 (`llm.py`, `tool.py`)
- [fix] Backend: raised `RunManager` event buffer 2000→10000 (`run_manager.py`)
- [feat] Frontend: `LogPanel` rows now expandable — click "more" to reveal full content; removed hard `.slice(0,200)` previews in `dataContent`
- [fix] Frontend: store log buffer raised 500→5000 entries (`useGraphStore.ts`)
- [feat] Frontend: persist active run to `sessionStorage` on start/resume; clear on terminal state (`run_completed`/`run_failed`)
- [feat] Frontend: `recoverActiveRun()` action — on page load, reads `sessionStorage`, validates run via `api.getRun()`, reconnects WebSocket with `_catchup` replay if still running
- [feat] `App.tsx` calls `recoverActiveRun()` after `loadGraphList()` on mount

## 2026-02-24 (Phase 4 memory policy defaults)
- [docs] `todo.md`: expanded Phase 4 defaults with an explicit future-tuning policy — current memory budgets/thresholds/TTLs/reducer choices are baseline defaults to be iteratively tuned using telemetry, retrieval quality, and cost/latency trade-offs

## 2026-02-24 (Phase 3.75 — 6-9 multi-tab workflow sessions plan)
- [docs] Added `docs/plans/6-9-multi-tab-workflow-sessions.md` with a tab-scoped state architecture plan (snapshot/restore, per-tab run recovery, toolbar tab UI, and tests)
- [docs] Reviewed and tightened 6-9 plan scope: added cross-tab event bleed guard (`run_id` check), lightweight storage constraints (metadata-only persistence), legacy-key migration, and `createGraph` tab-open behavior
- [docs] Updated `docs/plans/6-phase-3.75-visual-editor-editing.md` — set parent status to `in-progress` and added sub-plan row for 6-9
- [docs] Updated `docs/todo.md` — added 6-9 as an unchecked Phase 3.75 sub-plan and marked the parent 6-phase item as in-progress

## 2026-02-24 (Phase 3.75 — 6-10 gate editor UX)
- [feat] `editor/src/types/graph.ts`: added `GateNode` interface (`gate_mode`, `condition`, `max_iterations`) to `DanNode` union, `NODE_TYPE_CATALOG`, and `NODE_DESCRIPTIONS`
- [feat] `editor/src/lib/paletteTemplates.ts`: added `ifElseGateFactory` and `whileGateFactory` template factories + registered in `PREDEFINED_AGENT_TEMPLATES`
- [feat] `editor/src/lib/nodeIcons.tsx`: added diamond/rhombus icon for "gate" node type
- [feat] `editor/src/components/DanNode.tsx`: gate condition badge, "IF"/"WHILE" header indicator, green/red branch port coloring for gate output handles
- [feat] `editor/src/lib/graphAdapter.ts`: back-edge detection for while-gate `continue` port (dashed, animated, muted "loop back" label); added "gate" case to `createDefaultNode`
- [feat] `editor/src/components/ConfigPanel.tsx`: dedicated gate config section with gate_mode dropdown (auto-swaps output ports), condition input, and conditional max_iterations field

## 2026-02-24 (Phase 3.75 — 6-10 GateNode model + executor)
- [feat] Added `GateNode` model in `src/dan/models/control_flow.py` — unified conditional gate with `gate_mode="if_else"` (true/false branches) and `gate_mode="while"` (continue/done branches), `max_iterations` guard
- [feat] Added `GateExecutor` in `src/dan/executors/control_flow.py` — evaluates condition, routes inputs to exactly one branch output port, emits `gate_evaluated` event
- [feat] Registered `GateExecutor` in `src/dan/engine/scheduler.py` under `"gate"` node type
- [feat] Added `GateNode` to `NodeTypeRegistry` in `src/dan/registry.py`, `Node` discriminated union in `src/dan/models/graph.py`, and package exports in `src/dan/__init__.py`
- [feat] Added `GATE_EVALUATED` event type to `src/dan/engine/events.py`
- [test] Added `tests/test_engine/test_gate.py` — 10 tests covering model validation, if_else/while branching, error handling, metadata
- [fix] Updated `test_builtins_registered` count from 11 → 12 to account for new gate type

## 2026-02-24 (Phase 3.75 — 6-10 gate loop + condition redesign plan)
- [docs] Added `docs/plans/6-10-gate-loop-condition-redesign.md` covering gate-based visible loop authoring, condition-routing redesign, cycle-aware scheduler updates, collapsible loop groups, and migration strategy
- [docs] Reviewed and tightened plan risk areas: limited scope to while+condition redesign (keep `for_each`/`composite` unchanged), added transition compatibility for legacy graphs, constrained scheduler work to gate-controlled cycle regions with DAG fast-path retained, and added builder/decompiler + rollout-flag tasks
- [docs] Updated `docs/plans/6-phase-3.75-visual-editor-editing.md` with 6-10 sub-plan row and sequencing note
- [docs] Updated `docs/todo.md` with unchecked 6-10 Phase 3.75 sub-plan entry

## 2026-02-24 (Phase 3.75 — 6-10 GateNode/GateExecutor iteration tracking)
- [feat] Updated `GateNode.model_post_init` in `src/dan/models/control_flow.py` — auto-derives `output_ports` from `gate_mode` (if_else: true/false, while: continue/done) when not explicitly set
- [feat] Updated `GateExecutor` in `src/dan/executors/control_flow.py` — while mode now reads `gate_iteration` from `context.local_state` and injects it as `iteration` into condition vars; event data includes `iteration`/`max_iterations`; metadata includes `iteration`; error returns raw `ConditionError` string
- [test] Added `tests/test_engine/test_gate_executor.py` — 9 tests covering if_else branching, while branching with iteration tracking, condition errors, iteration counter availability, and output port derivation
- [fix] Updated `tests/test_engine/test_gate.py` — added `local_state` to MockContext for while-mode tests, updated error assertion to match new error format

## 2026-02-24 (Phase 3.75 — 6-10 cycle-aware scheduler + validation)
- [feat] `src/dan/engine/state.py`: added `PortDataStore.clear_node(node_id)` for clean cycle iteration resets
- [feat] `src/dan/engine/scheduler.py`: cycle-aware scheduling — `_topological_levels_with_backedges()` detects gate back-edges, `_iterate_cycle()` resets and re-executes cycle-region nodes, `_should_skip()` exempts while-gate back-edge ports from skip logic
- [feat] `src/dan/validation/graph.py`: enhanced `_validate_gate_cycles()` with bidirectional reachability for per-gate cycle regions, rejects gateless cycles and overlapping multi-gate cycles
- [test] Added `tests/test_engine/test_cycle_scheduling.py` — 14 tests covering DAG fast-path, while-gate loops, max_iterations, if_else branch skipping, gateless cycle rejection, and multi-gate cycle rejection
- [fix] Fixed `_should_skip` pre-existing bug where while-gate back-edge ports caused cycle-body nodes to be silently skipped
- [fix] Updated `tests/test_engine/test_streaming.py` — corrected `_max_event_buffer` assertion from 2000 → 10000 to match earlier buffer increase

## 2026-02-24 (Phase 3.75 — 6-9 multi-tab workflow sessions implementation)
- [feat] `editor/src/store/useGraphStore.ts`: added `TabInfo`/`TabSnapshot` types, `tabs`/`activeTabId`/`tabCache` state, `_snapshotActiveTab`/`_restoreTab`/`_persistTabState` helpers, `openTab`/`switchTab`/`closeTab`/`restoreTabs` actions, cross-tab event guard, tab-aware `createGraph`/`deleteGraph`/`startRun`/`resumeRun`
- [feat] Added `editor/src/components/TabBar.tsx` — horizontal tab bar with graph name, run status dot, close button, "+" graph picker dropdown
- [feat] `editor/src/components/EditorToolbar.tsx`: replaced graph `<select>` dropdown with `<TabBar />`, routed graph open through `openTab`
- [feat] `editor/src/App.tsx`: startup calls `restoreTabs()` after `loadGraphList()` for tab + run recovery
- [docs] Updated `docs/architecture.md` test count to 322
- [docs] Marked 6-10 tasks 1, 2, 3, 4 (partial), 5 (partial), 7 (partial) as completed in plan

## 2026-02-24 (Phase 3.75 — 6-10 backend migration, builder/decompiler, tests)
- [feat] `src/dan/executors/control_flow.py`: added `DeprecationWarning` to `IfElseExecutor.execute()` and `WhileLoopExecutor.execute()` — legacy executors still work but emit warnings (task 2-3)
- [feat] `src/dan/validation/graph.py`: added `_check_deprecated_edge_conditions()` — emits non-blocking deprecation warnings for `ControlEdge.condition` usage (task 4-3)
- [feat] `src/dan/engine/scheduler.py`, `src/dan/builder/compiler.py`: added `"deprecated"` to `_VALIDATION_WARNING_PATTERNS` so deprecation messages are non-fatal in both engine and builder
- [feat] `src/dan/server/app.py`: validate endpoint now separates deprecated messages into `warnings` list (was always `[]`)
- [feat] Created `src/dan/migration/gate_migration.py` with `migrate_if_else_to_gate()`, `migrate_while_loop_to_flat_gate()`, and `migrate_graph()` (tasks 6-1, 6-2)
- [feat] `src/dan/server/app.py`: `GET /api/graphs/{id}` applies `migrate_graph()` when `DAN_GATE_MIGRATION_ENABLED=true` env var is set (tasks 6-3, 6-5)
- [feat] `src/dan/builder/builder.py`: added `gate()` method to `WorkflowBuilder` (task 6-4)
- [feat] `src/dan/builder/compiler.py`: added `GateNode` to `_build_node()` and `"gate"` to `DEFAULT_OUTPUT_PORTS` (task 6-4)
- [feat] `src/dan/builder/decompiler.py`: added gate node decompilation — emits `wf.gate()` with conditional `gate_mode`/`max_iterations` kwargs (task 6-4)
- [test] Created `tests/test_migration/test_gate_migration.py` — 11 tests covering if_else migration, while_loop flattening, combined migration, noop on clean graphs (task 7-3)
- [test] Added 4 gate round-trip tests to `tests/test_builder/test_decompiler.py` — if_else and while gate modes, default kwarg elision (task 7-4)
- [fix] Updated `tests/test_builder/test_compiler.py` expected node type set to include `"gate"`

## 2026-02-24 (Phase 3.75 — 6-10 collapsible loop groups)
- [feat] `editor/src/types/graph.ts`: added `LoopGroup` interface and `loop_groups` field on `GraphMetadata` for visual-only loop grouping
- [feat] `editor/src/lib/graphAdapter.ts`: added `injectLoopGroups` / `stripLoopGroups` helpers for injecting/removing group nodes + synthetic edges; updated `reactFlowToDanGraph` to filter out `loopGroup` nodes
- [feat] Added `editor/src/components/LoopGroupNode.tsx` — collapsed view (compact card with handles + expand button) and expanded view (dashed amber border with collapse button)
- [feat] `editor/src/components/GraphCanvas.tsx`: registered `loopGroup` node type
- [feat] `editor/src/components/ContextMenu.tsx`: added "Create Loop Group" (when multi-select includes a while-gate) and "Ungroup Loop" (when right-clicking a grouped node)
- [feat] `editor/src/store/useGraphStore.ts`: added `loopGroups` state, `createLoopGroup`/`toggleLoopGroup`/`removeLoopGroup` actions, loop-group-aware load/save (serializes to `metadata.loop_groups`), tab snapshot integration

## 2026-02-24 (Phase 3.75 — 6-10 migration + builder + rollout)
- [feat] `src/dan/executors/control_flow.py`: added `DeprecationWarning` to `IfElseExecutor` and `WhileLoopExecutor`
- [feat] `src/dan/validation/graph.py`: added `_check_deprecated_edge_conditions()` — non-blocking warnings for `ControlEdge.condition` usage
- [feat] Created `src/dan/migration/gate_migration.py`: `migrate_if_else_to_gate()`, `migrate_while_loop_to_flat_gate()`, `migrate_graph()` helpers
- [feat] `src/dan/builder/builder.py`: added `gate()` method to `WorkflowBuilder`
- [feat] `src/dan/builder/decompiler.py`: added gate node decompilation support
- [feat] `src/dan/server/app.py`: `DAN_GATE_MIGRATION_ENABLED` env flag for optional migration on graph load
- [test] Added `tests/test_migration/test_gate_migration.py` — 11 migration tests
- [test] Added gate round-trip tests in `tests/test_builder/test_decompiler.py` — 4 tests
- [docs] Updated `README.md` with Phase 3.75 features, new API endpoints, test count (337), roadmap entry
- [docs] Updated `docs/architecture.md` with gate model, cycle-aware scheduling, migration policy, test count
- [docs] Marked 6-10 plan as completed, parent 6-phase plan as completed, both checked off in `todo.md`

## 2026-02-24 (Phase 3.75 — code review fixes)
- [fix] `editor/src/store/useGraphStore.ts`: `closeTab` now checks `dirty` state (from live store or tabCache) and shows a confirmation dialog before closing
- [fix] `editor/src/store/useGraphStore.ts`: `deleteGraph` on the last open tab now clears `tabs`, `activeTabId`, `loopGroups`, and run state, then persists to sessionStorage
- [fix] `src/dan/migration/gate_migration.py`: updated `migrate_if_else_to_gate` docstring to document that `branch→true` remapping is best-effort; added `logger.warning` on each remapped edge
- [fix] `src/dan/migration/gate_migration.py`: `migrate_while_loop_to_flat_gate` now infers exit/entry port names from body node output_ports/input_ports instead of hardcoding `result`/`input`; skips migration gracefully when entry or exit points are empty
- [fix] `src/dan/server/app.py`: unified validation warning classification — "schema safety bypassed" and "untyped data edge" messages now classified as warnings alongside "deprecated"
- [fix] `editor/src/components/ContextMenu.tsx`: moved `setSelectedNode(targetId)` from render body into `useEffect` to prevent state writes during render
- [test] Added 4 migration edge-case tests: port inference for exit/entry, empty body skip, and warning log assertion (341 tests total)
- [docs] Updated test count to 341 in `README.md` and `docs/architecture.md`

## 2026-02-24 (Phase 3.75 — 6-11 workflow UX polish)
- [feat] `src/dan/executors/llm.py`: widened `_call_llm` return type to include token usage dict; streaming path passes `stream_options={"include_usage": True}`, non-streaming reads `resp.usage`; `execute()` accumulates usage across normalization retries and includes it in `NodeResult.metadata`
- [feat] `src/dan/engine/scheduler.py`: added `_aggregate_usage()` to sum token counts from all `state.node_metadata`; `_execute()` now records `run_start_time` and emits `elapsed_seconds`, `total_prompt_tokens`, `total_completion_tokens`, `total_tokens` in `run_completed`/`run_failed` events
- [feat] `editor/src/store/useGraphStore.ts`: added `runSummary` state (captured from `run_completed`/`run_failed` event data); included in `TabSnapshot` for tab-switch persistence; cleared on run start/resume
- [feat] `editor/src/components/LogPanel.tsx`: added `RunSummaryBar` component — compact summary bar at bottom of log panel showing elapsed time and token counts on run completion/failure
- [feat] `editor/src/lib/layout.ts`: added `needsAutoLayout(nodes)` — detects degenerate positions (all same point, bounding box < 50px, or NaN/undefined)
- [feat] `editor/src/store/useGraphStore.ts`: `drillIn`, `drillOut`, `jumpToLayer` now auto-apply dagre layout when sub-graph positions are degenerate
- [feat] `editor/src/components/TabBar.tsx`: removed already-open graph filter from "+" picker — all saved graphs always shown; replaced "All graphs already open" with "No saved graphs" empty state; added duplicate tab name disambiguation with counter suffix
- [feat] `editor/src/store/useGraphStore.ts`: removed `openTab` short-circuit that redirected to existing tab with same `graphId` — each `openTab` call now creates an independent tab
- [docs] Updated plan `6-11-workflow-ux-polish.md` — patched with review findings (widened return type, aggregate from node_metadata, save-conflict note, NaN positions), marked all tasks completed
- [docs] Updated `docs/todo.md` — marked 6-11 completed
- [docs] Updated `docs/changelog.md` with implementation entry

## 2026-02-24 (Phase 3.75 — 6-11 template switch follow-up)
- [fix] `editor/src/components/TabBar.tsx`: fixed Babel parse error by parenthesizing `??`/`||` expression for tab title rendering
- [feat] `editor/src/store/useGraphStore.ts`: added `replaceActiveTabGraph(graphId)` to swap template/graph in the active tab (dirty-check confirmation, websocket disconnect, run/log/summary reset)
- [feat] `editor/src/components/TabBar.tsx`: added dual picker modes — `+` opens selected template in a new tab; `↺` replaces the current tab template in-place
- [fix] `editor/src/store/useGraphStore.ts`: cleared `runSummary` in last-tab `deleteGraph` branch and in `openTab` reset state to prevent stale summary leakage across template switches
- [test] Verified TypeScript (`npx tsc --noEmit`) and backend tests (`341 passed`)
- [docs] Updated `docs/plans/6-11-workflow-ux-polish.md` with completed sub-task 3-4 for current-tab template switching

## 2026-02-24 (Phase 3.75 — tab UX polish)
- [feat] `TabBar.tsx`: click active tab now opens a dropdown with all templates + search box (replaces `↺` button); removed `PickerMode` dual-button pattern
- [feat] `useGraphStore.ts`: `refreshTab()` action reloads current tab's graph from server, disconnects WS, resets run/log/summary state
- [feat] `useGraphStore.ts`: `closeTab` now allows closing the last tab — auto-creates a blank tab afterwards
- [feat] `useGraphStore.ts`: `openTab("blank")` creates an empty tab (no server graph) with name "blank"
- [feat] `EditorToolbar.tsx`: added refresh icon button (↻) next to Save; renamed `+ New` to `+ Blank`
- [feat] `useGraphStore.ts`: `deleteGraph` on last tab now delegates to `closeTab` which auto-opens blank tab (removed special-case empty-state branch)
- [fix] `useGraphStore.ts`: `restoreTabs` skips `loadGraph` for blank tabs (empty `graphId`)
- [fix] `useGraphStore.ts`: `loadGraphList` fallback opens a blank tab when no `last_opened` graph exists
- [test] TypeScript clean, 341 backend tests passing

## 2025-02-25 (Phase 4 — markdown agent format)
- [feat] `src/dan/loader/flow_parser.py`: flow notation parser — parses `## Flow` lines into typed `FlowStatement` objects (chain, each, loop, if). Supports Unicode/ASCII arrows, port-specific wiring with consume-once semantics, pipe operators with keyword args, quoted condition strings, line continuations, comments, and `FlowParseError` diagnostics with line/position.

## 2026-02-24 (Phase 3.75 — shared template picker)
- [feat] `TabBar.tsx`: unified template picker dropdown shared by active-tab click (replace mode) and `+ New` button (new-tab mode); rendered via `createPortal` to avoid tab-strip overflow clipping
- [feat] `TabBar.tsx`: dropdown shows search box, top 5 most-frequent templates (tracked in `localStorage` via `dan_tpl_freq`), all templates section, and "Blank" option in new-tab mode
- [feat] `TabBar.tsx`: dropdown width matches the anchor element (tab or button), with 220px minimum
- [feat] `EditorToolbar.tsx`: renamed `+ Blank` to `+ Create` (creates a new named server-side graph — distinct from the tab-level template picker)
- [test] TypeScript clean, 341 backend tests passing
