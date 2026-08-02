"""Tests for browser_control.py — MockBrowserController, domain checking, Playwright availability."""

from __future__ import annotations

import asyncio
import os

import pytest

from dan.tools.browser_control import (
    BrowserController,
    BrowserSessionContext,
    MockBrowserController,
    PlaywrightBrowserController,
    _browser_launch_args,
    is_playwright_available,
    _cleanup_old_screenshots,
    _ensure_screenshot_dir,
    SCREENSHOT_DIR,
)
from dan.server.concierge.computer_policy import BrowserDomainRule, is_domain_allowed


# =========================================================================
# MockBrowserController
# =========================================================================


class TestMockBrowserController:
    @pytest.mark.asyncio
    async def test_open_records_action(self):
        mock = MockBrowserController()
        result = await mock.open("https://example.com")
        assert result["status"] == "ok"
        assert len(mock.actions) == 1
        assert mock.actions[0]["action"] == "open"
        assert mock.actions[0]["url"] == "https://example.com"

    @pytest.mark.asyncio
    async def test_click_records_action(self):
        mock = MockBrowserController()
        await mock.click("#btn")
        assert mock.actions[-1]["action"] == "click"
        assert mock.actions[-1]["selector"] == "#btn"

    @pytest.mark.asyncio
    async def test_type_text_records(self):
        mock = MockBrowserController()
        await mock.type_text("#input", "hello")
        assert mock.actions[-1]["action"] == "type_text"
        assert mock.actions[-1]["text"] == "hello"

    @pytest.mark.asyncio
    async def test_fill_records(self):
        mock = MockBrowserController()
        await mock.fill("#field", "value")
        assert mock.actions[-1]["action"] == "fill"

    @pytest.mark.asyncio
    async def test_select_records(self):
        mock = MockBrowserController()
        await mock.select("#dropdown", "opt1")
        assert mock.actions[-1]["action"] == "select"
        assert mock.actions[-1]["value"] == "opt1"

    @pytest.mark.asyncio
    async def test_wait_for_records(self):
        mock = MockBrowserController()
        await mock.wait_for("#loaded", timeout=5.0)
        assert mock.actions[-1]["action"] == "wait_for"

    @pytest.mark.asyncio
    async def test_extract_text_default(self):
        mock = MockBrowserController()
        text = await mock.extract_text()
        assert text == "mock text content"

    @pytest.mark.asyncio
    async def test_extract_text_custom(self):
        mock = MockBrowserController(responses={"extract_text": "custom"})
        text = await mock.extract_text("#el")
        assert text == "custom"

    @pytest.mark.asyncio
    async def test_screenshot_returns_path(self):
        mock = MockBrowserController()
        path = await mock.screenshot()
        assert path.endswith(".png")

    @pytest.mark.asyncio
    async def test_download_returns_path(self):
        mock = MockBrowserController()
        path = await mock.download()
        assert path is not None

    @pytest.mark.asyncio
    async def test_list_tabs(self):
        mock = MockBrowserController()
        tabs = await mock.list_tabs()
        assert isinstance(tabs, list)
        assert len(tabs) >= 1

    @pytest.mark.asyncio
    async def test_switch_tab(self):
        mock = MockBrowserController()
        result = await mock.switch_tab(1)
        assert result["status"] == "ok"
        assert mock.actions[-1]["index"] == 1

    @pytest.mark.asyncio
    async def test_close(self):
        mock = MockBrowserController()
        await mock.close()
        assert mock._closed is True
        assert mock.actions[-1]["action"] == "close"

    @pytest.mark.asyncio
    async def test_custom_responses(self):
        responses = {
            "open": {"status": "ok", "title": "Custom Title"},
            "screenshot": "/custom/path.png",
        }
        mock = MockBrowserController(responses=responses)
        result = await mock.open("https://test.com")
        assert result["title"] == "Custom Title"
        path = await mock.screenshot()
        assert path == "/custom/path.png"

    @pytest.mark.asyncio
    async def test_action_sequence(self):
        mock = MockBrowserController()
        await mock.open("https://example.com")
        await mock.click("#login")
        await mock.fill("#user", "admin")
        await mock.fill("#pass", "secret")
        await mock.click("#submit")
        assert len(mock.actions) == 5
        assert [a["action"] for a in mock.actions] == [
            "open", "click", "fill", "fill", "click",
        ]

    @pytest.mark.asyncio
    async def test_browser_tabs_tool_uses_shared_controller(self, monkeypatch):
        from dan.tools import _browser_session
        from dan.tools.browser_tabs import browser_tabs

        mock = MockBrowserController()

        async def fake_controller():
            return mock

        monkeypatch.setattr(_browser_session, "get_controller", fake_controller)

        result = await browser_tabs()

        assert result["count"] == 1
        assert result["tabs"][0]["url"] == "about:blank"
        assert mock.actions[-1]["action"] == "list_tabs"

    @pytest.mark.asyncio
    async def test_browser_inspect_tool_returns_element_metadata(self, monkeypatch):
        from dan.tools import _browser_session
        from dan.tools.browser_inspect import browser_inspect

        mock = MockBrowserController()

        async def fake_controller():
            return mock

        monkeypatch.setattr(_browser_session, "get_controller", fake_controller)

        result = await browser_inspect(include_html=True, element_limit=10)

        assert result["url"] == "about:blank"
        assert result["html"]
        assert result["elements"][0]["selector"] == "#ok"
        assert result["elements"][0]["text"] == "OK"
        assert mock.actions[-1]["action"] == "inspect_dom"

    @pytest.mark.asyncio
    async def test_browser_select_tool_uses_shared_controller(self, monkeypatch):
        from dan.tools import _browser_session
        from dan.tools.browser_select import browser_select

        mock = MockBrowserController()

        async def fake_controller():
            return mock

        monkeypatch.setattr(_browser_session, "get_controller", fake_controller)

        result = await browser_select(selector="select#country", value="US")

        assert result["status"] == "ok"
        assert result["selector"] == "select#country"
        assert result["value"] == "US"
        assert mock.actions[-1]["action"] == "select"


