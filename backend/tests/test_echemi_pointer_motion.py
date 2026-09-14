import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def motion(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'echemi-browser'))
    return importlib.import_module('pointer_motion')


@pytest.mark.parametrize('latency', [.001, .025, .08])
def test_slow_transport_skips_stale_samples_and_still_reaches_endpoint(motion, latency):
    now, sent, updates = [0.0], [], []
    async def sleep(delay): now[0] += delay
    async def move(x, y):
        sent.append((now[0], x, y))
        now[0] += latency
    # Replaying all 181 samples would take 14.48s at 80ms per CDP move.
    points = [(i / 180, i * 280 / 180, 40) for i in range(181)]
    stats = {}
    asyncio.run(motion.move_timed(SimpleNamespace(move=move), points,
        update=lambda x, y: updates.append((x, y)), metrics=stats,
        clock=lambda: now[0], sleep=sleep))
    assert sent[-1][1:] == pytest.approx((280, 40))
    assert updates[-1] == pytest.approx((280, 40))
    assert 1 <= stats['actual_seconds'] <= 1 + 2 * latency + .021
    assert len(sent) < 60
    assert all(b[1] >= a[1] for a, b in zip(sent, sent[1:]))
    # The position follows wall time, rather than a growing backlog of sample IDs.
    assert all(x == pytest.approx(min(t, 1) * 280) for t, x, _ in sent)


def test_track_padding_and_recording_tail_do_not_move_beyond_endpoint(motion):
    handle = {'x': 102, 'y': 50, 'width': 40, 'height': 40}
    track = {'x': 100, 'y': 50, 'width': 320, 'height': 40}
    points = [(0, 0, 0), (.5, 140, 13), (1, 280, 13), (1.2, 479, 21)]
    path = motion.slider_path(points, handle, track)
    assert path[0] == (0, 122, 70)
    assert path[-1] == (1, 400, 70)
    assert all(122 <= x <= 400 and 60 <= y <= 80 for _, x, y in path)


def test_repository_recording_is_trimmed_at_actual_drag_end(motion):
    file = Path(__file__).resolve().parents[2] / 'echemi-browser' / 'trajectory.json'
    points = json.loads(file.read_text())
    path = motion.slider_path(points, {'x': 10, 'y': 10, 'width': 40, 'height': 40},
                              {'x': 10, 'y': 10, 'width': 320, 'height': 40})
    assert 2.34 < path[-1][0] < 2.36
    assert path[-1][1:] == (310, 30)


@pytest.mark.parametrize('points', [[(0, 0, 0)], [(0, 0, 0), (0, 1, 1)],
                                  [(0, 0, 0), (1, float('nan'), 0)]])
def test_invalid_timeline_never_moves_pointer(motion, points):
    async def move(*args): raise AssertionError('Must not send invalid input')
    with pytest.raises(ValueError):
        asyncio.run(motion.move_timed(SimpleNamespace(move=move), points))


@pytest.mark.parametrize('failure', [ValueError, asyncio.CancelledError])
def test_drag_always_releases_button_and_keeps_partial_metrics(motion, monkeypatch, failure):
    probe = importlib.import_module('captcha_probe')
    released, pressed = [], []
    h = {'x': 10, 'y': 10, 'width': 40, 'height': 40}
    t = {'x': 10, 'y': 10, 'width': 320, 'height': 40}
    async def noop(*args, **kwargs): pass
    class Locator:
        def __init__(self, box): self.box = box
        wait_for = scroll_into_view_if_needed = noop
        async def bounding_box(self): return self.box
    async def viewport(*args): return {'w': 1280, 'h': 900}
    async def capture(): return True
    async def down(): pressed.append(True)
    async def up(): released.append(True)
    async def fail(*args, metrics, **kwargs):
        metrics.update(moves_sent=1, actual_seconds=.1)
        raise failure()
    page = SimpleNamespace(locator=lambda name: Locator(h if name.endswith('-slider') else t),
                           evaluate=viewport, mouse=SimpleNamespace(down=down, up=up))
    monkeypatch.setattr(probe.asyncio, 'sleep', noop)
    monkeypatch.setattr(probe, 'move_timed', fail)
    stats = {}
    with pytest.raises(failure):
        asyncio.run(probe.drag(page, SimpleNamespace(go=noop),
            SimpleNamespace(epoch=1, capture=capture), 1, metrics=stats))
    assert pressed == released == [True]
    assert stats['pressed'] and stats['moves_sent'] == 1


def test_diagnostics_keep_only_uuid_request_id_and_explain_risk_code(motion):
    diagnostics = importlib.import_module('diagnostics')
    body = {'RequestId': '12345678-1234-1234-1234-123456789abc',
            'Result': {'VerifyCode': 'F001', 'VerifyResult': False, 'securityToken': 'SECRET'}}
    result = diagnostics.verification_result(body)
    assert result['request_id'] == body['RequestId'] and 'SECRET' not in str(result)
    assert 'оценки риска' in diagnostics.rejection_message([{'verification': result}])
    assert diagnostics.rejection_message([{'verification': result},
        {'verification': {'verify_result': True, 'verify_code': 'T001'}}]) == ''
    body['RequestId'] = 'not-a-request-id-SECRET'
    assert 'request_id' not in diagnostics.verification_result(body)
