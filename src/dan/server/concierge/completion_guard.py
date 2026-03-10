"""Completion guard — validates that every requirement in the user's message
is addressed before sending the final response (31-9).

The guard is a *coverage* check, not a *quality* check.  It asks "did you
attempt every item?" rather than "is each answer good?".
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment knobs
# ---------------------------------------------------------------------------

_COMPLETION_CHECK_ENABLED = os.environ.get("DAN_COMPLETION_CHECK", "1") == "1"
_COMPLETION_CHECK_THRESHOLD = int(
    os.environ.get("DAN_COMPLETION_CHECK_THRESHOLD", "2")
)

# ---------------------------------------------------------------------------
# Pydantic v2 models
# ---------------------------------------------------------------------------

RequirementType = Literal["action", "question", "constraint", "deliverable"]
RequirementPriority = Literal["must", "should", "nice"]


class Requirement(BaseModel):
    id: str
    description: str
    type: RequirementType = "question"
    priority: RequirementPriority = "must"


class CompletionStatus(BaseModel):
    requirement_id: str
    status: Literal["addressed", "missing", "partial"] = "missing"
    evidence: str = ""


class CompletionReport(BaseModel):
    requirements: list[Requirement]
    statuses: list[CompletionStatus]
    all_met: bool
    addressed_count: int
    total_count: int


class CompletionStats(BaseModel):
    total_checks: int = 0
    passed: int = 0
    partial: int = 0
    failed: int = 0
    common_miss_patterns: dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Heuristic helpers
# ---------------------------------------------------------------------------

_NUMBERED_RE = re.compile(r"^\s*(\d+)[.)]\s+(.+)", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.+)", re.MULTILINE)
_AND_ALSO_RE = re.compile(r"\b(?:and also|also|additionally)\b[,:]?\s*(.+?)(?:[.!?]|$)", re.IGNORECASE)
_MAKE_SURE_RE = re.compile(r"\b(?:make sure|ensure|be sure|don'?t forget)\b[^.!?]*[.!?]?", re.IGNORECASE)
_IMPERATIVE_RE = re.compile(
    r"(?:^|\.\s+)((?:create|write|build|add|remove|delete|update|fix|implement|"
    r"generate|show|list|explain|describe|compare|summarize|send|deploy|test|"
    r"refactor|optimize|configure|set up|install)\b[^.!?]*)",
    re.IGNORECASE | re.MULTILINE,
)
_COMMA_SPLIT_RE = re.compile(r",\s*(?:and\s+)?", re.IGNORECASE)
_COLON_LIST_RE = re.compile(r"^(.+?):\s*(.+)$", re.DOTALL)
_IMPERATIVE_VERBS = (
    "create", "write", "build", "add", "remove", "delete", "update", "fix",
    "implement", "generate", "show", "list", "explain", "describe", "compare",
    "summarize", "send", "deploy", "test", "refactor", "optimize", "configure",
    "set up", "install", "search", "run", "check", "monitor", "design",
)
_MULTI_IMPERATIVE_RE = re.compile(
    r",\s*(?:and\s+)?(?=" + "|".join(_IMPERATIVE_VERBS) + r")",
    re.IGNORECASE,
)

_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can",
    "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "it", "its", "this", "that", "these", "those", "i", "me",
    "my", "we", "our", "you", "your", "he", "she", "they", "them",
    "and", "or", "but", "if", "then", "so", "as", "not", "no",
    "than", "too", "very", "just", "about", "up", "out", "how",
    "what", "which", "who", "when", "where", "why", "all", "each",
    "every", "some", "any", "most", "other", "into", "through",
    "please",
})


def _classify_requirement_type(text: str) -> RequirementType:
    lower = text.lower().strip()
    question_markers = ("what", "how", "why", "when", "where", "who", "which", "explain", "describe", "tell me")
    if lower.endswith("?") or any(lower.startswith(m) for m in question_markers):
        return "question"
    action_markers = ("create", "write", "build", "add", "remove", "delete", "update",
                       "fix", "implement", "deploy", "send", "install", "set up",
                       "refactor", "optimize", "configure", "test", "run")
    if any(lower.startswith(m) for m in action_markers):
        return "action"
    deliverable_markers = ("generate", "produce", "output", "provide", "show", "list", "give me")
    if any(lower.startswith(m) for m in deliverable_markers):
        return "deliverable"
    constraint_markers = ("make sure", "ensure", "don't", "must", "should", "always", "never",
                           "constraint", "limit", "require", "be sure")
    if any(m in lower for m in constraint_markers):
        return "constraint"
    return "question"


def _normalize_word(word: str) -> str:
    """Crude suffix stripping for better keyword overlap."""
    if word.endswith("tion"):
        return word[:-4] if len(word) > 6 else word
    if word.endswith("ing") and len(word) > 5:
        return word[:-3]
    if word.endswith("ment") and len(word) > 6:
        return word[:-4]
    if word.endswith("ness") and len(word) > 6:
        return word[:-4]
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("es") and len(word) > 4:
        return word[:-2]
    if word.endswith("ed") and len(word) > 4:
        return word[:-2]
    if word.endswith("s") and len(word) > 3 and not word.endswith("ss"):
        return word[:-1]
    return word


def _extract_keywords(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z]{2,}", text.lower())
    return {_normalize_word(w) for w in words if w not in _STOPWORDS}


def _complexity_score(message: str) -> int:
    """Rough proxy for how many distinct requests a message contains."""
    score = 0
    score += len(_NUMBERED_RE.findall(message))
    score += len(_BULLET_RE.findall(message))
    score += len(_AND_ALSO_RE.findall(message))
    score += len(_MAKE_SURE_RE.findall(message))
    if score == 0:
        sentences = [s.strip() for s in re.split(r"[.!?]+", message) if s.strip()]
        score = len(sentences)
    return max(score, 1)


# ---------------------------------------------------------------------------
# RequirementExtractor
# ---------------------------------------------------------------------------

LLMFunction = Callable[[str], Awaitable[str]]

_LLM_EXTRACT_PROMPT = """\
Extract the distinct requirements from the following user message.
Return one requirement per line, formatted as:
TYPE|PRIORITY|DESCRIPTION

