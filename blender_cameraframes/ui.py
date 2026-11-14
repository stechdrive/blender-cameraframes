# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from . import camera, overlay


class CF_UL_frames(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, 'selected', text='')
        row.prop(item, 'name', text='', emboss=False)
        row.prop(item, 'visible', text='', icon='HIDE_OFF' if item.visible else 'HIDE_ON', emboss=False)
        row.prop(item, 'export', text='', icon='EXPORT', emboss=False)


class CF_PT_main(bpy.types.Panel):
    bl_label = 'Camera Frames'
    bl_idname = 'CF_PT_main'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Camera Frames'

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.operator('cf.register_camera', icon='ADD')
        for i, entry in enumerate(scene.cf_scene.cameras):
            row = layout.row(align=True)
            obj = entry.camera
            row.operator('cf.activate_camera', text=obj.name if obj else '(削除済み)', translate=False, icon='CAMERA_DATA',
                         depress=i == scene.cf_scene.active_index).index = i
            row.operator('cf.unregister_camera', text='', icon='X').index = i
        obj = camera.active(scene)
        if not obj:
            selected = context.active_object
            if ((selected and selected.type == 'CAMERA' and camera.registered_index(scene, selected) < 0)
                    or (scene.camera and camera.registered_index(scene, scene.camera) < 0)):
                layout.label(text='未登録のカメラです', icon='INFO')
                layout.label(text='「選択カメラを登録」で追加できます')
            return
        s = obj.cf_camera
        layout.operator('cf.duplicate_camera', icon='DUPLICATE')
        if not camera.synced(scene, obj):
            box = layout.box()
            box.alert = True
            if scene.camera != obj:
                box.label(text='使用カメラが切り替わっています', icon='INFO')
            else:
                box.label(text='用紙と出力設定が異なります', icon='ERROR')
                box.operator('cf.sync_output')
        layout.prop(s, 'show_guides')


class CameraPanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Camera Frames'
    bl_parent_id = 'CF_PT_main'

    @classmethod
    def poll(cls, context):
        return camera.active(context.scene) is not None


class CF_PT_view(CameraPanel, bpy.types.Panel):
    bl_label = 'ビュー操作'
    bl_idname = 'CF_PT_view'

    def draw(self, context):
        layout = self.layout
        layout.prop(context.space_data, 'lock_camera', text='カメラをビューに')
        col = layout.column()
        col.enabled = overlay.view_target(context, camera_view=False) is not None
        col.operator('cf.fit_view', icon='VIEW_CAMERA')
        row = col.row(align=True)
        row.enabled = overlay.view_target(context) is not None
        row.operator('cf.zoom_view', text='', icon='REMOVE').factor = 1 / 1.2
        row.prop(context.window_manager, 'cf_view_scale')
        row.operator('cf.zoom_view', text='', icon='ADD').factor = 1.2
        layout.label(text='表示倍率は構図を変えません')


class CF_PT_paper(CameraPanel, bpy.types.Panel):
    bl_label = '用紙と構図'
    bl_idname = 'CF_PT_paper'

    def draw(self, context):
        s = camera.active(context.scene).cf_camera
        layout = self.layout
        layout.label(text=f'用紙 {s.width} × {s.height}px')
        layout.label(text=f'出力 {s.width * s.percentage // 100} × {s.height * s.percentage // 100}px ({s.percentage}%)')
        layout.prop(s, 'percentage', text='出力倍率 %')
        layout.prop(s, 'paper_anchor', text='固定基点')
        layout.prop(s, 'aspect_lock')
        row = layout.row(align=True)
        row.operator('cf.paper_size', text='数値で変更')
        row.operator('cf.drag_paper', text='ドラッグ')
        layout.operator('cf.reset_paper')


