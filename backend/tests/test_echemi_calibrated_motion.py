import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def probe(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'echemi-browser'))
    module = importlib.import_module('captcha_probe')
    monkeypatch.setattr(module, 'MOTION_PROFILE', 'calibrated_v2')
    return module


def test_calibration_uses_relative_grip_two_segments_and_bounded_overshoot(probe):
    h = {'x': 100, 'y': 200, 'width': 40, 'height': 40}
    t = {**h, 'width': 320}
    first, second = probe.calibrated_segments(h, t)
    assert first[0] == pytest.approx((0, 116.56, 227.04))
    assert first[-1][0] + .015 + second[-1][0] == pytest.approx(1.49)
    assert second[-1][1] - first[0][1] == pytest.approx(329.28)
    assert max(abs(y - first[0][2]) for part in (first, second) for _, _, y in part) <= 1.281
    assert second[0][1] - first[-1][1] == pytest.approx(6.44)


@pytest.mark.parametrize('state', ['kept', 'reset', 'changed', 'offscreen', 'invalid_handle'])
def test_calibrated_drag_checks_regrip_and_always_releases(probe, monkeypatch, state):
    initial = {'x': 10, 'y': 10, 'width': 40, 'height': 40}
    track = {**initial, 'width': 320}
    calls, boxes = [], [0]
    context = SimpleNamespace(epoch=1, capture=AsyncMock(return_value=True))
    class Handle:
        wait_for = scroll_into_view_if_needed = AsyncMock()
        async def bounding_box(self):
            boxes[0] += 1
            if boxes[0] > 2 and state == 'invalid_handle': return {**initial, 'x': 2000}
            return {**initial, 'x': 58.44} if boxes[0] > 2 and state != 'reset' else initial
    handle = Handle()
    page = SimpleNamespace(locator=lambda selector: handle if selector.endswith('-slider') else
                           SimpleNamespace(bounding_box=AsyncMock(return_value=track)),
                           evaluate=AsyncMock(return_value={'w': 200 if state == 'offscreen' else 1280, 'h': 900}))
    async def down(): calls.append('down')
    async def up():
        calls.append('up')
        if state == 'changed': context.epoch += 1
    page.mouse = SimpleNamespace(down=down, up=up, move=AsyncMock())
    async def play(pointer, path, **kwargs):
        kwargs['check']()
        kwargs['metrics'].update(moves_sent=4, max_move_seconds=.02, actual_seconds=path[-1][0])
    monkeypatch.setattr(probe, 'move_timed', play)
    monkeypatch.setattr(probe.asyncio, 'sleep', AsyncMock())
    stats = {}
    async def run():
        await probe.drag(page, SimpleNamespace(go=AsyncMock()), context, 1, metrics=stats)
    if state in {'kept', 'reset'}:
        asyncio.run(run())
        assert calls == ['down', 'up', 'down', 'up']
        assert stats['regrips'] == 1 and stats['moves_sent'] == 8
        assert stats['profile'] == 'calibrated_v2' and stats['planned_seconds'] == 1.49
        assert stats['regrip_start'][0] == pytest.approx(81.44 if state == 'kept' else 33)
    else:
        with pytest.raises(ValueError): asyncio.run(run())
        if state == 'offscreen': assert calls == []
        else:
            assert calls == ['down', 'up', 'up']
            assert stats['regrips'] == 0


@pytest.mark.parametrize('mode', ['fresh', 'missing', 'stale', 'uninitialized'])
def test_refresh_requires_new_initialized_context_and_falls_back(probe, monkeypatch, mode):
    now = [0.]
    monkeypatch.setattr(probe.time, 'monotonic', lambda: now[0])
    async def sleep(delay): now[0] += delay
    monkeypatch.setattr(probe.asyncio, 'sleep', sleep)
    context = SimpleNamespace(epoch=1, capture=AsyncMock(return_value=mode != 'uninitialized'))
    async def down():
        if mode in {'fresh', 'uninitialized'}: context.epoch = 2
    async def reload(**kwargs): context.epoch += 1
    page = SimpleNamespace(locator=lambda _: SimpleNamespace(
        is_visible=AsyncMock(return_value=mode != 'missing'),
        bounding_box=AsyncMock(return_value={'x':700,'y':200,'width':14,'height':14})),
        mouse=SimpleNamespace(down=AsyncMock(side_effect=down),up=AsyncMock()),
        reload=AsyncMock(side_effect=reload))
    answer = asyncio.run(probe.refresh_challenge(page, SimpleNamespace(go=AsyncMock()), context))
    assert answer == {'method':'widget' if mode == 'fresh' else 'page_reload', 'fresh_context':True}
    if mode == 'fresh': page.reload.assert_not_awaited()
    else: page.reload.assert_awaited_once()


def test_legacy_profile_is_available_for_control_comparison(probe, monkeypatch):
    monkeypatch.setattr(probe, 'MOTION_PROFILE', 'legacy_v1')
    h = {'x':10,'y':10,'width':40,'height':40}
    t = {**h,'width':320}
    handle = SimpleNamespace(wait_for=AsyncMock(),scroll_into_view_if_needed=AsyncMock(),
                             bounding_box=AsyncMock(return_value=h))
    page = SimpleNamespace(locator=lambda s:handle if s.endswith('-slider') else
                           SimpleNamespace(bounding_box=AsyncMock(return_value=t)),
                           evaluate=AsyncMock(return_value={'w':1280,'h':900}),
                           mouse=SimpleNamespace(down=AsyncMock(),up=AsyncMock()))
    async def play(*args, **kwargs): kwargs['metrics'].update(moves_sent=3)
    monkeypatch.setattr(probe,'move_timed',play)
    monkeypatch.setattr(probe.asyncio,'sleep',AsyncMock())
    stats={}
    asyncio.run(probe.drag(page,SimpleNamespace(go=AsyncMock()),
                          SimpleNamespace(epoch=1,capture=AsyncMock(return_value=True)),1,metrics=stats))
    assert stats['profile']=='legacy_v1' and stats['regrips']==0
    assert 2.34 < stats['planned_seconds'] < 2.36
    page.mouse.down.assert_awaited_once()
