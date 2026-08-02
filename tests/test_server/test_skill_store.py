"""Tests for dan.server.skill_store — IDE-compatible skill loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from dan.server.skill_store import (
    SkillDescriptor,
    SkillStore,
    handle_skill_command,
    parse_skill_md,
)


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

class TestParseSkillMd:
    """Tests for ``parse_skill_md()``."""

    def test_minimal_ide_format(self):
        """Cursor / Claude / Codex minimal frontmatter is accepted."""
        text = (
            "---\n"
            "name: scientific-writer\n"
            "description: Write scientific papers\n"
            "---\n\n"
            "# Scientific Writer\n\nInstructions here."
        )
        desc = parse_skill_md(text)
        assert desc is not None
        assert desc.name == "scientific-writer"
        assert desc.description == "Write scientific papers"
        assert desc.content == "# Scientific Writer\n\nInstructions here."
        assert desc.hyperedge_type == "skill"
        assert desc.hook == "pre_prompt"

    def test_dan_extended_format(self):
        """DAN-specific fields are parsed when present."""
        text = (
            "---\n"
            "name: code-reviewer\n"
            "description: Review code for quality\n"
            "tags: [quality, review]\n"
            "hyperedge_type: guardrail\n"
            "hook: post_output\n"
            "attach_to_tags: [writing, code]\n"
            "attach_to_type: [llm_operator]\n"
            "scope: project\n"
            "---\n\n"
            "Check all outputs for correctness."
        )
        desc = parse_skill_md(text)
        assert desc is not None
        assert desc.tags == ["quality", "review"]
        assert desc.hyperedge_type == "guardrail"
        assert desc.hook == "post_output"
        assert desc.attach_to_tags == ["writing", "code"]
        assert desc.attach_to_type == ["llm_operator"]
        assert desc.scope == "project"

    def test_no_frontmatter_returns_none(self):
        text = "Just markdown without frontmatter.\n"
        assert parse_skill_md(text) is None

    def test_no_name_returns_none(self):
        text = "---\ndescription: no name field\n---\n\nBody.\n"
        assert parse_skill_md(text) is None

    def test_fallback_name(self):
        text = "---\ndescription: has no name\n---\n\nBody.\n"
        desc = parse_skill_md(text, fallback_name="my-skill")
        assert desc is not None
        assert desc.name == "my-skill"

    def test_boolean_fields(self):
        text = (
            "---\n"
            "name: test\n"
            "attach_globally: true\n"
            "enabled: false\n"
            "propagate: yes\n"
            "---\n\nBody.\n"
        )
        desc = parse_skill_md(text)
        assert desc is not None
        assert desc.attach_globally is True
        assert desc.enabled is False
        assert desc.propagate is True

    def test_backward_compat_target_nodes(self):
        text = (
            "---\n"
            "name: test\n"
            "target_nodes: [llm_operator, code_operator]\n"
            "---\n\nBody.\n"
        )
        desc = parse_skill_md(text)
        assert desc is not None
        assert desc.attach_to_type == ["llm_operator", "code_operator"]

    def test_backward_compat_hook_points(self):
        text = (
            "---\n"
            "name: test\n"
            "hook_points: [post_output, validation]\n"
            "---\n\nBody.\n"
        )
        desc = parse_skill_md(text)
        assert desc is not None
        assert desc.hook == "post_output"

    def test_quoted_values(self):
        text = (
            "---\n"
            "name: 'quoted name'\n"
            "description: \"double quoted\"\n"
            "---\n\nBody.\n"
        )
        desc = parse_skill_md(text)
        assert desc is not None
        assert desc.name == "quoted name"
        assert desc.description == "double quoted"

    def test_comment_lines_ignored(self):
        text = (
            "---\n"
            "name: test\n"
            "# This is a comment\n"
            "description: works\n"
            "---\n\nBody.\n"
        )
        desc = parse_skill_md(text)
        assert desc is not None
        assert desc.description == "works"


# ---------------------------------------------------------------------------
# SkillDescriptor model
# ---------------------------------------------------------------------------

class TestSkillDescriptor:
    def test_to_hyperedge_with_tags(self):
        desc = SkillDescriptor(
            name="Test Skill",
            description="A test",
            tags=["writing"],
            content="Do the thing.",
            skill_id="test_skill",
        )
        he = desc.to_hyperedge()
        assert he.id == "skill_test_skill"
        assert he.name == "Test Skill"
        assert he.hyperedge_type == "skill"
        assert he.hook == "pre_prompt"
        assert he.content == "Do the thing."
        assert he.attach_to_tags == ["writing"]
        assert he.attach_globally is False

    def test_to_hyperedge_no_selectors_becomes_global(self):
        desc = SkillDescriptor(
            name="Global Skill",
            content="Global instructions.",
            skill_id="global_skill",
        )
        he = desc.to_hyperedge()
        assert he.attach_globally is True

    def test_to_hyperedge_guardrail(self):
        desc = SkillDescriptor(
            name="Safety Check",
            hyperedge_type="guardrail",
            hook="post_output",
            attach_to_type=["llm_operator"],
            content="Verify output.",
            skill_id="safety",
        )
        he = desc.to_hyperedge()
        assert he.hyperedge_type == "guardrail"
        assert he.hook == "post_output"

    def test_to_library_entry(self):
        desc = SkillDescriptor(
            name="Test",
            description="desc",
            tags=["t"],
            inject_as="system",
            content="body",
        )
        entry = desc.to_library_entry()
        assert entry["name"] == "Test"
        assert entry["text"] == "body"
        assert entry["inject_as"] == "system"


# ---------------------------------------------------------------------------
# SkillStore scanning
# ---------------------------------------------------------------------------

class TestSkillStoreScan:
    def test_scan_directory_layout(self, tmp_path: Path):
        """SKILL.md in subdirectory is discovered."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: My Skill\ndescription: Does things\n---\n\nInstructions.\n"
        )

        store = SkillStore(user_dir=tmp_path)
        count = store.scan()
        assert count == 1
        skills = store.list_skills()
        assert len(skills) == 1
        assert skills[0].name == "My Skill"
        assert skills[0].scope == "user"

    def test_scan_flat_file(self, tmp_path: Path):
        """Flat .md file in directory is discovered."""
        (tmp_path / "quick-skill.md").write_text(
            "---\nname: Quick Skill\n---\n\nDo it quickly.\n"
        )

        store = SkillStore(user_dir=tmp_path)
        assert store.scan() == 1
        assert store.get("quick_skill") is not None

    def test_scan_mixed_layouts(self, tmp_path: Path):
        """Both directory and flat file layouts in one scan."""
        (tmp_path / "flat.md").write_text("---\nname: Flat\n---\n\nFlat body.\n")
        sub = tmp_path / "nested"
        sub.mkdir()
        (sub / "SKILL.md").write_text("---\nname: Nested\n---\n\nNested body.\n")

        store = SkillStore(user_dir=tmp_path)
        assert store.scan() == 2

    def test_project_scope_shadows_user(self, tmp_path: Path):
        """Project-level skill overrides user-level skill with same name."""
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()

        (user_dir / "shared.md").write_text("---\nname: Shared\n---\n\nUser version.\n")
        (proj_dir / "shared.md").write_text("---\nname: Shared\n---\n\nProject version.\n")

        store = SkillStore(user_dir=user_dir, project_dir=proj_dir)
        store.scan()
        desc = store.get("shared")
        assert desc is not None
        assert desc.scope == "project"
        assert "Project version" in desc.content

    def test_empty_dir(self, tmp_path: Path):
        store = SkillStore(user_dir=tmp_path)
        assert store.scan() == 0
        assert store.list_skills() == []

    def test_nonexistent_dir(self, tmp_path: Path):
        store = SkillStore(user_dir=tmp_path / "nonexistent")
        assert store.scan() == 0

    def test_hidden_files_ignored(self, tmp_path: Path):
        (tmp_path / ".hidden.md").write_text("---\nname: Hidden\n---\n\nBody.\n")
        (tmp_path / "_private.md").write_text("---\nname: Private\n---\n\nBody.\n")
        store = SkillStore(user_dir=tmp_path)
        assert store.scan() == 0

    def test_no_frontmatter_files_skipped(self, tmp_path: Path):
        (tmp_path / "readme.md").write_text("Just a README, no frontmatter.\n")
        store = SkillStore(user_dir=tmp_path)
        assert store.scan() == 0


