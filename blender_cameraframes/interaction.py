# SPDX-License-Identifier: GPL-3.0-or-later
from math import atan2, hypot, isfinite
import bpy
from bpy.props import EnumProperty, IntProperty
from . import camera, geometry, overlay, frames
from .operators import CameraOperator, EditSession, selected_indices


def get_view(context, obj):
    if not context.area or context.area.type != 'VIEW_3D':
        raise ValueError('3Dビューで操作してください')
    space = context.area.spaces.active
    if space.region_quadviews:
        raise ValueError('ガイドのドラッグは単一のカメラビューで操作してください')
    if space.region_3d.view_perspective != 'CAMERA' or overlay.view_camera(space, context.scene) != obj:
        raise ValueError('編集カメラのカメラビューを表示してください')
    if not camera.synced(context.scene, obj):
        raise ValueError('「用紙サイズを出力に反映」を実行してください')
    return next(r for r in context.area.regions if r.type == 'WINDOW'), space.region_3d


class CF_OT_drag(CameraOperator, bpy.types.Operator):
    bl_idname = 'cf.drag_frame'
    bl_label = 'FRAMEをドラッグ'
    bl_description = 'クリックしてドラッグ。枠内: 移動、四隅: 拡縮、黄点: ベジェ。Shiftクリック: 複数選択'
    bl_options = {'UNDO', 'BLOCKING'}
    mode: EnumProperty(items=[
        ('PICK', '選択して変形', '枠内をドラッグして移動、四隅で拡縮、黄色い点でベジェを編集します'),
        ('MOVE', '移動', 'ドラッグで移動。Shift: 水平・垂直に固定、X/Y: 軸指定、Ctrl: 微調整、Esc: 取消'),
        ('SCALE', '拡縮', 'ビュー内でドラッグして選択FRAMEを基点から拡縮します。Shiftで微調整、Escで取消'),
        ('ROTATE', '回転', 'ビュー内でドラッグして選択FRAMEを基点の周りに回転します。Shiftで微調整、Escで取消'),
        ('PIVOT', '基点移動', '拡縮・回転の基点をFRAMEや用紙の外にも移動できます。枠の位置は変わりません')], default='PICK')

    @classmethod
    def description(cls, context, properties):
        return properties.bl_rna.properties['mode'].enum_items[properties.mode].description

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
            self.report({'WARNING'}, str(error))
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
        label = {'PICK': 'FRAMEを選択・ドラッグ', 'MOVE': 'FRAMEを移動', 'SCALE': 'FRAMEを拡縮',
                 'ROTATE': 'FRAMEを回転', 'PIVOT': '基点を移動',
                 'HANDLE_IN': '前のFRAME側のハンドルを編集', 'HANDLE_OUT': '次のFRAME側のハンドルを編集'}[mode]
        axis = self.axis or self.auto_axis
        if mode == 'MOVE' and axis:
            label += '（水平）' if axis == 'X' else '（垂直）'
        hint = (' / Shift: 軸固定 / X・Y: 軸指定 / Ctrl: 微調整' if mode in {'MOVE', 'PICK'}
                else ' / Shift: 微調整')
        self._edit.header(label + ('中 / マウスを離す: 確定' if self.started else ' / ビュー内でドラッグ') +
                          hint + ' / Esc: 取消')

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
            raise ValueError('マウス座標が無効です')
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
                    self.report({'WARNING'}, str(error))
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
    bl_idname = 'cf.drag_paper'
    bl_label = '用紙をドラッグ'
    bl_description = 'ビュー内でドラッグ: 右で幅を拡大、上で高さを拡大。Shiftで微調整、Escで取消'
    bl_options = {'UNDO', 'BLOCKING'}

    def invoke(self, context, event):
        self.obj = camera.active(context.scene)
        try:
            self.region, self.rv = get_view(context, self.obj)
            camera.check_resize(context.scene, self.obj)
            self._edit = EditSession(context, paper=True)
        except ValueError as error:
            self.report({'WARNING'}, str(error))
            return {'CANCELLED'}
        self.scene = context.scene
        self.box = overlay.rect(context.scene, self.obj, self.region, self.rv)
        if not self.box or self.box[2] <= self.box[0] or self.box[3] <= self.box[1]:
            self._edit.finish()
            return {'CANCELLED'}
        self.start = None
        self._edit.header('用紙: ビュー内でドラッグ / Shift 微調整 / Esc 取消')
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
                self._edit.header(f'用紙: {width} × {height}px / Shift 微調整 / Esc 取消')
            except ValueError as error:
                self._edit.header(str(error))
            context.area.tag_redraw()
        elif self.start and event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self.finish()
            return {'FINISHED'}
        return {'RUNNING_MODAL'}


class CF_OT_nudge(CameraOperator, bpy.types.Operator):
    bl_idname = 'cf.nudge_frame'
    bl_label = 'FRAMEを1px移動'
    bl_description = '選択FRAMEを用紙上で1px移動します。Shiftで10px。小数座標と位置関係を保ちます'
    bl_options = {'UNDO'}
    direction: EnumProperty(items=[(v, v, '') for v in ('LEFT', 'RIGHT', 'UP', 'DOWN')])
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
    bl_description = '撮影FRAMEの選択・移動・拡縮・ベジェ編集'
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
    bl_idname = 'cf.edit_tool'
    bl_label = 'FRAME編集を切り替え'
    bl_description = 'ビュー内のFRAMEを直接選んで編集するツールを開始・終了します。Shiftクリックで複数選択できます'

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
