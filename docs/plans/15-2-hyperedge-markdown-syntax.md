# 15-2: Hyperedge Markdown Syntax

**Parent:** [15-behavior-modifiers](15-behavior-modifiers.md)
**Status:** completed
**Goal:** Define a markdown file format for skills and rules, add reference syntax to workflow markdown files for attaching hyperedges with scope, extend the loader/compiler/decompiler for round-trip fidelity, and update the builder API and visual editor to support hyperedge authoring.

## Existing Baseline

| Component | Location | What exists | Gap |
|---|---|---|---|
| Markdown agent format | `loader/parser.py` | Frontmatter (`name`, `type`, `model`, `system_prompt`, etc.) + `## Ports`, `## Context`, `## Flow` sections | No skill/rule file type; no hyperedge references |
| Markdown workflow format | `loader/compiler.py` | `## Agents` section references agent `.md` files; `## Flow` section wires them | No `## Skills`, `## Rules`, or `## Hyperedges` section |
| `AgentSpec` IR | `loader/models.py` | Parsed agent representation with ports, context, prompts | No hyperedge-related fields |
| `WorkflowSpec` IR | `loader/models.py` | Parsed workflow representation with agents and flow statements | No hyperedge references |
| Decompiler | `loader/decompiler.py` | Graph → markdown (agent files + workflow file) | No hyperedge emission |
| Builder API | `builder/builder.py` | `wf.llm()`, `wf.tool()`, `wf.composite()`, etc. | No `wf.skill()`, `wf.rule()`, `wf.hyperedge()` |
| Builder compiler | `builder/compiler.py` | Compiles builder state to `Graph` model | No hyperedge compilation |
| Builder decompiler | `builder/decompiler.py` | Graph → Python builder code | No hyperedge decompilation |
| Visual editor palette | `editor/src/components/NodePalette.tsx` | Categorized node types | No hyperedge/skill/rule category |
| Visual editor config | `editor/src/components/ConfigPanel.tsx` | Per-node property editor | No hyperedge attachment UI |
| Editor graph types | `editor/src/types/graph.ts` | TypeScript types mirroring `dan_graph_v1` | No `Hyperedge` type |
| `SKILL_LIBRARY` | `server/skill_library.py` | 2 hardcoded skills as dicts | Not file-based; not referenceable from markdown |
| `TOOL_PORT_MANIFESTS` | `server/graph_mutator.py` | Port declarations for tool nodes | No parallel manifest pattern yet for hyperedge mutation ops (add/edit/remove) |

## Tasks

- [ ] 1. Define skill/rule markdown file format
  - [ ] 1-1. Define skill file format (`.md`): frontmatter with `type: skill`, `name`, `description`, `hook: pre_prompt` (default), `tags` (list), `attach_to_type` (list, optional), `attach_to_tags` (list, optional), `propagate` (bool, default true). Body (after frontmatter) is the skill content — injected text. Example:
    ```
    ---
    type: skill
    name: Scientific Writing
    hook: pre_prompt
    attach_to_tags: [writing, review]
    ---
    You are an expert scientific writer following INFORMS conventions...
    ```
  - [ ] 1-2. Define rule file format (`.md`): frontmatter with `type: guardrail | style | override`, `name`, `description`, `hook` (required — `post_output`, `tool_call`, `validation`, or `pre_prompt`), `severity` (info/warning/error, default warning), `block_on_fail` (bool, default false), `attach_to*` selectors as above. Body is the rule content — constraint text (for pre_prompt/post_output), expression (for validation), or override spec (for tool_call). Example:
    ```
    ---
    type: guardrail
    name: No PII in output
    hook: post_output
    severity: error
    block_on_fail: true
    attach_to_type: [llm_operator]
    ---
    Output must not contain personal identifiable information (names, emails, SSNs, phone numbers).
    Reject if any PII patterns are detected.
    ```
  - [ ] 1-3. Define override file format: body contains a YAML or JSON block specifying the override behavior (e.g., `replace_tool`, `modify_args`, `require_approval`). Parse body as structured override spec, not free text.
  - [ ] 1-4. Validate file format: `type` must be one of `skill`, `guardrail`, `style`, `override`. `hook` must be compatible with type (same rules as 15-1 task 7-2). Attachment must specify at least one non-empty selector (`attach_to*`) or `attach_globally: true`.

