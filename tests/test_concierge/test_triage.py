from __future__ import annotations

import json
import importlib

import pytest

from dan.server.concierge.models import Project, ResolvedContext, Task, TaskTurn
from dan.server.concierge.project_store import ProjectStore
from dan.server.concierge.triage import _build_triage_messages, fast_classify_text, triage

triage_module = importlib.import_module("dan.server.concierge.triage")

def _make_context(
    *,
    surface_id: str = "cli-user",
    project_label: str = "Revenue Tracker",
    task_label: str = "Draft quarterly report",
    workflow_ids: list[str] | None = None,
    turns: list[TaskTurn] | None = None,
) -> tuple[ResolvedContext, Project, Task]:
    project = Project(
        surface_id=surface_id,
        label=project_label,
        linked_workflow_ids=workflow_ids or ["workflow-quarterly"],
    )
    task = Task(label=task_label, status="active")
    if turns:
        task.turns = turns
    project.tasks.append(task)
    project.current_task_id = task.task_id
    context = ResolvedContext(
        project=project,
        task=task,
        is_new_project=False,
        is_new_task=False,
        confidence=1.0,
    )
    return context, project, task


def _workflow_activity_turns() -> list[TaskTurn]:
    """Recent turns that indicate workflow build/edit activity."""
    return [
        TaskTurn(role="user", content="build a workflow for quarterly reports"),
        TaskTurn(
            role="assistant",
            content="I created a workflow with 3 nodes.",
            intent="workflow_build",
            metadata={"route_target": "workflow"},
        ),
    ]


def _llm_json(payload: dict):
    async def _complete(_messages):
        return json.dumps(payload)

    return _complete



@pytest.mark.asyncio
async def test_triage_parses_basic_llm_json():
    context, project, _task = _make_context()

    result = await triage(
        "Explain the reporting task",
        context,
        _llm_json(
            {
                "tier": 1,
                "intent": "ask",
                "route": {
                    "mode": "ask",
                    "target": "general",
                    "action_hints": ["status_check"],
                },
                "confidence": 0.91,
                "goal": "Explain the reporting task",
                "deliverable": "A short explanation",
                "entities": [
                    {
                        "kind": "project",
                        "label": project.label,
                        "confidence": 0.95,
                    },
                ],
                "is_resume": False,
                "resume_task_id": None,
                "is_social": False,
                "social_response": None,
                "context_needs": ["memory"],
                "subtasks": [],
                "execution_order": "parallel",
                "rationale": "Straightforward explanatory request",
            }
        ),
    )

    assert result.tier == 1
    assert result.intent == "ask"
    assert result.route is not None
    assert result.route.mode.value == "ask"
    assert result.route.target == "general"
    assert result.route.action_hints == ["status_check"]
    assert result.goal == "Explain the reporting task"
    assert result.deliverable == "A short explanation"
    assert [entity.label for entity in result.entities] == [project.label]
    assert result.context_needs == ["memory"]


@pytest.mark.asyncio
async def test_triage_resolves_entity_ids_against_known_context(tmp_path):
    context, project, task = _make_context()
    project_store = ProjectStore(tmp_path)
    project_store.save_project(project)

    result = await triage(
        "Work on the quarterly workflow",
        context,
        _llm_json(
            {
                "tier": 2,
                "intent": "agent",
                "route": {
                    "mode": "agent",
                    "target": "workflow",
                    "action_hints": ["workflow_query"],
                },
                "confidence": 0.88,
                "goal": "Continue work on the quarterly workflow",
                "deliverable": "Updated workflow progress",
                "entities": [
                    {"kind": "project", "label": project.label},
                    {"kind": "task", "label": task.label},
                    {"kind": "workflow", "label": project.linked_workflow_ids[0]},
                ],
                "is_resume": False,
                "resume_task_id": None,
                "is_social": False,
                "social_response": None,
                "context_needs": ["memory"],
                "subtasks": ["Inspect workflow status"],
                "execution_order": "serial",
                "rationale": "Uses known local entities",
            }
        ),
        project_store=project_store,
    )

    resolved = {entity.kind: entity.id for entity in result.entities}
    assert resolved == {
        "project": project.project_id,
        "task": task.task_id,
        "workflow": project.linked_workflow_ids[0],
    }


@pytest.mark.asyncio
async def test_triage_returns_safe_default_when_llm_complete_is_none():
    context, _project, _task = _make_context()

    result = await triage("What is 2 plus 2?", context, None)

    assert result.intent == "ask"
    assert result.tier == 1
    assert result.confidence == 0.5


