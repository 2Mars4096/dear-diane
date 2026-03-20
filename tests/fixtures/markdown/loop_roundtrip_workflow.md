---
name: Loop Round-Trip Test
description: Loop with until, state, defaults for round-trip verification
format_version: 1
---

## Agents

- [counter](simple_agent.md)
- [incrementer](simple_agent.md)

## Flow

counter | loop(incrementer, until: "count >= 3", max: 10, state: "{\"count\": 0}", defaults: "{\"count\": 0}")
