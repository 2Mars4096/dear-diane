"""Small allowlist helpers shared by Diane computer-control tools."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel


class BrowserDomainRule(BaseModel):
    pattern: str
    includes_subdomains: bool = True


def is_domain_allowed(url: str, rules: list[BrowserDomainRule]) -> bool:
    if not rules:
        return True
    try:
        hostname = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not hostname:
        return False
    for rule in rules:
        pattern = rule.pattern.lower()
        if hostname == pattern:
            return True
        if rule.includes_subdomains and hostname.endswith(f".{pattern}"):
            return True
    return False


def is_app_allowed(app_name: str, chunk_policy: Any) -> bool:
    allowed = list(getattr(chunk_policy, "allowed_apps", []) or [])
    if not allowed:
        return True
    normalized = app_name.lower()
    return any(str(candidate).lower() == normalized for candidate in allowed)


__all__ = ["BrowserDomainRule", "is_app_allowed", "is_domain_allowed"]
