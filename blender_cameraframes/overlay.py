# SPDX-License-Identifier: GPL-3.0-or-later
from collections import OrderedDict
from math import sqrt
import bpy
import blf
from bpy.app.handlers import persistent
from bpy_extras.view3d_utils import location_3d_to_region_2d
from . import camera, geometry, mask

_handler = None
_shader = None
_frame_cache = OrderedDict()
_path_cache = OrderedDict()
_auxiliary_cache = OrderedDict()
_mask_cache = OrderedDict()
_mask_shader = None
_mask_fill_shader = None
_mask_quad = None
_markers = {}
_last_error = None
_view_states = OrderedDict()
_editing_areas = set()


def guides_visible(context, obj):
    space = context.space_data
    return bool(obj and obj.cf_camera.show_guides and space and space.type == 'VIEW_3D'
                and space.overlay.show_overlays)


def frame_tool_active(context):
    tool = context.workspace.tools.from_space_view3d_mode(context.mode, create=False)
    return bool(tool and tool.idname == 'cf.edit_guides')


def begin_frame_edit(context):
    key = (context.window.as_pointer(), context.area.as_pointer())
    _editing_areas.add(key)
    context.area.tag_redraw()
    return key


def end_frame_edit(key):
    _editing_areas.discard(key)


def editing_handles(context):
    if context.space_data.region_quadviews:
        return False
    key = (context.window.as_pointer(), context.area.as_pointer())
    return key in _editing_areas or frame_tool_active(context)


def view_target(context, *, camera_view=True):
    """Resolve the current viewport, including calls from its sidebar."""
    if not context.area or context.area.type != 'VIEW_3D':
        return None
    space = context.space_data
    obj = camera.active(context.scene)
    if (not obj or not camera.synced(context.scene, obj) or space.region_quadviews or
            view_camera(space, context.scene) != obj):
        return None
    rv = space.region_3d
    if camera_view and rv.view_perspective != 'CAMERA':
        return None
    region = next((r for r in context.area.regions if r.type == 'WINDOW'), None)
    return (obj, region, rv) if region else None


def display_scale(context):
    target = view_target(context)
    if target is None:
        return 100.0
    obj, region, rv = target
    factor = (sqrt(2) + rv.view_camera_zoom / 50) ** 2 / 4
    return 100 * fitted_width(region, view_metadata(obj.cf_camera)) * factor / obj.cf_camera.width


def set_display_scale(context, value):
    target = view_target(context)
    if target is None:
        return
    obj, region, rv = target
    old_factor = (sqrt(2) + rv.view_camera_zoom / 50) ** 2 / 4
    factor = max(0.0001, value / 100) * obj.cf_camera.width / fitted_width(region, view_metadata(obj.cf_camera))
    prop = rv.bl_rna.properties['view_camera_zoom']
    rv.view_camera_zoom = max(prop.hard_min, min(prop.hard_max, (sqrt(4 * factor) - sqrt(2)) * 50))
    new_factor = (sqrt(2) + rv.view_camera_zoom / 50) ** 2 / 4
    # Keep the paper centre in place, including when the view was panned.
    rv.view_camera_offset = tuple(v * old_factor / new_factor for v in rv.view_camera_offset)
    context.area.tag_redraw()


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


def cached(cache, key, build, *args):
    if key not in cache:
        cache[key] = build(*args)
        if len(cache) > 4:
            cache.popitem(last=False)
    cache.move_to_end(key)
    return cache[key]


def line_batch(paths):
    from gpu_extras.batch import batch_for_shader
    points = [(p[0], p[1], 0) for path in paths for pair in zip(path, path[1:]) for p in pair]
    return batch_for_shader(_shader, 'LINES', {'pos': points}) if points else None


def make_frame_batches(s):
    """Paper-space frames and handle locations, independent of the viewport."""
    ratio = s.ratio_x / s.ratio_y
    batches, labels, markers = [], [], []
    for i, f in enumerate(s.frames):
        if not f.visible:
            continue
        points = geometry.corners(f, ratio)
        color = tuple(f.color)
        batches.append((line_batch([points + points[:1]]), color, None))
        if f.selected or i == s.frame_index:
            labels.append((f.name, points[0], color))
            markers.append(('CROSS', geometry.pivot(f, ratio), (1, 1, 1)))
            for p in points:
                markers.append(('BOX', p, (1, 1, 1)))
            if s.path_type == 'BEZIER' and s.show_path:
                hi, ho = geometry.handles(s.frames, i)
                ends = [(f.x + h[0], f.y + h[1]) for h in (hi, ho)]
                batches.append((line_batch([[ends[0], (f.x, f.y), ends[1]]]), (0.8, 0.8, 0.8), 1))
                for p in ends:
                    markers.append(('DIAMOND', p, (1, 0.75, 0.2)))
    return batches, labels, markers


