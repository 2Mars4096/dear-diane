# 12-9: Workflow Build Feedback Honesty

**Parent:** [12-cursor-parity-chat](12-cursor-parity-chat.md)
**Status:** completed
**Goal:** Make workflow-build chat clearly distinguish proposed mutation previews from applied/tested workflows, while surfacing clearer phase progress during build turns.

## Tasks
- [x] 1. Tighten workflow-build prompt guidance.
 - [x] 1-1. Teach workflow-build prompts that `plan_graph_mutations` only prepares a proposed preview/diff.
 - [x] 1-2. Require honest phase labeling across inspect/propose/validate/apply/test steps.
- [x] 2. Make backend mutation summaries status-accurate.
 - [x] 2-1. Replace model-authored mutation summary text with a server-authored preview summary that explicitly says proposed vs applied.
 - [x] 2-2. Emit concise workflow-preview progress labels during prepare/repair/refresh phases.
- [x] 3. Surface clearer workflow-build feedback in editor chat surfaces.
 - [x] 3-1. Prefer `phase_label` progress text in full chat, compact sidebar chat, and detached background streams.
 - [x] 3-2. Give `plan_graph_mutations` a workflow-specific progress label instead of generic tool wording.
 - [x] 3-3. Replace stale preview copy with an applied summary after the user applies the mutation.
- [x] 4. Add regression coverage for prompt copy, mutation preview wording, progress forwarding, and tool-progress text.

## Decisions
- Server-authored mutation preview copy is more trustworthy than provider-authored free text for the proposed/applied boundary.
- Editor chat surfaces should prefer concise `phase_label` text over longer raw heartbeat text when both are present.

## Notes
- Validation: `python -m pytest tests/test_chat_prompt_modules.py tests/test_post_tool_followup_recovery.py -q`
- Validation: `cd editor && npx vitest run src/lib/__tests__/editorChat.test.ts src/lib/__tests__/toolCallPresentation.test.ts src/lib/__tests__/chatProgress.test.ts`
- Validation: `cd editor && npm run build` (passes; Vite still warns that Node `20.19+` is preferred while this machine is on `20.17.0`)
