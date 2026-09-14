import asyncio
import importlib
import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def motion(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'echemi-browser'))
    return importlib.import_module('pointer_motion')


@pytest.mark.parametrize('latency', [.001, .025, .08, .3])
def test_slow_delivery_keeps_small_steps_and_never_jumps_ahead(motion, latency):
    now, sent, updates = [0.], [], []
    async def sleep(delay): now[0] += delay
    async def move(x, y):
        sent.append((now[0], x, y))
        now[0] += latency
    stats = {}
    asyncio.run(motion.move_continuous(SimpleNamespace(move=move), [(0, 10, 20), (1, 290, 20)],
        clock=lambda: now[0], sleep=sleep, metrics=stats, update=lambda x,y:updates.append((x,y))))
    assert sent[0][1:] == (10, 20) and sent[-1][1:] == (290, 20)
    assert len(sent) == 71 and updates == [p[1:] for p in sent]
    assert all(0 < b[1]-a[1] <= 4.000001 and a[2] == b[2] for a,b in zip(sent,sent[1:]))
    assert all(b[0]-a[0] >= max(latency, 1/70)-1e-8 for a,b in zip(sent,sent[1:]))
    assert stats['actual_seconds'] == pytest.approx(70*max(latency, 1/70)+latency, abs=.001)
    assert stats['max_step_pixels'] == pytest.approx(4)


@pytest.mark.parametrize('width', [41, 320, 800])
def test_single_stroke_is_horizontal_monotonic_and_ends_inside_track(motion, width):
    handle={'x':102,'y':50,'width':40,'height':40}
    track={'x':100,'y':50,'width':width+2,'height':40}
    path=motion.smooth_slider_path(handle,track)
    assert path[0] == (0,122,70)
    assert path[-1] == (3,82+width,70)
    steps=[b[1]-a[1] for a,b in zip(path,path[1:])]
    assert all(0 < dx <= 4.000001 for dx in steps)
    assert all(y == 70 for _,_,y in path)
    if len(steps)>3:
        assert steps[0] < max(steps)/4 and steps[-1] < max(steps)/4


@pytest.mark.parametrize('points,max_step', [([(0,0,0)],4), ([(0,0,0),(0,1,1)],4),
    ([(0,0,0),(1,math.nan,0)],4), ([(0,0,0),(1,10,0)],0), ([(0,0,0),(1,10,0)],math.inf)])
def test_invalid_path_is_rejected_before_mouse_input(motion, points, max_step):
    pointer=SimpleNamespace(move=AsyncMock())
    with pytest.raises(ValueError):asyncio.run(motion.move_continuous(pointer,points,max_step=max_step))
    pointer.move.assert_not_awaited()


@pytest.mark.parametrize('change', [{'width':0},{'x':float('inf')},{'x':-50},{'x':400}])
def test_invalid_handle_does_not_produce_a_drag(motion, change):
    h={'x':100,'y':50,'width':40,'height':40}
    with pytest.raises(ValueError):motion.smooth_slider_path({**h,**change},{**h,'width':320})


@pytest.mark.parametrize('outcome', ['complete','changed','cancelled','transport_error'])
def test_smooth_drag_holds_once_and_releases_on_every_exit(motion, monkeypatch, outcome):
    probe=importlib.import_module('captcha_probe')
    monkeypatch.setattr(probe,'MOTION_PROFILE','smooth_v3')
    h={'x':10,'y':10,'width':40,'height':40};track={**h,'width':320}
    locator=SimpleNamespace(wait_for=AsyncMock(),scroll_into_view_if_needed=AsyncMock(),bounding_box=AsyncMock(return_value=h))
    context=SimpleNamespace(epoch=1,capture=AsyncMock(return_value=True))
    calls=[];now=[0.]
    async def sleep(delay):now[0]+=delay
    async def down():calls.append(('down',))
    async def up():calls.append(('up',))
    async def move(x,y):
        calls.append(('move',x,y));now[0]+=.08
        if len(calls)==4:
            if outcome=='changed':context.epoch=2
            if outcome=='cancelled':raise asyncio.CancelledError()
            if outcome=='transport_error':raise RuntimeError('Synthetic transport error')
    page=SimpleNamespace(locator=lambda s:locator if s.endswith('-slider') else SimpleNamespace(bounding_box=AsyncMock(return_value=track)),
                         evaluate=AsyncMock(return_value={'w':1280,'h':900}),mouse=SimpleNamespace(move=move,down=down,up=up))
    async def play(pointer,path,**kwargs):
        await motion.move_continuous(pointer,path,clock=lambda:now[0],sleep=sleep,**kwargs)
    monkeypatch.setattr(probe,'move_continuous',play)
    monkeypatch.setattr(probe.asyncio,'sleep',sleep)
    stats={};mouse=SimpleNamespace(go=AsyncMock())
    task=probe.drag(page,mouse,context,1,metrics=stats)
    if outcome=='complete':asyncio.run(task)
    else:
        with pytest.raises({'changed':ValueError,'cancelled':asyncio.CancelledError,'transport_error':RuntimeError}[outcome]):asyncio.run(task)
    assert [x[0] for x in calls if x[0]!='move'] == ['down','up']
    assert calls[-1] == ('up',) and stats['regrips']==0 and stats['pressed']
    moves=[x[1:] for x in calls if x[0]=='move']
    assert all(0 <= b[0]-a[0] <= 4.000001 and a[1]==b[1] for a,b in zip(moves,moves[1:]))
    if outcome=='complete':
        assert moves[-1] == (310,30) and (mouse.x,mouse.y)==(310,30)
        assert stats['moves_sent'] > 100 and stats['max_step_pixels'] <= 4.000001
        assert stats['planned_seconds']==3 and len(stats['segments'])==1
