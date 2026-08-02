"""Tests for desktop_control.py — MockDesktopController, permission detection, cross-platform stubs."""

from __future__ import annotations

import platform

import pytest

from dan.tools.desktop_control import (
    DesktopController,
    LinuxDesktopController,
    MacOSDesktopController,
    MockDesktopController,
    PermissionStatus,
    WindowsDesktopController,
    check_macos_permissions,
    detect_platform,
)


# =========================================================================
# MockDesktopController — action recording
# =========================================================================


class TestMockDesktopController:
    @pytest.mark.asyncio
    async def test_screenshot_records(self):
        mock = MockDesktopController()
        path = await mock.screenshot()
        assert path.endswith(".png")
        assert mock.actions[-1]["action"] == "screenshot"

    @pytest.mark.asyncio
    async def test_screenshot_with_region(self):
        mock = MockDesktopController()
        await mock.screenshot(region=(0, 0, 800, 600))
        assert mock.actions[-1]["region"] == (0, 0, 800, 600)

    @pytest.mark.asyncio
    async def test_ocr_records(self):
        mock = MockDesktopController()
        text = await mock.ocr()
        assert text == "mock ocr text"
        assert mock.actions[-1]["action"] == "ocr"

    @pytest.mark.asyncio
    async def test_list_windows(self):
        mock = MockDesktopController()
        windows = await mock.list_windows()
        assert isinstance(windows, list)
        assert windows[0]["app"] == "Finder"
        assert mock.actions[-1]["action"] == "list_windows"

    @pytest.mark.asyncio
    async def test_focus_window(self):
        mock = MockDesktopController()
        result = await mock.focus_window(app="Safari")
        assert result["status"] == "ok"
        assert mock.actions[-1]["app"] == "Safari"

    @pytest.mark.asyncio
    async def test_click(self):
        mock = MockDesktopController()
        result = await mock.click(100, 200, button="right")
        assert result["status"] == "ok"
        assert mock.actions[-1]["x"] == 100
        assert mock.actions[-1]["y"] == 200
        assert mock.actions[-1]["button"] == "right"

    @pytest.mark.asyncio
    async def test_type_text(self):
        mock = MockDesktopController()
        result = await mock.type_text("hello world")
        assert result["status"] == "ok"
        assert mock.actions[-1]["text"] == "hello world"

    @pytest.mark.asyncio
    async def test_hotkey(self):
        mock = MockDesktopController()
        result = await mock.hotkey(["cmd", "c"])
        assert result["status"] == "ok"
        assert mock.actions[-1]["keys"] == ["cmd", "c"]

    @pytest.mark.asyncio
    async def test_clipboard_read(self):
        mock = MockDesktopController()
        text = await mock.clipboard_read()
        assert text == "mock clipboard content"

    @pytest.mark.asyncio
    async def test_clipboard_write(self):
        mock = MockDesktopController()
        result = await mock.clipboard_write("copied text")
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_custom_responses(self):
        mock = MockDesktopController(responses={
            "ocr": "custom ocr result",
            "clipboard_read": "custom clipboard",
        })
        assert await mock.ocr() == "custom ocr result"
        assert await mock.clipboard_read() == "custom clipboard"

    @pytest.mark.asyncio
    async def test_action_sequence(self):
        mock = MockDesktopController()
        await mock.focus_window(app="Safari")
        await mock.click(100, 200)
        await mock.type_text("search query")
        await mock.hotkey(["cmd", "enter"])
        assert len(mock.actions) == 4
        assert [a["action"] for a in mock.actions] == [
            "focus_window", "click", "type_text", "hotkey",
        ]

    @pytest.mark.asyncio
    async def test_desktop_tool_wrappers_use_shared_controller(self, monkeypatch):
        from dan.tools import _desktop_session
        from dan.tools.desktop_click import desktop_click
        from dan.tools.desktop_focus import desktop_focus
        from dan.tools.desktop_hotkey import desktop_hotkey
        from dan.tools.desktop_observe import desktop_observe
        from dan.tools.desktop_type import desktop_type

        monkeypatch.setenv("DAN_COMPUTER_CONTROL", "1")
        mock = MockDesktopController()

        async def fake_controller():
            return mock

        monkeypatch.setattr(_desktop_session, "get_controller", fake_controller)

        observation = await desktop_observe()
        focus = await desktop_focus(app="Safari")
        click = await desktop_click(x=10, y=20)
        typed = await desktop_type(text="hello")
        hotkey = await desktop_hotkey(keys=["command", "l"])

        assert observation["surface_type"] == "desktop"
        assert observation["window_title"] == "Finder"
        assert focus["app"] == "Safari"
        assert click["x"] == 10
        assert typed["text"] == "hello"
        assert hotkey["keys"] == ["command", "l"]
        assert [entry["action"] for entry in mock.actions] == [
            "screenshot",
            "ocr",
            "list_windows",
            "focus_window",
            "click",
            "type_text",
            "hotkey",
        ]

    @pytest.mark.asyncio
    async def test_desktop_tool_wrappers_require_computer_control_env(self, monkeypatch):
        from dan.tools.desktop_click import desktop_click

        monkeypatch.delenv("DAN_COMPUTER_CONTROL", raising=False)

        with pytest.raises(PermissionError, match="DAN_COMPUTER_CONTROL=1"):
            await desktop_click(x=10, y=20)


# =========================================================================
# Protocol conformance
# =========================================================================


class TestProtocolConformance:
    def test_mock_is_desktop_controller(self):
        mock = MockDesktopController()
        assert isinstance(mock, DesktopController)


