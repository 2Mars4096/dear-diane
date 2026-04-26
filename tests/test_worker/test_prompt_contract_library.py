from __future__ import annotations

from hashlib import sha256

from dan.worker.brief import RoleSpec, render_brief_prompt
from dan.worker.contracts.prompt_context import PromptContext
from dan.worker.contracts.sampling import resolve_sampling_policy
from dan.worker.contracts.snippets import pacing_contract
from dan.worker.contracts.templates import coding_brief, research_brief, review_brief, role_brief, scheduler_brief


def test_pacing_contract_does_not_invent_numeric_caps() -> None:
    text = pacing_contract({})

    assert "1200" not in text
    assert "200" not in text
    assert "file_edit" in text


def test_pacing_contract_uses_policy_supplied_numbers() -> None:
    text = pacing_contract(
        {
            "safe_file_write_word_limit": 777,
            "safe_file_write_line_limit": 88,
        }
    )

    assert "777 words" in text
    assert "88 lines" in text


def test_prompt_context_stable_fingerprint_ignores_dynamic_refs() -> None:
    base = PromptContext(
        prompt_architecture_id="arch",
        system_constitution_id="constitution",
        tool_schema_hash="tools",
        policy_schema_hash="policy",
        output_contract_hash="output",
        slot_values={"task": "same"},
        dynamic_snapshot_refs=["snapshot:a"],
    )
    changed = base.model_copy(update={"dynamic_snapshot_refs": ["snapshot:b"]})

    assert base.stable_fingerprint() == changed.stable_fingerprint()
    assert base.slot_fingerprint() == changed.slot_fingerprint()
    assert base.full_fingerprint() != changed.full_fingerprint()


def test_sampling_policy_resolves_profile_and_overrides() -> None:
    policy = resolve_sampling_policy("creative", temperature=0.33, provider_hints={"reasoning_effort": "low"})

    assert policy.profile == "creative"
    assert policy.temperature == 0.33
    assert policy.provider_hints["reasoning_effort"] == "low"


def test_template_rendered_prompt_snapshots_are_stable() -> None:
    role = RoleSpec(
        role_label="builder",
        responsibility="Build files",
        success_criteria=["done"],
        trace_role="trace.builder",
    )
    rendered = {
        "role_brief": render_brief_prompt(
            role_brief(
                role=role,
                task="Do the generic task.",
                scope="src",
                hard_constraints=["Stay scoped."],
                prompt_slots={"ticket": "P56"},
            )
        ),
        "coding_brief": render_brief_prompt(
            coding_brief(
                role=role,
                task="Patch the module.",
                pacing_policy={"safe_file_write_word_limit": 100, "safe_file_write_line_limit": 20},
                allowed_tool_ids=["file_read", "file_edit"],
            )
        ),
        "review_brief": render_brief_prompt(
            review_brief(
                role={"role_label": "reviewer", "responsibility": "Read only"},
                task="Review the output.",
                allowed_tool_ids=["file_read"],
                failure_phrases=["lorem ipsum"],
            )
        ),
        "research_brief": render_brief_prompt(
            research_brief(
                role={"role_label": "researcher", "responsibility": "Find evidence"},
                task="Research the claim.",
                allowed_tool_ids=["web_search", "file_read"],
            )
        ),
        "scheduler_brief": render_brief_prompt(
            scheduler_brief(
                role={"role_label": "scheduler", "responsibility": "Pick next task"},
                task="Schedule work.",
            )
        ),
    }

    assert {name: sha256(text.encode("utf-8")).hexdigest() for name, text in rendered.items()} == {
        "role_brief": "2ddfcdcf5c1fccf0fe351731426ebd73345f870e59caf2d2849d3f27c6d1b963",
        "coding_brief": "16f7f961ed7d5abafeed3a0df197395823a0e8334622836e79afba275ddecc56",
        "review_brief": "78b4ee9c2415ef565b1ab4ead1a111f3ab06e4c7d9dcdfa27605559ca4d4420e",
        "research_brief": "ca91cbb886b7b6af46c745d189a2e121cec5fe8200ac39964d95b1c9c3adeb71",
        "scheduler_brief": "c6f88f47fef6e0ac9b70b28023085d2d9658be7472d7acd6ccaac1fff61cff81",
    }
    assert "Patch the module." in rendered["coding_brief"]
    assert "Fail if the output still looks like a generic template" in rendered["review_brief"]
    assert "Ground claims in supplied or retrieved evidence" in rendered["research_brief"]
    assert "scheduler-proposal-001" in rendered["scheduler_brief"]


def test_templates_share_snippets_without_duplicate_prompt_text() -> None:
    policy = {"safe_file_write_word_limit": 100, "safe_file_write_line_limit": 20}
    expected = pacing_contract(policy)
    coding = coding_brief(
        role={"role_label": "coding-worker"},
        task="Patch the module.",
        pacing_policy=policy,
    )
    explorer = role_brief(
        role={"role_label": "explorer"},
        task="Inspect the target files.",
        contract_snippets=[expected],
    )

    assert coding.contract_snippets.count(expected) == 1
    assert explorer.contract_snippets.count(expected) == 1
    assert render_brief_prompt(coding).count(expected) == 1
    assert render_brief_prompt(explorer).count(expected) == 1
