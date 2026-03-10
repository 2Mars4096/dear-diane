"""Browser automation — Protocol, Playwright backend, and mock for testing.

Provides ``BrowserController`` protocol, ``PlaywrightBrowserController`` that
wraps Playwright (optional dependency), and ``MockBrowserController`` for tests.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

SCREENSHOT_DIR = os.path.expanduser("~/.dan/screenshots")
MAX_SCREENSHOTS = 50


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
    async def wait_for(
        self, selector: str | None = None, timeout: float = 30.0
    ) -> dict: ...
    async def extract_text(self, selector: str | None = None) -> str: ...
    async def screenshot(self) -> str: ...
    async def download(
        self, trigger: Callable[[], Awaitable[Any]] | None = None,
    ) -> str | None: ...
    async def list_tabs(self) -> list[dict]: ...
    async def switch_tab(self, index: int) -> dict: ...
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


def _ensure_screenshot_dir() -> str:
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    return SCREENSHOT_DIR


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
    ) -> None:
        if not is_playwright_available():
            raise ImportError(
                "playwright is not installed. Install with: pip install playwright && python -m playwright install"
            )
        self.session = session
        self._allowed_domains = allowed_domains or []
        self._headless = headless
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None
        self._context: Any = None

    async def _ensure_browser(self) -> Any:
        """Lazily launch browser and page."""
        if self._page is not None:
            return self._page
        from playwright.async_api import async_playwright  # type: ignore[import-untyped]

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self._headless)
        self._context = await self._browser.new_context()
        self._page = await self._context.new_page()
        return self._page

    def _check_domain(self, url: str) -> None:
        """Raise if URL violates the domain allowlist."""
        if not self._allowed_domains:
            return
        from dan.server.concierge.computer_policy import BrowserDomainRule, is_domain_allowed

        rules = [
            r if isinstance(r, BrowserDomainRule) else BrowserDomainRule(pattern=str(r))
            for r in self._allowed_domains
        ]
        if not is_domain_allowed(url, rules):
            raise PermissionError(f"Domain not in allowlist: {url}")

    async def open(self, url: str) -> dict:
        self._check_domain(url)
        page = await self._ensure_browser()
        await page.goto(url, wait_until="domcontentloaded")
        if self.session:
            self.session.current_url = url
        return {"status": "ok", "url": url, "title": await page.title()}

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

    async def wait_for(
        self, selector: str | None = None, timeout: float = 30.0
    ) -> dict:
        page = await self._ensure_browser()
        if selector:
            await page.wait_for_selector(selector, timeout=timeout * 1000)
        else:
            await page.wait_for_load_state("networkidle", timeout=timeout * 1000)
        return {"status": "ok", "selector": selector}

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
        d = _ensure_screenshot_dir()
        path = os.path.join(d, f"browser_{int(time.time() * 1000)}.png")
        await page.screenshot(path=path)
        _cleanup_old_screenshots()
        return path

    async def download(
        self, trigger: Callable[[], Awaitable[Any]] | None = None,
    ) -> str | None:
        page = await self._ensure_browser()
        if trigger is None:
            logger.warning("download() requires a trigger callback to start the download")
            return None
        try:
            async with page.expect_download(timeout=30000) as dl_info:
                await trigger()
            dl = await dl_info.value
            save_path = os.path.join(_ensure_screenshot_dir(), dl.suggested_filename)
            await dl.save_as(save_path)
            return save_path
        except Exception:
            return None

    async def list_tabs(self) -> list[dict]:
        if self._context is None:
            return []
        pages = self._context.pages
        return [
            {"index": i, "url": p.url, "title": await p.title()}
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

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None
            self._page = None
            self._context = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None


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
        self, trigger: Callable[[], Awaitable[Any]] | None = None,
    ) -> str | None:
        self._record("download")
        return self._responses.get("download", "/tmp/mock_download.pdf")

    async def list_tabs(self) -> list[dict]:
        self._record("list_tabs")
        return self._responses.get(
            "list_tabs",
            [{"index": 0, "url": "about:blank", "title": "New Tab"}],
        )

    async def switch_tab(self, index: int) -> dict:
        return self._record("switch_tab", index=index)

    async def close(self) -> None:
        self._closed = True
        self._record("close")
