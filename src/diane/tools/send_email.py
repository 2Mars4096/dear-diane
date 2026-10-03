"""Built-in tool: send email via SMTP."""

from __future__ import annotations

import logging
import os
from email.message import EmailMessage

logger = logging.getLogger(__name__)

TOOL_METADATA = {
    "tool_id": "send_email",
    "description": (
        "Send an email via SMTP. Requires DAN_SMTP_HOST, DAN_SMTP_PORT, "
        "DAN_SMTP_USER, and DAN_SMTP_PASSWORD environment variables. "
        "Optionally attach files from the workspace."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Recipient email address.",
            },
            "subject": {
                "type": "string",
                "description": "Email subject line.",
            },
            "body": {
                "type": "string",
                "description": "Email body text.",
            },
            "from_addr": {
                "type": "string",
                "description": "Sender address. Defaults to DAN_SMTP_USER.",
            },
            "attachments": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of file paths to attach.",
            },
        },
        "required": ["to", "subject", "body"],
    },
    "examples": [
        {
            "input": {"to": "user@example.com", "subject": "Report", "body": "Please find the report attached."},
            "output": {"sent": True, "to": "user@example.com", "subject": "Report"},
        },
    ],
    "category": "communication",
    "returns": "dict with sent (bool), to, subject",
}


async def send_email(
    to: str,
    subject: str,
    body: str,
    from_addr: str | None = None,
    attachments: list[str] | None = None,
    **_kwargs,
) -> dict:
    host = os.environ.get("DAN_SMTP_HOST")
    port = int(os.environ.get("DAN_SMTP_PORT", "587"))
    user = os.environ.get("DAN_SMTP_USER")
    password = os.environ.get("DAN_SMTP_PASSWORD")

    if not host or not user:
        raise RuntimeError(
            "Email not configured. Set DAN_SMTP_HOST, DAN_SMTP_PORT, "
            "DAN_SMTP_USER, and DAN_SMTP_PASSWORD environment variables."
        )

    sender = from_addr or user

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg.set_content(body)

    if attachments:
        import mimetypes
        from diane.tools._workspace import validate_path

        for att_path in attachments:
            resolved = validate_path(att_path)
            if not os.path.isfile(resolved):
                raise FileNotFoundError(f"Attachment not found: '{att_path}'")
            ctype, _ = mimetypes.guess_type(resolved)
            maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
            with open(resolved, "rb") as fp:
                msg.add_attachment(
                    fp.read(),
                    maintype=maintype,
                    subtype=subtype,
                    filename=os.path.basename(resolved),
                )

    try:
        import aiosmtplib
    except ImportError:
        raise RuntimeError(
            "aiosmtplib is required for send_email. Install with: pip install aiosmtplib"
        )

    await aiosmtplib.send(
        msg,
        hostname=host,
        port=port,
        username=user,
        password=password,
        start_tls=True,
    )

    return {"sent": True, "to": to, "subject": subject}
