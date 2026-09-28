import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from dan import _atomic_file
from dan.notes_frontmatter import parse_yaml_frontmatter, yaml_list


def test_metadata_parser_retains_notes_and_paper_fields():
    value = parse_yaml_frontmatter('''title: "A # quoted title" # comment
draft: false
tags: [networks, trade]
categories:
 - research
 - "paper"
''')
    assert value == {'title': 'A # quoted title', 'draft': False, 'tags': ['networks', 'trade'], 'categories': ['research', 'paper']}
    assert yaml_list(value['draft']) == []


def test_private_replacement_is_private_during_write_and_after_replace(tmp_path, monkeypatch):
    target = tmp_path / 'state.json'
    target.write_text('old')
    target.chmod(0o644)
    replace = os.replace
    observed = []
    def inspect(source, destination):
        observed.append(source.stat().st_mode & 0o777)
        replace(source, destination)
    monkeypatch.setattr(_atomic_file.os, 'replace', inspect)
    _atomic_file.atomic_write_text(target, 'private', mode=0o600)
    assert observed == [0o600]
    assert target.stat().st_mode & 0o777 == 0o600
    assert target.read_text() == 'private'


def test_workspace_permissions_remain_and_failed_replace_cleans_up(tmp_path, monkeypatch):
    target = tmp_path / 'notes.md'
    target.write_text('old')
    target.chmod(0o640)
    _atomic_file.atomic_write_text(target, 'new')
    assert target.stat().st_mode & 0o777 == 0o640
    def fail(*_args):
        raise OSError('simulated failure')
    monkeypatch.setattr(_atomic_file.os, 'replace', fail)
    with pytest.raises(OSError, match='simulated failure'):
        _atomic_file.atomic_write_text(target, 'lost')
    assert target.read_text() == 'new'
    assert sorted(path.name for path in tmp_path.iterdir()) == ['notes.md']


def test_concurrent_private_writers_use_distinct_temporaries(tmp_path):
    target = tmp_path / 'state.json'
    values = [str(index) * 2000 for index in range(8)]
    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(lambda value: _atomic_file.atomic_write_text(target, value, mode=0o600), values))
    assert target.read_text() in values
    assert target.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.iterdir()) == [target]
