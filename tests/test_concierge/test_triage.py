from __future__ import annotations

import json

import pytest

from dan.server.concierge.models import Project, ResolvedContext, Task
from dan.server.concierge.project_store import ProjectStore
from dan.server.concierge.triage import triage


def _make_context(
    *,
    surface_id: str = "cli-user",
    project_label: str = "Revenue Tracker",
    task_label: str = "Draft quarterly report",
    workflow_ids: list[str] | None = None,
) -> tuple[ResolvedContext, Project, Task]:
    project = Project(
        surface_id=surface_id,
        label=project_label,
        linked_workflow_ids=workflow_ids or ["workflow-quarterly"],
    )
    task = Task(label=task_label, status="active")
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
