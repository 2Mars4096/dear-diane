"""Miscellaneous capability handlers: current_datetime, telegram_poll, send_email."""
from __future__ import annotations

import os
from typing import Any

from dan.server.capability_registry import CapabilityContext, CapabilityResult
from dan.server.chat.prompts import get_prompt_detail_body


async def handle_current_datetime(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    from datetime import datetime, timezone

    now = datetime.now()
    utc = datetime.now(timezone.utc)
    return CapabilityResult(
        success=True,
        message=f"Local: {now.strftime('%A, %B %d, %Y %I:%M %p')} | UTC: {utc.strftime('%Y-%m-%d %H:%M:%S')}",
        data={"local": now.isoformat(), "utc": utc.isoformat()},
    )


async def handle_load_prompt_detail(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    detail_id = str(args.get("detail_id") or "").strip()
    if not detail_id:
        return CapabilityResult(success=False, message="'detail_id' is required.")
    detail = get_prompt_detail_body(detail_id)
    if not detail:
        return CapabilityResult(
            success=False,
            message=f"Unknown prompt detail: {detail_id}",
        )
    return CapabilityResult(
        success=True,
        message=detail,
        data={"detail_id": detail_id},
        output_preview=detail[:200],
    )


async def handle_telegram_poll(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    question = args.get("question", "").strip()
    options = args.get("options", [])
    is_anonymous = args.get("is_anonymous", False)
    allows_multiple = args.get("allows_multiple", False)

    if not question:
        return CapabilityResult(success=False, message="Poll question is required.")
    if len(question) > 300:
        return CapabilityResult(
            success=False, message="Poll question must be 300 characters or less.",
        )
    if not isinstance(options, list) or len(options) < 2:
        return CapabilityResult(
            success=False, message="At least 2 poll options are required.",
        )
    if len(options) > 10:
        return CapabilityResult(
            success=False, message="Maximum 10 poll options allowed.",
        )

    cleaned: list[str] = []
    for i, opt in enumerate(options):
        if not isinstance(opt, str) or not opt.strip():
            return CapabilityResult(
                success=False, message=f"Option {i + 1} is empty.",
            )
        if len(opt) > 100:
            return CapabilityResult(
                success=False,
                message=f"Option {i + 1} exceeds 100 character limit.",
            )
        cleaned.append(opt.strip())

    return CapabilityResult(
        success=True,
        message=f'Poll created: "{question}" with {len(cleaned)} options.',
        data={
            "poll_request": True,
            "question": question,
            "options": cleaned,
            "is_anonymous": is_anonymous,
            "allows_multiple": allows_multiple,
        },
    )


async def handle_send_email(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    to = args.get("to", "").strip()
    subject = args.get("subject", "").strip()
    body = args.get("body", "").strip()
    if not to or not subject or not body:
        return CapabilityResult(success=False, message="'to', 'subject', and 'body' are all required.")

    smtp_host = os.environ.get("DAN_SMTP_HOST", "").strip()
    smtp_user = os.environ.get("DAN_SMTP_USER", "").strip()
    smtp_password = os.environ.get("DAN_SMTP_PASSWORD", "").strip()
    if not smtp_host or not smtp_user or not smtp_password:
        return CapabilityResult(
            success=False,
            message="Email not configured. Set DAN_SMTP_HOST, DAN_SMTP_USER, and DAN_SMTP_PASSWORD in .env.",
        )

    try:
        import aiosmtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        from_name = os.environ.get("DAN_SMTP_FROM_NAME", "DAN")
        port = int(os.environ.get("DAN_SMTP_PORT", "587"))

        msg = MIMEMultipart("alternative")
        msg["From"] = f"{from_name} <{smtp_user}>"
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        await aiosmtplib.send(
            msg,
            hostname=smtp_host,
            port=port,
            username=smtp_user,
            password=smtp_password,
            use_tls=True,
        )
        return CapabilityResult(success=True, message=f"Email sent to {to}.")
    except ImportError:
        return CapabilityResult(success=False, message="aiosmtplib not installed. Run: pip install 'dan[messaging]'")
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Failed to send email: {exc}")