def make_path_batch(s):
    return line_batch(geometry.trajectory(s.frames, s.ratio_x / s.ratio_y, s.path_type == 'BEZIER', s.corner_paths))


def make_auxiliary_batches(scene, obj):
    s = obj.cf_camera
    axis, horizon = camera.auxiliary(scene, obj, include_axis=s.show_axis, include_horizon=s.show_horizon)
    batches = []
    if axis is not None and 0 <= axis[0] <= s.width and 0 <= axis[1] <= s.height:
        batches.append((line_batch(geometry.axis_strokes(axis)), geometry.AXIS_COLOR, None))
    if horizon is not None:
        batches.append((line_batch([horizon]), geometry.HORIZON_COLOR, None))
    return batches


def batches_for(scene, obj):
    """Only geometry inputs invalidate GPU buffers; view transforms never do."""
    s = obj.cf_camera
    frames = tuple(frame_key(f) for f in s.frames)
    key = (s.ratio_x, s.ratio_y, s.frame_index, s.show_path, s.path_type, frames)
    batches, labels, markers = cached(_frame_cache, key, make_frame_batches, s)
    path = None
    if s.show_path and len(s.frames) > 1:
        # Name, selection, visibility and colours do not change the trajectory.
        path_key = (s.ratio_x, s.ratio_y, s.path_type, s.corner_paths,
                    tuple((f[1:5], f[9:12]) for f in frames))
        path = cached(_path_cache, path_key, make_path_batch, s)
    auxiliary = []
    if s.show_axis or (s.show_horizon and obj.data.type == 'PERSP'):
        up = camera.horizon_normal(obj) if s.show_horizon and obj.data.type == 'PERSP' else None
        key = (s.width, s.height, s.aspect_x, s.aspect_y, tuple(camera.projection(obj).values()),
               s.show_axis, s.show_horizon, up)
        auxiliary = cached(_auxiliary_cache, key, make_auxiliary_batches, scene, obj)
    return path, batches, auxiliary, labels, markers


def marker_batch(kind):
    if kind not in _markers:
        paths = {'CROSS': [[(-5, 0), (5, 0)], [(0, -5), (0, 5)]],
                 'BOX': [[(-4, -4), (4, -4), (4, 4), (-4, 4), (-4, -4)]],
                 'DIAMOND': [[(0, 4), (4, 0), (0, -4), (-4, 0), (0, 4)]]}
        _markers[kind] = line_batch(paths[kind])
    return _markers[kind]


def draw_batch(batch, color, width):
    if batch is not None:
        _shader.uniform_float('color', (*color, 1))
        _shader.uniform_float('lineWidth', width)
        batch.draw(_shader)


def make_mask_texture(key, width, height):
    """Union on the GPU only after a paper/shape change; no scene rendering."""
    import gpu
    from mathutils import Matrix
    from gpu_extras.batch import batch_for_shader
    global _mask_fill_shader
    triangles = mask.triangles(key, width, height)
    # Bounded, paper-based resolution, unchanged by camera/view navigation.
    scale = min(2.0, 4096 / max(width, height), sqrt(8_000_000 / (width * height)))
    tw, th = max(1, round(width * scale)), max(1, round(height * scale))
    texture = gpu.types.GPUTexture((tw, th), format='R8')
    texture.filter_mode(True)
    framebuffer = gpu.types.GPUFrameBuffer(color_slots=(texture,))
    if _mask_fill_shader is None:
        _mask_fill_shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    points = []
    for tri in triangles:
        if mask.cross(*tri) < 0:
            tri = tri[::-1]
        points.extend((x, y, 0) for x, y in tri)
    batch = batch_for_shader(_mask_fill_shader, 'TRIS', {'pos': points}) if points else None
    viewport, scissor = gpu.state.viewport_get(), gpu.state.scissor_get()
    blend, depth = gpu.state.blend_get(), gpu.state.depth_test_get()
    try:
        with framebuffer.bind():
            gpu.state.viewport_set(0, 0, tw, th)
            gpu.state.scissor_set(0, 0, tw, th)
            framebuffer.clear(color=(0, 0, 0, 0))
            gpu.state.blend_set('NONE')
            gpu.state.depth_test_set('NONE')
            with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
                gpu.matrix.load_identity()
                gpu.matrix.load_projection_matrix(Matrix(((2 / width, 0, 0, -1), (0, 2 / height, 0, -1),
                                                         (0, 0, 1, 0), (0, 0, 0, 1))))
                if batch:
                    _mask_fill_shader.bind()
                    _mask_fill_shader.uniform_float('color', (1, 1, 1, 1))
                    batch.draw(_mask_fill_shader)
    finally:
        gpu.state.viewport_set(*viewport)
        gpu.state.scissor_set(*scissor)
        gpu.state.blend_set(blend)
        gpu.state.depth_test_set(depth)
    return texture


