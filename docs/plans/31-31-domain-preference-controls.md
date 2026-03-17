# 31-31: Domain Preference Controls

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** completed
**Goal:** Give users a lightweight command to inspect and edit canonical saved domains without touching profile JSON by hand.

## Tasks
- [x] 1. Add a chat command for saved domain preferences
 - [x] 1-1. Register `/domains` in the canonical command registry with discoverable subcommands
 - [x] 1-2. Implement add/remove/list/known/clear behavior with canonical alias + slug normalization
- [x] 2. Keep live runtime state consistent after manual edits
 - [x] 2-1. Persist `UserProfile.common_domains` updates immediately
 - [x] 2-2. Refresh profile-backed domain facts in `MemoryKernel` after edits
- [x] 3. Add regressions and docs
 - [x] 3-1. Cover direct handler behavior and concierge fast-command dispatch in tests
 - [x] 3-2. Update tracking docs plus CLI/README command references

## Decisions
- The first control surface is a chat fast command instead of a settings UI because it works across CLI, editor chat, and messaging surfaces with minimal wiring.
- `/domains add/remove` treats the full remaining argument as one domain unless commas are present, so users can type multi-word domains like `scientific writing` without quoting.
- Manual profile edits also resync imported `fact:domain:*` memory items so prompt hints and memory retrieval do not temporarily disagree within the same runtime.

## Notes
- Validation: `pytest -q tests/test_concierge/test_domain_preferences.py tests/test_concierge/test_fast_commands.py tests/test_concierge/test_command_registry.py`
