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
        name="file_request",
        summary="Retrieve or inspect a local file or folder itself.",
        choose_when=(
            "The user wants a file or folder found, opened, sent, read, or simply summarized.",
            "The primary output is the file match, file contents, or a lightweight review of that file.",
        ),
        avoid_when=(
            "The user wants follow-on work after reading the file, such as rewriting, extracting, comparing, updating, converting, or saving a new artifact.",
            "A filesystem path is just the working location for a broader task.",
        ),
        examples=(
            IntentExample(
                user="Find /Users/me/report.pdf",
                reason="The goal is to locate the file itself.",
            ),
        ),
    ),
    IntentDefinition(
        name="direct_task",
        summary="Carry out a tool-using task in the workspace or on the web.",
        choose_when=(
            "The user wants work done: research, drafting, code/file edits, transformations, data gathering, or artifact creation.",
            "The request may read files, use a folder as workspace, search online, and then write or deliver output.",
            "A message that starts from a file or folder but asks for additional work still belongs here.",
        ),
        avoid_when=(
            "The user only wants the file itself located or opened with no extra work.",
            "The request is specifically about editing or inspecting a workflow/graph structure.",
        ),
        examples=(
            IntentExample(
                user="Read /Users/me/report.tex, update 2025 to 2026, and save a new copy.",
                reason="The request starts from a file but the real goal is a transformation and new output.",
            ),
            IntentExample(
                user="Help me write a comprehensive report in /Users/me/project as tex and cite figures from the web.",
                reason="The folder is a workspace for a multi-step artifact task.",
            ),
        ),
    ),
    IntentDefinition(
        name="run_control",
        summary="Start, stop, cancel, resume, or pause a workflow run.",
        choose_when=(
            "The user is controlling execution state for a DAN run or workflow.",
        ),
        examples=(
            IntentExample(
                user="Cancel the active run.",
                reason="The user is issuing an execution control action.",
            ),
        ),
    ),
    IntentDefinition(
        name="status_check",
        summary="Check the status or progress of DAN's own runs, tasks, or jobs.",
        choose_when=(
            "The user asks what is running, how far along something is, or whether a DAN job completed.",
        ),
        avoid_when=(
            "The user is asking about the status of an external topic, company, or project in the real world.",
        ),
        examples=(
            IntentExample(
                user="What's the status of the run?",
                reason="This is a progress query about DAN's own execution state.",
            ),
        ),
    ),
    IntentDefinition(
        name="workflow_build",
        summary="Create or modify a workflow, graph, pipeline, nodes, or edges.",
        choose_when=(
            "The user wants to add, remove, edit, wire, or redesign workflow structure.",
        ),
        examples=(
            IntentExample(
                user="Add a reviewer node after summarize and wire it to approval.",
                reason="The request is about workflow structure rather than doing the task directly.",
            ),
        ),
    ),
    IntentDefinition(
        name="workflow_query",
        summary="Inspect, list, or explain existing workflows.",
        choose_when=(
            "The user wants to browse or inspect workflows without changing them.",
        ),
        examples=(
            IntentExample(
                user="List the workflows we already saved.",
                reason="The request is about existing workflow inventory.",
            ),
        ),
    ),
    IntentDefinition(
        name="experience_query",
        summary="Ask about similar past work, prior runs, or lessons learned.",
        choose_when=(
            "The user is looking for analogous projects, memories, or prior solutions.",
        ),
        examples=(
            IntentExample(
                user="Have we done something similar before?",
                reason="The request targets prior experience rather than current execution.",
            ),
        ),
    ),
    IntentDefinition(
        name="publish_share",
        summary="Publish, export, or share a workflow.",
        choose_when=(
            "The user wants a workflow exposed, exported, or shared with others.",
        ),
        examples=(
            IntentExample(
                user="Publish this workflow and give me a shareable link.",
                reason="The action is about distribution, not execution or editing.",
            ),
        ),
    ),
    IntentDefinition(
        name="meta_goal",
        summary="Set up a broad end-to-end automation system or longer-horizon agentic plan.",
        choose_when=(
            "The user wants a substantial automation system or repeated autonomous process orchestrated from scratch.",
        ),
        examples=(
            IntentExample(
                user="Set up an always-on research automation system that scouts papers and emails me weekly.",
                reason="This is a broad automation goal rather than a single immediate task.",
            ),
        ),
    ),
    IntentDefinition(
        name="conversation",
        summary="General discussion, brainstorming, or lightweight advice with no concrete operational task.",
        choose_when=(
            "The user is chatting, asking for opinions, or discussing ideas without asking DAN to take action.",
        ),
        examples=(
            IntentExample(
                user="What do you think is a good name for this project?",
                reason="This is conversational guidance rather than an operational route.",
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
        "You are DAN's routing classifier. Choose exactly one intent for the latest user message.\n"
        'Return JSON only: {"intent":"<name>","confidence":0.0,"reason":"short explanation"}\n\n'
        "Routing principles:\n"
        "- Classify the user's underlying goal, not surface keyword overlap.\n"
        "- Use recent turns to resolve pronouns like it, that, this file, or there.\n"
        "- Filesystem paths are context, not intent. If a path is the workspace, input, or output location for a broader task, choose direct_task.\n"
        "- If the user wants the file or folder itself found, opened, sent, read, or simply summarized, choose file_request.\n"
        "- If the user wants to read a file and then do more work with it (rewrite, compare, extract, update, convert, save output), choose direct_task.\n"
        "- status_check is only for DAN's own runs, tasks, queues, or jobs.\n"
        "- workflow_build is for editing workflow structure, not carrying out the task directly.\n"
        "- conversation is the fallback only when no stronger operational route applies.\n\n"
        "Intent catalog:\n"
        f"{catalog}\n"
    )
