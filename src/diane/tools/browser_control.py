"""Browser automation — Protocol, Playwright backend, and mock for testing.

Provides ``BrowserController`` protocol, ``PlaywrightBrowserController`` that
wraps Playwright (optional dependency), and ``MockBrowserController`` for tests.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import shutil
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

SCREENSHOT_DIR = os.path.expanduser("~/.dan/screenshots")
DOWNLOAD_DIR = os.path.expanduser("~/.dan/downloads")
PROFILE_DIR = os.path.expanduser("~/.dan/browser-profiles")
MAX_SCREENSHOTS = 50
_DEFAULT_NONINTERACTIVE_CHROMIUM_ARGS = (
    "--use-mock-keychain",
    "--password-store=basic",
)


# ---------------------------------------------------------------------------
# Browser session context
# ---------------------------------------------------------------------------


@dataclass
class BrowserSessionContext:
    """Tracks per-task browser session state."""

    task_id: str
    current_url: str | None = None
    cookies: list[dict[str, Any]] = field(default_factory=list)
    open_tabs: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class BrowserController(Protocol):
    """Common interface for browser automation backends."""

    async def open(self, url: str) -> dict: ...
    async def click(self, selector: str) -> dict: ...
    async def type_text(self, selector: str, text: str) -> dict: ...
    async def fill(self, selector: str, text: str) -> dict: ...
    async def select(self, selector: str, value: str) -> dict: ...
    async def inspect_dom(
        self,
        selector: str | None = None,
        *,
        include_html: bool = False,
        include_elements: bool = True,
        max_html_length: int = 50000,
        element_limit: int = 100,
    ) -> dict: ...
    async def wait_for(
        self, selector: str | None = None, timeout: float = 30.0
    ) -> dict: ...
    async def extract_text(self, selector: str | None = None) -> str: ...
    async def screenshot(self) -> str: ...
    async def download(
        self,
        selector: str | None = None,
        *,
        trigger: Callable[[], Awaitable[Any]] | None = None,
        destination_path: str | None = None,
        timeout: float = 30.0,
    ) -> str | None: ...
    async def list_tabs(self) -> list[dict]: ...
    async def switch_tab(self, index: int) -> dict: ...
    async def handle_native_dialog(self, dialog_type: str = "file_picker") -> dict: ...
    def session_info(self) -> dict[str, Any]: ...
    async def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Playwright implementation
# ---------------------------------------------------------------------------

_playwright_available: bool | None = None


def is_playwright_available() -> bool:
    """Return True if Playwright can be imported."""
    global _playwright_available
    if _playwright_available is None:
        try:
            import playwright  # noqa: F401
            _playwright_available = True
        except ImportError:
            _playwright_available = False
    return _playwright_available


def _browser_launch_args() -> list[str]:
    """Return Chromium launch args suitable for non-interactive automation."""

    disable_keychain = os.environ.get("DAN_BROWSER_DISABLE_KEYCHAIN", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    args: list[str] = []
    if disable_keychain:
        args.extend(_DEFAULT_NONINTERACTIVE_CHROMIUM_ARGS)
    extra = os.environ.get("DAN_BROWSER_ARGS", "").strip()
    if extra:
        try:
            args.extend(shlex.split(extra))
        except ValueError:
            logger.warning("Ignoring invalid DAN_BROWSER_ARGS value: %r", extra)
    deduped: list[str] = []
    seen: set[str] = set()
    for arg in args:
        if arg and arg not in seen:
            seen.add(arg)
            deduped.append(arg)
    return deduped


def _explicit_browser_executable_path() -> str | None:
    value = os.environ.get("DAN_BROWSER_EXECUTABLE", "").strip()
    if not value:
        return None
    if value.lower() in {"0", "false", "no", "off", "none"}:
        return ""
    return os.path.expanduser(value)


def _system_browser_candidates() -> list[str]:
    candidates: list[str] = []
    if sys.platform == "darwin":
        candidates.extend(
            [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            ]
        )
    elif os.name == "nt":
        for root in (
            os.environ.get("LOCALAPPDATA", ""),
            os.environ.get("PROGRAMFILES", ""),
            os.environ.get("PROGRAMFILES(X86)", ""),
        ):
            if root:
                candidates.extend(
                    [
                        os.path.join(root, "Google", "Chrome", "Application", "chrome.exe"),
                        os.path.join(root, "Microsoft", "Edge", "Application", "msedge.exe"),
                    ]
                )
    for name in (
        "chromium-browser",
        "chromium",
        "google-chrome",
        "google-chrome-stable",
        "microsoft-edge",
        "brave-browser",
    ):
        resolved = shutil.which(name)
        if resolved:
            candidates.append(resolved)
    return candidates


def _fallback_browser_executable_path() -> str | None:
    explicit = _explicit_browser_executable_path()
    if explicit is not None:
        return explicit or None
    for candidate in _system_browser_candidates():
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def _browser_launch_options(browser_type: Any, *, headless: bool) -> dict[str, Any]:
    options: dict[str, Any] = {
        "headless": headless,
        "args": _browser_launch_args(),
    }
    explicit = _explicit_browser_executable_path()
    if explicit is not None:
        if explicit:
            options["executable_path"] = explicit
        return options
    managed_path = str(getattr(browser_type, "executable_path", "") or "")
    if managed_path and os.path.exists(managed_path):
        return options
    fallback = _fallback_browser_executable_path()
    if fallback:
        options["executable_path"] = fallback
    return options


def _ensure_screenshot_dir() -> str:
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    return SCREENSHOT_DIR


def _ensure_download_dir() -> str:
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    return DOWNLOAD_DIR


def _resolve_download_path(destination_path: str | None, suggested_filename: str) -> str:
    if destination_path:
        expanded = os.path.expanduser(destination_path)
        resolved = expanded if os.path.isabs(expanded) else os.path.realpath(expanded)
        parent = os.path.dirname(resolved)
        if parent:
            os.makedirs(parent, exist_ok=True)
        return resolved
    return os.path.join(_ensure_download_dir(), suggested_filename)


def _cleanup_old_screenshots() -> None:
    """Remove oldest screenshots beyond MAX_SCREENSHOTS."""
    try:
        d = _ensure_screenshot_dir()
        files = sorted(
            (os.path.join(d, f) for f in os.listdir(d) if f.endswith(".png")),
            key=os.path.getmtime,
        )
        while len(files) > MAX_SCREENSHOTS:
            os.unlink(files.pop(0))
    except Exception:
        pass


class PlaywrightBrowserController:
    """Playwright-backed browser controller.

    Wraps ``playwright.async_api`` for async browser automation.
    Raises ``ImportError`` at construction time if Playwright is not installed.
    """

    def __init__(
        self,
        session: BrowserSessionContext | None = None,
        *,
        allowed_domains: list[Any] | None = None,
        headless: bool = True,
        profile: str | None = None,
        artifact_dir: str | None = None,
        web_only: bool = False,
    ) -> None:
        if not is_playwright_available():
            raise ImportError(
                "playwright is not installed. Install with: pip install playwright && python -m playwright install"
            )
        self.session = session
        self._allowed_domains = allowed_domains or []
        self._headless = headless
        self._profile = profile
        self._artifact_dir = artifact_dir
        self._web_only = web_only
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None
        self._context: Any = None
        self._launch_lock = asyncio.Lock()

    def session_info(self) -> dict[str, Any]:
        """Return operator-visible browser session metadata."""

        profile_dir = os.path.join(PROFILE_DIR, self._profile) if self._profile else None
        return {
            "headless": self._headless,
            "profile": self._profile,
            "profile_dir": profile_dir,
            "gui_preview_available": not self._headless,
            "preview": "local GUI browser window" if not self._headless else "headless browser session",
            "execution_host": "backend",
            "isolated": self._profile is None,
        }

    async def _ensure_browser(self, *, reopen: bool = False) -> Any:
        async with self._launch_lock:
            if self._page is not None and getattr(self._page, "is_closed", lambda: False)():
                pages = list(self._context.pages) if self._context is not None else []
                if pages:
                    self._page = pages[-1]
                elif not reopen:
                    raise RuntimeError("The browser window was closed. Use browser_open with the intended URL to reopen it before continuing.")
                else:
                    await self.close()
            return await self._launch_browser()

    async def _launch_browser(self) -> Any:
        """Lazily launch browser and page.

        When *profile* is set, uses ``launch_persistent_context`` so cookies,
        localStorage, and login sessions survive across restarts.  Log in once
        interactively (``headless=False``) and every later automated run reuses
        that auth state.
        """
        if self._page is not None:
            return self._page
        from playwright.async_api import async_playwright  # type: ignore[import-untyped]

        self._playwright = await async_playwright().start()

        if self._profile:
            user_data_dir = os.path.join(PROFILE_DIR, self._profile)
            os.makedirs(user_data_dir, exist_ok=True)
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir,
                **_browser_launch_options(self._playwright.chromium, headless=self._headless),
            )
            self._page = (
                self._context.pages[0]
                if self._context.pages
                else await self._context.new_page()
            )
        else:
            self._browser = await self._playwright.chromium.launch(
                **_browser_launch_options(self._playwright.chromium, headless=self._headless),
            )
            self._context = await self._browser.new_context()
            self._page = await self._context.new_page()

        return self._page

    def _check_domain(self, url: str) -> None:
        """Raise if URL violates the domain allowlist."""
        if not self._allowed_domains:
            return
        from diane.tools.control_policy import BrowserDomainRule, is_domain_allowed

        rules = [
            r if isinstance(r, BrowserDomainRule) else BrowserDomainRule(pattern=str(r))
            for r in self._allowed_domains
        ]
        if not is_domain_allowed(url, rules):
            raise PermissionError(f"Domain not in allowlist: {url}")

    async def open(self, url: str) -> dict:
        if self._web_only:
            from urllib.parse import urlsplit
            if urlsplit(url).scheme not in {"http", "https"}:
                raise ValueError("Browser navigation requires an http or https URL")
        self._check_domain(url)
        page = await self._ensure_browser(reopen=True)
        await page.goto(url, wait_until="domcontentloaded")
        if self.session:
            self.session.current_url = url
        return {"status": "ok", "url": url, "title": await page.title(), "session": self.session_info()}

    async def click(self, selector: str) -> dict:
        page = await self._ensure_browser()
        await page.click(selector)
        return {"status": "ok", "selector": selector}

    async def type_text(self, selector: str, text: str) -> dict:
        page = await self._ensure_browser()
        await page.type(selector, text)
        return {"status": "ok", "selector": selector, "length": len(text)}

    async def fill(self, selector: str, text: str) -> dict:
        page = await self._ensure_browser()
        await page.fill(selector, text)
        return {"status": "ok", "selector": selector, "length": len(text)}

    async def select(self, selector: str, value: str) -> dict:
        page = await self._ensure_browser()
        await page.select_option(selector, value)
        return {"status": "ok", "selector": selector, "value": value}

    async def inspect_dom(
        self,
        selector: str | None = None,
        *,
        include_html: bool = False,
        include_elements: bool = True,
        max_html_length: int = 50000,
        element_limit: int = 100,
    ) -> dict:
        page = await self._ensure_browser()
        html = ""
        html_truncated = False
        if include_html:
            if selector:
                html = await page.locator(selector).evaluate("(el) => el.outerHTML")
            else:
                html = await page.content()
            original_length = len(html)
            html_truncated = original_length > max_html_length
            if html_truncated:
                html = html[:max_html_length] + "... [truncated]"
        else:
            original_length = 0

        elements: list[dict[str, Any]] = []
        if include_elements:
            elements = await page.evaluate(
                """
                ({ rootSelector, limit }) => {
                  const root = rootSelector ? document.querySelector(rootSelector) : document;
                  if (!root) return [];
                  const esc = (value) => {
                    if (window.CSS && CSS.escape) return CSS.escape(String(value));
                    return String(value).replace(/\\\\/g, "\\\\\\\\").replace(/"/g, "\\\"");
                  };
                  const selectorFor = (el) => {
                    if (el.id) return "#" + esc(el.id);
                    const dataTest = el.getAttribute("data-testid") || el.getAttribute("data-test");
                    if (dataTest) return `[data-testid="${esc(dataTest)}"]`;
                    const name = el.getAttribute("name");
                    const parts = [];
                    let current = el;
                    while (current && current.nodeType === Node.ELEMENT_NODE && current !== document.body) {
                      let part = current.tagName.toLowerCase();
                      if (current.id) {
                        parts.unshift("#" + esc(current.id));
                        break;
                      }
                      if (current === el && name) part += `[name="${esc(name)}"]`;
                      const parent = current.parentElement;
                      if (parent) {
                        const siblings = Array.from(parent.children).filter(
                          (sibling) => sibling.tagName === current.tagName
                        );
                        if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(current) + 1})`;
                      }
                      parts.unshift(part);
                      current = parent;
                    }
                    return parts.join(" > ");
                  };
                  const nodes = Array.from(root.querySelectorAll(
                    "a, button, input, textarea, select, option, summary, label, [role], [onclick], [contenteditable='true']"
                  ));
                  return nodes.slice(0, limit).map((el, index) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    const text = (el.innerText || el.textContent || el.getAttribute("aria-label") || "").trim();
                    return {
                      index,
                      selector: selectorFor(el),
                      tag: el.tagName.toLowerCase(),
                      role: el.getAttribute("role"),
                      type: el.getAttribute("type"),
                      name: el.getAttribute("name"),
                      id: el.id || null,
                      text: text.slice(0, 240),
                      aria_label: el.getAttribute("aria-label"),
                      placeholder: el.getAttribute("placeholder"),
                      href: el.getAttribute("href"),
                      value: el.value || null,
                      disabled: Boolean(el.disabled || el.getAttribute("aria-disabled") === "true"),
                      visible: Boolean(rect.width && rect.height && style.visibility !== "hidden" && style.display !== "none"),
                      bbox: {
                        x: Math.round(rect.x),
                        y: Math.round(rect.y),
                        width: Math.round(rect.width),
                        height: Math.round(rect.height),
                      },
                    };
                  });
                }
                """,
                {"rootSelector": selector, "limit": int(element_limit)},
            )

        tabs = await self.list_tabs()
        return {
            "url": page.url,
            "title": await page.title(),
            "selector": selector,
            "tabs": tabs,
            "html": html if include_html else None,
            "html_length": original_length,
            "html_truncated": html_truncated,
            "elements": elements,
            "element_count": len(elements),
            "session": self.session_info(),
        }

    async def wait_for(
        self, selector: str | None = None, timeout: float = 30.0
    ) -> dict:
        page = await self._ensure_browser()
        if selector:
            await page.wait_for_selector(selector, timeout=timeout * 1000)
        else:
            await page.wait_for_load_state("networkidle", timeout=timeout * 1000)
        return {"status": "ok", "selector": selector, "session": self.session_info()}

    async def extract_text(self, selector: str | None = None) -> str:
        page = await self._ensure_browser()
        if selector:
            el = await page.query_selector(selector)
            if el:
                return (await el.text_content()) or ""
            return ""
        return await page.inner_text("body")

    async def screenshot(self) -> str:
        page = await self._ensure_browser()
        d = self._artifact_dir or _ensure_screenshot_dir()
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"browser_{int(time.time() * 1000)}.png")
        await page.screenshot(path=path)
        if not self._artifact_dir:
            _cleanup_old_screenshots()
        return path

    async def download(
        self,
        selector: str | None = None,
        *,
        trigger: Callable[[], Awaitable[Any]] | None = None,
        destination_path: str | None = None,
        timeout: float = 30.0,
    ) -> str | None:
        page = await self._ensure_browser()
        if trigger is None and selector is None:
            logger.warning("download() requires a selector or trigger callback")
            return None
        try:
            async with page.expect_download(timeout=timeout * 1000) as dl_info:
                if trigger is not None:
                    await trigger()
                else:
                    await page.click(selector)
            dl = await dl_info.value
            save_path = _resolve_download_path(destination_path, dl.suggested_filename)
            await dl.save_as(save_path)
            return save_path
        except Exception:
            logger.debug("Browser download failed", exc_info=True)
            return None

    async def list_tabs(self) -> list[dict]:
        if self._context is None:
            return []
        pages = self._context.pages
        return [
            {"index": i, "url": p.url, "title": await p.title(), "session": self.session_info()}
            for i, p in enumerate(pages)
        ]

    async def switch_tab(self, index: int) -> dict:
        if self._context is None:
            return {"status": "error", "message": "No browser context"}
        pages = self._context.pages
        if 0 <= index < len(pages):
            self._page = pages[index]
            await self._page.bring_to_front()
            return {"status": "ok", "index": index, "url": self._page.url}
        return {"status": "error", "message": f"Tab index {index} out of range"}

    async def handle_native_dialog(self, dialog_type: str = "file_picker") -> dict:
        """Hand off to desktop layer for browser-triggered native dialogs.

        V1: returns a structured handoff request rather than directly
        controlling the dialog.
        """
        return {
            "status": "handoff_required",
            "dialog_type": dialog_type,
            "session_context": self.session,
        }

    async def close(self) -> None:
        try:
            if self._browser:
                await self._browser.close()
            elif self._context:
                await self._context.close()
        finally:
            self._browser = None
            self._page = None
            self._context = None
            if self._playwright:
                playwright, self._playwright = self._playwright, None
                await playwright.stop()


# ---------------------------------------------------------------------------
# Mock implementation (for testing)
# ---------------------------------------------------------------------------


class MockBrowserController:
    """Records all actions for test assertions. Returns configurable responses."""

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.actions: list[dict[str, Any]] = []
        self._responses = responses or {}
        self._closed = False

    def _record(self, action: str, **kwargs: Any) -> dict:
        entry = {"action": action, **kwargs}
        self.actions.append(entry)
        return self._responses.get(action, {"status": "ok", **kwargs})

    def session_info(self) -> dict[str, Any]:
        return {
            "headless": True,
            "profile": "mock",
            "profile_dir": None,
            "gui_preview_available": False,
            "preview": "mock browser session",
        }

    async def open(self, url: str) -> dict:
        return self._record("open", url=url)

    async def click(self, selector: str) -> dict:
        return self._record("click", selector=selector)

    async def type_text(self, selector: str, text: str) -> dict:
        return self._record("type_text", selector=selector, text=text)

    async def fill(self, selector: str, text: str) -> dict:
        return self._record("fill", selector=selector, text=text)

    async def select(self, selector: str, value: str) -> dict:
        return self._record("select", selector=selector, value=value)

    async def inspect_dom(
        self,
        selector: str | None = None,
        *,
        include_html: bool = False,
        include_elements: bool = True,
        max_html_length: int = 50000,
        element_limit: int = 100,
    ) -> dict:
        self._record(
            "inspect_dom",
            selector=selector,
            include_html=include_html,
            include_elements=include_elements,
            max_html_length=max_html_length,
            element_limit=element_limit,
        )
        return self._responses.get(
            "inspect_dom",
            {
                "url": "about:blank",
                "title": "New Tab",
                "selector": selector,
                "tabs": [{"index": 0, "url": "about:blank", "title": "New Tab"}],
                "html": "<button id=\"ok\">OK</button>" if include_html else None,
                "html_length": 27 if include_html else 0,
                "html_truncated": False,
                "elements": [
                    {
                        "index": 0,
                        "selector": "#ok",
                        "tag": "button",
                        "role": None,
                        "type": None,
                        "name": None,
                        "id": "ok",
                        "text": "OK",
                        "aria_label": None,
                        "placeholder": None,
                        "href": None,
                        "value": None,
                        "disabled": False,
                        "visible": True,
                        "bbox": {"x": 0, "y": 0, "width": 80, "height": 24},
                    }
                ]
                if include_elements
                else [],
                "element_count": 1 if include_elements else 0,
            },
        )

    async def wait_for(
        self, selector: str | None = None, timeout: float = 30.0
    ) -> dict:
        return self._record("wait_for", selector=selector, timeout=timeout)

    async def extract_text(self, selector: str | None = None) -> str:
        self._record("extract_text", selector=selector)
        return self._responses.get("extract_text", "mock text content")

    async def screenshot(self) -> str:
        self._record("screenshot")
        return self._responses.get("screenshot", "/tmp/mock_screenshot.png")

    async def download(
        self,
        selector: str | None = None,
        *,
        trigger: Callable[[], Awaitable[Any]] | None = None,
        destination_path: str | None = None,
        timeout: float = 30.0,
    ) -> str | None:
        self._record(
            "download",
            selector=selector,
            destination_path=destination_path,
            timeout=timeout,
        )
        if destination_path:
            return self._responses.get("download", destination_path)
        return self._responses.get("download", "/tmp/mock_download.pdf")

    async def list_tabs(self) -> list[dict]:
        self._record("list_tabs")
        return self._responses.get(
            "list_tabs",
            [{"index": 0, "url": "about:blank", "title": "New Tab"}],
        )

    async def switch_tab(self, index: int) -> dict:
        return self._record("switch_tab", index=index)

    async def handle_native_dialog(self, dialog_type: str = "file_picker") -> dict:
        self._record("handle_native_dialog", dialog_type=dialog_type)
        return {
            "status": "handoff_required",
            "dialog_type": dialog_type,
            "session_context": None,
        }

    async def close(self) -> None:
        self._closed = True
        self._record("close")
