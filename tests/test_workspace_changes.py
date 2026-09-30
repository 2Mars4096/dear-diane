import subprocess
from pathlib import Path
import pytest
from dan.workspace_changes import capture, changes, snapshots


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True).stdout


def test_review_preserves_dirty_index_and_tracks_untracked_edits(tmp_path):
    root = tmp_path / 'repo'; root.mkdir(); git(root, 'init')
    (root / 'a.txt').write_text('staged\n'); git(root, 'add', 'a.txt')
    index = (root / '.git/index').read_bytes()
    (root / 'a.txt').write_text('existing user edit\n')
    (root / 'new.txt').write_text('untracked before\n')
    (root / '.gitignore').write_text('secret\n')
    (root / 'secret').write_text('excluded')
    row = capture(tmp_path / 'state', str(root), 'thread', 'run', 'Change one thing')
    assert 'secret' not in row['files']
    (root / 'a.txt').write_text('agent edit\n')
    (root / 'new.txt').unlink()
    diff = changes(tmp_path / 'state', str(root), 'thread', row['id'])
    assert 'existing user edit' in diff['files'][0]['diff']
    assert diff['files'][1]['kind'] == 'deleted'
    assert (root / '.git/index').read_bytes() == index
    # Retrying the same run does not move the boundary forward.
    assert capture(tmp_path / 'state', str(root), 'thread', 'run', 'retry')['files']['a.txt'] == 'existing user edit\n'
    with pytest.raises(ValueError): changes(tmp_path / 'state', str(root), 'other', row['id'])
    with pytest.raises(ValueError): changes(tmp_path / 'state', str(root), 'thread', '../escape')


def test_binary_symlink_and_non_git_are_explicit(tmp_path):
    git(tmp_path, 'init')
    (tmp_path / 'binary').write_bytes(b'\0data')
    (tmp_path / 'link').symlink_to('/etc/hosts')
    row = capture(tmp_path / 'state', str(tmp_path), 'thread', 'run', 'Test')
    assert set(row['skipped']) == {'binary', 'link'}
    outside = tmp_path / 'outside'; outside.mkdir()
    # A subfolder of a repository is supported, but a truly separate folder isn't.
    assert snapshots(tmp_path / 'state', str(outside), 'thread') == []


def test_diff_keeps_lines_separate_without_final_newline(tmp_path):
    git(tmp_path, 'init')
    (tmp_path / 'a.txt').write_text('before')
    row = capture(tmp_path / '.git/review', str(tmp_path), 't', 'r', 'Edit')
    (tmp_path / 'a.txt').write_text('after')
    diff = changes(tmp_path / '.git/review', str(tmp_path), 't', row['id'])['files'][0]['diff']
    assert '-before\n' in diff and '+after\n' in diff
    assert diff.count('\\ No newline at end of file') == 2
