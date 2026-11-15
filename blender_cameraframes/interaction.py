# SPDX-License-Identifier: GPL-3.0-or-later
from math import atan2, hypot, isfinite
import bpy
from bpy.props import EnumProperty
from . import camera, geometry, overlay
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
        ('MOVE', '移動', 'ビュー内でドラッグして選択FRAMEを移動します。Shiftで微調整、Escで取消'),
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
        self._last_transform_error = None
        self._edit.header('Camera Frames: ドラッグ / G 移動 / S 拡縮 / R 回転 / A 基点 / Shift 微調整・複数選択 / Esc 取消')
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS' and context.region.type == 'WINDOW':
            result = self.begin(context, event)
            if result:
                return result
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def point(self, event):
        return overlay.paper_point((event.mouse_x - self.region.x, event.mouse_y - self.region.y), self.box, self.obj.cf_camera)

    def begin(self, context, event):
        s = self.obj.cf_camera
        point = self.point(event)
        self.operation = self.mode
        if self.mode == 'PICK':
            threshold = 9 * s.width / (self.box[2] - self.box[0])
            index = min(s.frame_index, len(s.frames) - 1)
            hit = None
            f = s.frames[index]
            if s.show_path and s.path_type == 'BEZIER' and f.visible:
                for which, h in enumerate(geometry.handles(s.frames, index)):
                    if hypot(point[0] - f.x - h[0], point[1] - f.y - h[1]) <= threshold:
                        hit = (index, 'HANDLE_IN' if which == 0 else 'HANDLE_OUT')
                        break
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
            s.frame_index = index
            if event.shift:
                s.frames[index].selected = not s.frames[index].selected
                self.finish(context)
                return {'FINISHED'}
            if not s.frames[index].selected:
                for f in s.frames:
                    f.selected = False
                s.frames[index].selected = True
        self.indices = [i for i in selected_indices(s) if s.frames[i].visible]
        if not self.indices:
            self.finish(context)
            return {'CANCELLED'}
        self._edit.dirty = True
        self.index = min(s.frame_index, len(s.frames) - 1)
        if not s.frames[self.index].visible:
            self.index = self.indices[0]
        f = s.frames[self.index]
        self.pivot = geometry.pivot(f, s.ratio_x / s.ratio_y)
        self.start = point
        self.frames = [camera.dump(f) for f in s.frames]
        self.start_handles = geometry.handles(s.frames, self.index)
        self.started = True
        return None

    def update(self, event):
        s = self.obj.cf_camera
        point = self.point(event)
        if not all(isfinite(v) for v in point):
            raise ValueError('マウス座標が無効です')
        fine = 0.1 if event.shift else 1.0
        dx, dy = (point[0] - self.start[0]) * fine, (point[1] - self.start[1]) * fine
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
                x, y = geometry.rotate((point[0] - saved['x'], point[1] - saved['y']), -saved['angle'])
                f.anchor = (x * 2 / saved['width'], y * 2 / (saved['width'] * s.ratio_y / s.ratio_x))
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
            if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                result = self.begin(context, event)
                if result:
                    return result
        elif event.type == 'MOUSEMOVE':
            try:
                self.update(event)
                self._last_transform_error = None
            except ValueError as error:
                if str(error) != self._last_transform_error:
                    self.report({'WARNING'}, str(error))
                    self._last_transform_error = str(error)
            context.area.tag_redraw()
        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
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
    )


class CF_OT_edit_tool(CameraOperator, bpy.types.Operator):
    bl_idname = 'cf.edit_tool'
    bl_label = 'FRAME編集を切り替え'
    bl_description = 'FRAME編集ツールを開始・終了します。編集時だけ操作ハンドルを表示します'

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and (overlay.frame_tool_active(context) or CF_OT_drag.poll(context))

    def execute(self, context):
        name = 'builtin.select_box' if overlay.frame_tool_active(context) else 'cf.edit_guides'
        bpy.ops.wm.tool_set_by_id(name=name)
        context.area.tag_redraw()
        return {'FINISHED'}


CLASSES = (CF_OT_drag, CF_OT_paper_drag, CF_OT_edit_tool)


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
