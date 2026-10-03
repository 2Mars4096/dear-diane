"""Bounded browser uploads stored on the execution host for chat attachments."""
import uuid
from pathlib import Path
from urllib.parse import unquote
from fastapi import APIRouter, HTTPException, Request
from diane.server.paths import resolve_graphs_dir

router = APIRouter()
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024

@router.post('/api/chat-attachments')
async def upload_attachment(request: Request):
    name = unquote(request.headers.get('x-filename', 'Attachment'))
    if not name or name in {'.', '..'} or '/' in name or '\\' in name or any(ord(c) < 32 for c in name) or len(name.encode('utf-8')) > 200:
        raise HTTPException(400, 'Invalid attachment filename')
    directory = Path(resolve_graphs_dir()).parent / 'chat-attachments'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / f'{uuid.uuid4().hex}-{name}'
    size = 0
    try:
        with target.open('xb') as output:
            target.chmod(0o600)
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_ATTACHMENT_BYTES:
                    raise HTTPException(413, 'Attachments support files up to 20 MB')
                output.write(chunk)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return {'path': str(target), 'filename': name, 'size': size}
