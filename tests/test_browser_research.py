"""Browser wiring, native RPC, isolation, and download boundary regressions."""
import asyncio
import json
import shlex
import sys

import pytest

from diane.native_workers import browser_bridge
from diane.tools import _browser_session as sessions


class Browser:
    instances = []

    def __init__(self, **kwargs):
        self.options = kwargs
        self.url = ""
        self.closed = False
        self.instances.append(self)

    async def open(self, url):
        self.url = url
        return {"url": url, "session": self.session_info()}

    async def extract_text(self, selector=None):
        return "@article{sample, title={Verified citation}}"

    def session_info(self):
        return {"headless": self.options["headless"]}

    async def close(self):
        self.closed = True


@pytest.fixture
def browsers(monkeypatch):
    Browser.instances = []
    monkeypatch.setattr("diane.tools.browser_control.PlaywrightBrowserController", Browser)
    return Browser.instances


@pytest.mark.parametrize("platform,display,override,expected", [
    ("darwin", "", "auto", False), ("win32", "", "auto", False),
    ("linux", "", "auto", True), ("linux", ":1", "auto", False),
    ("darwin", "", "1", True), ("linux", "", "0", False),
])
def test_visibility(monkeypatch, platform, display, override, expected):
    monkeypatch.setattr(sessions.sys, "platform", platform)
    monkeypatch.setenv("DAN_BROWSER_HEADLESS", override)
    monkeypatch.setenv("DISPLAY", display)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert sessions.browser_headless() is expected


@pytest.mark.asyncio
async def test_scopes_are_lazy_isolated_and_close_on_cancel(tmp_path, browsers):
    async with sessions.browser_scope(tmp_path, "no tools"):
        assert not browsers
    ready = asyncio.Event()
    async def run():
        async with sessions.browser_scope(tmp_path, "other"):
            ctrl = await sessions.get_controller()
            await ctrl.open("https://other.example")
            ready.set()
            await asyncio.Future()
    task = asyncio.create_task(run())
    await ready.wait()
    async with sessions.browser_scope(tmp_path, "main"):
        ctrl = await sessions.get_controller()
        assert ctrl is await sessions.get_controller()
        await ctrl.open("https://main.example")
        assert browsers[0].url == "https://other.example"
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert browsers[0].closed and not ctrl.closed
    assert all(browser.closed for browser in browsers)


@pytest.mark.asyncio
async def test_downloads_cannot_escape_or_overwrite(tmp_path):
    (tmp_path / "exists.bib").write_text("keep")
    (tmp_path / "outside").symlink_to(tmp_path.parent, target_is_directory=True)
    async with sessions.browser_scope(tmp_path, "run"):
        assert sessions.download_destination("refs.bib") == str(tmp_path / "refs.bib")
        for value in [None, "../escape.bib", "outside/escape.bib", "exists.bib"]:
            with pytest.raises(ValueError):
                sessions.download_destination(value)
    assert (tmp_path / "exists.bib").read_text() == "keep"


@pytest.mark.asyncio
async def test_bridge_client_retrieves_citation_and_plan_rejects_actions(tmp_path, browsers):
    directory = tmp_path / "rpc"
    directory.mkdir()
    server = asyncio.create_task(browser_bridge.serve(directory, str(tmp_path), "run", "plan"))
    async def call(action, args=None):
        proc = await asyncio.create_subprocess_exec(
            sys.executable, browser_bridge.__file__, "--queue", str(directory), action,
            "--args", json.dumps(args or {}), stdout=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), 10)
        return json.loads(out)
    try:
        help_result = await call("help")
        assert "open" in help_result["result"] and "click" not in help_result["result"]
        assert not browsers
        assert (await call("open", {"url": "https://scholar.google.com"}))["ok"]
        result = await call("extract")
        assert "@article" in result["result"]["text"]
        assert not (await call("click", {"selector": "button"}))["ok"]
        assert not (await call("open", {"url": "https://example.com", "unexpected": 1}))["ok"]
    finally:
        server.cancel()
        await asyncio.gather(server, return_exceptions=True)
    assert browsers[0].closed
    assert not list(directory.glob("*.browser-response"))


