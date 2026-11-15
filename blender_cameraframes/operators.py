# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ExportHelper
from . import camera, geometry, mask, overlay, properties, frames


_edits = set()
FRAME_EDIT_FIELDS = ('x', 'y', 'width', 'angle', 'anchor', 'selected',
                     'handle_mode', 'handle_in', 'handle_out')
PAPER_PROJECTION = ('sensor_fit', 'sensor_width', 'shift_x', 'shift_y', 'ortho_scale')


class EditSession:
    """Own one preview's target and cleanup, without a timer or saved RNA state."""
    def __init__(self, context, *, paper=False):
        self.scene, self.obj = context.scene, camera.active(context.scene)
        if any(edit.obj == self.obj for edit in _edits):
            raise ValueError('このカメラでは別の編集操作が進行中です')
        self.data = self.obj.data
        self.window, self.area = context.window, context.area
        self.paper, self.closed, self.dirty = paper, False, False
        self.has_header = False
        self.token = None
        self.state = camera.snapshot(self.scene, self.obj) if paper else {'settings': camera.dump(self.obj.cf_camera)}
        self.frame_refs = tuple(self.obj.cf_camera.frames)
        self.views = overlay.capture_views(self.scene, self.obj) if paper else []
        self.last_render = None
        _edits.add(self)

    def target_exists(self):
        try:
            return (not self.closed and bpy.data.scenes.get(self.scene.name) == self.scene
                    and self.scene.objects.get(self.obj.name) == self.obj and self.obj.data == self.data
                    and self.obj.cf_camera.uid == self.state['settings']['uid'])
        except ReferenceError:
            return False

    def live_area(self):
        try:
            return (self.window and self.window in tuple(bpy.context.window_manager.windows)
                    and self.area and self.area in tuple(self.window.screen.areas))
        except ReferenceError:
            return False

    def current(self):
        if not self.target_exists():
            return False
        try:
            return (self.window.scene == self.scene and self.live_area() and self.area.type == 'VIEW_3D'
                    and camera.active(self.scene) == self.obj and self.scene.camera == self.obj
                    and len(self.obj.cf_camera.frames) == len(self.frame_refs)
                    and all(a == b for a, b in zip(self.obj.cf_camera.frames, self.frame_refs)))
        except ReferenceError:
            return False

    def header(self, text):
        if self.live_area() and (text is not None or self.has_header):
            self.area.header_text_set(text)
            self.area.tag_redraw()
        self.has_header = text is not None

    def live_views(self):
        """A captured area may now display another scene, camera or workspace."""
        result = []
        for view in self.views:
            area, region, rv = view[:3]
            try:
                if any(window.scene == self.scene and area in tuple(window.screen.areas)
                       for window in bpy.context.window_manager.windows):
                    space = area.spaces.active
                    if (area.type == 'VIEW_3D' and not space.region_quadviews and space.region_3d == rv
                            and region in tuple(area.regions) and overlay.view_camera(space, self.scene) == self.obj):
                        result.append(view)
            except ReferenceError:
                continue
        return result

    def restore_local(self):
        """Restore only fields this operation changes, without rebuilding FRAME collections."""
        s, saved = self.obj.cf_camera, self.state['settings']
        fields = ('x', 'y') if self.paper else FRAME_EDIT_FIELDS
        current_frames = set(s.frames)
        for frame, values in zip(self.frame_refs, saved['frames']):
            if frame not in current_frames:
                continue
            try:
                for name in fields:
                    if name == 'selected':
                        frame[name] = values[name]
                    else:
                        setattr(frame, name, values[name])
            except ReferenceError:
                continue
        if self.paper:
            for name in ('width', 'height', 'origin', 'aspect_lock'):
                setattr(s, name, saved[name])
            for name in PAPER_PROJECTION:
                setattr(self.data, name, self.state['projection'][name])
        elif tuple(s.frames) == self.frame_refs:
            s['frame_index'] = saved['frame_index']

    def preview_paper(self, width, height, lock):
        if not self.current():
            self.cancel()
            raise ValueError('操作対象が変わったため、用紙変更を取り消しました')
        views = self.live_views()
        self.restore_local()
        camera.apply(self.scene, self.obj)
        overlay.restore_views(views)
        self.dirty = True
        try:
            margins = camera.resize(self.scene, self.obj, width, height)
            self.obj.cf_camera.aspect_lock = lock
            overlay.preserve_views(self.scene, self.obj, views, margins)
        finally:
            self.last_render = {key: getattr(self.scene.render, key) for key in camera.RENDER}

    def cancel(self):
        if self.closed:
            return
        try:
            if self.dirty and self.target_exists():
                # Restore shared output only while it still belongs to this
                # camera and still contains the last values written by us.
                restore_render = (self.paper and self.scene.camera == self.obj and
                    self.last_render == {key: getattr(self.scene.render, key) for key in camera.RENDER})
                views = self.live_views() if restore_render else []
                self.restore_local()
                if restore_render:
                    for key, value in self.state['render'].items():
                        setattr(self.scene.render, key, value)
                    overlay.restore_views(views)
        finally:
            self.finish()

    def finish(self):
        if self.closed:
            return
        self.header(None)
        overlay.end_frame_edit(self.token)
        _edits.discard(self)
        self.closed = True
        self.state = self.last_render = None
        self.frame_refs, self.views = (), []
        self.obj = self.scene = self.data = self.window = self.area = self.token = None


