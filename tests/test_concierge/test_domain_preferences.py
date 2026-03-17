from __future__ import annotations

from pathlib import Path

from dan.engine.memory_adapters import ProfileAdapter
from dan.engine.memory_kernel import MemoryKernel
from dan.engine.user_profile import UserProfile
from dan.server.concierge.command_registry import get_default_registry
from dan.server.concierge.domain_preferences import handle_domains_command


class _StubBehaviorStore:
    def __init__(self, keyword_map: dict[str, list[str]] | None = None) -> None:
        self._keyword_map = keyword_map or {}

    def get(self, key: str) -> dict[str, list[str]] | None:
        if key == "domains/keyword_maps":
            return self._keyword_map
        return None


def test_default_registry_includes_domains_command() -> None:
    registry = get_default_registry()

    descriptor = registry.get("/domains")

    assert descriptor is not None
    assert descriptor.handler == "dan.server.concierge.domain_preferences.handle_domains_command"
    assert "known" in descriptor.subcommands
    assert "add" in descriptor.subcommands


def test_domains_add_normalizes_persists_and_syncs_memory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    profile_path = tmp_path / "profile.json"
    monkeypatch.setenv("DAN_PROFILE_PATH", str(profile_path))
    profile = UserProfile(common_domains=["equity_research"])
    kernel = MemoryKernel(base_dir=str(tmp_path / "memory-kernel"))
    ProfileAdapter.import_profile(profile, kernel)

    result = handle_domains_command(
        "/domains add scientific writing",
        profile,
        memory_kernel=kernel,
    )

    assert "paper_rendering" in result
    assert profile.common_domains == ["equity_research", "paper_rendering"]
    assert kernel.get("fact:domain:paper_rendering") is not None
    saved = UserProfile.model_validate_json(profile_path.read_text(encoding="utf-8"))
    assert saved.common_domains == ["equity_research", "paper_rendering"]


def test_domains_remove_prunes_profile_and_memory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    profile_path = tmp_path / "profile.json"
    monkeypatch.setenv("DAN_PROFILE_PATH", str(profile_path))
    profile = UserProfile(common_domains=["equity_research", "paper_rendering"])
    kernel = MemoryKernel(base_dir=str(tmp_path / "memory-kernel"))
    ProfileAdapter.import_profile(profile, kernel)

    result = handle_domains_command(
        "/domains remove scientific writing",
        profile,
        memory_kernel=kernel,
    )

    assert "Removed 1 saved domain preference." in result
    assert profile.common_domains == ["equity_research"]
    assert kernel.get("fact:domain:paper_rendering") is None
    saved = UserProfile.model_validate_json(profile_path.read_text(encoding="utf-8"))
    assert saved.common_domains == ["equity_research"]


def test_domains_known_uses_behavior_store_override() -> None:
    profile = UserProfile()
    store = _StubBehaviorStore({"Machine Learning": ["pytorch", "sklearn"]})

    result = handle_domains_command(
        "/domains known",
        profile,
        behavior_store=store,
    )

    assert "machine_learning" in result
    assert "paper_rendering" not in result
