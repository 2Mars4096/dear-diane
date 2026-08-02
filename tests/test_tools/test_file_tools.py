"""Tests for file_read, file_write, file_edit, and list_directory tools."""

from __future__ import annotations

import os

import pytest

from dan.tools.file_read import MAX_FILE_SIZE, file_read
from dan.tools.file_edit import file_edit
from dan.tools.file_write import file_write
from dan.tools.list_directory import list_directory
from dan.tools.workspace_check import workspace_check
from dan.worker.context_capsules import build_tool_context_capsules


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("DAN_WORKSPACE_ROOT", str(tmp_path))
    return tmp_path


# ── file_read ──────────────────────────────────────────────────────


class TestFileRead:
    @pytest.mark.asyncio
    async def test_read_simple(self, workspace):
        f = workspace / "hello.txt"
        f.write_text("line1\nline2\nline3\n")
        result = await file_read(path="hello.txt")
        assert result["line_count"] == 3
        assert "line1" in result["content"]

    @pytest.mark.asyncio
    async def test_read_line_range(self, workspace):
        f = workspace / "lines.txt"
        f.write_text("a\nb\nc\nd\ne\n")
        result = await file_read(path="lines.txt", start_line=2, end_line=4)
        assert result["line_count"] == 3
        assert result["returned_line_count"] == 3
        assert result["total_line_count"] == 5
        assert result["line_start"] == 2
        assert result["line_end"] == 4
        assert result["content"] == "b\nc\nd\n"

    @pytest.mark.asyncio
    async def test_file_not_found(self, workspace):
        with pytest.raises(FileNotFoundError, match="not found"):
            await file_read(path="nope.txt")

    @pytest.mark.asyncio
    async def test_max_size_guard(self, workspace):
        f = workspace / "big.txt"
        f.write_bytes(b"x" * (MAX_FILE_SIZE + 1))
        with pytest.raises(ValueError, match="limit"):
            await file_read(path="big.txt")

    @pytest.mark.asyncio
    async def test_rejects_path_traversal(self):
        with pytest.raises(ValueError, match="outside the workspace"):
            await file_read(path="../../etc/passwd")

    @pytest.mark.asyncio
    async def test_absolute_path_outside_root_resolves(self):
        """Explicit absolute paths are allowed by validate_path (only relative paths are sandboxed)."""
        with pytest.raises(FileNotFoundError):
            await file_read(path="/nonexistent_test_path_abc123/file.txt")

    @pytest.mark.asyncio
    async def test_workspace_alias_absolute_path_reads_from_workspace(self, workspace):
        f = workspace / "hello.txt"
        f.write_text("line1\nline2\n")
        result = await file_read(path="/workspace/hello.txt")
        assert result["line_count"] == 2
        assert result["content"] == "line1\nline2\n"


# ── workspace_check ─────────────────────────────────────────────────


