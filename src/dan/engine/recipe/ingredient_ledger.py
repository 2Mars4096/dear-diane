"""Ingredient ledger for recipe provenance tracking (Plan 36-2).

Tracks which papers fed each recipe version, with inclusion/exclusion
reasons, version-to-version diffs, and publishable provenance manifests.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from dan.engine.recipe.models import (
    IngredientRecord,
    IngredientStatus,
    VersionDiff,
)

logger = logging.getLogger(__name__)


class IngredientLedger:
    """Persistent ledger tracking ingredient provenance across recipe versions.

    Stored as JSON at ~/.dan/furnace/ledgers/{corpus_id}.json
    """

    def __init__(self, corpus_id: str, base_dir: str | Path | None = None):
        self.corpus_id = corpus_id
        self._base_dir = Path(base_dir or os.path.expanduser("~/.dan/furnace/ledgers"))
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._path = self._base_dir / f"{corpus_id}.json"
        self._ingredients: dict[str, IngredientRecord] = {}
        self._version_diffs: list[VersionDiff] = []
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._ingredients = {
                k: IngredientRecord.model_validate(v)
                for k, v in data.get("ingredients", {}).items()
            }
            self._version_diffs = [
                VersionDiff.model_validate(d)
                for d in data.get("version_diffs", [])
            ]

    def _save(self) -> None:
        data = {
            "corpus_id": self.corpus_id,
            "ingredients": {k: v.model_dump() for k, v in self._ingredients.items()},
            "version_diffs": [d.model_dump() for d in self._version_diffs],
        }
        self._path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def add_ingredient(self, record: IngredientRecord) -> None:
        """Add or update an ingredient record."""
        existing = self._ingredients.get(record.paper_id)
        if existing:
            record.added_at = existing.added_at
        record.updated_at = time.time()
        self._ingredients[record.paper_id] = record
        self._save()

    def get_ingredient(self, paper_id: str) -> IngredientRecord | None:
        return self._ingredients.get(paper_id)

    def list_ingredients(
        self, status: IngredientStatus | None = None
    ) -> list[IngredientRecord]:
        items = list(self._ingredients.values())
        if status is not None:
            items = [i for i in items if i.status == status]
        return sorted(items, key=lambda i: i.added_at)

    def update_status(
        self, paper_id: str, status: IngredientStatus, reason: str = ""
    ) -> bool:
        rec = self._ingredients.get(paper_id)
        if rec is None:
            return False
        rec.status = status
        if status == IngredientStatus.EXCLUDED:
            rec.exclusion_reason = reason
        elif status == IngredientStatus.ACTIVE:
            rec.inclusion_reason = reason or rec.inclusion_reason
        rec.updated_at = time.time()
        self._save()
        return True

    def mark_version(self, paper_id: str, version: str) -> bool:
        """Record that a paper contributed to a recipe version."""
        rec = self._ingredients.get(paper_id)
        if rec is None:
            return False
        if version not in rec.included_in_versions:
            rec.included_in_versions.append(version)
            rec.updated_at = time.time()
            self._save()
        return True

    def compute_version_diff(self, from_version: str, to_version: str) -> VersionDiff:
        """Compute what changed between two recipe versions."""
        from_ids = {
            pid
            for pid, r in self._ingredients.items()
            if from_version in r.included_in_versions
        }
        to_ids = {
            pid
            for pid, r in self._ingredients.items()
            if to_version in r.included_in_versions
        }
        diff = VersionDiff(
            from_version=from_version,
            to_version=to_version,
            added_ingredients=sorted(to_ids - from_ids),
            removed_ingredients=sorted(from_ids - to_ids),
        )
        self._version_diffs.append(diff)
        self._save()
        return diff

    def get_version_diffs(self) -> list[VersionDiff]:
        return list(self._version_diffs)

    def to_human_readable(self) -> str:
        """Generate human-readable ingredient manifest for recipe.md."""
        lines = ["## Ingredients\n"]
        active = self.list_ingredients(IngredientStatus.ACTIVE)
        for rec in active:
            authors = ", ".join(rec.authors[:3])
            if len(rec.authors) > 3:
                authors += " et al."
            year_str = f" ({rec.year})" if rec.year else ""
            lines.append(f"- **{rec.paper_id}**: {rec.title}{year_str}")
            if authors:
                lines.append(f"  - Authors: {authors}")
            lines.append(f"  - Source: {rec.source.value}")
            if rec.included_in_versions:
                lines.append(f"  - Versions: {', '.join(rec.included_in_versions)}")
            if rec.inclusion_reason:
                lines.append(f"  - Reason: {rec.inclusion_reason}")
        return "\n".join(lines)

    def to_machine_manifest(self) -> dict[str, Any]:
        """Generate machine-readable manifest for marketplace/export."""
        all_items = self.list_ingredients()
        active_count = sum(1 for r in all_items if r.status == IngredientStatus.ACTIVE)
        return {
            "corpus_id": self.corpus_id,
            "ingredient_count": len(all_items),
            "active_count": active_count,
            "ingredients": [
                {
                    "paper_id": r.paper_id,
                    "title": r.title,
                    "authors": r.authors,
                    "year": r.year,
                    "source": r.source.value,
                    "status": r.status.value,
                    "included_in_versions": r.included_in_versions,
                }
                for r in all_items
            ],
            "version_diffs": [d.model_dump() for d in self._version_diffs],
        }

    def delete(self) -> bool:
        if self._path.exists():
            self._path.unlink()
            return True
        return False
