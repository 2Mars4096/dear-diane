"""Command handlers for learning & evolution features (31-15, 31-22).

Provides ``/corrections``, ``/adaptations``, ``/changes``, ``/revert``,
and ``/behavior`` chat commands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dan.engine.adaptation_registry import AdaptationRegistry
    from dan.engine.behavior_store import (
        AdaptableParameterRegistry,
        BehaviorChangeLog,
        BehaviorStore,
    )
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

    if pending:
        lines.append(
            "\nUse `/approve <id>` to accept or `/reject <id>` to dismiss a pending adaptation."
        )

    return "\n".join(lines)


# -- Behavior commands (31-22) --------------------------------------------


def handle_changes_command(
    text: str,
    changelog: BehaviorChangeLog,
) -> str:
    """Handle ``/changes`` — list recent behavioral adaptations."""
    from datetime import datetime, timezone

    args = text.strip().split()

    show_all = "--all" in args
    category_filter: str | None = None
    if "--category" in args:
        idx = args.index("--category")
        if idx + 1 < len(args):
            category_filter = args[idx + 1]

    if show_all:
        entries = changelog.list_all(category=category_filter)
    else:
        entries = changelog.list_recent(hours=24.0)
        if category_filter:
            entries = [e for e in entries if e.category == category_filter]

    if not entries:
        scope = f" in category '{category_filter}'" if category_filter else ""
        window = "" if show_all else " (last 24h)"
        return f"No behavioral changes{scope}{window}."

    lines: list[str] = [f"**Behavioral changes** ({len(entries)} entries)\n"]
    for e in entries:
        ts = datetime.fromtimestamp(e.timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        val_summary = ""
        if e.before_summary and e.after_summary:
            val_summary = f": {e.before_summary} → {e.after_summary}"
        source_str = f" ({e.source})" if e.source else ""
        lines.append(
            f"- [`{e.id}`] {ts} {e.category}/{e.key} "
            f"{e.action}{val_summary}{source_str}"
        )
    return "\n".join(lines)


def handle_revert_command(
    text: str,
    changelog: BehaviorChangeLog,
    store: BehaviorStore,
) -> str:
    """Handle ``/revert <id>`` — roll back a specific behavior change."""
    from dan.engine.behavior_store import BehaviorChangeEntry

    change_id = text.strip()
    if not change_id:
        return "Usage: `/revert <change_id>`"

    entry = changelog.get(change_id)
    if entry is None:
        return f"Change `{change_id}` not found."

    old_value = store.get(entry.key)
    try:
        store.revert(entry.key)
    except (KeyError, ValueError) as exc:
        return f"Cannot revert `{entry.key}`: {exc}"
    new_value = store.get(entry.key)

    changelog.append(BehaviorChangeEntry(
        category=entry.category,
        key=entry.key,
        action="revert",
        before_summary=str(old_value)[:200],
        after_summary=str(new_value)[:200],
        evidence=[f"manual revert of {change_id}"],
        source="user",
    ))

    return (
        f"Reverted `{entry.key}`\n"
        f"- Before: {old_value}\n"
        f"- After:  {new_value}"
    )


def handle_behavior_command(
    text: str,
    store: BehaviorStore,
    param_registry: AdaptableParameterRegistry | None = None,
) -> str:
    """Handle ``/behavior`` — inspect current behavior state."""
    from collections import Counter
    from datetime import datetime, timezone

    args = text.strip().split()

    if "--key" in args:
        idx = args.index("--key")
        if idx + 1 >= len(args):
            return "Usage: `/behavior --key <key>`"
        key = args[idx + 1]
        artifact = store.get_artifact(key)
        if artifact is None:
            seed = store.get(key)
            if seed is not None:
                return f"**{key}** (seed default)\n- Value: {seed}"
            return f"Key `{key}` not found."
        lines: list[str] = [
            f"**{key}**",
            f"- Value: {artifact.value}",
            f"- Version: {artifact.version}",
        ]
        if artifact.previous_versions:
            lines.append("- History:")
            for h in artifact.previous_versions[-5:]:
                ts = datetime.fromtimestamp(
                    h.get("updated_at", 0), tz=timezone.utc,
                ).strftime("%Y-%m-%d %H:%M")
                lines.append(f"  - [v{h.get('version', '?')}] {ts} — {str(h.get('value', ''))[:80]}")
        return "\n".join(lines)

    if "--seeds" in args:
        all_keys = store.list_keys()
        if not all_keys:
            return "No behavior keys registered."
        deviations: list[str] = []
        for key in sorted(all_keys):
            artifact = store.get_artifact(key)
            seed = store.get_seed(key)
            if artifact is not None and seed is not None and artifact.value != seed:
                deviations.append(
                    f"- `{key}`: seed={str(seed)[:60]}, current={str(artifact.value)[:60]}"
                )
        if not deviations:
            return "All values match seed defaults."
        return (
            f"**Deviations from seed defaults** ({len(deviations)})\n\n"
            + "\n".join(deviations)
        )

    if "--reset" in args:
        idx = args.index("--reset")
        if idx + 1 >= len(args):
            return "Usage: `/behavior --reset <key>`"
        key = args[idx + 1]
        seed = store.get_seed(key)
        if seed is None:
            return f"No seed default for `{key}`."
        old_value = store.get(key)
        store.set(key, seed, reason="manual reset to seed default")
        return f"Reset `{key}`: {old_value} → {seed}"

    all_keys = store.list_keys()
    if not all_keys:
        return "No behavior state recorded."
    summary: Counter[str] = Counter()
    for key in all_keys:
        cat = key.split("/", 1)[0] if "/" in key else "other"
        summary[cat] += 1
    lines = ["**Behavior state summary**\n"]
    for cat, count in sorted(summary.items()):
        lines.append(f"- {cat}: {count} keys")
    lines.append(f"\nTotal: {sum(summary.values())} keys")
    return "\n".join(lines)
