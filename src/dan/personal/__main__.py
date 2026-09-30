"""Run this checkout's personal organizer locally, with separate persistent state."""
from __future__ import annotations

import argparse
import os
from pathlib import Path


def main():
    checkout = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=4197, help='Loopback port (default: 4197)')
    parser.add_argument('--data-dir', type=Path, default=checkout / '.personal-local', help='Persistent local data directory; defaults to .personal-local in this checkout')
    parser.add_argument('--static-dir', type=Path, default=checkout / 'editor' / 'dist', help='Built browser client directory')
    parser.add_argument('--env-file', type=Path, help='Load existing provider configuration from a private environment file')
    parser.add_argument('--model', help='Explicit provider/model ID for conversation and extraction')
    args = parser.parse_args()
    if args.env_file:
        from dotenv import load_dotenv
        if not args.env_file.expanduser().is_file():
            parser.error('Provider environment file not found')
        load_dotenv(args.env_file.expanduser(), override=False)
    if args.model:
        os.environ['DAN_PERSONAL_MODEL'] = args.model
    if not 1024 <= args.port <= 65535:
        parser.error('--port must be between 1024 and 65535')
    static = args.static_dir.expanduser().resolve()
    if not (static / 'index.html').is_file():
        parser.error('Browser build missing. Run npm run build:verify in editor first.')
    data = args.data_dir.expanduser().resolve()
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(data, 0o700)
    workspace = data / 'workspace'
    workspace.mkdir(exist_ok=True, mode=0o700)
    os.environ.update(DAN_PERSONAL_ENABLED='1', DAN_GRAPHS_DIR=str(data / 'graphs'),
                      DAN_WORKSPACE_ROOT=str(workspace), DAN_STATIC_DIR=str(static))
    print(f'Dear Diane personal: http://127.0.0.1:{args.port}/#personal', flush=True)
    print(f'Persistent data: {data}', flush=True)
    print('Keep this process running for reminders. Ctrl+C stops it; saved records remain.', flush=True)
    import uvicorn
    uvicorn.run('dan.server.app:app', host='127.0.0.1', port=args.port, workers=1)


if __name__ == '__main__':
    main()