@pytest.mark.asyncio
async def test_native_lead_can_use_copied_browser_client(monkeypatch, tmp_path, browsers):
    from diane.native_workers.lead import NativeLeadAdapter
    from diane.server.chat_v2_backend import AgentBackendRunRequest
    monkeypatch.setenv("DAN_GRAPHS_DIR", str(tmp_path / "graphs"))
    def launch(runtime, profile, prompt, workspace, session=""):
        command = next(line.removesuffix(" help") for line in prompt.splitlines()
                       if "browser.py" in line and line.endswith(" help"))
        script = (
            "import subprocess,json; "
            f"a=subprocess.run({shlex.split(command) + ['open', '--args', json.dumps({'url':'https://example.com'})]!r},capture_output=True,text=True); "
            "assert json.loads(a.stdout)['ok'],a.stdout; "
            f"b=subprocess.run({shlex.split(command) + ['extract']!r},capture_output=True,text=True); "
            "print(json.dumps({'type':'result','session_id':'browser-demo','result':json.loads(b.stdout)['result']['text']}))"
        )
        return [sys.executable, "-c", script], {}
    monkeypatch.setattr("diane.native_workers.service.launch", launch)
    events = []
    result = await NativeLeadAdapter("claude").run(AgentBackendRunRequest(
        task_id="task", run_id="run", thread_id="thread", workspace_root=str(tmp_path),
        objective="Retrieve citation through the browser", profile_policy={},
    ), events.append)
    assert result.status == "completed" and "@article" in result.summary
    assert any(e.source_event_type == "native_lead.browser" for e in events)
    assert browsers[0].closed
    assert not list(tmp_path.glob(".dan-team-*"))


def test_gui_request_exposes_browser_without_keyword_matching(tmp_path):
    from diane.server.chat_v2_backend import AgentBackendRunRequest, _build_super_dan_args, _load_super_dan_cli
    cli = _load_super_dan_cli()
    req = AgentBackendRunRequest(task_id="task", run_id="run", workspace_root=str(tmp_path), objective="帮我查论文并导出引用")
    args = _build_super_dan_args(cli, req, workspace_root=tmp_path)
    report = cli.run_super_organism_demo(req.objective)
    choice = cli._super_live_choice(report, args)
    assert {"browser_open", "browser_click", "browser_extract", "browser_download"} <= set(choice.tool_policy["allowed_tool_ids"])
    req.metadata["surface_policy"] = {"capability_packs": [], "permission_scope": "read_only"}
    args = _build_super_dan_args(cli, req, workspace_root=tmp_path)
    choice = cli._super_live_choice(report, args)
    assert "browser_open" not in choice.tool_policy["allowed_tool_ids"]


@pytest.mark.asyncio
async def test_browser_tab_switch_is_exposed(monkeypatch):
    from diane.tools.browser_tabs import browser_tabs
    class Tabs:
        index = 0
        async def switch_tab(self, index):
            self.index = index
            return {"status": "ok"}
        async def list_tabs(self):
            return [{"index": self.index}]
    tabs = Tabs()
    async def controller():
        return tabs
    monkeypatch.setattr(sessions, "get_controller", controller)
    assert (await browser_tabs(index=1))["tabs"] == [{"index": 1}]


@pytest.mark.asyncio
async def test_closed_window_requires_navigation_before_restarting(monkeypatch):
    from types import SimpleNamespace
    from diane.tools.browser_control import PlaywrightBrowserController
    monkeypatch.setattr("diane.tools.browser_control.is_playwright_available", lambda: True)
    ctrl = PlaywrightBrowserController()
    ctrl._page = SimpleNamespace(is_closed=lambda: True)
    closed = []
    async def close():
        closed.append(True)
    ctrl._context = SimpleNamespace(pages=[], close=close)
    relaunched = object()
    async def launch():
        assert ctrl._page is None and ctrl._context is None
        return relaunched
    monkeypatch.setattr(ctrl, "_launch_browser", launch)
    with pytest.raises(RuntimeError, match="browser_open"):
        await ctrl._ensure_browser()
    assert not closed
    assert await ctrl._ensure_browser(reopen=True) is relaunched
    assert closed == [True]


@pytest.mark.asyncio
async def test_closed_tab_uses_remaining_tab(monkeypatch):
    from types import SimpleNamespace
    from diane.tools.browser_control import PlaywrightBrowserController
    monkeypatch.setattr("diane.tools.browser_control.is_playwright_available", lambda: True)
    ctrl = PlaywrightBrowserController()
    remaining = SimpleNamespace(is_closed=lambda: False)
    ctrl._page = SimpleNamespace(is_closed=lambda: True)
    ctrl._context = SimpleNamespace(pages=[remaining])
    assert await ctrl._ensure_browser() is remaining