class TestWorkspaceCheck:
    @pytest.mark.asyncio
    async def test_exists_reports_all_paths(self, workspace):
        (workspace / "index.html").write_text("<html></html>", encoding="utf-8")

        result = await workspace_check(
            check="exists",
            paths=["index.html", "missing.css"],
        )

        assert result["check"] == "exists"
        assert result["passed"] is False
        assert result["results"][0]["exists"] is True
        assert result["results"][0]["is_file"] is True
        assert result["results"][1]["exists"] is False

    @pytest.mark.asyncio
    async def test_html_tags_counts_start_tags_with_attributes(self, workspace):
        (workspace / "index.html").write_text(
            '<!doctype html>\n<html lang="en"><head></head><body><main></main></body></html>\n',
            encoding="utf-8",
        )

        result = await workspace_check(
            check="html_tags",
            path="index.html",
            tags=["html", "head", "body", "main"],
        )

        assert result["passed"] is True
        assert result["tags"]["html"] == {"start": 1, "end": 1, "balanced": True}
        assert result["tags"]["main"] == {"start": 1, "end": 1, "balanced": True}

    @pytest.mark.asyncio
    async def test_html_tags_flags_duplicate_document_roots(self, workspace):
        (workspace / "index.html").write_text(
            "<html><body></body></html><html><body></body></html>",
            encoding="utf-8",
        )

        result = await workspace_check(
            check="html_tags",
            path="index.html",
            tags=["html", "body"],
        )

        assert result["passed"] is False
        assert any("duplicate document tag" in issue for issue in result["issues"])

    @pytest.mark.asyncio
    async def test_literal_and_regex_counts(self, workspace):
        (workspace / "index.html").write_text(
            "<section></section>\n<section></section>\n",
            encoding="utf-8",
        )

        literal = await workspace_check(
            check="literal_count",
            path="index.html",
            patterns=["<section>"],
        )
        regex = await workspace_check(
            check="regex_count",
            path="index.html",
            patterns=[r"</?section>"],
        )

        assert literal["counts"] == [{"pattern": "<section>", "count": 2}]
        assert regex["counts"] == [{"pattern": r"</?section>", "count": 4}]

    @pytest.mark.asyncio
    async def test_syntax_checks_python_and_json(self, workspace):
        (workspace / "module.py").write_text("def broken(:\n", encoding="utf-8")
        (workspace / "data.json").write_text('{"ok": true}', encoding="utf-8")

        python_result = await workspace_check(check="syntax", path="module.py")
        json_result = await workspace_check(check="syntax", path="data.json")

        assert python_result["passed"] is False
        assert python_result["syntax"] == "python"
        assert "line 1" in python_result["error"]
        assert json_result["passed"] is True
        assert json_result["syntax"] == "json"

    @pytest.mark.asyncio
    async def test_syntax_checks_gdscript_shape(self, workspace):
        (workspace / "script.gd").write_text(
            "extends Node3D\n\nfunc _ready\n\tpass\n",
            encoding="utf-8",
        )

        result = await workspace_check(check="syntax", path="script.gd")

        assert result["passed"] is False
        assert result["syntax"] == "gdscript"
        assert "malformed function header" in result["error"]

    @pytest.mark.asyncio
    async def test_syntax_checks_css_shape(self, workspace):
        (workspace / "styles.css").write_text(
            "body {\n  color: white;\n}\n.card {\n  padding: 1rem;\n",
            encoding="utf-8",
        )

        result = await workspace_check(check="syntax", path="styles.css")

        assert result["passed"] is False
        assert result["syntax"] == "css"
        assert "unclosed CSS rule block" in result["error"]

    @pytest.mark.asyncio
    async def test_syntax_auto_for_unsupported_file_type_is_informational(
        self, workspace
    ):
        (workspace / "README.md").write_text(
            "# Project\n\nPlain markdown.\n", encoding="utf-8"
        )

        result = await workspace_check(check="syntax", path="README.md")

        assert result["passed"] is None
        assert result["unsupported"] is True
        assert result["syntax"] == "unsupported"
        assert result["requested_syntax"] == "unsupported"
        assert "No deterministic workspace_check syntax profile" in result["warning"]

    @pytest.mark.asyncio
    async def test_syntax_checks_duplicate_gdscript_member_variables(self, workspace):
        (workspace / "script.gd").write_text(
            (
                "extends Node3D\n\n"
                "@export var speed: float = 1.0\n"
                "var _state: int = 0\n"
                "var _state: int = 1\n"
                "func _ready() -> void:\n"
                "\tpass\n"
            ),
            encoding="utf-8",
        )

        result = await workspace_check(check="syntax", path="script.gd")

        assert result["passed"] is False
        assert result["syntax"] == "gdscript"
        assert "duplicate member variable declarations" in result["error"]

    @pytest.mark.asyncio
    async def test_syntax_checks_duplicate_gdscript_class_name(self, workspace):
        (workspace / "store.gd").write_text(
            "class_name Store\n\nclass_name Store\n\nvar hp: int = 1\n",
            encoding="utf-8",
        )

        result = await workspace_check(check="syntax", path="store.gd")

        assert result["passed"] is False
        assert result["syntax"] == "gdscript"
        assert "duplicate class_name declarations" in result["error"]

    @pytest.mark.asyncio
    async def test_syntax_checks_unexpected_gdscript_indentation(self, workspace):
        (workspace / "script.gd").write_text(
            (
                "extends Node3D\n\n"
                "func _input(event: InputEvent) -> void:\n"
                "\telif event is InputEventMouseMotion:\n"
                "\t\t_drag_end = event.position\n"
                "\t\t\t_cmd_mode = CommandMode.MOVE\n"
            ),
            encoding="utf-8",
        )

        result = await workspace_check(check="syntax", path="script.gd")

        assert result["passed"] is False
        assert result["syntax"] == "gdscript"
        assert "unexpected indentation" in result["error"]

    def test_workspace_check_emits_validation_capsule(self):
        capsules = build_tool_context_capsules(
            {
                "tool_id": "workspace_check",
                "tool_call_id": "tool-1",
                "ok": True,
                "arguments": {"check": "html_tags", "path": "index.html"},
                "result": {
                    "check": "html_tags",
                    "path": "index.html",
                    "passed": True,
                    "tags": {"html": {"start": 1, "end": 1, "balanced": True}},
                },
            }
        )

        assert len(capsules) == 1
        capsule = capsules[0]
        assert capsule.kind == "validation_result"
        assert capsule.artifact_state == "validated"
        assert "validation:deterministic_check_passed" in capsule.unlocks


