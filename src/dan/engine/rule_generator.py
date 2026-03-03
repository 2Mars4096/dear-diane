"""Self-generating rules system — maps causal principles to runtime hyperedges.

Causal principles discovered by the error-memory subsystem are transformed into
Hyperedge instances that modify future workflow executions.  Rules are persisted
per-workflow with TTL-based expiry, effectiveness tracking, and lifecycle
management (create / disable / prune / rollback).
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from dan.models.hyperedges import VALID_HOOKS_BY_TYPE, Hyperedge

if TYPE_CHECKING:
    from dan.engine.error_memory import CausalPrinciple

logger = logging.getLogger(__name__)

_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _safe_segment(value: str) -> str:
    """Sanitize a path segment to prevent directory traversal."""
    sanitized = value.replace("/", "_").replace("\\", "_").replace("..", "_").replace("\0", "")
    if not sanitized or not _SAFE_SEGMENT_RE.match(sanitized):
        raise ValueError(f"Invalid path segment: {value!r}")
    return sanitized


_NEGATION_PATTERN = re.compile(
    r"\b(avoid|do\s+not|never|prevent|don'?t)\b", re.IGNORECASE
)
_PREFERENCE_PATTERN = re.compile(
    r"\b(prefer|use|always|default\s+to|ensure)\b", re.IGNORECASE
)
_INTERCEPTION_PATTERN = re.compile(
    r"\b(block|reject|deny|override)\b", re.IGNORECASE
)


class RuleGenerator:
    """Maps a *CausalPrinciple* to a *Hyperedge* using semantic keyword analysis."""

    def __init__(self, base_priority: int = 100) -> None:
        self._base_priority = base_priority

    def generate_rule(
        self, principle: CausalPrinciple, workflow_id: str
    ) -> Hyperedge | None:
        repair_level = getattr(principle, "repair_level", "prompt_fix")
        if repair_level == "parameter_fix":
            return None

        action = getattr(principle, "action", "")

        hyperedge_type, hook = self._classify_action(action)

        attach_to: list[str] = []
        attach_to_tags: list[str] = []
        attach_to_type: list[str] = []

        tags = getattr(principle, "tags", None) or []
        source_ids = getattr(principle, "source_node_ids", None) or []

        if tags:
            attach_to_tags = list(tags)
        elif source_ids and len(source_ids) <= 3:
            attach_to = list(source_ids)
        else:
            attach_to_type = ["llm_operator"]

        condition = getattr(principle, "condition", "")
        reason = getattr(principle, "reason", "")
        content = (
            "Based on past experience with this workflow:\n"
            f"- WHEN: {condition}\n"
            f"- THEREFORE: {action}\n"
            f"- REASON: {reason}\n"
            "Apply this guidance to your current task."
        )

        principle_id = getattr(principle, "id", str(uuid.uuid4()))
        edge_id = f"gen-{principle_id[:8]}"
        name = f"Auto: {condition[:50]}"

        try:
            return Hyperedge(
                id=edge_id,
                name=name,
                description=reason,
                hyperedge_type=hyperedge_type,
                hook=hook,
                content=content,
                priority=self._base_priority,
                attach_to=attach_to,
                attach_to_tags=attach_to_tags,
                attach_to_type=attach_to_type,
            )
        except (ValueError, Exception):
            pass

        try:
            return Hyperedge(
                id=edge_id,
                name=name,
                description=reason,
                hyperedge_type="skill",
                hook="pre_prompt",
                content=content,
                priority=self._base_priority,
                attach_to=attach_to,
                attach_to_tags=attach_to_tags,
                attach_to_type=attach_to_type,
            )
        except (ValueError, Exception):
            logger.warning(
                "Failed to generate hyperedge for principle %s even with "
                "skill/pre_prompt fallback",
                principle_id,
            )
            return None

    @staticmethod
    def _classify_action(action: str) -> tuple[str, str]:
        if _NEGATION_PATTERN.search(action):
            return "guardrail", "pre_prompt"
        if _INTERCEPTION_PATTERN.search(action):
            return "override", "tool_call"
        if _PREFERENCE_PATTERN.search(action):
            return "skill", "pre_prompt"
        return "skill", "pre_prompt"


class GeneratedRule(BaseModel):
    """A persisted rule that wraps a generated Hyperedge with lifecycle metadata."""

    rule_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    hyperedge: Hyperedge
    source_principle_id: str
    workflow_id: str
    created_at: float = Field(default_factory=time.time)
    expires_at: float | None = None
    apply_count: int = 0
    prevented_count: int = 0
    recurred_count: int = 0
    status: Literal["active", "expired", "disabled", "pending", "superseded"] = (
        "active"
    )

    @property
    def effectiveness_score(self) -> float:
        return self.prevented_count / max(self.apply_count, 1)


_ALLOWED_MUTATION_FIELDS = frozenset({
    "model", "temperature", "max_tokens", "timeout_seconds", "tool_config",
})

_TOPOLOGY_FIELDS = frozenset({
    "nodes", "edges", "entry_points", "exit_points", "sub_graphs",
    "input_ports", "output_ports", "node_type",
})


class ParameterMutation(BaseModel):
    """Runtime-only parameter patch for a specific node.

    Applied as a temporary graph modification in scheduler._execute();
    the persisted graph remains unchanged.
    """

    mutation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_principle_id: str
    source_rule_id: str = ""
    workflow_id: str
    target_node_id: str
    changes: dict[str, Any] = Field(default_factory=dict)
    status: Literal["active", "expired", "disabled"] = "active"
    created_at: float = Field(default_factory=time.time)
    expires_at: float | None = None

    def validate_changes(self) -> list[str]:
        """Return list of rejected field names."""
        rejected = []
        for key in self.changes:
            if key in _TOPOLOGY_FIELDS:
                rejected.append(key)
            elif key not in _ALLOWED_MUTATION_FIELDS:
                rejected.append(key)
        return rejected


class RuleLifecycleManager:
    """Filesystem-backed CRUD and lifecycle management for generated rules.

    Layout::

        {base_dir}/{workflow_id}/{rule_id}.json
    """

    def __init__(
        self,
        base_dir: str = "./rules",
        generator: RuleGenerator | None = None,
        default_ttl_days: int = 30,
        max_rules_per_workflow: int = 20,
    ) -> None:
        self._base = Path(base_dir)
        self._generator = generator or RuleGenerator()
        self._default_ttl_days = default_ttl_days
        self._max_rules = max_rules_per_workflow

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_rule(
        self,
        principle: CausalPrinciple,
        workflow_id: str,
        auto_activate: bool = True,
    ) -> GeneratedRule | None:
        hyperedge = self._generator.generate_rule(principle, workflow_id)
        if hyperedge is None:
            return None

        existing = self._load_all(workflow_id)

        if len(existing) >= self._max_rules:
            if not self._evict_one(existing, workflow_id):
                logger.warning(
                    "Cannot create rule for workflow %s: cap of %d reached "
                    "and no evictable rule found",
                    workflow_id,
                    self._max_rules,
                )
                return None

        principle_id = getattr(principle, "id", str(uuid.uuid4()))

        rule = GeneratedRule(
            hyperedge=hyperedge,
            source_principle_id=principle_id,
            workflow_id=workflow_id,
            expires_at=time.time() + self._default_ttl_days * 86400,
            status="active" if auto_activate else "pending",
        )
        self._persist(rule)
        return rule

    def list_rules(
        self, workflow_id: str, status: str | None = None
    ) -> list[GeneratedRule]:
        rules = self._load_all(workflow_id)
        now = time.time()
        changed = False
        for rule in rules:
            if (
                rule.status == "active"
                and rule.expires_at is not None
                and rule.expires_at <= now
            ):
                rule.status = "expired"
                self._persist(rule)
                changed = True

        if status is not None:
            rules = [r for r in rules if r.status == status]
        return rules

    def get_active_hyperedges(self, workflow_id: str) -> list[Hyperedge]:
        active = self.list_rules(workflow_id, status="active")
        return [r.hyperedge for r in active]

    def create_mutation(
        self,
        principle: CausalPrinciple,
        workflow_id: str,
    ) -> ParameterMutation | None:
        """Create a ParameterMutation from a parameter_fix principle."""
        repair_level = getattr(principle, "repair_level", "prompt_fix")
        if repair_level != "parameter_fix":
            return None

        changes = getattr(principle, "suggested_parameter_changes", {})
        if not changes:
            return None

        target_nodes = getattr(principle, "source_node_ids", [])
        if not target_nodes:
            return None

        mutations_created = []
        for target_node_id in target_nodes:
            mutation = ParameterMutation(
                source_principle_id=getattr(principle, "id", ""),
                workflow_id=workflow_id,
                target_node_id=target_node_id,
                changes=dict(changes),
                expires_at=time.time() + self._default_ttl_days * 86400,
            )
            rejected = mutation.validate_changes()
            if rejected:
                logger.warning(
                    "ParameterMutation rejected fields %s for node %s",
                    rejected, target_node_id,
                )
                mutation.changes = {
                    k: v for k, v in mutation.changes.items()
                    if k not in set(rejected)
                }
                if not mutation.changes:
                    continue

            self._persist_mutation(mutation)
            mutations_created.append(mutation)

        return mutations_created[0] if mutations_created else None

    def get_active_mutations(self, workflow_id: str) -> list[ParameterMutation]:
        """Load active parameter mutations for a workflow."""
        mutations = self._load_all_mutations(workflow_id)
        now = time.time()
        active = []
        for m in mutations:
            if m.status == "active" and (m.expires_at is None or m.expires_at > now):
                active.append(m)
            elif m.status == "active" and m.expires_at is not None and m.expires_at <= now:
                m.status = "expired"
                self._persist_mutation(m)
        return active

    def disable_rule(self, workflow_id: str, rule_id: str) -> bool:
        rule = self._load_one(workflow_id, rule_id)
        if rule is None:
            return False
        rule.status = "disabled"
        self._persist(rule)
        return True

    def enable_rule(self, workflow_id: str, rule_id: str) -> bool:
        rule = self._load_one(workflow_id, rule_id)
        if rule is None:
            return False
        rule.status = "active"
        self._persist(rule)
        return True

    def delete_rule(self, workflow_id: str, rule_id: str) -> bool:
        path = self._rule_path(workflow_id, rule_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def record_application(self, workflow_id: str, rule_id: str) -> None:
        rule = self._load_one(workflow_id, rule_id)
        if rule is None:
            logger.debug("record_application: rule %s not found", rule_id)
            return
        rule.apply_count += 1
        self._persist(rule)

    def record_outcome(
        self, workflow_id: str, rule_id: str, error_recurred: bool
    ) -> None:
        rule = self._load_one(workflow_id, rule_id)
        if rule is None:
            logger.debug("record_outcome: rule %s not found", rule_id)
            return
        if error_recurred:
            rule.recurred_count += 1
        else:
            rule.prevented_count += 1
        self._persist(rule)

    def prune_ineffective(
        self,
        workflow_id: str,
        min_effectiveness: float = 0.3,
        min_apply_count: int = 5,
    ) -> list[str]:
        rules = self._load_all(workflow_id)
        disabled: list[str] = []
        for rule in rules:
            if rule.status != "active":
                continue
            if rule.apply_count < min_apply_count:
                continue
            if rule.effectiveness_score < min_effectiveness:
                rule.status = "disabled"
                self._persist(rule)
                disabled.append(rule.rule_id)
                logger.info(
                    "Pruned ineffective rule %s (score=%.2f, applies=%d)",
                    rule.rule_id,
                    rule.effectiveness_score,
                    rule.apply_count,
                )
        return disabled

    def rollback(self, workflow_id: str, before_timestamp: float) -> list[str]:
        rules = self._load_all(workflow_id)
        disabled: list[str] = []
        for rule in rules:
            if rule.created_at >= before_timestamp and rule.status not in (
                "disabled",
                "expired",
            ):
                rule.status = "disabled"
                self._persist(rule)
                disabled.append(rule.rule_id)
        return disabled

    def stats(self, workflow_id: str) -> dict[str, Any]:
        rules = self.list_rules(workflow_id)
        counts: dict[str, int] = {
            "total": 0,
            "active": 0,
            "expired": 0,
            "disabled": 0,
            "pending": 0,
            "superseded": 0,
        }
        effectiveness_sum = 0.0
        effectiveness_n = 0

        for rule in rules:
            counts["total"] += 1
            counts[rule.status] = counts.get(rule.status, 0) + 1
            if rule.apply_count > 0:
                effectiveness_sum += rule.effectiveness_score
                effectiveness_n += 1

        return {
            **counts,
            "avg_effectiveness": (
                effectiveness_sum / effectiveness_n if effectiveness_n else 0.0
            ),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _workflow_dir(self, workflow_id: str) -> Path:
        return self._base / _safe_segment(workflow_id)

    def _rule_path(self, workflow_id: str, rule_id: str) -> Path:
        return self._workflow_dir(workflow_id) / f"{_safe_segment(rule_id)}.json"

    def _persist(self, rule: GeneratedRule) -> None:
        directory = self._workflow_dir(rule.workflow_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = self._rule_path(rule.workflow_id, rule.rule_id)
        fd, tmp_path = tempfile.mkstemp(dir=str(directory), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(rule.model_dump_json(indent=2))
            os.replace(tmp_path, str(target))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _load_one(self, workflow_id: str, rule_id: str) -> GeneratedRule | None:
        path = self._rule_path(workflow_id, rule_id)
        if not path.exists():
            return None
        try:
            return GeneratedRule.model_validate_json(path.read_text())
        except Exception:
            logger.warning("Corrupt rule file %s — skipping", path)
            return None

    def _load_all(self, workflow_id: str) -> list[GeneratedRule]:
        directory = self._workflow_dir(workflow_id)
        if not directory.is_dir():
            return []
        rules: list[GeneratedRule] = []
        for entry in directory.iterdir():
            if entry.suffix != ".json":
                continue
            try:
                rules.append(GeneratedRule.model_validate_json(entry.read_text()))
            except Exception:
                logger.warning("Corrupt rule file %s — skipping", entry)
        return rules

    def _mutations_dir(self, workflow_id: str) -> Path:
        return self._base / _safe_segment(workflow_id) / "mutations"

    def _mutation_path(self, workflow_id: str, mutation_id: str) -> Path:
        return self._mutations_dir(workflow_id) / f"{_safe_segment(mutation_id)}.json"

    def _persist_mutation(self, mutation: ParameterMutation) -> None:
        directory = self._mutations_dir(mutation.workflow_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = self._mutation_path(mutation.workflow_id, mutation.mutation_id)
        fd, tmp_path = tempfile.mkstemp(dir=str(directory), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(mutation.model_dump_json(indent=2))
            os.replace(tmp_path, str(target))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _load_all_mutations(self, workflow_id: str) -> list[ParameterMutation]:
        directory = self._mutations_dir(workflow_id)
        if not directory.is_dir():
            return []
        mutations: list[ParameterMutation] = []
        for entry in directory.iterdir():
            if entry.suffix != ".json":
                continue
            try:
                mutations.append(ParameterMutation.model_validate_json(entry.read_text()))
            except Exception:
                logger.warning("Corrupt mutation file %s — skipping", entry)
        return mutations

    def _evict_one(
        self, rules: list[GeneratedRule], workflow_id: str
    ) -> bool:
        expired = [r for r in rules if r.status == "expired"]
        if expired:
            return self.delete_rule(workflow_id, expired[0].rule_id)

        disabled = [r for r in rules if r.status == "disabled"]
        if disabled:
            return self.delete_rule(workflow_id, disabled[0].rule_id)

        active = [r for r in rules if r.status == "active"]
        if active:
            worst = min(active, key=lambda r: r.effectiveness_score)
            return self.delete_rule(workflow_id, worst.rule_id)

        return False
