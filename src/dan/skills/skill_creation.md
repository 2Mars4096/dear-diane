---
type: skill
name: DAN Skill Creation
hook: pre_prompt
attach_to_tags: [skill, skills, authoring, capability]
---
Create or adapt DAN-compatible skills as concise SKILL.md folders.

- Use YAML frontmatter with at least `name` and `description`; make the description clear about when the skill should trigger.
- Keep `SKILL.md` focused on essential workflow guidance. Move large references, examples, templates, or assets into sibling `references/`, `scripts/`, or `assets/` folders.
- Prefer interoperable fields shared by Codex, Claude Code, Cursor, and DAN; DAN-specific fields such as `tags` or `attach_to_*` are optional extensions.
- Treat imported external skills as source material first: preserve their intent, adapt only what is needed for DAN's brief-driven runtime, and record provenance.
- Skills may guide behavior, standards, and review criteria, but they must not silently expand tool permissions, workspace access, or safety boundaries.
