"""Workspace-local product state for the DAN Code CLI."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

CODE_PRODUCT_NAME = "DAN Code"
CODE_PRODUCT_DIRNAME = ".dan-code"
CODE_PRODUCT_SESSION_FORMAT_VERSION = 2


def _compute_runtime_build_id() -> str:
    digest = hashlib.sha256()
    digest.update(str(CODE_PRODUCT_SESSION_FORMAT_VERSION).encode("utf-8"))
    fingerprint_paths = (
        Path(__file__).resolve(),
        Path(__file__).with_name("code.py").resolve(),
        Path(__file__).resolve().parents[1] / "worker" / "organisms" / "coding_conversation.py",
    )
    for path in fingerprint_paths:
        digest.update(str(path).encode("utf-8"))
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<missing>")
    return digest.hexdigest()[:12]


CODE_PRODUCT_RUNTIME_BUILD_ID = _compute_runtime_build_id()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _resolve_path(value: str | Path, *, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _normalize_text_list(value: Any) -> list[str]:
    if value is None:
        return []

    normalized: list[str] = []
    if isinstance(value, str):
        texts = [value]
    elif isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
        if items and all(len(item) <= 1 for item in items):
            texts = ["".join(items)]
        else:
            texts = items
    elif isinstance(value, set):
        texts = [str(item) for item in value]
    else:
        texts = [str(value)]

    for text in texts:
        for line in str(text).splitlines():
            cleaned = line.strip()
            if not cleaned:
                continue
            if cleaned.startswith("- "):
                cleaned = cleaned[2:].strip()
            normalized.append(cleaned)
    if normalized:
        return normalized

    cleaned = " ".join(str(text).strip() for text in texts).strip()
    return [cleaned] if cleaned else []


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
    event_log_path: str | None = None
    target_files: list[str] = Field(default_factory=list)
    test_plan: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    outputs: dict[str, Any] = Field(default_factory=dict)
    handoff_count: int = 0
    signal_count: int = 0
    error: str | None = None
    trace_rows: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("target_files", "test_plan", "risks", mode="before")
    @classmethod
    def _normalize_list_fields(cls, value: Any) -> list[str]:
        return _normalize_text_list(value)

    def has_material_output(self) -> bool:
        return bool(
            self.candidate_id
            or self.target_files
            or " ".join(self.change_summary.split())
        )

    def is_failed_no_output(self) -> bool:
        return self.status.strip().lower() != "completed" and not self.has_material_output()


class CodingConversationEntry(BaseModel):
    """One durable user/assistant exchange in the DAN Code shell."""

    role: str
    text: str
    kind: str = "message"
    created_at: str = Field(default_factory=_utcnow_iso)


class CodingCliSession(BaseModel):
    """Persistent session state for the product shell."""

    session_format_version: int = Field(default=CODE_PRODUCT_SESSION_FORMAT_VERSION)
    runtime_build_id: str = Field(default=CODE_PRODUCT_RUNTIME_BUILD_ID)
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

    def context_reports(self, *, limit: int = 4) -> list[CodingOrganismReport]:
        selected: list[CodingOrganismReport] = []
        for report in reversed(self.turns):
            if report.is_failed_no_output():
                continue
            selected.append(report)
            if len(selected) >= limit:
                break
        return list(reversed(selected))

    def carry_forward_findings(self, *, limit: int = 3) -> list[str]:
        findings: list[str] = []
        for report in self.context_reports(limit=limit):
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

    def refresh_for_current_runtime(self) -> bool:
        if (
            self.session_format_version == CODE_PRODUCT_SESSION_FORMAT_VERSION
            and self.runtime_build_id == CODE_PRODUCT_RUNTIME_BUILD_ID
        ):
            return False
        self.session_format_version = CODE_PRODUCT_SESSION_FORMAT_VERSION
        self.runtime_build_id = CODE_PRODUCT_RUNTIME_BUILD_ID
        self.conversation = []
        self.pending_clarification = None
        self.orchestrator_state = {}
        self.updated_at = _utcnow_iso()
        return True

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
    max_tool_rounds: int | None = None
    max_tool_calls: int = 24
    completion_timeout_seconds: float | None = None


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
    session.session_format_version = CODE_PRODUCT_SESSION_FORMAT_VERSION
    session.runtime_build_id = CODE_PRODUCT_RUNTIME_BUILD_ID
    payload = session.model_dump(
        mode="json",
        exclude={
            "turns": {
                "__all__": {
                    "outputs",
                    "trace_rows",
                }
            }
        },
    )
    Path(paths.session).write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )


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
        "event_log_path": report.event_log_path,
        "target_files": list(report.target_files),
        "test_plan": list(report.test_plan),
        "risks": list(report.risks),
    }
    with transcript_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        handle.write("\n")