# =========================================================================
# Protocol conformance
# =========================================================================


class TestProtocolConformance:
    def test_mock_is_browser_controller(self):
        mock = MockBrowserController()
        assert isinstance(mock, BrowserController)


# =========================================================================
# BrowserSessionContext
# =========================================================================


class TestBrowserSessionContext:
    def test_defaults(self):
        ctx = BrowserSessionContext(task_id="t1")
        assert ctx.task_id == "t1"
        assert ctx.current_url is None
        assert ctx.cookies == []
        assert ctx.open_tabs == []

    def test_update_url(self):
        ctx = BrowserSessionContext(task_id="t1")
        ctx.current_url = "https://example.com"
        assert ctx.current_url == "https://example.com"


# =========================================================================
# Domain allowlist checking (via computer_policy, tested here for browser context)
# =========================================================================


class TestBrowserDomainChecking:
    def test_subdomain_chain(self):
        rules = [BrowserDomainRule(pattern="github.com")]
        assert is_domain_allowed("https://api.github.com/repos", rules)
        assert is_domain_allowed("https://raw.githubusercontent.com", rules) is False

    def test_multiple_rules(self):
        rules = [
            BrowserDomainRule(pattern="google.com"),
            BrowserDomainRule(pattern="googleapis.com"),
        ]
        assert is_domain_allowed("https://www.google.com", rules)
        assert is_domain_allowed("https://fonts.googleapis.com", rules)

    def test_path_irrelevant(self):
        rules = [BrowserDomainRule(pattern="example.com")]
        assert is_domain_allowed("https://example.com/long/path?q=1#frag", rules)


# =========================================================================
# Playwright availability
# =========================================================================


