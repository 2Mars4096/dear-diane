"""Shared lightweight Hugo metadata parser (not a general YAML loader)."""
from __future__ import annotations

import re


def _strip_yaml_comment(value: str) -> str:
    quote = ""
    for index, char in enumerate(value):
        if char in ("'", '"') and (index == 0 or value[index - 1] != "\\"):
            quote = "" if quote == char else quote or char
        if char == "#" and not quote and (index == 0 or value[index - 1].isspace()):
            return value[:index].strip()
    return value.strip()


def _yaml_scalar(value: str) -> str | bool:
    cleaned = _strip_yaml_comment(value)
    if not cleaned:
        return ""
    lowered = cleaned.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return cleaned.strip().strip("\"'")


def yaml_list(value: str | bool | list[str]) -> list[str]:
    if isinstance(value, list):
        return [item for item in value if item]
    if isinstance(value, bool):
        return []
    cleaned = str(value or "").strip()
    if not cleaned:
        return []
    if cleaned.startswith("[") and cleaned.endswith("]"):
        cleaned = cleaned[1:-1]
    return [
        str(_yaml_scalar(item)).strip()
        for item in cleaned.split(",")
        if str(_yaml_scalar(item)).strip()
    ]


def parse_yaml_frontmatter(raw: str) -> dict[str, str | bool | list[str]]:
    data: dict[str, str | bool | list[str]] = {}
    active_list_key = ""
    for line in raw.splitlines():
        if not line.strip():
            continue
        list_match = re.match(r"^\s*-\s+(.+?)\s*$", line)
        if list_match and active_list_key:
            current = data.get(active_list_key)
            data[active_list_key] = [
                *(current if isinstance(current, list) else []),
                str(_yaml_scalar(list_match.group(1))),
            ]
            continue
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*?)\s*$", line)
        if not match:
            continue
        key, value = match.group(1), match.group(2)
        if value:
            active_list_key = ""
            scalar = _yaml_scalar(value)
            data[key] = yaml_list(str(scalar)) if str(value).strip().startswith("[") else scalar
        else:
            active_list_key = key
            data[key] = []
    return data