# ---------------------------------------------------------------------------
# SkillStore import
# ---------------------------------------------------------------------------

class TestSkillStoreImport:
    def test_import_directory_with_skill_md(self, tmp_path: Path):
        """Import a Cursor-style skill directory."""
        source = tmp_path / "source" / "my-skill"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(
            "---\nname: My Imported Skill\ndescription: From Cursor\ntags: [imported]\n---\n\nSkill body.\n"
        )
        (source / "reference.md").write_text("Extra reference content.\n")

        user_dir = tmp_path / "dan-skills"
        store = SkillStore(user_dir=user_dir)

        desc = store.import_skill(source)
        assert desc is not None
        assert desc.name == "My Imported Skill"
        assert desc.scope == "user"
        assert (user_dir / "my_imported_skill" / "SKILL.md").exists()
        assert (user_dir / "my_imported_skill" / "reference.md").exists()

    def test_import_flat_md_file(self, tmp_path: Path):
        """Import a single .md file."""
        source = tmp_path / "source" / "quick.md"
        source.parent.mkdir(parents=True)
        source.write_text("---\nname: Quick Import\n---\n\nQuick body.\n")

        user_dir = tmp_path / "dan-skills"
        store = SkillStore(user_dir=user_dir)

        desc = store.import_skill(source)
        assert desc is not None
        assert (user_dir / "quick_import" / "SKILL.md").exists()

    def test_import_nonexistent_path(self, tmp_path: Path):
        store = SkillStore(user_dir=tmp_path)
        assert store.import_skill(tmp_path / "nonexistent") is None

    def test_import_no_frontmatter(self, tmp_path: Path):
        source = tmp_path / "bad.md"
        source.write_text("No frontmatter here.\n")
        store = SkillStore(user_dir=tmp_path / "skills")
        assert store.import_skill(source) is None

    def test_import_dir_without_skill_md_uses_first_md(self, tmp_path: Path):
        """Directory without SKILL.md falls back to first .md file."""
        source = tmp_path / "source" / "custom"
        source.mkdir(parents=True)
        (source / "instructions.md").write_text("---\nname: Custom\n---\n\nBody.\n")

        user_dir = tmp_path / "dan-skills"
        store = SkillStore(user_dir=user_dir)
        desc = store.import_skill(source)
        assert desc is not None
        assert desc.name == "Custom"


