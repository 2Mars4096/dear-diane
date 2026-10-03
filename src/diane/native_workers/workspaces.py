"""Owned Git worktrees and conservative integration for existing Team workers."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

_locks: dict[str, threading.Lock] = {}


def git(root: str, *args: str, data: bytes | None = None, env=None) -> bytes:
    result = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-C', root, *args], input=data,
                            capture_output=True, timeout=60, env=env)
    if result.returncode:
        raise ValueError(result.stderr.decode(errors='replace')[:2000] or 'Git operation failed')
    return result.stdout


def fingerprint(path: Path) -> str:
    if path.is_symlink():
        return 'link:' + os.readlink(path)
    if not path.exists():
        return 'absent'
    if not path.is_file():
        return 'directory'
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while data := stream.read(1024 * 1024): digest.update(data)
    return f'{path.stat().st_mode & 0o111}:{digest.hexdigest()}'


def names(root: str) -> list[str]:
    return sorted(set(git(root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard').decode().split('\0')) - {''})


def tree(root: str) -> str:
    # A private temporary index includes untracked additions without changing the worker's staging area.
    with tempfile.TemporaryDirectory(prefix='diane-index-') as temporary:
        env = {**os.environ, 'GIT_INDEX_FILE': str(Path(temporary) / 'index')}
        git(root, 'read-tree', 'HEAD', env=env)
        git(root, 'add', '-A', '--', '.', env=env)
        return git(root, 'write-tree', env=env).decode().strip()


def create(base: Path, worker_id: str, source: str) -> dict:
    root = str(Path(source).resolve())
    top = git(root, 'rev-parse', '--show-toplevel').decode().strip()
    if Path(top).resolve() != Path(root):
        raise ValueError('Isolated workers require the Git project root; choose shared workspace for this folder.')
    head = git(root, 'rev-parse', 'HEAD').decode().strip()
    target = base.parent / 'worker_workspaces' / worker_id
    target.parent.mkdir(parents=True, exist_ok=True)
    git(root, 'worktree', 'add', '--detach', str(target), head)
    try:
        patch = git(root, 'diff', '--binary', head, '--', '.')
        if patch: git(str(target), 'apply', '--binary', '-', data=patch)
        for name in git(root, 'ls-files', '-z', '--others', '--exclude-standard').decode().split('\0'):
            if not name: continue
            original, copy = Path(root) / name, target / name
            if not original.resolve().is_relative_to(Path(root)) or original.is_symlink():
                raise ValueError(f'Cannot copy external or symbolic untracked file: {name}')
            if original.is_file():
                copy.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original, copy)
        baseline = tree(str(target))
        original = {name: fingerprint(target / name) for name in names(str(target))}
        return dict(workspace_root=str(target), source_workspace=root, source_head=head,
                    baseline_tree=baseline, source_files=original, integrated_tree='')
    except Exception:
        git(root, 'worktree', 'remove', '--force', str(target))
        raise


def preview(record: dict) -> dict:
    if not record.get('baseline_tree'):
        raise ValueError('This worker does not own an isolated workspace')
    current = tree(record['workspace_root'])
    patch = git(record['workspace_root'], 'diff', '--no-ext-diff', record.get('integrated_tree') or record['baseline_tree'], current).decode(errors='replace')
    return {'tree': current, 'diff': patch[:100000], 'truncated': len(patch) > 100000}


def apply(record: dict, expected_tree: str = '') -> dict:
    root, workspace = record.get('source_workspace'), record['workspace_root']
    if not root or not record.get('baseline_tree'):
        raise ValueError('This worker does not own an isolated workspace')
    if record.get('profile', {}).get('permission') == 'plan':
        raise ValueError('Plan mode cannot apply changes')
    with _locks.setdefault(root, threading.Lock()):
        if git(root, 'rev-parse', 'HEAD').decode().strip() != record['source_head']:
            raise ValueError('The project commit changed. Review and integrate this worktree manually.')
        current = tree(workspace)
        if expected_tree and current != expected_tree:
            raise ValueError("The working copy changed. Review its latest changes before applying.")
        baseline = record.get('integrated_tree') or record['baseline_tree']
        changed = [name for name in git(workspace, 'diff', '--name-only', '-z', baseline, current).decode().split('\0') if name]
        conflicts = [name for name in changed if fingerprint(Path(root) / name) != record['source_files'].get(name, 'absent')]
        if conflicts:
            raise ValueError('Project files changed since delegation: ' + ', '.join(conflicts[:10]))
        patch = git(workspace, 'diff', '--binary', baseline, current)
        if patch:
            git(root, 'apply', '--check', '--binary', '-', data=patch)
            git(root, 'apply', '--binary', '-', data=patch)
        record['integrated_tree'] = current
        for name in changed: record['source_files'][name] = fingerprint(Path(root) / name)
        return {'files': changed, 'workspace_root': workspace}
