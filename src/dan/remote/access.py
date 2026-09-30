"""Authentication boundary for every remote HTTP and WebSocket route."""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import secrets
import time
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse


class RemoteAccess:
    def __init__(self, app, *, config: dict):
        self.app = app
        self.key = config["access_key"]
        self.origin = config["url"].rstrip("/")
        self.host = urlsplit(self.origin).netloc
        self.machine = config["id"]
        self.cookie = "dan_session_" + self.machine
        self.failures: dict[str, list[float]] = {}

    def signature(self, value):
        return hmac.new(self.key.encode(), value.encode(), hashlib.sha256).hexdigest()

    def session(self):
        value = f"{int(time.time()) + 7 * 86400}.{secrets.token_hex(16)}"
        return value + "." + self.signature(value)

    def valid(self, value):
        try:
            payload, signature = value.rsplit(".", 1)
            return int(payload.split(".")[0]) > time.time() and hmac.compare_digest(signature, self.signature(payload))
        except (ValueError, AttributeError, TypeError):
            return False

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        bearer = hmac.compare_digest(headers.get("authorization", "").encode(), ("Bearer " + self.key).encode())
        origin = headers.get("origin")
        # Origin checks apply to all cookie requests, including WebSocket upgrades.
        bad_origin = origin is not None and origin != self.origin
        host_ok = headers.get("host", "") == self.host
        cookies = SimpleCookie()
        try:
            cookies.load(headers.get("cookie", ""))
        except Exception:
            pass
        session = cookies.get(self.cookie)
        authorized = bearer or (host_ok and session is not None and self.valid(session.value))
        path = scope["path"]
        if not bearer and scope.get("method") not in (None, "GET", "HEAD", "OPTIONS") and origin != self.origin:
            bad_origin = True

        async def reject(code, message):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 4401 if code == 401 else 4403})
            else:
                await JSONResponse({"detail": message}, status_code=code)(scope, receive, send)

        if bad_origin or (not host_ok and not bearer):
            return await reject(403, "Unrecognized remote origin")
        # Installation metadata contains no user or host records. Browsers may
        # fetch icons/manifest without the authenticated page's cookies.
        public_app_assets = {
            "/manifest.webmanifest", "/sw.js", "/app/offline.html",
            "/app/icon-192.png", "/app/icon-512.png", "/app/apple-touch-icon.png",
        }
        if scope["type"] == "http" and scope["method"] in ("GET", "HEAD") and path in public_app_assets:
            return await self.app(scope, receive, send)
        if path == "/remote/login" and scope["type"] == "http":
            request = Request(scope, receive)
            error = ""
            if request.method == "POST":
                if origin != self.origin:
                    return await reject(403, "Sign in from this Dear Diane address")
                address = scope.get("client", ("unknown",))[0]
                attempts = [t for t in self.failures.get(address, []) if t > time.time() - 60]
                if len(attempts) >= 10:
                    return await reject(429, "Too many attempts; wait a minute")
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 4096:
                        return await reject(413, "Sign-in request too large")
                key = parse_qs(body.decode(errors="replace")).get("key", [""])[0]
                if hmac.compare_digest(key.encode(), self.key.encode()):
                    self.failures.pop(address, None)
                    response = RedirectResponse("/", status_code=303)
                    response.set_cookie(self.cookie, self.session(), httponly=True, samesite="strict",
                                        secure=self.origin.startswith("https:"), max_age=7 * 86400)
                    response.headers["Cache-Control"] = "no-store"
                    return await response(scope, receive, send)
                self.failures[address] = attempts + [time.time()]
                error = "That access key was not accepted."
            elif request.method != "GET":
                return await reject(405, "Method not allowed")
            page = f'''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Dear Diane · {html.escape(self.machine)}</title>
            <style>body{{font:16px system-ui;background:#f5f2ea;color:#302d28;margin:12vh auto;padding:24px;max-width:400px}}input,button{{font:inherit;padding:12px;box-sizing:border-box;width:100%;margin:12px 0}}button{{cursor:pointer}}p{{line-height:1.5}}</style></head>
            <body><h1>Dear Diane · {html.escape(self.machine)}</h1><p>Use the access key from Dear Diane settings on the computer that set up this connection.</p>
            <form method="post" action="/remote/login"><label for="key">Access key</label><input id="key" name="key" type="password" autocomplete="current-password" required><button>Connect</button><p role="alert">{error}</p></form></body></html>'''
            return await HTMLResponse(page, headers={"Cache-Control": "no-store", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'", "Referrer-Policy": "no-referrer"})(scope, receive, send)
        if not authorized:
            if scope["type"] == "http" and scope["method"] == "GET" and path == "/":
                return await RedirectResponse("/remote/login")(scope, receive, send)
            return await reject(401, "Sign in to this remote Dear Diane")
        if path == "/remote/logout" and scope["type"] == "http" and scope["method"] == "POST":
            response = RedirectResponse("/remote/login", status_code=303)
            response.delete_cookie(self.cookie)
            return await response(scope, receive, send)
        await self.app(scope, receive, send)


def access_config():
    path = os.environ.get("DAN_REMOTE_CONFIG")
    return json.loads(Path(path).read_text()) if path else None
