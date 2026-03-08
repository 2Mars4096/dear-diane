from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .identity import format_prefix
from .models import Project, Task


@dataclass
class PromotionProposal:
    summary: str
    workflow_id: str
    suggested_name: str
    save_command: str


class WorkflowPromoter:
    def __init__(self, graph_store: Any, experience_store: Any) -> None:
        self.graph_store = graph_store
        self.experience_store = experience_store

    def should_propose(self, project: Project, task: Task) -> bool:
        if task.status != "completed":
            return False
        if not project.linked_workflow_ids:
            return False
        workflow_id = project.linked_workflow_ids[-1]
        graph = self.graph_store.get_graph(workflow_id) if self.graph_store is not None else None
        if not isinstance(graph, dict):
            return False
        nodes = graph.get("nodes") or []
        if len(nodes) <= 3:
            return False
        if self.experience_store is not None and hasattr(self.experience_store, "has_experience"):
            if self.experience_store.has_experience(workflow_id):
                return False
        return True

    def build_proposal(self, project: Project, task: Task) -> PromotionProposal:
        workflow_id = project.linked_workflow_ids[-1]
        return PromotionProposal(
            summary=f"{format_prefix(project.label)} Done! This workflow is reusable.",
            workflow_id=workflow_id,
            suggested_name=project.label,
            save_command=f"/save {project.label}",
        )
