"""Read and continue prior managed workers without moving their historical records."""
from copy import deepcopy
import asyncio
import json
from pathlib import Path
import time
from uuid import uuid4
from weakref import WeakKeyDictionary

from .sessions import same_folder

_locks = WeakKeyDictionary()
_TERMINAL = {'completed', 'failed', 'stopped', 'interrupted'}


def _belongs(team, row):
    thread = getattr(team.parent_request, 'thread_id', '')
    return bool(isinstance(row, dict) and thread and row.get('thread_id') == thread and not row.get('origin')
                and not row.get('parent_worker_id')
                and same_folder(row.get('source_workspace') or row.get('workspace_root', ''), team.workspace))


def prior_worker(team, identity):
    if not identity or Path(identity).name != identity or identity in {'.', '..'}:
        raise ValueError('Unknown worker in this conversation')
    try:
        row = json.loads((team.base / f'{identity}.json').read_text())
    except (OSError, ValueError):
        raise ValueError('Unknown worker in this conversation')
    if not isinstance(row, dict) or row.get('worker_id') != identity or not _belongs(team, row):
        raise ValueError('Worker does not belong to this conversation and workspace')
    return row


def previous_workers(team):
    if not getattr(team.parent_request, 'thread_id', ''):
        return []
    rows = []
    for path in team.base.glob('*.json'):
        try:
            row = json.loads(path.read_text())
            if (_belongs(team, row) and row.get('parent_run_id') != team.parent_id
                    and not row.get('continued_by_worker_id')):
                rows.append(row)
        except (OSError, ValueError, TypeError):
            continue
    return sorted(rows, key=lambda row: row.get('created_at', 0), reverse=True)[:20]


async def resume_previous(team, identity, prompt):
    from .service import active_teams
    loop = asyncio.get_running_loop()
    lock = _locks.setdefault(loop, asyncio.Lock())
    async with lock, team.admission_lock:
        row = prior_worker(team, identity)
        seen = set()
        while row.get('continued_by_worker_id'):
            if row['worker_id'] in seen:
                raise ValueError('Worker continuation history contains a cycle')
            seen.add(row['worker_id'])
            row = prior_worker(team, row['continued_by_worker_id'])
        if row['worker_id'] in team.records:
            return await team._start(row['backend'], prompt, row['worker_id'])
        if row.get('status') not in _TERMINAL:
            raise ValueError('Prior worker is not settled; inspect or stop its original run first')
        owner = active_teams.get(row.get('parent_run_id'))
        if owner is not None and owner is not team and owner.parent_request is not None:
            raise ValueError('The previous lead run is still active; wait for it to finish')
        root = row.get('continuation_root_id') or row['worker_id']
        for owner in active_teams.values():
            for key, task in owner.tasks.items():
                record = owner.records.get(key, {})
                if not task.done() and (record.get('continuation_root_id') or key) == root:
                    raise ValueError('This worker continuation is already running')
        profile_id = row.get('profile_id') or row['backend']
        configured = team.profiles.get(profile_id, {})
        original = row.get('profile') or {}
        if not configured.get('enabled'):
            raise ValueError('Enable the original configured worker before resuming it')
        if configured.get('backend', profile_id) != row['backend']:
            raise ValueError('Cannot resume under a different worker runtime')
        for key, default in [('account', 'default'), ('permission', 'auto'), ('claude_sandbox', 'default'), ('provider', 'native'), ('base_url', '')]:
            if (configured.get(key) or default) != (original.get(key) or default):
                raise ValueError(f'Worker {key} settings changed; restore its settings or start a new task')
        if not row.get('workspace_root') or not Path(row['workspace_root']).is_dir():
            raise ValueError('The prior worker working directory is unavailable')
        # A new run-owned record points at the existing native session/worktree.
        # Historical task/result/attempt data remains under the original ID.
        clone = deepcopy(row)
        new_id = uuid4().hex
        clone.update(worker_id=new_id, parent_run_id=team.parent_id,
                     continued_from_worker_id=row['worker_id'], continuation_root_id=root,
                     worker_name=configured.get('name') or row.get('worker_name', ''), created_at=time.time())
        clone.pop('continued_by_worker_id', None)
        team.records[new_id] = clone
        try:
            result = await team._start(row['backend'], prompt, new_id)
            row['continued_by_worker_id'] = new_id
            team.save(row)
            return result
        except Exception:
            task = team.tasks.get(new_id)
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            team.records.pop(new_id, None)
            raise
