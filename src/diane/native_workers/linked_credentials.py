"""Publish file locations for same-user linked apps, never credential values."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from diane._atomic_file import atomic_write_text
from diane.server.paths import resolve_graphs_dir


def publish_locations(env_file: str = "") -> None:
    """Desktop-only discovery. Existing credentials and .env are read in place."""
    if os.environ.get("DAN_DESKTOP_LINKED_CREDENTIALS") != "1":
        return
    home = Path.home()
    if sys.platform == "darwin":
        import pwd
        home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    target = home / ".dan" / "linked-credentials.json"
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = {"schema_version": 1,
            "saved_keys_file": str((Path(resolve_graphs_dir()) / "model_credentials/keys.json").resolve()),
            "env_file": str(Path(env_file).resolve()) if env_file else None}
    atomic_write_text(target, json.dumps(data, indent=2), mode=0o600)
