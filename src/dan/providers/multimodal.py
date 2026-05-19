"""Shared helpers for provider-agnostic image message parts."""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


SUPPORTED_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
DEFAULT_MAX_IMAGE_BYTES = 20_000_000


def is_supported_image_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS


def image_mime_type(path: str | Path) -> str:
    guessed = mimetypes.guess_type(str(path))[0]
    if guessed and guessed.startswith("image/"):
        return guessed
    suffix = Path(path).suffix.lower()
    if suffix == ".jpg":
        return "image/jpeg"
    if suffix in {".png", ".jpeg", ".webp", ".gif"}:
        return f"image/{suffix.lstrip('.')}"
    return "image/png"


def image_data_url_from_path(
    path: str | Path,
    *,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
) -> str:
    resolved = Path(path).expanduser()
    if not resolved.is_file():
        raise FileNotFoundError(f"Image attachment not found: {path}")
    if not is_supported_image_path(resolved):
        raise ValueError(f"Unsupported image attachment type: {resolved.suffix}")
    size = resolved.stat().st_size
    if size > int(max_bytes):
        raise ValueError(f"Image attachment is {size:,} bytes, limit is {int(max_bytes):,}.")
    b64 = base64.b64encode(resolved.read_bytes()).decode("ascii")
    return f"data:{image_mime_type(resolved)};base64,{b64}"


def parse_data_url_image(url: str) -> tuple[str, str] | None:
    match = re.match(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.+)$", str(url or ""), flags=re.DOTALL)
    if not match:
        return None
    return match.group(1), match.group(2)


def openai_image_block_from_data_url(url: str) -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": url}}


def anthropic_image_block_from_data_url(url: str) -> dict[str, Any] | None:
    parsed = parse_data_url_image(url)
    if parsed is None:
        return None
    media_type, data = parsed
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": data,
        },
    }


def gemini_inline_data_from_data_url(url: str) -> dict[str, Any] | None:
    parsed = parse_data_url_image(url)
    if parsed is None:
        return None
    mime_type, data = parsed
    return {"inline_data": {"mime_type": mime_type, "data": data}}


def normalize_openai_content_blocks(content: Any) -> Any:
    """Normalize common image block aliases into Chat Completions content parts."""

    if not isinstance(content, list):
        return content
    normalized: list[Any] = []
    for part in content:
        if not isinstance(part, Mapping):
            normalized.append(part)
            continue
        block = dict(part)
        if block.get("type") != "image_url":
            normalized.append(block)
            continue
        image_url = block.get("image_url")
        if isinstance(image_url, Mapping) and image_url.get("url"):
            normalized.append({"type": "image_url", "image_url": {"url": str(image_url.get("url"))}})
            continue
        url = block.get("url")
        if url:
            normalized.append(openai_image_block_from_data_url(str(url)))
            continue
        normalized.append(block)
    return normalized


def normalize_openai_messages_for_multimodal(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for message in messages:
        row = dict(message)
        row["content"] = normalize_openai_content_blocks(row.get("content"))
        normalized.append(row)
    return normalized


def anthropic_blocks_from_openai_content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")
    blocks: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, Mapping):
            text = str(part or "")
            if text:
                blocks.append({"type": "text", "text": text})
            continue
        part_type = str(part.get("type") or "").strip()
        if part_type == "text":
            blocks.append({"type": "text", "text": str(part.get("text") or "")})
            continue
        if part_type == "image_url":
            image_url = part.get("image_url")
            url = ""
            if isinstance(image_url, Mapping):
                url = str(image_url.get("url") or "")
            else:
                url = str(part.get("url") or "")
            image_block = anthropic_image_block_from_data_url(url)
            if image_block is not None:
                blocks.append(image_block)
            continue
        blocks.append(dict(part))
    return blocks


