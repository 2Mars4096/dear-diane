"""CLI entry point: ``python -m dan.server`` or ``dan-serve``."""

import argparse
from pathlib import Path

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="DAN visual editor server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--reload",
        action="store_true",
        default=True,
        help="Auto-reload on Python/workflow changes (default: on)",
    )
    parser.add_argument("--no-reload", action="store_true", help="Disable auto-reload (for production)")
    args = parser.parse_args()
    reload = args.reload and not args.no_reload
    # Watch project root so changes in src/, examples/, graphs/ trigger restart
    reload_dirs = [str(Path(__file__).resolve().parents[3])] if reload else None
    uvicorn.run(
        "dan.server.app:app",
        host=args.host,
        port=args.port,
        reload=reload,
        reload_dirs=reload_dirs,
    )


if __name__ == "__main__":
    main()
