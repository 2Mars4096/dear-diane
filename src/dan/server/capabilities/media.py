"""Media capability handlers: image_describe, audio_transcribe."""
from __future__ import annotations

from typing import Any

from dan.server.capability_registry import CapabilityContext, CapabilityResult
from dan.server.capabilities._helpers import _resolve_user_path


async def handle_image_describe(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    raw_path = args.get("path", "").strip()
    if not raw_path:
        return CapabilityResult(success=False, message="No image path provided.")
    try:
        from dan.tools.image_describe import image_describe

        resolved = _resolve_user_path(raw_path)
        if not resolved.is_file():
            return CapabilityResult(success=False, message=f"Image not found: {raw_path}")
        result = await image_describe(
            path=str(resolved),
            question=str(args.get("question") or "Describe this image in detail."),
            model=str(args.get("model") or "gpt-4o"),
        )
        return CapabilityResult(
            success=True,
            message=str(result.get("description") or ""),
            data={"path": str(resolved), **result},
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Failed to describe image: {exc}")


async def handle_audio_transcribe(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    raw_path = args.get("path", "").strip()
    if not raw_path:
        return CapabilityResult(success=False, message="No audio path provided.")
    try:
        from dan.tools.audio_transcribe import audio_transcribe

        resolved = _resolve_user_path(raw_path)
        if not resolved.is_file():
            return CapabilityResult(success=False, message=f"Audio file not found: {raw_path}")
        result = await audio_transcribe(
            path=str(resolved),
            language=args.get("language"),
            model=str(args.get("model") or "whisper-1"),
        )
        return CapabilityResult(
            success=True,
            message=str(result.get("text") or ""),
            data={"path": str(resolved), **result},
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Failed to transcribe audio: {exc}")
