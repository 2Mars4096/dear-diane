# LR3 Repo Fixture

This tiny package preserves a Python-2-only surface for benchmark-prep
workflow generation.

The intended migration target is Python 3 behavior:

- `normalize_name("  Alice ") == "alice"`
- `ratio(9, 3) == 3.0`

The current source uses `unicode` and `long`, so the tests fail under Python 3
until the code is migrated.
