# SPDX-License-Identifier: GPL-3.0-or-later
from collections import OrderedDict
from math import sqrt
import bpy
import blf
from bpy.app.handlers import persistent
from bpy_extras.view3d_utils import location_3d_to_region_2d
from . import camera, geometry

_handler = None
_shader = None
_cache = OrderedDict()
_last_error = None
_view_states = OrderedDict()


def view_metadata(settings):
    return (settings.uid, settings.width, settings.height, tuple(settings.origin), settings.aspect_x,
            settings.aspect_y, settings.id_data.data.sensor_fit)


def fitted_width(region, metadata):
    _, width, height, origin, ax, ay, sensor_fit = metadata
    aspect = width * ax / (height * ay)
    if bpy.app.version < (5, 3, 0):
        # 5.2 uses the camera Sensor Fit for the viewport projection too.
        if sensor_fit == 'HORIZONTAL':
            return region.width
        if sensor_fit == 'VERTICAL':
            return region.height * aspect
        return max(region.width, region.height) * min(1, aspect)
    return min(region.width, region.height * aspect)


def remember_view(region, rv, metadata):
    key = (region.as_pointer(), rv.as_pointer())
    _view_states[key] = metadata
    _view_states.move_to_end(key)
    if len(_view_states) > 32:
        _view_states.popitem(last=False)


def fitted_rect(region, rv, metadata):
    _, width, height, origin, ax, ay, sensor_fit = metadata
    factor = (sqrt(2) + rv.view_camera_zoom / 50) ** 2 / 4
    aspect = width * ax / (height * ay)
    w = fitted_width(region, metadata) * factor
    h = w / aspect
    cx = region.width * (0.5 - rv.view_camera_offset[0] * 2 * factor)
    cy = region.height * (0.5 - rv.view_camera_offset[1] * 2 * factor)
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def rect(scene, obj, region, rv3d):
    matrix = obj.matrix_world.normalized()
    points = [location_3d_to_region_2d(region, rv3d, matrix @ p) for p in obj.data.view_frame(scene=scene)]
    if any(p is None for p in points):
        return None
    return min(p.x for p in points), min(p.y for p in points), max(p.x for p in points), max(p.y for p in points)


def view_camera(space, scene):
    return space.camera if space.use_local_camera else scene.camera


def editable_view(context, obj):
    return (context.area and context.area.type == 'VIEW_3D' and context.region.type == 'WINDOW' and
            context.region_data and context.region_data.view_perspective == 'CAMERA' and
            view_camera(context.space_data, context.scene) == obj)


def screen_point(point, box, settings):
    l, b, r, t = box
    return l + point[0] / settings.width * (r - l), t - point[1] / settings.height * (t - b)


def paper_point(point, box, settings):
    l, b, r, t = box
    return (point[0] - l) / (r - l) * settings.width, (t - point[1]) / (t - b) * settings.height


def capture_views(scene, obj):
    views = []
    for window in bpy.context.window_manager.windows:
        if window.scene != scene:
            continue
        for area in window.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            space = area.spaces.active
            if space.region_quadviews or view_camera(space, scene) != obj:
                continue
            rv = space.region_3d
            region = next((r for r in area.regions if r.type == 'WINDOW'), None)
            if region and rv.view_perspective == 'CAMERA':
                box = rect(scene, obj, region, rv)
                if box:
                    views.append((area, region, rv, box, rv.view_camera_zoom, tuple(rv.view_camera_offset),
                                  obj.cf_camera.width, obj.cf_camera.height, view_metadata(obj.cf_camera)))
    return views


def restore_views(views):
    for area, region, rv, box, zoom, offset, width, height, metadata in views:
        rv.view_camera_zoom, rv.view_camera_offset = zoom, offset
        remember_view(region, rv, metadata)
        rv.update()
        area.tag_redraw()


