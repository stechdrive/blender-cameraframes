# SPDX-License-Identifier: GPL-3.0-or-later
"""Paper-space geometry, independent of Blender and of the drawing backend.

Coordinates are pixels at 100 percent output size, with Y increasing downwards.
Handle vectors are relative to their FRAME centre; angles are clockwise.
"""
from math import atan, cos, degrees, hypot, pi, sin

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


def trajectory(frames, ratio, curved=True, corner_paths=False):
    """Bounded adaptive sampling, also retaining rotation/scale between FRAMEs."""
    paths = [[] for _ in range(5 if corner_paths else 1)]
    for i in range(len(frames) - 1):
        f, g = frames[i], frames[i + 1]
        a, d = (f.x, f.y), (g.x, g.y)
        h1, h2 = handles(frames, i)[1], handles(frames, i + 1)[0]
        b, c = (a[0] + h1[0], a[1] + h1[1]), (d[0] + h2[0], d[1] + h2[1])
        # Screen-independent samples also keep PSD and viewport paths identical.
        length = hypot(b[0] - a[0], b[1] - a[1]) + hypot(c[0] - b[0], c[1] - b[1]) + hypot(d[0] - c[0], d[1] - c[1])
        turn = (g.angle - f.angle + pi) % (2 * pi) - pi
        count = min(256, max(8, int(length / 12), int(abs(turn) * 48))) if curved or corner_paths else 1
        for j in range(count + 1):
            if i and j == 0:
                continue
            t = j / count
            p = cubic(a, b, c, d, t) if curved else (a[0] + (d[0] - a[0]) * t, a[1] + (d[1] - a[1]) * t)
            paths[0].append(p)
            if corner_paths:
                w = (f.width + (g.width - f.width) * t) / 2
                for k, unit in enumerate(((-1, -1), (1, -1), (1, 1), (-1, 1)), 1):
                    x, y = rotate((unit[0] * w, unit[1] * w / ratio), f.angle + turn * t)
                    paths[k].append((p[0] + x, p[1] + y))
    return paths


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
