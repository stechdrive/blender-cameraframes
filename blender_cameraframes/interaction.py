# SPDX-License-Identifier: GPL-3.0-or-later
from math import atan2, hypot, isfinite
import bpy
from .translations import CONTEXT, iface, report as rpt, tip
from bpy.props import EnumProperty, IntProperty
from . import camera, geometry, overlay, frames
from .operators import CameraOperator, EditSession, selected_indices


def get_view(context, obj):
    if not context.area or context.area.type != 'VIEW_3D':
        raise ValueError('Use a 3D Viewport for this operation')
    space = context.area.spaces.active
    if space.region_quadviews:
        raise ValueError('Drag guides in a single camera view')
    if space.region_3d.view_perspective != 'CAMERA' or overlay.view_camera(space, context.scene) != obj:
        raise ValueError('Switch to the camera view of the camera being edited')
    if not camera.synced(context.scene, obj):
        raise ValueError('Use Apply Paper Size to Output first')
    return next(r for r in context.area.regions if r.type == 'WINDOW'), space.region_3d


class CF_OT_drag(CameraOperator, bpy.types.Operator):
    bl_translation_context = CONTEXT
    bl_idname = 'cf.drag_frame'
    bl_label = 'Drag FRAME'
    bl_description = 'Click and drag inside a FRAME to move, at a corner to scale, or at a yellow point to edit Bezier handles. Shift-click to select multiple FRAMEs'
    bl_options = {'UNDO', 'BLOCKING'}
    mode: EnumProperty(items=[
        ('PICK', 'Select and Transform', 'Drag inside to move, at corners to scale, or at yellow points to edit Bezier handles'),
        ('MOVE', 'Move', 'Drag to move. Shift: constrain horizontally or vertically; X/Y: choose axis; Ctrl: precision; Esc: cancel'),
        ('SCALE', 'Scale', 'Drag in the view to scale selected FRAMEs around the pivot. Shift: precision; Esc: cancel'),
        ('ROTATE', 'Rotate', 'Drag in the view to rotate selected FRAMEs around the pivot. Shift: precision; Esc: cancel'),
        ('PIVOT', 'Move Pivot', 'Move the scale and rotation pivot, including outside the FRAME or paper, while keeping the FRAME in place')], default='PICK', translation_context=CONTEXT)

    @classmethod
    def description(cls, context, properties):
        return tip(properties.bl_rna.properties['mode'].enum_items[properties.mode].description)

    @classmethod
    def poll(cls, context):
        obj = camera.active(context.scene)
        return bool(overlay.guides_visible(context, obj) and overlay.view_target(context) is not None
                    and any(frame.visible for frame in obj.cf_camera.frames))

    def invoke(self, context, event):
        self.obj = camera.active(context.scene)
        try:
            self.region, self.rv = get_view(context, self.obj)
            self._edit = EditSession(context)
        except ValueError as error:
            self.report({'WARNING'}, rpt(str(error)))
            return {'CANCELLED'}
        if not self.obj.cf_camera.frames:
            self._edit.finish()
            return {'CANCELLED'}
        self.scene = context.scene
        self.box = overlay.rect(context.scene, self.obj, self.region, self.rv)
        if not self.box or self.box[2] <= self.box[0] or self.box[3] <= self.box[1]:
            self._edit.finish()
            return {'CANCELLED'}
        self._edit.token = overlay.begin_frame_edit(context)
        self.started = False
        self.axis = self.auto_axis = None
        self.shift_click = None
        self.dragged = False
        self.drag_threshold = 4 * context.preferences.system.ui_scale
        self._last_transform_error = None
        self.show_status()
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS' and context.region.type == 'WINDOW':
            result = self.begin(context, event)
            if result:
                return result
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def point(self, event):
        return overlay.paper_point((event.mouse_x - self.region.x, event.mouse_y - self.region.y), self.box, self.obj.cf_camera)

    def show_status(self):
        mode = self.operation if self.started else self.mode
        label = {'PICK': 'Select and drag FRAME', 'MOVE': 'Move FRAME', 'SCALE': 'Scale FRAME',
                 'ROTATE': 'Rotate FRAME', 'PIVOT': 'Move pivot',
                 'HANDLE_IN': 'Edit incoming handle', 'HANDLE_OUT': 'Edit outgoing handle'}[mode]
        label = iface(label)
        axis = self.axis or self.auto_axis
        if mode == 'MOVE' and axis:
            label += iface(' (horizontal)' if axis == 'X' else ' (vertical)')
        hint = iface(' / Shift: constrain / X, Y: axis / Ctrl: precision' if mode in {'MOVE', 'PICK'}
                     else ' / Shift: precision')
        self._edit.header(label + iface(' / Release: confirm' if self.started else ' / Drag in view') +
                          hint + iface(' / Esc: cancel'))

    def begin(self, context, event):
        s = self.obj.cf_camera
        point = self.point(event)
        self.operation = self.mode
        if self.mode == 'PICK':
            threshold = 9 * s.width / (self.box[2] - self.box[0])
            index = min(s.frame_index, len(s.frames) - 1)
            hit = None
            if s.show_path and s.path_type == 'BEZIER':
                candidates = []
                for i, f in enumerate(s.frames):
                    if not f.visible or not f.selected:
                        continue
                    for which, h in geometry.editable_handles(s.frames, i):
                        distance = hypot(point[0] - f.x - h[0], point[1] - f.y - h[1])
                        if distance <= threshold:
                            candidates.append((distance, i != index, i, which))
                if candidates:
                    _, _, i, which = min(candidates)
                    hit = (i, 'HANDLE_IN' if which == 0 else 'HANDLE_OUT')
            if hit is None:
                for i in reversed(range(len(s.frames))):
                    f = s.frames[i]
                    if not f.visible:
                        continue
                    points = geometry.corners(f, s.ratio_x / s.ratio_y)
                    if any(hypot(point[0] - x, point[1] - y) <= threshold for x, y in points):
                        hit = (i, 'SCALE')
                        break
                    x, y = geometry.rotate((point[0] - f.x, point[1] - f.y), -f.angle)
                    if abs(x) <= f.width / 2 + threshold and abs(y) <= f.width * s.ratio_y / s.ratio_x / 2 + threshold:
                        hit = (i, 'MOVE')
                        break
            if hit is None:
                self.finish(context)
                return {'CANCELLED'}
            self._edit.dirty = True
            index, self.operation = hit
            if event.shift:
                self.shift_click = index
                frames.select(s, (*selected_indices(s), index), active=index)
            elif self.operation in {'HANDLE_IN', 'HANDLE_OUT'} or not s.frames[index].selected:
                frames.select(s, (index,), active=index)
            else:
                s['frame_index'] = index
        self.indices = [i for i in selected_indices(s) if s.frames[i].visible]
        if not self.indices:
            self.finish(context)
            return {'CANCELLED'}
        self._edit.dirty = True
        self.index = s.frame_index
        f = s.frames[self.index]
        self.pivot = geometry.pivot(f, s.ratio_x / s.ratio_y)
        self.start = point
        self.start_mouse = (event.mouse_x, event.mouse_y)
        self.last_point = point
        self.move_delta = (0.0, 0.0)
        self.frames = [camera.dump(f) for f in s.frames]
        self.start_handles = geometry.handles(s.frames, self.index)
        self.started = True
        self.show_status()
        return None

    def update(self, event):
        s = self.obj.cf_camera
        point = self.point(event)
        if not all(isfinite(v) for v in point):
            raise ValueError('Invalid pointer coordinates')
        if not self.dragged:
            self.dragged = hypot(event.mouse_x - self.start_mouse[0], event.mouse_y - self.start_mouse[1]) >= self.drag_threshold
        if self.shift_click is not None and not self.dragged:
            return
        fine = 0.1 if event.shift else 1.0
        dx, dy = (point[0] - self.start[0]) * fine, (point[1] - self.start[1]) * fine
        if self.operation == 'MOVE':
            fine = 0.1 if event.ctrl else 1.0
            self.move_delta = tuple(d + (v - old) * fine for d, v, old in
                                    zip(self.move_delta, point, self.last_point))
            self.last_point = point
            dx, dy = self.move_delta
            if not event.shift:
                self.auto_axis = None
            elif self.auto_axis is None and self.dragged:
                self.auto_axis = 'X' if abs(dx) >= abs(dy) else 'Y'
            axis = self.axis or self.auto_axis
            if axis == 'X':
                dy = 0.0
            elif axis == 'Y':
                dx = 0.0
            self.show_status()
        px, py = self.pivot
        if self.operation == 'SCALE':
            distance = hypot(self.start[0] - px, self.start[1] - py)
            factor = max(0.001, 1 + (hypot(point[0] - px, point[1] - py) / max(distance, 1) - 1) * fine)
        else:
            factor = 1
        turn = (atan2(point[1] - py, point[0] - px) - atan2(self.start[1] - py, self.start[0] - px)) * fine
        if self.operation in {'HANDLE_IN', 'HANDLE_OUT', 'PIVOT'}:
            f, saved = s.frames[self.index], self.frames[self.index]
            if self.operation == 'PIVOT':
                limit = geometry.FRAME_COORD_LIMIT
                point = tuple(max(-limit, min(limit, v)) for v in point)
                for i in self.indices:
                    saved = self.frames[i]
                    x, y = geometry.rotate((point[0] - saved['x'], point[1] - saved['y']), -saved['angle'])
                    s.frames[i].anchor = (x * 2 / saved['width'], y * 2 / (saved['width'] * s.ratio_y / s.ratio_x))
            else:
                which = 0 if self.operation == 'HANDLE_IN' else 1
                vector = (self.start_handles[which][0] + dx, self.start_handles[which][1] + dy)
                f.handle_in, f.handle_out = self.start_handles
                f.handle_mode = saved['handle_mode'] if saved['handle_mode'] in {'FREE', 'MIRRORED'} else 'FREE'
                setattr(f, 'handle_in' if which == 0 else 'handle_out', vector)
                if f.handle_mode == 'MIRRORED':
                    setattr(f, 'handle_out' if which == 0 else 'handle_in', (-vector[0], -vector[1]))
        else:
            values = geometry.transform_frames([self.frames[i] for i in self.indices], self.operation,
                                               self.pivot, delta=(dx, dy), factor=factor, turn=turn)
            for i, fields in zip(self.indices, values):
                for name, value in fields.items():
                    setattr(s.frames[i], name, value)

    def finish(self, context):
        self._edit.finish()
        self.frames = []
        self.obj = self.scene = self.region = self.rv = None

    def cancel(self, context):
        self._edit.cancel()
        self.finish(context)

    def modal(self, context, event):
        if self._edit.closed:
            self.finish(context)
            return {'CANCELLED'}
        if (not self._edit.current() or context.scene != self.scene
                or not overlay.guides_visible(context, self.obj)
                or (self.started and any(not self.obj.cf_camera.frames[i].visible for i in self.indices))):
            self.cancel(context)
            return {'CANCELLED'}
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self.cancel(context)
            return {'CANCELLED'}
        if not self.started:
            if event.type in {'G', 'S', 'R', 'A'} and event.value == 'PRESS':
                self.mode = {'G': 'MOVE', 'S': 'SCALE', 'R': 'ROTATE', 'A': 'PIVOT'}[event.type]
                self.show_status()
            if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                result = self.begin(context, event)
                if result:
                    return result
        elif event.type in {'MOUSEMOVE', 'X', 'Y', 'LEFT_SHIFT', 'RIGHT_SHIFT', 'LEFT_CTRL', 'RIGHT_CTRL'}:
            if event.type in {'X', 'Y'} and event.value == 'PRESS' and self.operation == 'MOVE':
                self.axis = None if self.axis == event.type else event.type
            try:
                self.update(event)
                self._last_transform_error = None
            except ValueError as error:
                if str(error) != self._last_transform_error:
                    self.report({'WARNING'}, rpt(str(error)))
                    self._last_transform_error = str(error)
            context.area.tag_redraw()
        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            if self.shift_click is not None and not self.dragged:
                s = self.obj.cf_camera
                saved = self._edit.state['settings']
                indices = {i for i, f in enumerate(saved['frames']) if f['selected'] and f['visible']}
                if self.shift_click in indices:
                    indices.remove(self.shift_click)
                else:
                    indices.add(self.shift_click)
                frames.select(s, indices, active=self.shift_click)
            self.finish(context)
            return {'FINISHED'}
        return {'RUNNING_MODAL'}


