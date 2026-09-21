import os
from pathlib import Path

from dan.native_workers import catalog, skills


def make(root: Path, name: str, description="Does things"):
    folder = root / name; folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n")


def setup(monkeypatch, tmp_path):
    home = tmp_path / "home"
    make(home / ".codex/skills", "beamer"); make(home / ".codex/skills", "shared-name"); make(home / ".claude/skills", "shared-name"); make(home / ".claude/skills", "paper-reader")
    (home / ".codex/skills/.hidden").mkdir()
    monkeypatch.setattr(skills, "user_home", lambda: home); monkeypatch.delenv("CODEX_HOME", raising=False)
    return home, tmp_path / "graphs"


def test_pool_links_other_clis_skills_without_touching_their_folders(monkeypatch, tmp_path):
    home, base = setup(monkeypatch, tmp_path)
    before = sorted(p.name for p in (home / ".claude/skills").iterdir())
    pool = skills.build_pool("claude", base)
    links = pool / ".claude/skills"
    assert sorted(p.name for p in links.iterdir()) == ["beamer"]            # same-named skill stays native
    assert os.readlink(links / "beamer") == str((home / ".codex/skills/beamer").resolve())
    assert sorted(p.name for p in (home / ".claude/skills").iterdir()) == before
    assert skills.build_pool("codex", base) is None                          # Codex is never written to


def test_exclusions_and_disable_remove_links(monkeypatch, tmp_path):
    home, base = setup(monkeypatch, tmp_path)
    skills.build_pool("claude", base)
    skills.write_settings(base, True, ["beamer"])
    assert skills.build_pool("claude", base) is None
    assert not (base / "skill_pool/claude/.claude/skills/beamer").is_symlink()
    skills.write_settings(base, False, [])
    assert skills.shared_for("claude", base) == []
    report = skills.report(base)
    assert report["enabled"] is False and {row["name"] for row in report["skills"]} == {"beamer", "shared-name", "paper-reader"}


def test_claude_launch_adds_the_pool(monkeypatch, tmp_path):
    home, base = setup(monkeypatch, tmp_path)
    monkeypatch.setattr(catalog, "binary", lambda runtime: "/bin/claude")
    monkeypatch.setattr(catalog, "accounts", lambda: {"claude": {"default": {"env": {}}}})
    monkeypatch.setattr("dan.server.paths.resolve_graphs_dir", lambda: str(base))
    cmd, _ = catalog.launch("claude", {}, "hello", str(tmp_path))
    assert cmd[cmd.index("--add-dir") + 1] == str(base / "skill_pool/claude")
