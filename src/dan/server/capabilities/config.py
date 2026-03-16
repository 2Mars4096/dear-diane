"""Config capability handlers: get_config, set_config."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dan.server.capability_registry import CapabilityContext, CapabilityResult

_CONFIGURABLE_PREFIXES = (
    "DAN_SMTP_", "DAN_BRAVE_API_KEY", "DAN_TAVILY_API_KEY",
    "DAN_GOOGLE_API_KEY", "DAN_ANTHROPIC_API_KEY", "DAN_OPENAI_API_KEY",
    "DAN_STATA_", "DAN_MCP_", "DAN_TOOL_", "DAN_PATH_",
    "DAN_LLM_MODEL", "DAN_CHAT_MODEL", "DAN_LLM_BASE_URL",
    "DAN_BOT_NAME", "DAN_ENABLE_TIER_POLICY", "DAN_FULL_TOOLS",
    "DAN_TELEMETRY", "DAN_LEARNING_MODE",
)


def _update_env_file(key: str, value: str) -> None:
    """Update or append a key=value in the .env file."""
    env_path = Path(os.environ.get("DAN_WORKSPACE_ROOT", os.getcwd())) / ".env"
    if not env_path.exists():
        env_path.write_text(f"{key}={value}\n")
        return

    lines = env_path.read_text().splitlines(keepends=True)
    found = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} ="):
            lines[i] = f"{key}={value}\n"
            found = True
            break
    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append(f"{key}={value}\n")
    env_path.write_text("".join(lines))


async def handle_get_config(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    import os
    from urllib.parse import urlparse

    model = getattr(ctx, "chat_manager", None)
    current_model = model._chat_model if model else os.environ.get("DAN_CHAT_MODEL") or os.environ.get("DAN_LLM_MODEL", "unknown")

    base_url = os.environ.get("DAN_LLM_BASE_URL", "")
    if base_url:
        try:
            parsed = urlparse(base_url)
            base_url = parsed.hostname or base_url
        except Exception:
            pass

    features = []
    for f in ("DAN_PROMPT_OPTIMIZATION", "DAN_MODEL_LEARNING", "DAN_TOPOLOGY_LEARNING", "DAN_SKILL_LEARNING", "DAN_MEMORY_EXTRACTION_LLM", "DAN_MEMORY_DUAL_WRITE"):
        if os.environ.get(f, "0") == "1":
            features.append(f)

    tier_policy = os.environ.get("DAN_ENABLE_TIER_POLICY", "0") == "1"
    full_tools = os.environ.get("DAN_FULL_TOOLS", "1") != "0"
    telemetry_enabled = os.environ.get("DAN_TELEMETRY", "1") == "1"
    learning_mode = os.environ.get("DAN_LEARNING_MODE", "0") == "1"

    mcp_servers = []
    if getattr(ctx, "mcp_bridge", None):
        mcp_servers = list(ctx.mcp_bridge.get_connected_servers().keys())

    config = {
        "model": current_model,
        "base_url_host": base_url,
        "bot_name": os.environ.get("DAN_BOT_NAME", "DAN"),
        "active_learning_features": features,
        "tier_policy_enabled": tier_policy,
        "full_tools_enabled": full_tools,
        "telemetry_enabled": telemetry_enabled,
        "learning_mode_enabled": learning_mode,
        "mcp_servers_connected": mcp_servers,
    }

    import json
    return CapabilityResult(success=True, message=json.dumps(config, indent=2))


async def handle_set_config(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    key = args.get("key", "").strip().upper()
    value = args.get("value", "").strip()
    if not key:
        return CapabilityResult(success=False, message="No key provided.")

    if not any(key.startswith(p) if p.endswith("_") else key == p for p in _CONFIGURABLE_PREFIXES):
        allowed = ", ".join(_CONFIGURABLE_PREFIXES)
        return CapabilityResult(
            success=False,
            message=f"Key '{key}' is not in the allowed list. Configurable prefixes: {allowed}",
        )

    os.environ[key] = value

    if key in ("DAN_LLM_MODEL", "DAN_CHAT_MODEL") and hasattr(ctx, "chat_manager") and ctx.chat_manager:
        ctx.chat_manager._chat_model = value or os.environ.get("DAN_LLM_MODEL", "claude-sonnet-4-6")

    if key == "DAN_BOT_NAME":
        try:
            from dan.server.concierge import identity as concierge_identity
            concierge_identity._cached_bot_name = None
        except Exception:
            pass

    try:
        from dan.utils.env import update_env_file
        update_env_file(key, value)
    except Exception as exc:
        return CapabilityResult(
            success=True,
            message=f"Set {key} in running server (but failed to persist to .env: {exc}). Will be lost on restart.",
        )

    display_value = value[:4] + "..." if len(value) > 8 else value
    return CapabilityResult(
        success=True,
        message=f"Set {key}={display_value} (active now + saved to .env).",
    )