def preserve_views(scene, obj, views, margins):
    s = obj.cf_camera
    for area, region, rv, old, zoom, offset, width, height, metadata in views:
        # RegionView3D.update() leaves the camera projection matrix stale until
        # redraw. Compute the fitted camera border directly, without a timer or
        # a second corrective redraw. Zoom factor: (sqrt(2) + zoom/50)^2 / 4.
        scale_x, scale_y = (old[2] - old[0]) / width, (old[3] - old[1]) / height
        factor = s.width * scale_x / fitted_width(region, view_metadata(s))
        prop = rv.bl_rna.properties['view_camera_zoom']
        desired = (sqrt(4 * factor) - sqrt(2)) * 50
        rv.view_camera_zoom = max(prop.hard_min, min(prop.hard_max, desired))
        actual_factor = (sqrt(2) + rv.view_camera_zoom / 50) ** 2 / 4
        centre_x = old[0] + (s.width / 2 - margins[0]) * scale_x
        centre_y = old[3] - (s.height / 2 - margins[1]) * scale_y
        rv.view_camera_offset = ((region.width / 2 - centre_x) / (region.width * 2 * actual_factor),
                                 (region.height / 2 - centre_y) / (region.height * 2 * actual_factor))
        remember_view(region, rv, view_metadata(s))
        rv.update()
        area.tag_redraw()


@persistent
def after_undo(unused):
    """Blender's Undo restores IDs, but does not restore viewport zoom/offset."""
    for window in bpy.context.window_manager.windows:
        scene = window.scene
        obj = camera.active(scene)
        if not obj or not camera.synced(scene, obj):
            continue
        now = view_metadata(obj.cf_camera)
        for area in window.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            space = area.spaces.active
            if space.region_quadviews or space.region_3d.view_perspective != 'CAMERA' or view_camera(space, scene) != obj:
                continue
            region = next((r for r in area.regions if r.type == 'WINDOW'), None)
            if region is None:
                continue
            rv = space.region_3d
            previous = _view_states.get((region.as_pointer(), rv.as_pointer()))
            if previous and previous[0] == now[0] and previous[1:] != now[1:]:
                box = fitted_rect(region, rv, previous)
                view = (area, region, rv, box, rv.view_camera_zoom, tuple(rv.view_camera_offset), previous[1], previous[2], previous)
                margins = (now[3][0] - previous[3][0], now[3][1] - previous[3][1])
                preserve_views(scene, obj, [view], margins)
            remember_view(region, rv, now)


def frame_key(f):
    return (f.name, f.x, f.y, f.width, f.angle, tuple(f.anchor), f.visible, f.selected,
            tuple(f.color), f.handle_mode, tuple(f.handle_in), tuple(f.handle_out))


def make_batches(scene, obj, box):
    from gpu_extras.batch import batch_for_shader
    s = obj.cf_camera
    ratio = s.ratio_x / s.ratio_y
    batches, labels = [], []
    def line(paths, color, width=1.5, paper=True):
        points = []
        for path in paths:
            path = [screen_point(p, box, s) for p in path] if paper else path
            for a, b in zip(path, path[1:]):
                points.extend(((a[0], a[1], 0), (b[0], b[1], 0)))
        if points:
            batches.append((batch_for_shader(_shader, 'LINES', {'pos': points}), (*color[:3], 1), width))
    scale = (box[2] - box[0]) / s.width
    if s.show_path and len(s.frames) > 1:
        line(geometry.trajectory(s.frames, ratio, s.path_type == 'BEZIER', s.corner_paths), s.path_color, max(0.7, s.line_width * scale))
    for i, f in enumerate(s.frames):
        if not f.visible:
            continue
        points = geometry.corners(f, ratio)
        line([points + points[:1]], f.color, max(0.7, s.line_width * scale))
        label_point = screen_point(points[0], box, s)
        labels.append((f.name, label_point, tuple(f.color)))
        if f.selected or i == s.frame_index:
            pp = screen_point(geometry.pivot(f, ratio), box, s)
            line([[(pp[0] - 5, pp[1]), (pp[0] + 5, pp[1])], [(pp[0], pp[1] - 5), (pp[0], pp[1] + 5)]], (1, 1, 1), paper=False)
            for p in points:
                x, y = screen_point(p, box, s)
                line([[(x - 4, y - 4), (x + 4, y - 4), (x + 4, y + 4), (x - 4, y + 4), (x - 4, y - 4)]], (1, 1, 1), paper=False)
            if s.path_type == 'BEZIER' and s.show_path:
                hi, ho = geometry.handles(s.frames, i)
                ends = [(f.x + h[0], f.y + h[1]) for h in (hi, ho)]
                line([[ends[0], (f.x, f.y), ends[1]]], (0.8, 0.8, 0.8), 1)
                for p in ends:
                    x, y = screen_point(p, box, s)
                    line([[(x, y + 4), (x + 4, y), (x, y - 4), (x - 4, y), (x, y + 4)]], (1, 0.75, 0.2), paper=False)
    axis, horizon = camera.auxiliary(scene, obj)
    if s.show_axis and 0 <= axis[0] <= s.width and 0 <= axis[1] <= s.height:
        x, y = screen_point(axis, box, s)
        line([[(x - 8, y), (x + 8, y)], [(x, y - 8), (x, y + 8)]], (1, 0.35, 0.35), paper=False)
    if s.show_horizon and horizon:
        line([horizon], (0.4, 1, 0.5), 1)
    return batches, labels


