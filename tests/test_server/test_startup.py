from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from dan.blocks.models import MANIFEST_FILENAME
from dan.blocks.registry import BlockRegistry
from dan.engine.recipe.session_store import FurnaceSessionStore
from dan.server.app_state import AppState
from dan.server.startup import init_stores


def _write_block_manifest(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / MANIFEST_FILENAME).write_text(
        json.dumps(
            {
                "name": "demo-block",
                "version": "1.0.0",
                "description": "demo",
                "block_type": "workflow",
            }
        ),
        encoding="utf-8",
    )


def test_block_registry_scan_survives_unwritable_index(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _write_block_manifest(workspace / ".dan" / "blocks" / "demo-block" / "1.0.0")

    registry = BlockRegistry(workspace=workspace)
    original_write_text = Path.write_text

    def _flaky_write_text(path: Path, text: str, *args, **kwargs):
        if path.name == "_index.json":
            raise PermissionError("read-only home")
        return original_write_text(path, text, *args, **kwargs)

    with patch.object(Path, "write_text", new=_flaky_write_text):
        registry.scan()

    block = registry.get_block("demo-block", "1.0.0")
    assert block is not None
    assert block.name == "demo-block"


def test_furnace_session_store_is_lazy(tmp_path: Path) -> None:
    base_dir = tmp_path / "sessions"
    store = FurnaceSessionStore(base_dir=base_dir)
    assert not base_dir.exists()

    session = store.create_session("corpus", "recipe", ["paper-1"])

    assert base_dir.exists()
    assert (base_dir / f"{session.session_id}.json").exists()


@pytest.mark.asyncio
async def test_init_stores_skips_furnace_when_disabled(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DAN_FURNACE_API_ENABLED", "0")
    state = AppState(graphs_dir=str(tmp_path / "graphs"))

    with patch("dan.blocks.registry.BlockRegistry.scan", return_value=None):
        await init_stores(state)

    assert state.run_store is not None
    assert state.furnace_enabled is False
    assert state.furnace_session_store is None


@pytest.mark.asyncio
async def test_init_stores_survives_block_registry_scan_failure(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DAN_FURNACE_API_ENABLED", "0")
    state = AppState(graphs_dir=str(tmp_path / "graphs"))

    with patch(
        "dan.blocks.registry.BlockRegistry.scan",
        side_effect=PermissionError("read-only home"),
    ):
        await init_stores(state)

    assert state.run_store is not None
    assert state.block_registry is not None