@persistent
def cancel_edits(_unused=None):
    for edit in tuple(_edits):
        edit.cancel()


def cancel_inactive_edits():
    for edit in tuple(_edits):
        if not edit.current():
            edit.cancel()


class CameraOperator:
    @classmethod
    def poll(cls, context):
        return bool(camera.active(context.scene))


class CF_OT_register(bpy.types.Operator):
    bl_idname = 'cf.register_camera'
    bl_label = '選択カメラを登録'
    bl_description = '選択したカメラを登録します。他のカメラと設定を共有している場合は独立させます'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == 'CAMERA'

    def execute(self, context):
        try:
            obj = camera.register_camera(context.scene, context.active_object)
            camera.activate(context, obj)
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class CF_OT_activate(CameraOperator, bpy.types.Operator):
    bl_idname = 'cf.activate_camera'
    bl_label = '編集カメラを切り替え'
    bl_description = 'このカメラを選択し、用紙サイズをBlenderの出力設定へ反映します'
    bl_options = {'REGISTER', 'UNDO'}
    index: IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
        return len(context.scene.cf_scene.cameras) > 0

    def execute(self, context):
        state = context.scene.cf_scene
        index = self.index if self.index >= 0 else state.active_index
        if not 0 <= index < len(state.cameras):
            return {'CANCELLED'}
        obj = state.cameras[index].camera
        if not obj or obj.type != 'CAMERA' or context.scene.objects.get(obj.name) != obj:
            self.report({'WARNING'}, 'このカメラはシーン内にありません。登録解除してください')
            return {'CANCELLED'}
        camera.activate(context, obj)
        if context.area:
            context.area.tag_redraw()
        return {'FINISHED'}


class CF_OT_sync_output(CameraOperator, bpy.types.Operator):
    bl_idname = 'cf.sync_output'
    bl_label = '用紙サイズを出力に反映'
    bl_description = 'このカメラの用紙寸法・ピクセル比率をBlenderの出力設定へ反映します'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = camera.active(context.scene)
        return obj is not None and context.scene.camera == obj

    def execute(self, context):
        camera.apply(context.scene, camera.active(context.scene))
        return {'FINISHED'}


class CF_OT_unregister(bpy.types.Operator):
    bl_idname = 'cf.unregister_camera'
    bl_label = '登録解除'
    bl_description = '一覧から外します。カメラとその設定は保持します'
    bl_options = {'REGISTER', 'UNDO'}
    index: IntProperty(default=-1)

    def execute(self, context):
        state = context.scene.cf_scene
        index = self.index if self.index >= 0 else state.active_index
        if not 0 <= index < len(state.cameras):
            return {'CANCELLED'}
        obj = camera.active(context.scene)
        state.cameras.remove(index)
        state.active_index = camera.registered_index(context.scene, obj)
        camera.remember_selection()
        return {'FINISHED'}


