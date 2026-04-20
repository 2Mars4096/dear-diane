"""Workspace-local product state for the DAN Research CLI."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

RESEARCH_PRODUCT_NAME = "DAN Research"
RESEARCH_PRODUCT_DIRNAME = ".dan-research"
RESEARCH_PRODUCT_SESSION_FORMAT_VERSION = 1
RESEARCH_QUALITY_GATE_NAMES = (
    "time_anchor",
    "scope_boundary",
    "source_authority",
    "numeric_reconciliation",
    "claim_object_fit",
    "final_status",
)
RESEARCH_ARTIFACT_MODES = (
    "final_report",
    "provisional_report",
    "blocker_report",
)


def _compute_runtime_build_id() -> str:
    digest = hashlib.sha256()
    digest.update(str(RESEARCH_PRODUCT_SESSION_FORMAT_VERSION).encode("utf-8"))
    fingerprint_paths = (
        Path(__file__).resolve(),
        Path(__file__).with_name("research.py").resolve(),
        Path(__file__).resolve().parents[1]
        / "worker"
        / "organisms"
        / "research_conversation.py",
    )
    for path in fingerprint_paths:
        digest.update(str(path).encode("utf-8"))
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<missing>")
    return digest.hexdigest()[:12]


RESEARCH_PRODUCT_RUNTIME_BUILD_ID = _compute_runtime_build_id()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _resolve_path(value: str | Path, *, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    temp_path.write_text(text, encoding="utf-8")
    temp_path.replace(path)


def _normalize_text_list(value: Any) -> list[str]:
    if value is None:
        return []

    normalized: list[str] = []
    if isinstance(value, str):
        texts = [value]
    elif isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
        if items and all(len(item) <= 1 for item in items):
            texts = ["".join(items)]
        else:
            texts = items
    elif isinstance(value, set):
        texts = [str(item) for item in value]
    else:
        texts = [str(value)]

    for text in texts:
        for line in str(text).splitlines():
            cleaned = line.strip()
            if not cleaned:
                continue
            if cleaned.startswith("- "):
                cleaned = cleaned[2:].strip()
            normalized.append(cleaned)
    if normalized:
        return normalized

    cleaned = " ".join(str(text).strip() for text in texts).strip()
    return [cleaned] if cleaned else []


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _normalize_claim_component(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).strip()


def _canonical_claim_key(
    *,
    fact: Any,
    entity_type: Any = "",
    metric_kind: Any = "",
    series_kind: Any = "",
    geography_or_scope: Any = "",
    comparison_basis: Any = "",
) -> str:
    parts = [
        _normalize_claim_component(geography_or_scope),
        _normalize_claim_component(entity_type),
        _normalize_claim_component(metric_kind),
        _normalize_claim_component(series_kind),
        _normalize_claim_component(comparison_basis),
        _normalize_claim_component(fact),
    ]
    return "|".join(part for part in parts if part)


def _source_authority_rank(value: Any) -> int:
    lowered = str(value or "").strip().lower()
    if not lowered:
        return 0
    if any(
        token in lowered
        for token in (
            "sec",
            "edgar",
            "government",
            "regulator",
            "issuer",
            "exchange",
            "official",
            "prospectus",
            "fact sheet",
            "factsheet",
            "press release",
            "occ",
            "nyse",
            "nasdaq",
            "hkex",
            "sse",
            "szse",
        )
    ):
        return 100
    if any(
        token in lowered
        for token in (
            "bloomberg",
            "reuters",
            "fred",
            "fred",
            "marketwatch",
            "mysteel",
            "cctd",
            "benchmark",
            "industry",
            "refinitiv",
            "factset",
            "wsj",
            "cnbc",
            "ycharts",
        )
    ):
        return 70
    if any(
        token in lowered
        for token in (
            "yahoo",
            "marketbeat",
            "robinhood",
            "public.com",
            "retail",
            "blog",
            "forum",
        )
    ):
        return 20
    return 40


def _normalize_artifact_mode(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"blocker", "blocked", "incomplete", "blocker_report"}:
        return "blocker_report"
    if text in {"provisional", "draft", "provisional_report"}:
        return "provisional_report"
    return "final_report"


def _infer_artifact_mode(*, status: Any, report_readiness: Any) -> str:
    normalized_status = str(status or "").strip().lower()
    readiness = _normalize_report_readiness(report_readiness)
    if normalized_status != "completed" or readiness == "blocked":
        return "blocker_report"
    if readiness == "provisional":
        return "provisional_report"
    return "final_report"


def _verification_fact_is_frozen(
    *,
    status: Any,
    note: Any = "",
    source: Any = "",
    fact: Any = "",
) -> bool:
    if _normalize_verification_status(status) == "verified":
        return True
    lowered = " ".join(
        str(part or "").strip().lower() for part in (note, source, fact) if str(part or "").strip()
    )
    return any(
        cue in lowered
        for cue in (
            "unverifiable",
            "not available",
            "not published",
            "not disclosed",
            "no public series",
            "source inaccessible",
        )
    )


def _claim_record_priority(fact: "ResearchVerificationFact") -> tuple[int, int, int, int]:
    status_weight = {
        "verified": 3,
        "conflicted": 2,
        "unverified": 1,
    }.get(str(fact.status or "").strip().lower(), 0)
    return (
        status_weight,
        int(fact.authority_rank or 0),
        1 if str(fact.as_of or "").strip() else 0,
        len(str(fact.source or "").strip()),
    )


def _normalize_verification_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"verified", "confirm", "confirmed", "grounded", "supported", "true"}:
        return "verified"
    if text in {"conflict", "conflicted", "contradicted", "disputed", "inconsistent"}:
        return "conflicted"
    return "unverified"


def _normalize_audit_issue_kind(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"logic", "logical", "reasoning"}:
        return "logic"
    if text in {"freshness", "staleness", "currentness", "date"}:
        return "freshness"
    if text in {"authority", "source", "source_authority", "provenance"}:
        return "authority"
    if text in {"scope_fit", "scope", "fit", "mismatch", "mapping"}:
        return "scope_fit"
    if text in {"conflict", "contradiction", "contradictions"}:
        return "conflict"
    if text in {"method", "methodology", "measurement", "quant"}:
        return "method"
    return "logic"


def _normalize_audit_issue_severity(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"critical", "blocker", "blocking", "fatal"}:
        return "critical"
    if text in {"major", "material", "high"}:
        return "major"
    return "minor"


def _normalize_report_readiness(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"blocked", "not_ready", "notready"}:
        return "blocked"
    if text in {"provisional", "draft", "exploratory", "scout"}:
        return "provisional"
    if text in {"grounded", "stable", "ready"}:
        return "grounded"
    if text in {
        "actionable",
        "decision_ready",
        "decisionready",
        "recommendation_ready",
        "recommendation_grade",
        "recommendationgrade",
    }:
        return "actionable"
    return "provisional"


def _normalize_temporal_mode(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"current", "current_as_of_runtime", "live", "latest"}:
        return "current"
    if text in {"historical", "historical_snapshot", "snapshot", "as_of"}:
        return "historical_snapshot"
    if text in {"trend", "trend_over_time", "range", "timeline"}:
        return "trend"
    return "timeless"


def _normalize_quality_gate_name(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "time": "time_anchor",
        "date": "time_anchor",
        "freshness": "time_anchor",
        "scope": "scope_boundary",
        "scope_fit": "scope_boundary",
        "boundary": "scope_boundary",
        "source": "source_authority",
        "authority": "source_authority",
        "primary_source": "source_authority",
        "numeric": "numeric_reconciliation",
        "number": "numeric_reconciliation",
        "aum": "numeric_reconciliation",
        "reconciliation": "numeric_reconciliation",
        "claim_fit": "claim_object_fit",
        "object_fit": "claim_object_fit",
        "instrument_fit": "claim_object_fit",
        "thesis_fit": "claim_object_fit",
        "readiness": "final_status",
        "final": "final_status",
        "status": "final_status",
    }
    return aliases.get(text, text if text in RESEARCH_QUALITY_GATE_NAMES else "final_status")


def _normalize_quality_gate_status(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"pass", "passed", "ok", "met", "clear", "satisfied"}:
        return "pass"
    if text in {"fail", "failed", "block", "blocked", "missing", "critical"}:
        return "fail"
    if text in {"na", "n_a", "not_applicable", "notapplicable", "irrelevant"}:
        return "not_applicable"
    return "warn"


def _normalize_integrity_claim_status(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"accepted", "verified", "grounded", "supported", "resolved"}:
        return "accepted"
    if text in {
        "accepted_with_proxy",
        "proxy",
        "proxied",
        "derived",
        "lagged_proxy",
        "estimated",
    }:
        return "accepted_with_proxy"
    if text in {"conflict", "conflicted", "contradicted", "disputed"}:
        return "conflicted"
    if text in {
        "unverifiable",
        "not_available",
        "not_published",
        "not_disclosed",
        "no_public_series",
    }:
        return "unverifiable"
    return "pending"


def _normalize_integrity_check_status(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"matched", "resolved", "verified", "clear", "present"}:
        return "matched"
    if text in {"missing", "absent", "unset"}:
        return "missing"
    return "unclear"


_INTEGRITY_UNVERIFIABLE_CUES = (
    "unverifiable",
    "not available",
    "not published",
    "not disclosed",
    "no public series",
    "source inaccessible",
)
_INTEGRITY_PROXY_CUES = (
    "proxy",
    "lagged",
    "weekly",
    "monthly",
    "approx",
    "approximate",
    "estimated",
    "estimate",
    "derived",
    "interpolated",
    "fallback",
    "stale",
)


def _unit_or_scale_present(*texts: Any) -> bool:
    lowered = " ".join(str(text or "").strip().lower() for text in texts if str(text or "").strip())
    if not lowered:
        return False
    return any(
        token in lowered
        for token in (
            "%",
            "percent",
            "basis point",
            "bps",
            "usd",
            "cny",
            "rmb",
            "eur",
            "gbp",
            "hk$",
            "¥",
            "$",
            "million",
            "billion",
            "trillion",
            "per share",
            "per barrel",
            "per tonne",
            "per ton",
            "shares/day",
            "volume",
        )
    )


def _claim_issue_overlap(*, claim_key: str, claim: str, issue_claim: Any, issue_text: Any) -> bool:
    issue_claim_key = _canonical_claim_key(fact=issue_claim)
    if claim_key and issue_claim_key and claim_key == issue_claim_key:
        return True
    claim_tokens = set(claim_key.split()) if claim_key else set()
    issue_tokens = set(issue_claim_key.split()) if issue_claim_key else set()
    if claim_tokens and issue_tokens and len(claim_tokens & issue_tokens) >= min(2, len(claim_tokens), len(issue_tokens)):
        return True
    claim_text = _normalize_claim_component(claim)
    issue_text_normalized = _normalize_claim_component(issue_text)
    if claim_text and issue_text_normalized and (
        claim_text in issue_text_normalized or issue_text_normalized in claim_text
    ):
        return True
    return False


def _claim_related_audit_issues(
    *,
    fact: "ResearchVerificationFact",
    audit_issues: list["ResearchAuditIssue"],
) -> list["ResearchAuditIssue"]:
    related: list["ResearchAuditIssue"] = []
    claim_key = str(fact.claim_key or "").strip()
    for issue in audit_issues:
        if _claim_issue_overlap(
            claim_key=claim_key,
            claim=fact.fact,
            issue_claim=issue.affected_claim,
            issue_text=issue.issue,
        ):
            related.append(issue)
    return related


def _integrity_note_text(*texts: Any) -> str:
    return " ".join(str(text or "").strip().lower() for text in texts if str(text or "").strip())


def _derive_integrity_claim_status(
    *,
    fact: "ResearchVerificationFact",
    related_issues: list["ResearchAuditIssue"],
) -> str:
    note_text = _integrity_note_text(
        fact.note,
        *(issue.issue for issue in related_issues),
        *(issue.required_follow_up for issue in related_issues),
    )
    if str(fact.status or "").strip().lower() == "conflicted" or any(
        issue.kind == "conflict" for issue in related_issues
    ):
        return "conflicted"
    if any(cue in note_text for cue in _INTEGRITY_UNVERIFIABLE_CUES):
        return "unverifiable"
    if str(fact.status or "").strip().lower() == "verified":
        if related_issues or any(cue in note_text for cue in _INTEGRITY_PROXY_CUES):
            return "accepted_with_proxy"
        return "accepted"
    if any(cue in note_text for cue in _INTEGRITY_PROXY_CUES):
        return "accepted_with_proxy"
    return "pending"


def _derive_integrity_check_status(
    *,
    known: bool,
    has_issue: bool,
) -> str:
    if has_issue:
        return "unclear"
    return "matched" if known else "missing"


_CONFIDENCE_LABELS: list[tuple[str, float]] = [
    ("very high", 0.9),
    ("high", 0.8),
    ("medium-high", 0.7),
    ("medium high", 0.7),
    ("medium", 0.6),
    ("medium-low", 0.4),
    ("medium low", 0.4),
    ("low", 0.25),
    ("very low", 0.1),
]


def _normalize_confidence(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        score = float(value)
        if score > 1.0 and score <= 100.0:
            score = score / 100.0
        return max(0.0, min(1.0, score))

    text = str(value).strip()
    if not text:
        return None

    percent_match = re.search(r"(-?\d+(?:\.\d+)?)\s*%", text)
    if percent_match:
        try:
            score = float(percent_match.group(1)) / 100.0
            return max(0.0, min(1.0, score))
        except ValueError:
            pass

    numeric_match = re.search(r"-?\d+(?:\.\d+)?", text)
    if numeric_match:
        try:
            score = float(numeric_match.group(0))
            if score > 1.0 and score <= 100.0:
                score = score / 100.0
            return max(0.0, min(1.0, score))
        except ValueError:
            pass

    lowered = text.lower()
    for label, mapped in _CONFIDENCE_LABELS:
        if label in lowered:
            return mapped
    return None


class ResearchVerificationFact(BaseModel):
    """Structured verification row for critical report facts."""

    fact: str
    status: Literal["verified", "unverified", "conflicted"] = "unverified"
    source: str = ""
    as_of: str = ""
    note: str = ""
    claim_key: str = ""
    authority_rank: int = 0
    entity_type: str = ""
    metric_kind: str = ""
    series_kind: str = ""
    geography_or_scope: str = ""
    comparison_basis: str = ""

    @field_validator(
        "fact",
        "source",
        "as_of",
        "note",
        "claim_key",
        "entity_type",
        "metric_kind",
        "series_kind",
        "geography_or_scope",
        "comparison_basis",
        mode="before",
    )
    @classmethod
    def _normalize_text_field(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_status_field(cls, value: Any) -> str:
        return _normalize_verification_status(value)

    @field_validator("authority_rank", mode="before")
    @classmethod
    def _normalize_authority_rank_field(cls, value: Any) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 0
        return parsed if parsed > 0 else 0

    @model_validator(mode="after")
    def _derive_claim_metadata(self) -> "ResearchVerificationFact":
        self.claim_key = self.claim_key or _canonical_claim_key(
            fact=self.fact,
            entity_type=self.entity_type,
            metric_kind=self.metric_kind,
            series_kind=self.series_kind,
            geography_or_scope=self.geography_or_scope,
            comparison_basis=self.comparison_basis,
        )
        if not self.authority_rank:
            self.authority_rank = _source_authority_rank(self.source)
        return self


class ResearchAuditIssue(BaseModel):
    """Structured audit row for unresolved reasoning or sourcing issues."""

    kind: Literal[
        "logic",
        "freshness",
        "authority",
        "scope_fit",
        "conflict",
        "method",
    ] = "logic"
    severity: Literal["critical", "major", "minor"] = "minor"
    issue: str
    affected_claim: str = ""
    required_follow_up: str = ""

    @field_validator("issue", "affected_claim", "required_follow_up", mode="before")
    @classmethod
    def _normalize_text_field(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("kind", mode="before")
    @classmethod
    def _normalize_kind_field(cls, value: Any) -> str:
        return _normalize_audit_issue_kind(value)

    @field_validator("severity", mode="before")
    @classmethod
    def _normalize_severity_field(cls, value: Any) -> str:
        return _normalize_audit_issue_severity(value)


class ResearchQualityGate(BaseModel):
    """Standard high-level quality gate row for general deep research."""

    gate: Literal[
        "time_anchor",
        "scope_boundary",
        "source_authority",
        "numeric_reconciliation",
        "claim_object_fit",
        "final_status",
    ] = "final_status"
    status: Literal["pass", "warn", "fail", "not_applicable"] = "warn"
    summary: str = ""
    evidence_ref: str = ""
    required_follow_up: str = ""

    @field_validator("summary", "evidence_ref", "required_follow_up", mode="before")
    @classmethod
    def _normalize_text_field(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("gate", mode="before")
    @classmethod
    def _normalize_gate_field(cls, value: Any) -> str:
        return _normalize_quality_gate_name(value)

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_status_field(cls, value: Any) -> str:
        return _normalize_quality_gate_status(value)


class ResearchEvidenceIntegrityRow(BaseModel):
    """Canonicalized integrity row for one important claim in a research report."""

    claim: str
    claim_key: str = ""
    status: Literal[
        "accepted",
        "accepted_with_proxy",
        "conflicted",
        "unverifiable",
        "pending",
    ] = "pending"
    source_identity: Literal["matched", "unclear", "missing"] = "unclear"
    metric_identity: Literal["matched", "unclear", "missing"] = "unclear"
    unit_scale_consistency: Literal["matched", "unclear", "missing"] = "unclear"
    time_alignment: Literal["matched", "unclear", "missing"] = "unclear"
    scope_alignment: Literal["matched", "unclear", "missing"] = "unclear"
    same_source_consistency: Literal["matched", "unclear", "missing"] = "unclear"
    source: str = ""
    as_of: str = ""
    rationale: str = ""
    entity_type: str = ""
    metric_kind: str = ""
    series_kind: str = ""
    geography_or_scope: str = ""
    comparison_basis: str = ""

    @field_validator(
        "claim",
        "claim_key",
        "source",
        "as_of",
        "rationale",
        "entity_type",
        "metric_kind",
        "series_kind",
        "geography_or_scope",
        "comparison_basis",
        mode="before",
    )
    @classmethod
    def _normalize_text_field(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_status_field(cls, value: Any) -> str:
        return _normalize_integrity_claim_status(value)

    @field_validator(
        "source_identity",
        "metric_identity",
        "unit_scale_consistency",
        "time_alignment",
        "scope_alignment",
        "same_source_consistency",
        mode="before",
    )
    @classmethod
    def _normalize_check_status_field(cls, value: Any) -> str:
        return _normalize_integrity_check_status(value)

    @model_validator(mode="after")
    def _derive_claim_key(self) -> "ResearchEvidenceIntegrityRow":
        self.claim_key = self.claim_key or _canonical_claim_key(
            fact=self.claim,
            entity_type=self.entity_type,
            metric_kind=self.metric_kind,
            series_kind=self.series_kind,
            geography_or_scope=self.geography_or_scope,
            comparison_basis=self.comparison_basis,
        )
        return self


def _normalize_verification_facts(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []

    if isinstance(value, dict):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        items = [value]

    normalized: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, ResearchVerificationFact):
            normalized.append(item.model_dump(mode="json"))
            continue
        if isinstance(item, dict):
            fact = str(
                item.get("fact")
                or item.get("claim")
                or item.get("statement")
                or item.get("item")
                or ""
            ).strip()
            if not fact:
                continue
            normalized.append(
                {
                    "fact": fact,
                    "status": _normalize_verification_status(item.get("status")),
                    "source": str(item.get("source") or item.get("evidence_ref") or "").strip(),
                    "as_of": str(item.get("as_of") or item.get("date") or "").strip(),
                    "note": str(item.get("note") or item.get("rationale") or "").strip(),
                    "claim_key": str(item.get("claim_key") or "").strip(),
                    "authority_rank": item.get("authority_rank") or 0,
                    "entity_type": str(item.get("entity_type") or "").strip(),
                    "metric_kind": str(item.get("metric_kind") or "").strip(),
                    "series_kind": str(item.get("series_kind") or "").strip(),
                    "geography_or_scope": str(
                        item.get("geography_or_scope") or item.get("scope") or ""
                    ).strip(),
                    "comparison_basis": str(item.get("comparison_basis") or "").strip(),
                }
            )
            continue

        fact = str(item or "").strip()
        if not fact:
            continue
        normalized.append(
            {
                "fact": fact,
                "status": "unverified",
                "source": "",
                "as_of": "",
                "note": "",
                "claim_key": "",
                "authority_rank": 0,
                "entity_type": "",
                "metric_kind": "",
                "series_kind": "",
                "geography_or_scope": "",
                "comparison_basis": "",
            }
        )
    return normalized


def _normalize_audit_issues(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []

    if isinstance(value, dict):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        items = [value]

    normalized: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, ResearchAuditIssue):
            normalized.append(item.model_dump(mode="json"))
            continue
        if isinstance(item, dict):
            issue = str(
                item.get("issue")
                or item.get("gap")
                or item.get("problem")
                or item.get("finding")
                or ""
            ).strip()
            if not issue:
                continue
            normalized.append(
                {
                    "kind": _normalize_audit_issue_kind(
                        item.get("kind") or item.get("type")
                    ),
                    "severity": _normalize_audit_issue_severity(
                        item.get("severity") or item.get("level")
                    ),
                    "issue": issue,
                    "affected_claim": str(
                        item.get("affected_claim")
                        or item.get("claim")
                        or item.get("target")
                        or ""
                    ).strip(),
                    "required_follow_up": str(
                        item.get("required_follow_up")
                        or item.get("follow_up")
                        or item.get("next_step")
                        or item.get("repair")
                        or ""
                    ).strip(),
                }
            )
            continue

        issue = str(item or "").strip()
        if not issue:
            continue
        normalized.append(
            {
                "kind": "logic",
                "severity": "minor",
                "issue": issue,
                "affected_claim": "",
                "required_follow_up": "",
            }
        )
    return normalized


def _normalize_quality_gates(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []

    if isinstance(value, dict):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        items = [value]

    normalized: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, ResearchQualityGate):
            normalized.append(item.model_dump(mode="json"))
            continue
        if isinstance(item, dict):
            gate = _normalize_quality_gate_name(
                item.get("gate") or item.get("name") or item.get("type")
            )
            summary = str(
                item.get("summary")
                or item.get("finding")
                or item.get("note")
                or item.get("issue")
                or ""
            ).strip()
            normalized.append(
                {
                    "gate": gate,
                    "status": _normalize_quality_gate_status(item.get("status")),
                    "summary": summary,
                    "evidence_ref": str(
                        item.get("evidence_ref")
                        or item.get("source")
                        or item.get("ref")
                        or ""
                    ).strip(),
                    "required_follow_up": str(
                        item.get("required_follow_up")
                        or item.get("follow_up")
                        or item.get("next_step")
                        or ""
                    ).strip(),
                }
            )
            continue

        text = str(item or "").strip()
        if not text:
            continue
        normalized.append(
            {
                "gate": "final_status",
                "status": "warn",
                "summary": text,
                "evidence_ref": "",
                "required_follow_up": "",
            }
        )
    return normalized


def _normalize_evidence_integrity(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []

    if isinstance(value, dict):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        items = [value]

    normalized: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, ResearchEvidenceIntegrityRow):
            normalized.append(item.model_dump(mode="json"))
            continue
        if isinstance(item, dict):
            claim = str(item.get("claim") or item.get("fact") or item.get("statement") or "").strip()
            if not claim:
                continue
            normalized.append(
                {
                    "claim": claim,
                    "claim_key": str(item.get("claim_key") or "").strip(),
                    "status": _normalize_integrity_claim_status(item.get("status")),
                    "source_identity": _normalize_integrity_check_status(item.get("source_identity")),
                    "metric_identity": _normalize_integrity_check_status(item.get("metric_identity")),
                    "unit_scale_consistency": _normalize_integrity_check_status(
                        item.get("unit_scale_consistency") or item.get("unit_scale")
                    ),
                    "time_alignment": _normalize_integrity_check_status(item.get("time_alignment")),
                    "scope_alignment": _normalize_integrity_check_status(item.get("scope_alignment")),
                    "same_source_consistency": _normalize_integrity_check_status(
                        item.get("same_source_consistency")
                    ),
                    "source": str(item.get("source") or "").strip(),
                    "as_of": str(item.get("as_of") or "").strip(),
                    "rationale": str(item.get("rationale") or item.get("note") or "").strip(),
                    "entity_type": str(item.get("entity_type") or "").strip(),
                    "metric_kind": str(item.get("metric_kind") or "").strip(),
                    "series_kind": str(item.get("series_kind") or "").strip(),
                    "geography_or_scope": str(
                        item.get("geography_or_scope") or item.get("scope") or ""
                    ).strip(),
                    "comparison_basis": str(item.get("comparison_basis") or "").strip(),
                }
            )
            continue
        claim = str(item or "").strip()
        if not claim:
            continue
        normalized.append(
            {
                "claim": claim,
                "claim_key": "",
                "status": "pending",
                "source_identity": "missing",
                "metric_identity": "missing",
                "unit_scale_consistency": "missing",
                "time_alignment": "missing",
                "scope_alignment": "missing",
                "same_source_consistency": "missing",
                "source": "",
                "as_of": "",
                "rationale": "",
                "entity_type": "",
                "metric_kind": "",
                "series_kind": "",
                "geography_or_scope": "",
                "comparison_basis": "",
            }
        )
    return normalized


def _derive_evidence_integrity_rows(
    *,
    verification_facts: list[ResearchVerificationFact],
    audit_issues: list[ResearchAuditIssue],
) -> list[ResearchEvidenceIntegrityRow]:
    rows: list[ResearchEvidenceIntegrityRow] = []
    seen: set[str] = set()
    for fact in verification_facts:
        related_issues = _claim_related_audit_issues(fact=fact, audit_issues=audit_issues)
        note_text = _integrity_note_text(
            fact.fact,
            fact.note,
            *(issue.issue for issue in related_issues),
            *(issue.required_follow_up for issue in related_issues),
        )
        authority_issue = any(issue.kind == "authority" for issue in related_issues)
        method_issue = any(issue.kind == "method" for issue in related_issues)
        freshness_issue = any(issue.kind == "freshness" for issue in related_issues)
        scope_issue = any(issue.kind == "scope_fit" for issue in related_issues)
        conflict_or_method_issue = any(
            issue.kind in {"conflict", "method"} for issue in related_issues
        )
        row = ResearchEvidenceIntegrityRow(
            claim=fact.fact,
            claim_key=fact.claim_key,
            status=_derive_integrity_claim_status(
                fact=fact,
                related_issues=related_issues,
            ),
            source_identity=_derive_integrity_check_status(
                known=bool(str(fact.source or "").strip()),
                has_issue=authority_issue or int(fact.authority_rank or 0) < 40,
            ),
            metric_identity=_derive_integrity_check_status(
                known=bool(
                    str(fact.metric_kind or "").strip()
                    or str(fact.series_kind or "").strip()
                    or str(fact.comparison_basis or "").strip()
                ),
                has_issue=method_issue,
            ),
            unit_scale_consistency=_derive_integrity_check_status(
                known=_unit_or_scale_present(
                    fact.fact,
                    fact.note,
                    fact.metric_kind,
                    fact.series_kind,
                    fact.comparison_basis,
                ),
                has_issue=method_issue,
            ),
            time_alignment=_derive_integrity_check_status(
                known=bool(str(fact.as_of or "").strip()),
                has_issue=freshness_issue,
            ),
            scope_alignment=_derive_integrity_check_status(
                known=bool(str(fact.geography_or_scope or "").strip()),
                has_issue=scope_issue,
            ),
            same_source_consistency=_derive_integrity_check_status(
                known=bool(str(fact.source or "").strip()),
                has_issue=conflict_or_method_issue,
            ),
            source=fact.source,
            as_of=fact.as_of,
            rationale=_clean_text(
                fact.note
                or "; ".join(issue.issue for issue in related_issues if issue.issue)
            ),
            entity_type=fact.entity_type,
            metric_kind=fact.metric_kind,
            series_kind=fact.series_kind,
            geography_or_scope=fact.geography_or_scope,
            comparison_basis=fact.comparison_basis,
        )
        claim_key = str(row.claim_key or "").strip()
        if not claim_key or claim_key in seen:
            continue
        seen.add(claim_key)
        rows.append(row)

    for issue in audit_issues:
        claim = str(issue.affected_claim or "").strip()
        if not claim:
            continue
        if any(
            _claim_issue_overlap(
                claim_key=str(existing.claim_key or "").strip(),
                claim=existing.claim,
                issue_claim=claim,
                issue_text=issue.issue,
            )
            for existing in rows
        ):
            continue
        row = ResearchEvidenceIntegrityRow(
            claim=claim,
            status=(
                "conflicted"
                if issue.kind == "conflict"
                else "pending"
            ),
            source_identity="missing" if issue.kind == "authority" else "unclear",
            metric_identity="unclear" if issue.kind == "method" else "missing",
            unit_scale_consistency="unclear" if issue.kind == "method" else "missing",
            time_alignment="unclear" if issue.kind == "freshness" else "missing",
            scope_alignment="unclear" if issue.kind == "scope_fit" else "missing",
            same_source_consistency="unclear" if issue.kind in {"conflict", "method"} else "missing",
            rationale=_clean_text(issue.issue or issue.required_follow_up),
        )
        claim_key = str(row.claim_key or "").strip()
        if not claim_key or claim_key in seen:
            continue
        seen.add(claim_key)
        rows.append(row)

    return rows


class ResearchOrganismReport(BaseModel):
    """Compact report for one DAN Research run."""

    status: str
    trace_id: str
    organism_id: str
    organ_id: str
    task_id: str
    objective: str
    temporal_mode: Literal[
        "current",
        "historical_snapshot",
        "trend",
        "timeless",
    ] = "timeless"
    temporal_anchor: str = ""
    temporal_window: str = ""
    temporal_guidance: str = ""
    delivery_target: str = ""
    findings: list[str] = Field(default_factory=list)
    evidence_summary: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    verification_facts: list[ResearchVerificationFact] = Field(default_factory=list)
    evidence_integrity: list[ResearchEvidenceIntegrityRow] = Field(default_factory=list)
    audit_issues: list[ResearchAuditIssue] = Field(default_factory=list)
    quality_gates: list[ResearchQualityGate] = Field(default_factory=list)
    report_readiness: Literal["blocked", "provisional", "grounded", "actionable"] = (
        "grounded"
    )
    artifact_mode: Literal["final_report", "provisional_report", "blocker_report"] = (
        "final_report"
    )
    readiness_note: str = ""
    confidence: float | None = None
    recommended_change: str = ""
    selected_reader_count: int | None = None
    selected_reader_briefs: list[str] = Field(default_factory=list)
    depth_profile: str = "standard"
    max_tool_rounds: int | None = None
    max_tool_calls: int = 24
    outputs: dict[str, Any] = Field(default_factory=dict)
    handoff_count: int = 0
    signal_count: int = 0
    event_log_path: str | None = None
    event_log_schema: str | None = None
    markdown_report_path: str | None = None
    control_log_path: str | None = None
    control_log_schema: str | None = None
    error: str | None = None
    stage_records: list[dict[str, Any]] = Field(default_factory=list)
    trace_rows: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator(
        "findings",
        "evidence_summary",
        "evidence_refs",
        "contradictions",
        "open_questions",
        "selected_reader_briefs",
        mode="before",
    )
    @classmethod
    def _normalize_list_fields(cls, value: Any) -> list[str]:
        return _normalize_text_list(value)

    @field_validator("temporal_mode", mode="before")
    @classmethod
    def _normalize_temporal_mode_field(cls, value: Any) -> str:
        return _normalize_temporal_mode(value)

    @field_validator(
        "temporal_anchor",
        "temporal_window",
        "temporal_guidance",
        mode="before",
    )
    @classmethod
    def _normalize_temporal_text_fields(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence_field(cls, value: Any) -> float | None:
        return _normalize_confidence(value)

    @field_validator("verification_facts", mode="before")
    @classmethod
    def _normalize_verification_facts_field(
        cls,
        value: Any,
    ) -> list[dict[str, str]]:
        return _normalize_verification_facts(value)

    @field_validator("evidence_integrity", mode="before")
    @classmethod
    def _normalize_evidence_integrity_field(
        cls,
        value: Any,
    ) -> list[dict[str, str]]:
        return _normalize_evidence_integrity(value)

    @field_validator("audit_issues", mode="before")
    @classmethod
    def _normalize_audit_issues_field(
        cls,
        value: Any,
    ) -> list[dict[str, str]]:
        return _normalize_audit_issues(value)

    @field_validator("quality_gates", mode="before")
    @classmethod
    def _normalize_quality_gates_field(
        cls,
        value: Any,
    ) -> list[dict[str, str]]:
        return _normalize_quality_gates(value)

    @field_validator("report_readiness", mode="before")
    @classmethod
    def _normalize_report_readiness_field(cls, value: Any) -> str:
        return _normalize_report_readiness(value)

    @field_validator("artifact_mode", mode="before")
    @classmethod
    def _normalize_artifact_mode_field(cls, value: Any) -> str:
        return _normalize_artifact_mode(value)

    @field_validator("readiness_note", mode="before")
    @classmethod
    def _normalize_readiness_note_field(cls, value: Any) -> str:
        return str(value or "").strip()

    @model_validator(mode="after")
    def _derive_artifact_mode(self) -> "ResearchOrganismReport":
        inferred = _infer_artifact_mode(
            status=self.status,
            report_readiness=self.report_readiness,
        )
        if self.artifact_mode != inferred and (
            not str(self.artifact_mode or "").strip()
            or inferred != "final_report"
        ):
            self.artifact_mode = inferred
        if not self.evidence_integrity:
            self.evidence_integrity = _derive_evidence_integrity_rows(
                verification_facts=list(self.verification_facts),
                audit_issues=list(self.audit_issues),
            )
        return self

    def has_material_output(self) -> bool:
        return bool(
            self.findings
            or self.evidence_refs
            or self.verification_facts
            or self.evidence_integrity
            or self.audit_issues
            or self.quality_gates
            or " ".join(self.recommended_change.split())
        )

    def is_failed_no_output(self) -> bool:
        return self.status.strip().lower() != "completed" and not self.has_material_output()


class ResearchConversationEntry(BaseModel):
    """One durable user/assistant exchange in the DAN Research shell."""

    role: str
    text: str
    kind: str = "message"
    created_at: str = Field(default_factory=_utcnow_iso)


class ResearchCliSession(BaseModel):
    """Persistent session state for the product shell."""

    session_format_version: int = Field(default=RESEARCH_PRODUCT_SESSION_FORMAT_VERSION)
    runtime_build_id: str = Field(default=RESEARCH_PRODUCT_RUNTIME_BUILD_ID)
    session_id: str = Field(default_factory=lambda: f"research-session-{uuid4().hex[:8]}")
    workspace_root: str = ""
    created_at: str = Field(default_factory=_utcnow_iso)
    updated_at: str = Field(default_factory=_utcnow_iso)
    turns: list[ResearchOrganismReport] = Field(default_factory=list)
    claim_ledger: list[ResearchVerificationFact] = Field(default_factory=list)
    conversation: list[ResearchConversationEntry] = Field(default_factory=list)
    pending_clarification: str | None = None
    orchestrator_state: dict[str, Any] = Field(default_factory=dict)

    def next_turn_number(self) -> int:
        return len(self.turns) + 1

    def task_id_for(self, base_task_id: str) -> str:
        return f"{base_task_id}:{self.next_turn_number()}"

    def context_reports(self, *, limit: int = 4) -> list[ResearchOrganismReport]:
        selected: list[ResearchOrganismReport] = []
        for report in reversed(self.turns):
            if report.is_failed_no_output():
                continue
            selected.append(report)
            if len(selected) >= limit:
                break
        return list(reversed(selected))

    def carry_forward_findings(self, *, limit: int = 3) -> list[str]:
        findings: list[str] = []
        for report in self.context_reports(limit=limit):
            findings_summary = "; ".join(report.findings[:2]) or "(none)"
            findings.append(
                "Previous research turn: "
                f"objective={report.objective}; "
                f"status={report.status}; "
                f"confidence={report.confidence if report.confidence is not None else '(unset)'}; "
                f"findings={findings_summary}"
            )
        return findings

    def refresh_for_current_runtime(self) -> bool:
        if (
            self.session_format_version == RESEARCH_PRODUCT_SESSION_FORMAT_VERSION
            and self.runtime_build_id == RESEARCH_PRODUCT_RUNTIME_BUILD_ID
        ):
            return False
        self.session_format_version = RESEARCH_PRODUCT_SESSION_FORMAT_VERSION
        self.runtime_build_id = RESEARCH_PRODUCT_RUNTIME_BUILD_ID
        self.conversation = []
        self.pending_clarification = None
        self.orchestrator_state = {}
        self.updated_at = _utcnow_iso()
        return True

    def record_turn(self, report: ResearchOrganismReport) -> None:
        self.turns.append(report)
        claim_ledger_by_key = {
            str(item.claim_key or "").strip(): item
            for item in self.claim_ledger
            if str(item.claim_key or "").strip()
        }
        for fact in list(report.verification_facts or []):
            if not isinstance(fact, ResearchVerificationFact):
                try:
                    fact = ResearchVerificationFact.model_validate(fact)
                except ValidationError:
                    continue
            if not _verification_fact_is_frozen(
                status=fact.status,
                note=fact.note,
                source=fact.source,
                fact=fact.fact,
            ):
                continue
            claim_key = str(fact.claim_key or "").strip()
            if not claim_key:
                continue
            existing = claim_ledger_by_key.get(claim_key)
            if existing is None:
                self.claim_ledger.append(fact)
                claim_ledger_by_key[claim_key] = fact
                continue
            if _claim_record_priority(fact) > _claim_record_priority(existing):
                replacement = fact.model_copy(deep=True)
                index = self.claim_ledger.index(existing)
                self.claim_ledger[index] = replacement
                claim_ledger_by_key[claim_key] = replacement
        self.updated_at = _utcnow_iso()

    def record_message(self, *, role: str, text: str, kind: str = "message") -> None:
        cleaned = str(text or "").strip()
        if not cleaned:
            return
        self.conversation.append(
            ResearchConversationEntry(
                role=str(role or "").strip() or "assistant",
                text=cleaned,
                kind=str(kind or "").strip() or "message",
            )
        )
        self.updated_at = _utcnow_iso()


class ResearchProductConfig(BaseModel):
    """Workspace-local DAN Research defaults."""

    product_name: str = RESEARCH_PRODUCT_NAME
    workspace_root: str
    default_model: str | None = None
    thinking_mode: str = "auto"
    default_tool_ids: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    delivery_target: str = "research memo"
    depth_profile: str = "standard"
    research_reader_count: int | None = None
    max_supervision_loops: int | None = None
    max_tool_rounds: int | None = 8
    max_tool_calls: int = 24


class ResearchProductPaths(BaseModel):
    """Filesystem locations for the DAN Research product shell."""

    root: str
    config: str
    session: str
    transcript: str
    control_log: str
    runs_dir: str


def resolve_research_product_paths(
    workspace_root: str | Path,
    *,
    session_file: str | None = None,
) -> ResearchProductPaths:
    base_dir = Path(workspace_root).expanduser().resolve()
    root = base_dir / RESEARCH_PRODUCT_DIRNAME
    session_path = (
        _resolve_path(session_file, base_dir=base_dir)
        if session_file
        else root / "session.json"
    )
    return ResearchProductPaths(
        root=str(root),
        config=str(root / "config.json"),
        session=str(session_path),
        transcript=str(root / "transcript.jsonl"),
        control_log=str(root / "control-plane-events.jsonl"),
        runs_dir=str(root / "runs"),
    )


def load_research_product_config(
    paths: ResearchProductPaths,
) -> ResearchProductConfig | None:
    config_path = Path(paths.config)
    if not config_path.exists():
        return None
    raw = config_path.read_text(encoding="utf-8")
    if not raw.strip():
        return None
    try:
        return ResearchProductConfig.model_validate_json(raw)
    except ValidationError:
        return None


def write_research_product_config(
    paths: ResearchProductPaths,
    config: ResearchProductConfig,
) -> None:
    root = Path(paths.root)
    root.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(Path(paths.config), config.model_dump_json(indent=2))


def load_research_product_session(
    paths: ResearchProductPaths,
) -> ResearchCliSession | None:
    session_path = Path(paths.session)
    if not session_path.exists():
        return None
    raw = session_path.read_text(encoding="utf-8")
    if not raw.strip():
        return None
    try:
        return ResearchCliSession.model_validate_json(raw)
    except ValidationError:
        return None


def save_research_product_session(
    paths: ResearchProductPaths,
    session: ResearchCliSession,
) -> None:
    Path(paths.root).mkdir(parents=True, exist_ok=True)
    session.session_format_version = RESEARCH_PRODUCT_SESSION_FORMAT_VERSION
    session.runtime_build_id = RESEARCH_PRODUCT_RUNTIME_BUILD_ID
    payload = session.model_dump(
        mode="json",
        exclude={
            "turns": {
                "__all__": {
                    "outputs",
                    "stage_records",
                    "trace_rows",
                }
            }
        },
    )
    _atomic_write_text(Path(paths.session), json.dumps(payload, indent=2))


def append_research_product_transcript(
    paths: ResearchProductPaths,
    *,
    session: ResearchCliSession,
    report: ResearchOrganismReport,
) -> None:
    transcript_path = Path(paths.transcript)
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": session.updated_at,
        "session_id": session.session_id,
        "turn_number": len(session.turns),
        "trace_id": report.trace_id,
        "task_id": report.task_id,
        "objective": report.objective,
        "delivery_target": report.delivery_target,
        "status": report.status,
        "artifact_mode": report.artifact_mode,
        "report_readiness": report.report_readiness,
        "confidence": report.confidence,
        "selected_reader_count": report.selected_reader_count,
        "depth_profile": report.depth_profile,
        "event_log_path": report.event_log_path,
        "event_log_schema": report.event_log_schema,
        "markdown_report_path": report.markdown_report_path,
        "control_log_path": report.control_log_path or paths.control_log,
        "control_log_schema": report.control_log_schema,
    }
    with transcript_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


__all__ = [
    "RESEARCH_PRODUCT_DIRNAME",
    "RESEARCH_PRODUCT_NAME",
    "RESEARCH_PRODUCT_RUNTIME_BUILD_ID",
    "RESEARCH_PRODUCT_SESSION_FORMAT_VERSION",
    "ResearchCliSession",
    "ResearchConversationEntry",
    "ResearchOrganismReport",
    "ResearchVerificationFact",
    "RESEARCH_ARTIFACT_MODES",
    "ResearchProductConfig",
    "ResearchProductPaths",
    "append_research_product_transcript",
    "load_research_product_config",
    "load_research_product_session",
    "resolve_research_product_paths",
    "save_research_product_session",
    "write_research_product_config",
]
