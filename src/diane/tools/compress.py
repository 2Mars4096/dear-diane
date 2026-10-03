"""Built-in tool: create zip or tar.gz archives."""

from __future__ import annotations

import os
import tarfile
import zipfile

from diane.tools._workspace import validate_path

TOOL_METADATA = {
    "tool_id": "compress",
    "description": (
        "Create a zip or tar.gz archive from one or more files or directories. "
        "All paths must be within the workspace."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of file or directory paths to include.",
            },
            "output": {
                "type": "string",
                "description": "Output archive path (e.g. 'archive.zip').",
            },
            "format": {
                "type": "string",
                "description": "Archive format.",
                "enum": ["zip", "tar.gz"],
                "default": "zip",
            },
        },
        "required": ["paths", "output"],
    },
    "examples": [
        {
            "input": {"paths": ["src/", "README.md"], "output": "project.zip"},
            "output": {"archive_path": "project.zip", "size_bytes": 4096, "file_count": 5},
        },
    ],
    "category": "utility",
    "returns": "dict with archive_path, size_bytes, file_count",
}


async def compress(
    paths: list[str],
    output: str,
    format: str = "zip",
    **_kwargs,
) -> dict:
    resolved_paths = [validate_path(p) for p in paths]
    resolved_output = validate_path(output, operation="write")
    file_count = 0

    os.makedirs(os.path.dirname(resolved_output) or ".", exist_ok=True)

    if format == "zip":
        with zipfile.ZipFile(resolved_output, "w", zipfile.ZIP_DEFLATED) as zf:
            for rp, orig in zip(resolved_paths, paths):
                if os.path.isdir(rp):
                    for root, _dirs, files in os.walk(rp):
                        for fname in files:
                            fpath = os.path.join(root, fname)
                            arcname = os.path.relpath(fpath, os.path.dirname(rp))
                            zf.write(fpath, arcname)
                            file_count += 1
                else:
                    zf.write(rp, os.path.basename(rp))
                    file_count += 1
    elif format == "tar.gz":
        with tarfile.open(resolved_output, "w:gz") as tf:
            for rp, orig in zip(resolved_paths, paths):
                arcname = os.path.basename(rp)
                tf.add(rp, arcname=arcname)
                if os.path.isdir(rp):
                    for root, _dirs, files in os.walk(rp):
                        file_count += len(files)
                else:
                    file_count += 1
    else:
        raise ValueError(f"Unsupported format: '{format}'. Use 'zip' or 'tar.gz'.")

    size = os.path.getsize(resolved_output)
    return {
        "archive_path": output,
        "size_bytes": size,
        "file_count": file_count,
    }
