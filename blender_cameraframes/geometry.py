# SPDX-License-Identifier: GPL-3.0-or-later
"""Paper-space geometry, independent of Blender and of the drawing backend.

Coordinates are pixels at 100 percent output size, with Y increasing downwards.
Handle vectors are relative to their FRAME centre; angles are clockwise.
"""
from functools import lru_cache
from math import atan, ceil, cos, degrees, hypot, isfinite, pi, sin, sqrt

AXIS_COLOR = (1.0, 0.35, 0.35)
HORIZON_COLOR = (0.4, 1.0, 0.5)


def axis_strokes(point):
    """A cross with a 16px span in the 100% paper coordinate system."""
    x, y = point
    return [[(x - 8, y), (x + 8, y)], [(x, y - 8), (x, y + 8)]]


def rotate(point, angle):
    x, y = point
    c, s = cos(angle), sin(angle)
    return (c * x - s * y, s * x + c * y)


def locked_dimensions(width, height, base_width, base_height, axis='X'):
    ratio = base_width / base_height
    if axis == 'X':
        height = round(width / ratio)
        if not 4 <= height <= 30000:
            height = max(4, min(30000, height))
            width = round(height * ratio)
    else:
        width = round(height * ratio)
        if not 4 <= width <= 30000:
            width = max(4, min(30000, width))
            height = round(width / ratio)
    return width, height


def corners(frame, ratio):
    w, h = frame.width / 2, frame.width / (2 * ratio)
    return [(frame.x + x, frame.y + y) for x, y in
            (rotate(p, frame.angle) for p in ((-w, -h), (w, -h), (w, h), (-w, h)))]


def pivot(frame, ratio):
    x, y = rotate((frame.anchor[0] * frame.width / 2,
                   frame.anchor[1] * frame.width / (2 * ratio)), frame.angle)
    return frame.x + x, frame.y + y


def handles(frames, index):
    f = frames[index]
    if f.handle_mode == 'MIRRORED':
        return tuple(f.handle_in), tuple(-v for v in f.handle_in)
    if f.handle_mode == 'FREE':
        return tuple(f.handle_in), tuple(f.handle_out)
    if f.handle_mode == 'CORNER' or len(frames) < 2:
        return (0.0, 0.0), (0.0, 0.0)
    a, b = frames[max(0, index - 1)], frames[min(len(frames) - 1, index + 1)]
    dx, dy = b.x - a.x, b.y - a.y
    length = hypot(dx, dy)
    if length < 1e-9:
        return (0.0, 0.0), (0.0, 0.0)
    before, after = hypot(f.x - a.x, f.y - a.y) / 3, hypot(b.x - f.x, b.y - f.y) / 3
    return (-dx / length * before, -dy / length * before), (dx / length * after, dy / length * after)


def cubic(a, b, c, d, t):
    u = 1 - t
    return tuple(u ** 3 * a[k] + 3 * u * u * t * b[k] +
                 3 * u * t * t * c[k] + t ** 3 * d[k] for k in (0, 1))


MOTION_ERROR = 0.15  # Paper pixels, independent of viewport zoom and output %.
MAX_MOTION_SAMPLES = 65536


def motion_key(frames, ratio, curved=True):
    """Only effective geometry: names, colours, visibility and selection are absent."""
    return (ratio, curved if len(frames) > 1 else False, tuple(
        (f.x, f.y, f.width, f.angle, handles(frames, i) if curved and len(frames) > 1 else ((0, 0), (0, 0)))
        for i, f in enumerate(frames)))


def motion_rectangle(center, width, angle, ratio):
    return tuple((center[0] + x, center[1] + y) for x, y in
                 (rotate((u * width / 2, v * width / (2 * ratio)), angle)
                  for u, v in ((-1, -1), (1, -1), (1, 1), (-1, 1))))


