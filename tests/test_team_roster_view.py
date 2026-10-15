from types import SimpleNamespace
from diane.native_workers.service import active_teams
from diane.server.routers import native_workers


def test_roster_view_includes_idle_slots_and_hides_private_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr(native_workers, 'resolve_graphs_dir', lambda: str(tmp_path))
    active_teams['run'] = SimpleNamespace(profiles={
        'one': {'backend': 'claude', 'enabled': True, 'name': '', 'account': 'private-account'},
        'two': {'backend': 'codex', 'enabled': True, 'name': 'Worker 2'},
        'custom': {'backend': 'codex', 'enabled': True, 'name': 'Reviewer'},
        'placeholder': {'enabled': False},
    })
    try:
        result = native_workers.list_workers('run')
        assert result['workers'] == []
        assert [row['name'] for row in result['roster']] == ['Worker 1', 'Worker 2', 'Reviewer']
        assert result['roster'][0]['backend'] == 'claude'
        assert 'private-account' not in str(result)
    finally:
        active_teams.pop('run')
