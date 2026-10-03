import json
from pathlib import Path

from diane.native_workers.codex_home import prepare_home, private_home


def test_private_storage_copies_only_inputs_and_keeps_local_refresh(monkeypatch, tmp_path):
    monkeypatch.setenv('DAN_GRAPHS_DIR', str(tmp_path / 'graphs'))
    source = tmp_path / 'desktop'; source.mkdir()
    (source / 'auth.json').write_text('{"test":"original"}')
    (source / 'config.toml').write_text('model="test-model"')
    (source / 'sessions').mkdir(); (source / 'sessions' / 'original').write_text('private history')
    (source / 'state_5.sqlite').write_text('desktop database')
    (source / 'thread-writer-locks').mkdir()
    home = prepare_home(source)
    assert home != source and home == private_home(source)
    assert (home / 'auth.json').read_bytes() == (source / 'auth.json').read_bytes()
    assert (home / 'auth.json').stat().st_mode & 0o777 == 0o600
    assert home.stat().st_mode & 0o777 == 0o700
    assert not any((home / name).exists() for name in ('sessions', 'state_5.sqlite', 'thread-writer-locks'))
    (home / 'auth.json').write_text('{"test":"diane-refreshed"}')
    prepare_home(source)
    assert json.loads((home / 'auth.json').read_text())['test'] == 'diane-refreshed'
    assert json.loads((source / 'auth.json').read_text())['test'] == 'original'
    (source / 'auth.json').write_text('{"test":"new-account-login"}')
    prepare_home(source)
    assert json.loads((home / 'auth.json').read_text())['test'] == 'new-account-login'
    assert private_home(tmp_path / 'another') != home


def test_launch_overrides_inherited_database_and_config(monkeypatch, tmp_path):
    from diane.native_workers import catalog
    source = tmp_path / 'desktop'; source.mkdir()
    (source / 'config.toml').write_text('sqlite_home="/shared/db"\nlog_dir="/shared/log"')
    monkeypatch.setenv('DAN_GRAPHS_DIR', str(tmp_path / 'graphs'))
    monkeypatch.setenv('CODEX_SQLITE_HOME', '/shared/other-db')
    monkeypatch.setattr(catalog, 'binary', lambda _: '/bin/codex')
    monkeypatch.setattr(catalog, 'accounts', lambda: {'codex': {'default': {'env': {'CODEX_HOME': str(source)}}}})
    command, env = catalog.launch('codex', {}, 'test', str(tmp_path))
    home = private_home(source)
    assert env['CODEX_HOME'] == env['CODEX_SQLITE_HOME'] == str(home)
    assert f'sqlite_home={json.dumps(str(home))}' in command
    assert f'log_dir={json.dumps(str(home / "log"))}' in command


def test_usage_discovers_private_transcripts(monkeypatch, tmp_path):
    from diane.native_workers import token_usage
    monkeypatch.setenv('DAN_GRAPHS_DIR', str(tmp_path / 'graphs'))
    source = tmp_path / 'desktop'
    home = private_home(source)
    (home / 'sessions').mkdir(parents=True)
    monkeypatch.setattr(token_usage, 'accounts', lambda: {'codex': {'default': {'env': {'CODEX_HOME': str(source)}}}})
    assert ('codex', 'default', home) in token_usage._stores()
