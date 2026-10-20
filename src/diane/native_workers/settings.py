"""Apply explicit Team settings carried by an operator message."""
from copy import deepcopy
import json


def normalized_settings(value, permission):
    if not isinstance(value, dict) or not isinstance(value.get('native_workers'), dict):
        raise ValueError('Team settings require a worker roster')
    capacity = value.get('team_max_parallel_workers')
    if type(capacity) is not int or not 1 <= capacity <= 4:
        raise ValueError('Team capacity must be an integer from 1 to 4')
    profiles = {}
    for key, row in value['native_workers'].items():
        if not isinstance(key, str) or not isinstance(row, dict):
            raise ValueError('Invalid worker profile')
        backend = row.get('backend', key)
        if backend not in {'dan', 'codex', 'claude', 'cursor', 'antigravity'}:
            raise ValueError('Unsupported worker agent')
        if type(row.get('allow_children', False)) is not bool:
            raise ValueError('Allow child sessions must be a boolean')
        if row.get('allow_children') and backend not in {'dan', 'codex', 'claude'}:
            raise ValueError('This agent does not support child sessions')
        profile = {name: deepcopy(row[name]) for name in ('backend', 'name', 'model', 'account', 'effort', 'fast', 'provider', 'base_url', 'allow_children', 'claude_sandbox') if name in row}
        profile.update(enabled=bool(row.get('backend') or row.get('enabled')), permission=permission)
        if backend in {'codex', 'claude'}:
            profile.update(_live_steering=True, _interactive=True)
        profiles[key] = profile
    return {'native_workers': profiles, 'team_max_parallel_workers': capacity}


def settings_notice(settings):
    roster = [{**{key: row.get(key, '') for key in ('name', 'model', 'account', 'effort')},
               'profile_id': identity, 'backend': row.get('backend', identity),
               'allow_children': row.get('allow_children', False)}
              for identity, row in settings['native_workers'].items() if row['enabled']]
    return ('Diane Team settings for this message: ' + json.dumps(roster)
            + f". Shared parallel capacity: {settings['team_max_parallel_workers']}. "
            'Use the existing Diane team tool/bridge and refresh status before delegating. '
            'Existing worker sessions retain their saved configuration; new starts use this roster. '
            'A reduced capacity does not stop existing work; wait for room before starting more. '
            'Do not replace this roster with an external team controller.')


async def record_with_team_settings(store, run, command):
    from .service import active_teams
    from diane.server.chat_v2_store import command_fingerprint
    fingerprint = command_fingerprint(command)
    raw = (command.payload or {}).get('team_settings')
    if raw is None or command.command not in {'append_followup', 'continue_after_current'}:
        return store.record_agent_command(command)
    if command.run_id != run.run_id or command.task_id != run.task_id:
        raise ValueError('Team settings must target the same run and task as the message')
    policy = dict(run.metadata.get('profile_policy') or (run.metadata.get('restart_execution') or {}).get('profile_policy') or {})
    settings = normalized_settings(raw, policy.get('permission_mode', 'auto'))
    policy.update(settings)
    payload = dict(command.payload)
    payload['profile_policy'] = {**dict(payload.get('profile_policy') or {}), **policy}
    payload['team_settings'] = settings
    # Keep the displayed user message unchanged while supplying coordination context.
    payload.setdefault('display_text', payload.get('text', ''))
    payload['text'] = str(payload.get('text') or '') + '\n\n' + settings_notice(settings)
    command = command.model_copy(update={'payload': payload})
    team = active_teams.get(run.run_id)

    def record():
        event = store.record_agent_command(command, request_fingerprint=fingerprint)
        if event.type != 'queue_item_added' or event.source_event_type.endswith('queue_coalesced'):
            return event
        if command.command == 'append_followup':
            if team:
                team.profiles = deepcopy(settings['native_workers'])
                team.max_parallel_workers = settings['team_max_parallel_workers']
            saved = dict(run.metadata.get('restart_execution') or {})
            if saved:
                saved['profile_policy'] = {**dict(saved.get('profile_policy') or {}), **policy}
            metadata = {'profile_policy': policy, 'team_settings': settings}
            if saved:
                metadata['restart_execution'] = saved
            store.update_run_metadata(run.run_id, metadata)
        return event

    if team:
        async with team.admission_lock:
            return record()
    return record()
