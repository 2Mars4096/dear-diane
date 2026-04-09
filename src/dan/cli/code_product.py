"""Workspace-local product state for the DAN Code CLI."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

CODE_PRODUCT_NAME = "DAN Code"
CODE_PRODUCT_DIRNAME = ".dan-code"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _resolve_path(value: str | Path, *, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


class CodingOrganismReport(BaseModel):
    """Compact report for one DAN Code run."""

    status: str
    trace_id: str
    organism_id: str
    organ_id: str
    task_id: str
    objective: str
    candidate_id: str | None = None
    change_summary: str = ""
    target_files: list[str] = Field(default_factory=list)
    test_plan: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    outputs: dict[str, Any] = Field(default_factory=dict)
    handoff_count: int = 0
    signal_count: int = 0
    error: str | None = None
    trace_rows: list[dict[str, Any]] = Field(default_factory=list)


class CodingConversationEntry(BaseModel):
    """One durable user/assistant exchange in the DAN Code shell."""

    role: str
    text: str
    kind: str = "message"
    created_at: str = Field(default_factory=_utcnow_iso)


class CodingCliSession(BaseModel):
    """Persistent session state for the product shell."""

    session_id: str = Field(default_factory=lambda: f"coding-session-{uuid4().hex[:8]}")
    workspace_root: str = ""
    created_at: str = Field(default_factory=_utcnow_iso)
    updated_at: str = Field(default_factory=_utcnow_iso)
    turns: list[CodingOrganismReport] = Field(default_factory=list)
    conversation: list[CodingConversationEntry] = Field(default_factory=list)
    pending_clarification: str | None = None
    orchestrator_state: dict[str, Any] = Field(default_factory=dict)

    def next_turn_number(self) -> int:
        return len(self.turns) + 1

    def task_id_for(self, base_task_id: str) -> str:
        return f"{base_task_id}:{self.next_turn_number()}"

    def carry_forward_findings(self, *, limit: int = 3) -> list[str]:
        findings: list[str] = []
        for report in self.turns[-limit:]:
            target_files = ", ".join(report.target_files) or "(none)"
            test_plan = "; ".join(report.test_plan) or "(none)"
            findings.append(
                "Previous coding turn: "
                f"objective={report.objective}; "
                f"candidate={report.candidate_id or '(none)'}; "
                f"status={report.status}; "
                f"files={target_files}; "
                f"tests={test_plan}"
            )
        return findings

    def record_turn(self, report: CodingOrganismReport) -> None:
        self.turns.append(report)
        self.updated_at = _utcnow_iso()

    def record_message(self, *, role: str, text: str, kind: str = "message") -> None:
        cleaned = str(text or "").strip()
        if not cleaned:
            return
        self.conversation.append(
            CodingConversationEntry(
                role=str(role or "").strip() or "assistant",
                text=cleaned,
                kind=str(kind or "").strip() or "message",
            )
        )
        self.updated_at = _utcnow_iso()


class CodeProductConfig(BaseModel):
    """Workspace-local DAN Code defaults."""

    product_name: str = CODE_PRODUCT_NAME
    workspace_root: str
    default_model: str | None = None
    thinking_mode: str = "auto"
    default_tool_ids: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    max_tool_rounds: int = 8
    max_tool_calls: int = 24


class CodeProductPaths(BaseModel):
    """Filesystem locations for the DAN Code product shell."""

    root: str
    config: str
    session: str
    transcript: str
    runs_dir: str


def resolve_code_product_paths(
    workspace_root: str | Path,
    *,
    session_file: str | None = None,
) -> CodeProductPaths:
    base_dir = Path(workspace_root).expanduser().resolve()
    root = base_dir / CODE_PRODUCT_DIRNAME
    session_path = (
        _resolve_path(session_file, base_dir=base_dir)
        if session_file
        else root / "session.json"
    )
    return CodeProductPaths(
        root=str(root),
        config=str(root / "config.json"),
        session=str(session_path),
        transcript=str(root / "transcript.jsonl"),
        runs_dir=str(root / "runs"),
    )


def load_code_product_config(paths: CodeProductPaths) -> CodeProductConfig | None:
    config_path = Path(paths.config)
    if not config_path.exists():
        return None
    return CodeProductConfig.model_validate_json(config_path.read_text(encoding="utf-8"))


def write_code_product_config(paths: CodeProductPaths, config: CodeProductConfig) -> None:
    root = Path(paths.root)
    root.mkdir(parents=True, exist_ok=True)
    Path(paths.config).write_text(config.model_dump_json(indent=2), encoding="utf-8")


def load_code_product_session(paths: CodeProductPaths) -> CodingCliSession | None:
    session_path = Path(paths.session)
    if not session_path.exists():
        return None
    return CodingCliSession.model_validate_json(session_path.read_text(encoding="utf-8"))


def save_code_product_session(paths: CodeProductPaths, session: CodingCliSession) -> None:
    Path(paths.root).mkdir(parents=True, exist_ok=True)
    Path(paths.session).write_text(session.model_dump_json(indent=2), encoding="utf-8")


def append_code_product_transcript(
    paths: CodeProductPaths,
    *,
    session: CodingCliSession,
    report: CodingOrganismReport,
) -> None:
    transcript_path = Path(paths.transcript)
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": session.updated_at,
        "session_id": session.session_id,
        "turn_number": len(session.turns),
        "trace_id": report.trace_id,
        "task_id": report.task_id,
        "objective": report.objective,
        "status": report.status,
        "candidate_id": report.candidate_id,
        "target_files": list(report.target_files),
        "test_plan": list(report.test_plan),
        "risks": list(report.risks),
    }
    with transcript_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        handle.write("\n")
