import json
import time

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from dan.remote.access import RemoteAccess
from dan.remote.profiles import Profile, read_profiles, save_profile
from dan.server.routers.remote import router


def test_phone_installation_assets_are_public_but_keep_origin_and_private_boundaries():
    app = FastAPI()
    app.add_middleware(RemoteAccess, config={"id": "phone", "url": "https://diane.test", "access_key": "test-only"})

    @app.get("/{path:path}")
    def asset(path: str):
        return {"path": path}

    with TestClient(app, base_url="https://diane.test") as client:
        for path in ("/manifest.webmanifest", "/sw.js", "/app/offline.html", "/app/icon-192.png", "/app/icon-512.png", "/app/apple-touch-icon.png"):
            assert client.get(path).status_code == 200
            assert client.get(path, headers={"Origin": "https://evil.test"}).status_code == 403
            assert client.post(path, headers={"Origin": "https://diane.test"}).status_code == 401
        for path in ("/index.html", "/api/personal/commitments", "/app/private.json", "/assets/workspace.js"):
            assert client.get(path).status_code == 401


def test_remote_boundary_covers_files_api_ws_origin_and_expiry():
    app = FastAPI()
    config = {"id": "mini", "url": "http://relay:8765", "access_key": "test-secret"}
    app.add_middleware(RemoteAccess, config=config)

    @app.get("/api/file")
    def file():
        return {"private": True}

    @app.post("/api/write")
    def write():
        return {"ok": True}

    @app.websocket("/events")
    async def events(ws: WebSocket):
        await ws.accept()
        await ws.send_text("connected")
        await ws.close()

    with TestClient(app, base_url=config["url"]) as client:
        assert client.get("/api/file").status_code == 401
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/events"):
                pass
        assert client.get("/", follow_redirects=False).headers["location"] == "/remote/login"
        assert client.post("/remote/login", data={"key": "test-secret"}).status_code == 403
        assert client.post("/remote/login", headers={"Origin": config["url"]}, data={"key": "错误"}).status_code == 200
        response = client.post("/remote/login", headers={"Origin": config["url"]}, data={"key": "test-secret"}, follow_redirects=False)
        assert response.status_code == 303
        assert "HttpOnly" in response.headers["set-cookie"]
        assert client.get("/api/file").status_code == 200
        assert client.get("/api/file", headers={"Origin": "http://evil"}).status_code == 403
        assert client.post("/api/write").status_code == 403
        assert client.post("/api/write", headers={"Origin": config["url"]}).status_code == 200
        with client.websocket_connect("ws://relay:8765/events", headers={"Origin": config["url"]}) as ws:
            assert ws.receive_text() == "connected"
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/events", headers={"Origin": "http://evil"}):
                pass
        assert client.get("/api/file", headers={"Host": "other:8765"}).status_code == 403
        boundary = RemoteAccess(app, config=config)
        payload = f"{int(time.time())-1}.old"
        assert not boundary.valid(payload + "." + boundary.signature(payload))
        assert not boundary.valid("malformed")
        assert client.post("/remote/logout", headers={"Origin": config["url"]}, follow_redirects=False).status_code == 303
        assert client.get("/api/file").status_code == 401
        assert client.get("/api/file", headers={"Authorization": "Bearer test-secret"}).status_code == 200


def test_profile_persistence_validation_and_local_only_management(monkeypatch, tmp_path):
    monkeypatch.setenv("DAN_GRAPHS_DIR", str(tmp_path))
    monkeypatch.delenv("DAN_REMOTE_CONFIG", raising=False)
    p = Profile(id="mini", name="Mini", ssh_alias="mini", address="10.77.77.3")
    saved = save_profile(p)
    assert "access_key" not in saved
    key = read_profiles()["mini"]["access_key"]
    save_profile(p.model_copy(update={"name": "Renamed"}))
    assert read_profiles()["mini"]["access_key"] == key
    assert (tmp_path / "remote_connections/profiles.json").stat().st_mode & 0o777 == 0o600
    values = read_profiles()
    values["mini"].update(installed=True, execution={"boot_persistent": True})
    from dan.remote.profiles import write_profiles
    write_profiles(values)
    renamed = save_profile(p.model_copy(update={"name": "Another name"}))
    assert renamed["installed"] and renamed["execution"]["boot_persistent"]
    for updates in ({"ssh_alias": "-oProxyCommand=evil"}, {"address": "0.0.0.0"}, {"relay_address": "8.8.8.8"}, {"workspace": "/tmp\nExecStart=evil"}, {"id": "../escape"}):
        with pytest.raises(ValueError):
            Profile.model_validate({**p.model_dump(), **updates})
    with pytest.raises(ValueError, match="Another machine"):
        save_profile(p.model_copy(update={"id": "s600"}))
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        assert client.get("/api/remote/connections").status_code == 200
        assert "access_key" not in client.get("/api/remote/connections").text
        assert client.post("/api/remote/connections/mini/access-key", headers={"Origin": "https://evil.test"}).status_code == 403
        assert client.get("/api/remote/connections", headers={"Host": "evil.test"}).status_code == 403
        monkeypatch.setenv("DAN_REMOTE_CONFIG", "/not/read/for/actions")
        assert client.post("/api/remote/connections/mini/inspect").status_code == 403


