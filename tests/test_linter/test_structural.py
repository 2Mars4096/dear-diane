from __future__ import annotations

from dan.linter.config import RuleSeverity, StructuralConfig
from dan.linter.rules.structural import STRUCTURAL_RULES, validate_structural


def test_structural_rule_registry_describes_landed_rules() -> None:
    assert [rule.code for rule in STRUCTURAL_RULES] == [
        "schema_conformance",
        "required_keys",
        "non_empty",
        "string_max_length",
        "numeric_range",
        "format_pattern",
    ]


def test_validate_structural_reports_field_paths() -> None:
    diagnostics, fixed_data, applied_fixes = validate_structural(
        {"ticket_id": "abc-12"},
        StructuralConfig(format_patterns={"ticket_id": r"[A-Z]{3}-\d{4}"}),
        severity=RuleSeverity.ERROR,
    )

    assert fixed_data is None
    assert applied_fixes == []
    assert len(diagnostics) == 1
    assert diagnostics[0].field_path == ["ticket_id"]


def test_validate_structural_relint_stabilizes_after_autofix() -> None:
    diagnostics, fixed_data, applied_fixes = validate_structural(
        {"score": "11"},
        StructuralConfig(
            json_schema={
                "type": "object",
                "properties": {"score": {"type": "integer"}},
            },
            ranges={"score": {"minimum": 0, "maximum": 10}},
        ),
        severity=RuleSeverity.ERROR,
        autofix=["coerce", "clamp"],
    )

    assert diagnostics == []
    assert fixed_data == {"score": 10}
    assert set(applied_fixes) == {"coerce", "clamp"}


def test_validate_structural_passes_realistic_agent_payload() -> None:
    diagnostics, fixed_data, applied_fixes = validate_structural(
        {
            "summary": "Acme Q4 finance summary with revenue, margin, and risk notes.",
            "confidence_score": 0.82,
            "ticket_id": "FIN-2026",
            "recommendations": [
                "prepare margin drilldown",
                "watch operating cash flow",
            ],
            "metadata": {
                "owner": "finance",
                "priority": "high",
                "region": "global",
            },
        },
        StructuralConfig(
            json_schema={
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "confidence_score": {"type": "number"},
                    "ticket_id": {"type": "string"},
                    "recommendations": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "metadata": {"type": "object"},
                },
                "required": [
                    "summary",
                    "confidence_score",
                    "ticket_id",
                    "recommendations",
                ],
            },
            required_keys=[
                "summary",
                "confidence_score",
                "ticket_id",
                "recommendations",
            ],
            non_empty_keys=["summary", "recommendations"],
            ranges={"confidence_score": {"minimum": 0.0, "maximum": 1.0}},
            format_patterns={"ticket_id": r"[A-Z]{3}-\d{4}"},
            string_max_lengths={"summary": 256},
        ),
        severity=RuleSeverity.ERROR,
    )

    assert diagnostics == []
    assert fixed_data is None
    assert applied_fixes == []
