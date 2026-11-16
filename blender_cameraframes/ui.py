# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from . import camera, geometry, overlay, frames


def lens_row(layout, settings, axis, label):
    row = layout.row(align=True)
    target, focal, angle, direction = (row.row(align=True) for _ in range(4))
    for column, width in zip((target, focal, angle, direction), (3.3, 3.0, 2.5, 2.3)):
        column.ui_units_x = width
    target.label(text=label)
    direction.prop(settings, axis, text='')
    return focal, angle


def draw_lens_values(layout, scene, obj):
    if obj.data.type != 'PERSP' or not camera.synced(scene, obj):
        return
    s = obj.cf_camera
    error = None
    header = layout.split(factor=0.78, align=True)
    header.label(text='フルサイズ換算焦点距離・画角')
    header.popover(panel='CF_PT_lens_details', text='長辺' if s.lens_reference == 'LONG_EDGE' else '対角')
    if s.frames:
        focal, angle = lens_row(layout, s, 'frame_lens_axis', '選択FRAME')
        single = len(frames.selected_indices(s)) == 1
        if single:
            try:
                camera.check_frame_focal(scene, obj)
            except ValueError as failure:
                error = str(failure)
        focal.enabled = angle.enabled = single and error is None
        if frames.active(s) is not None:
            focal.prop(s, 'frame_focal', text='')
            angle.prop(s, 'frame_angle_ui', text='')
        else:
            focal.label(text='— mm')
            angle.label(text='—°')
    focal, angle = lens_row(layout, s, 'output_lens_axis', '出力全体')
    mm, degrees = geometry.output_lens(camera.bounds(scene, obj), s.output_lens_axis, s.lens_reference)
    number = f'{mm:.2f}' if mm < 1e6 else f'{mm:.4g}'
    focal.alignment = angle.alignment = 'RIGHT'
    focal.label(text=f'{number} mm', translate=False)
    angle.label(text=f'{degrees:.2f}°', translate=False)
    if error:
        layout.label(text=error, icon='INFO')


