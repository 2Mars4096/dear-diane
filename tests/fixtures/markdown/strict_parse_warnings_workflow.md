---
name: Strict Parse Warnings Test
description: Workflow with invalid flow line to test strict mode
format_version: 1
tags: [test]
---

## Agents

- [a](simple_agent.md)
- [b](simple_agent.md)

## Flow

a → b
invalid_garbage_line_that_fails_parse
