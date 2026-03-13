"""Tests for computer_policy.py — config, classification, allowlists, audit, lease, commands."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from dan.server.concierge.computer_policy import (
    ActionType,
    AuditEntry,
    AuditLog,
    BrowserDomainRule,
    ChunkPolicies,
    ChunkPolicy,
    ComputerControlConfig,
    FileSafetyPolicy,
    SessionOverride,
    VisionExportPolicy,
    check_vision_export,
    classify_action,
    is_app_allowed,
    is_domain_allowed,
    requires_approval,
)
from dan.server.concierge.computer_use import (
    ComputerUseController,
    ComputerUseLeaseManager,
    ObservedElement,
    UIObservation,
    handle_computer_command,
)
from dan.server.concierge.progress_ux import ProgressSession


# =========================================================================
# Config models
# =========================================================================


class TestComputerControlConfig:
    def test_defaults(self):
        cfg = ComputerControlConfig()
        assert cfg.enabled is False
        assert cfg.foreground_only is True
        assert cfg.chunk_policies.observe.enabled is True
        assert cfg.chunk_policies.browser.enabled is True
        assert cfg.chunk_policies.input.enabled is False
        assert cfg.chunk_policies.system.enabled is False

    def test_load_from_file(self, tmp_path, monkeypatch):
        config_dir = tmp_path / ".dan"
        config_dir.mkdir()
        config_file = config_dir / "computer_control.json"
        config_file.write_text(json.dumps({
            "enabled": True,
            "foreground_only": False,
            "chunk_policies": {
                "browser": {
                    "enabled": True,
                    "allowed_domains": [
                        {"pattern": "example.com", "includes_subdomains": True}
                    ],
                },
                "input": {"enabled": True, "allowed_apps": ["Safari"]},
            },
        }))
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("DAN_COMPUTER_CONTROL", raising=False)

        cfg = ComputerControlConfig.load()
        assert cfg.enabled is True
        assert cfg.foreground_only is False
        assert cfg.chunk_policies.browser.allowed_domains[0].pattern == "example.com"
        assert cfg.chunk_policies.input.enabled is True
        assert cfg.chunk_policies.input.allowed_apps == ["Safari"]

    def test_env_override_enables(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("DAN_COMPUTER_CONTROL", "1")
        cfg = ComputerControlConfig.load()
        assert cfg.enabled is True

    def test_env_override_disables(self, tmp_path, monkeypatch):
        config_dir = tmp_path / ".dan"
        config_dir.mkdir()
        (config_dir / "computer_control.json").write_text(
            json.dumps({"enabled": True})
        )
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("DAN_COMPUTER_CONTROL", "0")
        cfg = ComputerControlConfig.load()
        assert cfg.enabled is False

    def test_missing_file_returns_defaults(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("DAN_COMPUTER_CONTROL", raising=False)
        cfg = ComputerControlConfig.load()
        assert cfg.enabled is False

    def test_corrupt_file_returns_defaults(self, tmp_path, monkeypatch):
        config_dir = tmp_path / ".dan"
        config_dir.mkdir()
        (config_dir / "computer_control.json").write_text("not json")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("DAN_COMPUTER_CONTROL", raising=False)
        cfg = ComputerControlConfig.load()
        assert cfg.enabled is False

    def test_chunk_policies_roundtrip(self):
        policies = ChunkPolicies(
            observe=ChunkPolicy(enabled=True),
            browser=ChunkPolicy(
                enabled=True,
                allowed_domains=[BrowserDomainRule(pattern="google.com")],
            ),
            input=ChunkPolicy(enabled=False),
        )
        data = policies.model_dump()
        restored = ChunkPolicies.model_validate(data)
        assert restored.browser.allowed_domains[0].pattern == "google.com"
        assert restored.input.enabled is False


# =========================================================================
# Action classification
# =========================================================================


class TestClassifyAction:
    def test_read_only_actions(self):
        assert classify_action("screenshot") == "read_only"
        assert classify_action("ocr") == "read_only"
        assert classify_action("list_windows") == "read_only"
        assert classify_action("list_tabs") == "read_only"
        assert classify_action("extract_text") == "read_only"
        assert classify_action("wait_for") == "read_only"

    def test_benign_input_actions(self):
        assert classify_action("open") == "benign_input"
        assert classify_action("click") == "benign_input"
        assert classify_action("scroll") == "benign_input"
        assert classify_action("focus_window") == "benign_input"

    def test_sensitive_input_actions(self):
        assert classify_action("type_text") == "sensitive_input"
        assert classify_action("fill") == "sensitive_input"
        assert classify_action("clipboard_write") == "sensitive_input"
        assert classify_action("hotkey") == "sensitive_input"

    def test_destructive_actions(self):
        assert classify_action("download") == "destructive"
        assert classify_action("close") == "destructive"
        assert classify_action("delete") == "destructive"

    def test_system_level_actions(self):
        assert classify_action("launch_app") == "system_level"
        assert classify_action("shell") == "system_level"

    def test_destructive_target_escalation(self):
        assert classify_action("click", "submit order") == "destructive"
        assert classify_action("click", "delete account") == "destructive"
        assert classify_action("fill", "confirm payment") == "destructive"

    def test_benign_target_no_escalation(self):
        assert classify_action("click", "next page") == "benign_input"
        assert classify_action("click", "#login-button") == "benign_input"

    def test_unknown_action_defaults_to_benign(self):
        assert classify_action("unknown_action") == "benign_input"


# =========================================================================
# Domain allowlist
# =========================================================================


class TestDomainAllowlist:
    def test_empty_rules_allow_all(self):
        assert is_domain_allowed("https://anything.com", []) is True

    def test_exact_match(self):
        rules = [BrowserDomainRule(pattern="example.com")]
        assert is_domain_allowed("https://example.com/page", rules) is True
        assert is_domain_allowed("https://other.com/page", rules) is False

    def test_subdomain_match(self):
        rules = [BrowserDomainRule(pattern="example.com", includes_subdomains=True)]
        assert is_domain_allowed("https://sub.example.com", rules) is True
        assert is_domain_allowed("https://deep.sub.example.com", rules) is True

    def test_subdomain_disabled(self):
        rules = [BrowserDomainRule(pattern="example.com", includes_subdomains=False)]
        assert is_domain_allowed("https://example.com/page", rules) is True
        assert is_domain_allowed("https://sub.example.com", rules) is False

    def test_case_insensitive(self):
        rules = [BrowserDomainRule(pattern="Example.COM")]
        assert is_domain_allowed("https://EXAMPLE.com/page", rules) is True

    def test_invalid_url(self):
        rules = [BrowserDomainRule(pattern="example.com")]
        assert is_domain_allowed("not a url", rules) is False
        assert is_domain_allowed("", rules) is False

    def test_multiple_rules_any_match(self):
        rules = [
            BrowserDomainRule(pattern="google.com"),
            BrowserDomainRule(pattern="github.com"),
        ]
        assert is_domain_allowed("https://google.com", rules) is True
        assert is_domain_allowed("https://github.com", rules) is True
        assert is_domain_allowed("https://gitlab.com", rules) is False

    def test_no_partial_match(self):
        rules = [BrowserDomainRule(pattern="ample.com")]
        assert is_domain_allowed("https://example.com", rules) is False

    def test_port_ignored(self):
        rules = [BrowserDomainRule(pattern="localhost")]
        assert is_domain_allowed("http://localhost:3000/api", rules) is True


# =========================================================================
# App allowlist
# =========================================================================


class TestAppAllowlist:
    def test_empty_allows_all(self):
        chunk = ChunkPolicy(enabled=True, allowed_apps=[])
        assert is_app_allowed("AnyApp", chunk) is True

    def test_exact_match(self):
        chunk = ChunkPolicy(enabled=True, allowed_apps=["Safari", "Chrome"])
        assert is_app_allowed("Safari", chunk) is True
        assert is_app_allowed("Firefox", chunk) is False

    def test_case_insensitive(self):
        chunk = ChunkPolicy(enabled=True, allowed_apps=["Safari"])
        assert is_app_allowed("safari", chunk) is True
        assert is_app_allowed("SAFARI", chunk) is True


# =========================================================================
# Approval requirements
# =========================================================================


class TestRequiresApproval:
    def test_read_only_never_requires(self):
        cfg = ComputerControlConfig(enabled=True)
        assert requires_approval("read_only", cfg) is False

    def test_destructive_always_requires(self):
        cfg = ComputerControlConfig(enabled=True)
        assert requires_approval("destructive", cfg) is True

    def test_system_level_always_requires(self):
        cfg = ComputerControlConfig(enabled=True)
        assert requires_approval("system_level", cfg) is True

    def test_sensitive_input_requires_by_default(self):
        cfg = ComputerControlConfig(enabled=True)
        assert requires_approval("sensitive_input", cfg) is True

    def test_benign_input_no_approval(self):
        cfg = ComputerControlConfig(enabled=True)
        assert requires_approval("benign_input", cfg) is False

    def test_session_override_grants_system(self):
        cfg = ComputerControlConfig(enabled=True)
        overrides = [SessionOverride(chunk="system", granted=True)]
        assert requires_approval("system_level", cfg, overrides) is False

    def test_session_override_grants_destructive(self):
        cfg = ComputerControlConfig(enabled=True)
        overrides = [SessionOverride(chunk="destructive", granted=True)]
        assert requires_approval("destructive", cfg, overrides) is False

    def test_session_override_grants_input(self):
        cfg = ComputerControlConfig(enabled=True)
        overrides = [SessionOverride(chunk="input", granted=True)]
        assert requires_approval("sensitive_input", cfg, overrides) is False

    def test_expired_override_still_requires(self):
        cfg = ComputerControlConfig(enabled=True)
        expired = datetime.now(timezone.utc) - timedelta(hours=1)
        overrides = [SessionOverride(chunk="system", granted=True, expires_at=expired)]
        assert requires_approval("system_level", cfg, overrides) is True

    def test_non_granted_override_still_requires(self):
        cfg = ComputerControlConfig(enabled=True)
        overrides = [SessionOverride(chunk="system", granted=False)]
        assert requires_approval("system_level", cfg, overrides) is True


# =========================================================================
# Audit log
# =========================================================================


class TestAuditLog:
    def test_empty_log(self):
        log = AuditLog()
        assert len(log) == 0
        assert log.recent() == []
        summary = log.format_summary()
        assert "No computer-use actions" in summary

    def test_add_and_recent(self):
        log = AuditLog()
        log.add(AuditEntry(action="click", target="#btn", result="success"))
        log.add(AuditEntry(action="type_text", target="#input", result="success"))
        assert len(log) == 2
        recent = log.recent(1)
        assert len(recent) == 1
        assert recent[0].action == "type_text"

    def test_format_summary(self):
        log = AuditLog()
        log.add(AuditEntry(
            action="open",
            target="https://example.com",
            action_type="benign_input",
            result="success",
        ))
        summary = log.format_summary()
        assert "open" in summary
        assert "example.com" in summary
        assert "benign_input" in summary

    def test_recent_cap(self):
        log = AuditLog()
        for i in range(30):
            log.add(AuditEntry(action=f"action_{i}", result="success"))
        assert len(log) == 30
        recent = log.recent(5)
        assert len(recent) == 5
        assert recent[0].action == "action_25"


# =========================================================================
# Lease manager
# =========================================================================


class TestComputerUseLeaseManager:
    @pytest.mark.asyncio
    async def test_acquire_and_release(self):
        mgr = ComputerUseLeaseManager()
        assert not mgr.is_active()
        assert await mgr.acquire("task-1")
        assert mgr.is_active()
        assert mgr.active_task_id == "task-1"
        await mgr.release("task-1")
        assert not mgr.is_active()

    @pytest.mark.asyncio
    async def test_reentrant_acquire(self):
        mgr = ComputerUseLeaseManager()
        assert await mgr.acquire("task-1")
        assert await mgr.acquire("task-1")  # same task, OK

    @pytest.mark.asyncio
    async def test_conflict_denied(self):
        mgr = ComputerUseLeaseManager()
        assert await mgr.acquire("task-1")
        assert not await mgr.acquire("task-2")

    @pytest.mark.asyncio
    async def test_release_wrong_task_noop(self):
        mgr = ComputerUseLeaseManager()
        assert await mgr.acquire("task-1")
        await mgr.release("task-999")
        assert mgr.is_active()  # still held by task-1

    @pytest.mark.asyncio
    async def test_acquire_after_release(self):
        mgr = ComputerUseLeaseManager()
        assert await mgr.acquire("task-1")
        await mgr.release("task-1")
        assert await mgr.acquire("task-2")
        assert mgr.active_task_id == "task-2"


# =========================================================================
# /computer command handler
# =========================================================================


class TestHandleComputerCommand:
    def test_status_default(self):
        cfg = ComputerControlConfig(enabled=True)
        lease = ComputerUseLeaseManager()
        audit = AuditLog()
        result = handle_computer_command("/computer", cfg, lease, audit)
        assert "Enabled: True" in result
        assert "Active session: none" in result

    def test_status_with_chunks(self):
        cfg = ComputerControlConfig(
            enabled=True,
            chunk_policies=ChunkPolicies(
                observe=ChunkPolicy(enabled=True),
                browser=ChunkPolicy(enabled=True),
                input=ChunkPolicy(enabled=True),
            ),
        )
        lease = ComputerUseLeaseManager()
        audit = AuditLog()
        result = handle_computer_command("/computer status", cfg, lease, audit)
        assert "observe" in result
        assert "browser" in result
        assert "input" in result

    def test_doctor(self):
        cfg = ComputerControlConfig()
        lease = ComputerUseLeaseManager()
        audit = AuditLog()
        result = handle_computer_command("/computer doctor", cfg, lease, audit)
        assert "Computer Doctor" in result
        assert "Platform:" in result
        assert "Playwright:" in result

    def test_approve_with_id(self):
        cfg = ComputerControlConfig()
        lease = ComputerUseLeaseManager()
        audit = AuditLog()
        result = handle_computer_command("/computer approve req-42", cfg, lease, audit)
        assert "req-42" in result
        assert "Approval" in result

    def test_approve_missing_id(self):
        cfg = ComputerControlConfig()
        lease = ComputerUseLeaseManager()
        audit = AuditLog()
        result = handle_computer_command("/computer approve", cfg, lease, audit)
        assert "Usage" in result

    def test_unknown_subcommand(self):
        cfg = ComputerControlConfig()
        lease = ComputerUseLeaseManager()
        audit = AuditLog()
        result = handle_computer_command("/computer foobar", cfg, lease, audit)
        assert "Unknown" in result

    def test_status_shows_audit(self):
        cfg = ComputerControlConfig(enabled=True)
        lease = ComputerUseLeaseManager()
        audit = AuditLog()
        audit.add(AuditEntry(action="open", target="https://x.com", result="success"))
        result = handle_computer_command("/computer status", cfg, lease, audit)
        assert "open" in result
        assert "x.com" in result


# =========================================================================
# UIObservation / ObservedElement models
# =========================================================================


class TestObservationModels:
    def test_observed_element_defaults(self):
        el = ObservedElement(label="Submit", role="button")
        assert el.confidence == 1.0
        assert el.bounds is None
        assert el.selector is None

    def test_ui_observation_browser(self):
        obs = UIObservation(
            surface_type="browser",
            page_url="https://example.com",
            elements=[
                ObservedElement(label="Login", role="button", selector="#login"),
            ],
        )
        assert obs.surface_type == "browser"
        assert len(obs.elements) == 1

    def test_ui_observation_desktop(self):
        obs = UIObservation(
            surface_type="desktop",
            screenshot_path="/tmp/screen.png",
            ocr_text="File Edit View",
            window_title="Finder",
        )
        assert obs.surface_type == "desktop"
        assert obs.ocr_text == "File Edit View"

    def test_roundtrip(self):
        obs = UIObservation(
            surface_type="browser",
            page_url="https://test.com",
            elements=[ObservedElement(label="btn", role="button", bounds=(0, 0, 100, 30))],
        )
        data = obs.model_dump()
        restored = UIObservation.model_validate(data)
        assert restored.elements[0].bounds == (0, 0, 100, 30)


# =========================================================================
# ComputerUseController (integration-style with mocks)
# =========================================================================


class TestComputerUseController:
    @pytest.mark.asyncio
    async def test_disabled_config_blocks_all(self):
        from dan.tools.browser_control import MockBrowserController

        cfg = ComputerControlConfig(enabled=False)
        ctrl = ComputerUseController(
            config=cfg,
            lease=ComputerUseLeaseManager(),
            audit=AuditLog(),
            browser=MockBrowserController(),
        )
        result = await ctrl.act("open", "https://example.com")
        assert result["status"] == "error"
        assert "disabled" in result["message"]

    @pytest.mark.asyncio
    async def test_read_only_no_lease_needed(self):
        from dan.tools.browser_control import MockBrowserController

        cfg = ComputerControlConfig(enabled=True)
        lease = ComputerUseLeaseManager()
        ctrl = ComputerUseController(
            config=cfg,
            lease=lease,
            audit=AuditLog(),
            browser=MockBrowserController(),
        )
        result = await ctrl.act("screenshot")
        assert result["status"] == "ok"
        assert not lease.is_active()

    @pytest.mark.asyncio
    async def test_act_acquires_lease(self):
        from dan.tools.browser_control import MockBrowserController

        cfg = ComputerControlConfig(enabled=True)
        lease = ComputerUseLeaseManager()
        audit = AuditLog()
        ctrl = ComputerUseController(
            config=cfg,
            lease=lease,
            audit=audit,
            browser=MockBrowserController(),
        )
        result = await ctrl.act("open", "https://example.com", task_id="t1")
        assert result["status"] == "ok"
        assert lease.active_task_id == "t1"
        assert len(audit) == 1

    @pytest.mark.asyncio
    async def test_destructive_denied_without_override(self):
        from dan.tools.browser_control import MockBrowserController

        cfg = ComputerControlConfig(enabled=True)
        audit = AuditLog()
        ctrl = ComputerUseController(
            config=cfg,
            lease=ComputerUseLeaseManager(),
            audit=audit,
            browser=MockBrowserController(),
        )
        result = await ctrl.act("download")
        assert result["status"] == "denied"
        assert audit.recent(1)[0].result == "denied"

    @pytest.mark.asyncio
    async def test_observe_browser(self):
        from dan.tools.browser_control import MockBrowserController

        browser = MockBrowserController(responses={
            "extract_text": "Hello World",
            "list_tabs": [{"url": "https://test.com", "title": "Test", "index": 0}],
        })
        cfg = ComputerControlConfig(enabled=True)
        ctrl = ComputerUseController(
            config=cfg,
            lease=ComputerUseLeaseManager(),
            audit=AuditLog(),
            browser=browser,
        )
        obs = await ctrl.observe("browser")
        assert obs.surface_type == "browser"

    @pytest.mark.asyncio
    async def test_observe_desktop_fallback(self):
        from dan.tools.desktop_control import MockDesktopController

        desktop = MockDesktopController()
        cfg = ComputerControlConfig(enabled=True)
        ctrl = ComputerUseController(
            config=cfg,
            lease=ComputerUseLeaseManager(),
            audit=AuditLog(),
            desktop=desktop,
        )
        obs = await ctrl.observe("desktop")
        assert obs.surface_type == "desktop"

    @pytest.mark.asyncio
    async def test_lease_conflict_blocks_action(self):
        from dan.tools.browser_control import MockBrowserController

        cfg = ComputerControlConfig(enabled=True)
        lease = ComputerUseLeaseManager()
        await lease.acquire("other-task")
        ctrl = ComputerUseController(
            config=cfg,
            lease=lease,
            audit=AuditLog(),
            browser=MockBrowserController(),
        )
        result = await ctrl.act("click", "#btn", task_id="my-task")
        assert result["status"] == "error"
        assert "Lease" in result["message"]


# =========================================================================
# VisionExportPolicy (Task 4-5)
# =========================================================================


class TestVisionExportPolicy:
    def test_defaults(self):
        policy = VisionExportPolicy()
        assert policy.enabled is False
        assert policy.require_pii_protection is True
        assert policy.require_redaction is True

    def test_config_includes_vision_export(self):
        cfg = ComputerControlConfig()
        assert cfg.vision_export.enabled is False

    def test_check_disabled_by_default(self):
        cfg = ComputerControlConfig()
        assert check_vision_export(cfg) is False

    def test_check_enabled_but_pii_not_set(self, monkeypatch):
        monkeypatch.delenv("DAN_PII_PROTECTION", raising=False)
        cfg = ComputerControlConfig(
            vision_export=VisionExportPolicy(enabled=True)
        )
        assert check_vision_export(cfg) is False

    def test_check_enabled_pii_set_but_redaction_required(self, monkeypatch):
        monkeypatch.setenv("DAN_PII_PROTECTION", "1")
        cfg = ComputerControlConfig(
            vision_export=VisionExportPolicy(enabled=True, require_redaction=True)
        )
        assert check_vision_export(cfg) is False

    def test_check_enabled_pii_set_no_redaction(self, monkeypatch):
        monkeypatch.setenv("DAN_PII_PROTECTION", "1")
        cfg = ComputerControlConfig(
            vision_export=VisionExportPolicy(
                enabled=True,
                require_redaction=False,
            )
        )
        assert check_vision_export(cfg) is True

    def test_check_enabled_no_pii_required_no_redaction(self, monkeypatch):
        monkeypatch.delenv("DAN_PII_PROTECTION", raising=False)
        cfg = ComputerControlConfig(
            vision_export=VisionExportPolicy(
                enabled=True,
                require_pii_protection=False,
                require_redaction=False,
            )
        )
        assert check_vision_export(cfg) is True


# =========================================================================
# FileSafetyPolicy (Task 6-6)
# =========================================================================


class TestFileSafetyPolicy:
    def test_defaults(self):
        policy = FileSafetyPolicy()
        assert "~/Downloads" in policy.allowed_download_dirs
        assert "~/.dan/downloads" in policy.allowed_download_dirs
        assert policy.require_overwrite_confirmation is True
        assert policy.auto_open_downloads is False
        assert policy.screenshot_ttl_hours == 24.0
        assert policy.temp_crop_ttl_hours == 1.0
        assert policy.download_ttl_hours == 0.0

    def test_config_includes_file_safety(self):
        cfg = ComputerControlConfig()
        assert isinstance(cfg.file_safety, FileSafetyPolicy)

    def test_roundtrip(self):
        policy = FileSafetyPolicy(
            allowed_download_dirs=["~/custom"],
            allowed_upload_roots=["~/proj"],
            screenshot_ttl_hours=48.0,
        )
        data = policy.model_dump()
        restored = FileSafetyPolicy.model_validate(data)
        assert restored.allowed_download_dirs == ["~/custom"]
        assert restored.screenshot_ttl_hours == 48.0

    def test_config_load_with_file_safety(self, tmp_path, monkeypatch):
        config_dir = tmp_path / ".dan"
        config_dir.mkdir()
        (config_dir / "computer_control.json").write_text(json.dumps({
            "enabled": True,
            "file_safety": {
                "allowed_download_dirs": ["~/safe"],
                "require_overwrite_confirmation": False,
                "screenshot_ttl_hours": 12.0,
            },
        }))
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("DAN_COMPUTER_CONTROL", raising=False)
        cfg = ComputerControlConfig.load()
        assert cfg.file_safety.allowed_download_dirs == ["~/safe"]
        assert cfg.file_safety.require_overwrite_confirmation is False
        assert cfg.file_safety.screenshot_ttl_hours == 12.0


# =========================================================================
# Perception method resolution (Task 4-4)
# =========================================================================


class TestPerceptionMethodResolution:
    def test_browser_with_browser(self):
        methods = ComputerUseController._resolve_perception_method(
            "browser", has_browser=True
        )
        assert methods[0] == "dom_selectors"
        assert "local_ocr" in methods
        assert "vision_model" not in methods

    def test_desktop_with_desktop(self):
        methods = ComputerUseController._resolve_perception_method(
            "desktop", has_desktop=True
        )
        assert methods[0] == "accessibility_tree"
        assert "local_ocr" in methods

    def test_vision_model_included_when_enabled(self):
        methods = ComputerUseController._resolve_perception_method(
            "browser", has_browser=True, vision_export_enabled=True
        )
        assert "vision_model" in methods
        assert methods[-1] == "vision_model"

    def test_fallback_only_ocr(self):
        methods = ComputerUseController._resolve_perception_method(
            "desktop", has_desktop=False
        )
        assert methods == ["local_ocr"]


# =========================================================================
# Progress integration (Task 5-5)
# =========================================================================


class TestProgressIntegration:
    @pytest.mark.asyncio
    async def test_progress_emissions_on_act(self):
        from dan.tools.browser_control import MockBrowserController

        progress = ProgressSession()
        progress.start_phase("test-phase", "test")

        cfg = ComputerControlConfig(enabled=True)
        ctrl = ComputerUseController(
            config=cfg,
            lease=ComputerUseLeaseManager(),
            audit=AuditLog(),
            browser=MockBrowserController(),
            progress=progress,
        )
        await ctrl.act("open", "https://example.com", task_id="t1")
        phase = progress._phases.get("test-phase")
        assert phase is not None
        assert any("opening browser" in s for s in phase.sub_steps)

    @pytest.mark.asyncio
    async def test_no_progress_when_none(self):
        from dan.tools.browser_control import MockBrowserController

        cfg = ComputerControlConfig(enabled=True)
        ctrl = ComputerUseController(
            config=cfg,
            lease=ComputerUseLeaseManager(),
            audit=AuditLog(),
            browser=MockBrowserController(),
        )
        result = await ctrl.act("open", "https://example.com", task_id="t1")
        assert result["status"] == "ok"


# =========================================================================
# Approval integration (Task 5-6)
# =========================================================================


class TestApprovalIntegration:
    def test_create_approval_request(self):
        cfg = ComputerControlConfig(enabled=True)
        ctrl = ComputerUseController(
            config=cfg,
            lease=ComputerUseLeaseManager(),
            audit=AuditLog(),
        )
        req_id, interaction = ctrl.create_approval_request(
            "download", "https://example.com/file.zip", "destructive"
        )
        assert req_id.startswith("cu-approval-")
        assert interaction.kind == "required_clarification"
        assert len(interaction.checkpoint.options) == 2
        assert any(o.value == "approve" for o in interaction.checkpoint.options)
        assert any(o.value == "deny" for o in interaction.checkpoint.options)

    def test_resolve_approval(self):
        cfg = ComputerControlConfig(enabled=True)
        ctrl = ComputerUseController(
            config=cfg,
            lease=ComputerUseLeaseManager(),
            audit=AuditLog(),
        )
        req_id, _ = ctrl.create_approval_request("download", None, "destructive")
        assert ctrl.resolve_approval(req_id) is True
        assert ctrl.resolve_approval(req_id) is False

    @pytest.mark.asyncio
    async def test_denied_action_returns_approval_id(self):
        from dan.tools.browser_control import MockBrowserController

        cfg = ComputerControlConfig(enabled=True)
        ctrl = ComputerUseController(
            config=cfg,
            lease=ComputerUseLeaseManager(),
            audit=AuditLog(),
            browser=MockBrowserController(),
        )
        result = await ctrl.act("download")
        assert result["status"] == "denied"
        assert "approval_request_id" in result
        assert result["approval_request_id"].startswith("cu-approval-")

    def test_handle_approve_with_controller(self):
        cfg = ComputerControlConfig(enabled=True)
        lease = ComputerUseLeaseManager()
        audit = AuditLog()
        ctrl = ComputerUseController(
            config=cfg, lease=lease, audit=audit,
        )
        req_id, _ = ctrl.create_approval_request("download", None, "destructive")
        result = handle_computer_command(
            f"/computer approve {req_id}", cfg, lease, audit, controller=ctrl,
        )
        assert "granted" in result.lower()

    def test_handle_approve_unknown_id(self):
        cfg = ComputerControlConfig(enabled=True)
        lease = ComputerUseLeaseManager()
        audit = AuditLog()
        ctrl = ComputerUseController(
            config=cfg, lease=lease, audit=audit,
        )
        result = handle_computer_command(
            "/computer approve unknown-id", cfg, lease, audit, controller=ctrl,
        )
        assert "no matching" in result.lower() or "expired" in result.lower()


# =========================================================================
# /computer status shows vision export status
# =========================================================================


class TestComputerCommandVisionStatus:
    def test_status_shows_vision_disabled(self):
        cfg = ComputerControlConfig(enabled=True)
        result = handle_computer_command(
            "/computer status", cfg, ComputerUseLeaseManager(), AuditLog(),
        )
        assert "Vision export: disabled" in result

    def test_status_shows_vision_enabled(self):
        cfg = ComputerControlConfig(
            enabled=True,
            vision_export=VisionExportPolicy(enabled=True),
        )
        result = handle_computer_command(
            "/computer status", cfg, ComputerUseLeaseManager(), AuditLog(),
        )
        assert "Vision export: enabled" in result
