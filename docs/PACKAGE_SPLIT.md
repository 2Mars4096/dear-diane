# Package Split Plan

Current state: **single `dan` package** with optional dependency groups. This document records the planned split boundary for when separate PyPI packages become necessary.

## Split Boundary

| Package | Modules | Dependencies |
|---------|---------|--------------|
| `dan-core` | `dan.engine`, `dan.builder`, `dan.loader`, `dan.meta`, `dan.models`, `dan.validation`, `dan.registry`, `dan.providers`, `dan.tools`, `dan.rag`, `dan.utils` | `pydantic`, `openai`, `httpx`, `tiktoken` |
| `dan-server` | `dan.server` + editor static assets (`editor/dist/`) | `dan-core`, `fastapi`, `uvicorn`, `websockets` |
| `dan-cli` | `dan.cli` | `dan-core`, `rich` (optional) |
| `dan-publish` | `dan.publish` | `dan-core`, `mcp` (optional) |
| `dan-adapters` | `dan.adapters` | `dan-core`, `python-telegram-bot`, `aiosmtplib` (all optional) |
| `dan-blocks` | `dan.blocks` | `dan-core` |

## Current Approach

For v0.1.x, everything ships as a single `dan` package. Optional dependency groups (`[cli]`, `[mcp]`, `[messaging]`) control what's installed:

```bash
pip install dan            # core + server (FastAPI is core for now)
pip install dan[cli]       # + rich TUI
pip install dan[mcp]       # + MCP SDK
pip install dan[messaging] # + telegram + email adapters
pip install dan[all]       # everything
```

## When to Split

Split into separate packages when:
- Users want `dan-core` without the server's FastAPI/uvicorn overhead
- Package size becomes a concern (editor static assets)
- Different release cadences for core vs. tooling

## Rename Procedure

The package name `dan` is a placeholder. To rename:
1. Change `name` in `pyproject.toml`
2. Rename `src/dan/` directory
3. Update all internal imports (grep for `from dan.` and `import dan.`)
4. Update entry points in `pyproject.toml`
5. Update documentation