@pytest.mark.asyncio
async def test_relay_streams_bidirectionally_and_closes():
    import asyncio
    from dan.remote.relay import forward
    async def echo(reader, writer):
        while chunk := await reader.read(100):
            writer.write(chunk)
            await writer.drain()
        writer.close()
    target = await asyncio.start_server(echo, "127.0.0.1", 0)
    port = target.sockets[0].getsockname()[1]
    relay = await asyncio.start_server(lambda r, w: forward(r, w, "127.0.0.1", port), "127.0.0.1", 0)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", relay.sockets[0].getsockname()[1])
        for data in (b"hello", b"streaming"):
            writer.write(data)
            await writer.drain()
            assert await asyncio.wait_for(reader.readexactly(len(data)), 2) == data
        writer.close()
        await writer.wait_closed()
    finally:
        relay.close()
        target.close()
        await relay.wait_closed()
        await target.wait_closed()


def test_registry_merges_independent_device_edits(monkeypatch, tmp_path):
    from dan.server.routers.remote_registry import router as registry
    monkeypatch.setenv("DAN_REMOTE_CONFIG", "enabled")
    monkeypatch.setenv("DAN_GRAPHS_DIR", str(tmp_path))
    monkeypatch.setenv("DAN_WORKSPACE_ROOT", str(tmp_path))
    app = FastAPI()
    app.include_router(registry)
    with TestClient(app) as client:
        original = client.get("/api/remote/registry").json()
        assert original["projects"]["remote-default"]["pinnedPaths"] == [str(tmp_path)]
        assert client.patch("/api/remote/registry", json={"projects": {"remote-default": {"name": "From phone"}}}).status_code == 200
        assert client.patch("/api/remote/registry", json={"projects": {"remote-default": {"color": "green"}}}).status_code == 200
        result = client.get("/api/remote/registry").json()["projects"]["remote-default"]
        assert result["name"] == "From phone"
        assert result["color"] == "green"
        assert client.patch("/api/remote/registry", json={"projects": {"remote-default": {"id": "other"}}}).status_code == 422


@pytest.mark.asyncio
async def test_bootstrap_only_provisions_provider_key_on_execution_host(monkeypatch, tmp_path):
    import httpx
    from dan.remote import profiles
    monkeypatch.setenv("DAN_GRAPHS_DIR", str(tmp_path))
    profile = Profile(id="mini", name="Mini", ssh_alias="mini", address="10.77.77.3")
    save_profile(profile)
    archive = tmp_path / "release.tar.gz"
    archive.write_bytes(b"test bundle")
    monkeypatch.setattr(profiles, "build_bundle", lambda: archive)
    monkeypatch.setattr("dan.native_workers.models.openrouter_key", lambda: "provider-test-secret")
    calls = []
    async def command(args, *, data=None, timeout=30):
        if data:
            calls.append(json.loads(data))
        return json.dumps({"active": True, "boot_persistent": True})
    monkeypatch.setattr(profiles, "command", command)
    async def get(self, url, **kwargs):
        return httpx.Response(200, json={"status": "ok"}, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    await profiles.deploy(read_profiles()["mini"], share_openrouter=True)
    execution, relay = calls
    assert execution["openrouter_key"] == "provider-test-secret"
    assert "openrouter_key" not in relay and "access_key" not in relay
    assert read_profiles()["mini"]["installed"] is True
    assert not archive.exists()


def test_service_paths_use_systemd_directive_syntax(monkeypatch, tmp_path):
    from dan.remote import install
    from pathlib import Path
    from types import SimpleNamespace
    home = tmp_path / "a home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("USER", "operator")
    monkeypatch.setattr(install, "run", lambda args, **kw: "Linger=yes" if args[0] == "loginctl" else "active")
    monkeypatch.setattr(install.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0))
    monkeypatch.setattr(time, "sleep", lambda _: None)
    result = install.install({"role": "relay", "id": "mini", "relay_source": "# relay", "relay_address": "10.77.77.1", "relay_port": 8765, "address": "10.77.77.3", "port": 8765})
    unit = (home / ".config/systemd/user/dan-relay-mini.service").read_text()
    assert f"WorkingDirectory={home}/.local/share/dan-remote/mini\n" in unit
    assert result["boot_persistent"] is True


