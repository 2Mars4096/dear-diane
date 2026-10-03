import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from diane.server.routers.native_workers import setup_status, connect_openrouter, ProviderKeyInput


def request(remote=False):
    return SimpleNamespace(headers={'host':'testserver'},client=SimpleNamespace(host='outside' if remote else 'testclient'))


def test_existing_key_status_and_remote_setup_restriction(monkeypatch):
    monkeypatch.delenv('DAN_REMOTE_CONFIG',raising=False)
    monkeypatch.setattr('diane.native_workers.models.provider_key',lambda _: 'secret')
    assert setup_status(request()) == {'ready':True,'local':True}
    assert setup_status(request(True)) == {'ready':True,'local':False}
    with pytest.raises(HTTPException) as error:
        asyncio.run(connect_openrouter(ProviderKeyInput(api_key='key'),request(True)))
    assert error.value.status_code == 403


@pytest.mark.parametrize('status,data,expected',[(200,{'limit_remaining':None},True),(401,{},False),(200,{'limit_remaining':0},False),(200,{'is_management_key':True},False),(503,{},False)])
def test_validate_before_save(monkeypatch,status,data,expected):
    monkeypatch.delenv('DAN_REMOTE_CONFIG',raising=False)
    saved=[]
    monkeypatch.setattr('diane.native_workers.provider_credentials.save_key',lambda *args:saved.append(args))
    client=AsyncMock()
    client.__aenter__.return_value=client
    client.get.return_value=SimpleNamespace(status_code=status,json=lambda:{'data':data})
    monkeypatch.setattr('httpx.AsyncClient',lambda **_:client)
    if expected:
        assert asyncio.run(connect_openrouter(ProviderKeyInput(api_key=' test-key '),request()))['ready']
        assert saved == [('openrouter','test-key')]
    else:
        with pytest.raises(HTTPException):asyncio.run(connect_openrouter(ProviderKeyInput(api_key='test-key'),request()))
        assert not saved