# =========================================================================
# Permission detection (mocked subprocess)
# =========================================================================


class TestPermissionDetection:
    def test_permission_status_defaults(self):
        status = PermissionStatus()
        assert status.screen_recording is False
        assert status.accessibility is False
        assert status.apple_events is True
        assert status.platform_supported is False

    def test_check_non_macos(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        status = check_macos_permissions()
        assert status.platform_supported is False

    def test_check_macos_success(self, monkeypatch):
        import subprocess as sp

        class FakeResult:
            returncode = 0

        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        monkeypatch.setattr(sp, "run", lambda *a, **kw: FakeResult())
        status = check_macos_permissions()
        assert status.platform_supported is True
        assert status.screen_recording is True
        assert status.accessibility is True

    def test_check_macos_screen_recording_denied(self, monkeypatch):
        import subprocess as sp

        call_count = 0

        def fake_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1

            class R:
                returncode = 1 if call_count == 1 else 0
            return R()

        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        monkeypatch.setattr(sp, "run", fake_run)
        status = check_macos_permissions()
        assert status.screen_recording is False
        assert status.accessibility is True

    def test_check_macos_subprocess_error(self, monkeypatch):
        import subprocess as sp

        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        monkeypatch.setattr(sp, "run", lambda *a, **kw: (_ for _ in ()).throw(OSError("fail")))
        status = check_macos_permissions()
        assert status.platform_supported is True
        assert status.screen_recording is False


# =========================================================================
# Cross-platform stubs (Task 3-3)
# =========================================================================


class TestWindowsDesktopController:
    @pytest.mark.asyncio
    async def test_screenshot_raises(self):
        ctrl = WindowsDesktopController()
        with pytest.raises(NotImplementedError, match="Windows"):
            await ctrl.screenshot()

    @pytest.mark.asyncio
    async def test_ocr_raises(self):
        ctrl = WindowsDesktopController()
        with pytest.raises(NotImplementedError, match="Windows"):
            await ctrl.ocr()

    @pytest.mark.asyncio
    async def test_click_raises(self):
        ctrl = WindowsDesktopController()
        with pytest.raises(NotImplementedError, match="Windows"):
            await ctrl.click(0, 0)

    @pytest.mark.asyncio
    async def test_type_text_raises(self):
        ctrl = WindowsDesktopController()
        with pytest.raises(NotImplementedError, match="Windows"):
            await ctrl.type_text("hello")

    @pytest.mark.asyncio
    async def test_list_windows_raises(self):
        ctrl = WindowsDesktopController()
        with pytest.raises(NotImplementedError, match="Windows"):
            await ctrl.list_windows()

    @pytest.mark.asyncio
    async def test_handle_file_dialog_raises(self):
        ctrl = WindowsDesktopController()
        with pytest.raises(NotImplementedError, match="Windows"):
            await ctrl.handle_file_dialog("save", "/tmp/test.txt")


class TestLinuxDesktopController:
    @pytest.mark.asyncio
    async def test_screenshot_raises(self):
        ctrl = LinuxDesktopController()
        with pytest.raises(NotImplementedError, match="Linux"):
            await ctrl.screenshot()

    @pytest.mark.asyncio
    async def test_hotkey_raises(self):
        ctrl = LinuxDesktopController()
        with pytest.raises(NotImplementedError, match="Linux"):
            await ctrl.hotkey(["ctrl", "c"])

    @pytest.mark.asyncio
    async def test_clipboard_read_raises(self):
        ctrl = LinuxDesktopController()
        with pytest.raises(NotImplementedError, match="Linux"):
            await ctrl.clipboard_read()

    @pytest.mark.asyncio
    async def test_clipboard_write_raises(self):
        ctrl = LinuxDesktopController()
        with pytest.raises(NotImplementedError, match="Linux"):
            await ctrl.clipboard_write("hello")


class TestDetectPlatform:
    def test_darwin_returns_macos(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        ctrl = detect_platform()
        assert isinstance(ctrl, MacOSDesktopController)

    def test_windows_returns_windows_stub(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        ctrl = detect_platform()
        assert isinstance(ctrl, WindowsDesktopController)

    def test_linux_returns_linux_stub(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        ctrl = detect_platform()
        assert isinstance(ctrl, LinuxDesktopController)

    def test_unknown_returns_linux_stub(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "FreeBSD")
        ctrl = detect_platform()
        assert isinstance(ctrl, LinuxDesktopController)


# =========================================================================
# MacOSDesktopController — platform guard
# =========================================================================


class TestMacOSDesktopController:
    def test_non_macos_raises(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        with pytest.raises(RuntimeError, match="macOS"):
            MacOSDesktopController()

    def test_build_ocr_script_escapes_screenshot_path(self):
        import dan.tools.desktop_control as dc

        assert hasattr(dc, "_build_ocr_script")
        script = dc._build_ocr_script('/tmp/shot" & do shell script "rm -rf /".png')

        assert 'set imgPath to POSIX file ' in script
        assert '\\"' in script

    @pytest.mark.asyncio
    async def test_focus_window_escapes_applescript_target(self, monkeypatch):
        import dan.tools.desktop_control as dc

        captured: dict[str, str] = {}

        async def fake_run(script: str) -> str:
            captured["script"] = script
            return ""

        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        monkeypatch.setattr(dc, "_run_applescript", fake_run)

        controller = MacOSDesktopController()
        malicious = 'Finder" & do shell script "rm -rf /"'
        result = await controller.focus_window(app=malicious)

        assert result["status"] == "ok"
        assert 'do shell script "rm -rf /"' not in captured["script"]