def test_manual_ssh_profiles_without_relay_and_create_collision(monkeypatch, tmp_path):
    from dan.remote import profiles
    monkeypatch.setenv("DAN_GRAPHS_DIR", str(tmp_path))
    monkeypatch.delenv("DAN_REMOTE_CONFIG", raising=False)
    profile = Profile(id="manual", name="Manual", ssh_alias="adam@server.example", ssh_port=2222,
                      identity_file="~/.ssh/my key", relay_enabled=False, ssh_via_relay=False)
    assert profile.url is None
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        body = profile.model_dump()
        assert client.put("/api/remote/connections/manual", json=body, headers={"If-None-Match": "*"}).status_code == 200
        assert client.put("/api/remote/connections/manual", json={**body, "name": "Overwrite"}, headers={"If-None-Match": "*"}).status_code == 409
        assert read_profiles()["manual"]["name"] == "Manual"
        assert client.post("/api/remote/connections/manual/install", json={}).status_code == 409
        assert "access_key" not in client.get("/api/remote/connections").text
    # Two SSH-only profiles need not reserve private relay ports.
    save_profile(profile.model_copy(update={"id": "second"}))
    for update in ({"ssh_port": 0}, {"ssh_port": 65536}, {"identity_file": "-oProxyCommand=bad"}, {"identity_file": "/tmp/key\nextra"}, {"relay_enabled": True}):
        with pytest.raises(ValueError):
            Profile.model_validate({**profile.model_dump(), **update})
    # Introducing defaults must not mark an unchanged legacy install as stale.
    legacy = Profile(id="mini", name="Mini", ssh_alias="mini", address="10.77.77.3").model_dump()
    for key in ("ssh_port", "identity_file", "relay_enabled"):
        legacy.pop(key)
    profiles.write_profiles({"mini": {**legacy, "installed": True, "access_key": "keep"}})
    assert save_profile(Profile.model_validate({**legacy, "name": "Rename"}))["installed"]


def test_ssh_alias_suggestions_include_files_and_stay_local(monkeypatch, tmp_path):
    from pathlib import Path
    from dan.remote import profiles
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("DAN_REMOTE_CONFIG", raising=False)
    folder = tmp_path / ".ssh"
    folder.mkdir()
    (folder / "config").write_text('Host mini s600 *.internal !excluded\n IdentityFile /secret/key\nInclude "extra config"\nHost=ny\n')
    (folder / "extra config").write_text('Host work-host\nInclude config\nHost *\n')
    assert profiles.ssh_hosts() == ["mini", "ny", "s600", "work-host"]
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get("/api/remote/ssh-hosts")
        assert response.json() == {"hosts": ["mini", "ny", "s600", "work-host"]}
        assert "secret" not in response.text
        assert client.get("/api/remote/ssh-hosts", headers={"Origin": "https://evil.test"}).status_code == 403
        monkeypatch.setenv("DAN_REMOTE_CONFIG", "remote")
        assert client.get("/api/remote/ssh-hosts").status_code == 403


@pytest.mark.asyncio
async def test_manual_port_and_identity_reach_inspection_and_upload(monkeypatch, tmp_path):
    import httpx
    from dan.remote import profiles
    monkeypatch.setenv("DAN_GRAPHS_DIR", str(tmp_path))
    p = Profile(id="manual", name="Manual", ssh_alias="adam@server.example", address="10.77.77.3", ssh_port=2222, identity_file=str(tmp_path / "key with spaces"), ssh_via_relay=False)
    save_profile(p)
    calls = []
    async def command(args, **kwargs):
        calls.append(args)
        return json.dumps({"hostname": "test", "boot_persistent": True})
    monkeypatch.setattr(profiles, "command", command)
    await profiles.inspect(p)
    assert calls[0][-2] == p.ssh_alias
    assert calls[0][calls[0].index("-p") + 1] == "2222"
    assert calls[0][calls[0].index("-i") + 1] == p.identity_file
    assert "-J" not in calls[0]
    archive = tmp_path / "release.tar.gz"
    archive.write_bytes(b"fixture")
    monkeypatch.setattr(profiles, "build_bundle", lambda: archive)
    async def get(self, url, **kwargs):
        return httpx.Response(200, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    await profiles.deploy(read_profiles()[p.id])
    upload, execution, relay = calls[1:]
    assert upload[0] == "scp" and upload[upload.index("-P") + 1] == "2222"
    assert upload[upload.index("-i") + 1] == p.identity_file
    assert execution[execution.index("-p") + 1] == "2222"
    assert "-i" not in relay and "-p" not in relay
