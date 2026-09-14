"""Synthetic gestures only; no private recordings or live CAPTCHA calls."""
import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'echemi-browser'))
    return importlib.import_module('recorded_motion')


def library():
    return {'version': 1, 'traces': [{'id': 'synthetic',
        'handle': {'x': 400, 'y': 400, 'width': 40, 'height': 40},
        'track': {'x': 400, 'y': 400, 'width': 320, 'height': 40},
        'events': [{'type': kind, 't': t, 'x': x, 'y': 420} for kind, t, x in
                   [('move', 0, 410), ('down', .001, 420), ('move', .002, 700), ('up', .003, 700)]]}]}


def install(tmp_path, data):
    path = tmp_path / 'private.json'
    path.write_text(json.dumps(data))
    return path


def test_load_and_translate_without_stretching(module, tmp_path):
    traces, digest = module.load_recordings(install(tmp_path, library()))
    trace = traces[0]
    mapped = module.mapped_events(trace, dict(trace['handle'], y=200), dict(trace['track'], y=200),
                                  {'w': 1280, 'h': 900})
    assert len(digest) == 16 and [p['y'] for p in mapped] == [220] * 4
    assert [p['t'] for p in mapped] == [p['t'] for p in trace['events']]
    assert trace['events'][0]['y'] == 420


@pytest.mark.parametrize('case', ['nan', 'order', 'double_press', 'no_release', 'off_handle', 'too_long',
                                  'post_release', 'duplicate_id', 'version'])
def test_invalid_library_cannot_reach_mouse(module, tmp_path, case):
    data = library()
    events = data['traces'][0]['events']
    if case == 'nan': events[0]['x'] = float('nan')
    elif case == 'order': events[2]['t'] = 0
    elif case == 'double_press': events[2]['type'] = 'down'
    elif case == 'no_release': events.pop()
    elif case == 'off_handle': events[1]['x'] = 500
    elif case == 'too_long': events[-1]['t'] = 31
    elif case == 'post_release': events.append(dict(events[-1]))
    elif case == 'duplicate_id': data['traces'].append(data['traces'][0])
    elif case == 'version': data['version'] = 2
    with pytest.raises(ValueError): module.load_recordings(install(tmp_path, data))


def test_dimension_and_viewport_mismatch(module):
    trace = library()['traces'][0]
    with pytest.raises(ValueError, match='dimensions'):
        module.mapped_events(trace, trace['handle'], dict(trace['track'], width=330), {'w':1280,'h':900})
    with pytest.raises(ValueError, match='outside viewport'):
        module.mapped_events(trace, dict(trace['handle'], x=1100), dict(trace['track'], x=1100), {'w':1280,'h':900})


@pytest.mark.parametrize('end', ['success', 'epoch_change', 'cancelled', 'hidden'])
def test_replay_order_release_and_no_click_on_disappeared_challenge(module, monkeypatch, tmp_path, end):
    trace = library()['traces'][0]
    monkeypatch.setenv('ECHEMI_CAPTCHA_RECORDINGS_FILE', str(install(tmp_path, library())))
    monkeypatch.setattr(module, 'needs_verification', AsyncMock(return_value=end != 'hidden'))
    context = SimpleNamespace(epoch=1, capture=AsyncMock(return_value=True))
    calls = []
    async def move(x,y):
        calls.append(('move',x,y))
        if x == 700:
            if end == 'epoch_change': context.epoch = 2
            if end == 'cancelled': raise asyncio.CancelledError()
    async def down(): calls.append(('down',))
    async def up(): calls.append(('up',))
    page = SimpleNamespace(mouse=SimpleNamespace(move=move,down=down,up=up),
        locator=lambda _: SimpleNamespace(bounding_box=AsyncMock(return_value=trace['handle'])))
    metrics = {}
    async def run():
        call = module.replay(page, SimpleNamespace(), context, 1, trace['handle'], trace['track'],
                             {'w':1280,'h':900}, metrics)
        if end == 'success': await call
        else:
            with pytest.raises(asyncio.CancelledError if end == 'cancelled' else ValueError): await call
    asyncio.run(run())
    if end == 'hidden': assert calls == [('move',410,420)]
    else: assert calls.count(('up',)) == 1 and calls[-1] == ('up',)
    if end == 'success':
        assert metrics['events_sent'] == 4 and metrics['pressed']
        assert [c[1:] for c in calls if c[0] == 'move'] == [(410,420),(420,420),(700,420),(700,420)]
