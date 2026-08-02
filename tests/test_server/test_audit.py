"""Tests for ChatAuditRecord model and ChatAuditStore persistence."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from dan.server.audit import (
    ChatAuditRecord,
    ChatAuditStore,
    ToolCallRecord,
    _redact_secrets,
)
from dan.server.chat_manager import _try_persist_audit


# ---------------------------------------------------------------------------
# Model defaults
# ---------------------------------------------------------------------------


class TestChatAuditRecordModel:
    def test_defaults(self):
        rec = ChatAuditRecord()
        assert len(rec.id) == 16
        assert rec.timestamp > 0
        assert rec.surface_id == ""
        assert rec.prompt_module_ids == []
        assert rec.workflow_guidance_injected is False
        assert rec.workflow_guidance_surface == ""
        assert rec.tool_calls == []
        assert rec.cited_sources == []
        assert rec.memory_item_ids == []
        assert rec.intent == ""
        assert rec.mode == ""

    def test_with_redacted_prompt(self):
        rec = ChatAuditRecord(
            prompt_messages=[
                {"role": "system", "content": "Your API_KEY=sk-abc123456789012345678901"},
                {"role": "user", "content": "Hello"},
            ],
        )
        safe = rec.with_redacted_prompt()
        assert "sk-abc" not in safe.prompt_messages[0]["content"]
        assert "[REDACTED]" in safe.prompt_messages[0]["content"]
        assert safe.prompt_messages[1]["content"] == "Hello"

    def test_roundtrip_json(self):
        rec = ChatAuditRecord(
            surface_id="wa.123",
            user_message="Find papers on LLM agents",
            intent="research",
            prompt_module_ids=["workflow_generation_contract"],
            workflow_guidance_injected=True,
            workflow_guidance_surface="build",
            tool_calls=[
                ToolCallRecord(
                    tool_name="web_search",
                    args={"query": "LLM agents"},
                    result_summary="10 results",
                    source_urls=["https://arxiv.org/123"],
                ),
            ],
            assistant_message="Here are the papers...",
            cited_sources=["https://arxiv.org/123"],
        )
        raw = rec.model_dump_json()
        restored = ChatAuditRecord.model_validate_json(raw)
        assert restored.surface_id == rec.surface_id
        assert restored.prompt_module_ids == ["workflow_generation_contract"]
        assert restored.workflow_guidance_injected is True
        assert restored.workflow_guidance_surface == "build"
        assert restored.tool_calls[0].tool_name == "web_search"
        assert restored.cited_sources == ["https://arxiv.org/123"]


# ---------------------------------------------------------------------------
# ToolCallRecord
# ---------------------------------------------------------------------------


class TestToolCallRecord:
    def test_captures_sources(self):
        tc = ToolCallRecord(
            tool_name="pdf_read",
            args={"path": "/tmp/paper.pdf"},
            result_summary="Read 12 pages",
            status="success",
            duration_ms=350,
            source_urls=["https://doi.org/10.1234/example"],
            source_files=["/tmp/paper.pdf"],
        )
        assert tc.source_urls == ["https://doi.org/10.1234/example"]
        assert tc.source_files == ["/tmp/paper.pdf"]
        assert tc.duration_ms == 350

    def test_defaults(self):
        tc = ToolCallRecord(tool_name="noop")
        assert tc.status == "success"
        assert tc.source_urls == []
        assert tc.source_files == []
        assert tc.duration_ms == 0


# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------


class TestRedaction:
    def test_env_var_style(self):
        msgs = [{"role": "system", "content": "SECRET_KEY=hunter2 and more text"}]
        out = _redact_secrets(msgs)
        assert "hunter2" not in out[0]["content"]

    def test_bearer_token(self):
        msgs = [{"role": "user", "content": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc"}]
        out = _redact_secrets(msgs)
        assert "eyJhbG" not in out[0]["content"]

    def test_no_false_positive_on_normal_text(self):
        msgs = [{"role": "user", "content": "Please summarize this paper"}]
        out = _redact_secrets(msgs)
        assert out[0]["content"] == "Please summarize this paper"


# ---------------------------------------------------------------------------
# Store: append and load
# ---------------------------------------------------------------------------


class TestChatAuditStore:
    def test_append_and_load(self, tmp_path: Path):
        store = ChatAuditStore(base_dir=tmp_path)
        rec = ChatAuditRecord(
            surface_id="test-surface",
            project_id="proj-1",
            task_id="task-1",
            turn_id="turn-aaa",
            user_message="Hello",
            assistant_message="Hi there",
        )
        store.append(rec)

        loaded = store.load_by_surface("test-surface")
        assert len(loaded) == 1
        assert loaded[0].turn_id == "turn-aaa"
        assert loaded[0].user_message == "Hello"

    def test_load_by_date(self, tmp_path: Path):
        store = ChatAuditStore(base_dir=tmp_path)
        rec = ChatAuditRecord(surface_id="s1", user_message="dated")
        store.append(rec)

        today = ChatAuditStore._date_key(rec.timestamp)
        assert len(store.load_by_surface("s1", date=today)) == 1
        assert len(store.load_by_surface("s1", date="1999-01-01")) == 0

    def test_load_by_project(self, tmp_path: Path):
        store = ChatAuditStore(base_dir=tmp_path)
        store.append(ChatAuditRecord(surface_id="s1", project_id="proj-A", turn_id="t1"))
        store.append(ChatAuditRecord(surface_id="s2", project_id="proj-A", turn_id="t2"))
        store.append(ChatAuditRecord(surface_id="s1", project_id="proj-B", turn_id="t3"))

        results = store.load_by_project("proj-A")
        assert len(results) == 2
        assert {r.turn_id for r in results} == {"t1", "t2"}

    def test_load_by_turn(self, tmp_path: Path):
        store = ChatAuditStore(base_dir=tmp_path)
        store.append(ChatAuditRecord(surface_id="s1", turn_id="needle"))
        store.append(ChatAuditRecord(surface_id="s1", turn_id="other"))

        found = store.load_by_turn("needle")
        assert found is not None
        assert found.turn_id == "needle"
        assert store.load_by_turn("nonexistent") is None

    def test_failure_does_not_raise(self, tmp_path: Path):
        store = ChatAuditStore(base_dir=tmp_path)
        surface_dir = tmp_path / "bad_surface"
        surface_dir.mkdir()
        corrupt_file = surface_dir / "2025-01-01.jsonl"
        corrupt_file.write_text("{{not valid json}}\n", encoding="utf-8")

        results = store.load_by_surface("bad_surface", date="2025-01-01")
        assert results == []

    def test_limit_respected(self, tmp_path: Path):
        store = ChatAuditStore(base_dir=tmp_path)
        for i in range(10):
            store.append(ChatAuditRecord(surface_id="s1", turn_id=f"t{i}"))

        assert len(store.load_by_surface("s1", limit=3)) == 3

    def test_redaction_on_persist(self, tmp_path: Path):
        store = ChatAuditStore(base_dir=tmp_path)
        rec = ChatAuditRecord(
            surface_id="s1",
            prompt_messages=[
                {"role": "system", "content": "API_KEY=supersecret123"},
            ],
        )
        store.append(rec)

        loaded = store.load_by_surface("s1")
        assert len(loaded) == 1
        assert "supersecret" not in loaded[0].prompt_messages[0]["content"]
        assert "[REDACTED]" in loaded[0].prompt_messages[0]["content"]

    def test_redaction_on_tool_args_and_metadata(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        store = ChatAuditStore(base_dir=tmp_path)

        def _patched_init(self, base_dir=None):
            self._base = Path(base_dir or tmp_path)
            self._base.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(ChatAuditStore, "__init__", _patched_init)

        _try_persist_audit(
            workflow_id="wf-1",
            message_id="turn-secret",
            user_message="Please save DAN_OPENAI_API_KEY=sk-secret-secret-secret",
            assistant_message="",
            mode="conversation",
            model="gpt-4o-mini",
            audit_tool_records=[
                {
                    "tool_name": "set_config",
                    "args": {"key": "DAN_OPENAI_API_KEY", "value": "sk-secret-secret-secret"},
                    "args_preview": '{"key":"DAN_OPENAI_API_KEY","value":"sk-secret-secret-secret"}',
                    "output_preview": "Set DAN_OPENAI_API_KEY=sk-secret-secret-secret",
                    "status": "success",
                    "duration_ms": 12,
                    "result_data": {"run_id": "run-123"},
                }
            ],
            prompt_messages=[
                {"role": "user", "content": "Bearer top-secret-token-value"},
            ],
            surface="server",
            error="provider failed",
            audit_metadata={
                "project_id": "proj-1",
                "task_id": "task-1",
                "intent": "direct_task",
                "reuse_decision": "generate",
                "prompt_module_ids": ["workflow_generation_contract"],
                "workflow_guidance_injected": True,
                "workflow_guidance_surface": "mutate",
            },
        )

        loaded = store.load_by_turn("turn-secret")
        assert loaded is not None
        assert loaded.project_id == "proj-1"
        assert loaded.task_id == "task-1"
        assert loaded.intent == "direct_task"
        assert loaded.run_id == "run-123"
        assert loaded.prompt_module_ids == ["workflow_generation_contract"]
        assert loaded.workflow_guidance_injected is True
        assert loaded.workflow_guidance_surface == "mutate"
        assert "[REDACTED]" in loaded.user_message
        assert "[REDACTED]" in loaded.prompt_messages[0]["content"]
        assert "[REDACTED]" in loaded.tool_calls[0].result_summary
        assert "[REDACTED]" in str(loaded.tool_calls[0].args)
        assert loaded.assistant_message == "provider failed"

    def test_empty_surface_returns_empty(self, tmp_path: Path):
        store = ChatAuditStore(base_dir=tmp_path)
        assert store.load_by_surface("nonexistent") == []
        assert store.load_by_project("nonexistent") == []