TYPE is one of: action, question, constraint, deliverable
PRIORITY is one of: must, should, nice

User message:
{message}
"""


class RequirementExtractor:
    """Parses user messages into structured requirements.

    Heuristic-first; LLM fallback for complex ambiguous messages only.
    """

    def __init__(self, *, llm_fn: LLMFunction | None = None) -> None:
        self._llm_fn = llm_fn

    async def extract_requirements(self, message: str) -> list[Requirement]:
        reqs = self._heuristic_extract(message)
        if not reqs and self._llm_fn and _complexity_score(message) >= 2:
            reqs = await self._llm_extract(message)
        if not reqs:
            reqs = [Requirement(
                id="req_1",
                description=message.strip(),
                type=_classify_requirement_type(message),
                priority="must",
            )]
        return reqs

    # -- heuristic ----------------------------------------------------------

    def _heuristic_extract(self, message: str) -> list[Requirement]:
        reqs: list[Requirement] = []
        seen_descriptions: set[str] = set()

        def _add(desc: str) -> None:
            desc = desc.strip().rstrip(".")
            if not desc or len(desc) < 5:
                return
            norm = desc.lower()
            if norm in seen_descriptions:
                return
            seen_descriptions.add(norm)
            reqs.append(Requirement(
                id=f"req_{len(reqs) + 1}",
                description=desc,
                type=_classify_requirement_type(desc),
                priority="must",
            ))

        numbered = _NUMBERED_RE.findall(message)
        for _, desc in numbered:
            _add(desc)

        bullets = _BULLET_RE.findall(message)
        for desc in bullets:
            _add(desc)

        if not reqs:
            make_sure = _MAKE_SURE_RE.findall(message)
            for phrase in make_sure:
                _add(phrase)

            and_also = _AND_ALSO_RE.findall(message)
            for phrase in and_also:
                _add(phrase)

            imperatives = _IMPERATIVE_RE.findall(message)
            for desc in imperatives:
                sub_parts = _MULTI_IMPERATIVE_RE.split(desc)
                if len(sub_parts) > 1:
                    for part in sub_parts:
                        _add(part)
                else:
                    _add(desc)

        if not reqs:
            reqs = self._extract_comma_separated(message)

        if not reqs:
            reqs = self._extract_colon_list(message)

        return reqs

    def _extract_comma_separated(self, message: str) -> list[Requirement]:
        reqs: list[Requirement] = []
        parts = _COMMA_SPLIT_RE.split(message)
        if len(parts) < 2:
            return reqs
        for i, part in enumerate(parts, 1):
            part = part.strip().rstrip(".")
            if len(part) >= 5:
                reqs.append(Requirement(
                    id=f"req_{i}",
                    description=part,
                    type=_classify_requirement_type(part),
                    priority="must",
                ))
        return reqs

    def _extract_colon_list(self, message: str) -> list[Requirement]:
        """Handle patterns like 'X that can: A, B, and C'."""
        reqs: list[Requirement] = []
        m = _COLON_LIST_RE.match(message.strip())
        if not m:
            return reqs
        items_text = m.group(2)
        parts = _COMMA_SPLIT_RE.split(items_text)
        if len(parts) < 2:
            return reqs
        for i, part in enumerate(parts, 1):
            part = part.strip().rstrip(".")
            if len(part) >= 3:
                reqs.append(Requirement(
                    id=f"req_{i}",
                    description=part,
                    type=_classify_requirement_type(part),
                    priority="must",
                ))
        return reqs

    # -- LLM fallback -------------------------------------------------------

    async def _llm_extract(self, message: str) -> list[Requirement]:
        assert self._llm_fn is not None
        try:
            prompt = _LLM_EXTRACT_PROMPT.format(message=message)
            raw = await self._llm_fn(prompt)
            return self._parse_llm_response(raw)
        except Exception:
            logger.debug("LLM requirement extraction failed, falling back", exc_info=True)
            return []

    @staticmethod
    def _parse_llm_response(raw: str) -> list[Requirement]:
        reqs: list[Requirement] = []
        valid_types = {"action", "question", "constraint", "deliverable"}
        valid_priorities = {"must", "should", "nice"}
        for line in raw.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("|", 2)
            if len(parts) == 3:
                rtype = parts[0].strip().lower()
                rprio = parts[1].strip().lower()
                rdesc = parts[2].strip()
                if rtype not in valid_types:
                    rtype = "question"
                if rprio not in valid_priorities:
                    rprio = "must"
                reqs.append(Requirement(
                    id=f"req_{len(reqs) + 1}",
                    description=rdesc,
                    type=rtype,  # type: ignore[arg-type]
                    priority=rprio,  # type: ignore[arg-type]
                ))
        return reqs


# ---------------------------------------------------------------------------
# CompletionChecker
# ---------------------------------------------------------------------------

_LLM_VERIFY_PROMPT = """\
Does the following response address this requirement?
Requirement: {requirement}
Response: {response}

