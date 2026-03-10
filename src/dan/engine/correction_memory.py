"""Correction memory — detect user corrections and route them to learning actions.

Captures when the user corrects DAN's output, classifies the correction type,
and produces learning actions (preference, principle, negative evidence).
"""

from __future__ import annotations

import re
import uuid
import logging
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

CorrectionType = Literal["negation", "override", "style", "redo", "preference"]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class CorrectionSignal(BaseModel):
    """A detected user correction of a previous assistant output."""

    confidence: float = 0.5
    correction_type: CorrectionType = "negation"
    original_output: str = ""
    correction_text: str = ""
    extracted_preference: str | None = None
    extracted_principle: str | None = None


class CorrectionRecord(BaseModel):
    """Persisted correction event with routing actions."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    signal: CorrectionSignal
    actions: list[dict] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Pattern tables
# ---------------------------------------------------------------------------

_NEGATION_STRONG = [
    r"\bno[,.]?\s+don'?t\b",
    r"\bstop\s+doing\b",
    r"\bthat'?s\s+wrong\b",
    r"\bnot\s+what\s+i\b",
    r"\bdon'?t\s+do\s+that\b",
]
_NEGATION_MODERATE = [
    r"\bnot\s+that\b",
    r"\bnot\s+like\s+that\b",
    r"\bwrong\b",
]

_OVERRIDE_STRONG = [
    r"\buse\s+\S+\s+instead\b",
    r"\bswitch\s+to\b",
    r"\breplace\s+\S+\s+with\b",
    r"\bchange\s+\S+\s+to\b",
]
_OVERRIDE_MODERATE = [
    r"\binstead\s+of\b",
    r"\brather\s+than\b",
]

_STYLE_STRONG = [
    r"\bmore\s+concise\b",
    r"\bsummarize\s+first\b",
    r"\bkeep\s+it\s+short\b",
    r"\btoo\s+(?:long|verbose|wordy)\b",
]
_STYLE_MODERATE = [
    r"\bshorter\b",
    r"\blonger\b",
    r"\bmore\s+detail\b",
    r"\bless\s+detail\b",
    r"\bsimplify\b",
    r"\bmore\s+formal\b",
]

_REDO_STRONG = [
    r"\btry\s+again\b",
    r"\bredo\b",
    r"\bdo\s+(?:it\s+)?over\b",
    r"\bstart\s+over\b",
]
_REDO_MODERATE = [
    r"\bagain\b",
    r"\bone\s+more\s+time\b",
]

_PREFERENCE_STRONG = [
    r"\bi\s+prefer\b",
    r"\balways\s+use\b",
    r"\bnever\s+use\b",
    r"\bi\s+like\s+\S+\s+better\b",
    r"\bfrom\s+now\s+on\b",
]
_PREFERENCE_MODERATE = [
    r"\bi\s+(?:like|want)\b",
    r"\bi'?d\s+rather\b",
]

_PATTERN_GROUPS: list[tuple[CorrectionType, list[str], float, list[str], float]] = [
    ("negation", _NEGATION_STRONG, 0.9, _NEGATION_MODERATE, 0.7),
    ("override", _OVERRIDE_STRONG, 0.9, _OVERRIDE_MODERATE, 0.7),
    ("style", _STYLE_STRONG, 0.9, _STYLE_MODERATE, 0.7),
    ("redo", _REDO_STRONG, 0.9, _REDO_MODERATE, 0.7),
    ("preference", _PREFERENCE_STRONG, 0.9, _PREFERENCE_MODERATE, 0.7),
]


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


def detect_correction(
    user_message: str,
    previous_assistant_message: str,
) -> CorrectionSignal | None:
    """Detect whether *user_message* corrects the previous assistant turn.

    Heuristic-first: keyword patterns → confidence scoring.
    Returns ``None`` when no correction signal is found.
    """
    if not user_message or not previous_assistant_message:
        return None

    text = user_message.strip()
    best_type: CorrectionType | None = None
    best_conf: float = 0.0

    for ctype, strong_pats, strong_conf, mod_pats, mod_conf in _PATTERN_GROUPS:
        for pat in strong_pats:
            if re.search(pat, text, re.IGNORECASE):
                if strong_conf > best_conf:
                    best_conf = strong_conf
                    best_type = ctype
                break
        for pat in mod_pats:
            if re.search(pat, text, re.IGNORECASE):
                if mod_conf > best_conf:
                    best_conf = mod_conf
                    best_type = ctype
                break

    if best_type is None:
        weak_signals = [
            r"\bno\b",
            r"\bactually\b",
            r"\bbut\b",
        ]
        for pat in weak_signals:
            if re.search(pat, text, re.IGNORECASE):
                best_conf = 0.5
                best_type = "negation"
                break

    if best_type is None:
        return None

    preference = _extract_preference(text) if best_type == "preference" else None
    principle = _extract_principle(text) if best_type in ("style", "override") else None

    return CorrectionSignal(
        confidence=best_conf,
        correction_type=best_type,
        original_output=previous_assistant_message[:500],
        correction_text=text,
        extracted_preference=preference,
        extracted_principle=principle,
    )


def _extract_preference(text: str) -> str | None:
    """Try to extract the user preference from a preference-type correction."""
    m = re.search(
        r"(?:i\s+prefer|always\s+use|never\s+use|from\s+now\s+on)\s+(.+)",
        text,
        re.IGNORECASE,
    )
    return m.group(1).strip().rstrip(".") if m else None


def _extract_principle(text: str) -> str | None:
    """Try to extract a general principle from style/override corrections."""
    m = re.search(
        r"(?:summarize\s+first|keep\s+it\s+short|use\s+\S+\s+instead|"
        r"change\s+\S+\s+to\s+\S+|switch\s+to\s+\S+)",
        text,
        re.IGNORECASE,
    )
    return m.group(0).strip() if m else None


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def route_correction(signal: CorrectionSignal) -> list[dict]:
    """Produce learning actions from a correction signal."""
    if signal.confidence < 0.7:
        return []

    actions: list[dict] = []

    if signal.correction_type == "preference" and signal.extracted_preference:
        actions.append({"type": "preference", "value": signal.extracted_preference})

    if signal.correction_type in ("style", "override") and signal.extracted_principle:
        actions.append({"type": "principle", "value": signal.extracted_principle})

    actions.append({
        "type": "negative_evidence",
        "scope": signal.correction_type,
        "decrement": 0.1,
    })

    return actions


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class CorrectionStore:
    """In-memory store for correction records."""

    def __init__(self) -> None:
        self._records: list[CorrectionRecord] = []

    def add(self, record: CorrectionRecord) -> None:
        self._records.append(record)

    def list_recent(self, n: int = 20) -> list[CorrectionRecord]:
        return self._records[-n:]

    def count(self) -> int:
        return len(self._records)