# ── file_write ─────────────────────────────────────────────────────


class TestFileWrite:
    @pytest.mark.asyncio
    async def test_overwrite(self, workspace):
        result = await file_write(path="out.txt", content="hello")
        assert result["bytes_written"] == 5
        assert result["mode"] == "overwrite"
        assert (workspace / "out.txt").read_text() == "hello"

    @pytest.mark.asyncio
    async def test_append(self, workspace):
        (workspace / "log.txt").write_text("first\n")
        await file_write(path="log.txt", content="second\n", mode="append")
        assert (workspace / "log.txt").read_text() == "first\nsecond\n"

    @pytest.mark.asyncio
    async def test_creates_parent_dirs(self, workspace):
        await file_write(path="a/b/c/deep.txt", content="nested")
        assert (workspace / "a" / "b" / "c" / "deep.txt").read_text() == "nested"

    @pytest.mark.asyncio
    async def test_rejects_path_traversal(self):
        with pytest.raises(ValueError, match="outside the workspace"):
            await file_write(path="../../evil.txt", content="bad")

    @pytest.mark.asyncio
    async def test_invalid_mode(self, workspace):
        with pytest.raises(ValueError, match="Invalid mode"):
            await file_write(path="x.txt", content="y", mode="delete")

    @pytest.mark.asyncio
    async def test_rejects_syntax_invalid_overwrite_of_existing_python_file(
        self, workspace
    ):
        target = workspace / "module.py"
        original = """def alpha():
    return 1


def beta():
    return alpha()
"""
        target.write_text(original, encoding="utf-8")

        with pytest.raises(ValueError, match="tool_arguments_invalid:") as excinfo:
            await file_write(
                path="module.py",
                content="""def alpha(
    return 1
""",
            )

        assert "syntactically invalid" in str(excinfo.value)
        assert target.read_text(encoding="utf-8") == original

    @pytest.mark.asyncio
    async def test_allows_valid_overwrite_of_existing_python_file(self, workspace):
        target = workspace / "module.py"
        target.write_text(
            """def alpha():
    return 1
""",
            encoding="utf-8",
        )

        result = await file_write(
            path="module.py",
            content="""def alpha():
    return 2
""",
        )

        assert result["mode"] == "overwrite"
        assert target.read_text(encoding="utf-8") == "def alpha():\n    return 2\n"

    @pytest.mark.asyncio
    async def test_rejects_suspicious_source_file_shrink_overwrite(self, workspace):
        target = workspace / "script.gd"
        original = (
            "\n".join(f"func section_{i}() -> void:\n\tpass" for i in range(60)) + "\n"
        )
        target.write_text(original, encoding="utf-8")

        with pytest.raises(
            ValueError, match="suspicious full-file overwrite"
        ) as excinfo:
            await file_write(
                path="script.gd",
                content="extends Node3D\n\n",
            )

        assert "from 120 lines to 2 lines" in str(excinfo.value)
        assert target.read_text(encoding="utf-8") == original

    @pytest.mark.asyncio
    async def test_rejects_duplicate_gdscript_function_overwrite(self, workspace):
        target = workspace / "script.gd"

        with pytest.raises(
            ValueError, match="duplicate function definitions"
        ) as excinfo:
            await file_write(
                path="script.gd",
                content=(
                    "extends Node3D\n\n"
                    "func _ready() -> void:\n"
                    "\tpass\n\n"
                    "func _ready() -> void:\n"
                    '\tprint("again")\n'
                ),
            )

        assert "tool_arguments_invalid:" in str(excinfo.value)
        assert not target.exists()


