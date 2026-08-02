from __future__ import annotations

import os

import pytest

from dan.server.capabilities.file_io import handle_list_directory
from dan.tools.list_directory import list_directory


def _seed_directory(root, names: list[str]) -> None:
    for name in names:
        path = root / name
        path.write_text(name)


@pytest.mark.asyncio
async def test_list_directory_returns_pagination_metadata(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("DAN_WORKSPACE_ROOT", str(tmp_path))
    _seed_directory(tmp_path, ["alpha.txt", "beta.txt", "gamma.txt", "omega.txt"])

    first_page = await list_directory(path=str(tmp_path), limit=2)

    assert first_page["count"] == 2
    assert first_page["total_count"] == 4
    assert first_page["remaining_count"] == 2
    assert first_page["truncated"] is True
    assert first_page["next_start_after"] == "beta.txt"
    assert [entry["path"] for entry in first_page["entries"]] == ["alpha.txt", "beta.txt"]

    second_page = await list_directory(
        path=str(tmp_path),
        limit=2,
        start_after=first_page["next_start_after"],
    )

    assert second_page["count"] == 2
    assert second_page["total_count"] == 2
    assert second_page["remaining_count"] == 0
    assert second_page["truncated"] is False
    assert second_page["next_start_after"] is None
    assert [entry["path"] for entry in second_page["entries"]] == ["gamma.txt", "omega.txt"]


@pytest.mark.asyncio
async def test_handle_list_directory_surfaces_continuation_guidance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("DAN_WORKSPACE_ROOT", str(tmp_path))
    _seed_directory(tmp_path, ["alpha.txt", "beta.txt", "gamma.txt", "omega.txt"])

    result = await handle_list_directory({"path": str(tmp_path), "limit": 2}, None)

    assert result.success is True
    assert isinstance(result.data, dict)
    assert result.data["truncated"] is True
    assert result.data["next_start_after"] == "beta.txt"
    assert "NOTE: This listing is partial." in result.message
    assert 'Continue with start_after="beta.txt"' in result.message
    assert "Do NOT infer absence from this cutoff." in result.message
    assert "[partial]" in result.output_preview


@pytest.mark.asyncio
async def test_list_directory_survives_missing_cwd_with_workspace_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _seed_directory(tmp_path, ["watchlist.csv"])
    monkeypatch.delenv("DAN_WORKSPACE_ROOT", raising=False)

    def _missing_cwd() -> str:
        raise FileNotFoundError("cwd vanished")

    monkeypatch.setattr(os, "getcwd", _missing_cwd)
    monkeypatch.setattr("dan.server.paths.resolve_workspace_root", lambda: str(tmp_path))

    result = await list_directory(path=".", glob_pattern="watchlist*")

    assert result["count"] == 1
    assert result["entries"][0]["name"] == "watchlist.csv"