class CF_UL_frames(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        selection = row.row(align=True)
        selection.enabled = item.visible
        selection.prop(item, 'selected', text='')
        row.prop(item, 'name', text='', emboss=False)
        row.prop(item, 'visible', text='', icon='HIDE_OFF' if item.visible else 'HIDE_ON', emboss=False)
        row.prop(item, 'export', text='PSD')


class CF_PT_main(bpy.types.Panel):
    bl_label = 'Camera Frames'
    bl_idname = 'CF_PT_main'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Camera Frames'

    def draw_header_preset(self, context):
        if camera.active(context.scene):
            self.layout.popover(panel='CF_PT_guide_style', text='', icon='PREFERENCES')

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        row = layout.row(align=True)
        row.operator('cf.register_camera', text='選択カメラを登録')
        row.operator('cf.create_camera', text='新規作成', icon='ADD')
        row.menu('CF_MT_camera_presets', text='', icon='PRESET')
        for i, entry in enumerate(scene.cf_scene.cameras):
            row = layout.row(align=True)
            obj = entry.camera
            selected = obj is not None and i == scene.cf_scene.active_index
            row.operator('cf.activate_camera', text='' if selected else obj.name if obj else '(削除済み)',
                         translate=False, icon='CAMERA_DATA', depress=selected).index = i
            if selected:
                row.prop(obj, 'name', text='')
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
        row = layout.split(factor=0.27, align=True)
        row.prop(s, 'show_guides', text='ガイド')
        rest = row.split(factor=0.37, align=True)
        rest.prop(s, 'show_axis')
        rest.prop(s, 'show_horizon')


class CameraPanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Camera Frames'
    bl_parent_id = 'CF_PT_main'

    @classmethod
    def poll(cls, context):
        return camera.active(context.scene) is not None


class CF_PT_guide_style(bpy.types.Panel):
    bl_label = 'ガイドの線幅'
    bl_idname = 'CF_PT_guide_style'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 12

    def draw(self, context):
        obj = camera.active(context.scene)
        if obj:
            self.layout.prop(obj.cf_camera, 'line_width')


class CF_PT_lens_details(bpy.types.Panel):
    bl_label = 'レンズ表示の詳細'
    bl_idname = 'CF_PT_lens_details'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 18

    @classmethod
    def poll(cls, context):
        obj = camera.active(context.scene)
        return obj is not None and obj.data.type == 'PERSP'

    def draw(self, context):
        obj = camera.active(context.scene)
        s, layout = obj.cf_camera, self.layout
        layout.label(text='フルサイズ換算の基準（共通）')
        layout.prop(s, 'lens_reference', text='')
        layout.separator()
        layout.label(text='画角は光軸を中央に置いた場合の値です')
        if not camera.synced(context.scene, obj):
            return
        axis, _ = camera.auxiliary(context.scene, obj, include_horizon=False)
        layout.label(text='光軸位置：左上0%、右下100%')
        frame = frames.active(s)
        if frame is not None:
            x, y = geometry.frame_coordinates(axis, frame, s.ratio_x / s.ratio_y)
            layout.label(text=f'FRAME  X {x * 100:.2f}%  /  Y {y * 100:.2f}%', translate=False)
        layout.label(text=f'出力  X {axis[0] / s.width * 100:.2f}%  /  Y {axis[1] / s.height * 100:.2f}%', translate=False)


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


class CF_PT_paper(CameraPanel, bpy.types.Panel):
    bl_label = '出力（用紙設定）'
    bl_idname = 'CF_PT_paper'

    def draw(self, context):
        s = camera.active(context.scene).cf_camera
        layout = self.layout
        layout.label(text=f'用紙 {s.width} × {s.height}px')
        layout.prop(s, 'paper_anchor', text='固定基点')
        layout.prop(s, 'aspect_lock')
        row = layout.row(align=True)
        row.operator('cf.paper_size', text='数値で変更')
        row.operator('cf.drag_paper', text='ドラッグで変更')


class CF_PT_frames(CameraPanel, bpy.types.Panel):
    bl_label = '撮影FRAME'
    bl_idname = 'CF_PT_frames'

    def draw_header_preset(self, context):
        obj = camera.active(context.scene)
        row = self.layout.row(align=True)
        count = len(frames.selected_indices(obj.cf_camera))
        if count != 1:
            row.label(text=f'{count}枚選択' if count else '未選択')
        row.prop(obj.cf_camera, 'show_frame_names', text='名前')

    def draw(self, context):
        obj = camera.active(context.scene)
        s, layout = obj.cf_camera, self.layout
        selected = frames.selected_indices(s)
        layout.template_list('CF_UL_frames', '', s, 'frames', s, 'frame_index', rows=3)
        row = layout.row(align=True)
        for action, icon in (('ADD', 'ADD'), ('DUPLICATE', 'DUPLICATE'), ('REMOVE', 'REMOVE'), ('UP', 'TRIA_UP'), ('DOWN', 'TRIA_DOWN')):
            button = row.row(align=True)
            button.enabled = action == 'ADD' or bool(selected)
            button.operator('cf.frame', text='', icon=icon).action = action
        row.separator()
        row.operator('cf.frame', text='', icon='SELECT_EXTEND').action = 'SELECT_ALL'
        row.operator('cf.frame', text='', icon='SELECT_SET').action = 'DESELECT'
        row.separator()
        row.operator('cf.align_pixels', text='', icon='SNAP_INCREMENT')
        if not s.frames:
            draw_lens_values(layout, context.scene, obj)
            return
        editing = overlay.frame_tool_active(context)
        layout.operator('cf.edit_tool', text='ビューでの編集を終了' if editing else 'ビューで編集',
                        icon='RESTRICT_SELECT_OFF', depress=editing)
        row = layout.row(align=True)
        row.enabled = bool(selected)
        for mode, label in (('MOVE', '移動'), ('SCALE', '拡縮'), ('ROTATE', '回転'), ('PIVOT', '基点移動')):
            row.operator('cf.drag_frame', text=label).mode = mode
        col = layout.column(align=True)
        col.enabled = bool(selected)
        row = col.row(align=True)
        row.prop(s, 'edit_x')
        row.prop(s, 'edit_y')
        row = col.row(align=True)
        row.prop(s, 'edit_width')
        row.prop(s, 'edit_height')
        row = col.split(factor=0.72, align=True)
        row.prop(s, 'edit_angle')
        row.prop(s, 'edit_color', text='')
        row = layout.split(factor=0.46, align=True)
        row.label(text='縦横比（共通）')
        values = row.row(align=True)
        values.prop(s, 'ratio_x', text='')
        values.prop(s, 'ratio_y', text=':')
        if camera.synced(context.scene, obj):
            if obj.data.type == 'PERSP':
                draw_lens_values(layout, context.scene, obj)
            elif obj.data.type == 'ORTHO':
                l, r, b, t = camera.bounds(context.scene, obj)
                f = frames.active(s)
                layout.label(text=f'正投影 幅 {f.width / s.width * (r - l):.4g}' if f else '正投影 幅 —')


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
    bl_label = '軌道'
    bl_idname = 'CF_PT_path'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        s, layout = camera.active(context.scene).cf_camera, self.layout
        layout.prop(s, 'path_type')
        layout.prop(s, 'show_path')
        layout.prop(s, 'path_points')
        layout.prop(s, 'path_color')
        f = frames.active(s)
        if len(s.frames) > 1 and s.path_type == 'BEZIER' and f is not None:
            col = layout.column(align=True)
            col.enabled = len(frames.selected_indices(s)) == 1
            col.prop(f, 'handle_mode')
            if f.handle_mode in {'MIRRORED', 'FREE'}:
                if s.frame_index > 0 or f.handle_mode == 'MIRRORED':
                    col.prop(f, 'handle_in', text='ハンドル' if f.handle_mode == 'MIRRORED' else '前のFRAME側')
                if f.handle_mode == 'FREE' and s.frame_index < len(s.frames) - 1:
                    col.prop(f, 'handle_out')


class CF_PT_export(CameraPanel, bpy.types.Panel):
    bl_label = 'ガイドPSD'
    bl_idname = 'CF_PT_export'

    def draw(self, context):
        obj = camera.active(context.scene)
        s, layout = obj.cf_camera, self.layout
        layout.prop(s, 'ppi', text='PSDのPPI')
        layout.prop(s, 'export_path')
        layout.prop(s, 'export_axis')
        layout.prop(s, 'export_horizon')
        layout.prop(s, 'export_mask')
        layout.operator('cf.export_psd', icon='EXPORT')


CLASSES = (CF_UL_frames, CF_PT_main, CF_PT_guide_style, CF_PT_lens_details, CF_PT_view, CF_PT_paper, CF_PT_frames, CF_PT_mask, CF_PT_path, CF_PT_export)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
