# SPDX-License-Identifier: GPL-3.0-or-later
"""Swept FRAME coverage shared by the viewport and guide PSD.

Rasterizers take the union before applying opacity; overlap never darkens it.
No Blender data blocks, render handlers or third-party polygon library.
"""
from functools import lru_cache
from math import ceil
from . import geometry


def key_for(settings):
    frames = settings.frames
    if settings.mask_source == 'ACTIVE' and frames:
        frames = (frames[min(settings.frame_index, len(frames) - 1)],)
    return geometry.motion_key(frames, settings.ratio_x / settings.ratio_y, settings.path_type == 'BEZIER')


def cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def intersection(a, b, c, d):
    den = (b[0] - a[0]) * (d[1] - c[1]) - (b[1] - a[1]) * (d[0] - c[0])
    if abs(den) < 1e-12:
        return None
    t = ((c[0] - a[0]) * (d[1] - c[1]) - (c[1] - a[1]) * (d[0] - c[0])) / den
    u = ((c[0] - a[0]) * (b[1] - a[1]) - (c[1] - a[1]) * (b[0] - a[0])) / den
    if 0 < t < 1 and 0 < u < 1:
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
    return None


def quad_triangles(a, b, c, d):
    # Rotating edges can form a bow tie. An arbitrary diagonal fills its
    # overlap twice and incorrectly exposes a wedge outside the swept FRAME.
    p = intersection(a, b, c, d)
    if p is not None:
        return ((a, p, d), (b, c, p))
    p = intersection(b, c, d, a)
    if p is not None:
        return ((a, b, p), (c, d, p))
    if cross(a, b, c) * cross(a, c, d) >= 0:
        return ((a, b, c), (a, c, d))
    return ((a, b, d), (b, c, d))


def clip_polygon(points, width, height):
    for axis, bound, sign in ((0, 0, 1), (0, width, -1), (1, 0, 1), (1, height, -1)):
        if not points:
            break
        output = []
        a = points[-1]
        inside_a = (a[axis] - bound) * sign >= 0
        for b in points:
            inside_b = (b[axis] - bound) * sign >= 0
            if inside_a != inside_b:
                t = (bound - a[axis]) / (b[axis] - a[axis])
                output.append(tuple(bound if k == axis else a[k] + t * (b[k] - a[k]) for k in (0, 1)))
            if inside_b:
                output.append(b)
            a, inside_a = b, inside_b
        points = output
    return points


@lru_cache(maxsize=4)
def triangles(key, width, height):
    motion = geometry.motion_for_key(key)
    if not motion:
        return ()
    result = []
    def add(triangle):
        if abs(cross(*triangle)) < 1e-10:
            return
        points = clip_polygon(triangle, width, height)
        for i in range(1, len(points) - 1):
            tri = (points[0], points[i], points[i + 1])
            if abs(cross(*tri)) > 1e-10:
                result.append(tri)
    # The first footprint plus the moving boundary covers the swept interior.
    for tri in quad_triangles(*motion[0][1]):
        add(tri)
    for (_, before), (_, after) in zip(motion, motion[1:]):
        for k in range(4):
            for tri in quad_triangles(before[k], before[(k + 1) % 4], after[(k + 1) % 4], after[k]):
                add(tri)
    if len(motion) > 1:
        for tri in quad_triangles(*motion[-1][1]):
            add(tri)
    return tuple(result)


def rasterize(width, height, triangles):
    """2x2 subpixel union via scanline intervals; at most one byte per pixel.

    Bits retain individual samples until all triangles have been unioned.
    Averaging per triangle would create seams or repeated edge opacity.
    """
    import numpy as np
    coverage = np.zeros((height, width), dtype=np.uint8)
    for triangle in triangles:
        low, high = min(p[1] for p in triangle), max(p[1] for p in triangle)
        for yi, offset_y in enumerate((0.25, 0.75)):
            first, last = max(0, ceil(low - offset_y)), min(height, ceil(high - offset_y))
            if last <= first:
                continue
            rows = np.arange(first, last)
            yy = rows + offset_y
            left, right = np.full(len(rows), np.inf), np.full(len(rows), -np.inf)
            for a, b in zip(triangle, triangle[1:] + triangle[:1]):
                if abs(b[1] - a[1]) < 1e-12:
                    continue
                valid = (yy >= min(a[1], b[1])) & (yy < max(a[1], b[1]))
                xx = a[0] + (yy - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
                left[valid] = np.minimum(left[valid], xx[valid])
                right[valid] = np.maximum(right[valid], xx[valid])
            for xi, offset_x in enumerate((0.25, 0.75)):
                starts = np.ceil(np.clip(left - offset_x, 0, width)).astype(np.int32)
                ends = np.ceil(np.clip(right - offset_x, 0, width)).astype(np.int32)
                bit = 1 << (yi * 2 + xi)
                for row, start, end in zip(rows, starts, ends):
                    if end > start:
                        coverage[row, start:end] |= bit
    lookup = np.array([round(255 * (1 - i.bit_count() / 4)) for i in range(16)], dtype=np.uint8)
    # Transform in strips to avoid a second full-size alpha allocation.
    for y in range(0, height, 64):
        coverage[y:y + 64] = lookup[coverage[y:y + 64]]
    return (0, 0, width, height), coverage


def clear():
    triangles.cache_clear()
    geometry.sample_motion.cache_clear()
