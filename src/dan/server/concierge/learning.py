"""Command handlers for learning & evolution features (31-15).

Provides ``/corrections`` and ``/adaptations`` chat commands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dan.engine.adaptation_registry import AdaptationRegistry
    from dan.engine.correction_memory import CorrectionStore


def handle_corrections_command(text: str, store: CorrectionStore) -> str:
    """Handle ``/corrections`` — list recent correction events."""
    records = store.list_recent(20)
    if not records:
        return "No corrections recorded yet."

    lines: list[str] = [f"**Recent corrections** ({store.count()} total)\n"]
    for rec in records:
        ts = rec.timestamp.strftime("%Y-%m-%d %H:%M")
        sig = rec.signal
        actions_str = ", ".join(a.get("type", "?") for a in rec.actions) or "none"
        lines.append(
            f"- [{ts}] **{sig.correction_type}** "
            f"(conf {sig.confidence:.0%}) → {actions_str}"
        )
        if sig.extracted_preference:
            lines.append(f"  Preference: {sig.extracted_preference}")
        if sig.extracted_principle:
            lines.append(f"  Principle: {sig.extracted_principle}")
    return "\n".join(lines)


def handle_adaptations_command(text: str, registry: AdaptationRegistry) -> str:
    """Handle ``/adaptations`` — list pending and applied adaptations."""
    pending = registry.list_pending()
    applied = registry.list_applied()

    if not pending and not applied:
        return "No adaptations recorded yet."

    lines: list[str] = []

    if pending:
        lines.append(f"**Pending adaptations** ({len(pending)})\n")
        for c in pending:
            lines.append(
                f"- `{c.id}` [{c.source}] {c.description or '(no description)'} "
                f"— conf {c.confidence:.0%}, {c.sample_size} samples, scope: {c.scope}"
            )

    if applied:
        lines.append(f"\n**Applied adaptations** ({len(applied)})\n")
        for c in applied:
            applied_str = c.applied_at.strftime("%Y-%m-%d %H:%M") if c.applied_at else "?"
            lines.append(
                f"- `{c.id}` [{c.source}] {c.description or '(no description)'} "
                f"— applied {applied_str}, scope: {c.scope}"
            )

    return "\n".join(lines)
