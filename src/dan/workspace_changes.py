"""Bounded local text snapshots for review. Never touches Git's index or user files."""
from __future__ import annotations
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import time
from dan._atomic_file import atomic_write_text

LIMIT = 2_000_000
TOTAL = 20_000_000


def folder(base: Path, root: str, thread: str) -> Path:
    key = hashlib.sha256(f'{Path(root).resolve()}\0{thread}'.encode()).hexdigest()
    return base / 'workspace_changes' / key


def texts(root: str) -> tuple[dict, list]:
    directory = Path(root).expanduser().resolve()
    result = subprocess.run(['git', '-C', str(directory), 'ls-files', '-z', '--cached', '--others', '--exclude-standard', '--', '.'], capture_output=True, timeout=10)
    if result.returncode:
        raise ValueError('Changes currently requires a Git project folder.')
    files, skipped, total = {}, [], 0
    names = sorted(set(result.stdout.decode('utf-8', errors='replace').split('\0')) - {''})
    for name in names:
        path = directory / name
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(directory) or len(files) >= 3000:
            skipped.append(name); continue
        try:
            size = path.stat().st_size
            if size > LIMIT or total + size > TOTAL:
                skipped.append(name); continue
            with path.open('rb') as stream:
                raw = stream.read(LIMIT + 1)
            if len(raw) > LIMIT or b'\0' in raw:
                skipped.append(name); continue
            files[name] = raw.decode('utf-8')
            total += len(raw)
        except (OSError, UnicodeError):
            skipped.append(name)
    return files, skipped


def capture(base: Path, root: str, thread: str, run: str, label: str) -> dict:
    if not root or not thread:
        return {}
    target = folder(base, root, thread)
    key = hashlib.sha256(run.encode()).hexdigest()
    path = target / f'{key}.json'
    if path.exists():
        return json.loads(path.read_text())
    files, skipped = texts(root)
    row = dict(id=key, created_at=time.time(), label=label[:160], root=str(Path(root).resolve()), files=files, skipped=skipped)
    target.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(row, ensure_ascii=False), mode=0o600)
    # Keep the ten latest review boundaries per session.
    for old in sorted(target.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[10:]:
        old.unlink(missing_ok=True)
    return row


def snapshots(base: Path, root: str, thread: str) -> list[dict]:
    rows = []
    for path in folder(base, root, thread).glob('*.json'):
        try:
            row = json.loads(path.read_text())
            rows.append({key: row[key] for key in ('id', 'label', 'created_at')})
        except (OSError, ValueError, KeyError):
            continue
    return sorted(rows, key=lambda row: row['created_at'], reverse=True)


def changes(base: Path, root: str, thread: str, snapshot: str) -> dict:
    if len(snapshot) != 64 or any(c not in '0123456789abcdef' for c in snapshot):
        raise ValueError('Choose an available review boundary.')
    path = folder(base, root, thread) / f'{snapshot}.json'
    if not path.is_file():
        raise ValueError('This review boundary is no longer available.')
    before = json.loads(path.read_text())
    now, skipped = texts(root)
    omitted = set(skipped) | set(before['skipped'])
    diffs = []
    for name in sorted(set(before['files']) | set(now)):
        if name in omitted:
            continue
        old, new = before['files'].get(name, ''), now.get(name, '')
        if old == new and (name in before['files']) == (name in now):
            continue
        lines = difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True), fromfile=name, tofile=name)
        diff = ''.join(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n' for line in lines)
        diffs.append(dict(path=name, diff=diff[:100000], truncated=len(diff) > 100000,
                          kind='added' if name not in before['files'] else 'deleted' if name not in now else 'modified'))
    return dict(files=diffs[:100], omitted=sorted(omitted), truncated=len(diffs) > 100)
