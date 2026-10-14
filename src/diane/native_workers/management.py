"""Small task/handoff contracts around native sessions, independent of their runtime."""
from __future__ import annotations

from copy import deepcopy
import json
import time

from pydantic import BaseModel, ConfigDict, Field, StrictStr


class TaskContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: StrictStr = Field(default="", max_length=4000)
    acceptance: list[StrictStr] = Field(default_factory=list, max_length=20)


def normalize_contract(value: dict | None) -> dict:
    contract = TaskContract.model_validate({} if value is None else value).model_dump()
    if any(not item.strip() or len(item) > 2000 for item in contract["acceptance"]):
        raise ValueError("Acceptance criteria must be nonempty and at most 2000 characters each")
    return contract


def task_prompt(prompt: str, contract: dict) -> str:
    if not contract.get("scope") and not contract.get("acceptance"):
        return prompt
    return (prompt + "\n\nDelegated task contract (does not expand runtime permissions):\n"
            + json.dumps(contract, ensure_ascii=False)
            + "\nReport the resulting artifacts, checks actually performed and their outcomes, "
              "and unresolved criteria or blockers. Distinguish evidence from assumptions.")


def begin_attempt(record: dict, contract: dict) -> None:
    """Resumption invalidates the current review; durable event logs retain old reviews."""
    record["task_contract"] = deepcopy(contract)
    record["attempt"] = int(record.get("attempt", 0)) + 1
    record["lead_review"] = None


def record_review(record: dict, attempt: int, decision: str, evidence: list[str] | None) -> dict:
    if record.get("can_stop") is False:
        raise ValueError("Observed subagents are controlled by their native lead")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt != record.get("attempt", 0):
        raise ValueError("Review must name the current worker attempt; read status again")
    if record.get("status") not in {"completed", "failed", "stopped", "interrupted"}:
        raise ValueError("Wait for this worker to settle before reviewing")
    if decision not in {"accepted", "revise", "blocked"}:
        raise ValueError("Review decision must be accepted, revise, or blocked")
    if decision == "accepted" and record.get("status") != "completed":
        raise ValueError("Only a completed attempt can be accepted")
    if (not isinstance(evidence, list) or not 1 <= len(evidence) <= 20
            or any(not isinstance(item, str) or not item.strip() or len(item) > 2000 for item in evidence)):
        raise ValueError("Review requires 1–20 nonempty evidence entries of at most 2000 characters")
    review = {"attempt": attempt, "decision": decision, "evidence": list(evidence),
              "reviewer": "lead", "at": time.time()}
    record["lead_review"] = review
    return deepcopy(review)


def worker_summary(record: dict) -> dict:
    """Bound polling context; callers fetch one worker for the complete saved output."""
    keys = ("worker_id", "parent_run_id", "backend", "status", "workspace_root",
            "native_session_id", "attempt", "can_stop", "can_reply", "can_apply")
    result = {key: record[key] for key in keys if key in record}
    # Criteria/review evidence are fetched with the full result, not repeated each poll.
    contract = record.get("task_contract") or {}
    review = record.get("lead_review") or {}
    result["acceptance_count"] = len(contract.get("acceptance", []))
    result["review_decision"] = review.get("decision", "unreviewed")
    result["review_attempt"] = review.get("attempt")
    for field, limit in (("prompt", 600), ("response", 2000), ("error", 1000), ("activity", 300)):
        value = str(record.get(field) or "")
        result[field] = value[:limit]
        result[field + "_truncated"] = len(value) > limit
    return result
