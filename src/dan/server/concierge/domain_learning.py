"""Domain learning — detect domains, extract knowledge, validate content.

Provides keyword-based domain detection, template management for domain-specific
knowledge extraction, LLM-powered reflection on completed tasks, and simple
rule-based content validation against accumulated domain knowledge.

Part of Phase 21 (plan 31-21).
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from dan.domain_taxonomy import (
    normalize_domain_keyword_map,
    normalize_domain_name,
)
from dan.keyword_overlap import query_keyword_overlap
from dan.engine.memory_kernel import (
    MemoryItem,
    MemoryLifecycle,
    MemoryScope,
    MemoryType,
)

from .feature_gates import engine_feature_enabled

if TYPE_CHECKING:
    from dan.providers.base import LLMProvider
    from dan.server.concierge.models import Project, TaskTurn

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Domain keyword maps
# ---------------------------------------------------------------------------

_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "paper_rendering": [
        "latex", "paper", "manuscript", "journal", "tex", "bibliography",
        "bibtex", "figure", "table", "abstract", "appendix",
    ],
    "equity_research": [
        "equity", "stock", "portfolio", "factor", "alpha", "crsp", "wrds",
        "returns", "market cap", "hedge",
    ],
    "data_analysis": [
        "dataset", "dataframe", "csv", "merge", "join", "pandas",
        "cleaning", "transform", "aggregate", "pivot",
    ],
    "literature_review": [
        "literature", "review", "survey", "systematic", "meta-analysis",
        "citation", "bibliography",
    ],
    "code_generation": [
        "code", "function", "class", "module", "refactor", "debug",
        "test", "implement", "api",
    ],
    "workflow_building": [
        "workflow", "pipeline", "automate", "schedule", "node", "edge",
        "graph", "dag",
    ],
    "supply_chain_management": [
        "supply chain", "supply chain logistics", "logistics", "procurement",
        "inventory optimization", "inventory management", "warehouse",
        "fulfillment", "demand planning", "s&op",
    ],
    "operations_management": [
        "operations management", "ops management", "process improvement",
        "capacity planning", "throughput", "lean", "six sigma",
        "production planning", "service operations",
    ],
    "operations_research": [
        "operations research", "linear programming",
        "integer programming", "stochastic optimization", "queueing",
        "simulation", "scheduling problem",
    ],
    "inventory_optimization": [
        "inventory optimization", "inventory policy", "safety stock",
        "reorder point", "stockout", "multi echelon", "forecast accuracy",
    ],
    "marketing_analytics": [
        "marketing analytics", "attribution", "customer segmentation",
        "conversion funnel", "campaign performance", "ltv", "cac",
    ],
    "product_management": [
        "product management", "product strategy", "roadmap", "feature prioritization",
        "user story", "product requirement", "product launch",
    ],
    "financial_modeling": [
        "financial modeling", "financial modelling", "discounted cash flow",
        "dcf", "valuation model", "forecast model", "scenario analysis",
    ],
    "healthcare_informatics": [
        "healthcare informatics", "health informatics", "ehr", "electronic health record",
        "clinical workflow", "patient outcomes", "medical coding",
    ],
    "machine_learning": [
        "machine learning", "ml", "feature engineering", "model training",
        "hyperparameter tuning", "supervised learning", "unsupervised learning",
    ],
    "natural_language_processing": [
        "natural language processing", "nlp", "language model",
        "text classification", "named entity recognition", "tokenization",
        "sentiment analysis",
    ],
    "computer_vision": [
        "computer vision", "cv", "image classification", "object detection",
        "segmentation", "vision transformer",
    ],
    "reinforcement_learning": [
        "reinforcement learning", "rl", "policy gradient", "q learning",
        "reward function", "agent training",
    ],
    "causal_inference": [
        "causal inference", "treatment effect", "difference in differences",
        "instrumental variables", "regression discontinuity",
    ],
    "econometrics": [
        "econometrics", "panel regression", "fixed effects", "standard errors",
        "identification strategy", "endogeneity",
    ],
}


def get_domain_keyword_map(behavior_store: Any = None) -> dict[str, list[str]]:
    """Return the shared domain keyword map, honoring BehaviorStore overrides."""
    if behavior_store is not None:
        stored = normalize_domain_keyword_map(
            behavior_store.get("domains/keyword_maps")
        )
        if stored:
            return stored
    return normalize_domain_keyword_map(_DOMAIN_KEYWORDS)


def register_seed_domains(store: Any) -> None:
    """Register domain keyword maps as seed defaults."""
    store.register_seed("domains/keyword_maps", get_domain_keyword_map())


def detect_domain(
    message: str,
    project: Project | None = None,
    behavior_store: Any = None,
    pattern_accumulator: Any = None,
) -> str | None:
    """Keyword + project-tag based domain classifier."""
    if project is not None:
        proj_domain = getattr(project, "domain", None)
        if proj_domain:
            return normalize_domain_name(str(proj_domain)) or str(proj_domain)

    kw_map = get_domain_keyword_map(behavior_store)

    msg_lower = message.lower()
    scores: dict[str, int] = {}
    for domain, kws in kw_map.items():
        count = sum(1 for kw in kws if kw in msg_lower)
        if count > 0:
            scores[domain] = count

    if not scores:
        if pattern_accumulator is not None:
            msg_kws = [w for w in message.lower().split() if len(w) > 3][:10]
            pattern_accumulator.record_unrecognized(message[:200], msg_kws, "domain")
        return None
    return max(scores, key=scores.__getitem__)


# ---------------------------------------------------------------------------
# Domain template model & I/O
# ---------------------------------------------------------------------------

_USER_TEMPLATE_DIR = Path("~/.dan/domain_templates").expanduser()
_SEED_TEMPLATE_DIR = Path(__file__).parent.parent.parent / "data" / "domain_templates"


class DomainTemplate(BaseModel):
    domain: str
    categories: list[str]
    extraction_prompts: dict[str, str]
    checklist: list[str]
    version: int = 1
    created_at: float = Field(default_factory=time.time)
    item_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


def load_domain_template(domain: str) -> DomainTemplate | None:
    """Load template from user dir, falling back to package seed templates."""
    canonical_domain = normalize_domain_name(domain) or domain
    raw_domain = str(domain or "").strip()
    user_paths = [_USER_TEMPLATE_DIR / f"{canonical_domain}.json"]
    if raw_domain and raw_domain != canonical_domain:
        user_paths.append(_USER_TEMPLATE_DIR / f"{raw_domain}.json")
    for user_path in user_paths:
        if not user_path.exists():
            continue
        try:
            data = json.loads(user_path.read_text(encoding="utf-8"))
            template = DomainTemplate.model_validate(data)
            if normalize_domain_name(template.domain) != template.domain:
                return template.model_copy(
                    update={"domain": normalize_domain_name(template.domain) or template.domain}
                )
            return template
        except Exception:
            logger.debug("Failed to load user template %s", user_path, exc_info=True)

    seed_paths = [_SEED_TEMPLATE_DIR / f"{canonical_domain}.json"]
    if raw_domain and raw_domain != canonical_domain:
        seed_paths.append(_SEED_TEMPLATE_DIR / f"{raw_domain}.json")
    for seed_path in seed_paths:
        if not seed_path.exists():
            continue
        try:
            data = json.loads(seed_path.read_text(encoding="utf-8"))
            template = DomainTemplate.model_validate(data)
            if normalize_domain_name(template.domain) != template.domain:
                return template.model_copy(
                    update={"domain": normalize_domain_name(template.domain) or template.domain}
                )
            return template
        except Exception:
            logger.debug("Failed to load seed template %s", seed_path, exc_info=True)

    return None


def save_domain_template(template: DomainTemplate) -> None:
    """Save template to user dir, backing up the previous version.

    Best-effort: logs a warning and returns silently if ``~/.dan`` is
    unwritable (e.g. containers, read-only home directories).
    """
    raw_domain = str(template.domain or "").strip()
    canonical_domain = normalize_domain_name(template.domain) or template.domain
    if canonical_domain != template.domain:
        template = template.model_copy(update={"domain": canonical_domain})
    try:
        _USER_TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.warning(
            "Cannot create domain template directory %s — template save skipped",
            _USER_TEMPLATE_DIR,
        )
        return
    path = _USER_TEMPLATE_DIR / f"{template.domain}.json"
    legacy_path = _USER_TEMPLATE_DIR / f"{raw_domain}.json" if raw_domain else path

    existing_path = path if path.exists() else legacy_path
    if existing_path.exists():
        try:
            old_data = json.loads(existing_path.read_text(encoding="utf-8"))
            old_version = old_data.get("version", 1)
            backup = _USER_TEMPLATE_DIR / f"{template.domain}.v{old_version}.json"
            existing_path.rename(backup)
        except Exception:
            logger.debug("Failed to backup old template", exc_info=True)

    try:
        path.write_text(
            json.dumps(template.model_dump(mode="json"), indent=2, default=str),
            encoding="utf-8",
        )
    except OSError:
        logger.warning(
            "Cannot write domain template to %s — save skipped", path,
        )


_DEFAULT_CATEGORIES = [
    "structure", "formatting", "tooling", "pitfall", "best_practice",
]


def create_generic_template(domain: str) -> DomainTemplate:
    return DomainTemplate(
        domain=normalize_domain_name(domain) or domain,
        categories=list(_DEFAULT_CATEGORIES),
        extraction_prompts={},
        checklist=[],
    )


def get_or_create_template(domain: str) -> DomainTemplate:
    canonical_domain = normalize_domain_name(domain) or domain
    existing = load_domain_template(canonical_domain)
    if existing is not None:
        return existing
    return create_generic_template(canonical_domain)


# ---------------------------------------------------------------------------
# Domain reflector — LLM-powered knowledge extraction
# ---------------------------------------------------------------------------

_CATEGORY_TO_TYPE: dict[str, MemoryType] = {
    "structure": MemoryType.FACT,
    "formatting": MemoryType.FACT,
    "tooling": MemoryType.PREFERENCE,
    "pitfall": MemoryType.PRINCIPLE,
    "best_practice": MemoryType.PRINCIPLE,
}

_CATEGORY_ALIASES: dict[str, str] = {
    "section_structure": "structure",
    "analysis_structure": "structure",
    "report_layout": "structure",
    "synthesis_structure": "structure",
    "thematic_grouping": "structure",
    "table_formatting": "formatting",
    "figure_placement": "formatting",
    "bibliography": "formatting",
    "math_typesetting": "formatting",
    "visualization": "formatting",
    "data_sources": "tooling",
    "data_loading": "tooling",
    "cleaning_patterns": "tooling",
    "join_strategies": "tooling",
    "search_strategy": "tooling",
    "source_types": "tooling",
    "journal_style": "best_practice",
    "factor_construction": "best_practice",
    "citation_density": "best_practice",
}


def _normalize_domain_category(category: str) -> str:
    normalized = (category or "").strip().lower()
    return _CATEGORY_ALIASES.get(normalized, normalized)


class DomainReflector:
    def __init__(
        self,
        memory_kernel: Any | None = None,
        llm: Any | None = None,
        feature_enabled: Callable[[str], bool] | None = None,
    ) -> None:
        self._kernel = memory_kernel
        self._llm: LLMProvider | None = llm  # type: ignore[assignment]
        self._feature_enabled = feature_enabled or engine_feature_enabled

    def reflect(
        self,
        domain: str,
        turns: list[TaskTurn],
        outcome: str = "completed",
    ) -> list[MemoryItem]:
        """Extract domain knowledge items from completed task turns via LLM."""
        if not self._feature_enabled("domain_learning"):
            return []
        if self._llm is None:
            return []

        canonical_domain = normalize_domain_name(domain) or domain
        template = get_or_create_template(canonical_domain)
        prompt = self._build_reflection_prompt(canonical_domain, turns, template)
        try:
            response = self._llm.complete(prompt, max_tokens=1000)
            return self._parse_reflection_response(response, canonical_domain)
        except Exception:
            logger.debug("Domain reflection failed", exc_info=True)
            return []

    def _build_reflection_prompt(
        self,
        domain: str,
        turns: list[TaskTurn],
        template: DomainTemplate,
    ) -> str:
        turn_text = "\n".join(
            f"[{t.role}] {t.content[:500]}" for t in turns[-10:]
        )
        checklist_text = (
            "\n".join(f"- {c}" for c in template.checklist)
            if template.checklist
            else "(none yet)"
        )
        return (
            f"You are a domain-knowledge extractor for the '{domain}' domain.\n"
            f"Categories: {', '.join(template.categories)}\n"
            f"Existing checklist:\n{checklist_text}\n\n"
            f"Task conversation:\n{turn_text}\n\n"
            "Extract domain-specific lessons learned. Return a JSON array:\n"
            '[{"category": "...", "content": "...", "importance": 0.5-0.9}]\n'
            "Only include genuinely useful, non-obvious insights."
        )

    def _parse_reflection_response(
        self, response: str, domain: str
    ) -> list[MemoryItem]:
        try:
            match = re.search(r"\[[\s\S]*?\]", response, re.DOTALL)
            if not match:
                return []
            items_raw = json.loads(match.group())
        except (json.JSONDecodeError, AttributeError):
            return []

        results: list[MemoryItem] = []
        for entry in items_raw:
            if not isinstance(entry, dict):
                continue
            category = entry.get("category", "")
            normalized_category = _normalize_domain_category(category)
            content = entry.get("content", "")
            importance = entry.get("importance", 0.5)
            if not content:
                continue
            importance = max(0.5, min(float(importance), 0.9))
            memory_type = _CATEGORY_TO_TYPE.get(normalized_category, MemoryType.FACT)
            metadata = {"domain": domain, "category": normalized_category}
            if normalized_category != category:
                metadata["source_category"] = category
            results.append(
                MemoryItem(
                    content=content,
                    memory_type=memory_type,
                    scope=MemoryScope.USER,
                    importance=importance,
                    tags=["domain_knowledge"],
                    metadata=metadata,
                )
            )
        return results


# ---------------------------------------------------------------------------
# Domain validator — keyword-level rule checking
# ---------------------------------------------------------------------------

_MODAL_RE = re.compile(r"\b(always|never|must|should)\b", re.IGNORECASE)


class DomainValidator:
    def validate(
        self, content: str, domain_items: list[MemoryItem]
    ) -> list[str]:
        """Check content against domain rules. Returns warning strings."""
        warnings: list[str] = []
        rule_items = [
            it for it in domain_items
            if _normalize_domain_category(it.metadata.get("category", "")) in ("pitfall", "best_practice")
        ]
        content_lower = content.lower()
        for item in rule_items:
            match = _MODAL_RE.search(item.content)
            if not match:
                continue
            modal = match.group(1).lower()
            after_modal = item.content[match.end():].strip()
            key_terms = after_modal.split()[:4]
            if not key_terms:
                continue
            key_phrase = " ".join(key_terms).lower()

            if modal in ("always", "must"):
                trigger_words = [w for w in key_terms if len(w) > 3]
                if trigger_words and not any(w.lower() in content_lower for w in trigger_words):
                    warnings.append(
                        f"Rule suggests: '{item.content[:80]}' — "
                        f"key term '{key_phrase}' not found in content"
                    )
            elif modal == "never":
                forbidden_words = [w for w in key_terms if len(w) > 3]
                if forbidden_words and any(w.lower() in content_lower for w in forbidden_words):
                    warnings.append(
                        f"Rule warns against: '{item.content[:80]}' — "
                        f"forbidden term found in content"
                    )
        return warnings


# ---------------------------------------------------------------------------
# Template consolidator
# ---------------------------------------------------------------------------


def _domain_scope_bucket(item: MemoryItem) -> str:
    if item.scope == MemoryScope.PROJECT:
        return f"project:{item.metadata.get('project_id') or 'unknown'}"
    return item.scope.value


class DomainTemplateConsolidator:
    def consolidate(
        self,
        domain: str,
        items: list[MemoryItem],
        template: DomainTemplate,
    ) -> DomainTemplate | None:
        """Consolidate domain items into template. Returns updated or None."""
        by_category: dict[str, list[MemoryItem]] = defaultdict(list)
        for item in items:
            cat = item.metadata.get("category", "uncategorized")
            by_category[cat].append(item)

        changed = False
        duplicate_ids: list[str] = []
        deduped_by_category: dict[str, list[MemoryItem]] = {}

        for cat, cat_items in by_category.items():
            ordered_items = sorted(
                cat_items,
                key=lambda item: (
                    item.importance,
                    getattr(item, "updated_at", 0.0),
                    getattr(item, "created_at", 0.0),
                ),
                reverse=True,
            )
            unique_items: list[MemoryItem] = []
            for item in ordered_items:
                item_bucket = _domain_scope_bucket(item)
                if any(
                    _domain_scope_bucket(keep) == item_bucket
                    and query_keyword_overlap(item.content, keep.content) > 0.8
                    for keep in unique_items
                ):
                    duplicate_ids.append(item.id)
                    continue
                unique_items.append(item)
            deduped_by_category[cat] = unique_items

        new_cats = [c for c in deduped_by_category if c not in template.categories]
        if new_cats:
            template.categories.extend(new_cats)
            changed = True

        coverage_gaps = [
            c for c in template.categories if len(deduped_by_category.get(c, [])) < 2
        ]
        new_metadata = dict(template.metadata or {})
        if coverage_gaps:
            new_metadata["coverage_gaps"] = coverage_gaps
        else:
            new_metadata.pop("coverage_gaps", None)
        if duplicate_ids:
            new_metadata["merged_duplicate_ids"] = duplicate_ids
        else:
            new_metadata.pop("merged_duplicate_ids", None)
        if new_metadata != template.metadata:
            template.metadata = new_metadata
            changed = True

        if duplicate_ids:
            logger.debug(
                "Merged %d near-duplicate domain items for %s",
                len(duplicate_ids),
                domain,
            )

        new_count = sum(len(cat_items) for cat_items in deduped_by_category.values())
        if new_count != template.item_count:
            template.item_count = new_count
            changed = True

        if changed:
            template.version += 1
            return template
        return None


# ---------------------------------------------------------------------------
# LLM-assisted template upgrade (≥20 items, tier 2)
# ---------------------------------------------------------------------------


class DomainTemplateUpgrader:
    """Upgrade domain templates using LLM to discover missing categories and checklist gaps.

    Gated behind ``domain_template_upgrade`` (tier 2). Accepts an ``llm``
    parameter with a ``.complete(prompt, max_tokens)`` method, following the
    same pattern as :class:`DomainReflector`.
    """

    def __init__(self, llm: Any | None = None) -> None:
        self._llm: LLMProvider | None = llm  # type: ignore[assignment]

    def upgrade(
        self,
        domain: str,
        template: DomainTemplate,
        items: list[MemoryItem],
    ) -> DomainTemplate | None:
        """Call LLM to discover missing categories and checklist questions.

        Returns the updated template, or None if no changes were made or the
        LLM call fails.
        """
        if self._llm is None:
            return None

        prompt = self._build_upgrade_prompt(domain, template, items)
        try:
            response = self._llm.complete(prompt, max_tokens=1200)
            return self._apply_upgrade(response, template)
        except Exception:
            logger.debug("LLM template upgrade failed for %s", domain, exc_info=True)
            return None

    def _build_upgrade_prompt(
        self,
        domain: str,
        template: DomainTemplate,
        items: list[MemoryItem],
    ) -> str:
        item_summaries = "\n".join(
            f"- [{it.metadata.get('category', '?')}] {it.content[:200]}"
            for it in items[:40]
        )
        existing_cats = ", ".join(template.categories) if template.categories else "(none)"
        existing_checklist = (
            "\n".join(f"- {q}" for q in template.checklist)
            if template.checklist
            else "(none yet)"
        )
        return (
            f"Given these {len(items)} domain knowledge items for '{domain}', "
            f"analyze gaps in our knowledge template.\n\n"
            f"Current categories: {existing_cats}\n"
            f"Current checklist:\n{existing_checklist}\n\n"
            f"Knowledge items:\n{item_summaries}\n\n"
            f"What categories of knowledge am I missing? "
            f"What additional checklist questions should I ask after "
            f"completing a task in this domain?\n\n"
            f"Return a JSON object with two keys:\n"
            f'{{"new_categories": ["cat1", "cat2"], '
            f'"new_checklist": ["question1?", "question2?"]}}\n'
            f"Only include genuinely useful additions not already covered."
        )

    def _apply_upgrade(
        self, response: str, template: DomainTemplate
    ) -> DomainTemplate | None:
        try:
            match = re.search(r"\{[\s\S]*?\}", response, re.DOTALL)
            if not match:
                return None
            parsed = json.loads(match.group())
        except (json.JSONDecodeError, AttributeError):
            return None

        new_cats = parsed.get("new_categories", [])
        new_checklist = parsed.get("new_checklist", [])
        if not isinstance(new_cats, list):
            new_cats = []
        if not isinstance(new_checklist, list):
            new_checklist = []

        existing_cats_lower = {c.lower() for c in template.categories}
        added_cats = [
            c for c in new_cats
            if isinstance(c, str) and c.strip() and c.lower() not in existing_cats_lower
        ]

        existing_checklist_lower = {q.lower() for q in template.checklist}
        added_checklist = [
            q for q in new_checklist
            if isinstance(q, str) and q.strip() and q.lower() not in existing_checklist_lower
        ]

        if not added_cats and not added_checklist:
            return None

        template.categories.extend(added_cats)
        template.checklist.extend(added_checklist)
        template.version += 1
        template.metadata["last_llm_upgrade_item_count"] = template.item_count
        return template


# ---------------------------------------------------------------------------
# Pattern generalizer
# ---------------------------------------------------------------------------

class DomainPatternGeneralizer:
    def generalize(
        self, domain: str, items: list[MemoryItem]
    ) -> list[MemoryItem]:
        """Extract generalized patterns from items spanning multiple tasks."""
        by_category: dict[tuple[str, str], list[MemoryItem]] = defaultdict(list)
        for item in items:
            cat = item.metadata.get("category", "uncategorized")
            by_category[(cat, _domain_scope_bucket(item))].append(item)

        patterns: list[MemoryItem] = []
        for (cat, _scope_bucket), cat_items in by_category.items():
            if len(cat_items) < 3:
                continue
            clusters: list[list[MemoryItem]] = []
            assigned: set[str] = set()
            for i, anchor in enumerate(cat_items):
                if anchor.id in assigned:
                    continue
                cluster = [anchor]
                assigned.add(anchor.id)
                for other in cat_items[i + 1:]:
                    if other.id in assigned:
                        continue
                    if query_keyword_overlap(anchor.content, other.content) > 0.7:
                        cluster.append(other)
                        assigned.add(other.id)
                if len(cluster) >= 3:
                    clusters.append(cluster)

            for cluster in clusters:
                representative = max(cluster, key=lambda it: it.importance)
                metadata = {
                    "domain": domain,
                    "category": cat,
                    "source_count": len(cluster),
                }
                if representative.scope == MemoryScope.PROJECT:
                    metadata["project_id"] = representative.metadata.get("project_id")
                patterns.append(
                    MemoryItem(
                        content=representative.content,
                        memory_type=MemoryType.WORKFLOW_PATTERN,
                        scope=representative.scope,
                        lifecycle=MemoryLifecycle.DURABLE,
                        importance=0.9,
                        tags=["domain_knowledge", "generalized_pattern"],
                        metadata=metadata,
                    )
                )
        return patterns


# ---------------------------------------------------------------------------
# Context package
# ---------------------------------------------------------------------------

class ContextPackage(BaseModel):
    domain: str | None = None
    domain_expertise: str = ""
    project_summary: str = ""
    recent_artifacts: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_references: list[str] = Field(default_factory=list)
    task_state: dict[str, Any] = Field(default_factory=dict)
    memory_context: str = ""
    auto_read_content: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Artifact reference resolution
# ---------------------------------------------------------------------------

_IMPLICIT_REF_RE = re.compile(
    r"\b(the|that|this)\s+"
    r"(paper|report|file|dataset|data|table|figure|document|spreadsheet|notebook)\b",
    re.IGNORECASE,
)

_ARTIFACT_TYPE_EXTENSIONS: dict[str, set[str]] = {
    "paper": {".pdf", ".tex", ".md", ".doc", ".docx", ".txt"},
    "report": {".pdf", ".tex", ".md", ".doc", ".docx", ".txt"},
    "document": {".pdf", ".tex", ".md", ".doc", ".docx", ".txt"},
    "dataset": {".csv", ".tsv", ".xlsx", ".xls", ".parquet", ".json"},
    "data": {".csv", ".tsv", ".xlsx", ".xls", ".parquet", ".json"},
    "spreadsheet": {".csv", ".tsv", ".xlsx", ".xls"},
    "table": {".csv", ".tsv", ".xlsx", ".xls"},
    "figure": {".png", ".jpg", ".jpeg", ".svg", ".pdf"},
    "notebook": {".ipynb"},
}
_PATH_LIKE_RE = re.compile(r"(?:~/|/|\./)[\w./\\-]+\.\w{1,8}")


def _recent_turn_path_matches_ref(ref_label: str, turn_content: str, path_str: str) -> bool:
    """Only use recent-turn paths when they plausibly match the requested artifact."""
    lower = turn_content.lower()
    if ref_label in lower:
        return True
    suffix = Path(path_str).suffix.lower()
    allowed = _ARTIFACT_TYPE_EXTENSIONS.get(ref_label)
    if not allowed:
        return False
    return suffix in allowed


def _fact_has_artifact_marker(fact: MemoryItem) -> bool:
    tags = set(fact.tags or [])
    meta = fact.metadata or {}
    return (
        "file_location" in tags
        or "data_format" in tags
        or "file_location" in meta
        or "data_format" in meta
    )


def _fact_artifact_path(fact: MemoryItem) -> str | None:
    meta = fact.metadata or {}
    path_str = meta.get("file_location")
    if isinstance(path_str, str) and path_str.strip():
        return path_str.strip()
    match = _PATH_LIKE_RE.search(fact.content)
    if match:
        return match.group()
    return None


def resolve_artifact_references(
    message: str,
    project_facts: list[MemoryItem],
    task_artifacts: dict[str, str],
    recent_turns: list[TaskTurn],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Resolve implicit artifact references in a message."""
    matches = _IMPLICIT_REF_RE.findall(message)
    if not matches:
        return [], []

    matched_artifacts: list[dict[str, Any]] = []
    unresolved: list[str] = []

    for _determiner, ref_type in matches:
        ref_label = ref_type.lower()
        resolved = False

        for fact in project_facts:
            meta = fact.metadata
            if _fact_has_artifact_marker(fact):
                if ref_label in fact.content.lower():
                    matched_artifacts.append({
                        "type": ref_label,
                        "source": "project_fact",
                        "content": fact.content,
                        "metadata": meta,
                        "path": _fact_artifact_path(fact),
                    })
                    resolved = True
                    break

        if not resolved:
            for name, path in task_artifacts.items():
                if ref_label in name.lower():
                    matched_artifacts.append({
                        "type": ref_label,
                        "source": "task_artifact",
                        "name": name,
                        "path": path,
                    })
                    resolved = True
                    break

        if not resolved:
            for turn in reversed(recent_turns):
                if turn.role != "assistant":
                    continue
                path_match = re.search(r"[\w/\\.-]+\.\w{1,8}", turn.content)
                if path_match and _recent_turn_path_matches_ref(
                    ref_label,
                    turn.content,
                    path_match.group(),
                ):
                    matched_artifacts.append({
                        "type": ref_label,
                        "source": "recent_turn",
                        "path": path_match.group(),
                    })
                    resolved = True
                    break

        if not resolved:
            unresolved.append(ref_label)

    return matched_artifacts, unresolved


