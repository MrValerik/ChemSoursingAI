"""Coarse motion model calibrated from an observed manual verification.

Only aggregate timings and normalized geometry are kept here, not a user's
raw event stream. The model remains an experiment, not a guaranteed solver.
"""


def calibrated_segments(handle, track):
    travel = track['x'] + track['width'] - handle['x'] - handle['width']
    sx = handle['x'] + .414 * handle['width']
    sy = handle['y'] + .676 * handle['height']
    if travel <= 0 or not track['y'] <= sy <= track['y'] + track['height']:
        raise ValueError('Invalid slider geometry')
    # Initial hold, short first drag, then a regrip within the moved handle.
    first = [(0, 0, 0), (.10, 0, 0), (.18, .04, 0), (.25, .10, 0), (.335, .173, 0)]
    # The hand continues beyond the track's end while the SDK clamps the handle.
    # Keep a near-horizontal finish and the brief hold before releasing.
    second = [(0, .196, 0), (.14, .405, 0), (.28, .574, -.032),
              (.42, .734, -.032), (.56, .871, -.032), (.70, .989, -.032),
              (.835, 1.135, -.032), (1.085, 1.176, 0), (1.14, 1.176, 0)]
    return [[(dt, sx + progress * travel, sy + dy * handle['height'])
             for dt, progress, dy in part] for part in (first, second)]


def regrip_segment(path, current, original, track):
    """Follow the actual handle if it has moved during release/animation."""
    if (not current or abs(current['width'] - original['width']) > 1
            or abs(current['height'] - original['height']) > 1
            or current['x'] < track['x'] - 1
            or current['x'] + current['width'] > track['x'] + track['width'] + 1
            or abs(current['y'] - original['y']) > 1):
        raise ValueError('Regrip geometry changed')
    start = current['x'] + .575 * current['width']
    previous, end = path[0][1], path[-1][1]
    if end <= start or end <= previous:
        raise ValueError('No remaining drag')
    return [(dt, start + (x - previous) / (end - previous) * (end - start), y)
            for dt, x, y in path]
