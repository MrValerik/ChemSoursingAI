import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests.test_echemi_manual_recording import Socket, setup


def test_native_control_preserves_order_without_dom_roundtrip_per_move(monkeypatch):
    manual, event, page = setup(monkeypatch)
    module = importlib.import_module('native_control')
    monkeypatch.setattr(module, 'manual', manual)
    monkeypatch.setattr(module, 'needs_verification', AsyncMock(return_value=True))
    session = SimpleNamespace(send=AsyncMock(), on=lambda *args: None, detach=AsyncMock())
    page.context = SimpleNamespace(new_cdp_session=AsyncMock(return_value=session))
    messages = [{'type': kind, 'x': 500+i, 'y': 460, 't_ms': i*30} for i, kind in
                enumerate(['move','down']+['move']*30+['up'])]
    socket = Socket(messages)
    asyncio.run(module.control(socket, 7))
    assert len(event['recordings'][0]['events']) == len(messages)
    assert page.mouse.move.await_count == len(messages)
    assert module.needs_verification.await_count < 10
    assert len([m for m in socket.messages if m['type']=='ack']) == len(messages)
    page.mouse.down.assert_awaited_once()
    page.mouse.up.assert_awaited_once()
    session.detach.assert_awaited_once()
    assert not manual.active['controller']


def test_native_frame_mismatch_stops_before_input(monkeypatch):
    manual, event, page = setup(monkeypatch)
    module = importlib.import_module('native_control')
    monkeypatch.setattr(module, 'manual', manual)
    monkeypatch.setattr(module, 'needs_verification', AsyncMock(return_value=True))
    callbacks = {}
    async def send(method, *args):
        if method == 'Page.startScreencast':
            callbacks['Page.screencastFrame']({'sessionId':1,'data':'AA==',
                'metadata':{'deviceWidth':1280,'deviceHeight':875}})
            await asyncio.sleep(0)
    session = SimpleNamespace(send=send, on=lambda name,fn: callbacks.update({name:fn}), detach=AsyncMock())
    page.context = SimpleNamespace(new_cdp_session=AsyncMock(return_value=session))
    asyncio.run(module.control(Socket([{'type':'down','x':500,'y':460}]),7))
    page.mouse.down.assert_not_awaited()
    assert event['recordings'][0]['stop_reason'] == 'frame_geometry_mismatch'
