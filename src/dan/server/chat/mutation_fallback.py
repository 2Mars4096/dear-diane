"""Compatibility wrapper for the canonical runtime-owned mutation fallback."""

from __future__ import annotations

import sys

from dan.agent_runtime import mutation_fallback as _mutation_fallback

sys.modules[__name__] = _mutation_fallback
