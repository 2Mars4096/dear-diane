# 8-1: Format Design & Parser

**Parent:** [8-markdown-agent-format](8-markdown-agent-format.md)
**Status:** completed
**Goal:** Define the agent and workflow markdown file formats, implement the parser, flow notation parser, port type inference, and format versioning to produce intermediate parsed representations (`AgentSpec`, `WorkflowSpec`, `FlowStatement`) that the compiler (8-2) consumes.

## Tasks

- [x] 1. Package structure (`src/dan/loader/`)
  - [x] 1-1. Create `src/dan/loader/__init__.py` with public API: `load(path: str | Path) -> Graph`, `load_agents(directory: str | Path) -> dict[str, AgentSpec]`
  - [x] 1-2. Create `src/dan/loader/models.py` — intermediate parsed representations: `AgentSpec` (parsed from agent `.md`), `WorkflowSpec` (parsed from workflow `.md`), `FlowStatement` (parsed flow lines)
  - [x] 1-3. Create `src/dan/loader/parser.py` — markdown file parser (frontmatter extraction + section parsing)

- [x] 2. Agent file format specification
  - [x] 2-1. YAML frontmatter schema — shared fields: `type` (literal: `llm`, `tool`, `code`, `human`, `router`, `composite`), `model` (string, optional), `temperature` (float, optional), `max_tokens` (int, optional), `system_prompt` (string, optional — alternative to `## System` section), `output_schema` (inline JSON Schema, optional), `retry_policy` (object, optional — mirrors `RetryPolicy` model: `max_retries`, `backoff`, `fallback_model`, `on_failure`). Type-specific fields: `tool_id` (string, required for `type: tool`), `tool_config` (dict, optional for `type: tool`), `route_descriptions` (dict of `route_name: description`, required for `type: router`), `timeout_seconds` (float, optional for `type: human`), `default_action` (string, optional for `type: human`)
  - [x] 2-2. Port declaration via blockquotes: `> Accepts: topic (string), papers (Paper[])` and `> Returns: draft (string), figures (Figure[])` — each port is `name (type)` comma-separated
  - [x] 2-3. Markdown body = prompt template. `{variable}` placeholders become input ports. Support multi-line prompts with full markdown formatting.
  - [x] 2-4. Optional `## System` section — if present, its content becomes the `system_prompt` field (overrides frontmatter `system_prompt`)
  - [x] 2-5. Parse agent file into `AgentSpec` model: frontmatter dict, port lists, prompt body string, system prompt string, source location (file path + line numbers)

- [x] 3. Workflow file format specification
  - [x] 3-1. YAML frontmatter: `name` (string), `description` (string, optional), `format_version` (int, default 1), `tags` (list of strings, optional)
  - [x] 3-2. `## Agents` section — markdown list of agent references: `- [agent_name](path/to/agent.md)` or `- [agent_name](path/to/agent.md) — optional description`. Agent name becomes the node ID.
  - [x] 3-3. `## Flow` section — one flow statement per line using arrow notation. Each line describes a connection or control-flow pattern.
  - [x] 3-4. Optional `## Context` section — shared context declarations: `- key_name: description (mode: read|write|append)`
  - [x] 3-5. Parse workflow file into `WorkflowSpec` model: frontmatter, agent list (name → file path mapping), flow statements, context declarations, source locations

- [x] 4. Flow notation parser
  - [x] 4-1. Basic chaining: `agent_a → agent_b → agent_c` — creates DataEdges between default ports
  - [x] 4-2. Port-specific wiring: `agent_a.outline → agent_b.input_outline` — creates DataEdge between named ports
  - [x] 4-3. Fan-out: `agent_a | each(agent_b, parallel: 4)` — creates ForEachNode wrapping agent_b, items from agent_a's default output
  - [x] 4-4. Loop: `agent_a | loop(agent_b, until: "verdict == 'accept'", max: 5)` — creates GateNode (while) wrapping agent_b with exit condition
  - [x] 4-5. Conditional: `agent_a | if("score > 0.8", then: agent_b, else: agent_c)` — creates GateNode (if_else) routing to agent_b or agent_c
  - [x] 4-6. Parse each flow line into `FlowStatement` model (union type: `ChainStatement`, `EachStatement`, `LoopStatement`, `IfStatement`)
  - [x] 4-7. Support line continuations (trailing `\` or indented continuation) for long flow statements
  - [x] 4-8. Error recovery — parser produces diagnostics (line number, column, error message) for malformed flow lines rather than crashing

- [x] 5. Port type inference
  - [x] 5-1. Name-based suffix rules: `items[]` or `sections[]` → array type, bare name → string, `count` / `num_*` / `*_count` → integer, `is_*` / `has_*` / `*_flag` → boolean
  - [x] 5-2. Explicit type annotations in blockquotes: `> Accepts: topic (string), papers (Paper[])` — the `(type)` part is parsed and validated
  - [x] 5-3. Implicit ports from `{variable}` placeholders in prompt body — each unique `{name}` that doesn't match a declared `> Accepts` port creates an implicit string input port
  - [x] 5-4. Default output port: if no `> Returns` is declared, infer a single `text` output port (matching LLMOperator default)

- [x] 6. Format versioning
  - [x] 6-1. `format_version: 1` required in workflow frontmatter (default if omitted)
  - [x] 6-2. Parser validates version on load — unknown versions produce a clear error with supported range
  - [x] 6-3. Document version evolution policy: patch-level changes (new optional fields) don't bump version; breaking changes (renamed fields, removed syntax) increment version

- [x] 7. Tests
  - [x] 7-1. Unit: parse agent file — minimal (frontmatter + body only), full (all fields, ports, system section)
  - [x] 7-2. Unit: parse workflow file — minimal, full with agents + flow + context
  - [x] 7-3. Unit: flow notation parser — each statement type (chain, each, loop, if), port-specific wiring, line continuations
  - [x] 7-4. Unit: port type inference — suffix rules, explicit types, implicit from placeholders, default output
  - [x] 7-5. Unit: error recovery — malformed frontmatter, invalid flow syntax, unknown agent references
  - [x] 7-6. Unit: format version validation — supported version, unsupported version, missing version (defaults to 1)
  - [x] 7-7. Fixture files: create `tests/fixtures/markdown/` with sample agent and workflow `.md` files for test cases

## Decisions

- (filled in during execution)

## Notes

- YAML frontmatter parsing: split on `---` delimiters + `yaml.safe_load()`. PyYAML is already a transitive dependency (via Pydantic). No need for the `python-frontmatter` package.
- The `{variable}` placeholder syntax in prompt bodies mirrors `dan.builder`'s f-string magic. The compiler (8-2) resolves these to input ports and DataEdges.
- Flow notation is intentionally simple — it covers the 80% case. Complex wiring (multiple edges between same nodes, conditional context edges) requires the Python builder or visual editor.
- Agent files are self-contained — they can be read and understood without the workflow file. The workflow file only adds wiring.
- `type: composite` agents are covered in 8-3 (advanced features), not here. This plan covers the 6 atomic types.