- [ ] 2. Add hyperedge references to workflow markdown
  - [ ] 2-1. Add `## Skills` and `## Rules` sections to workflow markdown format. Each line references a skill/rule file with optional scope override:
    ```
    ## Skills
    - scientific_writing.md
    - informs_latex.md -> @tags(latex)
    
    ## Rules
    - no_pii.md -> @type(llm_operator)
    - shell_approval.md -> @nodes(code_runner, shell_exec)
    ```
  - [ ] 2-2. Define scope override syntax: `-> @nodes(id1, id2)`, `-> @type(type1, type2)`, `-> @tags(tag1, tag2)`, `-> @subgraph(composite_id)`, `-> @global`. Default behavior: workflow override replaces file-level selectors unless `@merge_scope(...)` is explicitly requested in a later extension.
  - [ ] 2-3. If no scope override, use the selectors from the skill/rule file's own frontmatter.
  - [ ] 2-4. Support inline skill/rule definitions (no separate file) for simple cases:
    ```
    ## Skills
    - inline: "Always respond in formal academic English" -> @type(llm_operator)
    ```

- [ ] 3. Extend loader/compiler
  - [ ] 3-1. Add `HyperedgeSpec` to `loader/models.py` IR: `name`, `hyperedge_type`, `hook`, `content`, `config`, `selectors` (attachment), `source_file` (path).
  - [ ] 3-2. Add `load_hyperedge(path) -> HyperedgeSpec` to `loader/parser.py`: parse skill/rule `.md` file frontmatter + body. Validate format per task 1-4.
  - [ ] 3-3. Extend `WorkflowSpec` with `hyperedges: list[HyperedgeSpec]`.
  - [ ] 3-4. Extend `loader/compiler.py` `compile_workflow()`: parse `## Skills` and `## Rules` sections, load referenced files, apply scope overrides, compile `HyperedgeSpec` → `Hyperedge` model instances, add to `Graph.hyperedges`. Generate deterministic `id` from source path + normalized scope selectors (+ inline content hash when applicable) to avoid collisions.
  - [ ] 3-5. Diagnostics: warn if referenced skill/rule file not found, warn if scope override references unknown node names, error if file has invalid format.
  - [ ] 3-6. Support relative paths for skill/rule files (relative to workflow file directory). Support absolute paths with a `skills/` or `rules/` convention.

- [ ] 4. Extend decompiler
  - [ ] 4-1. Extend `loader/decompiler.py`: decompile `Graph.hyperedges` → separate skill/rule `.md` files + `## Skills` / `## Rules` sections in workflow `.md`.
  - [ ] 4-2. Group hyperedges by type: skills into `## Skills`, guardrails/styles/overrides into `## Rules`.
  - [ ] 4-3. Generate attachment scope syntax from selectors. Omit scope override if the hyperedge's selectors match what would be inferred from the file.
  - [ ] 4-4. For inline-origin hyperedges (no `source_file`), emit as inline definitions.
  - [ ] 4-5. Round-trip test: graph → markdown → graph → compare. Hyperedge content, selectors, and config must survive.

- [ ] 5. Extend builder API
  - [ ] 5-1. Add `wf.skill(name, content, attach_to=None, attach_to_type=None, attach_to_tags=None, attach_to_subgraph=None, propagate=True) -> HyperedgeRef` to `WorkflowBuilder`. Returns a reference for further configuration.
  - [ ] 5-2. Add `wf.rule(name, rule_type, hook, content, severity="warning", block_on_fail=False, ...) -> HyperedgeRef` for guardrails, styles, and overrides.
  - [ ] 5-3. Add `wf.hyperedge(...)` as a generic low-level method accepting all `Hyperedge` fields.
  - [ ] 5-4. Extend builder compiler (`builder/compiler.py`): compile `HyperedgeRef` objects into `Hyperedge` models in `Graph.hyperedges`.
  - [ ] 5-5. Extend builder decompiler (`builder/decompiler.py`): emit `wf.skill(...)` / `wf.rule(...)` calls for each graph hyperedge.
  - [ ] 5-6. Round-trip test: builder code → graph → decompile → builder code → graph → compare.

- [ ] 6. Extend visual editor
  - [ ] 6-1. Add `Hyperedge` type to `editor/src/types/graph.ts` mirroring the Python model.
  - [ ] 6-2. Add "Skills & Rules" category to `NodePalette.tsx` (or as a separate section below node palette). List available hyperedges with add button that creates a graph-level hyperedge with default scope.
  - [ ] 6-3. Add hyperedge panel to `ConfigPanel.tsx`: when a hyperedge is selected, show name, type, hook, content (editable textarea), attachment selectors (multi-select chips for nodes/types/tags), propagate toggle, severity/block_on_fail for guardrails.
  - [ ] 6-4. Visual overlay: show which nodes are affected by selected hyperedge (highlight or badge). Show hyperedge icon/indicator on nodes that have attached hyperedges.
  - [ ] 6-5. Extend graph adapter (`graphAdapter.ts`): serialize/deserialize `hyperedges` field in DAN↔React Flow conversion.
  - [ ] 6-6. Extend `graph_mutator.py` mutation operations: `add_hyperedge`, `remove_hyperedge`, `edit_hyperedge`. Wire through chat mutation pipeline.