class TestPlaywrightAvailability:
    def test_availability_check(self):
        result = is_playwright_available()
        assert isinstance(result, bool)

    def test_playwright_controller_import_error(self):
        if is_playwright_available():
            pytest.skip("Playwright is installed — skip ImportError test")
        with pytest.raises(ImportError):
            PlaywrightBrowserController()

    @pytest.mark.asyncio
    async def test_download_runs_trigger_inside_expect_download(self, monkeypatch):
        import dan.tools.browser_control as bc

        class FakeDownload:
            suggested_filename = "report.csv"

            def __init__(self) -> None:
                self.saved_path: str | None = None

            async def save_as(self, path: str) -> None:
                self.saved_path = path

        class FakeDownloadWaiter:
            def __init__(self, download: FakeDownload) -> None:
                self.download = download
                self.entered = False

            async def __aenter__(self) -> "FakeDownloadWaiter":
                self.entered = True
                return self

            async def __aexit__(self, exc_type, exc, tb) -> None:
                return None

            @property
            def value(self):
                async def _value() -> FakeDownload:
                    return self.download

                return _value()

        class FakePage:
            def __init__(self, download: FakeDownload) -> None:
                self.download = download
                self.waiter: FakeDownloadWaiter | None = None

            def expect_download(self, timeout: int) -> FakeDownloadWaiter:
                self.waiter = FakeDownloadWaiter(self.download)
                return self.waiter

        fake_download = FakeDownload()
        fake_page = FakePage(fake_download)
        controller = object.__new__(PlaywrightBrowserController)

        async def fake_ensure_browser():
            return fake_page

        controller._ensure_browser = fake_ensure_browser  # type: ignore[attr-defined]
        monkeypatch.setattr(bc, "_ensure_screenshot_dir", lambda: "/tmp")
        monkeypatch.setattr(bc, "_ensure_download_dir", lambda: "/tmp")

        triggered = {"value": False}

        async def trigger() -> None:
            assert fake_page.waiter is not None
            assert fake_page.waiter.entered is True
            triggered["value"] = True

        result = await PlaywrightBrowserController.download(controller, trigger=trigger)

        assert triggered["value"] is True
        assert result == "/tmp/report.csv"
        assert fake_download.saved_path == "/tmp/report.csv"

    def test_launch_args_disable_macos_keychain_prompt_by_default(self, monkeypatch):
        monkeypatch.delenv("DAN_BROWSER_DISABLE_KEYCHAIN", raising=False)
        monkeypatch.delenv("DAN_BROWSER_ARGS", raising=False)

        args = _browser_launch_args()

        assert "--use-mock-keychain" in args
        assert "--password-store=basic" in args

    def test_launch_args_can_be_extended_or_keychain_flags_disabled(self, monkeypatch):
        monkeypatch.setenv("DAN_BROWSER_DISABLE_KEYCHAIN", "0")
        monkeypatch.setenv("DAN_BROWSER_ARGS", "--disable-gpu --lang=en-US")

        args = _browser_launch_args()

        assert "--use-mock-keychain" not in args
        assert "--password-store=basic" not in args
        assert "--disable-gpu" in args
        assert "--lang=en-US" in args


# =========================================================================
# Screenshot cleanup
# =========================================================================


class TestScreenshotCleanup:
    def test_ensure_dir_creates(self, tmp_path, monkeypatch):
        import dan.tools.browser_control as bc
        custom_dir = str(tmp_path / "screenshots")
        monkeypatch.setattr(bc, "SCREENSHOT_DIR", custom_dir)
        result = _ensure_screenshot_dir()
        assert os.path.isdir(custom_dir)

    def test_cleanup_removes_excess(self, tmp_path, monkeypatch):
        import dan.tools.browser_control as bc
        custom_dir = str(tmp_path / "screenshots")
        os.makedirs(custom_dir)
        monkeypatch.setattr(bc, "SCREENSHOT_DIR", custom_dir)
        monkeypatch.setattr(bc, "MAX_SCREENSHOTS", 3)
        for i in range(5):
            (tmp_path / "screenshots" / f"test_{i}.png").write_text("img")
        _cleanup_old_screenshots()
        remaining = [f for f in os.listdir(custom_dir) if f.endswith(".png")]
        assert len(remaining) <= 3