class CF_OT_paper_drag(CameraOperator, bpy.types.Operator):
    bl_translation_context = CONTEXT
    bl_idname = 'cf.drag_paper'
    bl_label = 'Drag Paper Size'
    bl_description = 'Drag right to increase width and up to increase height. Shift: precision; Esc: cancel'
    bl_options = {'UNDO', 'BLOCKING'}

    def invoke(self, context, event):
        self.obj = camera.active(context.scene)
        try:
            self.region, self.rv = get_view(context, self.obj)
            camera.check_resize(context.scene, self.obj)
            self._edit = EditSession(context, paper=True)
        except ValueError as error:
            self.report({'WARNING'}, rpt(str(error)))
            return {'CANCELLED'}
        self.scene = context.scene
        self.box = overlay.rect(context.scene, self.obj, self.region, self.rv)
        if not self.box or self.box[2] <= self.box[0] or self.box[3] <= self.box[1]:
            self._edit.finish()
            return {'CANCELLED'}
        self.start = None
        self._edit.header(iface('Paper: drag in view / Shift: precision / Esc: cancel'))
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        self._edit.cancel()
        self.finish()

    def finish(self):
        self._edit.finish()
        self.obj = self.scene = self.region = self.rv = None

    def modal(self, context, event):
        if (not self._edit.current() or context.scene != self.scene
                or (event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS')):
            self.cancel(context)
            return {'CANCELLED'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self.start = (event.mouse_x, event.mouse_y)
        elif self.start and event.type == 'MOUSEMOVE':
            base = self._edit.state['settings']
            factor = 0.1 if event.shift else 1
            width = max(4, min(30000, round(base['width'] + (event.mouse_x - self.start[0]) * base['width'] / (self.box[2] - self.box[0]) * factor)))
            height = max(4, min(30000, round(base['height'] + (event.mouse_y - self.start[1]) * base['height'] / (self.box[3] - self.box[1]) * factor)))
            if base['aspect_lock']:
                width, height = geometry.locked_dimensions(width, height, base['width'], base['height'])
            try:
                self._edit.preview_paper(width, height, base['aspect_lock'])
                self._edit.header(iface('Paper: {width} × {height}px / Shift: precision / Esc: cancel',
                                        width=width, height=height))
            except ValueError as error:
                self._edit.header(iface(str(error)))
            context.area.tag_redraw()
        elif self.start and event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self.finish()
            return {'FINISHED'}
        return {'RUNNING_MODAL'}


class CF_OT_nudge(CameraOperator, bpy.types.Operator):
    bl_translation_context = CONTEXT
    bl_idname = 'cf.nudge_frame'
    bl_label = 'Nudge FRAME by 1 px'
    bl_description = 'Move selected FRAMEs by 1 paper pixel, or 10 with Shift. Preserves fractional coordinates and relative positions'
    bl_options = {'UNDO'}
    direction: EnumProperty(items=[(v, v, '') for v in ('LEFT', 'RIGHT', 'UP', 'DOWN')], translation_context=CONTEXT)
    step: IntProperty(default=1, min=1, max=10)

    @classmethod
    def poll(cls, context):
        obj = camera.active(context.scene)
        return bool(overlay.frame_tool_active(context) and CF_OT_drag.poll(context)
                    and frames.selected_indices(obj.cf_camera))

    def execute(self, context):
        s = camera.active(context.scene).cf_camera
        indices = frames.selected_indices(s)
        delta = {'LEFT': (-self.step, 0), 'RIGHT': (self.step, 0),
                 'UP': (0, -self.step), 'DOWN': (0, self.step)}[self.direction]
        saved = [dict(x=s.frames[i].x, y=s.frames[i].y) for i in indices]
        values = geometry.transform_frames(saved, 'MOVE', (0, 0), delta=delta)
        for i, value in zip(indices, values):
            s.frames[i].x, s.frames[i].y = value['x'], value['y']
        return {'FINISHED'}


class CF_WST_edit(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = 'cf.edit_guides'
    bl_label = 'Camera Frames'
    @staticmethod
    def bl_description(context, tool, keymap):
        return tip('Select, move and scale shooting FRAMEs, and edit Bezier handles')
    bl_icon = 'ops.generic.select_box'
    bl_keymap = (
        ('cf.drag_frame', {'type': 'LEFTMOUSE', 'value': 'PRESS'}, {'properties': [('mode', 'PICK')]}),
        ('cf.drag_frame', {'type': 'LEFTMOUSE', 'value': 'PRESS', 'shift': True}, {'properties': [('mode', 'PICK')]}),
        ('cf.drag_frame', {'type': 'LEFTMOUSE', 'value': 'PRESS', 'ctrl': True}, {'properties': [('mode', 'PICK')]}),
        ('cf.drag_frame', {'type': 'LEFTMOUSE', 'value': 'PRESS', 'shift': True, 'ctrl': True}, {'properties': [('mode', 'PICK')]}),
    ) + tuple(('cf.nudge_frame', {'type': direction + '_ARROW', 'value': 'PRESS', 'shift': shift},
               {'properties': [('direction', direction), ('step', 10 if shift else 1)]})
              for direction in ('LEFT', 'RIGHT', 'UP', 'DOWN') for shift in (False, True))


class CF_OT_edit_tool(CameraOperator, bpy.types.Operator):
    bl_translation_context = CONTEXT
    bl_idname = 'cf.edit_tool'
    bl_label = 'Toggle FRAME Editing'
    bl_description = 'Start or finish editing FRAMEs directly in the view. Shift-click to select multiple FRAMEs'

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and (overlay.frame_tool_active(context) or CF_OT_drag.poll(context))

    def execute(self, context):
        name = 'builtin.select_box' if overlay.frame_tool_active(context) else 'cf.edit_guides'
        bpy.ops.wm.tool_set_by_id(name=name)
        context.area.tag_redraw()
        return {'FINISHED'}


CLASSES = (CF_OT_drag, CF_OT_paper_drag, CF_OT_edit_tool, CF_OT_nudge)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    if not bpy.app.background:
        bpy.utils.register_tool(CF_WST_edit, after={'builtin.transform'}, separator=True)


def unregister():
    if not bpy.app.background:
        # Hidden workspaces also retain their active tool across file saves.
        for workspace in bpy.data.workspaces:
            tool = workspace.tools.from_space_view3d_mode('OBJECT', create=False)
            if tool and tool.idname == CF_WST_edit.bl_idname:
                with bpy.context.temp_override(workspace=workspace, object=None,
                                               active_object=None, edit_object=None):
                    bpy.ops.wm.tool_set_by_id(name='builtin.select_box', space_type='VIEW_3D')
        bpy.utils.unregister_tool(CF_WST_edit)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