Answer with exactly one word: yes, no, or partial
"""


class CompletionChecker:
    """Checks whether a response covers all extracted requirements."""

    def __init__(self, *, llm_fn: LLMFunction | None = None) -> None:
        self._llm_fn = llm_fn

    async def check_completion(
        self,
        requirements: list[Requirement],
        response: str,
    ) -> CompletionReport:
        statuses: list[CompletionStatus] = []
        response_keywords = _extract_keywords(response)
        response_lower = response.lower()

        for req in requirements:
            status = self._heuristic_check(req, response_keywords, response_lower)
            if (
                status.status != "addressed"
                and req.priority == "must"
                and self._llm_fn is not None
            ):
                status = await self._llm_verify(req, response)
            statuses.append(status)

        addressed = sum(1 for s in statuses if s.status == "addressed")
        return CompletionReport(
            requirements=requirements,
            statuses=statuses,
            all_met=addressed == len(requirements),
            addressed_count=addressed,
            total_count=len(requirements),
        )

    @staticmethod
    def _heuristic_check(
        req: Requirement,
        response_keywords: set[str],
        response_lower: str,
    ) -> CompletionStatus:
        req_keywords = _extract_keywords(req.description)
        if not req_keywords:
            return CompletionStatus(
                requirement_id=req.id,
                status="addressed",
                evidence="no keywords to match",
            )

        overlap = req_keywords & response_keywords
        coverage = len(overlap) / len(req_keywords) if req_keywords else 0.0

        desc_lower = req.description.lower()
        phrases = [
            p.strip()
            for p in re.split(r"\s{2,}|,\s*", desc_lower)
            if len(p.strip()) > 4
        ]
        phrase_hits = sum(1 for p in phrases if p in response_lower) if phrases else 0
        phrase_coverage = phrase_hits / len(phrases) if phrases else 0.0

        effective = max(coverage, phrase_coverage)

        if effective >= 0.5:
            return CompletionStatus(
                requirement_id=req.id,
                status="addressed",
                evidence=f"keyword coverage {coverage:.0%}, phrase coverage {phrase_coverage:.0%}",
            )
        if effective >= 0.25:
            return CompletionStatus(
                requirement_id=req.id,
                status="partial",
                evidence=f"keyword coverage {coverage:.0%}, phrase coverage {phrase_coverage:.0%}",
            )
        return CompletionStatus(
            requirement_id=req.id,
            status="missing",
            evidence=f"keyword coverage {coverage:.0%}, phrase coverage {phrase_coverage:.0%}",
        )

    async def _llm_verify(
        self,
        req: Requirement,
        response: str,
    ) -> CompletionStatus:
        assert self._llm_fn is not None
        try:
            prompt = _LLM_VERIFY_PROMPT.format(
                requirement=req.description,
                response=response[:2000],
            )
            raw = (await self._llm_fn(prompt)).strip().lower()
            if raw.startswith("yes"):
                return CompletionStatus(
                    requirement_id=req.id, status="addressed", evidence="LLM verified"
                )
            if raw.startswith("partial"):
                return CompletionStatus(
                    requirement_id=req.id, status="partial", evidence="LLM: partial"
                )
            return CompletionStatus(
                requirement_id=req.id, status="missing", evidence="LLM: not addressed"
            )
        except Exception:
            logger.debug("LLM verification failed for %s", req.id, exc_info=True)
            return CompletionStatus(
                requirement_id=req.id, status="missing", evidence="LLM verification error"
            )


# ---------------------------------------------------------------------------
# Response augmentation
# ---------------------------------------------------------------------------

_MAX_AUTO_FIX_ATTEMPTS = 1


def augment_response(
    report: CompletionReport,
    response: str,
    *,
    _attempt: int = 0,
) -> tuple[str, str | None]:
    """Return ``(augmented_response, follow_up_or_none)``.

    - All met → response as-is.
    - Missing questions only → append a note.
    - Missed action/deliverable → return a follow-up message.
    - Anti-loop: max 1 auto-fix attempt.
    """
    if report.all_met:
        return response, None

    if _attempt >= _MAX_AUTO_FIX_ATTEMPTS:
        return response, None

    missing = [
        (req, st)
        for req, st in zip(report.requirements, report.statuses)
        if st.status in ("missing", "partial")
    ]
    if not missing:
        return response, None

    has_action_miss = any(
        req.type in ("action", "deliverable") for req, _ in missing
    )

    if has_action_miss:
        items = "\n".join(f"- {req.description}" for req, _ in missing)
        follow_up = (
            f"I missed the following items from your request:\n{items}\n\n"
            "Let me address those now."
        )
        return response, follow_up

    item_list = ", ".join(req.description for req, _ in missing)
    note = (
        f"\n\nNote: I addressed {report.addressed_count}/{report.total_count} items. "
        f"I wasn't able to cover: {item_list}."
    )
    return response + note, None


# ---------------------------------------------------------------------------
# Convenience: full pipeline
# ---------------------------------------------------------------------------

_global_stats = CompletionStats()


def get_stats() -> CompletionStats:
    return _global_stats


def reset_stats() -> None:
    global _global_stats
    _global_stats = CompletionStats()


async def run_completion_check(
    message: str,
    response: str,
    *,
    llm_fn: LLMFunction | None = None,
) -> tuple[str, str | None, CompletionReport]:
    """High-level entry: extract → check → augment → track stats."""
    if not _COMPLETION_CHECK_ENABLED:
        dummy = CompletionReport(
            requirements=[], statuses=[], all_met=True, addressed_count=0, total_count=0
        )
        return response, None, dummy

    extractor = RequirementExtractor(llm_fn=llm_fn)
    reqs = await extractor.extract_requirements(message)

    if len(reqs) < _COMPLETION_CHECK_THRESHOLD:
        dummy = CompletionReport(
            requirements=reqs,
            statuses=[
                CompletionStatus(requirement_id=r.id, status="addressed")
                for r in reqs
            ],
            all_met=True,
            addressed_count=len(reqs),
            total_count=len(reqs),
        )
        return response, None, dummy

    checker = CompletionChecker(llm_fn=llm_fn)
    report = await checker.check_completion(reqs, response)
    augmented, follow_up = augment_response(report, response)

    _update_stats(report)
    return augmented, follow_up, report


def _update_stats(report: CompletionReport) -> None:
    _global_stats.total_checks += 1
    if report.all_met:
        _global_stats.passed += 1
    elif report.addressed_count > 0:
        _global_stats.partial += 1
    else:
        _global_stats.failed += 1

    for req, st in zip(report.requirements, report.statuses):
        if st.status in ("missing", "partial"):
            key = req.type
            _global_stats.common_miss_patterns[key] = (
                _global_stats.common_miss_patterns.get(key, 0) + 1
            )


# ---------------------------------------------------------------------------
# /completion command handler
# ---------------------------------------------------------------------------


def handle_completion_command(msg: str, capability_context: Any) -> str:
    """Handle ``/completion`` — show completion check statistics."""
    stats = _global_stats
    if stats.total_checks == 0:
        return "No completion checks recorded yet."

    pass_rate = stats.passed / stats.total_checks * 100 if stats.total_checks else 0
    lines = [
        "**Completion Guard Stats**",
        f"Total checks: {stats.total_checks}",
        f"Passed (all met): {stats.passed} ({pass_rate:.0f}%)",
        f"Partial: {stats.partial}",
        f"Failed: {stats.failed}",
    ]
    if stats.common_miss_patterns:
        lines.append("\nCommon miss patterns:")
        for pattern, count in sorted(
            stats.common_miss_patterns.items(), key=lambda x: -x[1]
        ):
            lines.append(f"  {pattern}: {count}")
    return "\n".join(lines)
