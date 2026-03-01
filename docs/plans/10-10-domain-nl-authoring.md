# 10-10: Domain NL Authoring — Templates, Skills, and Intent Quality

**Parent:** [10-chatbox-nl-workflow](10-chatbox-nl-workflow.md)
**Status:** not-started
**Goal:** Close the gap between "the NL system can produce a graph" and "the NL system produces a *correct, domain-calibrated* graph for real tasks." Specifically: a user who says "I have PDFs at path X and data at path Y, help me write an INFORMS paper for Management Science" should get a complete, runnable, expert-calibrated workflow — not a generic 6-node review_loop+chain stub.

## Context

10-9 delivered build-from-intent mode (`BUILD_FROM_INTENT_PROMPT`, template registry, empty-graph bootstrap). The gap that remains:

1. **Pattern library is too coarse.** The 4 patterns (`chain`, `review_loop`, `fan_out`, `rag_qa`) can't express "ingest PDFs from a path into RAG, then use them for literature grounding." There is no `data_ingest` pattern.
2. **Domain templates are shallow.** `WORKFLOW_TEMPLATES["paper_writing"]` produces a 6-node graph (disconnected chain + review_loop, generic prompts). The real `examples/paper_writing.py` is a 15+ node workflow with RAG, ForEach parallel drafting, HumanInTheLoop interview, compile_latex exit node, and INFORMS-specific prompts.
3. **Node prompts are generic.** Producing "Write a full draft based on: {input}" is not the same as producing prompts calibrated for Management Science submission requirements.
4. **No skill injection.** There is no way to say "apply INFORMS style to all LLM nodes." The hyperedge/skill design exists in architecture.md but is not implemented.
5. **Multi-turn clarification is absent.** 10-9 explicitly deferred single-turn → multi-turn clarification. For complex domain workflows this causes mismatch between intent and produced graph.

## Tasks

- [ ] 1. **`data_ingest` pattern**
  - [ ] 1-1. Add `data_ingest` to `PATTERN_LIBRARY` in `graph_mutator.py`: `InputNode(pdf_dir) → ToolOperator(list_directory) → ForEach → ToolOperator(pdf_read) → RAGOperator`. Params: `rag_name`, `collection`, `input_var` (default: `pdf_dir`).
  - [ ] 1-2. Add `"data_ingest"` to the `pattern` enum in `_build_mutation_tool_schema()` in `chat_manager.py`.
  - [ ] 1-3. Update `BUILD_FROM_INTENT_PROMPT` intent→pattern mapping: "For 'I have PDFs at path X' or 'literature from files': use `data_ingest` pattern."
  - [ ] 1-4. Update `SYSTEM_PROMPT_TEMPLATE` available patterns section to include `data_ingest`.
  - [ ] 1-5. Unit test: `expand_pattern("data_ingest", {...})` → valid graph with InputNode, ToolOperator, ForEach, ToolOperator, RAGOperator, correct edges.

- [ ] 2. **Rich INFORMS paper-writing domain template**
  - [ ] 2-1. Extend `WORKFLOW_TEMPLATES["paper_writing"]` (or add `"informs_paper_writing"`) to produce a complete pipeline: `InputNode(topic, pdf_dir, data_path)` → `data_ingest(pdf_dir)` → `ToolOperator(search_papers)` → `LLMOperator(literature_survey)` → `LLMOperator(outline)` → `ForEach(section_drafting)` → `GateNode(review_loop, max_iterations=3)` → `LLMOperator(latex_assembly)` → `ToolOperator(compile_latex)` → `ToolOperator(save_paper)`.
  - [ ] 2-2. Add `HumanInTheLoop` node for research positioning interview (between outline and section drafting), gated behind an `include_human_review` param (default: true).
  - [ ] 2-3. Node prompts in the template should be INFORMS-specific (not generic): section drafter prompt references MS contribution framing; reviewer prompt references MS methodology standards.
  - [ ] 2-4. Register the template in `BUILD_FROM_INTENT_PROMPT`'s "Available templates" section.
  - [ ] 2-5. Add `"rag_research"` template: `data_ingest` → `RAGOperator` → `LLMOperator(synthesize)` — useful for any "I have papers, help me understand them" intent.

- [ ] 3. **Lightweight skills (prompt injection)**
  - [ ] 3-1. Define `SKILL_LIBRARY: dict[str, dict]` in `chat_manager.py` (or new `src/dan/server/skill_library.py`). Each skill: `{name, description, tags, inject_as: "system"|"prepend", text}`. Start with two skills: `management_science_writing` and `informs_latex_style`.
  - [ ] 3-2. `management_science_writing` skill text: MS submission guidelines summary — contribution positioning, mathematical modeling conventions, empirical strategy requirements, reviewer expectations. ~200 tokens.
  - [ ] 3-3. `informs_latex_style` skill text: INFORMS LaTeX template conventions — `informs3.cls`, `plainnat` bibliography, figure/table captions, section ordering.
  - [ ] 3-4. Add `apply_skill` op to `GraphOperation` union and `GraphMutator`: given a skill name and a target (node_ids list, or `tag:"writing"`), it runs `edit_node` on each matching node to prepend the skill text to `system_prompt` (or `prompt_template` if no `system_prompt` field).
  - [ ] 3-5. Add `apply_skill` to `MUTATION_TOOL_SCHEMA` so the LLM can emit it: `{"op": "apply_skill", "skill": "management_science_writing", "target_tag": "writing"}`.
  - [ ] 3-6. Update `BUILD_FROM_INTENT_PROMPT`: "Available skills: management_science_writing, informs_latex_style. Use apply_skill op when user mentions a journal or domain style."
  - [ ] 3-7. Tag LLM nodes in `informs_paper_writing` template with `tags: ["writing"]` and `tags: ["review"]` so `apply_skill(target_tag="writing")` hits the right nodes.
  - [ ] 3-8. Unit tests: `apply_skill("management_science_writing", target_tag="writing")` → nodes with `tags=["writing"]` have skill text prepended to `system_prompt`.

