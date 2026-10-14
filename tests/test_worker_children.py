import asyncio
from types import SimpleNamespace

import pytest

from diane.native_workers.children import WorkerScope
from diane.native_workers.service import NativeTeam, current_team
from diane.tools.native_worker import native_worker


@pytest.mark.asyncio
async def test_children_inherit_frozen_configuration_share_capacity_and_stop_with_parent(monkeypatch, tmp_path):
    monkeypatch.setattr('diane.native_workers.service.launch', lambda *args: (['fake'], {}))
    captured = {}
    async def run(self, record, command, env):
        captured[record['worker_id']] = (record['prompt'], command)
        await asyncio.Event().wait()
    monkeypatch.setattr(NativeTeam, 'run', run)
    profile = dict(enabled=True, backend='codex', model='model-a', account='work', effort='high', fast=True, allow_children=True)
    team = NativeTeam('lead', str(tmp_path), {'first': profile, 'other': {**profile, 'model': 'model-b'}}, tmp_path / 'workers', parent_request=SimpleNamespace(profile_policy={'team_max_parallel_workers': 3}))
    parent = await team.start('codex', 'parent task', profile_id='first')
    parent = team.records[parent['worker_id']]
    scope = WorkerScope(team, parent)
    try:
        team.profiles['first'] = {**profile, 'model': 'changed'}
        child = await scope.start('', 'child task', profile_id='self')
        assert child['profile'] == parent['profile']
        assert child['parent_worker_id'] == parent['worker_id']
        assert child['parent_run_id'] == 'lead'
        sibling = await team.start('codex', 'sibling', profile_id='other')
        with pytest.raises(ValueError, match='configuration'):
            await scope.start('claude', 'wrong runtime')
        with pytest.raises(ValueError, match='configuration'):
            await scope.start('codex', 'wrong profile', profile_id='other')
        with pytest.raises(ValueError, match='capacity'):
            await WorkerScope(team, team.records[child['worker_id']]).start('', 'grandchild')
        with pytest.raises(ValueError, match='Unknown child'):
            await scope.stop(sibling['worker_id'])
        assert set(scope.records) == {child['worker_id']}
        await team.stop(sibling['worker_id'])
        grandchild = await WorkerScope(team, team.records[child['worker_id']]).start('', 'grandchild')
        assert grandchild['profile'] == parent['profile']
        assert grandchild['parent_worker_id'] == child['worker_id']
        await asyncio.sleep(.01)
        assert 'child-session bridge' in captured[parent['worker_id']][0]
        assert '--queue' in captured[child['worker_id']][0]
        await team.stop(parent['worker_id'])
        assert team.tasks[child['worker_id']].done()
        assert team.tasks[grandchild['worker_id']].done()
    finally:
        await team.close()
    assert not list(tmp_path.glob('.diane-children-*'))


@pytest.mark.asyncio
async def test_disabled_children_and_stale_parent_rejected(tmp_path):
    team = NativeTeam('root', str(tmp_path), {}, tmp_path / 'workers')
    parent = {'worker_id': 'p', 'profile_id': 'p', 'profile': {}, 'backend': 'codex', 'status': 'running'}
    scope = WorkerScope(team, parent)
    with pytest.raises(ValueError, match='disabled'):
        await scope.start('', 'task')
    parent['profile']['allow_children'] = True
    parent['status'] = 'completed'
    with pytest.raises(ValueError, match='no longer running'):
        await scope.start('', 'task')


@pytest.mark.asyncio
async def test_builtin_worker_receives_only_self_scope(monkeypatch, tmp_path):
    from diane.server.chat_v2_backend import AgentBackendRunResult
    async def run(self, request, emit, runtime=None):
        scope = current_team.get()
        assert isinstance(scope, WorkerScope)
        assert list(scope.profiles) == ['self']
        assert request.profile_policy['model'] == 'test'
        assert 'Children inherit' in request.objective
        return AgentBackendRunResult(status='completed', backend='super_dan', summary='done')
    monkeypatch.setattr('diane.server.chat_v2_backend.SuperDanBackendAdapter._run', run)
    team = NativeTeam('root', str(tmp_path), {'dan': {'enabled': True, 'model': 'test', 'allow_children': True}}, tmp_path / 'workers')
    row = await team.start('dan', 'task')
    await team.tasks[row['worker_id']]
    assert team.records[row['worker_id']]['response'] == 'done'


@pytest.mark.asyncio
async def test_parent_completion_cleans_child_and_scope_cannot_access_sibling(monkeypatch, tmp_path):
    monkeypatch.setattr('diane.native_workers.service.launch', lambda *args: (['fake'], {}))
    spawned = []
    async def run(self, record, command, env):
        if record.get('parent_worker_id'):
            await asyncio.Event().wait()
        else:
            row = await native_worker('start', prompt='child', isolate=False, profile_id='self')
            spawned.append(row['worker_id'])
            with pytest.raises(ValueError, match='Unknown child'):
                await native_worker('status', worker_id=record['worker_id'])
            record['status'] = 'completed'
    monkeypatch.setattr(NativeTeam, 'run', run)
    team = NativeTeam('root', str(tmp_path), {'codex': {'enabled': True, 'allow_children': True}}, tmp_path / 'workers')
    row = await team.start('codex', 'parent')
    await team.tasks[row['worker_id']]
    assert len(spawned) == 1
    assert team.tasks[spawned[0]].done()
    assert team.records[spawned[0]]['status'] == 'stopped'
    assert not list(tmp_path.glob('.diane-children-*'))


@pytest.mark.asyncio
async def test_builtin_shared_inspector_inherits_read_only_policy(monkeypatch, tmp_path):
    from diane.server.chat_v2_backend import AgentBackendRunResult
    async def run(self, request, emit, runtime=None):
        assert request.profile_policy['permission_mode'] == 'plan'
        assert request.mutation_policy['permission'] == 'forbidden'
        return AgentBackendRunResult(status='completed', backend='super_dan', summary='inspected')
    monkeypatch.setattr('diane.server.chat_v2_backend.SuperDanBackendAdapter._run', run)
    team = NativeTeam('root', str(tmp_path), {'dan': {'enabled': True, 'model': 'test'}}, tmp_path / 'workers')
    row = await team.start('dan', 'Inspect', read_only=True)
    await team.tasks[row['worker_id']]
    assert team.records[row['worker_id']]['response'] == 'inspected'


@pytest.mark.asyncio
async def test_builtin_read_only_dispatcher_blocks_writes_and_shell(tmp_path):
    from diane.native_workers.service import worker_read_only
    from diane.worker.organisms.local_runtime import LocalOrganismToolRuntime
    token = worker_read_only.set(True)
    try:
        runtime = LocalOrganismToolRuntime(workspace_root=tmp_path, tool_ids=['file_read', 'file_write', 'shell_command'])
        assert 'file_read' in runtime.tool_ids
        assert 'file_write' not in runtime.tool_ids
        assert 'shell_command' not in runtime.tool_ids
        with pytest.raises(KeyError, match='not enabled'):
            await runtime.call('file_write', {'path': str(tmp_path / 'forbidden.txt'), 'content': 'bad'})
        with pytest.raises(KeyError, match='not enabled'):
            await runtime.call('shell_command', {'command': 'touch forbidden.txt'})
        assert not (tmp_path / 'forbidden.txt').exists()
    finally:
        worker_read_only.reset(token)