# ── file_edit ──────────────────────────────────────────────────────


class TestFileEdit:
    @pytest.mark.asyncio
    async def test_replace_clamps_end_line_past_eof(self, workspace):
        target = workspace / "script.js"
        target.write_text("const a = 1;\nconst b = 2;\n", encoding="utf-8")

        result = await file_edit(
            path="script.js",
            start_line=1,
            end_line=3,
            content="const c = 3;\n",
            mode="replace",
        )

        assert result["end_line"] == 2
        assert target.read_text(encoding="utf-8") == "const c = 3;\n"

    @pytest.mark.asyncio
    async def test_rejects_suspicious_bulk_replace_collapse(self, workspace):
        target = workspace / "module.py"
        original = "".join(f"line {index}\n" for index in range(1, 41))
        target.write_text(original, encoding="utf-8")

        with pytest.raises(ValueError, match="Suspicious bulk replace") as excinfo:
            await file_edit(
                path="module.py",
                start_line=10,
                end_line=35,
                content="READING",
                mode="replace",
            )

        assert "tool_arguments_invalid:" in str(excinfo.value)
        assert target.read_text(encoding="utf-8") == original

    @pytest.mark.asyncio
    async def test_rejects_suspicious_source_shrink_edit(self, workspace):
        target = workspace / "script.gd"
        original = (
            "\n".join(f"func section_{i}() -> void:\n\tpass" for i in range(60)) + "\n"
        )
        target.write_text(original, encoding="utf-8")

        with pytest.raises(ValueError, match="suspicious source shrink") as excinfo:
            await file_edit(
                path="script.gd",
                start_line=1,
                end_line=120,
                content="extends Node3D\n\nfunc _ready() -> void:\n\tpass\n",
                mode="replace",
            )

        assert "from 120 lines to 4 lines" in str(excinfo.value)
        assert target.read_text(encoding="utf-8") == original

    @pytest.mark.asyncio
    async def test_rejects_empty_gdscript_control_block_edit(self, workspace):
        target = workspace / "script.gd"
        original = (
            "extends Node3D\n\n"
            "func _ready() -> void:\n"
            "\tpass\n\n"
            "func _physics_process(delta: float) -> void:\n"
            "\tif active:\n"
            "\t\tposition.x += delta\n"
            '\tprint("tick")\n'
        )
        target.write_text(original, encoding="utf-8")

        with pytest.raises(ValueError, match="empty control block") as excinfo:
            await file_edit(
                path="script.gd",
                start_line=7,
                end_line=8,
                content=("\tif active:\n" "func _start_round() -> void:\n" "\tpass\n"),
                mode="replace",
            )

        assert "tool_arguments_invalid:" in str(excinfo.value)
        assert target.read_text(encoding="utf-8") == original

    @pytest.mark.asyncio
    async def test_rejects_malformed_gdscript_function_header_edit(self, workspace):
        target = workspace / "script.gd"
        original = (
            "extends Node3D\n\n"
            "func _find_child_by_class(node: Node, class_name_str: String) -> Node:\n"
            "\treturn null\n"
        )
        target.write_text(original, encoding="utf-8")

        with pytest.raises(ValueError, match="malformed function header") as excinfo:
            await file_edit(
                path="script.gd",
                start_line=3,
                end_line=3,
                content="func _find_child_by_class\n",
                mode="replace",
            )

        assert "tool_arguments_invalid:" in str(excinfo.value)
        assert target.read_text(encoding="utf-8") == original

    @pytest.mark.asyncio
    async def test_rejects_duplicate_gdscript_local_variable_edit(self, workspace):
        target = workspace / "script.gd"
        original = (
            "extends Node3D\n\n"
            "func _spawn_team(count: int) -> void:\n"
            "\tfor i in range(count):\n"
            "\t\tvar offset := Vector3(i, 0, 0)\n"
            "\t\tprint(offset)\n"
        )
        target.write_text(original, encoding="utf-8")

        with pytest.raises(ValueError, match="duplicate local variable") as excinfo:
            await file_edit(
                path="script.gd",
                start_line=5,
                end_line=6,
                content=(
                    "\t\tvar offset := Vector3(i, 0, 0)\n"
                    "\t\tprint(offset)\n"
                    "\t\tvar offset := Vector3(i, 0, 1)\n"
                    "\t\tprint(offset)\n"
                ),
                mode="replace",
            )

        assert "offset at line 7" in str(excinfo.value)
        assert target.read_text(encoding="utf-8") == original

    @pytest.mark.asyncio
    async def test_rejects_unreachable_gdscript_statement_after_return_edit(
        self, workspace
    ):
        target = workspace / "script.gd"
        original = (
            "extends RefCounted\n\n"
            "func _pack_id(slot: int, gen: int) -> int:\n"
            "\treturn (gen << 16) | slot\n\n"
            "func _slot_from_id(id: int) -> int:\n"
            "\treturn id & 0xFFFF\n"
        )
        target.write_text(original, encoding="utf-8")

        with pytest.raises(
            ValueError, match="unreachable statement after return"
        ) as excinfo:
            await file_edit(
                path="script.gd",
                start_line=4,
                end_line=4,
                content=("\treturn (gen << 16) | slot\n" "\trange_sq[slot] = 144.0\n"),
                mode="replace",
            )

        assert "line(s): 5" in str(excinfo.value)
        assert target.read_text(encoding="utf-8") == original

    @pytest.mark.asyncio
    async def test_rejects_structural_python_block_removal(self, workspace):
        target = workspace / "module.py"
        original = """def alpha():
    return 1


def beta():
    value = 2
    value += 1
    value += 1
    value += 1
    value += 1
    value += 1
    value += 1
    value += 1
    value += 1
    value += 1
    value += 1
    value += 1
    return value


def gamma():
    value = 3
    value += 1
    value += 1
    value += 1
    value += 1
    return value
"""
        target.write_text(original, encoding="utf-8")

        with pytest.raises(
            ValueError, match="Suspicious structural replace"
        ) as excinfo:
            await file_edit(
                path="module.py",
                start_line=5,
                end_line=24,
                content="""def beta():
    value = 20
    value += 1
    value += 1
    value += 1
    value += 1
    value += 1
    return value
""",
                mode="replace",
            )

        assert "tool_arguments_invalid:" in str(excinfo.value)
        assert target.read_text(encoding="utf-8") == original

    @pytest.mark.asyncio
    async def test_rejects_missing_content_for_insert_mode(self, workspace):
        target = workspace / "module.py"
        target.write_text("alpha\nbeta\n", encoding="utf-8")

        with pytest.raises(ValueError, match="tool_arguments_invalid:") as excinfo:
            await file_edit(
                path="module.py",
                start_line=1,
                mode="insert_before",
            )

        assert "content" in str(excinfo.value)
        assert target.read_text(encoding="utf-8") == "alpha\nbeta\n"

    @pytest.mark.asyncio
    async def test_rejects_mixing_batched_and_top_level_arguments(self, workspace):
        target = workspace / "module.py"
        target.write_text("alpha\nbeta\n", encoding="utf-8")

        with pytest.raises(ValueError, match="tool_arguments_invalid:") as excinfo:
            await file_edit(
                path="module.py",
                start_line=1,
                mode="insert_before",
                edits=[
                    {"start_line": 1, "mode": "insert_before", "content": "# note\n"}
                ],
            )

        assert "not both" in str(excinfo.value)
        assert target.read_text(encoding="utf-8") == "alpha\nbeta\n"

    @pytest.mark.asyncio
    async def test_batch_edit_accepts_replace_alias_for_content(self, workspace):
        target = workspace / "module.py"
        target.write_text("alpha\nbeta\n", encoding="utf-8")

        result = await file_edit(
            path="module.py",
            edits=[{"start_line": 2, "end_line": 2, "replace": "patched\n"}],
        )

        assert result["changed"] is True
        assert target.read_text(encoding="utf-8") == "alpha\npatched\n"

    @pytest.mark.asyncio
    async def test_batch_edit_accepts_old_string_new_string_compatibility(
        self, workspace
    ):
        target = workspace / "page.html"
        target.write_text(
            "<header>\n  <a>Old</a>\n</header>\n<main>\n  <h1>Hello</h1>\n</main>\n",
            encoding="utf-8",
        )

        result = await file_edit(
            path="page.html",
            edits=[
                {
                    "old_string": "<header>\n  <a>Old</a>\n</header>",
                    "new_string": "<header>\n  <a>New</a>\n</header>",
                },
                {
                    "old_string": "  <h1>Hello</h1>",
                    "new_string": "  <h1>Hello world</h1>",
                },
            ],
        )

        assert result["changed"] is True
        assert target.read_text(encoding="utf-8") == (
            "<header>\n  <a>New</a>\n</header>\n<main>\n  <h1>Hello world</h1>\n</main>\n"
        )

    @pytest.mark.asyncio
    async def test_batch_edit_missing_anchor_reports_recoverable_shape(self, workspace):
        target = workspace / "module.py"
        target.write_text("alpha\nbeta\n", encoding="utf-8")

        with pytest.raises(ValueError, match="tool_arguments_invalid:") as excinfo:
            await file_edit(
                path="module.py",
                edits=[{"content": "patched\n", "mode": "replace"}],
            )

        assert "edits[0] must include start_line or old_string" in str(excinfo.value)
        assert target.read_text(encoding="utf-8") == "alpha\nbeta\n"

    @pytest.mark.asyncio
    async def test_rejects_empty_batch_edits(self, workspace):
        target = workspace / "module.py"
        target.write_text("alpha\nbeta\n", encoding="utf-8")

        with pytest.raises(
            ValueError, match="edits must be a non-empty list"
        ) as excinfo:
            await file_edit(path="module.py", edits=[])

        assert "tool_arguments_invalid:" in str(excinfo.value)
        assert target.read_text(encoding="utf-8") == "alpha\nbeta\n"

    @pytest.mark.asyncio
    async def test_rejects_delete_mode_with_replacement_content(self, workspace):
        target = workspace / "module.py"
        target.write_text("alpha\nbeta\n", encoding="utf-8")

        with pytest.raises(ValueError, match="delete mode") as excinfo:
            await file_edit(
                path="module.py",
                start_line=1,
                end_line=1,
                mode="delete",
                content="ALPHA\n",
            )

        assert "swap text" in str(excinfo.value)
        assert target.read_text(encoding="utf-8") == "alpha\nbeta\n"

    @pytest.mark.asyncio
    async def test_rejects_compatibility_replacement_with_non_replace_mode(
        self, workspace
    ):
        target = workspace / "module.py"
        target.write_text("alpha\nbeta\n", encoding="utf-8")

        with pytest.raises(ValueError, match="compatibility form") as excinfo:
            await file_edit(
                path="module.py",
                mode="insert_before",
                old_string="alpha\n",
                new_string="ALPHA\n",
            )

        assert "old_string plus new_string/content" in str(excinfo.value)
        assert target.read_text(encoding="utf-8") == "alpha\nbeta\n"

    @pytest.mark.asyncio
    async def test_rejects_batched_delete_mode_with_new_string(self, workspace):
        target = workspace / "module.py"
        target.write_text("alpha\nbeta\n", encoding="utf-8")

        with pytest.raises(ValueError, match="delete mode") as excinfo:
            await file_edit(
                path="module.py",
                edits=[
                    {
                        "start_line": 1,
                        "end_line": 1,
                        "mode": "delete",
                        "old_string": "alpha\n",
                        "new_string": "ALPHA\n",
                    }
                ],
            )

        assert "edits[0]" in str(excinfo.value)
        assert target.read_text(encoding="utf-8") == "alpha\nbeta\n"

    @pytest.mark.asyncio
    async def test_rejects_placeholder_style_python_replace(self, workspace):
        target = workspace / "module.py"
        original = """# Licensed under a 3-clause BSD style license - see LICENSE.rst

def alpha():
    return 1
"""
        target.write_text(original, encoding="utf-8")

        with pytest.raises(
            ValueError, match="Suspicious placeholder-style content"
        ) as excinfo:
            await file_edit(
                path="module.py",
                start_line=1,
                end_line=1,
                content="# git restore placeholder - use git diff\n",
                mode="replace",
            )

        assert "tool_arguments_invalid:" in str(excinfo.value)
        assert target.read_text(encoding="utf-8") == original

    @pytest.mark.asyncio
    async def test_rejects_placeholder_style_comment_replace_for_non_python_files(
        self, workspace
    ):
        target = workspace / "styles.css"
        original = "body {\n  color: white;\n}\n"
        target.write_text(original, encoding="utf-8")

        with pytest.raises(
            ValueError, match="Suspicious placeholder-style content"
        ) as excinfo:
            await file_edit(
                path="styles.css",
                start_line=1,
                end_line=99999,
                content="/* Read current content */",
                mode="replace",
            )

        assert "tool_arguments_invalid:" in str(excinfo.value)
        assert target.read_text(encoding="utf-8") == original

    @pytest.mark.asyncio
    async def test_allows_normal_python_comment_replace(self, workspace):
        target = workspace / "module.py"
        target.write_text(
            """# original comment

def alpha():
    return 1
""",
            encoding="utf-8",
        )

        result = await file_edit(
            path="module.py",
            start_line=1,
            end_line=1,
            content="# updated comment\n",
            mode="replace",
        )

        assert result["path"] == "module.py"
        assert result["changed"] is True
        assert result["no_op"] is False
        assert target.read_text(encoding="utf-8").startswith("# updated comment\n")

    @pytest.mark.asyncio
    async def test_noop_replace_reports_no_change(self, workspace):
        target = workspace / "module.py"
        original = "# stable comment\n\ndef alpha():\n    return 1\n"
        target.write_text(original, encoding="utf-8")

        result = await file_edit(
            path="module.py",
            start_line=1,
            end_line=1,
            content="# stable comment\n",
            mode="replace",
        )

        assert result["path"] == "module.py"
        assert result["changed"] is False
        assert result["no_op"] is True
        assert target.read_text(encoding="utf-8") == original