@pytest.mark.asyncio
async def test_triage_normalizes_and_deduplicates_context_needs():
    context, _project, _task = _make_context()

    result = await triage(
        "Research the report context",
        context,
        _llm_json(
            {
                "tier": 2,
                "intent": "agent",
                "route": {
                    "mode": "agent",
                    "target": "general",
                    "action_hints": ["search_web"],
                },
                "confidence": 0.84,
                "goal": "Gather context for the report",
                "deliverable": "Prepared research context",
                "entities": [],
                "is_resume": False,
                "resume_task_id": None,
                "is_social": False,
                "social_response": None,
                "context_needs": [
                    " memory ",
                    "file:/tmp/report.md",
                    "memory/file:/tmp/report.md/domain:Finance/reuse",
                    "DOMAIN:finance",
                    "reuse",
                ],
                "subtasks": ["Read notes", "Check memory"],
                "execution_order": "serial",
                "rationale": "Needs targeted context",
            }
        ),
    )

    assert result.context_needs == [
        "memory",
        "file:/tmp/report.md",
        "domain:finance",
        "reuse",
    ]


@pytest.mark.asyncio
async def test_triage_fallback_infers_write_file_for_short_resume_edit():
    context, _project, _task = _make_context(task_label="Update the quarterly report draft")

    async def _bad_complete(_messages):
        return "definitely not valid json"

    result = await triage("update it", context, _bad_complete)

    assert result.intent == "agent"
    assert result.route is not None
    assert result.route.target == "file"
    assert "write_file" in result.route.action_hints
    assert result.is_resume is True


@pytest.mark.asyncio
async def test_triage_uses_workflow_apply_lexical_scenario_with_recent_context():
    context, _project, _task = _make_context(turns=_workflow_activity_turns())
    llm_calls = 0

    async def _should_not_run(_messages):
        nonlocal llm_calls
        llm_calls += 1
        return "{}"

    result = await triage("apply it", context, _should_not_run)

    assert result.route_source == "fast_lexical"
    assert result.scenario_id == "workflow_followup_apply"
    assert result.route is not None
    assert result.route.target == "workflow"
    assert result.route.action_hints == ["workflow_edit"]
    assert llm_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "scenario_id", "target", "action_hints"),
    [
        ("run it", "workflow_run_followup", "run", ["workflow_run", "run_control"]),
        ("status of the workflow", "workflow_query_status", "workflow", ["workflow_query"]),
        ("what workflow is this", "workflow_query_identity", "workflow", ["workflow_query"]),
    ],
)
async def test_triage_uses_workflow_followup_lexical_scenarios_with_recent_context(
    text: str,
    scenario_id: str,
    target: str,
    action_hints: list[str],
):
    context, _project, _task = _make_context(turns=_workflow_activity_turns())
    llm_calls = 0

    async def _should_not_run(_messages):
        nonlocal llm_calls
        llm_calls += 1
        return "{}"

    result = await triage(text, context, _should_not_run)

    assert result.route_source == "fast_lexical"
    assert result.scenario_id == scenario_id
    assert result.route is not None
    assert result.route.target == target
    assert result.route.action_hints == action_hints
    assert llm_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "scenario_id", "target", "action_hints"),
    [
        ("search that online", "explicit_web_lookup", "web", ["search_web"]),
        ("check this file", "explicit_file_read", "file", ["read_file"]),
    ],
)
async def test_triage_uses_explicit_non_workflow_lexical_scenarios(
    text: str,
    scenario_id: str,
    target: str,
    action_hints: list[str],
):
    context, _project, _task = _make_context(turns=_workflow_activity_turns())
    llm_calls = 0

    async def _should_not_run(_messages):
        nonlocal llm_calls
        llm_calls += 1
        return "{}"

    result = await triage(text, context, _should_not_run)

    assert result.route_source == "fast_lexical"
    assert result.scenario_id == scenario_id
    assert result.route is not None
    assert result.route.target == target
    assert result.route.action_hints == action_hints
    assert llm_calls == 0


