# CLI

```text
dan serve [--host HOST] [--port PORT] [--no-reload]
dan up [--host HOST|phone] [--port PORT]
dan down
dan editor
dan super-organism [OPTIONS] [OBJECTIVE]
dan super-tui
```

- `serve` runs the Work/Notes and Agent V2 API in the foreground.
- `up` starts the same server in the background and reuses a healthy instance.
- `down` stops the managed background server.
- `editor` launches the GUI development entry point.
- `super-organism` runs the Super DAN plan-only or live command surface.
- `super-tui` opens the durable conversational terminal UI.

Use `dan <command> --help` for command-specific flags. Removed commands such as `code`, `research`, `reader`, `chat`, `run`, `adapter`, `bot`, `publish`, and `blocks` are available only in Git history.
