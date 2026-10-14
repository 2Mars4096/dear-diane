import asyncio
from types import SimpleNamespace

import pytest

from diane.native_workers.service import NativeTeam, active_teams
from diane.native_workers.settings import record_with_team_settings
from diane.server.chat_request import ChatMessageRequest
from diane.server.chat_v2 import AgentRunCommand, build_v2_bridge_context
from diane.server.chat_v2_store import ChatV2Store
from diane.server.chat_v2_backend import build_agent_backend_request


def setup(tmp_path):
    store = ChatV2Store(tmp_path / 'chat_v2')
    accepted = store.accept_bridge_context(build_v2_bridge_context(ChatMessageRequest(
        workflow_id='work', thread_id='thread', message='Original request', mode='agent',
        surface_type='frontend', surface_id='chunk-workspace')), stream_channel_id='test')
    policy = {'permission_mode': 'plan', 'lead_profile': {'model': 'lead-model'}, 'native_workers': {}}
    store.update_run_metadata(accepted.run_id, {'profile_policy': policy, 'restart_execution': {'backend': 'native_codex', 'profile_policy': policy}}, status='running')
    team = NativeTeam(accepted.run_id, str(tmp_path), {}, tmp_path / 'workers', parent_request=SimpleNamespace(profile_policy=policy))
    active_teams[accepted.run_id] = team
    return store, accepted, team


def command(run, lane='append_followup', capacity=3):
    return AgentRunCommand(command=lane, run_id=run.run_id, task_id=run.task_id, idempotency_key='message', payload={
        'text': 'Use the updated team', 'display_text': 'Use the updated team',
        'team_settings': {'team_max_parallel_workers': capacity, 'native_workers': {
            'reviewer': {'backend': 'codex', 'enabled': False, 'model': 'worker-model', 'permission': 'full', 'resume_session': 'injected'},
            'claude': {'enabled': True, 'model': 'claude-model'},
        }}})


@pytest.mark.asyncio
async def test_steer_updates_empty_team_and_persists_without_changing_lead(tmp_path):
    store, accepted, team = setup(tmp_path)
    try:
        event = await record_with_team_settings(store, store.get_run(accepted.run_id), command(accepted))
        assert event.type == 'queue_item_added'
        assert team.max_parallel_workers == 3
        assert team.profiles['reviewer']['enabled']
        assert team.profiles['reviewer']['permission'] == 'plan'
        assert 'resume_session' not in team.profiles['reviewer']
        saved = store.get_run(accepted.run_id)
        assert saved.metadata['restart_execution']['profile_policy']['lead_profile']['model'] == 'lead-model'
        item = store.get_task(accepted.task_id).queue_items[0]
        assert 'refresh status' in item.text
        assert item.metadata['command_payload']['display_text'] == 'Use the updated team'
        # Retrying an already queued request must not overwrite a newer team update.
        team.max_parallel_workers = 4
        await record_with_team_settings(store, saved, command(accepted))
        assert team.max_parallel_workers == 4
    finally:
        active_teams.pop(accepted.run_id)


@pytest.mark.asyncio
async def test_queued_settings_apply_on_promotion_despite_old_execution_overrides(tmp_path):
    store, accepted, team = setup(tmp_path)
    try:
        await record_with_team_settings(store, store.get_run(accepted.run_id), command(accepted, 'continue_after_current'))
        assert team.profiles == {} and team.max_parallel_workers == 2
        store.update_run_metadata(accepted.run_id, {}, status='completed')
        promoted = store.promote_next_continue_after_current(accepted.run_id)
        request = build_agent_backend_request(promoted, store.get_task(accepted.task_id), overrides={'profile_policy': {'native_workers': {}, 'team_max_parallel_workers': 1}})
        assert request.profile_policy['native_workers']['reviewer']['model'] == 'worker-model'
        assert request.profile_policy['team_max_parallel_workers'] == 3
    finally:
        active_teams.pop(accepted.run_id)


@pytest.mark.asyncio
async def test_invalid_settings_do_not_queue_or_mutate(tmp_path):
    store, accepted, team = setup(tmp_path)
    try:
        with pytest.raises(ValueError, match='capacity'):
            await record_with_team_settings(store, store.get_run(accepted.run_id), command(accepted, capacity=0))
        assert team.profiles == {}
        assert not store.get_task(accepted.task_id).queue_items
    finally:
        active_teams.pop(accepted.run_id)


@pytest.mark.asyncio
async def test_removed_worker_keeps_configuration_and_reduced_capacity_does_not_cancel(tmp_path, monkeypatch):
    store, accepted, team = setup(tmp_path)
    monkeypatch.setattr('diane.native_workers.service.launch', lambda *args: (['fake'], {}))
    async def run(self, record, command, env):
        await asyncio.Event().wait()
    monkeypatch.setattr(NativeTeam, 'run', run)
    team.profiles = {'old': {'backend': 'codex', 'enabled': True, 'model': 'old-model'}}
    try:
        row = await team.start('codex', 'task', profile_id='old')
        await record_with_team_settings(store, store.get_run(accepted.run_id), command(accepted, capacity=1))
        assert not team.tasks[row['worker_id']].done()
        assert team.records[row['worker_id']]['profile']['model'] == 'old-model'
        with pytest.raises(ValueError, match='capacity'):
            await team.start('codex', 'new task', profile_id='reviewer')
        await team.stop(row['worker_id'])
        resumed = await team.start('codex', 'followup', row['worker_id'])
        assert resumed['profile']['model'] == 'old-model'
    finally:
        await team.close()
        active_teams.pop(accepted.run_id)
