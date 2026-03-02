---
name: Paper Writing Pipeline
description: End-to-end academic paper writing workflow with review loop
format_version: 1
tags: [paper-writing, academic, informs]
---

## Agents

- [idea_generator](idea_generator.md)
- [outline_planner](outline_planner.md)
- [lit_searcher](lit_searcher.md)
- [section_writer](section_writer.md)
- [assembler](assembler.md)
- [reviewer](reviewer.md)
- [reviser](reviser.md)
- [human_reviewer](human_reviewer.md)

## Flow

idea_generator → outline_planner
outline_planner → lit_searcher
outline_planner.sections | each(section_writer, parallel: 4)
outline_planner_each_section_writer → assembler
assembler → reviewer
reviewer | loop(reviser, until: "verdict == 'accept'", max: 5, state: "{\"draft\": \"string\", \"verdict\": \"string\", \"comments\": \"string\"}", defaults: "{\"verdict\": \"reject\"}")
reviser → human_reviewer

## Context

- bibliography: Accumulated paper references (mode: append)
- style_guide: INFORMS journal formatting rules (mode: read)
