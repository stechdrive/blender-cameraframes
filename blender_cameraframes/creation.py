# SPDX-License-Identifier: GPL-3.0-or-later
"""Camera creation and explicit user-preset operations."""
import math
import bpy
from bpy.props import EnumProperty, FloatProperty, IntProperty, StringProperty
from . import camera, frames, presets


def from_camera(obj):
    s = obj.cf_camera
    f = frames.active(s)
    if len(frames.selected_indices(s)) != 1 or f is None:
        raise ValueError('保存するFRAMEを1枚選択してください')
    return presets.validate(dict(width=s.width, height=s.height, frame_width=f.width,
        ratio_x=s.ratio_x, ratio_y=s.ratio_y, ppi=s.ppi,
        offset_x=f.x - s.width / 2, offset_y=f.y - s.height / 2))


def collection_selectable(view_layer, target):
    stack = [(view_layer.layer_collection, True)]
    while stack:
        layer, inherited = stack.pop()
        selectable = inherited and not layer.collection.hide_select
        if layer == target:
            return selectable
        stack.extend((child, selectable) for child in layer.children)
    return False


def create(context, values, *, name='Camera'):
    v = presets.validate(values)
    layer = context.view_layer.active_layer_collection
    collection = layer.collection
    if (not layer.is_visible or collection.library or collection.override_library
            or not collection_selectable(context.view_layer, layer)):
        raise ValueError('追加先に、表示・選択できるローカルのコレクションを選んでください')
    scene, render = context.scene, context.scene.render
    previous_camera, previous_index = scene.camera, scene.cf_scene.active_index
    previous_render = {k: getattr(render, k) for k in camera.RENDER}
    previous_active = context.view_layer.objects.active
    previous_selected = tuple(context.selected_objects)
    space = context.space_data if context.area and context.area.type == 'VIEW_3D' else None
    rv = space.region_3d if space and not space.region_quadviews else None
    old_view = (rv.view_perspective, space.camera) if rv else None
    data, obj = None, None
    try:
        name = name.strip() or 'Camera'
        data = bpy.data.cameras.new(name)
        obj = bpy.data.objects.new(name, data)
        collection.objects.link(obj)
        if not obj.visible_get(view_layer=context.view_layer):
            raise ValueError('追加先のコレクションが非表示です')
        if rv:
            obj.matrix_world = rv.view_matrix.inverted()
            source = (space.camera if space.use_local_camera else previous_camera)
            if old_view[0] == 'CAMERA' and source and source.data.type in {'PERSP', 'ORTHO'}:
                for name in camera.PROJECTION:
                    setattr(data, name, getattr(source.data, name))
            else:
                data.lens = space.lens
                if old_view[0] == 'ORTHO':
                    data.type = 'ORTHO'
                    data.sensor_fit = 'HORIZONTAL'
                    data.ortho_scale = 2 / abs(rv.window_matrix[0][0])
        else:
            obj.location = context.scene.cursor.location
        render.resolution_x, render.resolution_y = v['width'], v['height']
        render.resolution_percentage = 100
        render.pixel_aspect_x = render.pixel_aspect_y = 1
        camera.register_camera(scene, obj)
        s = obj.cf_camera
        s.ratio_x, s.ratio_y, s.ppi = v['ratio_x'], v['ratio_y'], v['ppi']
        f = s.frames[0]
        f.width = v['frame_width']
        f.x, f.y = s.width / 2 + v['offset_x'], s.height / 2 + v['offset_y']
        camera.activate(context, obj)
        if context.view_layer.objects.active != obj:
            raise ValueError('追加先のコレクションではカメラを選択できません')
        if rv:
            if space.use_local_camera:
                space.camera = obj
            rv.view_perspective = 'CAMERA'
        return obj
    except Exception:
        if obj:
            index = camera.registered_index(scene, obj)
            if index >= 0:
                scene.cf_scene.cameras.remove(index)
            bpy.data.objects.remove(obj, do_unlink=True)
        if data and data.users == 0:
            bpy.data.cameras.remove(data)
        scene.camera, scene.cf_scene.active_index = previous_camera, previous_index
        for k, value in previous_render.items():
            setattr(render, k, value)
        for selected in previous_selected:
            selected.select_set(True)
        context.view_layer.objects.active = previous_active
        if rv:
            rv.view_perspective, space.camera = old_view
        camera.remember_selection()
        raise


def use_preset(self, _context):
    CF_OT_create_camera.set_values(self, presets.get(self.preset))


