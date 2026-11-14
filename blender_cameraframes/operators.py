# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ExportHelper
from . import camera, geometry, mask, overlay


class CameraOperator:
    @classmethod
    def poll(cls, context):
        return bool(camera.active(context.scene))


class CF_OT_register(bpy.types.Operator):
    bl_idname = 'cf.register_camera'
    bl_label = '選択カメラを登録'
    bl_description = '選択したカメラを登録します。共有Cameraデータは独立させます'
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
    bl_description = 'シーンの使用カメラと出力解像度も保存設定に合わせます'
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
    bl_description = 'このカメラの用紙寸法・出力倍率・ピクセル比率をBlenderの出力設定へ反映します'
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
    bl_description = 'Camera・ガイド・Actionを独立コピー。親・constraint・driverの参照先は保持します'
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
    _state = None
    _views = None
    _error = ''

    def invoke(self, context, event):
        obj = camera.active(context.scene)
        try:
            camera.check_resize(context.scene, obj)
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self._state = camera.snapshot(context.scene, obj)
        self._views = overlay.capture_views(context.scene, obj)
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
        obj = camera.active(context.scene)
        if not self._state or not obj:
            return False
        if self.lock:
            base = self._state['settings']
            if self.width != self._previous[0]:
                self.width, self.height = geometry.locked_dimensions(self.width, self.height, base['width'], base['height'])
            elif self.height != self._previous[1]:
                self.width, self.height = geometry.locked_dimensions(self.width, self.height, base['width'], base['height'], 'Y')
        self._previous = (self.width, self.height)
        camera.restore(context.scene, obj, self._state)
        overlay.restore_views(self._views)
        try:
            margins = camera.resize(context.scene, obj, self.width, self.height)
            overlay.preserve_views(context.scene, obj, self._views, margins)
            obj.cf_camera.aspect_lock = self.lock
            self._error = ''
        except ValueError as error:
            self._error = str(error)
        return True

    def execute(self, context):
        obj = camera.active(context.scene)
        if self._state:
            self.check(context)
            if self._error:
                self.cancel(context)
                self.report({'ERROR'}, self._error)
                return {'CANCELLED'}
        else:
            views = overlay.capture_views(context.scene, obj)
            try:
                margins = camera.resize(context.scene, obj, self.width, self.height)
                overlay.preserve_views(context.scene, obj, views, margins)
            except ValueError as error:
                self.report({'ERROR'}, str(error))
                return {'CANCELLED'}
        return {'FINISHED'}

    def cancel(self, context):
        obj = camera.active(context.scene)
        if self._state and obj:
            camera.restore(context.scene, obj, self._state)
            overlay.restore_views(self._views)


class CF_OT_reset(CameraOperator, bpy.types.Operator):
    bl_idname = 'cf.reset_paper'
    bl_label = '登録時の用紙・投影へ戻す'
    bl_description = '用紙とレンズ・投影を復帰。FRAMEの編集内容とカメラの位置・回転は保持します'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = camera.active(context.scene)
        views = overlay.capture_views(context.scene, obj)
        margins = (-obj.cf_camera.origin[0], -obj.cf_camera.origin[1])
        try:
            camera.reset_paper(context.scene, obj)
            overlay.preserve_views(context.scene, obj, views, margins)
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


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
    indices = [i for i, frame in enumerate(settings.frames) if frame.selected]
    if not indices and settings.frames:
        indices = [min(settings.frame_index, len(settings.frames) - 1)]
    return indices


class CF_OT_frame(CameraOperator, bpy.types.Operator):
    bl_idname = 'cf.frame'
    bl_label = 'FRAME操作'
    bl_options = {'REGISTER', 'UNDO'}
    action: EnumProperty(items=[(s, s, '') for s in ('ADD', 'DUPLICATE', 'REMOVE', 'UP', 'DOWN', 'SELECT_ALL', 'DESELECT')])

    def execute(self, context):
        s = camera.active(context.scene).cf_camera
        indices = selected_indices(s)
        if self.action in {'ADD', 'DUPLICATE'}:
            count = len(indices) if self.action == 'DUPLICATE' and indices else 1
            if len(s.frames) + count > 128:
                self.report({'WARNING'}, 'FRAMEは128個までです')
                return {'CANCELLED'}
            templates = [camera.dump(s.frames[i]) for i in indices] if self.action == 'DUPLICATE' else []
            for f in s.frames:
                f.selected = False
            for data in templates or [None]:
                f = s.frames.add()
                if data:
                    camera.load(f, data)
                    f.name += ' Copy'
                    f.x += 32
                    f.y += 32
                else:
                    f.name = 'FRAME ' + str(len(s.frames))
                    f.x, f.y = s.width / 2, s.height / 2
                    f.width = min(s.width * 0.8, s.height * 0.8 * s.ratio_x / s.ratio_y)
                f.selected = True
            s.frame_index = len(s.frames) - 1
        elif self.action == 'REMOVE':
            for i in reversed(indices):
                s.frames.remove(i)
            s.frame_index = min(s.frame_index, max(0, len(s.frames) - 1))
        elif self.action in {'UP', 'DOWN'} and s.frames:
            index = min(s.frame_index, len(s.frames) - 1)
            dest = max(0, min(len(s.frames) - 1, index + (-1 if self.action == 'UP' else 1)))
            s.frames.move(index, dest)
            s.frame_index = dest
        elif self.action in {'SELECT_ALL', 'DESELECT'}:
            for f in s.frames:
                f.selected = self.action == 'SELECT_ALL'
        if context.area:
            context.area.tag_redraw()
        return {'FINISHED'}


class CF_OT_export(CameraOperator, bpy.types.Operator, ExportHelper):
    bl_idname = 'cf.export_psd'
    bl_label = 'ガイドPSDを書き出す'
    bl_description = 'FRAME・軌道・補助線・マスクを別レイヤーで保存します。本画はレンダーしません'
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
        width, height = s.width * s.percentage // 100, s.height * s.percentage // 100
        sx, sy = width / s.width, height / s.height
        axis, horizon, omitted = camera.export_auxiliary(context.scene, obj)
        wm = context.window_manager
        wm.progress_begin(0, 100)
        try:
            layers = [(name, color, weight * min(sx, sy), [[(x * sx, y * sy) for x, y in path] for path in paths])
                      for name, color, weight, paths in geometry.guide_layers(s, axis=axis, horizon=horizon)]
            mask_layer = None
            if s.export_mask and s.frames:
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


CLASSES = (CF_OT_register, CF_OT_activate, CF_OT_sync_output, CF_OT_unregister, CF_OT_duplicate_camera,
           CF_OT_paper, CF_OT_reset, CF_OT_fit, CF_OT_zoom, CF_OT_frame, CF_OT_export)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
