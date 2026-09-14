"""Pointer playback with explicit timing and spatial step bounds."""
import asyncio
import math
import time


async def move_continuous(pointer, points, *, max_step=4, update=None, check=None,
                          metrics=None, clock=time.monotonic, sleep=asyncio.sleep):
    """Keep every small step; slow transport lengthens the motion, never jumps it."""
    if (len(points) < 2 or points[0][0] != 0
            or not math.isfinite(max_step) or max_step <= 0
            or any(not math.isfinite(v) for point in points for v in point)
            or any(b[0] <= a[0] for a, b in zip(points, points[1:]))):
        raise ValueError("Invalid pointer timeline")
    path = [points[0]]
    for a, b in zip(points, points[1:]):
        count = max(1, math.ceil(math.hypot(b[1]-a[1], b[2]-a[2]) / max_step))
        for i in range(1, count + 1):
            path.append(tuple(av + (bv-av) * i/count for av, bv in zip(a, b)))
    metrics = metrics if metrics is not None else {}
    metrics.update(planned_seconds=round(points[-1][0], 3), moves_sent=0,
                   max_move_seconds=0, max_step_pixels=0, playback='bounded_steps_v1')
    began = previous_sent = clock()
    previous = path[0]
    try:
        for index, (dt, x, y) in enumerate(path):
            if index:
                # Pace from the previous send, not the original wall-clock plan:
                # latency must not discard coordinates or trigger a catch-up jump.
                await sleep(max(0, dt - previous[0] - (clock() - previous_sent)))
            if check:
                check()
            previous_sent = clock()
            await pointer.move(x, y)
            metrics['moves_sent'] += 1
            metrics['max_move_seconds'] = max(metrics['max_move_seconds'], round(clock()-previous_sent, 3))
            metrics['max_step_pixels'] = max(metrics['max_step_pixels'], math.hypot(x-previous[1], y-previous[2]))
            if update:
                update(x, y)
            previous = (dt, x, y)
    finally:
        metrics['actual_seconds'] = round(clock() - began, 3)


def smooth_slider_path(handle, track, duration=3):
    """One horizontal stroke, easing at both ends, with no overshoot or regrip."""
    if (not math.isfinite(duration) or duration <= 0
            or any(not math.isfinite(box[k]) for box in (handle, track)
                   for k in ('x', 'y', 'width', 'height'))
            or any(box[k] <= 0 for box in (handle, track) for k in ('width', 'height'))):
        raise ValueError('Invalid slider geometry')
    sx, sy = handle['x'] + handle['width']/2, handle['y'] + handle['height']/2
    end = track['x'] + track['width'] - handle['width']/2
    if (end <= sx or handle['x'] < track['x']-1
            or not track['y'] <= sy <= track['y'] + track['height']):
        raise ValueError('Invalid slider geometry')
    count = max(2, math.ceil((end-sx)*1.5/4))
    return [(duration*i/count, sx + (end-sx)*(3*(i/count)**2 - 2*(i/count)**3), sy)
            for i in range(count+1)]


async def move_timed(pointer, points, *, update=None, check=None, metrics=None,
                     clock=time.monotonic, sleep=asyncio.sleep):
    if (len(points) < 2 or points[0][0] != 0
            or any(not math.isfinite(v) for point in points for v in point)
            or any(b[0] <= a[0] for a, b in zip(points, points[1:]))):
        raise ValueError("Invalid pointer timeline")
    duration = points[-1][0]
    metrics = metrics if metrics is not None else {}
    metrics.update(planned_seconds=round(duration, 3), moves_sent=0, max_move_seconds=0)
    began, index = clock(), 0
    try:
        while True:
            if check:
                check()
            elapsed = min(duration, clock() - began)
            while index < len(points) - 2 and points[index + 1][0] <= elapsed:
                index += 1
            a, b = points[index:index + 2]
            fraction = (elapsed - a[0]) / (b[0] - a[0])
            x, y = a[1] + (b[1] - a[1]) * fraction, a[2] + (b[2] - a[2]) * fraction
            sent = clock()
            await pointer.move(x, y)
            metrics['moves_sent'] += 1
            metrics['max_move_seconds'] = max(metrics['max_move_seconds'], round(clock() - sent, 3))
            if update:
                update(x, y)
            if elapsed >= duration:
                return
            # Interpolate at current elapsed time on the next frame; never replay
            # missed samples back-to-back when a CDP round trip has been slow.
            await sleep(min(.02, max(0, duration - (clock() - began))))
    finally:
        metrics['actual_seconds'] = round(clock() - began, 3)


def slider_path(recording, handle, track):
    """Scale only the recorded drag up to the original 280px endpoint."""
    sx, sy = handle['x'] + handle['width'] / 2, handle['y'] + handle['height'] / 2
    end = track['x'] + track['width'] - handle['width'] / 2
    if end <= sx or not track['y'] <= sy <= track['y'] + track['height']:
        raise ValueError("Invalid slider geometry")
    trimmed = []
    for dt, x, y in recording:
        if x >= 280:
            if not trimmed:
                raise ValueError("Invalid recorded start")
            previous = trimmed[-1]
            dt = previous[0] + (dt - previous[0]) * (280 - previous[1]) / (x - previous[1])
            trimmed.append((dt, 280, 0))
            break
        trimmed.append((dt, x, y))
    if not trimmed or trimmed[0] != (0, 0, 0) or trimmed[-1][1] != 280:
        raise ValueError("Incomplete slider recording")
    path = []
    for dt, x, y in trimmed:
        progress = min(1, max(0, x / 280))
        # Stay inside the handle's vertical band and finish at the track centre.
        vertical = max(-handle['height'] / 4, min(handle['height'] / 4, y))
        vertical *= min(1, (1 - progress) / .3)
        path.append((dt, sx + progress * (end - sx), sy + vertical))
    return path
