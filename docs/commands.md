# Command Reference

The active command surface is intentionally small:

| Command | Purpose |
|---|---|
| `dan serve` / `dan-serve` | Foreground API server |
| `dan up` / `dan-up` | Managed background API server |
| `dan down` / `dan-down` | Stop managed server |
| `dan editor` / `dan-editor` | GUI launcher/development entry |
| `dan super-organism` / `dan-super-organism` | Super DAN execution |
| `dan super-tui` / `dan-super-tui` | Super DAN terminal UI |

The server exposes Work/Notes, session, and Agent V2 APIs only. See [llm-api-guide.md](llm-api-guide.md) for request contracts.
