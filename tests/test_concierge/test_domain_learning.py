from __future__ import annotations

import json
from types import SimpleNamespace

from dan.server.concierge import domain_learning


def test_detect_domain_normalizes_legacy_project_domain():
    project = SimpleNamespace(domain="equity research")
    assert domain_learning.detect_domain("ignored", project=project) == "equity_research"


def test_load_domain_template_falls_back_to_legacy_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(domain_learning, "_USER_TEMPLATE_DIR", tmp_path)
    legacy_path = tmp_path / "scientific writing.json"
    legacy_path.write_text(
        json.dumps(
            {
                "domain": "scientific writing",
                "categories": ["structure"],
                "extraction_prompts": {},
                "checklist": [],
            }
        ),
        encoding="utf-8",
    )

    template = domain_learning.load_domain_template("paper_rendering")

    assert template is not None
    assert template.domain == "paper_rendering"
