from __future__ import annotations

import pytest

from dan.server.capabilities.file_io import handle_file_edit


@pytest.mark.asyncio
async def test_handle_file_edit_replaces_requested_lines(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("DAN_WORKSPACE_ROOT", str(tmp_path))
    target = tmp_path / "draft.md"
    target.write_text("line one\nline two\nline three\n", encoding="utf-8")

    result = await handle_file_edit(
        {
            "path": "draft.md",
            "start_line": 2,
            "end_line": 2,
            "content": "patched line two\n",
            "mode": "replace",
        },
        None,
    )

    assert result.success is True
    assert isinstance(result.data, dict)
    assert result.data["path"] == "draft.md"
    assert result.data["mode"] == "replace"
    assert target.read_text(encoding="utf-8") == "line one\npatched line two\nline three\n"
    assert "replace draft.md lines 2-2" in result.message


@pytest.mark.asyncio
async def test_handle_file_edit_reports_missing_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("DAN_WORKSPACE_ROOT", str(tmp_path))

    result = await handle_file_edit(
        {
            "path": "missing.md",
            "start_line": 1,
            "content": "x\n",
            "mode": "insert_before",
        },
        None,
    )

    assert result.success is False
    assert "Failed to edit file" in result.message
