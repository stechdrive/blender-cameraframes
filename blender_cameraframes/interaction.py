# SPDX-License-Identifier: GPL-3.0-or-later
from math import atan2, hypot
import bpy
from bpy.props import EnumProperty
from . import camera, geometry, overlay
from .operators import CameraOperator, selected_indices


def get_view(context, obj):
    if not context.area or context.area.type != 'VIEW_3D':
        raise ValueError('3Dビューで操作してください')
    space = context.area.spaces.active
    if space.region_quadviews:
        raise ValueError('ガイドのドラッグは単一のカメラビューで操作してください')
    if space.region_3d.view_perspective != 'CAMERA' or overlay.view_camera(space, context.scene) != obj:
        raise ValueError('編集カメラのカメラビューを表示してください')
    if not camera.synced(context.scene, obj):
        raise ValueError('「保存設定を適用」を実行してください')
    return next(r for r in context.area.regions if r.type == 'WINDOW'), space.region_3d


class CF_OT_drag(CameraOperator, bpy.types.Operator):
    bl_idname = 'cf.drag_frame'
    bl_label = 'FRAMEをドラッグ'
    bl_description = 'クリックしてドラッグ。枠内: 移動、四隅: 拡縮、黄点: ベジェ。Shiftクリック: 複数選択'
    bl_options = {'UNDO', 'BLOCKING'}
    mode: EnumProperty(items=[('PICK', '選択して変形', ''), ('MOVE', '移動', ''),
                              ('SCALE', '拡縮', ''), ('ROTATE', '回転', ''), ('PIVOT', '基点移動', '')], default='PICK')

    def invoke(self, context, event):
        self.obj = camera.active(context.scene)
        try:
            self.region, self.rv = get_view(context, self.obj)
        except ValueError as error:
            self.report({'WARNING'}, str(error))
            return {'CANCELLED'}
        if not self.obj.cf_camera.frames:
            return {'CANCELLED'}
        self.scene = context.scene
        self.original = camera.dump(self.obj.cf_camera)
        self.box = overlay.rect(context.scene, self.obj, self.region, self.rv)
        self.started = False
        context.area.header_text_set('Camera Frames: ドラッグ / G 移動 / S 拡縮 / R 回転 / A 基点 / Shift 微調整・複数選択 / Esc 取消')
        if event.type == 'LEFTMOUSE' and context.region.type == 'WINDOW':
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
                return None
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
        self.indices = selected_indices(s)
        self.index = min(s.frame_index, len(s.frames) - 1)
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
        fine = 0.1 if event.shift else 1.0
        dx, dy = (point[0] - self.start[0]) * fine, (point[1] - self.start[1]) * fine
        px, py = self.pivot
        if self.operation == 'SCALE':
            distance = hypot(self.start[0] - px, self.start[1] - py)
            factor = max(0.001, 1 + (hypot(point[0] - px, point[1] - py) / max(distance, 1) - 1) * fine)
            factor = max(max(1 / self.frames[i]['width'] for i in self.indices), factor)
            factor = min(min(1000000 / self.frames[i]['width'] for i in self.indices), factor)
        else:
            factor = 1
        turn = (atan2(point[1] - py, point[0] - px) - atan2(self.start[1] - py, self.start[0] - px)) * fine
        if self.operation in {'HANDLE_IN', 'HANDLE_OUT', 'PIVOT'}:
            f, saved = s.frames[self.index], self.frames[self.index]
            camera.load(f, saved)
            if self.operation == 'PIVOT':
                x, y = geometry.rotate((point[0] - f.x, point[1] - f.y), -f.angle)
                f.anchor = (max(-1, min(1, x * 2 / f.width)), max(-1, min(1, y * 2 / (f.width * s.ratio_y / s.ratio_x))))
            else:
                which = 0 if self.operation == 'HANDLE_IN' else 1
                vector = (self.start_handles[which][0] + dx, self.start_handles[which][1] + dy)
                f.handle_in, f.handle_out = self.start_handles
                if f.handle_mode not in {'FREE', 'MIRRORED'}:
                    f.handle_mode = 'FREE'
                setattr(f, 'handle_in' if which == 0 else 'handle_out', vector)
                if f.handle_mode == 'MIRRORED':
                    setattr(f, 'handle_out' if which == 0 else 'handle_in', (-vector[0], -vector[1]))
        else:
            for i in self.indices:
                f, saved = s.frames[i], self.frames[i]
                camera.load(f, saved)
                if self.operation == 'MOVE':
                    f.x, f.y = saved['x'] + dx, saved['y'] + dy
                elif self.operation == 'SCALE':
                    f.x, f.y = px + (saved['x'] - px) * factor, py + (saved['y'] - py) * factor
                    f.width = saved['width'] * factor
                    f.handle_in = tuple(v * factor for v in saved['handle_in'])
                    f.handle_out = tuple(v * factor for v in saved['handle_out'])
                elif self.operation == 'ROTATE':
                    x, y = geometry.rotate((saved['x'] - px, saved['y'] - py), turn)
                    f.x, f.y = px + x, py + y
                    f.angle = saved['angle'] + turn
                    f.handle_in, f.handle_out = geometry.rotate(saved['handle_in'], turn), geometry.rotate(saved['handle_out'], turn)

    def finish(self, context):
        if context.area:
            context.area.header_text_set(None)
            context.area.tag_redraw()

    def cancel(self, context):
        try:
            camera.load(self.obj.cf_camera, self.original)
        except ReferenceError:
            pass
        self.finish(context)

    def modal(self, context, event):
        if context.scene != self.scene or camera.active(context.scene) != self.obj:
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
            self.update(event)
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
        except ValueError as error:
            self.report({'WARNING'}, str(error))
            return {'CANCELLED'}
        self.scene = context.scene
        self.state = camera.snapshot(context.scene, self.obj)
        self.views = overlay.capture_views(context.scene, self.obj)
        self.box = overlay.rect(context.scene, self.obj, self.region, self.rv)
        self.start = None
        context.area.header_text_set('用紙: ビュー内でドラッグ / Shift 微調整 / Esc 取消')
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        try:
            camera.restore(self.scene, self.obj, self.state)
            overlay.restore_views(self.views)
        except ReferenceError:
            pass
        context.area.header_text_set(None)
        context.area.tag_redraw()

    def modal(self, context, event):
        if context.scene != self.scene or camera.active(context.scene) != self.obj or event.type in {'ESC', 'RIGHTMOUSE'}:
            self.cancel(context)
            return {'CANCELLED'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self.start = (event.mouse_x, event.mouse_y)
        elif self.start and event.type == 'MOUSEMOVE':
            base = self.state['settings']
            factor = 0.1 if event.shift else 1
            width = max(4, min(30000, round(base['width'] + (event.mouse_x - self.start[0]) * base['width'] / (self.box[2] - self.box[0]) * factor)))
            height = max(4, min(30000, round(base['height'] + (event.mouse_y - self.start[1]) * base['height'] / (self.box[3] - self.box[1]) * factor)))
            if base['aspect_lock']:
                width, height = geometry.locked_dimensions(width, height, base['width'], base['height'])
            camera.restore(self.scene, self.obj, self.state)
            overlay.restore_views(self.views)
            try:
                margins = camera.resize(self.scene, self.obj, width, height)
                overlay.preserve_views(self.scene, self.obj, self.views, margins)
                context.area.header_text_set(f'用紙: {width} × {height}px / Shift 微調整 / Esc 取消')
            except ValueError as error:
                context.area.header_text_set(str(error))
            context.area.tag_redraw()
        elif self.start and event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            context.area.header_text_set(None)
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


CLASSES = (CF_OT_drag, CF_OT_paper_drag)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    if not bpy.app.background:
        bpy.utils.register_tool(CF_WST_edit, after={'builtin.transform'}, separator=True)


def unregister():
    if not bpy.app.background:
        bpy.utils.unregister_tool(CF_WST_edit)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