# ---------------------------------------------------------------------------
# Context sufficiency check
# ---------------------------------------------------------------------------

_DATA_ACTION_RE = re.compile(
    r"\b(analyze|process|compute|calculate)\b", re.IGNORECASE,
)
_ARTIFACT_ACTION_RE = re.compile(
    r"\b(update|edit|revise|modify|read|review|summarize|open|inspect)\b",
    re.IGNORECASE,
)


def check_context_sufficiency(
    package: ContextPackage, message: str
) -> str | None:
    """Return a clarification question if context is insufficient, else None."""
    has_implicit_refs = bool(_IMPLICIT_REF_RE.search(message))
    if (
        has_implicit_refs
        and _ARTIFACT_ACTION_RE.search(message)
        and package.unresolved_references
        and not package.recent_artifacts
    ):
        refs = ", ".join(package.unresolved_references)
        return (
            f"You mentioned {refs}, but I couldn't determine which specific "
            f"file(s) you're referring to. Could you provide the file name or path?"
        )

    unresolved_data_refs = {
        ref for ref in package.unresolved_references
        if ref in {"dataset", "spreadsheet", "notebook", "data"}
    }
    if (
        _DATA_ACTION_RE.search(message)
        and not package.recent_artifacts
        and unresolved_data_refs
    ):
        return (
            "This looks like it needs a data source, but I don't see one in "
            "the current context. Could you specify which file or dataset to use?"
        )

    return None


