import subprocess
import pytest
from diane.native_workers.workspaces import create, apply


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True).stdout


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'repo'; root.mkdir(); git(root, 'init')
    (root / 'a.txt').write_text('base\n'); (root / 'b.txt').write_text('base\n')
    git(root, 'add', '.'); git(root, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-m', 'baseline')
    return root


def test_parallel_edits_and_dirty_baseline_integrate_without_staging(repo, tmp_path):
    (repo / 'a.txt').write_text('user edit\n')
    (repo / 'new.txt').write_text('user untracked\n')
    before_index = (repo / '.git/index').read_bytes()
    one = create(tmp_path / 'state/workers', 'one', str(repo))
    two = create(tmp_path / 'state/workers', 'two', str(repo))
    from pathlib import Path
    assert (Path(one['workspace_root']) / 'a.txt').read_text() == 'user edit\n'
    (Path(one['workspace_root']) / 'a.txt').write_text('agent one\n')
    (Path(two['workspace_root']) / 'b.txt').write_text('agent two\n')
    assert (repo / 'a.txt').read_text() == 'user edit\n'
    assert apply(one)['files'] == ['a.txt']
    assert apply(two)['files'] == ['b.txt']
    assert apply(one)['files'] == []
    assert (repo / 'new.txt').read_text() == 'user untracked\n'
    assert (repo / '.git/index').read_bytes() == before_index


def test_conflicting_source_edits_are_preserved(repo, tmp_path):
    from pathlib import Path
    row = create(tmp_path / 'state/workers', 'one', str(repo))
    (Path(row['workspace_root']) / 'a.txt').write_text('agent\n')
    (repo / 'a.txt').write_text('new user edit\n')
    with pytest.raises(ValueError, match='changed since delegation'):
        apply(row)
    assert (repo / 'a.txt').read_text() == 'new user edit\n'


@pytest.mark.asyncio
async def test_team_launches_in_owned_worktree_and_resumes_there(repo, tmp_path, monkeypatch):
    import asyncio, sys
    from diane.native_workers.service import NativeTeam
    seen = []
    def launch(backend, profile, prompt, workspace, session=''):
        seen.append((workspace, session))
        code = 'from pathlib import Path; import json; Path("a.txt").write_text("worker edit\\n"); print(json.dumps({"type":"result","session_id":"native","result":"done"}))'
        return [sys.executable, '-c', code], {}
    monkeypatch.setattr('diane.native_workers.service.launch', launch)
    team = NativeTeam('parent', str(repo), {'claude': {'enabled': True}}, tmp_path / 'state/workers')
    row = await team.start('claude', 'Edit a', isolate=True)
    await asyncio.gather(*team.tasks.values())
    assert (repo / 'a.txt').read_text() == 'base\n'
    assert seen[0][0] == row['workspace_root'] != str(repo)
    await team.start('claude', 'Check again', row['worker_id'])
    await asyncio.gather(*team.tasks.values())
    assert seen[1] == (row['workspace_root'], 'native')
    apply(team.records[row['worker_id']])
    assert (repo / 'a.txt').read_text() == 'worker edit\n'


def test_apply_rejects_changes_after_review(repo, tmp_path):
    from pathlib import Path
    from diane.native_workers.workspaces import preview
    row = create(tmp_path / 'state/workers', 'reviewed', str(repo))
    (Path(row['workspace_root']) / 'a.txt').write_text('reviewed edit\n')
    reviewed = preview(row)
    (Path(row['workspace_root']) / 'a.txt').write_text('later edit\n')
    with pytest.raises(ValueError, match='working copy changed'):
        apply(row, reviewed['tree'])
    assert (repo / 'a.txt').read_text() == 'base\n'


@pytest.mark.asyncio
async def test_failed_launch_admission_removes_only_its_new_worktree(repo, tmp_path, monkeypatch):
    from diane.native_workers.service import NativeTeam
    def unavailable(*args, **kwargs):
        raise ValueError('CLI is unavailable')
    monkeypatch.setattr('diane.native_workers.service.launch', unavailable)
    team = NativeTeam('parent', str(repo), {'claude': {'enabled': True}}, tmp_path / 'state/workers')
    (repo / 'a.txt').write_text('user work\n')
    with pytest.raises(ValueError, match='CLI is unavailable'):
        await team.start('claude', 'Edit a', isolate=True)
    assert not team.records
    assert git(repo, 'worktree', 'list', '--porcelain').count(b'worktree ') == 1
    assert (repo / 'a.txt').read_text() == 'user work\n'


def test_child_bridges_are_not_copied_or_integrated(repo, tmp_path):
    from pathlib import Path
    bridge = repo / '.diane-children-parent'
    bridge.mkdir()
    (bridge / 'team.py').write_text('private bridge')
    row = create(tmp_path / 'state/workers', 'child', str(repo))
    workspace = Path(row['workspace_root'])
    assert not (workspace / bridge.name).exists()
    nested = workspace / '.diane-children-child'
    nested.mkdir()
    (nested / 'request.response').write_text('runtime data')
    (workspace / 'a.txt').write_text('child edit')
    assert apply(row)['files'] == ['a.txt']
    assert not (repo / nested.name).exists()


@pytest.mark.asyncio
async def test_git_shared_editing_rejected_but_shared_inspection_is_read_only(repo, tmp_path, monkeypatch):
    import asyncio, sys
    from diane.native_workers.service import NativeTeam
    seen = []
    def launch(backend, profile, *args):
        seen.append(profile)
        return [sys.executable, '-c', 'print(\'{"type":"result","result":"inspected"}\')'], {}
    monkeypatch.setattr('diane.native_workers.service.launch', launch)
    team = NativeTeam('parent', str(repo), {'claude': {'enabled': True, 'permission': 'full'}}, tmp_path / 'state/workers')
    with pytest.raises(ValueError, match='isolated Git worktree'):
        await team.start('claude', 'Edit shared source', isolate=False)
    row = await team.start('claude', 'Inspect source', isolate=False, read_only=True)
    await team.tasks[row['worker_id']]
    assert seen[0]['permission'] == 'plan'
    assert row['workspace_root'] == str(repo)
    assert row['profile']['permission'] == 'plan'
    assert team.profiles['claude']['permission'] == 'full'


@pytest.mark.asyncio
async def test_agent_merge_requires_review_and_rejects_post_review_changes(repo, tmp_path, monkeypatch):
    import sys
    from pathlib import Path
    from diane.native_workers.service import NativeTeam, current_team
    from diane.tools.native_worker import native_worker
    monkeypatch.setattr('diane.native_workers.service.launch', lambda *a: ([sys.executable, '-c', 'print(\'{"type":"result","result":"done"}\')'], {}))
    team = NativeTeam('parent', str(repo), {'claude': {'enabled': True}}, tmp_path / 'state/workers')
    token = current_team.set(team)
    try:
        worker = await native_worker('start', backend='claude', prompt='Edit a')
        wid = worker['worker_id']
        await team.tasks[wid]
        copy = Path(worker['workspace_root'])
        (copy / 'a.txt').write_text('reviewed\n')
        assert (repo / 'a.txt').read_text() == 'base\n'
        with pytest.raises(ValueError, match='accepted review'):
            await native_worker('apply', worker_id=wid)
        changes = await native_worker('changes', worker_id=wid)
        assert 'reviewed' in changes['diff']
        await native_worker('review', worker_id=wid, attempt=1, decision='accepted', evidence=['Inspected a.txt diff and checks'])
        (copy / 'a.txt').write_text('changed after review\n')
        with pytest.raises(ValueError, match='working copy changed'):
            await native_worker('apply', worker_id=wid)
        assert (repo / 'a.txt').read_text() == 'base\n'
        with pytest.raises(ValueError, match='Inspect changes'):
            await native_worker('review', worker_id=wid, attempt=1, decision='accepted', evidence=['Stale inspection'])
        await native_worker('changes', worker_id=wid)
        await native_worker('review', worker_id=wid, attempt=1, decision='accepted', evidence=['Inspected latest diff and checks'])
        result = await native_worker('apply', worker_id=wid)
        assert result['files'] == ['a.txt']
        assert (repo / 'a.txt').read_text() == 'changed after review\n'
        assert team.records[wid]['integrated_attempt'] == 1
    finally:
        current_team.reset(token)
        await team.close()


@pytest.mark.asyncio
async def test_cli_bridge_worktree_review_merge_round_trip(repo, tmp_path, monkeypatch):
    import asyncio, json, sys
    from diane.native_workers import bridge
    from diane.native_workers.service import NativeTeam
    def launch(*args):
        code = 'from pathlib import Path; import json; Path("a.txt").write_text("isolated edit\\n"); print(json.dumps({"type":"result","result":"edited"}))'
        return [sys.executable, '-c', code], {}
    monkeypatch.setattr('diane.native_workers.service.launch', launch)
    team = NativeTeam('parent', str(repo), {'claude': {'enabled': True}}, tmp_path / 'state/workers')
    directory = tmp_path / 'bridge'; directory.mkdir()
    server = asyncio.create_task(bridge.serve(directory, team))
    async def call(*args):
        p = await asyncio.create_subprocess_exec(sys.executable, bridge.__file__, '--queue', str(directory), *args, stdout=asyncio.subprocess.PIPE)
        out, _ = await asyncio.wait_for(p.communicate(), 10)
        return json.loads(out)
    try:
        started = await call('start', '--backend', 'claude', '--prompt', 'Edit a', '--scope', 'a.txt only', '--acceptance', 'a.txt contains isolated edit')
        assert started['ok']
        wid = started['result']['worker_id']
        await team.tasks[wid]
        assert (repo / 'a.txt').read_text() == 'base\n'
        assert not (await call('apply', '--worker-id', wid))['ok']
        diff = await call('changes', '--worker-id', wid)
        assert diff['ok'] and 'isolated edit' in diff['result']['diff']
        accepted = await call('review', '--worker-id', wid, '--attempt', '1', '--decision', 'accepted', '--evidence', 'Inspected diff and test result')
        assert accepted['ok']
        assert (await call('apply', '--worker-id', wid))['ok']
        assert (repo / 'a.txt').read_text() == 'isolated edit\n'
    finally:
        server.cancel(); await asyncio.gather(server, return_exceptions=True)
        await team.close()