class CF_OT_create_camera(bpy.types.Operator):
    bl_idname = 'cf.create_camera'
    bl_label = 'カメラを新規作成'
    bl_description = '現在のコレクションに、用紙サイズとFRAME Aを設定したカメラを作成します'
    bl_options = {'UNDO'}
    camera_name: StringProperty(name='カメラ名', default='Camera', options={'SKIP_SAVE'},
        description='作成するカメラオブジェクトの名前。作成後も一覧やアウトライナーで変更できます')
    preset: EnumProperty(name='プリセット', items=presets.items, update=use_preset)
    width: IntProperty(name='用紙 幅', default=1754, min=4, max=30000)
    height: IntProperty(name='高さ', default=1240, min=4, max=30000)
    frame_width: FloatProperty(name='FRAME 幅', default=1536, min=1, max=1e6, precision=3)
    frame_height: FloatProperty(name='高さ', default=864, min=.00001, max=1e11, precision=3)
    ratio_x: FloatProperty(default=16, options={'HIDDEN'})
    ratio_y: FloatProperty(default=9, options={'HIDDEN'})
    offset_x: FloatProperty(name='横', description='用紙の中央から右へ移動するピクセル数', min=-1e6, max=1e6, precision=3)
    offset_y: FloatProperty(name='縦', description='用紙の中央から下へ移動するピクセル数', min=-1e6, max=1e6, precision=3)
    ppi: FloatProperty(name='PSDのPPI', default=150, min=1, max=10000)

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def set_values(self, v):
        for name, value in v.items():
            setattr(self, name, value)
        self.frame_height = v['frame_width'] * v['ratio_y'] / v['ratio_x']

    def values(self):
        v = {key: getattr(self, key) for key in presets.LIMITS}
        expected = self.frame_width * self.ratio_y / self.ratio_x
        if not math.isclose(expected, self.frame_height, rel_tol=1e-7, abs_tol=1e-6):
            w, h = self.frame_width, self.frame_height
            divisor = math.gcd(int(w), int(h)) if w.is_integer() and h.is_integer() else 1
            divisor = max(divisor, max(w, h) / 1000)
            v['ratio_x'], v['ratio_y'] = w / divisor, h / divisor
        return presets.validate(v)

    def invoke(self, context, event):
        self.preset = presets._state['last']
        self.set_values(presets._state['values'])
        return context.window_manager.invoke_props_dialog(self, width=380)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = False
        layout.prop(self, 'camera_name')
        layout.prop(self, 'preset')
        row = layout.row(align=True)
        row.prop(self, 'width'); row.prop(self, 'height')
        row = layout.row(align=True)
        row.prop(self, 'frame_width'); row.prop(self, 'frame_height')
        row = layout.row(align=True)
        row.label(text='中央から (px)')
        row.prop(self, 'offset_x'); row.prop(self, 'offset_y')
        layout.prop(self, 'ppi')
        layout.label(text='追加先: ' + context.view_layer.active_layer_collection.collection.name, icon='OUTLINER_COLLECTION', translate=False)
        if presets._error:
            layout.label(text='保存済みプリセットを読み込めません', icon='ERROR')

    def execute(self, context):
        try:
            values = self.values()
            create(context, values, name=self.camera_name)
        except (ValueError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        try:
            presets.remember(self.preset, values)
        except (OSError, ValueError):
            self.report({'WARNING'}, 'カメラを作成しましたが、前回の入力値を保存できませんでした')
        return {'FINISHED'}


class CF_OT_save_camera_preset(bpy.types.Operator):
    bl_idname = 'cf.save_camera_preset'
    bl_label = '用紙と選択FRAMEをプリセットに保存'
    bl_description = '用紙サイズ・選択FRAMEの内寸と中央からの位置・PPIを、新規作成用に保存します'
    name: StringProperty(name='名前', maxlen=64)

    @classmethod
    def poll(cls, context):
        obj = camera.active(context.scene)
        return bool(obj and len(frames.selected_indices(obj.cf_camera)) == 1)

    def invoke(self, context, event):
        self.name = '用紙プリセット'
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        self.layout.prop(self, 'name')

    def execute(self, context):
        try:
            presets.save(self.name, from_camera(camera.active(context.scene)))
        except (ValueError, OSError) as error:
            self.report({'ERROR'}, str(error) if isinstance(error, ValueError) else 'プリセットを保存できませんでした')
            return {'CANCELLED'}
        return {'FINISHED'}


class CF_OT_remove_camera_preset(bpy.types.Operator):
    bl_idname = 'cf.remove_camera_preset'
    bl_label = 'ユーザープリセットを削除'
    bl_description = '選んだユーザープリセットを削除します。作成済みのカメラには影響しません'
    preset: EnumProperty(name='プリセット', items=presets.user_items)

    @classmethod
    def poll(cls, context):
        return bool(presets._user_items)

    def invoke(self, context, event):
        self.preset = presets._user_items[0][0]
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        self.layout.prop(self, 'preset')

    def execute(self, context):
        try:
            presets.remove(self.preset)
        except (ValueError, OSError):
            self.report({'ERROR'}, 'プリセットを削除できませんでした')
            return {'CANCELLED'}
        return {'FINISHED'}


class CF_MT_camera_presets(bpy.types.Menu):
    bl_label = 'カメラ作成プリセット'
    def draw(self, context):
        self.layout.operator('cf.save_camera_preset', icon='PRESET_NEW')
        self.layout.operator('cf.remove_camera_preset', icon='REMOVE')


CLASSES = (CF_OT_create_camera, CF_OT_save_camera_preset, CF_OT_remove_camera_preset, CF_MT_camera_presets)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
