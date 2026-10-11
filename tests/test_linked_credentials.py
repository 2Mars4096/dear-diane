import json
from pathlib import Path
from types import SimpleNamespace

from diane.native_workers import linked_credentials


def test_discovery_publishes_paths_only_and_preserves_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(linked_credentials, 'sys', SimpleNamespace(platform='linux'))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('DAN_DESKTOP_LINKED_CREDENTIALS', '1')
    monkeypatch.setenv('DAN_GRAPHS_DIR', str(tmp_path / 'graphs'))
    env_file = tmp_path / '.env'
    original = b'OPENROUTER_API_KEY=synthetic-key\n'
    env_file.write_bytes(original)
    linked_credentials.publish_locations(str(env_file))
    marker = tmp_path / '.dan/linked-credentials.json'
    data = json.loads(marker.read_text())
    assert data == {'schema_version': 1, 'saved_keys_file': str(tmp_path / 'graphs/model_credentials/keys.json'), 'env_file': str(env_file)}
    assert 'synthetic-key' not in marker.read_text()
    assert env_file.read_bytes() == original
    assert marker.stat().st_mode & 0o777 == 0o600


def test_non_desktop_does_not_write_discovery(monkeypatch, tmp_path):
    monkeypatch.delenv('DAN_DESKTOP_LINKED_CREDENTIALS', raising=False)
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    linked_credentials.publish_locations()
    assert not (tmp_path / '.dan').exists()