@pytest.mark.asyncio
async def test_triage_escalates_ambiguous_workflow_and_file_followup_to_llm():
    context, _project, _task = _make_context(turns=_workflow_activity_turns())
    llm_calls = 0

    async def _complete(_messages):
        nonlocal llm_calls
        llm_calls += 1
        return json.dumps(
            {
                "tier": 1,
                "intent": "agent",
                "route": {
                    "mode": "agent",
                    "target": "workflow",
                    "action_hints": ["workflow_query"],
                },
                "confidence": 0.9,
                "goal": "Clarify the current workflow status",
                "deliverable": "Workflow status",
                "entities": [],
                "is_resume": False,
                "resume_task_id": None,
                "is_social": False,
                "social_response": None,
                "context_needs": [],
                "subtasks": [],
                "execution_order": "parallel",
                "rationale": "Ambiguous lexical cues were resolved by the LLM",
            }
        )

    result = await triage("check this file and workflow status", context, _complete)

    assert llm_calls == 1
    assert result.route_source == "llm"
    assert result.route is not None
    assert result.route.target == "workflow"
    assert result.route.action_hints == ["workflow_query"]


@pytest.mark.asyncio
async def test_triage_escalates_ambiguous_workflow_and_web_followup_to_llm():
    context, _project, _task = _make_context(turns=_workflow_activity_turns())
    llm_calls = 0

    async def _complete(_messages):
        nonlocal llm_calls
        llm_calls += 1
        return json.dumps(
            {
                "tier": 1,
                "intent": "agent",
                "route": {
                    "mode": "agent",
                    "target": "workflow",
                    "action_hints": ["workflow_query"],
                },
                "confidence": 0.9,
                "goal": "Clarify the current workflow status",
                "deliverable": "Workflow status",
                "entities": [],
                "is_resume": False,
                "resume_task_id": None,
                "is_social": False,
                "social_response": None,
                "context_needs": [],
                "subtasks": [],
                "execution_order": "parallel",
                "rationale": "Ambiguous lexical cues were resolved by the LLM",
            }
        )

    result = await triage("search that online and check workflow status", context, _complete)

    assert llm_calls == 1
    assert result.route_source == "llm"
    assert result.route is not None
    assert result.route.target == "workflow"
    assert result.route.action_hints == ["workflow_query"]


@pytest.mark.asyncio
async def test_triage_file_patch_request_does_not_hit_workflow_apply_scenario():
    context, _project, _task = _make_context(turns=_workflow_activity_turns())
    llm_calls = 0

    async def _should_not_run(_messages):
        nonlocal llm_calls
        llm_calls += 1
        return "{}"

    result = await triage("apply this patch to the file", context, _should_not_run)

    assert result.route_source == "fast_lexical"
    assert result.scenario_id == "explicit_file_write"
    assert result.route is not None
    assert result.route.target == "file"
    assert result.route.action_hints == ["write_file"]
    assert llm_calls == 0


@pytest.mark.asyncio
async def test_triage_forces_furnace_prompt_to_run_control_when_llm_misroutes():
    context, _project, _task = _make_context(task_label="General task")

    result = await triage(
        "help me create a furnace session on supply chain risks and read /tmp/paper.pdf",
        context,
        _llm_json(
            {
                "tier": 1,
                "intent": "agent",
                "route": {
                    "mode": "agent",
                    "target": "file",
                    "action_hints": ["write_file"],
                },
                "confidence": 0.7,
                "goal": "Create furnace session",
                "deliverable": "Done",
                "entities": [],
                "is_resume": False,
                "resume_task_id": None,
                "is_social": False,
                "social_response": None,
                "context_needs": [],
                "subtasks": [],
                "execution_order": "parallel",
                "rationale": "wrongly biased to file write",
            }
        ),
    )

    assert result.intent == "agent"
    assert result.tier == 2
    assert result.route is not None
    assert result.route.target == "run"
    assert "run_control" in result.route.action_hints
    assert "write_file" not in result.route.action_hints
    assert "read_file" in result.route.action_hints


@pytest.mark.asyncio
async def test_triage_fallback_for_furnace_prompt_prefers_run_control_over_write_file():
    context, _project, _task = _make_context(task_label="General task")

    async def _bad_complete(_messages):
        return "not json"

    result = await triage(
        "start furnace session and add this paper /tmp/ersahin2024supply.pdf",
        context,
        _bad_complete,
    )

    assert result.intent == "agent"
    assert result.tier == 2
    assert result.route is not None
    assert result.route.target == "run"
    assert "run_control" in result.route.action_hints
    assert "write_file" not in result.route.action_hints


@pytest.mark.asyncio
async def test_triage_furnace_folder_path_prefers_read_file():
    context, _project, _task = _make_context(task_label="General task")

    async def _bad_complete(_messages):
        return "not json"

    result = await triage(
        "start furnace session using folder /Users/lizhi/Dropbox/Projects/papers",
        context,
        _bad_complete,
    )

    assert result.intent == "agent"
    assert result.route is not None
    assert result.route.target == "run"
    assert "run_control" in result.route.action_hints
    assert "read_file" in result.route.action_hints
    assert "write_file" not in result.route.action_hints


