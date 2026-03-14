"""Browser and desktop computer-control capability handlers."""
from __future__ import annotations

from typing import Any

from dan.server.capability_registry import CapabilityContext, CapabilityResult

_computer_controller: Any = None


def _get_controller() -> Any:
    return _computer_controller


def set_controller(controller: Any) -> None:
    """Set the module-level computer controller instance."""
    global _computer_controller
    _computer_controller = controller


async def handle_browser_open(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    ctrl = _get_controller()
    if ctrl is None:
        return CapabilityResult(success=False, message="Computer controller not initialized")
    try:
        result = await ctrl.act("open", args["url"])
        return CapabilityResult(success=result.get("status") != "error", message=str(result), data=result)
    except Exception as exc:
        return CapabilityResult(success=False, message=f"browser_open error: {exc}")


async def handle_browser_click(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    ctrl = _get_controller()
    if ctrl is None:
        return CapabilityResult(success=False, message="Computer controller not initialized")
    try:
        result = await ctrl.act("click", args["selector"])
        return CapabilityResult(success=result.get("status") != "error", message=str(result), data=result)
    except Exception as exc:
        return CapabilityResult(success=False, message=f"browser_click error: {exc}")


async def handle_browser_type(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    ctrl = _get_controller()
    if ctrl is None:
        return CapabilityResult(success=False, message="Computer controller not initialized")
    try:
        result = await ctrl.act("type_text", args["selector"], text=args["text"])
        return CapabilityResult(success=result.get("status") != "error", message=str(result), data=result)
    except Exception as exc:
        return CapabilityResult(success=False, message=f"browser_type error: {exc}")


async def handle_browser_fill(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    ctrl = _get_controller()
    if ctrl is None:
        return CapabilityResult(success=False, message="Computer controller not initialized")
    try:
        result = await ctrl.act("fill", args["selector"], text=args["text"])
        return CapabilityResult(success=result.get("status") != "error", message=str(result), data=result)
    except Exception as exc:
        return CapabilityResult(success=False, message=f"browser_fill error: {exc}")


async def handle_browser_extract(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    ctrl = _get_controller()
    if ctrl is None:
        return CapabilityResult(success=False, message="Computer controller not initialized")
    try:
        result = await ctrl.act("extract_text", args.get("selector"))
        return CapabilityResult(success=True, message=str(result), data=result)
    except Exception as exc:
        return CapabilityResult(success=False, message=f"browser_extract error: {exc}")


async def handle_browser_screenshot(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    ctrl = _get_controller()
    if ctrl is None:
        return CapabilityResult(success=False, message="Computer controller not initialized")
    try:
        result = await ctrl.act("screenshot")
        return CapabilityResult(success=True, message=str(result), data=result)
    except Exception as exc:
        return CapabilityResult(success=False, message=f"browser_screenshot error: {exc}")


async def handle_browser_tabs(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    ctrl = _get_controller()
    if ctrl is None:
        return CapabilityResult(success=False, message="Computer controller not initialized")
    try:
        result = await ctrl.act("list_tabs")
        return CapabilityResult(success=True, message=str(result), data=result)
    except Exception as exc:
        return CapabilityResult(success=False, message=f"browser_tabs error: {exc}")


async def handle_desktop_observe(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    ctrl = _get_controller()
    if ctrl is None:
        return CapabilityResult(success=False, message="Computer controller not initialized")
    try:
        obs = await ctrl.observe("desktop")
        data = obs.model_dump() if hasattr(obs, "model_dump") else obs.dict()
        return CapabilityResult(success=True, message="Desktop observation captured", data=data)
    except Exception as exc:
        return CapabilityResult(success=False, message=f"desktop_observe error: {exc}")


async def handle_desktop_focus(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    ctrl = _get_controller()
    if ctrl is None:
        return CapabilityResult(success=False, message="Computer controller not initialized")
    try:
        result = await ctrl.act("focus_window", args["app"])
        return CapabilityResult(success=result.get("status") != "error", message=str(result), data=result)
    except Exception as exc:
        return CapabilityResult(success=False, message=f"desktop_focus error: {exc}")


async def handle_desktop_click(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    ctrl = _get_controller()
    if ctrl is None:
        return CapabilityResult(success=False, message="Computer controller not initialized")
    try:
        result = await ctrl.act("click", None, x=args["x"], y=args["y"])
        return CapabilityResult(success=result.get("status") != "error", message=str(result), data=result)
    except Exception as exc:
        return CapabilityResult(success=False, message=f"desktop_click error: {exc}")


async def handle_desktop_type(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    ctrl = _get_controller()
    if ctrl is None:
        return CapabilityResult(success=False, message="Computer controller not initialized")
    try:
        result = await ctrl.act("type_text", args["text"])
        return CapabilityResult(success=result.get("status") != "error", message=str(result), data=result)
    except Exception as exc:
        return CapabilityResult(success=False, message=f"desktop_type error: {exc}")


async def handle_desktop_hotkey(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    ctrl = _get_controller()
    if ctrl is None:
        return CapabilityResult(success=False, message="Computer controller not initialized")
    try:
        result = await ctrl.act("hotkey", None, keys=args["keys"])
        return CapabilityResult(success=result.get("status") != "error", message=str(result), data=result)
    except Exception as exc:
        return CapabilityResult(success=False, message=f"desktop_hotkey error: {exc}")
