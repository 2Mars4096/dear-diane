---
name: Review-Revise Pipeline
description: Demonstrates control flow patterns
format_version: 1
---

## Agents

- [writer](simple_agent.md)
- [reviewer](full_agent.md)
- [reviser](simple_agent.md)
- [processor](code_agent.md)
- [approver](human_agent.md)
- [router](router_agent.md)

## Flow

writer | each(processor, parallel: 4)
writer → reviewer
reviewer | loop(reviser, until: "verdict == 'accept'", max: 5)
reviser → approver
router | if("route == 'detailed'", then: writer, else: processor)

## Context

- style_guide: Writing style reference (mode: read)
- bibliography: Accumulated citations (mode: append)
