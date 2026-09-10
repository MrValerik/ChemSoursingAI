import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.websockets import WebSocketDisconnect
from app.api import echemi_manual
from app.models import User
from app.models.echemi_search import EchemiSearch
from app.models.enums import UserRole
from app.core.security import create_access_token
from tests.test_echemi_search import env


def prepare(env, role=UserRole.BUYER, author=42, active=True, status="running"):
    client, user, sessions = env
    with sessions() as db:
        User.__table__.create(db.get_bind())
        db.add(User(id=42, username="manual", full_name="Test", password_hash="unused",
                    role=role, is_active=active))
        row = EchemiSearch(author_id=author, query="synthetic", status=status)
        db.add(row); db.commit()
        return row.id


@pytest.mark.parametrize("role,author,active,status,code", [
    (UserRole.BUYER,43,True,"running",4403),
    (UserRole.AUDITOR,42,True,"running",4403),
    (UserRole.BUYER,42,False,"running",4403),
    (UserRole.BUYER,42,True,"completed",4403),
])
def test_control_rejects_unauthorized(env, role, author, active, status, code):
    sid = prepare(env,role,author,active,status)
    with env[0].websocket_connect(f"/echemi-searches/{sid}/manual") as ws:
        ws.send_text(create_access_token("manual", role.value))
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_bytes()
        assert exc.value.code == code


def test_bad_token(env):
    with env[0].websocket_connect("/echemi-searches/1/manual") as ws:
        ws.send_text("invalid")
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_bytes()
        assert exc.value.code == 4401


@pytest.mark.parametrize("role,author", [(UserRole.BUYER,42),(UserRole.HEAD,43),(UserRole.ADMIN,43)])
def test_authorized_relay(env, monkeypatch, role, author):
    sid = prepare(env, role, author)
    sent = []
    class Remote:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        def __aiter__(self): return self
        async def __anext__(self):
            if sent: raise StopAsyncIteration
            await asyncio.sleep(.01)
            return b"synthetic-jpeg"
        async def send(self,data): sent.append(data)
    monkeypatch.setattr(echemi_manual,"manual_connection",lambda *a,**kw: Remote())
    with env[0].websocket_connect(f"/echemi-searches/{sid}/manual") as ws:
        ws.send_text(create_access_token("manual",role.value))
        assert ws.receive_bytes() == b"synthetic-jpeg"
        ws.send_text('{"type":"down","x":100,"y":200}')
        while True:
            try: ws.receive_bytes()
            except WebSocketDisconnect: break
    assert sent == ['{"type":"down","x":100,"y":200}']


def manual_module(monkeypatch):
    path = Path(__file__).resolve().parents[2]/"echemi-browser"
    monkeypatch.syspath_prepend(str(path))
    spec = importlib.util.spec_from_file_location("echemi_manual_test",path/"manual.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("event", [None, {"type":"click","x":1,"y":1},
    {"type":"down","x":True,"y":2}, {"type":"move","x":float("nan"),"y":2},
    {"type":"up","x":1280,"y":2}, {"type":"down","x":-1,"y":2}])
def test_rejects_invalid_pointer(monkeypatch,event):
    with pytest.raises(ValueError): manual_module(monkeypatch).coordinates(event)


def test_manual_success_and_timeout(monkeypatch):
    module = manual_module(monkeypatch)
    class Page:
        def locator(self,_): return self
        async def inner_text(self): return "Products"
        async def title(self): return "Echemi"
    async def run():
        events=[]
        assert await module.wait_for_human(Page(),events,.1)
        assert events[-1]["status"]=="passed" and not module.active["waiting"]
        assert not await module.wait_for_human(Page(),events,0)
        assert events[-1]["status"]=="manual_timeout" and not module.active["waiting"]
        assert module.coordinates({"type":"move","x":1279,"y":899})==(1279,899)
    asyncio.run(run())
