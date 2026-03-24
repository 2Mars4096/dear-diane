"""Compatibility shim for canonical chat prompt definitions."""

from __future__ import annotations

import sys

from dan import chat_prompts as _chat_prompts

sys.modules[__name__] = _chat_prompts