# ---------------------------------------------------------------------------
# SkillStore → Hyperedge conversion
# ---------------------------------------------------------------------------

class TestSkillStoreHyperedges:
    def test_get_hyperedges(self, tmp_path: Path):
        (tmp_path / "skill-a.md").write_text(
            "---\nname: Skill A\ntags: [writing]\n---\n\nA instructions.\n"
        )
        (tmp_path / "skill-b.md").write_text(
            "---\nname: Skill B\nhyperedge_type: style\ntags: [latex]\n---\n\nB instructions.\n"
        )

        store = SkillStore(user_dir=tmp_path)
        store.scan()
        hyperedges = store.get_hyperedges()
        assert len(hyperedges) == 2

        names = {he.name for he in hyperedges}
        assert "Skill A" in names
        assert "Skill B" in names

        style_he = next(he for he in hyperedges if he.name == "Skill B")
        assert style_he.hyperedge_type == "style"

    def test_disabled_skill_excluded(self, tmp_path: Path):
        (tmp_path / "disabled.md").write_text(
            "---\nname: Disabled\nenabled: false\n---\n\nBody.\n"
        )
        store = SkillStore(user_dir=tmp_path)
        store.scan()
        assert len(store.get_hyperedges()) == 0
        assert len(store.list_skills()) == 1

    def test_populate_skill_library(self, tmp_path: Path):
        from dan.server.skill_library import SKILL_LIBRARY

        (tmp_path / "new-skill.md").write_text(
            "---\nname: Brand New\n---\n\nBrand new content.\n"
        )
        store = SkillStore(user_dir=tmp_path)
        store.scan()

        key = "brand_new"
        if key in SKILL_LIBRARY:
            del SKILL_LIBRARY[key]

        added = store.populate_skill_library()
        assert added >= 1
        assert key in SKILL_LIBRARY
        assert SKILL_LIBRARY[key]["text"] == "Brand new content."
        del SKILL_LIBRARY[key]