def draw_mask(s, box):
    if not s.show_mask or not s.frames or s.mask_opacity <= 0:
        return
    import gpu
    from gpu_extras.batch import batch_for_shader
    global _mask_shader, _mask_quad
    if _mask_shader is None:
        interface = gpu.types.GPUStageInterfaceInfo('cf_mask_uv')
        interface.smooth('VEC2', 'uv')
        info = gpu.types.GPUShaderCreateInfo()
        info.vertex_in(0, 'VEC2', 'pos')
        info.vertex_out(interface)
        info.push_constant('MAT4', 'mvp')
        info.push_constant('FLOAT', 'opacity')
        info.sampler(0, 'FLOAT_2D', 'coverage')
        info.fragment_out(0, 'VEC4', 'fragColor')
        info.vertex_source('void main() { uv = pos; gl_Position = mvp * vec4(pos, 0.0, 1.0); }')
        info.fragment_source('void main() { fragColor = vec4(0.0, 0.0, 0.0, opacity * (1.0 - texture(coverage, uv).r)); }')
        _mask_shader = gpu.shader.create_from_info(info)
        _mask_quad = batch_for_shader(_mask_shader, 'TRIS', {'pos': ((0, 0), (1, 0), (1, 1), (0, 1))},
                                      indices=((0, 1, 2), (0, 2, 3)))
    key = mask.key_for(s)
    texture = cached(_mask_cache, (key, s.width, s.height), make_mask_texture, key, s.width, s.height)
    with gpu.matrix.push_pop():
        gpu.matrix.translate((box[0], box[3], 0))
        gpu.matrix.scale((box[2] - box[0], box[1] - box[3], 1))
        _mask_shader.bind()
        _mask_shader.uniform_float('mvp', gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix())
        _mask_shader.uniform_float('opacity', s.mask_opacity / 100)
        _mask_shader.uniform_sampler('coverage', texture)
        _mask_quad.draw(_mask_shader)


def draw_guides(scene, obj, box, viewport_size, *, editing=False):
    import gpu
    s = obj.cf_camera
    path, batches, auxiliary, labels, markers = batches_for(scene, obj)
    draw_mask(s, box)
    scale_x, scale_y = (box[2] - box[0]) / s.width, (box[3] - box[1]) / s.height
    width = max(0.7, s.line_width * scale_x)
    _shader.bind()
    _shader.uniform_float('viewportSize', viewport_size)
    with gpu.matrix.push_pop():
        gpu.matrix.translate((box[0], box[3], 0))
        gpu.matrix.scale((scale_x, -scale_y, 1))
        draw_batch(path, s.path_color, width)
        for batch, color, fixed_width in batches:
            # Fixed-width batches are the edit-only Bezier handle connectors.
            if editing or fixed_width is None:
                draw_batch(batch, color, width if fixed_width is None else fixed_width)
    # Reuse small pixel-space shapes so zoom never rebuilds handle buffers.
    for kind, point, color in markers if editing else ():
        x, y = screen_point(point, box, s)
        with gpu.matrix.push_pop():
            gpu.matrix.translate((x, y, 0))
            draw_batch(marker_batch(kind), color, 1.5)
    with gpu.matrix.push_pop():
        gpu.matrix.translate((box[0], box[3], 0))
        gpu.matrix.scale((scale_x, -scale_y, 1))
        for batch, color, fixed_width in auxiliary:
            draw_batch(batch, color, width)
    blf.size(0, 12)
    for name, point, color in labels if editing and s.show_frame_names else ():
        x, y = screen_point(point, box, s)
        if -100 < x < viewport_size[0] and 0 < y < viewport_size[1]:
            blf.position(0, x + 5, y + 6, 0)
            blf.color(0, *color, 1)
            blf.draw(0, name)


def draw():
    global _shader, _last_error
    context = bpy.context
    obj = camera.active(context.scene)
    if not guides_visible(context, obj) or not editable_view(context, obj) or not camera.synced(context.scene, obj):
        return
    import gpu
    s = obj.cf_camera
    remember_view(context.region, context.region_data, view_metadata(s))
    box = rect(context.scene, obj, context.region, context.region_data)
    if not box:
        return
    blend, depth = gpu.state.blend_get(), gpu.state.depth_test_get()
    try:
        if _shader is None:
            _shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('NONE')
        draw_guides(context.scene, obj, box, (context.region.width, context.region.height), editing=editing_handles(context))
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
    global _handler, _shader, _mask_shader, _mask_fill_shader, _mask_quad
    if _handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handler, 'WINDOW')
        _handler = None
    for cache in (_frame_cache, _path_cache, _auxiliary_cache, _mask_cache, _markers):
        cache.clear()
    mask.clear()
    _view_states.clear()
    _editing_areas.clear()
    for handlers in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if after_undo in handlers:
            handlers.remove(after_undo)
    _shader = None
    _mask_shader = _mask_fill_shader = _mask_quad = None
