from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class IntentExample:
    user: str
    reason: str


@dataclass(frozen=True)
class IntentDefinition:
    name: str
    summary: str
    choose_when: tuple[str, ...]
    avoid_when: tuple[str, ...] = ()
    examples: tuple[IntentExample, ...] = ()


INTENT_DEFINITIONS: tuple[IntentDefinition, ...] = (
    IntentDefinition(
        name="ask",
        summary="Read-only help, lookup, explanation, or lightweight inspection.",
        choose_when=(
            "The user mainly wants an answer, explanation, summary, status, file lookup, workflow lookup, or conversational guidance.",
            "The result can be produced without carrying out a multi-step external action or writing a new artifact.",
        ),
        avoid_when=(
            "The user is asking you to carry out work, use tools repeatedly, write files, or create deliverables.",
            "The user wants to design or modify a workflow/automation structure.",
        ),
        examples=(
            IntentExample(
                user="Find /Users/me/report.pdf",
                reason="The user wants a read-only file lookup result.",
            ),
            IntentExample(
                user="What's the status of the run?",
                reason="The user wants a read-only status answer.",
            ),
        ),
    ),
    IntentDefinition(
        name="agent",
        summary="Carry out operational work using tools and external actions.",
        choose_when=(
            "The user wants work done: research, drafting, code/file edits, transformations, data gathering, artifact creation, publishing, or run control.",
            "The request may read files, use a folder as workspace, search online, and then write or deliver output.",
            "A message that starts from a file or folder but asks for additional work still belongs here.",
        ),
        avoid_when=(
            "The user only wants a read-only answer or lookup.",
            "The request is specifically about designing or editing workflow structure instead of executing work directly.",
        ),
        examples=(
            IntentExample(
                user="Read /Users/me/report.tex, update 2025 to 2026, and save a new copy.",
                reason="The request requires file transformation and output creation.",
            ),
            IntentExample(
                user="Help me write a comprehensive report in /Users/me/project as tex and cite figures from the web.",
                reason="The folder is a workspace for a multi-step artifact task.",
            ),
            IntentExample(
                user="Publish this workflow and give me a shareable link.",
                reason="Publishing is an operational action, not a read-only answer.",
            ),
        ),
    ),
    IntentDefinition(
        name="plan",
        summary="Plan, design, or modify workflow/automation structure.",
        choose_when=(
            "The user wants to add, remove, edit, wire, or redesign workflow structure.",
            "The user is asking for a broader automation plan or longer-horizon orchestration design.",
        ),
        avoid_when=(
            "The user wants the task executed directly right now instead of planning or restructuring the system.",
        ),
        examples=(
            IntentExample(
                user="Add a reviewer node after summarize and wire it to approval.",
                reason="The request is about workflow structure.",
            ),
            IntentExample(
                user="Set up an always-on research automation system that scouts papers and emails me weekly.",
                reason="This is a broader automation design / planning request.",
            ),
        ),
    ),
)


def _render_definition(definition: IntentDefinition) -> str:
    lines = [f"- {definition.name}: {definition.summary}"]
    if definition.choose_when:
        lines.append("  Choose when:")
        lines.extend(f"  - {item}" for item in definition.choose_when)
    if definition.avoid_when:
        lines.append("  Avoid when:")
        lines.extend(f"  - {item}" for item in definition.avoid_when)
    if definition.examples:
        lines.append("  Examples:")
        lines.extend(
            f'  - User: "{example.user}" -> {definition.name} ({example.reason})'
            for example in definition.examples
        )
    return "\n".join(lines)


@lru_cache(maxsize=1)
def build_classifier_prompt() -> str:
    catalog = "\n\n".join(_render_definition(definition) for definition in INTENT_DEFINITIONS)
    return (
        "You are DAN's routing classifier. Choose exactly one top-level route for the latest user message.\n"
        'Return JSON only with this schema: {"intent":"<name>","confidence":0.0,"reason":"short explanation","target":"general|file|web|run|workflow|memory","action_hints":["read_file|search_web|write_file|status_check|workflow_query|experience_lookup|run_control|workflow_run|publish|workflow_edit|long_horizon_goal"],"goal":"one-sentence user goal","deliverable":"one-sentence expected outcome","constraints":["short constraint"],"next_step":"short immediate next action"}\n\n'
        "Routing principles:\n"
        "- First infer the user's real goal and expected deliverable.\n"
        "- Then choose the route, target, and action hints that best match that goal.\n"
        "- Do a short internal second pass: check whether your chosen route would still make sense after the required reads, searches, or writes are complete.\n"
        "- Classify the user's underlying goal, not surface keyword overlap.\n"
        "- Use recent turns to resolve pronouns like it, that, this file, or there.\n"
        "- Filesystem paths are context, not route. If a path is the workspace, input, or output location for broader work, choose agent.\n"
        "- If the user wants the file or folder itself found, opened, sent, read, or simply summarized, choose ask.\n"
        "- If the user wants to read a file and then do more work with it (rewrite, compare, extract, update, convert, save output), choose agent.\n"
        "- If the user wants workflow / graph / automation structure changed or designed, choose plan.\n"
        "- ask is the default for general conversation, explanation, status, and lightweight lookup.\n\n"
        "Action-hint guidance:\n"
        "- Use `read_file` when the task should inspect a local file or folder before answering.\n"
        "- Use `search_web` when the task needs live external information.\n"
        "- Use `write_file` when the task must save or create a local artifact.\n"
        "- Use `workflow_edit` for workflow-structure edits, `workflow_run` for executing the current workflow, and `long_horizon_goal` for broader automation planning.\n"
        "- Use `run_control` only for Furnace / session-control requests, not ordinary workflow runs.\n"
        "- Use `status_check`, `workflow_query`, `experience_lookup`, `run_control`, `workflow_run`, or `publish` only when they are clearly the main action.\n\n"
        "Route catalog:\n"
        f"{catalog}\n"
    )