# ---------------------------------------------------------------------------
# SkillStore lookup
# ---------------------------------------------------------------------------

class TestSkillStoreLookup:
    def test_get_by_slug(self, tmp_path: Path):
        (tmp_path / "my-skill.md").write_text("---\nname: My Skill\n---\n\nBody.\n")
        store = SkillStore(user_dir=tmp_path)
        store.scan()
        assert store.get("my_skill") is not None

    def test_get_by_name_case_insensitive(self, tmp_path: Path):
        (tmp_path / "test.md").write_text("---\nname: Test Skill\n---\n\nBody.\n")
        store = SkillStore(user_dir=tmp_path)
        store.scan()
        assert store.get("test skill") is not None
        assert store.get("TEST SKILL") is not None

    def test_get_nonexistent(self, tmp_path: Path):
        store = SkillStore(user_dir=tmp_path)
        store.scan()
        assert store.get("no-such-skill") is None

    def test_list_by_scope(self, tmp_path: Path):
        user_dir = tmp_path / "user"
        proj_dir = tmp_path / "proj"
        user_dir.mkdir()
        proj_dir.mkdir()

        (user_dir / "a.md").write_text("---\nname: User Skill\n---\n\nBody.\n")
        (proj_dir / "b.md").write_text("---\nname: Project Skill\n---\n\nBody.\n")

        store = SkillStore(user_dir=user_dir, project_dir=proj_dir)
        store.scan()
        assert len(store.list_by_scope("user")) == 1
        assert len(store.list_by_scope("project")) == 1


# ---------------------------------------------------------------------------
# IDE compatibility — round-trip
# ---------------------------------------------------------------------------

class TestIDECompatibility:
    """Verify that skills from Cursor, Claude Code, and Codex parse correctly."""

    def test_cursor_skill_format(self):
        text = (
            "---\n"
            "name: code-review\n"
            "description: Review code for design, correctness, complexity, tests, naming, style, and security.\n"
            "---\n\n"
            "# Code Review\n\n"
            "## Instructions\n"
            "Review the code systematically.\n"
        )
        desc = parse_skill_md(text)
        assert desc is not None
        assert desc.name == "code-review"
        assert "Review code" in desc.description
        assert desc.hyperedge_type == "skill"

    def test_claude_superpowers_format(self):
        text = (
            '---\n'
            'name: systematic-debugging\n'
            'description: Use when encountering any bug, test failure, or unexpected behavior\n'
            '---\n\n'
            '# Systematic Debugging\n\n'
            '## Overview\n'
            'Random fixes waste time.\n'
        )
        desc = parse_skill_md(text)
        assert desc is not None
        assert desc.name == "systematic-debugging"

    def test_codex_skill_format(self):
        text = (
            '---\n'
            'name: "playwright"\n'
            'description: "Use when the task requires automating a real browser"\n'
            '---\n\n'
            '# Playwright CLI Skill\n\n'
            'Drive a real browser from the terminal.\n'
        )
        desc = parse_skill_md(text)
        assert desc is not None
        assert desc.name == "playwright"
        assert "automating a real browser" in desc.description

    def test_dan_skill_readable_by_other_ides(self, tmp_path: Path):
        """A DAN skill with extensions still has valid name+description for other IDEs."""
        text = (
            "---\n"
            "name: management-science-writing\n"
            "description: INFORMS Management Science submission guidelines\n"
            "tags: [writing, review]\n"
            "hyperedge_type: skill\n"
            "hook: pre_prompt\n"
            "attach_to_tags: [writing]\n"
            "scope: user\n"
            "---\n\n"
            "# Management Science Writing\n\n"
            "Follow INFORMS conventions.\n"
        )
        desc = parse_skill_md(text)
        assert desc is not None
        assert desc.name == "management-science-writing"
        assert desc.description == "INFORMS Management Science submission guidelines"
        assert desc.tags == ["writing", "review"]
        assert desc.attach_to_tags == ["writing"]


