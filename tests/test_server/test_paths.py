"""Tests for ``dan.server.paths.resolve_graphs_dir``."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def test_resolve_graphs_dir_env_wins(monkeypatch, tmp_path: Path) -> None:
    from dan.server import paths as paths_mod

    target = tmp_path / "g"
    target.mkdir()
    monkeypatch.setenv("DAN_GRAPHS_DIR", str(target))
    assert paths_mod.resolve_graphs_dir() == str(target)


def test_resolve_graphs_dir_marker_file(monkeypatch, tmp_path: Path) -> None:
    from dan.server import paths as paths_mod

    monkeypatch.delenv("DAN_GRAPHS_DIR", raising=False)
    dan_home = tmp_path / ".dan"
    dan_home.mkdir()
    graphs = tmp_path / "repo" / "graphs"
    graphs.mkdir(parents=True)
    (dan_home / "graphs_dir").write_text(f"{graphs}\n", encoding="utf-8")
    monkeypatch.setattr(paths_mod.Path, "home", classmethod(lambda cls: tmp_path))
    assert paths_mod.resolve_graphs_dir() == str(graphs)


def test_resolve_graphs_dir_default_relative(monkeypatch, tmp_path: Path) -> None:
    from dan.server import paths as paths_mod

    monkeypatch.delenv("DAN_GRAPHS_DIR", raising=False)
    monkeypatch.setattr(paths_mod.Path, "home", classmethod(lambda cls: tmp_path))
    assert paths_mod.resolve_graphs_dir() == "./graphs"


def test_resolve_workspace_root_env_wins(monkeypatch, tmp_path: Path) -> None:
    from dan.server import paths as paths_mod

    target = tmp_path / "workspace"
    target.mkdir()
    monkeypatch.setenv("DAN_WORKSPACE_ROOT", str(target))
    assert paths_mod.resolve_workspace_root() == str(target)


def test_resolve_workspace_root_canonicalizes_legacy_project_env(monkeypatch) -> None:
    from dan.server import paths as paths_mod

    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv(
        "DAN_WORKSPACE_ROOT",
        "/Volumes/data/Dropbox/Projects/deep-agent-network",
    )

    assert paths_mod.resolve_workspace_root() == str(repo_root)


def test_resolve_graphs_dir_falls_back_when_cwd_missing(
    monkeypatch, tmp_path: Path
) -> None:
    from dan.server import paths as paths_mod

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.delenv("DAN_GRAPHS_DIR", raising=False)
    monkeypatch.setattr(paths_mod.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        paths_mod.Path,
        "cwd",
        classmethod(
            lambda cls: (_ for _ in ()).throw(FileNotFoundError("cwd missing"))
        ),
    )
    monkeypatch.setattr(paths_mod, "_find_repo_root", lambda: repo_root)

    assert paths_mod.resolve_graphs_dir() == str(repo_root / "graphs")


def test_resolve_workspace_root_falls_back_when_cwd_missing(
    monkeypatch, tmp_path: Path
) -> None:
    from dan.server import paths as paths_mod

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.delenv("DAN_WORKSPACE_ROOT", raising=False)
    monkeypatch.setattr(paths_mod.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        paths_mod.Path,
        "cwd",
        classmethod(
            lambda cls: (_ for _ in ()).throw(FileNotFoundError("cwd missing"))
        ),
    )
    monkeypatch.setattr(paths_mod, "_find_repo_root", lambda: repo_root)

    assert paths_mod.resolve_workspace_root() == str(repo_root)


def test_server_main_seeds_resolved_graphs_dir(monkeypatch, tmp_path: Path) -> None:
    import dan.server.__main__ as main_mod

    target = tmp_path / "stable-graphs"
    target.mkdir()
    called: dict[str, object] = {}

    def _fake_run(app: str, **kwargs: object) -> None:
        called["app"] = app
        called["kwargs"] = kwargs

    monkeypatch.setattr(main_mod, "resolve_graphs_dir", lambda: str(target))
    monkeypatch.setattr(main_mod.uvicorn, "run", _fake_run)
    monkeypatch.setattr(sys, "argv", ["dan-serve", "--no-reload"])
    monkeypatch.delenv("DAN_GRAPHS_DIR", raising=False)

    main_mod.main()

    assert os.environ["DAN_GRAPHS_DIR"] == str(target)
    assert called["app"] == "dan.server.app:app"
    kwargs = called["kwargs"]
    assert kwargs["reload"] is False
    assert kwargs["reload_excludes"] is None


def test_server_main_seeds_resolved_workspace_root(
    monkeypatch, tmp_path: Path
) -> None:
    import dan.server.__main__ as main_mod

    graphs = tmp_path / "stable-graphs"
    graphs.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    monkeypatch.setattr(main_mod, "resolve_graphs_dir", lambda: str(graphs))
    monkeypatch.setattr(main_mod, "resolve_workspace_root", lambda: str(workspace))
    monkeypatch.setattr(main_mod.uvicorn, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(sys, "argv", ["dan-serve", "--no-reload"])
    monkeypatch.delenv("DAN_GRAPHS_DIR", raising=False)
    monkeypatch.delenv("DAN_WORKSPACE_ROOT", raising=False)

    main_mod.main()

    assert os.environ["DAN_GRAPHS_DIR"] == str(graphs)
    assert os.environ["DAN_WORKSPACE_ROOT"] == str(workspace)
