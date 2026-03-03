# 16-3: HumanNode Generalization

**Parent:** [16-execution-primitives](16-execution-primitives.md)
**Status:** not-started
**Goal:** Promote `HumanInTheLoopNode` to a first-class `HumanNode` with typed input/output schemas, multiple rendering modes (approval, form, free-text, selection, file-upload), a rendering surface abstraction that decouples the engine from the UI, and support for the "chat as renderer" pattern where the chat panel renders whichever HumanNode is active — enabling adjustable autonomy via graph topology rather than mode switches.

## Existing Baseline

| Component | Location | What exists | Gap |
|---|---|---|---|
| `HumanInTheLoopNode` | `models/control_flow.py` | `node_type: "human_in_the_loop"`, `prompt` (str), `timeout_seconds` (float\|None), `default_action` (str\|None) | No typed input/output schema; no rendering mode; single text prompt; no multi-field forms; no approval semantics |
| `HumanInTheLoopExecutor` | `executors/control_flow.py` | Awaits `context.human_input_callback(request_meta)` with prompt; handles timeout + default; merges response into outputs | Single callback interface; no rendering hint; no schema validation of human response; no multi-point coordination |
| `human_input_callback` | `engine/executor.py` `ExecutionContext` | `Callable[[dict], Awaitable[Any]] \| None` — optional async callback | Untyped; callback contract is a plain dict → Any; no rendering mode dispatch |
| `HumanInputDialog.tsx` | `editor/src/components/` | Modal popup: shows prompt text, single textarea input, submit button | Text-only; no form fields; no approval buttons; no file upload; no selection UI |
| `RunManager` human input | `server/run_manager.py` | `register_human_input_handler()`, `submit_human_input()` — maps `request_id` to asyncio.Future for pause/resume | Functional but minimal; no rendering metadata; no multi-request queuing |
| Architecture docs | `docs/architecture.md` §HumanNode | Full vision: typed I/O, chat-as-renderer, adjustable autonomy via topology, background mode = zero HumanNodes, multi-point interaction, rendering decoupling | **Prose-only** — none of the advanced features are implemented |
| `ChatPanel.tsx` | `editor/src/components/` | Chat sidebar with message send/stream, `@` mentions, mutation event handling | Not connected to HumanNode rendering; runs independently of graph execution |
| `OutputPreview.tsx` | `editor/src/components/` | Per-node output viewer with streaming text | Could display HumanNode prompts but currently only shows post-execution output |
| Engine event system | `engine/events.py` | `human_input_needed` event emitted by executor | Contains `prompt` and `request_id`; no rendering mode or schema info |
| WebSocket events | `server/run_manager.py` | Events multiplexed to WS subscribers; frontend receives and dispatches | HumanNode events reach frontend but only trigger basic modal |

## Tasks

- [ ] 1. Generalize HumanNode model
  - [ ] 1-1. Rename `HumanInTheLoopNode` to `HumanNode` with `node_type: Literal["human"]`. Add backward-compat alias: `HumanInTheLoopNode = HumanNode` and `node_type` validator that accepts `"human_in_the_loop"` as alias for `"human"` (migration helper).
  - [ ] 1-2. Add `input_schema` field (dict[str, Any] | None — JSON Schema for what the human receives). When set, the rendering surface uses this to display structured data (e.g., a draft to review, a form to fill). The node's `input_ports` define data flow; `input_schema` defines presentation.
  - [ ] 1-3. Add `output_schema` field (dict[str, Any] | None — JSON Schema for what the human must provide). When set, the rendering surface validates the response before accepting. Enables typed human responses beyond free text.
  - [ ] 1-4. Add `render_mode` field (Literal: `"text"`, `"approval"`, `"form"`, `"selection"`, `"file_upload"`, `"rich"`) — hint to the rendering surface for how to present the interaction:
    - `text`: free-text input (current behavior, default).
    - `approval`: show content + approve/reject buttons; output is `{"approved": bool, "comment": str}`.
    - `form`: render `output_schema` as form fields (text inputs, dropdowns, checkboxes).
    - `selection`: show multiple options; human picks one or more.
    - `file_upload`: accept file(s); output includes file paths/contents.
    - `rich`: full chat-like interface; human can write multi-turn responses.
  - [ ] 1-5. Add `options` field (list[str] | None) — for `"selection"` mode, the choices to present.
  - [ ] 1-6. Add `instructions` field (str, default "") — guidance text shown above the input area (richer than `prompt`; supports markdown).
  - [ ] 1-7. Keep `prompt`, `timeout_seconds`, `default_action` for backward compatibility. `prompt` becomes the title/header; `instructions` is the body.

