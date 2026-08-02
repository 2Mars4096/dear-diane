from __future__ import annotations

from dan.server.chat.prompts import WORKFLOW_TEMPLATES


def test_informs_paper_writing_template_prefers_canonical_human_node_type() -> None:
    ops = WORKFLOW_TEMPLATES["informs_paper_writing"]
    interview_nodes = [
        op for op in ops
        if op.get("op") == "add_node" and op.get("name") == "Research Interview"
    ]

    assert len(interview_nodes) == 1
    assert interview_nodes[0]["node_type"] == "human"
