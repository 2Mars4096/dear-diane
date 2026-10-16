import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from diane.native_workers.service import NativeTeam, active_teams, current_team, continue_worker, public_worker
from diane.tools.native_worker import native_worker


def request(thread='chat'):
    return SimpleNamespace(thread_id=thread, profile_policy={'team_max_parallel_workers': 2})


@pytest.mark.asyncio
async def test_resume_preserves_native_session_worktree_and_historical_record(tmp_path, monkeypatch):
    source = tmp_path / 'project'; source.mkdir()
    for args in [('init',), ('config', 'user.email', 'fixture@example.invalid'), ('config', 'user.name', 'Fixture')]:
        subprocess.run(['git', '-C', str(source), *args], check=True, capture_output=True)
    (source / 'README.md').write_text('Original\n')
    subprocess.run(['git','-C',str(source),'add','.'],check=True)
    subprocess.run(['git','-C',str(source),'commit','-m','fixture'],check=True,capture_output=True)
    launches = []
    def launch(backend, profile, prompt, workspace, session=''):
        launches.append((workspace, session))
        script = "from pathlib import Path; import json,time; Path('partial.txt').write_text('partial work'); time.sleep(.1); print(json.dumps({'type':'result','session_id':'native-one','result':'Handoff'}))"
        return [sys.executable, '-c', script], {}
    monkeypatch.setattr('diane.native_workers.service.launch', launch)
    profiles = {'slot': {'backend':'claude','enabled':True,'account':'default','permission':'auto','name':'Worker 1'}}
    base = tmp_path / 'workers'
    old = NativeTeam('old-run', str(source), profiles, base, parent_request=request())
    worker = await old.start('claude', 'Implement bounded task', isolate=True, profile_id='slot')
    await asyncio.gather(*old.tasks.values())
    old.records[worker['worker_id']]['status'] = 'stopped'; old.save(old.records[worker['worker_id']])
    team = NativeTeam('new-run', str(source), profiles, base, parent_request=request())
    active_teams['new-run'] = team
    token = current_team.set(team)
    try:
        status = await native_worker('status')
        assert status['previous_workers'][0]['worker_id'] == worker['worker_id']
        assert (await native_worker('events', worker_id=worker['worker_id']))['entries']
        resumed = await native_worker('resume', worker_id=worker['worker_id'], prompt='Continue the same task')
        assert resumed['worker_id'] != worker['worker_id']
        assert resumed['continued_from_worker_id'] == worker['worker_id']
        assert resumed['workspace_root'] == worker['workspace_root']
        assert Path(resumed['workspace_root'], 'partial.txt').read_text() == 'partial work'
        assert launches[-1][1] == 'native-one'
        assert resumed['attempt'] == 2
        assert resumed['lead_review'] is None
        with pytest.raises(ValueError, match='running|busy'):
            await native_worker('resume', worker_id=worker['worker_id'], prompt='Duplicate')
        await asyncio.gather(*team.tasks.values())
        history = json.loads((base / f"{worker['worker_id']}.json").read_text())
        assert history['parent_run_id'] == 'old-run' and history['status'] == 'stopped'
        assert history['continued_by_worker_id'] == resumed['worker_id']
        assert public_worker(history)['can_reply'] is False
        assert public_worker(history)['can_apply'] is False
        with pytest.raises(ValueError):
            await continue_worker(base, 'old-run', worker['worker_id'], 'Stale sidebar reply')
    finally:
        current_team.reset(token); await team.close(); active_teams.pop('new-run', None)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['thread', 'workspace', 'running', 'account', 'permission', 'child'])
async def test_prior_resume_rejects_scope_and_access_changes(tmp_path, change):
    source = tmp_path / 'project'; source.mkdir()
    base=tmp_path/'workers';base.mkdir()
    profile={'backend':'claude','enabled':True,'account':'default','permission':'auto'}
    row={'worker_id':'old','parent_run_id':'old-run','thread_id':'chat','backend':'claude','profile_id':'slot','profile':profile,'workspace_root':str(source),'source_workspace':str(source),'status':'stopped','created_at':1}
    if change=='thread':row['thread_id']='other'
    if change=='workspace':row['source_workspace']=str(tmp_path/'other')
    if change=='running':row['status']='running'
    if change=='child':row['parent_worker_id']='parent'
    (base/'old.json').write_text(json.dumps(row))
    configured=dict(profile)
    if change=='account':configured['account']='different'
    if change=='permission':configured['permission']='plan'
    team=NativeTeam('new-run',str(source),{'slot':configured},base,parent_request=request())
    with pytest.raises(ValueError):await team.resume_previous('old','Continue')
    assert not team.tasks and not team.records
    assert 'continued_by_worker_id' not in json.loads((base/'old.json').read_text())