@lru_cache(maxsize=4)
def sample_motion(key):
    """Shared centre/corner samples with a bound on linear interpolation error.

    For a twice differentiable curve, chord error <= max|p''| * dt**2 / 8.
    The cubic's second derivative is linear; check both ends, not just its
    midpoint (which misses symmetric S curves). Add the derivative bound for
    linearly changing width and shortest-turn rotation at the farthest corner.
    Failures are cached too, so an unsupported shape is not retried each draw.
    """
    ratio, curved, frames = key
    result = []
    try:
        if not isfinite(ratio) or ratio <= 0:
            raise ValueError('FRAMEの縦横比が無効です')
        for f in frames:
            if not all(isfinite(v) for v in (*f[:4], *f[4][0], *f[4][1])) or f[2] <= 0:
                raise ValueError('FRAMEまたは軌道の値が無効です')
        if frames:
            f = frames[0]
            result.append((f[:2], motion_rectangle(f[:2], f[2], f[3], ratio)))
        radius = hypot(1, 1 / ratio) / 2
        for f, g in zip(frames, frames[1:]):
            a, d = f[:2], g[:2]
            b = tuple(a[k] + f[4][1][k] for k in (0, 1))
            c = tuple(d[k] + g[4][0][k] for k in (0, 1))
            turn = (g[3] - f[3] + pi) % (2 * pi) - pi
            acceleration = 6 * max(hypot(*(c[k] - 2 * b[k] + a[k] for k in (0, 1))),
                                   hypot(*(d[k] - 2 * c[k] + b[k] for k in (0, 1)))) if curved else 0
            acceleration += radius * (2 * abs(g[2] - f[2]) * abs(turn) + max(f[2], g[2]) * turn * turn)
            count = max(1, ceil(sqrt(acceleration / (8 * MOTION_ERROR))), ceil(abs(turn) / (pi / 12)))
            if len(result) + count > MAX_MOTION_SAMPLES:
                raise ValueError('軌道が複雑すぎます。FRAMEの大きさ・縦横比・ハンドルを小さくしてください')
            for j in range(1, count + 1):
                t = j / count
                p = cubic(a, b, c, d, t) if curved else tuple(a[k] + (d[k] - a[k]) * t for k in (0, 1))
                result.append((p, motion_rectangle(p, f[2] + (g[2] - f[2]) * t, f[3] + turn * t, ratio)))
        return tuple(result)
    except (ValueError, OverflowError) as error:
        return str(error)


def motion_for_key(key):
    result = sample_motion(key)
    if isinstance(result, str):
        raise ValueError(result)
    return result


def trajectory(frames, ratio, curved=True, corner_paths=False):
    if len(frames) < 2:
        return [[] for _ in range(5 if corner_paths else 1)]
    motion = motion_for_key(motion_key(frames, ratio, curved))
    return [[p for p, corners in motion]] + ([[corners[k] for p, corners in motion] for k in range(4)] if corner_paths else [])


def guide_layers(settings, *, axis, horizon):
    """Bottom-to-top layers, each (name, colour, line width, polylines)."""
    ratio = settings.ratio_x / settings.ratio_y
    result = []
    for f in settings.frames:
        if f.export:
            pts = corners(f, ratio)
            result.append((f.name, tuple(f.color), settings.line_width, [pts + pts[:1]]))
    if settings.export_path and len(settings.frames) > 1:
        paths = trajectory(settings.frames, ratio, settings.path_type == 'BEZIER', settings.corner_paths)
        result.append(('軌道', tuple(settings.path_color), settings.line_width, paths))
    if settings.export_axis and axis is not None and 0 <= axis[0] <= settings.width and 0 <= axis[1] <= settings.height:
        result.append(('光軸', AXIS_COLOR, settings.line_width, axis_strokes(axis)))
    if settings.export_horizon and horizon is not None:
        result.append(('アイレベル', HORIZON_COLOR, settings.line_width, [horizon]))
    return result


def segment_distance(point, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    den = dx * dx + dy * dy
    t = max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / den)) if den else 0.0
    return hypot(point[0] - a[0] - t * dx, point[1] - a[1] - t * dy)


def clip_segment(a, b, width, height):
    """Liang-Barsky clipping, including segments entirely outside the paper."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    low, high = 0.0, 1.0
    for p, q in ((-dx, a[0]), (dx, width - a[0]), (-dy, a[1]), (dy, height - a[1])):
        if abs(p) < 1e-12:
            if q < 0:
                return None
        elif p < 0:
            low = max(low, q / p)
        else:
            high = min(high, q / p)
        if low > high:
            return None
    return ((a[0] + low * dx, a[1] + low * dy), (a[0] + high * dx, a[1] + high * dy))


def frame_lens(frame, ratio, bounds, width, height):
    l, r, b, t = bounds
    x, y = rotate((frame.width / 2, 0), frame.angle)
    span = hypot(2 * x / width * (r - l), 2 * y / height * (t - b))
    return (36 / span, degrees(2 * atan(span / 2))) if span > 1e-12 else (0, 0)