- [ ] 4. **`BUILD_FROM_INTENT_PROMPT` quality improvements**
  - [ ] 4-1. Add explicit multi-step composition examples to the prompt:
    - "Paper writing with data + PDFs: `data_ingest` → `rag_operator` → `outline` (llm) → `fan_out` (sections) → `review_loop` → `compile_latex` (tool)"
    - "Data analysis + report: `file_read` (tool) → `code_operator` (preprocess) → chain of llm nodes → `save_paper` (tool)"
  - [ ] 4-2. Add `HumanInTheLoop` guidance: "Add `human_in_the_loop` node when user says 'check with me', 'I want to approve', 'pause for review'."
  - [ ] 4-3. Add journal/domain → template+skill mapping: "INFORMS / Management Science → use `informs_paper_writing` template + `apply_skill(management_science_writing)`."
  - [ ] 4-4. Add file path handling guidance: "When user mentions 'data at path X' or 'PDFs at Y', create an `InputNode` with a variable for that path, then wire to `data_ingest` or `file_read` tool."

- [ ] 5. **Multi-turn clarification in build mode** *(deferred from 10-9)*
  - [ ] 5-1. When build mode intent is ambiguous (no recognized template or pattern match), LLM responds with clarifying questions instead of producing a plan immediately. Detect ambiguity via a lightweight pre-check in `_build_messages` or a dedicated clarify prompt.
  - [ ] 5-2. After LLM produces a plan, include a brief plain-text summary of the proposed graph ("I'll create X nodes: A → B → C with a review loop...") *before* the diff preview appears, so users can reject early without viewing a full diff.
  - [ ] 5-3. Add `ChatManager.clarify_intent()` method: returns a text-only streaming response asking 1-2 focused questions. Used when `intent_score < threshold` (heuristic: no template match + < 3 node types mentioned).

- [ ] 6. **Tests and docs**
  - [ ] 6-1. E2E test: intent "I have PDFs at /papers and want to write an INFORMS paper" → mutation plan contains `data_ingest`, `informs_paper_writing` template expansion, `apply_skill` ops.
  - [ ] 6-2. E2E test: `apply_skill("management_science_writing", target_tag="writing")` applied to a graph → correct nodes updated, others unchanged.
  - [ ] 6-3. Update `BUILD_FROM_INTENT_PROMPT` docstring and `chat_manager.py` module comment.
  - [ ] 6-4. Update `docs/llm-api-guide.md`: add `data_ingest` pattern, `apply_skill` op, skill library reference.
  - [ ] 6-5. Update `docs/changelog.md`, `docs/todo.md`.

## Decisions

- **Skill injection via `edit_node`, not a new execution primitive.** Skills modify the graph (prepend to `system_prompt`/`prompt_template`) rather than being applied at runtime. This keeps the execution engine unchanged and makes skills visible/editable in the visual editor. Runtime skill injection (hyperedge hooks) is deferred to Phase 10 Deep Systems.
- **`SKILL_LIBRARY` in Python, not markdown files.** Markdown skill files (like the Cursor skill system) are a good future design but add a loading/parsing layer. Start with a Python dict — same data, less machinery. Migrate to file-based loading later.
- **`informs_paper_writing` as a full mutation sequence, not a JSON file.** Keeps templates co-located with the pattern library, testable as code, and composable with other ops. JSON template files (in `graphs/templates/`) are a future optimization.
- **Multi-turn clarification is opt-in/heuristic.** Don't gate all build-mode interactions behind a clarification step. Only trigger when intent is genuinely ambiguous. The current single-turn flow works fine for well-formed intents.

## Sequencing

| Order | Task | Rationale |
|-------|------|-----------|
| 1 | Task 1 (`data_ingest` pattern) | Foundational — required by Task 2 template and Task 4 prompt improvements |
| 2 | Task 2 (domain template) | Uses `data_ingest`; delivers the most user-visible value |
| 3 | Task 3 (skills) | Layered on top of Task 2 — skills calibrate the template's prompts |
| 4 | Task 4 (prompt quality) | Prompt changes are low-risk; do after primitives exist so examples are accurate |
| 5 | Task 5 (multi-turn) | Optional quality improvement; can ship Tasks 1–4 first |
| 6 | Task 6 (tests/docs) | Throughout |

## Primary Files

- `src/dan/server/graph_mutator.py` — `PATTERN_LIBRARY`, `ExpandPattern._apply_data_ingest()`, `ApplySkill` op
- `src/dan/server/chat_manager.py` — `MUTATION_TOOL_SCHEMA`, `BUILD_FROM_INTENT_PROMPT`, `WORKFLOW_TEMPLATES`, `SKILL_LIBRARY`
- `docs/llm-api-guide.md` — pattern + skill reference
- `tests/test_server/` — new tests for `data_ingest` pattern and `apply_skill` op

## Notes

- `examples/paper_writing.py` is the ground truth for what the `informs_paper_writing` template should produce. Use it as the reference when writing template ops and prompts.
- The `compile_latex` and `save_paper` server-registered tools already exist in `app.py` lifespan — the template just needs to wire `ToolOperator` nodes that call them.
- Skills apply domain expertise without changing graph topology. This is by design — a user can remove a skill injection from a node without restructuring the whole graph.
- Future: expose skill library in the visual editor's Config Panel (per-node "Apply skill" dropdown). Not in scope here.