- [ ] 2. Define rendering surface protocol
  - [ ] 2-1. Create `HumanRenderRequest` model: `request_id` (str), `node_id` (str), `node_name` (str), `render_mode` (str), `prompt` (str), `instructions` (str), `input_data` (dict — the data flowing into the HumanNode), `input_schema` (dict | None), `output_schema` (dict | None), `options` (list[str] | None), `timeout_seconds` (float | None), `default_action` (str | None).
  - [ ] 2-2. Create `HumanRenderResponse` model: `request_id` (str), `data` (dict — the human's response, validated against `output_schema`), `source` (Literal: `"human"`, `"default"`, `"timeout"`) — for audit trail.
  - [ ] 2-3. Define `HumanRenderer` protocol: `async render(request: HumanRenderRequest) -> HumanRenderResponse`. This is the contract that any rendering surface (web UI, CLI, Jupyter, API) must implement.
  - [ ] 2-4. Move `human_input_callback` from bare `Callable` to `HumanRenderer` protocol on `ExecutionContext`. Backward compat: if `human_input_callback` is a plain callable, wrap it in a `LegacyCallbackRenderer` adapter.

- [ ] 3. Update HumanNode executor
  - [ ] 3-1. Rename `HumanInTheLoopExecutor` to `HumanNodeExecutor` (keep old name as alias).
  - [ ] 3-2. Build `HumanRenderRequest` from node fields + incoming data. Include `input_schema`, `output_schema`, `render_mode`, `options`, `instructions`.
  - [ ] 3-3. Call `context.human_renderer.render(request)` instead of raw `context.human_input_callback(meta)`.
  - [ ] 3-4. Validate response against `output_schema` if set. On validation failure, re-render with error message (up to 2 retries), then accept raw or fail based on `strict_mode` flag.
  - [ ] 3-5. Emit enriched `human_input_needed` event with full `HumanRenderRequest` (including render_mode, schema, options) so frontends can build the appropriate UI.
  - [ ] 3-6. Emit `human_input_received` event with `HumanRenderResponse` (including source) for audit.

- [ ] 4. Implement web UI renderers
  - [ ] 4-1. Refactor `HumanInputDialog.tsx` to dispatch on `render_mode`:
    - `text`: current textarea (preserve existing behavior).
    - `approval`: content display area + "Approve" (green) / "Reject" (red) buttons + optional comment field.
    - `form`: dynamically generate form fields from `output_schema` (text input for strings, number input for numbers, checkbox for booleans, dropdown for enums). Use schema `title`/`description` for labels/help text.
    - `selection`: radio buttons (single select) or checkboxes (multi-select based on schema) for `options` list.
    - `file_upload`: file drop zone + browse button; read file content and include in response.
    - `rich`: multi-message interface within the dialog (mini-chat).
  - [ ] 4-2. Show `instructions` as markdown-rendered body above the input area.
  - [ ] 4-3. Show `input_data` as formatted preview (collapsible JSON or rendered template) so the human can see what the graph is presenting.
  - [ ] 4-4. Add validation feedback: if `output_schema` validation fails on submit, show field-level error messages and prevent submission.

- [ ] 5. Implement chat-as-renderer pattern
  - [ ] 5-1. When a `HumanNode` with `render_mode="rich"` (or any mode) fires during execution, route the render request to the `ChatPanel` instead of (or alongside) `HumanInputDialog`. Display the prompt as a system message in the chat thread; the human's chat response becomes the HumanNode output.
  - [ ] 5-2. Add `render_target` field to `HumanNode`: `"dialog"` (default — popup modal), `"chat"` (render in chat panel), `"both"` (show in both). This controls where the interaction appears.
  - [ ] 5-3. In `ChatPanel`, detect incoming `human_input_needed` events. When `render_target` includes `"chat"`, insert a special `HumanInputChatMessage` component that shows the prompt, input data, and an inline response area matching the `render_mode`.
  - [ ] 5-4. Wire chat response submission through `RunManager.submit_human_input()` to resolve the pending Future.
  - [ ] 5-5. Support multi-point interaction: when multiple `HumanNode`s fire during a single run (possibly at different times), queue them in the chat panel as sequential interaction points. Show progress indicator between interactions.

- [ ] 6. Implement CLI and programmatic renderers
  - [ ] 6-1. Create `CLIHumanRenderer` implementing `HumanRenderer` protocol: for `text` mode, use `input()` prompt; for `approval`, show content + `[y/n]` prompt; for `selection`, numbered list + index input; for `form`, sequential field prompts. File upload not supported in CLI (skip with warning).
  - [ ] 6-2. Create `AutoRenderer` implementing `HumanRenderer`: always returns `default_action` if set, otherwise fails. For testing and background mode.
  - [ ] 6-3. Create `ProgrammaticRenderer` implementing `HumanRenderer`: accepts a `dict[str, Any]` mapping `node_id` → pre-scripted response. For automated testing of workflows with HumanNodes.
  - [ ] 6-4. Add `human_renderer` parameter to `Engine.__init__()` and `EngineConfig`. Default: `None` (backward compat — falls back to `human_input_callback` or fails).

- [ ] 7. Extend builder API
  - [ ] 7-1. Add `wf.human(name, render_mode="text", prompt="", instructions="", output_schema=None, ...)` to `WorkflowBuilder`. Returns `NodeRef`.
  - [ ] 7-2. Add `wf.approval(name, prompt="Review and approve:", ...)` as shorthand for `wf.human(render_mode="approval")`.
  - [ ] 7-3. Add `wf.form(name, schema={...}, ...)` as shorthand for `wf.human(render_mode="form", output_schema=schema)`.
  - [ ] 7-4. Extend builder compiler/decompiler for `HumanNode`.

- [ ] 8. Extend markdown loader
  - [ ] 8-1. Support `type: human` in agent markdown. Frontmatter fields: `render_mode`, `output_schema`, `options`, `timeout_seconds`, `default_action`, `instructions` (or body as instructions). Example:
    ```
    ---
    type: human
    name: Review Draft
    render_mode: approval
    timeout_seconds: 300
    default_action: '{"approved": true}'
    ---
    Please review the following draft and approve or reject it.
    Pay attention to factual accuracy and citation completeness.
    ```
  - [ ] 8-2. Extend loader/compiler and decompiler for `HumanNode` markdown format.

- [ ] 9. Migration and backward compatibility
  - [ ] 9-1. `node_type: "human_in_the_loop"` in existing graph JSON auto-migrates to `"human"` on load. Emit deprecation warning.
  - [ ] 9-2. `HumanInTheLoopNode` class remains as alias of `HumanNode`. Existing Python code using `HumanInTheLoopNode` continues to work.
  - [ ] 9-3. Existing `human_input_callback` on `ExecutionContext` continues to work via `LegacyCallbackRenderer` adapter.
  - [ ] 9-4. Add migration helper in `dan.migration` that converts `"human_in_the_loop"` → `"human"` in graph dicts.

- [ ] 10. Validation
  - [ ] 10-1. Extend `validate_graph()`: `HumanNode.render_mode="selection"` requires non-empty `options`. `render_mode="form"` requires `output_schema`. `render_mode="approval"` should have `output_schema` with `approved` boolean field (warn if missing).
  - [ ] 10-2. Validate `output_schema` is valid JSON Schema.
  - [ ] 10-3. Warn if HumanNode has no `timeout_seconds` and no `default_action` (execution could hang indefinitely in headless mode).

- [ ] 11. Tests and documentation
  - [ ] 11-1. Unit tests: `HumanNode` serialization (new fields + backward compat), `HumanRenderRequest`/`HumanRenderResponse` models, `LegacyCallbackRenderer` adapter, `AutoRenderer`, `ProgrammaticRenderer`.
  - [ ] 11-2. Integration tests: text mode (existing behavior preserved), approval mode (approve + reject paths), form mode (schema validation pass/fail), selection mode (single/multi), timeout → default action, multi-HumanNode sequential execution.
  - [ ] 11-3. Migration tests: `"human_in_the_loop"` JSON → `"human"` load, `HumanInTheLoopNode` alias works, existing graphs with old node type run correctly.
  - [ ] 11-4. Builder round-trip tests: `wf.human()` / `wf.approval()` / `wf.form()` → build → decompile → compare.
  - [ ] 11-5. Markdown round-trip tests: human agent `.md` → compile → decompile → compare.
  - [ ] 11-6. Update `docs/architecture.md` §HumanNode: replace conceptual prose with implementation references, document render modes, rendering surface protocol.
  - [ ] 11-7. Update `docs/llm-api-guide.md`: HumanNode model reference, render modes, builder API, markdown syntax, renderer protocol.
  - [ ] 11-8. Update `docs/changelog.md`, `docs/todo.md`, and this plan as implementation progresses.

## Primary Files

- `src/dan/models/control_flow.py` — `HumanNode` (generalized), backward-compat aliases
- `src/dan/models/graph.py` — update `Node` union (replace `HumanInTheLoopNode` with `HumanNode`)
- `src/dan/engine/executor.py` — `HumanRenderRequest`, `HumanRenderResponse`, `HumanRenderer` protocol, `LegacyCallbackRenderer`, `human_renderer` on `ExecutionContext`/`EngineConfig`
- `src/dan/executors/control_flow.py` — `HumanNodeExecutor` (generalized), backward-compat alias
- `src/dan/executors/__init__.py` — register for both `"human"` and `"human_in_the_loop"` node types
- `src/dan/engine/events.py` — enriched `human_input_needed` event data, `human_input_received` event
- `src/dan/migration/` — `"human_in_the_loop"` → `"human"` graph dict migration
- `src/dan/builder/builder.py` — `wf.human()`, `wf.approval()`, `wf.form()`
- `src/dan/builder/compiler.py` — compile human node declarations
- `src/dan/builder/decompiler.py` — decompile `HumanNode`
- `src/dan/loader/parser.py` — parse `type: human` frontmatter
- `src/dan/loader/compiler.py` — compile human agent to `HumanNode`
- `src/dan/loader/decompiler.py` — decompile `HumanNode` to markdown
- `editor/src/types/graph.ts` — update `HumanNode` TS type with new fields
- `editor/src/components/HumanInputDialog.tsx` — multi-mode rendering (approval, form, selection, etc.)
- `editor/src/components/ChatPanel.tsx` — chat-as-renderer integration
- `editor/src/components/ConfigPanel.tsx` — HumanNode config fields (render_mode, schema, options)
- `tests/test_models/` — HumanNode serialization + migration
- `tests/test_engine/` — HumanNode execution integration tests
- `tests/test_builder/` — human builder round-trip
- `tests/test_loader/` — human markdown round-trip

## Decisions

- **Rename with alias, don't deprecate.** `HumanInTheLoopNode` is a mouthful and doesn't match the architecture vision. `HumanNode` with `node_type: "human"` is cleaner. The old name remains as an alias for zero-breakage migration.
- **Rendering surface is a protocol, not a framework.** `HumanRenderer` is a single async method: `render(request) -> response`. Any surface that implements this contract works. The engine doesn't import UI code.
- **`render_mode` is a hint, not a contract.** The rendering surface should respect it but may fall back gracefully (e.g., CLI renders `"form"` as sequential prompts). The engine doesn't validate that the renderer matches the mode.
- **`output_schema` enables validation.** Unlike the current untyped callback, the generalized HumanNode can validate human responses against a JSON Schema — catching errors at input time rather than downstream. This is the same output normalization principle applied to human responses.
- **Chat-as-renderer is opt-in.** `render_target: "dialog"` preserves the current modal behavior. Users explicitly choose `"chat"` for the in-chat interaction pattern. This avoids surprising UX changes.
- **Background mode is zero HumanNodes.** Not a special engine mode — just a graph that has no HumanNode instances. `AutoRenderer` + `default_action` enables "semi-background" where human checkpoints have safe defaults.

## Notes

- Architecture docs (§HumanNode) already describe this vision in detail: "the human is a node in the graph", "chat is rendering", "adjustable autonomy is topology". This plan converts that prose into implementation tasks.
- The coding assistant PoC needs chat-as-renderer: Ask mode = one HumanNode for the question; Agent mode = periodic HumanNodes for approval; Debug mode = HumanNode for hypothesis confirmation. 16-3 is a direct enabler.
- Multi-point interaction (task 5-5) is a stretch goal — the core value is in typed schemas and render modes. Prioritize tasks 1-4 and 9, then 5-8.
- `ProgrammaticRenderer` (task 6-3) is essential for testing. Without it, every test that touches HumanNode needs mock callbacks. With it, tests declare responses upfront.
