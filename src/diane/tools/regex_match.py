"""Built-in tool: apply regex patterns for matching or replacement."""

from __future__ import annotations

import re

TOOL_METADATA = {
    "tool_id": "regex_match",
    "description": (
        "Apply a regular expression pattern to text. In match mode, returns "
        "all matches with capture groups, start, and end positions. "
        "In replacement mode (when 'replacement' is provided), performs "
        "substitution and returns the result."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The text to search or transform.",
            },
            "pattern": {
                "type": "string",
                "description": "Regular expression pattern.",
            },
            "replacement": {
                "type": "string",
                "description": "Replacement string (supports back-references like \\1). If provided, performs substitution instead of matching.",
            },
        },
        "required": ["text", "pattern"],
    },
    "examples": [
        {
            "input": {"text": "Hello World 123", "pattern": r"\d+"},
            "output": {
                "matches": [{"match": "123", "groups": [], "start": 12, "end": 15}],
                "count": 1,
            },
        },
        {
            "input": {"text": "foo bar baz", "pattern": r"\b(\w+)\b", "replacement": r"[\1]"},
            "output": {"result": "[foo] [bar] [baz]", "count": 3},
        },
    ],
    "category": "utility",
    "returns": "dict with matches/count (match mode) or result/count (replacement mode)",
}


async def regex_match(
    text: str,
    pattern: str,
    replacement: str | None = None,
    **_kwargs,
) -> dict:
    try:
        compiled = re.compile(pattern)
    except re.error as e:
        raise ValueError(
            f"Invalid regex pattern '{pattern}': {e}. "
            "Check the pattern syntax and try again."
        )

    if replacement is not None:
        result, count = compiled.subn(replacement, text)
        return {"result": result, "count": count}

    matches = []
    for m in compiled.finditer(text):
        matches.append({
            "match": m.group(),
            "groups": list(m.groups()),
            "start": m.start(),
            "end": m.end(),
        })

    return {"matches": matches, "count": len(matches)}
