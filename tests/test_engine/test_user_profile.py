"""Tests for UserProfile model, persistence, and helpers (Plan 26-3)."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dan.engine.user_profile import (
    RecentWorkflow,
    UserProfile,
    format_recent_workflows,
    load_user_profile,
    save_user_profile,
)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

class TestUserProfileDefaults:
    def test_default_user_id(self):
        p = UserProfile()
        assert p.user_id == "local"

    def test_default_display_name_empty(self):
        p = UserProfile()
        assert p.display_name == ""

    def test_default_preferred_models_empty(self):
        p = UserProfile()
        assert p.preferred_models == {}

    def test_default_preferred_output_format_empty(self):
        p = UserProfile()
        assert p.preferred_output_format == ""

    def test_default_common_domains_empty(self):
        p = UserProfile()
        assert p.common_domains == []

    def test_default_session_count_zero(self):
        p = UserProfile()
        assert p.session_count == 0

    def test_default_recent_workflows_empty(self):
        p = UserProfile()
        assert p.recent_workflows == []

    def test_timestamps_are_utc(self):
        p = UserProfile()
        assert p.created_at.tzinfo is not None
        assert p.updated_at.tzinfo is not None


# ---------------------------------------------------------------------------
# touch_workflow
# ---------------------------------------------------------------------------

class TestTouchWorkflow:
    def test_adds_workflow(self):
        p = UserProfile()
        p.touch_workflow("wf-1")
        assert len(p.recent_workflows) == 1
        assert p.recent_workflows[0].workflow_id == "wf-1"

    def test_deduplicates(self):
        p = UserProfile()
        p.touch_workflow("wf-1")
        p.touch_workflow("wf-2")
        p.touch_workflow("wf-1")
        assert len(p.recent_workflows) == 2
        assert p.recent_workflows[0].workflow_id == "wf-1"
        assert p.recent_workflows[1].workflow_id == "wf-2"

    def test_trims_to_10(self):
        p = UserProfile()
        for i in range(15):
            p.touch_workflow(f"wf-{i}")
        assert len(p.recent_workflows) == 10
        assert p.recent_workflows[0].workflow_id == "wf-14"

    def test_updates_timestamp(self):
        p = UserProfile()
        before = p.updated_at
        p.touch_workflow("wf-x")
        assert p.updated_at >= before


# ---------------------------------------------------------------------------
# increment_session
# ---------------------------------------------------------------------------

class TestIncrementSession:
    def test_increments(self):
        p = UserProfile()
        assert p.session_count == 0
        p.increment_session()
        assert p.session_count == 1
        p.increment_session()
        assert p.session_count == 2

    def test_updates_timestamp(self):
        p = UserProfile()
        before = p.updated_at
        p.increment_session()
        assert p.updated_at >= before


# ---------------------------------------------------------------------------
# merge_preferences
# ---------------------------------------------------------------------------

class TestMergePreferences:
    def test_merge_models_into_empty(self):
        p = UserProfile()
        p.merge_preferences(models={"drafting": "claude-sonnet-4-6"})
        assert p.preferred_models == {"drafting": "claude-sonnet-4-6"}

    def test_merge_models_no_overwrite(self):
        p = UserProfile(preferred_models={"drafting": "gpt-4o"})
        p.merge_preferences(models={"drafting": "claude-sonnet-4-6", "review": "gpt-4o"})
        assert p.preferred_models["drafting"] == "gpt-4o"
        assert p.preferred_models["review"] == "gpt-4o"

    def test_merge_domains_deduplicates(self):
        p = UserProfile(common_domains=["scientific writing", "supply chain"])
        p.merge_preferences(domains=["paper_rendering", "equity research"])
        assert p.common_domains == ["paper_rendering", "supply_chain_management", "equity_research"]

    def test_merge_domains_normalizes_unknown_labels_to_safe_slug(self):
        p = UserProfile()
        p.merge_preferences(domains=["Machine Learning", "machine_learning", "Ops / Research"])
        assert p.common_domains == ["machine_learning", "ops_research"]

    def test_merge_output_format_only_if_empty(self):
        p = UserProfile(preferred_output_format="markdown")
        p.merge_preferences(output_format="latex")
        assert p.preferred_output_format == "markdown"

    def test_merge_output_format_into_empty(self):
        p = UserProfile()
        p.merge_preferences(output_format="json")
        assert p.preferred_output_format == "json"

    def test_merge_none_args_no_change(self):
        p = UserProfile(preferred_models={"x": "y"})
        p.merge_preferences(models=None, domains=None, output_format=None)
        assert p.preferred_models == {"x": "y"}

    def test_merge_updates_timestamp(self):
        p = UserProfile()
        before = p.updated_at
        p.merge_preferences(domains=["ml"])
        assert p.updated_at >= before


class TestMergeSearchDirs:
    def test_merge_search_dirs_adds_unique_directories(self):
        p = UserProfile(search_dirs=["/tmp/a"])
        changed = p.merge_search_dirs(["/tmp/a", "/tmp/b", "~/tmp/c"])
        assert changed is True
        assert "/tmp/a" in p.search_dirs
        assert "/tmp/b" in p.search_dirs
        assert any(path.endswith("/tmp/c") for path in p.search_dirs)

    def test_merge_search_dirs_no_change_for_duplicates(self):
        p = UserProfile(search_dirs=["/tmp/a"])
        changed = p.merge_search_dirs(["/tmp/a"])
        assert changed is False
        assert p.search_dirs == ["/tmp/a"]


# ---------------------------------------------------------------------------
# load / save persistence
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_load_missing_file_returns_default(self, tmp_path: Path):
        p = load_user_profile(tmp_path / "missing.json")
        assert p.user_id == "local"
        assert p.session_count == 0

    def test_load_corrupt_file_returns_default(self, tmp_path: Path):
        bad = tmp_path / "bad.json"
        bad.write_text("not valid json!!!", encoding="utf-8")
        p = load_user_profile(bad)
        assert p.user_id == "local"

    def test_save_creates_parent_dirs(self, tmp_path: Path):
        p = UserProfile(display_name="test")
        dest = tmp_path / "sub" / "dir" / "profile.json"
        save_user_profile(p, dest)
        assert dest.exists()

    def test_round_trip(self, tmp_path: Path):
        original = UserProfile(
            user_id="u1",
            display_name="Alice",
            preferred_models={"drafting": "gpt-4o"},
            common_domains=["equity_research"],
            session_count=5,
        )
        original.touch_workflow("wf-abc")
        path = tmp_path / "profile.json"
        save_user_profile(original, path)
        loaded = load_user_profile(path)
        assert loaded.user_id == "u1"
        assert loaded.display_name == "Alice"
        assert loaded.preferred_models == {"drafting": "gpt-4o"}
        assert loaded.common_domains == ["equity_research"]
        assert loaded.session_count == 5
        assert len(loaded.recent_workflows) == 1
        assert loaded.recent_workflows[0].workflow_id == "wf-abc"

    def test_load_normalizes_legacy_domains_and_persists_cleanup(self, tmp_path: Path):
        path = tmp_path / "profile.json"
        raw = {
            "user_id": "u1",
            "common_domains": [
                "scientific writing",
                "equity research",
                "Machine Learning",
                "equity_research",
            ],
        }
        path.write_text(json.dumps(raw), encoding="utf-8")

        loaded = load_user_profile(path)

        assert loaded.common_domains == [
            "paper_rendering",
            "equity_research",
            "machine_learning",
        ]
        persisted = json.loads(path.read_text(encoding="utf-8"))
        assert persisted["common_domains"] == [
            "paper_rendering",
            "equity_research",
            "machine_learning",
        ]

    def test_save_is_atomic(self, tmp_path: Path):
        """After save, no .tmp file lingers."""
        path = tmp_path / "profile.json"
        save_user_profile(UserProfile(), path)
        assert not path.with_suffix(".tmp").exists()

    def test_env_profile_path_is_honored(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        path = tmp_path / "env-profile.json"
        monkeypatch.setenv("DAN_PROFILE_PATH", str(path))
        save_user_profile(UserProfile(display_name="Env User"))
        loaded = load_user_profile()
        assert path.exists()
        assert loaded.display_name == "Env User"


# ---------------------------------------------------------------------------
# format_recent_workflows
# ---------------------------------------------------------------------------

class TestFormatRecentWorkflows:
    def test_empty_profile_returns_empty(self):
        p = UserProfile()
        assert format_recent_workflows(p) == ""

    def test_formats_workflows(self):
        now = time.time()
        p = UserProfile(
            recent_workflows=[
                RecentWorkflow(workflow_id="alpha", opened_at=now - 120),
                RecentWorkflow(workflow_id="beta", opened_at=now - 7200),
                RecentWorkflow(workflow_id="gamma", opened_at=now - 172800),
            ]
        )
        result = format_recent_workflows(p)
        assert "Recent workflows:" in result
        assert "(1) alpha" in result
        assert "(2) beta" in result
        assert "(3) gamma" in result
        assert "Resume? [1-5/new]" in result

    def test_shows_at_most_5(self):
        now = time.time()
        p = UserProfile(
            recent_workflows=[
                RecentWorkflow(workflow_id=f"wf-{i}", opened_at=now - i * 60)
                for i in range(8)
            ]
        )
        result = format_recent_workflows(p)
        assert "(5)" in result
        assert "(6)" not in result

    def test_age_formatting_minutes(self):
        now = time.time()
        p = UserProfile(
            recent_workflows=[RecentWorkflow(workflow_id="r", opened_at=now - 300)]
        )
        result = format_recent_workflows(p)
        assert "5m ago" in result

    def test_age_formatting_hours(self):
        now = time.time()
        p = UserProfile(
            recent_workflows=[RecentWorkflow(workflow_id="r", opened_at=now - 10800)]
        )
        result = format_recent_workflows(p)
        assert "3h ago" in result

    def test_age_formatting_days(self):
        now = time.time()
        p = UserProfile(
            recent_workflows=[RecentWorkflow(workflow_id="r", opened_at=now - 259200)]
        )
        result = format_recent_workflows(p)
        assert "3d ago" in result
