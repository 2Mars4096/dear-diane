import asyncio
from types import SimpleNamespace

import pytest
from diane.native_workers.completion import CompletionInbox


def test_completion_is_once_per_attempt_and_ignores_children_and_reviewed():
    done = SimpleNamespace(done=lambda: True)
    row = dict(worker_id='w', attempt=1, status='completed')
    team = SimpleNamespace(records={'w': row}, tasks={'w': done})
    inbox = CompletionInbox()
    assert inbox.pending(team) == [row]
    inbox.acknowledge([row])
    assert inbox.pending(team) == []
    row['attempt'] = 2
    assert inbox.pending(team) == [row]
    row['lead_review'] = {'attempt': 2, 'decision': 'accepted'}
    assert inbox.pending(team) == []
    row['lead_review'] = None
    row['parent_worker_id'] = 'parent'
    assert inbox.pending(team) == []


@pytest.mark.asyncio
async def test_lead_waits_for_worker_then_resumes_same_session(monkeypatch, tmp_path):
    from diane.native_workers.lead import NativeLeadAdapter
    from diane.native_workers.service import NativeTeam, active_teams
    from diane.server.chat_v2_backend import AgentBackendRunRequest
    monkeypatch.setenv('DAN_GRAPHS_DIR', str(tmp_path / 'graphs'))
    launches = []
    def launch(backend, profile, prompt, workspace, session=''):
        launches.append((prompt, session))
        return ['fixture'], {}
    monkeypatch.setattr('diane.native_workers.service.launch', launch)
    async def run(self, record, command, env):
        record['native_session_id'] = 'same-lead-session'
        if record['attempt'] == 1:
            team = active_teams[record['parent_run_id']]
            worker = dict(worker_id='child', attempt=1, status='running', worker_name='Worker 1')
            team.records['child'] = worker
            async def finish():
                await asyncio.sleep(.05)
                worker['status'] = 'completed'
            team.tasks['child'] = asyncio.create_task(finish())
            record['response'] = 'Waiting for worker'
        else:
            assert 'Diane team completion notice' in record['prompt']
            assert 'remaining work' in record['prompt']
            record['response'] = 'Reviewed results; requested work complete'
        record['status'] = 'completed'
        self.save(record)
    monkeypatch.setattr(NativeTeam, 'run', run)
    request = AgentBackendRunRequest(task_id='task', run_id='run', thread_id='chat', workspace_root=str(tmp_path), objective='Complete requested work')
    result = await asyncio.wait_for(NativeLeadAdapter('claude').run(request, lambda e: None), 3)
    assert result.status == 'completed'
    assert result.summary == 'Reviewed results; requested work complete'
    assert len(launches) == 2
    assert launches[1][1] == 'same-lead-session'
    assert 'run' not in active_teams