@pytest.mark.asyncio
async def test_triage_furnace_topic_only_prefers_search_web():
    context, _project, _task = _make_context(task_label="General task")

    async def _bad_complete(_messages):
        return "not json"

    result = await triage(
        "start a furnace session on supply chain risk topic and learn online",
        context,
        _bad_complete,
    )

    assert result.intent == "agent"
    assert result.route is not None
    assert result.route.target == "run"
    assert "run_control" in result.route.action_hints
    assert "search_web" in result.route.action_hints
    assert "write_file" not in result.route.action_hints


def test_fast_classify_text_handles_simple_social_turn():
    result = fast_classify_text("Thanks!")
    assert result is not None
    assert result.tier == 0
    assert result.is_social is True
    assert result.social_response == "You're welcome."


def test_fast_classify_text_ignores_non_social_turn():
    result = fast_classify_text("Please summarize this file")
    assert result is None


def test_build_triage_messages_includes_six_recent_turns_with_wider_truncation():
    turns = [
        TaskTurn(role="user", content=f"user turn {idx} " + ("x" * 450))
        if idx % 2 == 0
        else TaskTurn(role="assistant", content=f"assistant turn {idx} " + ("y" * 450))
        for idx in range(8)
    ]
    context, _project, _task = _make_context(turns=turns)

    messages = _build_triage_messages("continue", context)

    history_messages = [
        message
        for message in messages
        if message["role"] in {"user", "assistant"}
    ]
    prior_messages = history_messages[:-1]
    assert len(prior_messages) == 6
    assert "turn 0" not in prior_messages[0]["content"]
    assert "turn 2" in prior_messages[0]["content"]
    assert len(prior_messages[0]["content"]) == 400


@pytest.mark.asyncio
async def test_triage_embedding_primary_takes_precedence_over_llm(monkeypatch):
    context, _project, _task = _make_context(task_label="General task")

    async def _embed_result(_text, _context):
        return triage_module.TriageResult(
            tier=2,
            intent="agent",
            route=triage_module.RouteDecision(
                mode=triage_module.RouteMode.AGENT,
                target="run",
                action_hints=["run_control"],
                rationale="embedding test",
            ),
            confidence=0.92,
            goal="Start furnace",
            deliverable="Start furnace",
        )

    monkeypatch.setattr(triage_module, "_embedding_triage_result", _embed_result)

    result = await triage(
        "start furnace session",
        context,
        _llm_json(
            {
                "tier": 1,
                "intent": "ask",
                "route": {
                    "mode": "ask",
                    "target": "general",
                    "action_hints": ["status_check"],
                },
                "confidence": 0.8,
                "goal": "wrong",
                "deliverable": "wrong",
                "entities": [],
                "is_resume": False,
                "resume_task_id": None,
                "is_social": False,
                "social_response": None,
                "context_needs": [],
                "subtasks": [],
                "execution_order": "parallel",
                "rationale": "llm fallback",
            }
        ),
    )

    assert result.intent == "agent"
    assert result.route is not None
    assert result.route.target == "run"
    assert "run_control" in result.route.action_hints


@pytest.mark.asyncio
async def test_triage_embedding_none_falls_back_to_llm(monkeypatch):
    context, _project, _task = _make_context(task_label="General task")

    async def _no_embed(_text, _context):
        return None

    monkeypatch.setattr(triage_module, "_embedding_triage_result", _no_embed)

    result = await triage(
        "what is the status",
        context,
        _llm_json(
            {
                "tier": 1,
                "intent": "ask",
                "route": {
                    "mode": "ask",
                    "target": "general",
                    "action_hints": ["status_check"],
                },
                "confidence": 0.9,
                "goal": "status",
                "deliverable": "status",
                "entities": [],
                "is_resume": False,
                "resume_task_id": None,
                "is_social": False,
                "social_response": None,
                "context_needs": [],
                "subtasks": [],
                "execution_order": "parallel",
                "rationale": "llm route",
            }
        ),
    )

    assert result.intent == "ask"
    assert result.route is not None
    assert result.route.target == "general"