# ── list_directory ─────────────────────────────────────────────────


class TestListDirectory:
    @pytest.mark.asyncio
    async def test_list_files(self, workspace):
        (workspace / "a.txt").write_text("a")
        (workspace / "b.py").write_text("b")
        result = await list_directory(path=".")
        assert result["count"] == 2

    @pytest.mark.asyncio
    async def test_glob_filter(self, workspace):
        (workspace / "a.txt").write_text("a")
        (workspace / "b.py").write_text("b")
        result = await list_directory(path=".", glob_pattern="*.py")
        assert result["count"] == 1
        assert result["entries"][0]["name"] == "b.py"

    @pytest.mark.asyncio
    async def test_recursive(self, workspace):
        sub = workspace / "sub"
        sub.mkdir()
        (sub / "deep.txt").write_text("deep")
        (workspace / "top.txt").write_text("top")
        result = await list_directory(path=".", recursive=True)
        names = {e["name"] for e in result["entries"]}
        assert "deep.txt" in names
        assert "sub" in names

    @pytest.mark.asyncio
    async def test_directory_not_found(self, workspace):
        with pytest.raises(FileNotFoundError, match="not found"):
            await list_directory(path="nope")

    @pytest.mark.asyncio
    async def test_rejects_path_traversal(self):
        with pytest.raises(ValueError, match="outside the workspace"):
            await list_directory(path="../../")

    @pytest.mark.asyncio
    async def test_workspace_alias_absolute_path_lists_workspace(self, workspace):
        (workspace / "a.txt").write_text("a")
        result = await list_directory(path="/workspace")
        assert result["count"] == 1
        assert result["entries"][0]["name"] == "a.txt"