def draw():
    global _shader, _last_error
    context = bpy.context
    obj = camera.active(context.scene)
    if not obj or not obj.cf_camera.show_guides or not editable_view(context, obj) or not camera.synced(context.scene, obj):
        return
    import gpu
    s = obj.cf_camera
    remember_view(context.region, context.region_data, view_metadata(s))
    box = rect(context.scene, obj, context.region, context.region_data)
    if not box:
        return
    key = (obj.as_pointer(), tuple(round(v, 4) for v in box), s.width, s.height, s.ratio_x, s.ratio_y,
           s.show_path, s.path_type, s.corner_paths, s.line_width, tuple(s.path_color), s.show_axis,
           s.show_horizon, s.frame_index, tuple(v for row in obj.matrix_world for v in row),
           tuple(camera.projection(obj).values()), tuple(frame_key(f) for f in s.frames))
    blend, depth = gpu.state.blend_get(), gpu.state.depth_test_get()
    try:
        if _shader is None:
            _shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        if key not in _cache:
            _cache[key] = make_batches(context.scene, obj, box)
            if len(_cache) > 4:
                _cache.popitem(last=False)
        _cache.move_to_end(key)
        batches, labels = _cache[key]
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('NONE')
        _shader.bind()
        _shader.uniform_float('viewportSize', (context.region.width, context.region.height))
        for batch, color, width in batches:
            _shader.uniform_float('color', color)
            _shader.uniform_float('lineWidth', width)
            batch.draw(_shader)
        blf.size(0, 12)
        for name, (x, y), color in labels:
            if -100 < x < context.region.width and 0 < y < context.region.height:
                blf.position(0, x + 5, y + 6, 0)
                blf.color(0, *color, 1)
                blf.draw(0, name)
        _last_error = None
    except (RuntimeError, ReferenceError, ValueError) as error:
        # Report once, never repeat exceptions at redraw frequency.
        if str(error) != _last_error:
            print('Camera Frames overlay:', error)
            _last_error = str(error)
    finally:
        gpu.state.blend_set(blend)
        gpu.state.depth_test_set(depth)


def register():
    global _handler
    if not bpy.app.background:
        _handler = bpy.types.SpaceView3D.draw_handler_add(draw, (), 'WINDOW', 'POST_PIXEL')
        bpy.app.handlers.undo_post.append(after_undo)
        bpy.app.handlers.redo_post.append(after_undo)


def unregister():
    global _handler, _shader
    if _handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handler, 'WINDOW')
        _handler = None
    _cache.clear()
    _view_states.clear()
    for handlers in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if after_undo in handlers:
            handlers.remove(after_undo)
    _shader = None