@pytest.mark.asyncio
async def test_triage_llm_general_retry_followup_is_upgraded_to_workflow_edit():
    context, _project, _task = _make_context(
        task_label="General task", turns=_workflow_activity_turns(),
    )

    result = await triage(
        "can you retry and fix this?",
        context,
        _llm_json(
            {
                "tier": 1,
                "intent": "ask",
                "route": {
                    "mode": "ask",
                    "target": "general",
                    "action_hints": ["status_check"],
                },
                "confidence": 0.82,
                "goal": "retry",
                "deliverable": "retry",
                "entities": [],
                "is_resume": False,
                "resume_task_id": None,
                "is_social": False,
                "social_response": None,
                "context_needs": [],
                "subtasks": [],
                "execution_order": "parallel",
                "rationale": "llm misroute",
            }
        ),
    )

    assert result.intent == "agent"
    assert result.tier == 2
    assert result.route is not None
    assert result.route.mode.value == "agent"
    assert result.route.target == "workflow"
    assert "workflow_edit" in result.route.action_hints
    assert "write_file" not in result.route.action_hints


@pytest.mark.asyncio
async def test_triage_embedding_general_retry_followup_is_upgraded_to_workflow_edit(monkeypatch):
    context, _project, _task = _make_context(
        task_label="General task", turns=_workflow_activity_turns(),
    )

    async def _embed_result(_text, _context):
        return triage_module.TriageResult(
            tier=1,
            intent="ask",
            route=triage_module.RouteDecision(
                mode=triage_module.RouteMode.ASK,
                target="general",
                action_hints=["status_check"],
                rationale="embedding misroute",
            ),
            confidence=0.88,
            goal="retry",
            deliverable="retry",
        )

    monkeypatch.setattr(triage_module, "_embedding_triage_result", _embed_result)

    result = await triage(
        "can you retry and fix this?",
        context,
        _llm_json(
            {
                "tier": 1,
                "intent": "ask",
                "route": {
                    "mode": "ask",
                    "target": "general",
                    "action_hints": ["status_check"],
                },
                "confidence": 0.8,
                "goal": "wrong",
                "deliverable": "wrong",
                "entities": [],
                "is_resume": False,
                "resume_task_id": None,
                "is_social": False,
                "social_response": None,
                "context_needs": [],
                "subtasks": [],
                "execution_order": "parallel",
                "rationale": "llm fallback",
            }
        ),
    )

    assert result.intent == "agent"
    assert result.tier == 2
    assert result.route is not None
    assert result.route.mode.value == "agent"
    assert result.route.target == "workflow"
    assert "workflow_edit" in result.route.action_hints
    assert "write_file" not in result.route.action_hints


def test_fallback_infers_workflow_edit_for_retry_build_with_linked_workflow():
    """'retry to build' + linked workflow + recent workflow activity → workflow_edit hint."""
    context, _project, _task = _make_context(turns=_workflow_activity_turns())
    hints = triage_module._infer_fallback_action_hints(
        "can you learn from previous failures and retry to build?",
        context,
    )
    assert "workflow_edit" in hints


def test_fallback_infers_workflow_edit_for_rebuild_workflow():
    """'rebuild the ... workflow' → workflow_edit hint (entity RE + edit RE, no activity needed)."""
    context, _project, _task = _make_context()
    hints = triage_module._infer_fallback_action_hints(
        "rebuild the daily equity watchlist workflow",
        context,
    )
    assert "workflow_edit" in hints


def test_fallback_no_workflow_edit_for_try_again_without_workflow_activity():
    """'try again' + linked workflow but NO recent workflow activity → no workflow_edit."""
    context, _project, _task = _make_context()
    hints = triage_module._infer_fallback_action_hints(
        "try again",
        context,
    )
    assert "workflow_edit" not in hints


def test_fallback_infers_workflow_edit_for_try_again_with_workflow_activity():
    """'try again' + linked workflow + recent workflow activity → workflow_edit hint."""
    context, _project, _task = _make_context(turns=_workflow_activity_turns())
    hints = triage_module._infer_fallback_action_hints(
        "try again",
        context,
    )
    assert "workflow_edit" in hints


def test_fallback_no_workflow_edit_for_plain_retry_without_workflow_activity():
    """Plain 'retry' + linked workflow but NO recent workflow activity → no workflow_edit."""
    context, _project, _task = _make_context()
    hints = triage_module._infer_fallback_action_hints(
        "retry",
        context,
    )
    assert "workflow_edit" not in hints


def test_fallback_no_workflow_edit_for_generic_build_without_linked_workflow():
    """'build a grocery list' with no linked workflows → no workflow_edit hint."""
    context, _project, _task = _make_context(workflow_ids=[])
    hints = triage_module._infer_fallback_action_hints(
        "build a grocery list for tonight",
        context,
    )
    assert "workflow_edit" not in hints
