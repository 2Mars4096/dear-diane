"""Git capability handlers."""
from __future__ import annotations

from typing import Any

from dan.server.capability_registry import CapabilityContext, CapabilityResult


async def handle_git_status(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    try:
        from dan.tools.git_status import git_status as _tool_git_status
        result = await _tool_git_status(**args)
        return CapabilityResult(success=True, message="Success", data=result, output_preview=str(result)[:1000])
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error in git_status: {exc}")


async def handle_git_diff(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    try:
        from dan.tools.git_diff import git_diff as _tool_git_diff
        result = await _tool_git_diff(**args)
        return CapabilityResult(success=True, message="Success", data=result, output_preview=str(result)[:1000])
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error in git_diff: {exc}")


async def handle_git_log(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    try:
        from dan.tools.git_log import git_log as _tool_git_log
        result = await _tool_git_log(**args)
        return CapabilityResult(success=True, message="Success", data=result, output_preview=str(result)[:1000])
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error in git_log: {exc}")


async def handle_git_branch(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    try:
        from dan.tools.git_branch import git_branch as _tool_git_branch
        result = await _tool_git_branch(**args)
        return CapabilityResult(success=True, message="Success", data=result, output_preview=str(result)[:1000])
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error in git_branch: {exc}")


async def handle_git_commit(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    try:
        from dan.tools.git_commit import git_commit as _tool_git_commit
        result = await _tool_git_commit(**args)
        return CapabilityResult(success=True, message="Success", data=result, output_preview=str(result)[:1000])
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error in git_commit: {exc}")


async def handle_git_worktree(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    try:
        from dan.tools.git_worktree import git_worktree as _tool_git_worktree
        result = await _tool_git_worktree(**args)
        return CapabilityResult(success=True, message="Success", data=result, output_preview=str(result)[:1000])
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error in git_worktree: {exc}")
