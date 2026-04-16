# Initial Pack

Frozen DAN Code live-test pack for the first non-website capability battery.

## Operator Setup

- Command surface: `python -m dan.cli.code`
- Workspace shape: isolated `/tmp` workspace per scenario
- Historical baseline model: resolved to `kimi-k2.5`
- Tool budget: `--max-tool-rounds 6`
- Prompt shape: short human brief only
- Reviewer contract: explicit acceptance checklist kept separate from the human prompt

## Scenarios

- `codemodx`
  - Prompt: [codemodx.prompt.md](codemodx.prompt.md)
  - Acceptance: [codemodx.acceptance.md](codemodx.acceptance.md)
- `taskforge`
  - Prompt: [taskforge.prompt.md](taskforge.prompt.md)
  - Acceptance: [taskforge.acceptance.md](taskforge.acceptance.md)
- `termboard`
  - Prompt: [termboard.prompt.md](termboard.prompt.md)
  - Acceptance: [termboard.acceptance.md](termboard.acceptance.md)

## Notes

- These files are the frozen operator inputs for manual reruns of the first DAN Code battery.
- Follow-up change requests are intentionally excluded here; they belong to the later `1-4` adaptation battery.
