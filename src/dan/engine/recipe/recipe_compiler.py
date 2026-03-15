"""Recipe artifact compiler (Plan 36-4).

Compiles recipe.md (master artifact) and skill.md (compressed runtime
projection) from memory, ingredient ledger, and benchmark artifacts.
"""
from __future__ import annotations

import logging
import time
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from dan.engine.recipe.ingredient_ledger import IngredientLedger
    from dan.engine.recipe.corpus import CorpusReader
    from dan.engine.recipe.models import (
        FurnaceSession,
        RecipeVersion,
        KnowledgeKind,
        Generality,
    )

logger = logging.getLogger(__name__)


class RecipeCompiler:
    """Compiles recipe.md and skill.md from corpus memory + ingredient ledger.

    recipe.md is the master artifact with all sections.
    skill.md is a compressed runtime projection.
    """

    RECIPE_SECTIONS = [
        "Ingredients",
        "Training History",
        "Checkpoints",
        "Domain Thesis",
        "Core Concepts",
        "Association Vectors",
        "Methods And Identification",
        "Rhetorical Taste",
        "Writing Rules",
        "Anti-Patterns",
        "Change Log",
    ]

    def __init__(
        self,
        corpus_id: str,
        recipe_id: str,
        reader: CorpusReader,
        ledger: IngredientLedger,
    ):
        self.corpus_id = corpus_id
        self.recipe_id = recipe_id
        self._reader = reader
        self._ledger = ledger

    def compile_recipe_md(
        self,
        session: FurnaceSession | None = None,
        version: str = "0.1.0",
        domain_thesis: str = "",
        benchmark_results: dict[str, Any] | None = None,
    ) -> str:
        """Compile the full recipe.md artifact."""
        lines = [self._frontmatter(version)]
        lines.append(self._section_ingredients())
        lines.append(self._section_training_history(session))
        lines.append(self._section_checkpoints(session))
        lines.append(self._section_domain_thesis(domain_thesis))
        lines.append(self._section_core_concepts())
        lines.append(self._section_association_vectors())
        lines.append(self._section_methods())
        lines.append(self._section_rhetorical_taste())
        lines.append(self._section_writing_rules())
        lines.append(self._section_anti_patterns())
        lines.append(self._section_change_log(session))
        return "\n\n".join(lines)

    def compile_skill_md(self, version: str = "0.1.0") -> str:
        """Compile the compressed skill.md runtime projection.

        Keeps only highest-value runtime guidance; strips evidence
        appendices and training logs.
        """
        lines = [self._skill_frontmatter(version)]

        # Writing rules (highest value for runtime)
        rules = self._get_knowledge_items("writing_rule")
        if rules:
            lines.append("## Writing Rules\n")
            for item in rules[:20]:
                lines.append(f"- {item.content}")

        # Rhetorical taste
        taste = self._get_knowledge_items("taste_signal")
        if taste:
            lines.append("\n## Rhetorical Taste\n")
            for item in taste[:15]:
                lines.append(f"- {item.content}")

        # Anti-patterns
        anti = self._get_knowledge_items("anti_pattern")
        if anti:
            lines.append("\n## Anti-Patterns\n")
            for item in anti[:10]:
                lines.append(f"- {item.content}")

        # Core concepts (brief)
        terms = self._get_knowledge_items("terminology")
        if terms:
            lines.append("\n## Core Concepts\n")
            for item in terms[:15]:
                lines.append(f"- {item.content}")

        # Methods (brief)
        methods = self._get_knowledge_items("method")
        if methods:
            lines.append("\n## Methods\n")
            for item in methods[:10]:
                lines.append(f"- {item.content}")

        return "\n".join(lines)

    def _frontmatter(self, version: str) -> str:
        from dan.engine.recipe.models import IngredientStatus

        active_count = len(self._ledger.list_ingredients(IngredientStatus.ACTIVE))
        return f"""---
recipe_id: {self.recipe_id}
corpus_id: {self.corpus_id}
version: {version}
ingredient_count: {active_count}
created_at: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
---

# Recipe: {self.recipe_id}"""

    def _skill_frontmatter(self, version: str) -> str:
        return f"""---
skill_id: {self.recipe_id}-skill
recipe_id: {self.recipe_id}
corpus_id: {self.corpus_id}
source_version: {version}
---

# Skill: {self.recipe_id}

> Compressed runtime projection of recipe `{self.recipe_id}` v{version}."""

    def _section_ingredients(self) -> str:
        return self._ledger.to_human_readable()

    def _section_training_history(
        self, session: FurnaceSession | None
    ) -> str:
        lines = ["## Training History\n"]
        if session and session.checkpoints:
            for cp in session.checkpoints:
                lines.append(
                    f"- **Batch {cp.batch_index}** (v{cp.recipe_version}): "
                    f"{len(cp.paper_ids)} papers, "
                    f"{cp.token_usage:,} tokens, ${cp.cost_usd:.4f}"
                )
        else:
            lines.append("_No training batches recorded yet._")
        return "\n".join(lines)

    def _section_checkpoints(self, session: FurnaceSession | None) -> str:
        lines = ["## Checkpoints\n"]
        if session and session.checkpoints:
            for cp in session.checkpoints:
                lines.append(
                    f"### Checkpoint {cp.checkpoint_id} (v{cp.recipe_version})"
                )
                lines.append(f"- Papers: {', '.join(cp.paper_ids[:5])}")
                if cp.changes_from_previous:
                    lines.append(f"- Changes: {cp.changes_from_previous}")
                if cp.benchmark_refs:
                    lines.append(f"- Benchmarks: {', '.join(cp.benchmark_refs)}")
        else:
            lines.append("_No checkpoints yet._")
        return "\n".join(lines)

    def _section_domain_thesis(self, thesis: str) -> str:
        if thesis:
            return f"## Domain Thesis\n\n{thesis}"
        # Try to generate from claims
        claims = self._get_knowledge_items("claim")
        if claims:
            top_claims = claims[:5]
            return "## Domain Thesis\n\n" + "\n".join(
                f"- {c.content}" for c in top_claims
            )
        return "## Domain Thesis\n\n_Not yet distilled._"

    def _section_core_concepts(self) -> str:
        terms = self._get_knowledge_items("terminology")
        if not terms:
            return "## Core Concepts\n\n_Not yet extracted._"
        lines = ["## Core Concepts\n"]
        for item in terms[:30]:
            lines.append(f"- {item.content}")
        return "\n".join(lines)

    def _section_association_vectors(self) -> str:
        assocs = self._get_knowledge_items("association_edge")
        if not assocs:
            return "## Association Vectors\n\n_Not yet inferred._"
        lines = ["## Association Vectors\n"]
        for item in assocs[:20]:
            lines.append(f"- {item.content}")
        return "\n".join(lines)

    def _section_methods(self) -> str:
        methods = self._get_knowledge_items("method")
        if not methods:
            return "## Methods And Identification\n\n_Not yet extracted._"
        lines = ["## Methods And Identification\n"]
        for item in methods[:20]:
            lines.append(f"- {item.content}")
        return "\n".join(lines)

    def _section_rhetorical_taste(self) -> str:
        taste = self._get_knowledge_items("taste_signal")
        moves = self._get_knowledge_items("rhetorical_move")
        combined = taste + moves
        if not combined:
            return "## Rhetorical Taste\n\n_Not yet inferred._"
        combined.sort(key=lambda i: -i.importance)
        lines = ["## Rhetorical Taste\n"]
        for item in combined[:20]:
            lines.append(f"- {item.content}")
        return "\n".join(lines)

    def _section_writing_rules(self) -> str:
        rules = self._get_knowledge_items("writing_rule")
        norms = self._get_knowledge_items("citation_norm")
        combined = rules + norms
        if not combined:
            return "## Writing Rules\n\n_Not yet distilled._"
        combined.sort(key=lambda i: -i.importance)
        lines = ["## Writing Rules\n"]
        for item in combined[:25]:
            lines.append(f"- {item.content}")
        return "\n".join(lines)

    def _section_anti_patterns(self) -> str:
        anti = self._get_knowledge_items("anti_pattern")
        if not anti:
            return "## Anti-Patterns\n\n_Not yet identified._"
        lines = ["## Anti-Patterns\n"]
        for item in anti[:15]:
            lines.append(f"- {item.content}")
        return "\n".join(lines)

    def _section_change_log(self, session: FurnaceSession | None) -> str:
        lines = ["## Change Log\n"]
        diffs = self._ledger.get_version_diffs()
        if diffs:
            for diff in reversed(diffs):
                lines.append(f"### {diff.from_version} → {diff.to_version}")
                if diff.added_ingredients:
                    lines.append(f"- Added: {', '.join(diff.added_ingredients)}")
                if diff.removed_ingredients:
                    lines.append(
                        f"- Removed: {', '.join(diff.removed_ingredients)}"
                    )
                if diff.summary:
                    lines.append(f"- {diff.summary}")
        else:
            lines.append("_Initial version._")
        return "\n".join(lines)

    def _get_knowledge_items(self, kind_value: str) -> list:
        """Get memory items of a specific knowledge kind from the corpus."""
        from dan.engine.recipe.models import KnowledgeKind, Generality

        try:
            kind = KnowledgeKind(kind_value)
        except ValueError:
            return []
        # Get recipe-level first, then domain, then paper
        items = []
        for gen in [Generality.RECIPE, Generality.DOMAIN, Generality.PAPER]:
            items.extend(
                self._reader.query_by_corpus(
                    self.corpus_id, knowledge_kind=kind, generality=gen
                )
            )
        return items

    def create_recipe_version(
        self,
        version: str,
        session: FurnaceSession | None = None,
        checkpoint_id: str | None = None,
        benchmark_results: dict[str, Any] | None = None,
    ) -> RecipeVersion:
        """Create a versioned recipe snapshot."""
        from dan.engine.recipe.models import IngredientStatus, RecipeVersion

        active = self._ledger.list_ingredients(IngredientStatus.ACTIVE)
        return RecipeVersion(
            version=version,
            recipe_id=self.recipe_id,
            corpus_id=self.corpus_id,
            checkpoint_id=checkpoint_id,
            ingredient_count=len(active),
            benchmark_results=benchmark_results or {},
        )

    def bump_version(self, current: str, bump_type: str = "patch") -> str:
        """Bump semver version string."""
        parts = current.split(".")
        if len(parts) != 3:
            parts = ["0", "1", "0"]
        major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
        if bump_type == "major":
            return f"{major + 1}.0.0"
        elif bump_type == "minor":
            return f"{major}.{minor + 1}.0"
        else:
            return f"{major}.{minor}.{patch + 1}"
