"""Import (install) blocks from directories, tarballs, or URLs."""

from __future__ import annotations

import json
import logging
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Literal

from dan.blocks.models import (
    MANIFEST_FILENAME,
    TARBALL_SUFFIX,
    DanBlock,
    InstalledBlock,
)

logger = logging.getLogger(__name__)

_USER_BLOCKS_ROOT = Path.home() / ".dan" / "blocks"


def import_block(
    source: str | Path,
    *,
    scope: Literal["user", "workspace"] = "user",
    workspace: Path | None = None,
    force: bool = False,
    _user_root_override: Path | None = None,
) -> InstalledBlock:
    """Install a block from a local directory, tarball, or URL.

    Parameters
    ----------
    source:
        Path to a block directory, ``.dan-block.tar.gz`` file, or an HTTP(S) URL.
    scope:
        ``"user"`` installs to ``~/.dan/blocks/``; ``"workspace"`` installs to
        ``{workspace}/.dan/blocks/``.
    workspace:
        Required when *scope* is ``"workspace"``.
    force:
        Overwrite an existing installation of the same name+version.
    """
    source_str = str(source)

    if source_str.startswith("http://") or source_str.startswith("https://"):
        block_dir = _download_and_extract(source_str)
    elif Path(source_str).is_file() and source_str.endswith(TARBALL_SUFFIX):
        block_dir = _extract_tarball(Path(source_str))
    elif Path(source_str).is_dir():
        block_dir = Path(source_str)
    else:
        raise ValueError(
            f"Source {source_str!r} is not a directory, tarball, or URL"
        )

    manifest = _load_manifest(block_dir)
    install_root = _resolve_install_root(scope, workspace, _user_root_override)
    install_path = install_root / manifest.name / manifest.version

    if install_path.exists():
        if not force:
            logger.info(
                "Block %s@%s already installed at %s — skipping (use force=True to overwrite)",
                manifest.name,
                manifest.version,
                install_path,
            )
            return InstalledBlock(
                name=manifest.name,
                version=manifest.version,
                install_path=install_path,
                block_type=manifest.block_type,
                metadata=manifest,
            )
        shutil.rmtree(install_path)

    _check_version_conflicts(install_root, manifest)
    _check_dependencies(install_root, manifest)

    install_path.mkdir(parents=True, exist_ok=True)
    _copy_tree(block_dir, install_path)

    logger.info("Installed block %s@%s to %s", manifest.name, manifest.version, install_path)
    return InstalledBlock(
        name=manifest.name,
        version=manifest.version,
        install_path=install_path,
        block_type=manifest.block_type,
        metadata=manifest,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_manifest(block_dir: Path) -> DanBlock:
    manifest_path = block_dir / MANIFEST_FILENAME
    if not manifest_path.exists():
        raise ValueError(f"No {MANIFEST_FILENAME} found in {block_dir}")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    return DanBlock.model_validate(data)


def _resolve_install_root(
    scope: Literal["user", "workspace"],
    workspace: Path | None,
    user_root_override: Path | None = None,
) -> Path:
    if scope == "workspace":
        if workspace is None:
            raise ValueError("workspace path required for scope='workspace'")
        return workspace / ".dan" / "blocks"
    if user_root_override is not None:
        return user_root_override
    return _USER_BLOCKS_ROOT


def _extract_tarball(tarball: Path) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="dan-block-"))
    with tarfile.open(tarball, "r:gz") as tar:
        tar.extractall(tmp, filter="data")
    dirs = [d for d in tmp.iterdir() if d.is_dir()]
    if len(dirs) == 1:
        return dirs[0]
    return tmp


def _download_and_extract(url: str) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="dan-block-dl-"))
    tarball_path = tmp / "block.tar.gz"
    urllib.request.urlretrieve(url, tarball_path)  # noqa: S310
    return _extract_tarball(tarball_path)


def _copy_tree(src: Path, dst: Path) -> None:
    for item in src.iterdir():
        dest = dst / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)


def _check_version_conflicts(install_root: Path, manifest: DanBlock) -> None:
    block_parent = install_root / manifest.name
    if not block_parent.exists():
        return
    existing = [d.name for d in block_parent.iterdir() if d.is_dir() and d.name != manifest.version]
    if existing:
        logger.warning(
            "Block %r already has versions %s installed; adding %s",
            manifest.name,
            existing,
            manifest.version,
        )


def _check_dependencies(install_root: Path, manifest: DanBlock) -> None:
    for dep in manifest.dependencies:
        dep_path = install_root / dep.name / dep.version
        if not dep_path.exists():
            logger.warning(
                "Block %s@%s depends on %s@%s which is not installed",
                manifest.name,
                manifest.version,
                dep.name,
                dep.version,
            )