def gemini_parts_from_openai_content(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"text": content}] if content else []
    if not isinstance(content, list):
        text = str(content or "")
        return [{"text": text}] if text else []
    parts: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, Mapping):
            text = str(part or "")
            if text:
                parts.append({"text": text})
            continue
        part_type = str(part.get("type") or "").strip()
        if part_type == "text":
            text = str(part.get("text") or "")
            if text:
                parts.append({"text": text})
            continue
        if part_type == "image_url":
            image_url = part.get("image_url")
            url = ""
            if isinstance(image_url, Mapping):
                url = str(image_url.get("url") or "")
            else:
                url = str(part.get("url") or "")
            inline = gemini_inline_data_from_data_url(url)
            if inline is not None:
                parts.append(inline)
            continue
    return parts


def _attachment_path(item: Mapping[str, Any]) -> str:
    return str(item.get("local_path") or item.get("path") or "").strip()


def image_attachment_payloads(
    raw_attachments: Any,
    *,
    max_images: int = 4,
) -> list[dict[str, Any]]:
    if not isinstance(raw_attachments, Sequence) or isinstance(raw_attachments, (str, bytes)):
        return []
    payloads: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_attachments:
        if len(payloads) >= int(max_images):
            break
        if not isinstance(raw, Mapping):
            continue
        path_text = _attachment_path(raw)
        if not path_text or not is_supported_image_path(path_text):
            continue
        kind = str(raw.get("kind") or "").strip().lower()
        if kind and kind not in {"image", "figure", "unknown"}:
            continue
        path = Path(path_text).expanduser()
        try:
            resolved = path.resolve()
            stat = resolved.stat()
        except OSError:
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        checksum = str(raw.get("checksum") or "").strip()
        if not checksum:
            try:
                checksum = hashlib.sha1(resolved.read_bytes()).hexdigest()
            except OSError:
                checksum = ""
        attachment_id = str(raw.get("id") or "").strip()
        if not attachment_id:
            attachment_id = f"image-{checksum[:16]}" if checksum else f"image-{len(payloads) + 1}"
        payload = dict(raw)
        payload.update(
            {
                "id": attachment_id,
                "kind": "image" if kind in {"", "unknown"} else kind,
                "local_path": str(resolved),
                "path": str(resolved),
                "display_name": str(raw.get("display_name") or raw.get("name") or resolved.name),
                "mime_type": str(raw.get("mime_type") or image_mime_type(resolved)),
                "size_bytes": int(raw.get("size_bytes") or stat.st_size),
                "checksum": checksum or None,
            }
        )
        payloads.append(payload)
    return payloads


def content_with_image_attachments(
    text: str,
    raw_attachments: Any,
    *,
    max_images: int = 4,
) -> str | list[dict[str, Any]]:
    payloads = image_attachment_payloads(raw_attachments, max_images=max_images)
    if not payloads:
        return str(text or "")
    attachment_lines = []
    blocks: list[dict[str, Any]] = []
    for index, item in enumerate(payloads, start=1):
        display_name = str(item.get("display_name") or Path(str(item.get("local_path") or "")).name or f"image {index}")
        path = str(item.get("local_path") or "")
        attachment_lines.append(f"- image {index}: {display_name} ({path})")
    prompt_text = str(text or "").rstrip()
    if attachment_lines:
        prompt_text = (
            f"{prompt_text}\n\nAttached images:\n" + "\n".join(attachment_lines)
            if prompt_text
            else "Attached images:\n" + "\n".join(attachment_lines)
        )
    blocks.append({"type": "text", "text": prompt_text})
    for item in payloads:
        try:
            url = image_data_url_from_path(str(item.get("local_path") or ""))
        except (OSError, ValueError):
            continue
        blocks.append(openai_image_block_from_data_url(url))
    if len(blocks) == 1:
        return str(text or "")
    return blocks


def image_attachments_from_metadata(metadata: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(metadata, Mapping):
        return []
    for key in ("image_attachments", "surface_image_attachments", "attachments", "surface_attachments"):
        payloads = image_attachment_payloads(metadata.get(key))
        if payloads:
            return payloads
    return []
