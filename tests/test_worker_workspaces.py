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
