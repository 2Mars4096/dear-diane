# Known Issues & Failed Approaches

## Open Bugs

- **Vibe research LLM calls may fail with provider quota/auth errors**: External dependency issue (`vectorengine.ai` returning 401/403 such as `Token not provided` or `insufficient_quota`). Workflow currently continues with fallback-safe outputs, but strategy generation/backtests degrade when LLM nodes fail.
- **LLM-generated factor code may violate `merge_asof` sorted-key contract**: Some generated `build_factor` scripts still emit unsorted joins (`ValueError: left keys must be sorted`). Prompt guidance improved, but this remains model-output variability until stronger validation/repair is added.

## Resolved Bugs

- **Custom strategy scripts crashed with `KeyError: ['ret']`**: Fixed — `load_crsp()` now normalizes a canonical `ret` alias (from `return_month` or `return_ex_div_month`) and keeps `mktcap`/`market_cap_month` aliases aligned. This matches strategy-coder contract and prevents `build_factor(...).dropna(subset=["ret"])` failures.
- **Vibe run crashed when saving checkpoints (`ValueError: Circular reference detected`)**: Fixed — removed self-referential strategy output (`out["result"] = out`) from `_run_strategy_script`, so checkpoint JSON serialization no longer encounters recursive dictionaries.
- **Generated strategy code failed with `NameError: name 'next' is not defined`**: Fixed — added `iter` and `next` to the sandbox/code executor allowed builtins used by `execute_python()`.
- **`Engine.run(inputs=...)` lost on InputNode variables**: Fixed — scheduler now injects virtual run inputs into `InputNode.variables` (not just `input_ports`), so workflow inputs like `start_year/end_year/max_factors` propagate correctly.
- **While loop body ran before initial gate continue signal**: Fixed — skip logic for `gate.continue/loop` now only bypasses gate-source checks when virtual loop-feedback inputs are present, preventing premature first-pass body execution.
- **Drop position wrong when zoomed/panned**: Fixed — replaced manual `clientX - bounds.left` with `screenToFlowPosition()` from `useReactFlow()` in `GraphCanvas.tsx`.
- **Node ID collisions after page refresh**: Fixed — replaced module-level `_counter` with `Date.now()` + random suffix in `graphAdapter.ts`.
- **Delete key doesn't fire reliably**: Fixed — removed wrapper `onKeyDown` handler, added `deleteKeyCode={["Delete", "Backspace"]}` prop to `<ReactFlow>` in `GraphCanvas.tsx`.
- **`onConnect` creates mismatched edge IDs**: Fixed — extracted `Date.now()` to a single `edgeId` const in `useGraphStore.ts`.
- **`hasBodyGraph` in App.tsx uses useCallback, should be useMemo**: Fixed — changed to `useMemo` returning a boolean, updated call site from `hasBodyGraph()` to `hasBodyGraph`.
- **`Object.groupBy` compatibility in `NodePalette`**: Fixed — replaced `Object.groupBy` with a typed `reduce` grouping implementation, removing ES2024 dependency.
- **Read-only drill-in bypassable**: Fixed — `NodePalette` and `ConfigPanel` now disable all add/edit/drag actions when `layerStack.length > 0`.
- **Template port contract mismatch**: Fixed — LLM template output port renamed to `text` (matching executor), ReAct condition to `True`, Plan-Execute given proper port mappings.
- **`startRun()` proceeds after failed save**: Fixed — `saveGraph()` returns boolean, `startRun` aborts if `false`.
- **Edge type edit doesn't update styling**: Fixed — `updateEdgeData` syncs top-level `animated`, `label`, `style.stroke` when `edge_type` changes.
- **Subgraph events lack hierarchy tags**: Fixed — `layer_path` threaded through `ExecutionContext` and emitted with every event.
- **LogPanel shows node IDs instead of names**: Fixed — `nodeNameMap` reads `data.name` (not `data.label`).
- **Paper workflow LaTeX compile fails on missing INFORMS assets/citation keys**: Fixed — `compile_latex` now auto-fetches `informs3.cls`, normalizes TeX for `plainnat` compatibility (`hyperref`, `\newblock`), and auto-fills missing BibTeX keys with placeholder entries.

## Known Limitations

- **No graph validation in editor**: The frontend saves graphs without running the Python validation rules (`validate_graph`). Invalid graphs (missing entry points, broken edges) can be saved and will fail at run time. Validation feedback in the editor is a future enhancement.
- **Single-user only**: The run manager holds state in memory. Multiple browser tabs can connect to the same run via WebSocket, but there's no multi-user session isolation.
- ~~**No error feedback — silent API failures**~~: Resolved in Phase 3.5 — toast notification system added; all store actions wrapped in try/catch with error toasts.
- ~~**No loading states**~~: Resolved in Phase 3.5 — `loadingGraph`/`savingGraph` flags with spinners in `EditorToolbar`.
- ~~**No connection validation**~~: Resolved in Phase 3.5 — `isValidConnection` callback added in `GraphCanvas` (rejects self-connections, duplicates).
- **ConfigPanel is a generic field dump**: Iterates all non-skipped fields without logical grouping. JSON fields have a raw textarea with silent `JSON.parse` catch — no validation feedback. No way to add/remove/rename ports. (Partially improved: edge config is now editable with type-specific fields.)
- ~~**Fixed-height bottom panel**~~: Resolved in Phase 3.5 — resizable panels via `allotment`.
- ~~**Two header bars waste vertical space**~~: Resolved in Phase 3.5 — merged into single `EditorToolbar`.
- ~~**No keyboard shortcuts**~~: Resolved in Phase 3.5 — Cmd+S save shortcut added.
- **No undo/redo**: React Flow supports undo/redo via state snapshots, but it's not wired up yet.
- **App title/favicon are Vite defaults**: HTML title is "editor", favicon is `vite.svg`. Basic branding gap.
- **Node.js version warning**: Vite 7.x and `@vitejs/plugin-react` require Node.js 20.19+ or 22.12+. Current dev environment runs 20.17 — builds succeed with warnings but may break on future Vite updates. Fix: upgrade Node.js.

## Phase 7 Review Fixes (2026-02-25)

- **Chat mutation events silently dropped**: Fixed — `ChatPanel.tsx` now handles `chat_mutation` WebSocket events.
- **`/run` chat commands ignored**: Fixed — `app.py::chat_message` now dispatches to `parse_run_command` + `build_scoped_graph`.
- **Token usage keys mismatched (backend→frontend)**: Fixed — `_normalize_usage()` maps `prompt_tokens`/`completion_tokens` → `prompt`/`completion`.
- **`_chat_streams` memory leak**: Fixed — entries now carry monotonic timestamps; stale entries reaped on each POST.
- **Markdown link XSS via `javascript:` URLs**: Fixed — `ChatMessage.tsx` allowlists `https:`, `http:`, `mailto:`, `#`.
- **Mention autocomplete fires mid-word**: Fixed — `findMentionQuery` requires whitespace or start-of-input before `@`.
- **Workflow mention click was no-op**: Fixed — `navigateToMention` calls `store.openTab()`.

## Failed Approaches (do not retry)

(none yet)
