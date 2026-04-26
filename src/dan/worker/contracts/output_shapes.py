"""Canonical output-shape examples for universal briefs."""

from __future__ import annotations

import json
from typing import Any


def _example(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def coding_v1() -> str:
    return _example(
        {
            "candidate_id": "coding-candidate-001",
            "change_summary": ["short summary of concrete changes"],
            "target_files": ["src/example.py"],
            "test_plan": ["PYTHONPATH=src:. pytest -q tests/test_example.py"],
            "risks": [],
            "files_created": [],
        }
    )


def coding_v1_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["candidate_id", "change_summary", "target_files", "test_plan", "risks"],
        "properties": {
            "candidate_id": {"type": "string"},
            "change_summary": {"type": "array", "items": {"type": "string"}},
            "target_files": {"type": "array", "items": {"type": "string"}},
            "test_plan": {"type": "array", "items": {"type": "string"}},
            "risks": {"type": "array", "items": {"type": "string"}},
            "files_created": {"type": "array", "items": {"type": "string"}},
        },
    }


def validation_v1() -> str:
    return _example(
        {
            "passed": True,
            "overall_score": 0.9,
            "dimension_scores": {"objective_alignment": 0.9},
            "repair_brief": "",
            "missing_requirements": [],
            "comparison_note": "The result materially satisfies the brief.",
        }
    )


def validation_v1_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["passed", "overall_score", "missing_requirements", "repair_brief"],
        "properties": {
            "passed": {"type": "boolean"},
            "overall_score": {"type": "number"},
            "dimension_scores": {"type": "object"},
            "repair_brief": {"type": "string"},
            "missing_requirements": {"type": "array", "items": {"type": "string"}},
            "comparison_note": {"type": "string"},
        },
    }


def research_evidence_v1() -> str:
    return _example(
        {
            "claim": "bounded claim",
            "evidence_refs": ["source:1"],
            "confidence": "medium",
            "limitations": [],
            "next_checks": [],
        }
    )


def scheduler_proposal_v1() -> str:
    return _example(
        {
            "proposal_id": "scheduler-proposal-001",
            "action": "parallel",
            "task_ids": ["worker-a", "worker-b"],
            "expected_value": 0.0,
            "risk_flags": [],
            "rationale": "brief rationale",
        }
    )