class CF_PT_frames(CameraPanel, bpy.types.Panel):
    bl_label = '撮影FRAME'
    bl_idname = 'CF_PT_frames'

    def draw(self, context):
        obj = camera.active(context.scene)
        s, layout = obj.cf_camera, self.layout
        layout.template_list('CF_UL_frames', '', s, 'frames', s, 'frame_index', rows=3)
        row = layout.row(align=True)
        for action, icon in (('ADD', 'ADD'), ('DUPLICATE', 'DUPLICATE'), ('REMOVE', 'REMOVE'), ('UP', 'TRIA_UP'), ('DOWN', 'TRIA_DOWN')):
            row.operator('cf.frame', text='', icon=icon).action = action
        row = layout.row(align=True)
        row.operator('cf.frame', text='全選択').action = 'SELECT_ALL'
        row.operator('cf.frame', text='選択解除').action = 'DESELECT'
        if not s.frames:
            return
        editing = overlay.frame_tool_active(context)
        layout.operator('cf.edit_tool', text='FRAME編集を終了' if editing else 'FRAMEを編集',
                        icon='RESTRICT_SELECT_OFF', depress=editing)
        row = layout.row(align=True)
        for mode, label in (('MOVE', '移動'), ('SCALE', '拡縮'), ('ROTATE', '回転'), ('PIVOT', '基点')):
            row.operator('cf.drag_frame', text=label).mode = mode
        f = s.frames[min(s.frame_index, len(s.frames) - 1)]
        col = layout.column(align=True)
        col.prop(f, 'x')
        col.prop(f, 'y')
        col.prop(f, 'width')
        col.prop(f, 'angle')
        col.prop(f, 'color')
        row = layout.row(align=True)
        row.prop(s, 'ratio_x')
        row.prop(s, 'ratio_y')
        if camera.synced(context.scene, obj):
            if obj.data.type == 'PERSP':
                col = layout.column(align=True)
                error = None
                try:
                    camera.check_frame_focal(context.scene, obj)
                except ValueError as failure:
                    error = str(failure)
                col.enabled = error is None
                col.prop(s, 'frame_focal')
                col.prop(s, 'frame_angle')
                if error:
                    layout.label(text=error, icon='INFO')
            elif obj.data.type == 'ORTHO':
                l, r, b, t = camera.bounds(context.scene, obj)
                layout.label(text=f'正投影 幅 {f.width / s.width * (r - l):.4g}')


class CF_PT_mask(CameraPanel, bpy.types.Panel):
    bl_label = 'FRAME外マスク'
    bl_idname = 'CF_PT_mask'

    def draw(self, context):
        s, layout = camera.active(context.scene).cf_camera, self.layout
        layout.prop(s, 'show_mask')
        col = layout.column()
        col.enabled = bool(s.frames)
        col.prop(s, 'mask_source')
        col.prop(s, 'mask_opacity')


class CF_PT_path(CameraPanel, bpy.types.Panel):
    bl_label = '軌道と補助線'
    bl_idname = 'CF_PT_path'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        s, layout = camera.active(context.scene).cf_camera, self.layout
        layout.prop(s, 'path_type')
        layout.prop(s, 'show_path')
        layout.prop(s, 'corner_paths')
        layout.prop(s, 'path_color')
        if s.frames and s.path_type == 'BEZIER':
            f = s.frames[min(s.frame_index, len(s.frames) - 1)]
            layout.prop(f, 'handle_mode')
            if f.handle_mode in {'MIRRORED', 'FREE'}:
                layout.prop(f, 'handle_in')
                if f.handle_mode == 'FREE':
                    layout.prop(f, 'handle_out')
        row = layout.row(align=True)
        row.prop(s, 'show_axis')
        row.prop(s, 'show_horizon')


class CF_PT_export(CameraPanel, bpy.types.Panel):
    bl_label = 'ガイドPSD'
    bl_idname = 'CF_PT_export'

    def draw(self, context):
        obj = camera.active(context.scene)
        s, layout = obj.cf_camera, self.layout
        layout.prop(s, 'line_width')
        layout.prop(s, 'ppi', text='PSDのPPI')
        layout.prop(s, 'export_path')
        layout.prop(s, 'export_axis')
        layout.prop(s, 'export_horizon')
        layout.prop(s, 'export_mask')
        if obj.data.type in {'PERSP', 'ORTHO'} and camera.synced(context.scene, obj):
            for notice in camera.export_auxiliary(context.scene, obj)[2]:
                layout.label(text=notice, icon='INFO')
        layout.label(text='FRAME一覧の矢印で出力対象を選択')
        width, height = s.width * s.percentage // 100, s.height * s.percentage // 100
        layout.label(text=f'{width * 25.4 / s.ppi:.1f} × {height * 25.4 / s.ppi:.1f} mm')
        layout.operator('cf.export_psd', icon='EXPORT')


CLASSES = (CF_UL_frames, CF_PT_main, CF_PT_view, CF_PT_paper, CF_PT_frames, CF_PT_mask, CF_PT_path, CF_PT_export)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
