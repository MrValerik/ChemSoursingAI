"""Time-based pointer playback without accumulating a queue of stale CDP moves."""
import asyncio
import math
import time


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
