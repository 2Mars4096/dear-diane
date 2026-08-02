from __future__ import annotations

import argparse

from dan.skills import invocation


def _catalog() -> list[dict]:
    return [
        {
            "id": "idea_cart",
            "name": "idea-cart",
            "description": "Capture important ideas",
            "content": "Capture ideas.",
        },
        {
            "id": "scaffold_dev",
            "name": "scaffold-dev",
            "description": "Create project docs",
            "content": "Create dev docs.",
        },
        {
            "id": "scaffold_research",
            "name": "scaffold-research",
            "description": "Create research docs",
            "content": "Create research docs.",
        },
    ]


def test_parse_skill_invocation_strips_exact_leading_mentions() -> None:
    parsed = invocation.parse_skill_invocation_text(
        "$idea-cart capture decisions",
        catalog=_catalog(),
    )

    assert parsed.should_run is True
    assert parsed.objective == "capture decisions"
    assert parsed.selected_tokens == ("idea-cart",)


def test_parse_skill_invocation_ignores_prices_env_vars_and_plain_unknown_dollars() -> None:
    catalog = _catalog()

    for text in ("pay $5 for this", "$PATH should stay literal", "$foo should stay literal"):
        parsed = invocation.parse_skill_invocation_text(text, catalog=catalog)
        assert parsed.should_run is True
        assert parsed.objective == text
        assert parsed.selected_tokens == ()


def test_parse_skill_invocation_blocks_unknown_and_ambiguous_skill_like_mentions() -> None:
    unknown = invocation.parse_skill_invocation_text("$not-a-skill do work", catalog=_catalog())
    assert unknown.should_run is False
    assert unknown.unknown_tokens == ("not-a-skill",)
    assert "Unknown skill mention" in unknown.message

    ambiguous = invocation.parse_skill_invocation_text("$scaffold do work", catalog=_catalog())
    assert ambiguous.should_run is False
    assert "$scaffold-dev" in ambiguous.message
    assert "$scaffold-research" in ambiguous.message


def test_prepare_skill_invocation_args_sets_surface_neutral_metadata() -> None:
    args = argparse.Namespace(target="$idea-cart capture decisions", workspace="/tmp/ws")

    parsed = invocation.prepare_skill_invocation_args(
        args,
        catalog=_catalog(),
        source="cli",
    )

    assert parsed.selected_tokens == ("idea-cart",)
    assert args.target == "capture decisions"
    assert args._selected_skill_mentions == ["idea-cart"]
    assert args.selected_skill_mentions == ["idea-cart"]
    assert args._selected_skill_source == "cli"
