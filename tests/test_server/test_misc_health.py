from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from dan.notes import default_workspace_notes_root
from dan.server import runtime_config
from dan.server.routers import dependencies
from dan.server.routers.misc import (
    health_check,
    list_organism_logs,
    list_workspace_roots,
    preview_workspace_note,
)
from dan.worker.organism_log import write_organism_log


def test_default_notes_root_uses_hugo_content_tree(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "my-knowledge-base"
    content = project / "content"
    content.mkdir(parents=True)
    monkeypatch.setenv("DAN_DEFAULT_CONTENT_ROOTS", str(project))
    monkeypatch.delenv("DAN_NOTES_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("DAN_NOTES_ROOT", raising=False)

    assert default_workspace_notes_root(workspace_root=tmp_path) == content.resolve()


@pytest.mark.asyncio
async def test_health_check_prefers_request_app_state_over_globals(monkeypatch) -> None:
    state_run_manager = SimpleNamespace(
        list_runs=lambda: [
            {"status": "running"},
            {"status": "pending"},
            {"status": "completed"},
        ]
    )
    state_engine_config = object()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                dan=SimpleNamespace(
                    run_manager=state_run_manager,
                    startup_degradations=[{"category": "capabilities", "detail": "partial"}],
                    engine_config=state_engine_config,
                )
            )
        )
    )

    monkeypatch.setattr(runtime_config, "provider_readiness_summary", lambda cfg: {"config": cfg})

    result = await health_check(request)

    assert result["status"] == "ok"
    assert result["active_runs"] == 2
    assert result["startup"] == {
        "status": "degraded",
        "issues": [{"category": "capabilities", "detail": "partial"}],
    }
    assert result["providers"] == {"config": state_engine_config}


@pytest.mark.asyncio
async def test_health_check_without_request_uses_active_app_state(monkeypatch) -> None:
    state_run_manager = SimpleNamespace(
        list_runs=lambda: [
            {"status": "running"},
            {"status": "completed"},
        ]
    )
    state_engine_config = object()
    state = SimpleNamespace(
        run_manager=state_run_manager,
        startup_degradations=[{"category": "engine", "detail": "degraded"}],
        engine_config=state_engine_config,
    )

    monkeypatch.setattr(runtime_config, "provider_readiness_summary", lambda cfg: {"config": cfg})
    monkeypatch.setattr(dependencies, "_get_active_app_state", lambda: state)

    result = await health_check()

    assert result["status"] == "ok"
    assert result["active_runs"] == 1
    assert result["startup"] == {
        "status": "degraded",
        "issues": [{"category": "engine", "detail": "degraded"}],
    }
    assert result["providers"] == {"config": state_engine_config}


@pytest.mark.asyncio
async def test_list_organism_logs_discovers_dan_code_control_plane(
    tmp_path: Path,
) -> None:
    control_log = tmp_path / ".dan-code" / "control-plane-events.jsonl"
    write_organism_log(
        control_log,
        [
            {
                "schema": "organism_log_v1",
                "stream": "control_plane",
                "product": "dan_code",
                "session_id": "code-session-control",
                "trace_id": "trace:code-control-1",
                "timestamp": "2026-04-23T00:00:00Z",
                "event": "orchestrator.turn.decision.started",
                "row_kind": "span_start",
                "span_kind": "control_stage",
                "span_id": "decision-1",
            },
            {
                "schema": "organism_log_v1",
                "stream": "control_plane",
                "product": "dan_code",
                "session_id": "code-session-control",
                "trace_id": "trace:code-control-1",
                "timestamp": "2026-04-23T00:00:01Z",
                "event": "orchestrator.turn.decision.completed",
                "row_kind": "span_end",
                "span_kind": "control_stage",
                "span_id": "decision-1",
                "status": "completed",
            },
        ],
    )

    payload = await list_organism_logs(root_path=str(tmp_path), limit=10)
    log_by_path = {item["path"]: item for item in payload["logs"]}
    summary = log_by_path[str(control_log.resolve())]

    assert summary["display_name"] == "Code control plane"
    assert summary["product"] == "dan_code"
    assert summary["stream_kind"] == "control_plane"


@pytest.mark.asyncio
async def test_list_workspace_roots_suggests_default_and_query_matches(
    monkeypatch,
    tmp_path: Path,
) -> None:
    alpha = tmp_path / "alpha-project"
    beta = tmp_path / "beta-project"
    alpha.mkdir()
    beta.mkdir()
    monkeypatch.setenv("DAN_WORKSPACE_ROOT", str(tmp_path))

    payload = await list_workspace_roots(query=str(tmp_path / "alp"), limit=10)
    suggested_paths = [item["path"] for item in payload["suggestions"]]

    assert payload["root"] == str(tmp_path.resolve())
    assert str(tmp_path.resolve()) in suggested_paths
    assert str(alpha.resolve()) in suggested_paths
    assert str(beta.resolve()) not in suggested_paths

    peer_payload = await list_workspace_roots(query=str(alpha), limit=10)
    peer_paths = [item["path"] for item in peer_payload["suggestions"]]

    assert str(alpha.resolve()) in peer_paths
    assert str(beta.resolve()) in peer_paths


@pytest.mark.asyncio
async def test_preview_workspace_note_strips_frontmatter_and_caches(
    monkeypatch,
    tmp_path: Path,
) -> None:
    note = tmp_path / "notes" / "example.md"
    note.parent.mkdir()
    note.write_text(
        "---\ntitle: Example Note\npageID: example-note\ntags: [alpha]\n---\n\n# Heading\n\nBody text.\n\n{{< summary \"peer-note\" >}}\n\nSee @peer-note.",
        encoding="utf-8",
    )
    monkeypatch.setenv("DAN_NOTES_WORKSPACE_ROOT", str(tmp_path))

    first = await preview_workspace_note(path=str(note))
    second = await preview_workspace_note(path=str(note))

    assert first == second
    assert first["note"]["title"] == "Example Note"
    assert first["note"]["page_id"] == "example-note"
    assert first["body_markdown"].startswith("# Heading")
    assert "title: Example Note" not in first["body_markdown"]
    assert "Summary transclusion: [@peer-note](#peer-note)" in first["preview_markdown"]
    assert "[@peer-note](#peer-note)" in first["preview_markdown"]
    assert "Heading" in first["compiled_html"]