- [ ] 7. Migrate hardcoded skills to file-based
  - [ ] 7-1. Convert `SKILL_LIBRARY` entries to `.md` files in a `skills/` directory (alongside `examples/` or in a new `src/dan/skills/` package).
  - [ ] 7-2. Update `ApplySkill` to resolve from file-based skills as well as inline library.
  - [ ] 7-3. Update paper-writing and vibe-research examples to reference skill files in their workflow definitions.

- [ ] 8. Tests and documentation
  - [ ] 8-1. Unit tests: skill/rule `.md` file parsing, workflow `## Skills`/`## Rules` section parsing, scope override parsing, inline definitions, `HyperedgeSpec` model validation.
  - [ ] 8-2. Compiler integration tests: full workflow with skill/rule references → graph with populated `hyperedges` field. Diagnostics for missing files, bad formats.
  - [ ] 8-3. Decompiler round-trip tests: graph with hyperedges → markdown files → graph → compare.
  - [ ] 8-4. Builder round-trip tests: builder code with `wf.skill()`/`wf.rule()` → graph → decompile → compare.
  - [ ] 8-5. Update `docs/llm-api-guide.md`: skill/rule file format, workflow reference syntax, builder API, editor workflow.
  - [ ] 8-6. Update `docs/architecture.md` §Hyperedges: add file format reference, loader/compiler pipeline.
  - [ ] 8-7. Update `docs/changelog.md`, `docs/todo.md`, and this plan as implementation progresses.

## Primary Files

- `src/dan/loader/models.py` — add `HyperedgeSpec` IR model
- `src/dan/loader/parser.py` — add `load_hyperedge()` parser for skill/rule `.md` files
- `src/dan/loader/compiler.py` — parse `## Skills`/`## Rules` sections, compile to `Graph.hyperedges`
- `src/dan/loader/decompiler.py` — emit hyperedge files and workflow sections
- `src/dan/builder/builder.py` — `wf.skill()`, `wf.rule()`, `wf.hyperedge()` methods
- `src/dan/builder/refs.py` — `HyperedgeRef` class
- `src/dan/builder/compiler.py` — compile hyperedge refs to `Hyperedge` models
- `src/dan/builder/decompiler.py` — decompile graph hyperedges to builder calls
- `editor/src/types/graph.ts` — `Hyperedge` TypeScript type
- `editor/src/components/NodePalette.tsx` — hyperedge palette section
- `editor/src/components/ConfigPanel.tsx` — hyperedge config editor
- `editor/src/lib/graphAdapter.ts` — hyperedge serialization
- `src/dan/server/graph_mutator.py` — `add_hyperedge`/`remove_hyperedge`/`edit_hyperedge` mutation ops
- `src/dan/server/skill_library.py` — deprecate in favor of file-based skills
- `src/dan/skills/` *(new)* — file-based skill `.md` files
- `tests/test_loader/` — hyperedge parsing, compilation, round-trip tests
- `tests/test_builder/` — hyperedge builder tests

## Decisions

- **One file per skill/rule.** Each skill or rule is a standalone `.md` file — self-documenting, version-controllable, and shareable. Inline definitions are a convenience, not the primary format.
- **Workflow references, not embedding.** Workflow files reference skill/rule files by path, not by embedding their content. This keeps workflow files concise and enables reuse across workflows.
- **Scope override at reference site.** When a workflow references a skill file, it can override the attachment scope. This allows the same skill to be applied differently in different workflows.
- **Separate sections, not mixed with flow.** `## Skills` and `## Rules` are top-level sections in the workflow file, not inline with `## Flow`. This mirrors how `## Agents` and `## Flow` are separate.
- **Builder API mirrors the model.** `wf.skill()` and `wf.rule()` are thin wrappers that create `Hyperedge` models. No magic — the builder is a convenience API.
- **Editor shows, doesn't hide.** Hyperedges are visible graph-level objects in the editor, not hidden metadata. Users can see, edit, and manage them explicitly.

## Notes

- This plan depends on 15-1 for the `Hyperedge` model and `Graph.hyperedges` field.
- The `skills/` directory convention should be documented in `architecture.md` directory structure.
- Phase 10 "Lightweight skills (prompt injection)" backlog item is fully superseded by this plan. Mark as superseded once complete.
- The markdown file format is designed to be LLM-generatable — minimal syntax, natural language content body, simple frontmatter.