def apply_new_domain(
    domain_name: str,
    keywords: list[str],
    behavior_store: Any,
) -> None:
    """Register a newly discovered domain — additions only, never removes existing."""
    canonical_domain = normalize_domain_name(domain_name) or domain_name
    kw_map = get_domain_keyword_map(behavior_store)
    kw_map[canonical_domain] = [
        str(keyword or "").strip().lower()
        for keyword in keywords
        if str(keyword or "").strip()
    ]
    behavior_store.set(
        "domains/keyword_maps",
        kw_map,
        reason=f"auto-discovered domain '{canonical_domain}'",
    )
    template = create_generic_template(canonical_domain)
    save_domain_template(template)

from .domain_learning_adaptations import (
    propose_domain_discoveries,
    propose_keyword_expansion,
)


# ---------------------------------------------------------------------------
# Memory-kernel consolidation hook (41-4 — engine must not import concierge)
# ---------------------------------------------------------------------------

_evolvement_log = logging.getLogger("dan.evolvement")


def consolidate_memory_kernel_domain_templates(
    kernel: Any,
    *,
    feature_enabled: Callable[[str], bool] | None = None,
) -> int:
    """Consolidate domain-knowledge memory items into templates (31-21 task 7).

    Called from :meth:`dan.engine.memory_kernel.MemoryKernel.run_consolidation`
    via an injected hook so ``memory_kernel`` does not import this module.

    Returns the number of domains for which a template was consolidated.
    """
    feature_gate = feature_enabled or engine_feature_enabled
    try:
        if not feature_gate("domain_learning"):
            return 0
    except Exception:
        return 0

    domains_consolidated = 0
    try:
        domain_items: dict[str, list[MemoryItem]] = {}
        for mem_type in (
            MemoryType.FACT,
            MemoryType.PREFERENCE,
            MemoryType.PRINCIPLE,
            MemoryType.WORKFLOW_PATTERN,
        ):
            for item in kernel.list_by_type(mem_type):
                if "domain_knowledge" not in (item.tags or []):
                    continue
                domain = item.metadata.get("domain")
                if domain:
                    domain_items.setdefault(domain, []).append(item)

        consolidator = DomainTemplateConsolidator()
        generalizer = DomainPatternGeneralizer()
        for domain, items in domain_items.items():
            if len(items) >= 5:
                try:
                    existing_patterns = {
                        item.content.strip().lower()
                        for item in kernel.list_by_type(MemoryType.WORKFLOW_PATTERN)
                        if "generalized_pattern" in (item.tags or [])
                        and item.metadata.get("domain") == domain
                    }
                    new_patterns = [
                        pattern
                        for pattern in generalizer.generalize(domain, items)
                        if pattern.content.strip().lower() not in existing_patterns
                    ]
                    if new_patterns:
                        kernel.store_many(new_patterns)
                except Exception:
                    logger.debug(
                        "Domain pattern generalization failed for %s",
                        domain,
                        exc_info=True,
                    )
            if len(items) < 10:
                continue
            template = get_or_create_template(domain)
            updated = consolidator.consolidate(domain, items, template)
            if updated is not None:
                duplicate_ids = list(
                    updated.metadata.get("merged_duplicate_ids") or []
                )
                if duplicate_ids:
                    for item_id in duplicate_ids:
                        kernel.delete(item_id, hard=True)
                    updated.metadata["merged_duplicate_count"] = len(duplicate_ids)
                    updated.metadata.pop("merged_duplicate_ids", None)
                save_domain_template(updated)
                domains_consolidated += 1
                _evolvement_log.info(
                    "Domain template consolidated: %s (v%d, %d items)",
                    domain,
                    updated.version,
                    len(items),
                )

            if len(items) >= 20 and feature_gate("domain_template_upgrade"):
                last_upgrade_count = (template.metadata or {}).get(
                    "last_llm_upgrade_item_count", 0
                )
                if len(items) - last_upgrade_count >= 10:
                    template.metadata["needs_llm_upgrade"] = True
                    save_domain_template(template)
                    _evolvement_log.info(
                        "Domain template marked for LLM upgrade: %s (%d items)",
                        domain,
                        len(items),
                    )
    except Exception:
        logger.debug("Domain template consolidation failed", exc_info=True)

    return domains_consolidated