# ---------------------------------------------------------------------------
# /skill command handler
# ---------------------------------------------------------------------------

class TestSkillCommand:
    @pytest.fixture
    def populated_store(self, tmp_path: Path) -> SkillStore:
        (tmp_path / "alpha.md").write_text(
            "---\nname: Alpha\ndescription: First skill\ntags: [a]\n---\n\nAlpha body.\n"
        )
        (tmp_path / "beta.md").write_text(
            "---\nname: Beta\ndescription: Second skill\nhyperedge_type: guardrail\n---\n\nBeta body.\n"
        )
        store = SkillStore(user_dir=tmp_path)
        store.scan()
        return store

    @pytest.mark.asyncio
    async def test_list_command(self, populated_store: SkillStore):
        result = await handle_skill_command("/skill list", populated_store)
        assert "Alpha" in result
        assert "Beta" in result
        assert "2 skill(s)" in result

    @pytest.mark.asyncio
    async def test_list_empty(self, tmp_path: Path):
        store = SkillStore(user_dir=tmp_path)
        store.scan()
        result = await handle_skill_command("/skill list", store)
        assert "No skills found" in result

    @pytest.mark.asyncio
    async def test_info_command(self, populated_store: SkillStore):
        result = await handle_skill_command("/skill info Alpha", populated_store)
        assert "Alpha" in result
        assert "First skill" in result
        assert "skill" in result.lower()

    @pytest.mark.asyncio
    async def test_info_not_found(self, populated_store: SkillStore):
        result = await handle_skill_command("/skill info NonExistent", populated_store)
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_import_command(self, tmp_path: Path):
        source = tmp_path / "source" / "test.md"
        source.parent.mkdir()
        source.write_text("---\nname: Imported\n---\n\nImported body.\n")

        user_dir = tmp_path / "dan-skills"
        store = SkillStore(user_dir=user_dir)

        result = await handle_skill_command(f"/skill import {source}", store)
        assert "Imported" in result

    @pytest.mark.asyncio
    async def test_import_bad_path(self, populated_store: SkillStore):
        result = await handle_skill_command("/skill import /nonexistent/path", populated_store)
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_scan_command(self, populated_store: SkillStore):
        result = await handle_skill_command("/skill scan", populated_store)
        assert "2" in result

    @pytest.mark.asyncio
    async def test_usage_on_empty(self, populated_store: SkillStore):
        result = await handle_skill_command("/skill", populated_store)
        assert "Usage" in result

    @pytest.mark.asyncio
    async def test_list_by_scope(self, tmp_path: Path):
        user_dir = tmp_path / "user"
        proj_dir = tmp_path / "proj"
        user_dir.mkdir()
        proj_dir.mkdir()
        (user_dir / "u.md").write_text("---\nname: User One\n---\n\nBody.\n")
        (proj_dir / "p.md").write_text("---\nname: Proj One\n---\n\nBody.\n")

        store = SkillStore(user_dir=user_dir, project_dir=proj_dir)
        store.scan()
        result = await handle_skill_command("/skill list user", store)
        assert "User One" in result
        assert "Proj One" not in result


# ---------------------------------------------------------------------------
# Extra dirs (legacy DAN_CUSTOM_SKILLS_DIR)
# ---------------------------------------------------------------------------

class TestExtraDirs:
    def test_extra_dir_scanned(self, tmp_path: Path):
        extra = tmp_path / "custom"
        extra.mkdir()
        (extra / "legacy.md").write_text("---\nname: Legacy Skill\n---\n\nLegacy body.\n")

        store = SkillStore(
            user_dir=tmp_path / "empty-user",
            extra_dirs=[extra],
        )
        count = store.scan()
        assert count == 1
        desc = store.get("legacy_skill")
        assert desc is not None
        assert desc.scope == "extra"