class CF_OT_duplicate_camera(CameraOperator, bpy.types.Operator):
    bl_idname = 'cf.duplicate_camera'
    bl_label = 'カメラを設定ごと複製'
    bl_description = '用紙設定・FRAME・アニメーションを引き継ぎ、独立して編集できるカメラを作ります'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            obj = camera.duplicate(context.scene, camera.active(context.scene))
            context.view_layer.update()
            camera.activate(context, obj)
        except (ValueError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        if obj.parent or obj.constraints or (obj.animation_data and obj.animation_data.drivers):
            self.report({'INFO'}, '複製しました。親・constraint・driverの参照先は引き継いでいます')
        return {'FINISHED'}


class CF_OT_paper(CameraOperator, bpy.types.Operator):
    bl_idname = 'cf.paper_size'
    bl_label = '構図を保って用紙変更'
    bl_description = '数値欄をドラッグまたは直接入力。プレビューを確認して確定します'
    bl_options = {'REGISTER', 'UNDO'}
    width: IntProperty(name='幅 px', min=4, max=30000, default=1920)
    height: IntProperty(name='高さ px', min=4, max=30000, default=1080)
    lock: BoolProperty(name='縦横比を固定')
    _edit = None
    _error = ''

    def invoke(self, context, event):
        obj = camera.active(context.scene)
        try:
            camera.check_resize(context.scene, obj)
            self._edit = EditSession(context, paper=True)
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.width, self.height, self.lock = obj.cf_camera.width, obj.cf_camera.height, obj.cf_camera.aspect_lock
        self._previous = (self.width, self.height)
        self._error = ''
        return context.window_manager.invoke_props_dialog(self, width=320)

    def draw(self, context):
        self.layout.prop(self, 'width')
        self.layout.prop(self, 'height')
        self.layout.prop(self, 'lock')
        self.layout.label(text='数値をドラッグ: 連続変更 / Shift: 微調整')
        self.layout.label(text='Esc: 変更を取り消す')
        if self._error:
            self.layout.label(text=self._error, icon='ERROR')

    def check(self, context):
        edit = self._edit
        if not edit or edit.closed:
            self._error = '操作対象が変わったため、用紙変更を取り消しました'
            return False
        if self.lock:
            base = edit.state['settings']
            if self.width != self._previous[0]:
                self.width, self.height = geometry.locked_dimensions(self.width, self.height, base['width'], base['height'])
            elif self.height != self._previous[1]:
                self.width, self.height = geometry.locked_dimensions(self.width, self.height, base['width'], base['height'], 'Y')
        self._previous = (self.width, self.height)
        try:
            edit.preview_paper(self.width, self.height, self.lock)
            self._error = ''
        except ValueError as error:
            self._error = str(error)
        return True

    def execute(self, context):
        if self._edit is not None:
            self.check(context)
            if self._error:
                self.cancel(context)
                self.report({'ERROR'}, self._error)
                return {'CANCELLED'}
            self._edit.finish()
            self._edit = None
        else:
            obj = camera.active(context.scene)
            views = overlay.capture_views(context.scene, obj)
            try:
                margins = camera.resize(context.scene, obj, self.width, self.height)
                overlay.preserve_views(context.scene, obj, views, margins)
            except ValueError as error:
                self.report({'ERROR'}, str(error))
                return {'CANCELLED'}
        return {'FINISHED'}

    def cancel(self, context):
        if self._edit is not None:
            self._edit.cancel()


class CF_OT_fit(CameraOperator, bpy.types.Operator):
    bl_idname = 'cf.fit_view'
    bl_label = '用紙を全体表示'
    bl_description = 'ビューポート表示だけを合わせます。用紙・構図は変更しません'

    def execute(self, context):
        target = overlay.view_target(context, camera_view=False)
        if target is None:
            return {'CANCELLED'}
        _, region, rv = target
        rv.view_perspective = 'CAMERA'
        rv.view_camera_zoom = 0
        rv.view_camera_offset = (0, 0)
        with context.temp_override(region=region):
            bpy.ops.view3d.view_center_camera()
        return {'FINISHED'}


class CF_OT_zoom(CameraOperator, bpy.types.Operator):
    bl_idname = 'cf.zoom_view'
    bl_label = '用紙の表示倍率を調整'
    bl_description = 'カメラの構図を変えずに用紙の画面表示を拡大・縮小します'
    factor: FloatProperty(default=1.2, min=0.1, max=10)

    def execute(self, context):
        if overlay.view_target(context) is None:
            return {'CANCELLED'}
        overlay.set_display_scale(context, overlay.display_scale(context) * self.factor)
        return {'FINISHED'}


def selected_indices(settings):
    return frames.selected_indices(settings)


class CF_OT_frame(CameraOperator, bpy.types.Operator):
    bl_idname = 'cf.frame'
    bl_label = 'FRAME操作'
    bl_description = '撮影FRAMEを編集します'
    bl_options = {'REGISTER', 'UNDO'}
    action: EnumProperty(items=[
        ('ADD', '追加', '撮影FRAMEを追加します'),
        ('DUPLICATE', '複製', '選択したFRAMEを複製します'),
        ('REMOVE', '削除', '選択したFRAMEを削除します'),
        ('UP', '上へ', '選択FRAMEを一覧の上へ移動します。軌道は一覧順につながります'),
        ('DOWN', '下へ', '選択FRAMEを一覧の下へ移動します。軌道は一覧順につながります'),
        ('SELECT_ALL', '全選択', '表示中のFRAMEをすべて選択します'),
        ('DESELECT', '選択解除', 'FRAMEの選択を解除します')])

    @classmethod
    def description(cls, context, properties):
        return properties.bl_rna.properties['action'].enum_items[properties.action].description

    def execute(self, context):
        s = camera.active(context.scene).cf_camera
        indices = selected_indices(s)
        if self.action in {'DUPLICATE', 'REMOVE', 'UP', 'DOWN'} and not indices:
            return {'CANCELLED'}
        if self.action in {'ADD', 'DUPLICATE'}:
            count = len(indices) if self.action == 'DUPLICATE' and indices else 1
            if len(s.frames) + count > 128:
                self.report({'WARNING'}, 'FRAMEは128個までです')
                return {'CANCELLED'}
            properties.initialize_frame_colors(s)
            templates = [camera.dump(s.frames[i]) for i in indices] if self.action == 'DUPLICATE' else []
            if templates:
                offsets = geometry.transform_frames(templates, 'MOVE', (0, 0), delta=(32, 32))
                templates = [dict(data, **offset) for data, offset in zip(templates, offsets)]
            if not s.frames:
                s.next_frame_color = 0
            frames.select(s, ())
            added = []
            for data in templates or [None]:
                f = s.frames.add()
                if data:
                    camera.load(f, data)
                    f.name += ' Copy'
                else:
                    f.name = 'FRAME ' + str(len(s.frames))
                    f.x, f.y = s.width / 2, s.height / 2
                    f.width = min(s.width * 0.8, s.height * 0.8 * s.ratio_x / s.ratio_y)
                f.color = geometry.FRAME_COLORS[max(0, s.next_frame_color)]
                s.next_frame_color = (max(0, s.next_frame_color) + 1) % len(geometry.FRAME_COLORS)
                f.selected = True
                added.append(len(s.frames) - 1)
            frames.select(s, added, active=added[-1])
        elif self.action == 'REMOVE':
            for i in reversed(indices):
                s.frames.remove(i)
            frames.select(s, ())
        elif self.action in {'UP', 'DOWN'} and s.frames:
            active_index = s.frame_index
            moving = set(indices)
            step = -1 if self.action == 'UP' else 1
            for index in indices if step < 0 else reversed(indices):
                dest = index + step
                if 0 <= dest < len(s.frames) and dest not in moving:
                    s.frames.move(index, dest)
                    if active_index == index:
                        active_index = dest
                    elif active_index == dest:
                        active_index = index
                    moving.remove(index)
                    moving.add(dest)
            frames.select(s, moving, active=active_index)
        elif self.action in {'SELECT_ALL', 'DESELECT'}:
            frames.select(s, range(len(s.frames)) if self.action == 'SELECT_ALL' else (), active=s.frame_index)
        if context.area:
            context.area.tag_redraw()
        return {'FINISHED'}


class CF_OT_export(CameraOperator, bpy.types.Operator, ExportHelper):
    bl_idname = 'cf.export_psd'
    bl_label = 'ガイドPSDを書き出す'
    bl_description = 'FRAME・軌道・補助線・マスクを別レイヤーで保存します'
    filename_ext = '.psd'
    filter_glob: StringProperty(default='*.psd', options={'HIDDEN'})

    def invoke(self, context, event):
        self.filepath = bpy.path.clean_name(camera.active(context.scene).name) + '_guides.psd'
        return super().invoke(context, event)

    def execute(self, context):
        from .psd import write_psd
        obj = camera.active(context.scene)
        s = obj.cf_camera
        if obj.data.type not in {'PERSP', 'ORTHO'}:
            self.report({'ERROR'}, '透視または正投影のカメラを使用してください')
            return {'CANCELLED'}
        if not camera.synced(context.scene, obj):
            self.report({'ERROR'}, '用紙と出力設定が異なります。「用紙サイズを出力に反映」を実行してください')
            return {'CANCELLED'}
        if context.scene.render.use_border and context.scene.render.use_crop_to_border:
            self.report({'ERROR'}, 'PSD出力時はレンダー領域の「領域にクロップ」を解除してください')
            return {'CANCELLED'}
        if abs(s.aspect_x - s.aspect_y) > 1e-6:
            self.report({'ERROR'}, 'PSD出力は正方形ピクセルに対応しています')
            return {'CANCELLED'}
        width, height = camera.output_size(context.scene, obj)
        sx, sy = width / s.width, height / s.height
        axis, horizon, omitted = camera.export_auxiliary(context.scene, obj)
        wm = context.window_manager
        wm.progress_begin(0, 100)
        try:
            layers = [(name, color, weight * min(sx, sy),
                       [[(x * sx, y * sy) for x, y in path] for path in paths], outside)
                      for name, color, weight, paths, outside in geometry.guide_layers(s, axis=axis, horizon=horizon)]
            mask_layer = None
            if s.export_mask and mask.has_source(s):
                triangles = mask.triangles(mask.key_for(s), s.width, s.height)
                mask_layer = (tuple(tuple((x * sx, y * sy) for x, y in tri) for tri in triangles), s.mask_opacity / 100)
            if not layers and mask_layer is None:
                raise ValueError('用紙内に出力できるガイドがありません' + (' / ' + ' / '.join(omitted) if omitted else ''))
            write_psd(bpy.path.ensure_ext(self.filepath, '.psd'), width, height, s.ppi, layers,
                      lambda fraction: wm.progress_update(fraction * 100), mask_layer=mask_layer)
        except (OSError, ValueError, MemoryError) as error:
            self.report({'ERROR'}, 'PSD出力に失敗: ' + str(error))
            return {'CANCELLED'}
        finally:
            wm.progress_end()
        self.report({'INFO'}, f'ガイドPSDを保存: {width} × {height}px / {s.ppi:g} PPI' +
                    (' / ' + ' / '.join(omitted) if omitted else ''))
        return {'FINISHED'}


class CF_OT_align_pixels(CameraOperator, bpy.types.Operator):
    bl_idname = 'cf.align_pixels'
    bl_label = 'ピクセルに合わせる'
    bl_description = '内側の四辺を出力画像のピクセル境界に合わせます。寸法・回転・複数FRAMEの位置関係を保ちます'
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = camera.active(context.scene)
        return bool(obj and camera.synced(context.scene, obj) and frames.selected_indices(obj.cf_camera)
                    and not any(edit.obj == obj for edit in _edits))

    def execute(self, context):
        obj = camera.active(context.scene)
        s = obj.cf_camera
        indices = frames.selected_indices(s)
        selected = [s.frames[i] for i in indices]
        saved = [(f.x, f.y) for f in selected]
        width, height = camera.output_size(context.scene, obj)
        scale = (width / s.width, height / s.height)
        try:
            delta = geometry.pixel_alignment(selected, s.ratio_x / s.ratio_y, scale,
                                              indices.index(frames.active_index(s)))
            for f, (x, y) in zip(selected, saved):
                f.x, f.y = x + delta[0], y + delta[1]
            # RNA stores floats. Reject an unrepresentable result as a whole.
            correction = geometry.pixel_alignment(selected, s.ratio_x / s.ratio_y, scale)
            if any(abs(v * a) > 1e-4 for v, a in zip(correction, scale)):
                raise ValueError('現在の座標精度ではピクセル境界に合わせられません')
        except ValueError as error:
            for f, (x, y) in zip(selected, saved):
                f.x, f.y = x, y
            self.report({'WARNING'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


CLASSES = (CF_OT_register, CF_OT_activate, CF_OT_sync_output, CF_OT_unregister, CF_OT_duplicate_camera,
           CF_OT_paper, CF_OT_fit, CF_OT_zoom, CF_OT_frame, CF_OT_export, CF_OT_align_pixels)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        handlers.append(cancel_edits)


def unregister():
    cancel_edits()
    for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        if cancel_edits in handlers:
            handlers.remove(cancel_edits)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
