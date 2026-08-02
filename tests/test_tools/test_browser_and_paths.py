from __future__ import annotations

from pathlib import Path

import pytest

from dan.tools._workspace import validate_path
from dan.tools.browser_control import MockBrowserController
from dan.tools.browser_download import browser_download
from dan.tools.file_read import file_read
from dan.tools.file_write import file_write
from dan.tools.list_directory import list_directory


@pytest.mark.asyncio
async def test_browser_download_uses_selector_and_destination(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    controller = MockBrowserController()

    async def fake_get_controller() -> MockBrowserController:
        return controller

    monkeypatch.setattr("dan.tools._browser_session.get_controller", fake_get_controller)

    destination = tmp_path / "papers" / "acemoglu2012network.pdf"
    result = await browser_download(
        selector="a.download-pdf",
        destination_path=str(destination),
        timeout_seconds=12.5,
    )

    assert result["downloaded"] is True
    assert result["path"] == str(destination)
    assert controller.actions[-1] == {
        "action": "download",
        "selector": "a.download-pdf",
        "destination_path": str(destination),
        "timeout": 12.5,
    }


@pytest.mark.asyncio
async def test_file_tools_accept_absolute_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("DAN_WORKSPACE_ROOT", str(workspace))

    external_dir = tmp_path / "external-papers"
    external_dir.mkdir()
    paper_note = external_dir / "index.md"

    await file_write(path=str(paper_note), content="# Notes\n")
    read_result = await file_read(path=str(paper_note))
    list_result = await list_directory(path=str(external_dir))

    assert paper_note.read_text(encoding="utf-8") == "# Notes\n"
    assert read_result["content"] == "# Notes\n"
    assert any(entry["path"] == str(paper_note) for entry in list_result["entries"])


def test_relative_paths_stay_workspace_scoped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("DAN_WORKSPACE_ROOT", str(workspace))

    inside = validate_path("notes/index.md")
    assert inside == str((workspace / "notes" / "index.md").resolve())

    with pytest.raises(ValueError):
        validate_path("../outside.md")
